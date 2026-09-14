"""The v0.12 flagship as a TOOL: preview, token, guards, writes (D1 6.3, 6.4).

The engine's own tests are in `test_v012_pseudonymise_engine.py` and need
no database. This file is about everything the engine deliberately does
not know: which sources are in scope, what the preview says, what the
token binds, which refusals come in which order, what lands on disk, and
what is still true after a crash.

Fixture shape. The project these tests build is QualCoder's own, table
for table, from the CREATE TABLE statements at `3.8.2:__main__.py`
(verified at the pin, and identical at master for every table this tool
touches). That matters more here than anywhere else in the suite,
because two of the constraints are load-bearing and several existing
fixtures do not carry them:

- `code_text unique(cid, fid, pos0, pos1, owner)` and
  `annotation unique(fid, pos0, pos1, owner)`, which are what the
  collision pre-check and the position parking exist for. A fixture
  without them cannot fail when the code gets that wrong.
- `journal unique(name)`, which is what the journal's numeric-suffix
  loop exists for.

The visibility fixture adds the `coder_names.visibility` column AND the
four views together, because QualCoder's `update_coder_names` creates
them in one routine on every project open (app.py:1448-1569): a project
with the column and no views is a shape QualCoder makes nowhere, and a
suite that builds one is testing its own invention.
"""

import ast
import json
import os
import sqlite3
import sys
import re
import stat
import shutil
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server
import track5_helpers as H
from track5_helpers import write_fixture_sidecar
from qualcoder_mcp import preview_tokens as pt
from qualcoder_mcp import pseudonymise as P
from qualcoder_mcp.database import QualcoderDatabase
from qualcoder_mcp.project_settings import (DEFAULT_AI_CODER_NAME,
                                            KNOWN_AI_ASSISTANT_OWNER,
                                            SIDECAR_NAME)
from qualcoder_mcp.sessions import AICodingSession, CodingSuggestion

POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX file modes and symlinks: Windows chmod moves only the "
           "read-only flag and symlink creation needs a privilege")

#                 0123456789...
TEXT = ("Thomas said he met Tom yesterday. Mary Ann agreed with Thomas. "
        "Later Thomas left.")
assert TEXT.index("Thomas") == 0
assert TEXT.index("Tom yesterday") == 19
assert TEXT.index("Mary Ann") == 34

MAPPING = [{"original": "Thomas", "pseudonym": "Alex", "variants": ["Tom"]},
           {"original": "Mary Ann", "pseudonym": "Sam"}]

# QualCoder's own CREATE TABLE statements (3.8.2:__main__.py:2347-2405),
# transcribed rather than paraphrased, including the tables this server
# never reads: the point of the fixture is the shape QualCoder makes.
SCHEMA = """
CREATE TABLE project (databaseversion text, date text, memo text,about text, bookmarkfile integer, bookmarkpos integer, codername text, recently_used_codes text);
CREATE TABLE source (id integer primary key, name text, fulltext text, mediapath text, memo text, owner text, date text, av_text_id integer, risid integer, unique(name));
CREATE TABLE code_image (imid integer primary key,id integer,x1 integer, y1 integer, width integer, height integer, cid integer, memo text, date text, owner text, important integer, pdf_page integer);
CREATE TABLE code_av (avid integer primary key,id integer,pos0 integer, pos1 integer, cid integer, memo text, date text, owner text, important integer);
CREATE TABLE annotation (anid integer primary key, fid integer,pos0 integer, pos1 integer, memo text, owner text, date text, unique(fid,pos0,pos1,owner));
CREATE TABLE attribute_type (name text primary key, date text, owner text, memo text, caseOrFile text, valuetype text);
CREATE TABLE attribute (attrid integer primary key, name text, attr_type text, value text, id integer, date text, owner text, unique(name,attr_type,id));
CREATE TABLE case_text (id integer primary key, caseid integer, fid integer, pos0 integer, pos1 integer, owner text, date text, memo text);
CREATE TABLE cases (caseid integer primary key, name text, memo text, owner text,date text, constraint ucm unique(name));
CREATE TABLE code_cat (catid integer primary key, name text, owner text, date text, memo text, supercatid integer, unique(name));
CREATE TABLE code_text (ctid integer primary key, cid integer, fid integer,seltext text, pos0 integer, pos1 integer, owner text, date text, memo text, avid integer, important integer, unique(cid,fid,pos0,pos1, owner));
CREATE TABLE code_name (cid integer primary key, name text, memo text, catid integer, owner text,date text, color text, unique(name));
CREATE TABLE journal (jid integer primary key, name text, jentry text, date text, owner text, unique(name));
CREATE TABLE stored_sql (title text, description text, grouper text, ssql text, unique(title));
CREATE TABLE graph (grid integer primary key, name text, description text, date text, scene_width integer, scene_height integer, unique(name));
CREATE TABLE coder_names (name TEXT UNIQUE NOT NULL);
"""

# The column and the four views, exactly as QualCoder's own routine
# creates them (app.py:1470-1475 then :1518-1561 at 9bddf17), and
# together, because that routine never makes one without the other.
VISIBILITY_COLUMN = ("ALTER TABLE coder_names ADD COLUMN visibility INTEGER "
                     "NOT NULL DEFAULT 1 CHECK (visibility IN (0, 1))")
VISIBILITY_VIEWS = [
    """CREATE VIEW IF NOT EXISTS code_image_visible AS
       SELECT t.* FROM code_image t
       WHERE NOT EXISTS (SELECT 1 FROM coder_names c
                         WHERE c.name = t.owner AND c.visibility = 0);""",
    """CREATE VIEW IF NOT EXISTS code_text_visible AS
       SELECT t.* FROM code_text t
       WHERE NOT EXISTS (SELECT 1 FROM coder_names c
                         WHERE c.name = t.owner AND c.visibility = 0);""",
    """CREATE VIEW IF NOT EXISTS code_av_visible AS
       SELECT t.* FROM code_av t
       WHERE NOT EXISTS (SELECT 1 FROM coder_names c
                         WHERE c.name = t.owner AND c.visibility = 0);""",
    """CREATE VIEW IF NOT EXISTS annotation_visible AS
       SELECT t.* FROM annotation t
       WHERE NOT EXISTS (SELECT 1 FROM coder_names c
                         WHERE c.name = t.owner AND c.visibility = 0);""",
]


def build_project(folder: Path, text: str = TEXT) -> Path:
    """A QualCoder v14 project with one coded text file and its neighbours."""
    folder.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(folder / "data.qda"))
    con.executescript(SCHEMA)
    con.execute("INSERT INTO project (databaseversion, date, memo, "
                "codername) VALUES ('v14','2026-01-01','','TestCoder')")
    con.execute("INSERT INTO code_name (cid, name, memo, catid, owner, date, "
                "color) VALUES (1,'Stress','',NULL,'TestCoder','d','#FF0000')")
    con.execute("INSERT INTO code_name (cid, name, memo, catid, owner, date, "
                "color) VALUES (2,'Coping','',NULL,'TestCoder','d','#00FF00')")
    con.execute(
        "INSERT INTO source (id,name,fulltext,mediapath,memo,owner,date) "
        "VALUES (1,'interview_01.txt',?,NULL,'','TestCoder','d')", (text,))
    con.execute(
        "INSERT INTO source (id,name,fulltext,mediapath,memo,owner,date) "
        "VALUES (2,'paper.pdf','extracted page text','/docs/paper.pdf','',"
        "'TestCoder','d')")
    con.execute(
        "INSERT INTO source (id,name,fulltext,mediapath,memo,owner,date) "
        "VALUES (3,'clip.mp3',NULL,'/audio/clip.mp3','','TestCoder','d')")
    con.execute(
        "INSERT INTO source (id,name,fulltext,mediapath,memo,owner,date) "
        "VALUES (4,'quiet.txt','nothing to see here',NULL,'','TestCoder','d')")
    con.execute("INSERT INTO cases (caseid,name,memo,owner,date) "
                "VALUES (1,'Participant A','','TestCoder','d')")
    con.commit()
    con.close()
    return folder


def add_coding(folder: Path, ctid: int, cid: int, pos0: int, pos1: int,
               owner: str = "TestCoder", fid: int = 1, memo: str = "",
               important: int = 0, seltext: str = None, text: str = TEXT):
    con = sqlite3.connect(str(folder / "data.qda"))
    con.execute(
        "INSERT INTO code_text (ctid,cid,fid,seltext,pos0,pos1,owner,date,"
        "memo,important) VALUES (?,?,?,?,?,?,?,'d',?,?)",
        (ctid, cid, fid, text[pos0:pos1] if seltext is None else seltext,
         pos0, pos1, owner, memo, important))
    con.commit()
    con.close()


def add_annotation(folder: Path, anid: int, pos0: int, pos1: int,
                   owner: str = "TestCoder", fid: int = 1, memo: str = "n"):
    con = sqlite3.connect(str(folder / "data.qda"))
    con.execute("INSERT INTO annotation (anid,fid,pos0,pos1,memo,owner,date) "
                "VALUES (?,?,?,?,?,?,'d')", (anid, fid, pos0, pos1, memo,
                                             owner))
    con.commit()
    con.close()


def add_case_link(folder: Path, link_id: int, pos0: int, pos1: int,
                  caseid: int = 1, fid: int = 1, owner: str = "TestCoder"):
    con = sqlite3.connect(str(folder / "data.qda"))
    con.execute("INSERT INTO case_text (id,caseid,fid,pos0,pos1,memo,owner,"
                "date) VALUES (?,?,?,?,?,'',?,'d')",
                (link_id, caseid, fid, pos0, pos1, owner))
    con.commit()
    con.close()


def hide_coder(folder: Path, name: str):
    """The column and the four views together, as QualCoder makes them."""
    con = sqlite3.connect(str(folder / "data.qda"))
    cur = con.cursor()
    if "visibility" not in {r[1] for r in
                            cur.execute("PRAGMA table_info(coder_names)")}:
        cur.execute(VISIBILITY_COLUMN)
    for ddl in VISIBILITY_VIEWS:
        cur.execute(ddl)
    cur.executemany(
        "INSERT OR REPLACE INTO coder_names (name, visibility) VALUES (?, ?)",
        [("TestCoder", 1), (name, 0)])
    con.commit()
    con.close()


def query(folder: Path, sql: str, params=()):
    con = sqlite3.connect(str(folder / "data.qda"))
    con.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in con.execute(sql, params).fetchall()]
    finally:
        con.close()


def backups(folder: Path):
    return sorted(p.name for p in folder.parent.glob(f"{folder.stem}_backup_*"))


@pytest.fixture
def project(tmp_path):
    """A selected project with codings, an annotation and a case link."""
    folder = build_project(tmp_path / "study.qda")
    add_coding(folder, 1, 1, 0, 6)                      # exactly "Thomas"
    add_coding(folder, 2, 1, 2, 10)                     # cuts into "Thomas"
    add_coding(folder, 3, 2, 34, 42, owner="Alice")     # "Mary Ann"
    add_coding(folder, 4, 2, 63, 68)                    # "Later", disjoint
    add_annotation(folder, 1, 0, 20)
    add_case_link(folder, 1, 0, len(TEXT))
    write_fixture_sidecar(str(folder))
    original_db, original_path = server.db, server.current_project_path
    server.db = QualcoderDatabase(str(folder))
    server.current_project_path = str(folder)
    yield folder
    if server.db is not None:
        try:
            server.db.close()
        except Exception:
            pass
    server.db, server.current_project_path = original_db, original_path


def call(**kwargs):
    return json.loads(server.pseudonymise_source(**kwargs))


def preview_of(**kwargs):
    kwargs.setdefault("mapping", MAPPING)
    return call(**kwargs)


def execute_from(out, **kwargs):
    """Execute exactly as the preview's own recipe says to."""
    arguments = dict(out["execute_with"]["arguments"])
    arguments.pop("use_project_pseudonyms", None)
    arguments.setdefault("mapping", MAPPING)
    arguments.update(kwargs)
    return call(**arguments)


def _house_rules(texts, labels=None):
    forbidden_spellings = ("color", "colors", "behavior", "organize",
                           "recognize", "authorization", "analyze",
                           "labeled", "favor", "pseudonymize",
                           "pseudonymization", "anonymize", "anonymization")
    for index, text in enumerate(texts):
        label = (labels[index] if labels else f"text {index}")
        assert "—" not in text, label
        lowered = text.lower()
        for word in forbidden_spellings:
            pattern = r"(?<![A-Za-z_])" + word + r"(?![A-Za-z_])"
            assert not re.search(pattern, lowered), (label, word)


# =============================================================================
# THE PREVIEW
# =============================================================================

class TestPreview:

    def test_the_preview_writes_nothing_and_returns_a_token(self, project):
        before = query(project, "SELECT fulltext FROM source WHERE id=1")
        out = preview_of()
        assert out["requires_confirmation"] is True
        assert re.fullmatch(r"qcp1\.\d+\.[0-9a-f]{8}\.[0-9a-f]{32}",
                            out["preview_token"])
        assert out["token_valid_for_minutes"] == 60
        assert query(project,
                     "SELECT fulltext FROM source WHERE id=1") == before
        assert backups(project) == []
        assert server.db.read_only is True

    def test_the_preview_counts_every_replacement_and_every_row(self,
                                                                project):
        body = preview_of()["preview"]
        assert body["totals"]["replacements"] == 5
        assert body["totals"]["files"] == 1
        item = body["files"][0]
        assert item["file_id"] == 1
        assert item["text_length"] == len(TEXT)
        assert item["new_text_length"] == len(TEXT) - 10
        assert [(r["entry"], r["count"]) for r in item["replacements"]] == \
            [(0, 4), (1, 1)]
        assert item["codings"]["total"] == 4
        assert item["annotations"]["total"] == 1
        assert item["case_links"]["total"] == 1
        assert item["case_links"]["whole_file"] == 1

    def test_a_file_with_no_match_is_not_listed_as_touched(self, project):
        body = preview_of()["preview"]
        assert [item["file_id"] for item in body["files"]] == [1]

    def test_a_pdf_and_a_media_file_are_skipped_with_a_reason(self, project):
        body = preview_of()["preview"]
        assert {item["file_id"]: item["reason"]
                for item in body["skipped_files"]} == {2: "pdf_source",
                                                       3: "no_fulltext"}

    def test_an_unknown_file_id_is_a_reason_not_a_failure(self, project):
        body = preview_of(file_ids=[1, 999])["preview"]
        assert {"file_id": 999, "reason": "unknown_file_id"} in \
            body["skipped_files"]
        assert body["totals"]["replacements"] == 5

    def test_a_selection_with_nothing_eligible_is_an_error_with_the_reasons(
            self, project):
        out = preview_of(file_ids=[2, 3])
        assert "No eligible text source" in out["error"]
        assert {item["file_id"] for item in out["skipped_files"]} == {2, 3}

    def test_context_is_off_by_default_and_capped_when_on(self, project):
        plain = preview_of()["preview"]["files"][0]["replacements"][0]
        assert "context" not in plain
        wide = preview_of(include_context=True,
                          context_chars=10_000)["preview"]
        contexts = wide["files"][0]["replacements"][0]["context"]
        assert all(len(c) <= 2 * P.MAX_CONTEXT_CHARS + 6 for c in contexts)

    def test_spans_are_truncated_on_request_and_say_so(self, project):
        item = preview_of(max_spans_per_entry=2)["preview"]["files"][0]
        first = item["replacements"][0]
        assert len(first["spans"]) == 2
        assert first["count"] == 4
        assert first["spans_truncated"] is True

    def test_the_execute_recipe_repeats_the_bound_arguments(self, project):
        out = preview_of(file_ids=[1], case_mode="insensitive")
        assert out["execute_with"]["tool"] == "pseudonymise_source"
        arguments = out["execute_with"]["arguments"]
        assert arguments["file_ids"] == [1]
        assert arguments["case_mode"] == "insensitive"
        assert arguments["overlap_policy"] == "snap_to_pseudonym"
        assert arguments["preview_token"] == out["preview_token"]

    def test_the_internal_effect_block_never_reaches_the_model(self,
                                                               project):
        """It is an internal structure, and a model that tried to copy it
        back would be sending the server its own fingerprint.

        Asserted as a KEY rather than as a substring of the serialised
        payload: pytest builds its temporary directory from the test's
        own name, so a substring check here matched the project path and
        passed for the wrong reason whatever the code did.
        """
        out = preview_of()
        assert "_effect" not in out
        assert "_effect" not in out["preview"]
        assert all(not key.startswith("_") for key in out["preview"])


class TestPreviewDiagnostics:

    def test_a_pre_existing_pseudonym_is_reported_and_warned_about(
            self, tmp_path):
        folder = build_project(tmp_path / "p.qda", "Thomas met Alex today.")
        write_fixture_sidecar(str(folder))
        server.db = QualcoderDatabase(str(folder))
        server.current_project_path = str(folder)
        try:
            out = preview_of()
            item = out["preview"]["files"][0]
            assert item["pre_existing_pseudonym_occurrences"] == [
                {"entry": 0, "pseudonym": "Alex", "count": 1,
                 "spans": [[11, 15]]}]
            assert any("already occurs in the text" in w
                       for w in out["warnings"])
        finally:
            server.db.close()
            server.db = None

    def test_case_variants_are_reported_under_exact_mode(self, tmp_path):
        folder = build_project(tmp_path / "p.qda", "Thomas and THOMAS.")
        write_fixture_sidecar(str(folder))
        server.db = QualcoderDatabase(str(folder))
        server.current_project_path = str(folder)
        try:
            item = preview_of()["preview"]["files"][0]
            assert item["case_variants_seen"] == [
                {"entry": 0, "form": "Thomas", "other_case_count": 1}]
        finally:
            server.db.close()
            server.db = None

    def test_shared_pseudonyms_are_reported_and_warned_about(self, project):
        out = preview_of(mapping=[
            {"original": "Thomas", "pseudonym": "Alex"},
            {"original": "Mary Ann", "pseudonym": "Alex"}])
        assert out["preview"]["shared_pseudonyms"] == [
            {"pseudonym": "Alex", "entries": [0, 1]}]
        assert any("become one identity" in w for w in out["warnings"])

    def test_every_warning_follows_the_house_rules(self, project):
        out = preview_of()
        _house_rules(out["warnings"] + [out["hint"]])


class TestResidue:

    def test_the_residue_counts_where_the_names_remain(self, project):
        con = sqlite3.connect(str(project / "data.qda"))
        con.execute("UPDATE source SET memo='about Thomas' WHERE id=1")
        con.execute("UPDATE cases SET name='Thomas case' WHERE caseid=1")
        con.execute("INSERT INTO journal (jid,name,jentry,date,owner) "
                    "VALUES (1,'Entry 1','Thomas was late','d','TestCoder')")
        con.execute("INSERT INTO attribute_type (name,date,owner,memo,"
                    "caseOrFile,valuetype) VALUES ('Role','d','TestCoder','',"
                    "'case','character')")
        con.execute("INSERT INTO attribute (attrid,name,attr_type,value,id,"
                    "date,owner) VALUES (1,'Role','case','Thomas the elder',"
                    "1,'d','TestCoder')")
        con.commit()
        con.close()
        server.db.close()
        server.db = QualcoderDatabase(str(project))
        residue = preview_of()["preview"]["residue"]
        assert residue["memos"]["source"] == 1
        assert residue["memos"]["journal"] == 1
        assert residue["case_names"] == 1
        assert residue["attribute_values"] == 1
        assert residue["memos"]["code_text"] == 0
        assert "ai_data" in residue["ai_data_note"]

    def test_a_name_only_in_a_private_note_is_neither_counted_nor_mentioned(
            self, project):
        """S-P2: the private zone is never read, so it cannot be counted.
        How many rows carry one IS reported, as a number."""
        con = sqlite3.connect(str(project / "data.qda"))
        con.execute("UPDATE source SET memo='nothing public here "
                    "##### Thomas is the participant' WHERE id=1")
        con.commit()
        con.close()
        server.db.close()
        server.db = QualcoderDatabase(str(project))
        residue = preview_of()["preview"]["residue"]
        assert residue["memos"]["source"] == 0
        assert residue["memos_with_private_zones_not_scanned"] == 1
        assert "Thomas" not in json.dumps(residue)

    def test_the_public_half_of_a_memo_with_a_private_zone_is_scanned(
            self, project):
        con = sqlite3.connect(str(project / "data.qda"))
        con.execute("UPDATE source SET memo='Thomas spoke ##### and Mary Ann "
                    "did not' WHERE id=1")
        con.commit()
        con.close()
        server.db.close()
        server.db = QualcoderDatabase(str(project))
        residue = preview_of()["preview"]["residue"]
        assert residue["memos"]["source"] == 1
        assert residue["memos_with_private_zones_not_scanned"] == 1

    def test_the_sidecars_are_reported_by_presence_and_never_parsed(
            self, project):
        (project / "pseudonyms.json").write_text("[]", encoding="utf-8")
        (project / "speakers.json").write_text(
            '[{"name": "Thomas", "fmt": {}}]', encoding="utf-8")
        residue = preview_of()["preview"]["residue"]
        assert residue["sidecars_present"] == ["pseudonyms.json",
                                               "speakers.json"]
        assert "speaker_regex.json" not in residue["sidecars_present"]

    def test_the_residue_can_be_turned_off(self, project):
        assert "residue" not in preview_of(scan_residue=False)["preview"]

    def test_the_residue_scan_never_returns_content(self, project):
        con = sqlite3.connect(str(project / "data.qda"))
        con.execute("UPDATE source SET memo='about Thomas' WHERE id=1")
        con.commit()
        con.close()
        server.db.close()
        server.db = QualcoderDatabase(str(project))
        residue = preview_of()["preview"]["residue"]
        assert "Thomas" not in json.dumps(residue)


# =============================================================================
# THE TOKEN
# =============================================================================

class TestToken:

    def test_the_happy_path(self, project):
        out = preview_of()
        result = execute_from(out)
        assert result["success"] is True
        assert result["preview_verified"] is True
        assert query(project, "SELECT fulltext FROM source WHERE id=1"
                     )[0]["fulltext"].startswith("Alex said")

    @pytest.mark.parametrize("changed", [
        {"mapping": [{"original": "Thomas", "pseudonym": "Zed"}]},
        {"case_mode": "insensitive"},
        {"overlap_policy": "qualcoder_edit_parity"},
        {"file_ids": [1]},
    ], ids=["mapping", "case_mode", "overlap_policy", "file_ids"])
    def test_a_bound_argument_that_differs_is_another_operation(
            self, project, changed):
        out = preview_of()
        refused = call(preview_token=out["preview_token"],
                       **{**{"mapping": MAPPING}, **changed})
        assert refused["reason"] == "token_other_operation"
        assert refused["nothing_changed"] is True
        assert backups(project) == []

    @pytest.mark.parametrize("unbound", [
        {"include_context": True},
        {"context_chars": 5},
        {"scan_residue": False},
        {"max_spans_per_entry": 1},
        {"record_in_journal": False},
    ], ids=["include_context", "context_chars", "scan_residue",
            "max_spans", "record_in_journal"])
    def test_an_unbound_argument_that_differs_changes_nothing(
            self, project, unbound):
        """The ruling of 2026-09-14, in its most testable form.

        These five arguments change what the preview SHOWS or whether the
        run records itself, never what happens to the text or to a row.
        Passing a different value on the execute call is therefore not
        "a different operation": it executes.
        """
        out = preview_of()
        result = call(mapping=MAPPING, preview_token=out["preview_token"],
                      **unbound)
        assert result.get("success") is True, result
        assert "reason" not in result

    def test_an_expired_token_sends_the_model_back_to_the_preview(
            self, project, monkeypatch):
        """No test sleeps and none reads the clock twice: the token's own
        issue time is read from the preview and the verifier's clock is
        moved past it by exactly the lifetime plus a minute."""
        out = preview_of()
        issued = int(out["preview_token"].split(".")[1])
        monkeypatch.setattr(
            pt, "_now",
            lambda: issued + pt.TOKEN_MAX_AGE_SECONDS + 60)
        refused = execute_from(out)
        assert refused["reason"] == "token_expired"
        assert refused["nothing_changed"] is True
        assert backups(project) == []

    def test_a_token_one_second_inside_the_window_still_executes(
            self, project, monkeypatch):
        """The other side of the same boundary, so the expiry test is a
        real comparison rather than a one-directional one."""
        out = preview_of()
        issued = int(out["preview_token"].split(".")[1])
        monkeypatch.setattr(pt, "_now",
                            lambda: issued + pt.TOKEN_MAX_AGE_SECONDS)
        assert execute_from(out)["success"] is True

    @pytest.mark.parametrize("bad", ["", "not-a-token", "qcp1.1.2.3",
                                     "qcp1.1.aaaaaaaa." + "z" * 32])
    def test_a_malformed_token_is_named_as_malformed(self, project, bad):
        refused = call(mapping=MAPPING, preview_token=bad)
        assert refused["reason"] == "token_malformed"

    def test_a_fulltext_change_to_a_touched_file_invalidates_the_token(
            self, project):
        out = preview_of()
        con = sqlite3.connect(str(project / "data.qda"))
        con.execute("UPDATE source SET fulltext = fulltext || ' More.' "
                    "WHERE id=1")
        con.commit()
        con.close()
        server.db.close()
        server.db = QualcoderDatabase(str(project))
        refused = execute_from(out)
        assert refused["reason"] == "project_changed"
        assert backups(project) == []

    def test_a_new_coding_on_a_touched_file_invalidates_the_token(
            self, project):
        """Every row of a touched file is in the signed state, not only
        the rows that move: the counts the researcher approved changed."""
        out = preview_of()
        add_coding(project, 9, 1, 55, 61)
        server.db.close()
        server.db = QualcoderDatabase(str(project))
        refused = execute_from(out)
        assert refused["reason"] == "project_changed"

    @pytest.mark.parametrize("sql", [
        "UPDATE code_text SET important = 1 WHERE ctid = 3",
        "UPDATE code_text SET owner = 'Carol' WHERE ctid = 3",
    ], ids=["important", "owner"])
    def test_a_row_change_the_counts_cannot_see_invalidates_the_token(
            self, project, sql):
        """What the ROW DIGEST is for, isolated from everything else.

        Adding or removing a coding moves the counts, so a token would
        fail on the effect block alone and a digest of nothing would
        still look correct. These two do not move a single count: the
        important flag and a change of visible owner leave every number
        in the preview's effect block exactly as it was. They are in the
        digest, so the token fails, which is right: the researcher
        approved a run over those rows, and these are not those rows.
        """
        out = preview_of()
        con = sqlite3.connect(str(project / "data.qda"))
        con.execute(sql)
        con.commit()
        con.close()
        server.db.close()
        server.db = QualcoderDatabase(str(project))
        refused = execute_from(out)
        assert refused["reason"] == "project_changed"
        assert refused["nothing_changed"] is True
        assert backups(project) == []

    def test_an_import_invalidates_a_token_issued_for_every_file(
            self, project):
        """With file_ids=None the eligible set is part of the state, so a
        new text source changes what the run would cover."""
        out = preview_of()
        con = sqlite3.connect(str(project / "data.qda"))
        con.execute("INSERT INTO source (id,name,fulltext,mediapath,memo,"
                    "owner,date) VALUES (5,'new.txt','Thomas again',NULL,'',"
                    "'TestCoder','d')")
        con.commit()
        con.close()
        server.db.close()
        server.db = QualcoderDatabase(str(project))
        refused = execute_from(out)
        assert refused["reason"] == "project_changed"

    def test_an_import_leaves_an_explicit_selection_valid(self, project):
        """The other half of the same rule: a token for file_ids=[1] says
        nothing about a file that did not exist, so it still applies."""
        out = preview_of(file_ids=[1])
        con = sqlite3.connect(str(project / "data.qda"))
        con.execute("INSERT INTO source (id,name,fulltext,mediapath,memo,"
                    "owner,date) VALUES (5,'new.txt','Thomas again',NULL,'',"
                    "'TestCoder','d')")
        con.commit()
        con.close()
        server.db.close()
        server.db = QualcoderDatabase(str(project))
        result = execute_from(out)
        assert result["success"] is True
        assert query(project, "SELECT fulltext FROM source WHERE id=5"
                     )[0]["fulltext"] == "Thomas again"

    def test_a_second_run_of_the_same_token_cannot_repeat_the_write(
            self, project):
        out = preview_of()
        assert execute_from(out)["success"] is True
        again = execute_from(out)
        assert again["reason"] == "project_changed"
        assert again["nothing_changed"] is True

    def test_a_rotated_secret_is_named_as_a_possible_cause(self, project,
                                                           tmp_path):
        out = preview_of()
        (pt.STATE_HOME / pt.SECRET_FILENAME).write_text(
            "0" * 64 + "\n", encoding="ascii")
        refused = execute_from(out)
        assert refused["reason"] in ("token_other_operation",
                                     "project_changed")
        assert "rotat" in refused["error"]

    def test_an_unusable_state_home_refuses_rather_than_writing(
            self, project, monkeypatch):
        def unavailable():
            raise pt.PreviewSecretUnavailable(pt.SECRET_UNAVAILABLE_MESSAGE)
        monkeypatch.setattr(pt, "load_secret", unavailable)
        monkeypatch.setattr(server, "load_secret", unavailable,
                            raising=False)
        out = preview_of()
        assert out["reason"] == "preview_secret_unavailable"
        assert backups(project) == []

    def test_every_refusal_text_follows_the_house_rules(self, project):
        texts = []
        out = preview_of()
        for bad in ("qcp1.1.2.3", ):
            texts.append(call(mapping=MAPPING, preview_token=bad)["error"])
        texts.append(call(mapping=[{"original": "Thomas",
                                    "pseudonym": "Zed"}],
                          preview_token=out["preview_token"])["error"])
        _house_rules(texts)


# =============================================================================
# THE HIDDEN-CODER RULE (X1)
# =============================================================================

class TestHiddenCoders:

    def test_a_pure_shift_of_a_hidden_row_needs_no_override(self, project):
        """Ruling X1's exemption: the row moves because the text moved,
        and nothing about what the coder marked has changed."""
        add_coding(project, 10, 1, 63, 68, owner="Hidden Coder")
        hide_coder(project, "Hidden Coder")
        server.db.close()
        server.db = QualcoderDatabase(str(project))
        out = preview_of()
        hidden = out["preview"]["hidden_coder_rows"]
        assert hidden["shifted"] == 1
        assert hidden["override_required"] is False
        assert "allow_hidden_coder" not in out["execute_with"]["arguments"]
        result = execute_from(out)
        assert result["success"] is True
        assert result["hidden_coder_rows_updated"] == 1

    def test_a_resize_of_a_hidden_row_requires_the_override(self, project):
        add_coding(project, 10, 1, 0, 6, owner="Hidden Coder")
        hide_coder(project, "Hidden Coder")
        server.db.close()
        server.db = QualcoderDatabase(str(project))
        out = preview_of()
        hidden = out["preview"]["hidden_coder_rows"]
        assert hidden["resized"] == 1
        assert hidden["override_required"] is True
        assert out["execute_with"]["arguments"]["allow_hidden_coder"] is True
        refused = call(mapping=MAPPING, preview_token=out["preview_token"])
        assert refused["reason"] == "hidden_coder_override_required"
        assert refused["nothing_changed"] is True
        assert backups(project) == []

    def test_the_override_lets_the_resize_proceed(self, project):
        add_coding(project, 10, 1, 0, 6, owner="Hidden Coder")
        hide_coder(project, "Hidden Coder")
        server.db.close()
        server.db = QualcoderDatabase(str(project))
        out = preview_of()
        result = execute_from(out, allow_hidden_coder=True)
        assert result["success"] is True
        assert query(project, "SELECT pos0,pos1 FROM code_text WHERE ctid=10"
                     ) == [{"pos0": 0, "pos1": 4}]

    def test_a_hidden_coder_is_never_named_anywhere(self, project):
        add_coding(project, 10, 1, 0, 6, owner="Hidden Coder")
        hide_coder(project, "Hidden Coder")
        server.db.close()
        server.db = QualcoderDatabase(str(project))
        out = preview_of()
        assert "Hidden Coder" not in json.dumps(out)
        refused = call(mapping=MAPPING, preview_token=out["preview_token"])
        assert "Hidden Coder" not in json.dumps(refused)
        result = execute_from(out, allow_hidden_coder=True)
        assert "Hidden Coder" not in json.dumps(result)

    def test_a_hidden_annotation_counts_too(self, project):
        """QualCoder creates an `annotation_visible` view, so an
        annotation can belong to a hidden coder; a count nested under
        `codings` would be the wrong place for it."""
        add_annotation(project, 9, 0, 6, owner="Hidden Coder")
        hide_coder(project, "Hidden Coder")
        server.db.close()
        server.db = QualcoderDatabase(str(project))
        out = preview_of()
        assert out["preview"]["hidden_coder_rows"]["resized"] == 1
        assert out["preview"]["hidden_coder_rows"]["override_required"] is True

    def test_a_case_link_is_never_hidden(self, project):
        """`case_text` has no visibility view at either pin, so QualCoder
        does not hide case links and neither does this accounting."""
        add_case_link(project, 9, 0, 6, owner="Hidden Coder")
        hide_coder(project, "Hidden Coder")
        server.db.close()
        server.db = QualcoderDatabase(str(project))
        out = preview_of()
        assert out["preview"]["hidden_coder_rows"]["override_required"] \
            is False


# =============================================================================
# AI ATTRIBUTION THROUGH THE D7 HELPER
# =============================================================================

class TestAiAttribution:

    def _set_names(self, folder, names):
        """Rename the project's AI coder through the real writer.

        By hand the sidecar would be whatever this test thinks the format
        is, which is how a fixture comes to test its own invention. The
        writer is the one that ships, so the history it builds is the
        history the helper will read.
        """
        from qualcoder_mcp.project_settings import write_ai_coder_name
        for name in names:
            write_ai_coder_name(folder, name, note="t",
                                host_declaration=None)

    def test_rows_under_every_historical_name_count_as_ours(self, project):
        """The set comes from the helper, never from a literal, so a
        project that renamed its AI coder twice still recognises its own
        earlier work (D7 1.2)."""
        from qualcoder_mcp.project_settings import ai_coder_names_for_project
        self._set_names(project, ["Older Still", "Old One", "Qwen 3.8 6bit"])
        names = set(ai_coder_names_for_project(project))
        assert {"Qwen 3.8 6bit", "Old One", "Older Still"} <= names
        add_coding(project, 10, 1, 0, 6, owner="Old One")
        add_coding(project, 11, 1, 19, 22, owner="Qwen 3.8 6bit")
        server.db.close()
        server.db = QualcoderDatabase(str(project))
        codings = preview_of()["preview"]["files"][0]["codings"]
        assert codings["ai_owned"] == 2
        assert {entry["owner"] for entry in codings["by_owner"]} == \
            {"TestCoder", "Alice"}

    def test_qualcoders_own_assistant_string_is_labelled_not_claimed(
            self, project):
        """D3 Q5 (a): "AI Agent" rows are another tool's work. They stay
        in by_owner with a heuristic label and are never counted as
        ours."""
        add_coding(project, 10, 1, 0, 6, owner=KNOWN_AI_ASSISTANT_OWNER)
        server.db.close()
        server.db = QualcoderDatabase(str(project))
        codings = preview_of()["preview"]["files"][0]["codings"]
        entry = [e for e in codings["by_owner"]
                 if e["owner"] == KNOWN_AI_ASSISTANT_OWNER]
        assert entry and entry[0]["known_ai_assistant"] is True
        assert codings["ai_owned"] == 0

    def test_the_counts_are_of_changed_rows_not_of_every_row(self, project):
        """A row the run leaves exactly where it is is not "affected",
        and reporting it as such would overstate the collateral on a run
        that moves two codings in a file that holds thirty.

        The fixture has to contain an UNCHANGED row for this to mean
        anything: in a file whose first character is a name, every row
        moves, and the two readings agree. Here the names come later, so
        the first coding is untouched and the difference is visible.
        """
        text = "aaa bbb ccc Thomas ddd"
        folder = build_project(tmp_path_for(project) / "mixed.qda", text)
        add_coding(folder, 1, 1, 0, 3, owner="Alice", seltext="aaa",
                   text=text)
        add_coding(folder, 2, 1, 12, 18, owner="Alice", seltext="Thomas",
                   text=text)
        add_coding(folder, 3, 1, 19, 22, owner=DEFAULT_AI_CODER_NAME,
                   seltext="ddd", text=text)
        write_fixture_sidecar(str(folder))
        server.db.close()
        server.db = QualcoderDatabase(str(folder))
        server.current_project_path = str(folder)
        codings = preview_of()["preview"]["files"][0]["codings"]
        assert codings["total"] == 3
        assert codings["unchanged"] == 1
        assert codings["changed"] == 2
        assert codings["by_owner"] == [{"owner": "Alice", "codings": 1}]
        assert codings["ai_owned"] == 1
        assert sum(e["codings"] for e in codings["by_owner"]) \
            + codings["ai_owned"] == codings["changed"]


# =============================================================================
# THE WRITE
# =============================================================================

class TestWrite:

    def test_every_stored_quote_matches_its_new_span(self, project):
        execute_from(preview_of())
        text = query(project, "SELECT fulltext FROM source WHERE id=1"
                     )[0]["fulltext"]
        for row in query(project, "SELECT ctid,pos0,pos1,seltext FROM "
                                  "code_text ORDER BY ctid"):
            assert text[row["pos0"]:row["pos1"]] == row["seltext"], row

    def test_nothing_but_positions_and_quotes_is_touched(self, project):
        before = query(project, "SELECT ctid,cid,fid,owner,date,memo,avid,"
                                "important FROM code_text ORDER BY ctid")
        execute_from(preview_of())
        after = query(project, "SELECT ctid,cid,fid,owner,date,memo,avid,"
                               "important FROM code_text ORDER BY ctid")
        assert before == after

    def test_an_important_mark_and_a_memo_survive_the_run(self, project):
        add_coding(project, 11, 1, 0, 6, owner="Alice", memo="keep me",
                   important=1)
        server.db.close()
        server.db = QualcoderDatabase(str(project))
        execute_from(preview_of())
        row = query(project, "SELECT memo,important FROM code_text "
                             "WHERE ctid=11")[0]
        assert row == {"memo": "keep me", "important": 1}

    def test_a_file_with_no_match_is_not_rewritten_at_all(self, project):
        before = query(project, "SELECT fulltext FROM source WHERE id=4")
        execute_from(preview_of())
        assert query(project, "SELECT fulltext FROM source WHERE id=4") \
            == before

    def test_a_backup_is_always_taken(self, project):
        execute_from(preview_of())
        assert len(backups(project)) == 1

    def test_the_whole_file_case_link_still_covers_the_whole_file(
            self, project):
        execute_from(preview_of())
        text = query(project, "SELECT fulltext FROM source WHERE id=1"
                     )[0]["fulltext"]
        link = query(project, "SELECT pos0,pos1 FROM case_text WHERE id=1")[0]
        assert (link["pos0"], link["pos1"]) == (0, len(text))

    def test_the_other_whole_file_case_link_convention_also_survives(
            self, project):
        """QualCoder master writes `pos1 = len(text)` for a whole-file
        case link and 3.8.2's file manager wrote `len - 1`. Both are
        whole-file links, this server's own duplicate check treats them
        as one, and neither is normalised into the other here: a run
        makes the minimal change, so a `len - 1` link comes out as
        `len - 1` of the new text."""
        con = sqlite3.connect(str(project / "data.qda"))
        con.execute("UPDATE case_text SET pos1 = ? WHERE id = 1",
                    (len(TEXT) - 1,))
        con.commit()
        con.close()
        server.db.close()
        server.db = QualcoderDatabase(str(project))
        out = preview_of()
        assert out["preview"]["files"][0]["case_links"]["whole_file"] == 1
        assert execute_from(out)["success"] is True
        text = query(project, "SELECT fulltext FROM source WHERE id=1"
                     )[0]["fulltext"]
        link = query(project, "SELECT pos0,pos1 FROM case_text WHERE id=1")[0]
        assert (link["pos0"], link["pos1"]) == (0, len(text) - 1)

    def test_a_row_untouched_by_the_run_keeps_its_stored_quote_verbatim(
            self, project):
        """A GUI-written quote holds U+2029 where the text holds a
        newline (QualCoder stores `selectedText()` unchanged,
        code_text.py:4869). A row the run does not affect is not
        rewritten, so its bytes survive."""
        text = "Line one\nThomas spoke\nLine three"
        folder = build_project(tmp_path_for(project) / "u.qda", text)
        add_coding(folder, 1, 1, 0, 8, seltext="Line one", text=text)
        add_coding(folder, 2, 1, 22, 32, seltext="Line three", text=text)
        write_fixture_sidecar(str(folder))
        server.db.close()
        server.db = QualcoderDatabase(str(folder))
        server.current_project_path = str(folder)
        out = preview_of(mapping=[{"original": "Thomas",
                                   "pseudonym": "Alexander"}])
        assert execute_from(out, mapping=[{"original": "Thomas",
                                           "pseudonym": "Alexander"}]
                            )["success"] is True
        rows = {r["ctid"]: r for r in
                query(folder, "SELECT ctid,seltext,pos0,pos1 FROM code_text")}
        assert rows[1]["seltext"] == "Line one"        # untouched, unmoved
        assert rows[2]["seltext"] == "Line three"      # moved, so refreshed
        assert " " not in rows[2]["seltext"]

    def test_a_quote_is_refreshed_even_when_the_span_does_not_move(
            self, project):
        """The case a "positions changed" test cannot reach.

        A pseudonym exactly as long as the name moves nothing: the
        coding's span is identical afterwards, and its stored quote is
        nonetheless wrong, because the text inside it changed. This is
        why the refresh keys on whether the span met an edit as well as
        on whether it moved, and it is the same row the hidden-coder
        rule treats as a PURE SHIFT, since the coder still marks the same
        passage.
        """
        text = "say Tom now, said Tom"
        folder = build_project(tmp_path_for(project) / "same.qda", text)
        add_coding(folder, 1, 1, 0, 11, seltext=text[0:11], text=text)
        write_fixture_sidecar(str(folder))
        server.db.close()
        server.db = QualcoderDatabase(str(folder))
        server.current_project_path = str(folder)
        mapping = [{"original": "Tom", "pseudonym": "Pat"}]
        out = preview_of(mapping=mapping)
        codings = out["preview"]["files"][0]["codings"]
        assert codings["unchanged"] == 1
        assert codings["seltext_refreshed"] == 1
        assert execute_from(out, mapping=mapping)["success"] is True
        row = query(folder, "SELECT pos0,pos1,seltext FROM code_text")[0]
        assert (row["pos0"], row["pos1"]) == (0, 11)
        assert row["seltext"] == "say Pat now"
        new_text = query(folder, "SELECT fulltext FROM source WHERE id=1"
                         )[0]["fulltext"]
        assert new_text[row["pos0"]:row["pos1"]] == row["seltext"]

    def test_the_parity_policy_deletes_and_says_how_many(self, project):
        out = preview_of(overlap_policy="qualcoder_edit_parity")
        assert out["preview"]["totals"]["rows_deleted"] >= 1
        assert any("would DELETE" in w for w in out["warnings"])
        result = execute_from(out)
        assert result["files"][0]["codings_deleted"] >= 1
        assert query(project, "SELECT ctid FROM code_text WHERE ctid=1") == []


def tmp_path_for(project: Path) -> Path:
    """The temporary directory a project fixture was built in."""
    return project.parent


class TestUniqueConstraint:

    def _collide(self, project):
        """Two codings of one code and coder that both become the name."""
        add_coding(project, 20, 1, 0, 6, owner="Bob")      # "Thomas"
        add_coding(project, 21, 1, 0, 4, owner="Bob")      # "Thom"
        server.db.close()
        server.db = QualcoderDatabase(str(project))

    def test_the_preview_lists_the_collision_and_warns(self, project):
        self._collide(project)
        out = preview_of()
        collisions = out["preview"]["files"][0][
            "unique_constraint_collisions"]["code_text"]
        assert collisions == [{"key": [1, 1, 0, 4, "Bob"],
                               "row_ids": [20, 21]}]
        assert any("same span after the remap" in w for w in out["warnings"])

    def test_the_execute_refuses_before_taking_a_backup(self, project):
        self._collide(project)
        out = preview_of()
        refused = execute_from(out)
        assert refused["reason"] == "unique_constraint_collision"
        assert refused["nothing_changed"] is True
        assert backups(project) == []
        assert query(project, "SELECT fulltext FROM source WHERE id=1"
                     )[0]["fulltext"] == TEXT

    def test_the_parity_policy_resolves_it_by_deleting(self, project):
        self._collide(project)
        out = preview_of(overlap_policy="qualcoder_edit_parity")
        assert not out["preview"]["totals"]["unique_constraint_collisions"]
        assert execute_from(out)["success"] is True

    def test_a_legal_set_of_moves_is_not_blocked_by_a_transient_collision(
            self, project):
        """SQLite checks UNIQUE per STATEMENT, not at commit, so a set of
        moves whose final state is legal can still fail part way through.
        Here row 30 moves onto exactly the span row 31 is vacating."""
        text = "Thomas aa bb cc Thomas dd"
        folder = build_project(tmp_path_for(project) / "swap.qda", text)
        # "Thomas" -> "Alex" is four characters for six, so everything
        # after the first replacement moves left by two.
        add_coding(folder, 30, 1, 9, 12, owner="Bob", seltext=text[9:12],
                   text=text)
        add_coding(folder, 31, 1, 7, 10, owner="Bob", seltext=text[7:10],
                   text=text)
        write_fixture_sidecar(str(folder))
        server.db.close()
        server.db = QualcoderDatabase(str(folder))
        server.current_project_path = str(folder)
        mapping = [{"original": "Thomas", "pseudonym": "Alex"}]
        out = preview_of(mapping=mapping)
        assert not out["preview"]["totals"]["unique_constraint_collisions"]
        result = execute_from(out, mapping=mapping)
        assert result.get("success") is True, result
        moved = {r["ctid"]: (r["pos0"], r["pos1"]) for r in
                 query(folder, "SELECT ctid,pos0,pos1 FROM code_text")}
        assert moved == {30: (7, 10), 31: (5, 8)}


# =============================================================================
# THE MANIFEST
# =============================================================================

class TestManifest:

    def test_the_manifest_lands_in_the_isolated_state_home(self, project):
        result = execute_from(preview_of())
        path = Path(result["manifest_path"])
        assert path.exists()
        assert path.parent.name == "pseudonymisation"
        assert str(pt.STATE_HOME) in str(path.parent)
        assert re.fullmatch(r"run_\d{8}T\d{6}Z_[0-9a-f]{8}\.json", path.name)

    def test_the_manifest_carries_no_original_and_no_old_text(self, project):
        result = execute_from(preview_of())
        body = Path(result["manifest_path"]).read_text(encoding="utf-8")
        for forbidden in ("Thomas", "Tom", "Mary Ann", "Mary"):
            assert forbidden not in body, forbidden
        assert "Alex" in body and "Sam" in body

    def test_the_manifest_records_the_spans_a_reversal_would_need(
            self, project):
        result = execute_from(preview_of())
        manifest = json.loads(
            Path(result["manifest_path"]).read_text(encoding="utf-8"))
        assert manifest["format"] == 1
        item = manifest["files"][0]
        text = query(project, "SELECT fulltext FROM source WHERE id=1"
                     )[0]["fulltext"]
        for span in item["replacements"]:
            start, end = span["new_span"]
            assert text[start:end] in ("Alex", "Sam")
        assert item["new_fingerprint"][0] == len(text)
        assert [row["id"] for row in item["codings"]] == [1, 2, 3, 4]

    def test_a_file_name_that_holds_a_name_is_withheld(self, project):
        con = sqlite3.connect(str(project / "data.qda"))
        con.execute("UPDATE source SET name='Thomas interview.txt' "
                    "WHERE id=1")
        con.commit()
        con.close()
        server.db.close()
        server.db = QualcoderDatabase(str(project))
        result = execute_from(preview_of())
        body = Path(result["manifest_path"]).read_text(encoding="utf-8")
        assert "Thomas" not in body
        assert json.loads(body)["files"][0]["name"] is None

    @POSIX_ONLY
    def test_the_manifest_is_owner_only(self, project):
        result = execute_from(preview_of())
        mode = stat.S_IMODE(Path(result["manifest_path"]).stat().st_mode)
        assert mode == 0o600

    def test_a_manifest_that_cannot_be_written_does_not_fail_the_run(
            self, project, monkeypatch):
        """It is written AFTER the commit. Refusing then would report a
        failure for a rewrite that has already succeeded."""
        monkeypatch.setattr(server, "_write_run_manifest",
                            lambda payload, name: None)
        result = execute_from(preview_of())
        assert result["success"] is True
        assert result["manifest_path"] is None
        assert "could not be written" in result["manifest_note"]

    def test_the_temp_file_is_cleaned_up_when_the_write_faults(
            self, project, monkeypatch):
        """The Windows rule the suite emulates: a leaked descriptor makes
        the cleanup unlink fail there, so the state folder fills with
        litter that is invisible on POSIX."""
        real = json.dump

        def exploding(payload, handle, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(server.json, "dump", exploding)
        result = execute_from(preview_of())
        assert result["success"] is True
        assert result["manifest_path"] is None
        monkeypatch.setattr(server.json, "dump", real)
        leftovers = list((pt.STATE_HOME / "pseudonymisation").glob("*.tmp"))
        assert leftovers == []

    def test_a_failing_fdopen_leaks_no_descriptor_and_leaves_no_litter(
            self, project, monkeypatch):
        """The Batch B round-5 lesson, on this batch's own atomic write.

        `tempfile.mkstemp` hands back a descriptor NOBODY owns until
        `os.fdopen` takes it. If that call raises, the descriptor is
        still open and the cleanup's unlink is the only thing that runs.
        POSIX allows unlinking an open file, so the leak is invisible
        here; Windows refuses it, so the temp survives and
        `~/.qualcoder_mcp` fills with litter. The suite emulates the
        Windows rule, so the leak fails HERE.
        """
        out = preview_of()          # the token secret exists before the fault

        def failing(fd, *args, **kwargs):
            raise OSError("too many open files")

        monkeypatch.setattr(server.os, "fdopen", failing)
        result = execute_from(out)
        monkeypatch.undo()
        assert result["success"] is True
        assert result["manifest_path"] is None
        directory = pt.STATE_HOME / "pseudonymisation"
        assert list(directory.glob("*")) == []
        assert H.open_paths_under(directory) == []

    def test_the_directory_comes_from_the_module_attribute(self):
        """Read the syntax, not a string: the suite isolates the state
        home by patching `preview_tokens.STATE_HOME`, and a second
        `Path.home()` of our own would write a real manifest into the
        researcher's folder during the test run.
        """
        source = Path(server.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        found = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and \
                    node.name == "_pseudonymisation_dir":
                found = node
        assert found is not None
        calls = {n.func.id for n in ast.walk(found)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "preview_tokens_state_home" in calls
        assert not any(
            isinstance(n, ast.Attribute) and n.attr == "home"
            for n in ast.walk(found))


# =============================================================================
# THE JOURNAL ENTRY
# =============================================================================

class TestJournal:

    def test_the_entry_is_written_by_default_and_names_no_original(
            self, project):
        result = execute_from(preview_of())
        rows = query(project, "SELECT name,jentry,owner FROM journal")
        assert len(rows) == 1
        assert rows[0]["name"] == result["journal_entry"]
        for forbidden in ("Thomas", "Tom", "Mary Ann", "Mary"):
            assert forbidden not in rows[0]["jentry"], forbidden
            assert forbidden not in rows[0]["name"], forbidden
        assert "Alex" in rows[0]["jentry"]
        assert rows[0]["owner"] == DEFAULT_AI_CODER_NAME

    def test_the_entry_can_be_turned_off(self, project):
        execute_from(preview_of(), record_in_journal=False)
        assert query(project, "SELECT jid FROM journal") == []

    def test_the_name_is_uniquified_against_an_existing_entry(self, project):
        """`journal` is unique on name, so the run must find a free one
        rather than let the insert raise: an IntegrityError inside this
        transaction would roll the whole rewrite back."""
        out = preview_of()
        taken = server._pseudonymise_journal_name(
            [{"file_id": 1, "name": "interview_01.txt"}],
            P.Compiled(P.validate_mapping(MAPPING)))
        con = sqlite3.connect(str(project / "data.qda"))
        con.execute("INSERT INTO journal (jid,name,jentry,date,owner) "
                    "VALUES (1,?,'earlier','d','TestCoder')", (taken,))
        con.commit()
        con.close()
        server.db.close()
        server.db = QualcoderDatabase(str(project))
        result = execute_from(out)
        assert result["success"] is True
        assert result["journal_entry"] == f"{taken}_2"
        assert len(query(project, "SELECT jid FROM journal")) == 2

    def test_the_name_is_a_name_qualcoder_itself_would_accept(self, project):
        execute_from(preview_of())
        name = query(project, "SELECT name FROM journal")[0]["name"]
        assert re.fullmatch(r"[ \w-]+", name, flags=re.ASCII), name

    def test_a_file_name_that_holds_a_name_is_withheld_from_the_entry(
            self, project):
        con = sqlite3.connect(str(project / "data.qda"))
        con.execute("UPDATE source SET name='Thomas interview.txt' "
                    "WHERE id=1")
        con.commit()
        con.close()
        server.db.close()
        server.db = QualcoderDatabase(str(project))
        result = execute_from(preview_of())
        row = query(project, "SELECT name,jentry FROM journal")[0]
        assert "Thomas" not in row["name"]
        assert "Thomas" not in row["jentry"]
        assert "name withheld" in row["jentry"]

    def test_an_unset_ai_coder_name_asks_and_offers_the_rebate(
            self, project):
        """The rewrite writes no owner anywhere, so the only row that
        needs a coder name is the journal entry. The refusal says so and
        names the argument that skips it."""
        (project / SIDECAR_NAME).unlink()
        server.db.close()
        server.db = QualcoderDatabase(str(project))
        out = preview_of()
        refused = execute_from(out)
        assert refused["action_required"]
        assert "record_in_journal=false" in refused["alternative"]
        assert backups(project) == []
        result = execute_from(out, record_in_journal=False)
        assert result["success"] is True
        assert result["journal_entry"] is None


# =============================================================================
# CONCURRENCY, FAULTS AND GATES
# =============================================================================

class TestConcurrencyAndFaults:

    def test_a_racing_write_is_caught_inside_the_transaction(
            self, project, monkeypatch):
        """BEGIN IMMEDIATE takes the RESERVED lock before the re-read, so
        another writer can only commit strictly before it. The re-read
        then sees the change and refuses with the backup already taken,
        and says so."""
        out = preview_of()
        original = QualcoderDatabase.begin_immediate
        fired = []

        def racing_begin(self):
            if not fired:
                fired.append(True)
                con = sqlite3.connect(str(project / "data.qda"))
                con.execute("UPDATE source SET fulltext = fulltext || ' X' "
                            "WHERE id = 1")
                con.commit()
                con.close()
            return original(self)

        monkeypatch.setattr(QualcoderDatabase, "begin_immediate",
                            racing_begin)
        refused = execute_from(out)
        assert fired, "the race never happened; the test proves nothing"
        assert refused["reason"] == "project_changed"
        assert "A backup had already been taken" in refused["error"]
        assert len(backups(project)) == 1
        assert server.db.read_only is True

    def test_the_reserved_lock_blocks_a_second_writer(self, project,
                                                      monkeypatch):
        out = preview_of()
        blocked = []
        original = QualcoderDatabase.pseudonymise_write

        def probing_write(self, plan):
            con = sqlite3.connect(str(project / "data.qda"), timeout=0.1)
            try:
                con.execute("UPDATE source SET memo='x' WHERE id=4")
                con.commit()
                blocked.append(False)
            except sqlite3.OperationalError:
                blocked.append(True)
            finally:
                con.close()
            return original(self, plan)

        monkeypatch.setattr(QualcoderDatabase, "pseudonymise_write",
                            probing_write)
        assert execute_from(out)["success"] is True
        assert blocked == [True]

    def test_a_fault_on_the_second_file_leaves_the_first_untouched(
            self, project, monkeypatch):
        con = sqlite3.connect(str(project / "data.qda"))
        con.execute("INSERT INTO source (id,name,fulltext,mediapath,memo,"
                    "owner,date) VALUES (5,'second.txt','Thomas twice: "
                    "Thomas.',NULL,'','TestCoder','d')")
        con.commit()
        con.close()
        server.db.close()
        server.db = QualcoderDatabase(str(project))
        out = preview_of()
        original = QualcoderDatabase._pseudonymise_move_rows
        seen = []

        def failing(self, item, new_text, fid):
            seen.append(fid)
            if len(seen) == 2:
                raise sqlite3.OperationalError("disk I/O error")
            return original(self, item, new_text, fid)

        monkeypatch.setattr(QualcoderDatabase, "_pseudonymise_move_rows",
                            failing)
        result = execute_from(out)
        assert "error" in result
        assert len(seen) == 2
        texts = {row["id"]: row["fulltext"] for row in
                 query(project, "SELECT id,fulltext FROM source")}
        assert texts[1] == TEXT
        assert texts[5] == "Thomas twice: Thomas."
        assert server.db.read_only is True

    def test_a_backup_failure_aborts_before_any_write(self, project,
                                                      monkeypatch):
        out = preview_of()

        def no_backup(self):
            raise OSError("no space left on device")

        monkeypatch.setattr(QualcoderDatabase, "backup_before_write",
                            no_backup)
        result = execute_from(out)
        assert "Failed to create a backup" in result["error"]
        assert "no text was rewritten" in result["message"]
        assert query(project, "SELECT fulltext FROM source WHERE id=1"
                     )[0]["fulltext"] == TEXT

    def test_the_lock_gate_refuses_while_qualcoder_holds_the_project(
            self, project):
        import time
        out = preview_of()
        # A real lock is two lines, the username and an epoch refreshed
        # every five seconds; anything older than thirty is stale and is
        # deliberately NOT a refusal (database.py qualcoder_lock_state).
        lock = project / "project_in_use.lock"
        lock.write_text("someone\n%f\n" % time.time(), encoding="utf-8")
        try:
            result = execute_from(out)
            assert "error" in result
            assert query(project, "SELECT fulltext FROM source WHERE id=1"
                         )[0]["fulltext"] == TEXT
        finally:
            lock.unlink()


class TestStaleSessions:

    def _session(self, project, file_id, status="pending"):
        session = AICodingSession(project_path=str(project),
                                  description="s", file_ids=[file_id],
                                  code_names=["Stress"], instruction="i")
        session.add_suggestion(CodingSuggestion(
            file_id=file_id, file_name="f", code_id=1, code_name="Stress",
            start_pos=0, end_pos=6, segment_text=TEXT[0:6],
            reasoning="r", confidence=0.9, status=status))
        server.session_manager.save_session(session)
        return session

    def test_a_pending_suggestion_on_a_rewritten_file_is_listed(
            self, project):
        session = self._session(project, 1)
        result = execute_from(preview_of())
        assert result["stale_sessions"] == [session.session_id]

    def test_a_suggestion_on_an_untouched_file_is_not_listed(self, project):
        self._session(project, 4)
        assert execute_from(preview_of())["stale_sessions"] == []

    def test_an_applied_suggestion_is_not_listed(self, project):
        self._session(project, 1, status="applied")
        assert execute_from(preview_of())["stale_sessions"] == []

    def test_applying_a_stale_suggestion_afterwards_fails_safe(self,
                                                               project):
        """Why listing them is enough, and deleting them is not this
        tool's decision (D1 5.6).

        A suggestion records the exact excerpt it refers to. After the
        run that excerpt still holds the real name and the rewritten text
        does not, so the exact-verbatim invariant cannot find it and the
        suggestion is rejected rather than written at the wrong offsets.
        The failure mode is a refusal, not a misplaced coding.
        """
        # APPROVED, not pending: `apply_codings` only ever writes
        # approved suggestions, so a pending one would make this test
        # pass on "nothing to apply" and prove nothing at all.
        session = self._session(project, 1, status="approved")
        assert execute_from(preview_of())["stale_sessions"] == \
            [session.session_id]
        before = query(project, "SELECT ctid FROM code_text")
        out = json.loads(server.apply_codings(session.session_id,
                                              create_backup=False))
        assert out["total_approved"] == 1
        assert len(out["failures"]) == 1
        assert "does not match the file text" in out["failures"][0]["reason"]
        assert out["failures"][0]["provided_snippet"] == "Thomas"
        assert "nothing was written" in out["error"]
        assert query(project, "SELECT ctid FROM code_text") == before

    def test_the_run_never_touches_a_session_file(self, project):
        session = self._session(project, 1)
        path = Path(server.session_manager.storage_dir) / \
            f"session_{session.session_id}.json"
        before = path.read_bytes()
        execute_from(preview_of())
        assert path.read_bytes() == before


# =============================================================================
# pseudonyms.json
# =============================================================================

class TestProjectPseudonyms:

    def test_the_sidecar_is_read_and_reported_with_its_encoding(
            self, project):
        (project / "pseudonyms.json").write_text(
            json.dumps([{"original": "Thomas", "pseudonym": "Alex"}]),
            encoding="utf-8")
        out = preview_of(mapping=None, use_project_pseudonyms=True)
        assert out["preview"]["pseudonyms_json_encoding"] == "utf-8"
        assert out["preview"]["totals"]["replacements"] == 3

    def test_a_windows_written_sidecar_is_refused_on_a_utf8_machine(
            self, project, monkeypatch):
        """The honest consequence of upstream writing with no encoding.

        `pseudonyms.py:92` opens the file for writing with no `encoding`
        argument, so what lands on disk is the platform default: cp1252
        on a typical Windows machine. On macOS and Linux the default is
        UTF-8, so those bytes decode as neither, and there is nothing
        this tool can do about it except say so clearly and name the
        likely cause. QualCoder itself cannot read that file either.

        The fixture writes the BYTES rather than a string, so it is the
        same file on every platform the suite runs on, and the machine's
        default encoding is injected rather than assumed.
        """
        payload = '[{"original": "André", "pseudonym": "Alex"}]'
        (project / "pseudonyms.json").write_bytes(payload.encode("cp1252"))
        from qualcoder_mcp.database import read_project_pseudonyms
        monkeypatch.setattr(
            "qualcoder_mcp.database.locale.getpreferredencoding",
            lambda do_setlocale=True: "UTF-8")
        with pytest.raises(ValueError) as excinfo:
            read_project_pseudonyms(project)
        assert "neither UTF-8 nor this machine" in str(excinfo.value)
        assert "Windows" in str(excinfo.value)
        assert "Andr" not in str(excinfo.value)

    def test_the_same_sidecar_is_read_where_that_encoding_is_the_default(
            self, project, monkeypatch):
        """The other half: on the machine that wrote it, it reads."""
        payload = '[{"original": "André", "pseudonym": "Alex"}]'
        (project / "pseudonyms.json").write_bytes(payload.encode("cp1252"))
        from qualcoder_mcp.database import read_project_pseudonyms
        monkeypatch.setattr(
            "qualcoder_mcp.database.locale.getpreferredencoding",
            lambda do_setlocale=True: "cp1252")
        entries, encoding = read_project_pseudonyms(project)
        assert entries == [{"original": "André",
                            "pseudonym": "Alex"}]
        assert encoding == "cp1252"

    def test_a_byte_order_mark_is_accepted_and_named(self, project):
        payload = json.dumps([{"original": "Thomas", "pseudonym": "Alex"}])
        (project / "pseudonyms.json").write_bytes(
            b"\xef\xbb\xbf" + payload.encode("utf-8"))
        from qualcoder_mcp.database import read_project_pseudonyms
        entries, encoding = read_project_pseudonyms(project)
        assert entries[0]["original"] == "Thomas"
        assert encoding == "utf-8-sig"

    def test_extra_keys_are_ignored_on_read_and_never_written_back(
            self, project):
        (project / "pseudonyms.json").write_text(
            json.dumps([{"original": "Thomas", "pseudonym": "Alex",
                         "note": "added by something else"}]),
            encoding="utf-8")
        before = (project / "pseudonyms.json").read_bytes()
        out = preview_of(mapping=None, use_project_pseudonyms=True)
        assert out["preview"]["totals"]["replacements"] == 3
        execute_from(out, mapping=None, use_project_pseudonyms=True)
        assert (project / "pseudonyms.json").read_bytes() == before

    def test_an_absent_sidecar_says_so(self, project):
        out = preview_of(mapping=None, use_project_pseudonyms=True)
        assert "was not found in the project folder" in out["error"]

    def test_malformed_json_is_refused_without_echoing_the_file(
            self, project):
        (project / "pseudonyms.json").write_text(
            '[{"original": "Thomas",', encoding="utf-8")
        out = preview_of(mapping=None, use_project_pseudonyms=True)
        assert "could not be parsed" in out["error"]
        assert "Thomas" not in out["error"]

    def test_a_list_of_strings_is_refused(self, project):
        (project / "pseudonyms.json").write_text(
            json.dumps(["Thomas", "Mary"]), encoding="utf-8")
        out = preview_of(mapping=None, use_project_pseudonyms=True)
        assert "not an object with two text values" in out["error"]

    def test_both_mapping_sources_at_once_is_refused(self, project):
        out = preview_of(use_project_pseudonyms=True)
        assert "give one mapping source" in out["error"]

    def test_the_sidecar_binds_the_same_as_the_identical_typed_mapping(
            self, project):
        """`use_project_pseudonyms` is not part of what the token binds,
        so a preview from the sidecar executes with the same mapping
        typed out, and the other way round."""
        entries = [{"original": "Thomas", "pseudonym": "Alex"}]
        (project / "pseudonyms.json").write_text(json.dumps(entries),
                                                 encoding="utf-8")
        out = preview_of(mapping=None, use_project_pseudonyms=True)
        result = call(mapping=entries,
                      preview_token=out["preview_token"])
        assert result.get("success") is True, result

    @POSIX_ONLY
    def test_a_sidecar_linked_out_of_the_project_is_not_followed(
            self, project, tmp_path):
        outside = tmp_path / "elsewhere.json"
        outside.write_text(json.dumps(
            [{"original": "Thomas", "pseudonym": "Alex"}]), encoding="utf-8")
        (project / "pseudonyms.json").symlink_to(outside)
        out = preview_of(mapping=None, use_project_pseudonyms=True)
        assert "outside the project" in out["error"]

    def test_the_sidecar_is_never_written(self, project):
        """Owner ruling Q6: the file is the reverse key in plain text at
        the project root and this release never creates or changes it."""
        execute_from(preview_of())
        assert not (project / "pseudonyms.json").exists()


class TestImportRider:

    def test_the_import_applies_the_project_sidecar_when_asked(self,
                                                               project):
        (project / "pseudonyms.json").write_text(
            json.dumps([{"original": "Thomas", "pseudonym": "Alex"}]),
            encoding="utf-8")
        out = json.loads(server.import_text_file(
            filename="new.txt", content="Thomas arrived. Thomas left.",
            create_backup=False, apply_project_pseudonyms=True))
        assert out["success"] is True
        assert query(project, "SELECT fulltext FROM source WHERE id=?",
                     (out["file_id"],))[0]["fulltext"] == \
            "Alex arrived. Alex left."
        assert out["project_pseudonyms"]["applied"] == 2
        assert out["project_pseudonyms"]["per_pseudonym"] == [
            {"pseudonym": "Alex", "count": 2}]

    def test_the_import_is_unchanged_by_default(self, project):
        (project / "pseudonyms.json").write_text(
            json.dumps([{"original": "Thomas", "pseudonym": "Alex"}]),
            encoding="utf-8")
        out = json.loads(server.import_text_file(
            filename="new.txt", content="Thomas arrived.",
            create_backup=False))
        assert "project_pseudonyms" not in out
        assert query(project, "SELECT fulltext FROM source WHERE id=?",
                     (out["file_id"],))[0]["fulltext"] == "Thomas arrived."

    def test_the_import_does_not_chain_the_way_upstream_does(self, project):
        """Upstream applies each entry as its own pass, so a later entry
        rewrites what an earlier one wrote. A mapping that would chain is
        refused here rather than applied in an order-dependent way."""
        (project / "pseudonyms.json").write_text(
            json.dumps([{"original": "Thomas", "pseudonym": "Alex"},
                        {"original": "Alex", "pseudonym": "Pat"}]),
            encoding="utf-8")
        out = json.loads(server.import_text_file(
            filename="new.txt", content="Thomas and Alex.",
            create_backup=False, apply_project_pseudonyms=True))
        assert "chain" in out["error"]
        assert query(project, "SELECT id FROM source WHERE name='new.txt'") \
            == []

    def test_line_endings_are_normalised_before_the_match(self, project):
        """Upstream normalises first and replaces second
        (manage_files.py:3336-3349), so the counts and the stored text
        agree."""
        (project / "pseudonyms.json").write_text(
            json.dumps([{"original": "Thomas", "pseudonym": "Alex"}]),
            encoding="utf-8")
        out = json.loads(server.import_text_file(
            filename="new.txt", content="﻿Thomas\r\nspoke\rThomas",
            create_backup=False, apply_project_pseudonyms=True))
        stored = query(project, "SELECT fulltext FROM source WHERE id=?",
                       (out["file_id"],))[0]["fulltext"]
        assert stored == "Alex\nspoke\nAlex"
        assert out["project_pseudonyms"]["applied"] == 2

    def test_an_absent_sidecar_refuses_the_import(self, project):
        out = json.loads(server.import_text_file(
            filename="new.txt", content="Thomas arrived.",
            create_backup=False, apply_project_pseudonyms=True))
        assert "was not found" in out["error"]


# =============================================================================
# RESULT SHAPE, NOTES AND POSITION SAFETY
# =============================================================================

class TestResultShape:

    def test_the_result_carries_the_four_notes(self, project):
        result = execute_from(preview_of())
        joined = " ".join(result["notes"])
        assert "Positions in these files have changed" in joined
        assert "backup" in joined
        assert "open QualCoder window" in joined
        assert "search index" in joined
        _house_rules(result["notes"])

    def test_the_result_reports_both_fingerprints(self, project):
        result = execute_from(preview_of())
        item = result["files"][0]
        assert item["old_length"] == len(TEXT)
        assert item["new_length"] == len(TEXT) - 10
        assert item["old_sha256"] != item["new_sha256"]

    def test_a_run_with_nothing_to_replace_answers_without_a_backup(
            self, project):
        mapping = [{"original": "Zebedee", "pseudonym": "Quux"}]
        out = preview_of(mapping=mapping)
        assert any("rewrite nothing" in w for w in out["warnings"])
        result = execute_from(out, mapping=mapping)
        assert result["success"] is True
        assert result["nothing_changed"] is True
        assert backups(project) == []

    def test_an_unsafe_file_is_disclosed_but_not_refused(self, tmp_path):
        text = "Thomas said \r\n and then left."
        folder = build_project(tmp_path / "unsafe.qda", text)
        write_fixture_sidecar(str(folder))
        original_db, original_path = server.db, server.current_project_path
        server.db = QualcoderDatabase(str(folder))
        server.current_project_path = str(folder)
        try:
            out = preview_of()
            assert out["preview"]["files"][0]["position_safe"] is False
            assert any("emoji bug" in w for w in out["warnings"])
            result = execute_from(out)
            assert result["success"] is True
            assert "position_safety_warning" in result
        finally:
            server.db.close()
            server.db, server.current_project_path = original_db, original_path

    def test_a_pseudonym_cannot_make_a_safe_file_unsafe(self, project):
        """An astral pseudonym is refused by the mapping rules, so a file
        that was position-safe is position-safe afterwards."""
        out = call(mapping=[{"original": "Thomas",
                             "pseudonym": "Al\U0001f600ex"}])
        assert "beyond U+FFFF" in out["error"]
        result = execute_from(preview_of())
        assert result["files"][0]["position_safe"] is True


class TestStructureAndWindowsSafety:
    """D1 6.4, and the two pins that can only be read rather than driven."""

    @staticmethod
    def _function(module_file, name):
        tree = ast.parse(Path(module_file).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and node.name == name:
                return node
        raise AssertionError(f"{name} is not in {module_file}")

    def test_the_c7_check_runs_before_the_first_write(self):
        """Read the syntax, because the behaviour is unreachable.

        `_state_guarded` recomputes the signed state before this function
        is called, and that state already digests every touched file as
        (id, length, sha256), so a changed fulltext is refused before the
        C7 check could ever fire. The check is defence in depth: it
        survives only if nothing moves it after the first write, and only
        a structural pin can say so. Its position is asserted here, and
        the report says plainly that no mutation of it can be made to go
        red through behaviour.
        """
        from qualcoder_mcp import database as db_module
        node = self._function(db_module.__file__, "pseudonymise_write")
        verify_line = None
        first_write_line = None
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            func = inner.func
            if isinstance(func, ast.Attribute):
                if func.attr == "verify_fulltext_unchanged":
                    verify_line = min(verify_line or inner.lineno,
                                      inner.lineno)
                if func.attr == "execute":
                    first_write_line = min(first_write_line or inner.lineno,
                                           inner.lineno)
        assert verify_line is not None, "the C7 check is gone"
        assert first_write_line is not None
        assert verify_line < first_write_line, (
            "the fulltext fingerprint check must precede every statement "
            "this function issues")

    @pytest.mark.parametrize("module,name", [
        ("server", "_write_run_manifest"),
        ("database", "read_project_pseudonyms"),
    ])
    def test_every_text_file_this_feature_opens_names_its_encoding(
            self, module, name):
        """An unnamed encoding is the platform's, which differs between
        the machine that writes and the machine that reads. Read as
        syntax: a sweep for the word "encoding" would be satisfied by a
        comment."""
        import importlib
        target = importlib.import_module(f"qualcoder_mcp.{module}")
        node = self._function(target.__file__, name)
        opening = {"open", "fdopen", "read_text", "write_text"}
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            func = inner.func
            called = (func.id if isinstance(func, ast.Name)
                      else func.attr if isinstance(func, ast.Attribute)
                      else None)
            if called not in opening:
                continue
            keywords = {kw.arg for kw in inner.keywords}
            assert "encoding" in keywords, (
                f"{name} calls {called} at line {inner.lineno} without "
                f"naming an encoding")

    def test_the_run_writes_no_file_outside_the_places_it_declares(
            self, project, tmp_path):
        """Everything this run creates is the project itself, a backup
        folder beside it, or the state home.

        The allowed list has to be the exact set, not a convenient
        ancestor of it: listing `project.parent` would have covered the
        whole temporary tree and made this test unable to fail, which is
        what a mutation writing a stray file into that folder showed.
        """
        before = {p for p in tmp_path.rglob("*") if p.is_file()}
        result = execute_from(preview_of())
        assert result["success"] is True
        after = {p for p in tmp_path.rglob("*") if p.is_file()}
        backup_folders = list(
            project.parent.glob(f"{project.stem}_backup_*"))
        assert backup_folders, "the run took no backup, so this proves little"
        allowed = [project, pt.STATE_HOME,
                   Path(server.session_manager.storage_dir)] + backup_folders
        strays = [path for path in sorted(after - before)
                  if not any(root == path or root in path.parents
                             for root in allowed)]
        assert strays == [], strays

    def test_nothing_this_run_logs_carries_a_name_or_a_slice(
            self, project, caplog):
        """The Security gate's own item: counts and ids in the log, never
        a surface form and never a piece of the text (D1 5.1)."""
        import logging
        caplog.set_level(logging.DEBUG)
        out = preview_of()
        execute_from(out)
        logged = "\n".join(record.getMessage() for record in caplog.records)
        for forbidden in ("Thomas", "Tom", "Mary Ann", "Mary",
                          "said he met", "agreed with"):
            assert forbidden not in logged, forbidden

    def test_the_engine_module_has_no_logger_at_all(self):
        """The simplest way to keep a name out of the log is to have
        nowhere to put one. Read as syntax, not as a grep for
        "logger"."""
        from qualcoder_mcp import pseudonymise as engine
        tree = ast.parse(Path(engine.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not any(alias.name == "logging"
                               for alias in node.names)
            if isinstance(node, ast.ImportFrom):
                assert node.module != "logging"

    def test_no_mapping_string_reaches_a_regex_unescaped(self):
        """The whole class, read as syntax rather than grepped for.

        Upstream's survey importer interpolates an original straight into
        a pattern (`import_survey.py:134-140` at both pins), so a name
        containing a full stop matches any character and one containing
        an unbalanced bracket raises. Every pattern this engine builds
        must therefore come from `re.escape`d parts, or from nothing but
        literals and this module's own top-level constants.

        A tolerated pattern must also have been BUILT here: a bare local
        variable is refused even when it happens to be safe today,
        because a sweep that accepts one accepts the next one too.
        """
        from qualcoder_mcp import pseudonymise as engine
        source = Path(engine.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        module_constants = {
            target.id
            for node in tree.body if isinstance(node, ast.Assign)
            for target in node.targets if isinstance(target, ast.Name)}
        # Builtins a pattern may legitimately call to build a character
        # class out of code points.
        allowed_builtins = {"chr", "str", "len", "ord"}
        risky = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute)
                    and func.attr in ("compile", "finditer", "fullmatch",
                                      "search", "sub", "match")
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "re"):
                continue
            pattern = node.args[0] if node.args else None
            if pattern is None:
                continue
            if any(isinstance(sub, ast.Call)
                   and isinstance(sub.func, ast.Attribute)
                   and sub.func.attr == "escape"
                   for sub in ast.walk(pattern)):
                continue                      # built from escaped parts
            names = {sub.id for sub in ast.walk(pattern)
                     if isinstance(sub, ast.Name)}
            # A comprehension's own loop variable is bound by the
            # expression itself, so it carries whatever the thing it
            # iterates carries and is not a free name.
            for sub in ast.walk(pattern):
                if isinstance(sub, (ast.GeneratorExp, ast.ListComp,
                                    ast.SetComp, ast.DictComp)):
                    for generator in sub.generators:
                        names -= {t.id for t in ast.walk(generator.target)
                                  if isinstance(t, ast.Name)}
            if names <= (module_constants | allowed_builtins):
                continue                      # literals and our constants
            if isinstance(pattern, ast.Attribute):
                continue                      # a compiled pattern's own text
            risky.append((node.lineno, sorted(names)))
        assert risky == [], risky


class TestArgumentValidation:

    @pytest.mark.parametrize("kwargs,fragment", [
        ({"case_mode": "loose"}, "case_mode must be one of"),
        ({"overlap_policy": "whatever"}, "overlap_policy must be one of"),
        ({"mapping": []}, "mapping is empty"),
        ({"mapping": [{"original": "T", "pseudonym": "Alex"}]},
         "at least 2 characters"),
        ({"file_ids": [0]}, "positive integers"),
        ({"file_ids": list(range(1, 500))}, "at most 200 file ids"),
    ], ids=lambda v: str(v)[:40])
    def test_the_refusals(self, project, kwargs, fragment):
        payload = {"mapping": MAPPING}
        payload.update(kwargs)
        out = call(**payload)
        assert fragment in out["error"]
        assert backups(project) == []

    def test_validation_happens_before_any_token_check(self, project):
        """A malformed call is reported as malformed, not as a bad token:
        the caller cannot fix a mapping error by previewing again."""
        out = call(mapping=[{"original": "T", "pseudonym": "Alex"}],
                   preview_token="qcp1.1.aaaaaaaa." + "a" * 32)
        assert "at least 2 characters" in out["error"]
        assert "token" not in out["error"].lower()
