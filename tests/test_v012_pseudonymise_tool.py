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
from contextlib import contextmanager
from itertools import product
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server
import track5_helpers as H
from track5_helpers import write_fixture_sidecar
from qualcoder_mcp import preview_tokens as pt
from qualcoder_mcp import pseudonymise as P
from qualcoder_mcp.database import QualcoderDatabase
from qualcoder_mcp.project_settings import (AI_CODER_NAME_ENV,
                                            DEFAULT_AI_CODER_NAME,
                                            KNOWN_AI_ASSISTANT_OWNER,
                                            NEWER_FORMAT_MESSAGE,
                                            SIDECAR_NAME,
                                            UNREADABLE_MESSAGE)
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

# The column and the four views, together, because QualCoder's own
# routine never makes one without the other. Upstream does not ALTER an
# existing table: `update_coder_names` creates `coder_names` with the
# `visibility` column already declared in its CREATE TABLE
# (app.py:1448-1586 at 9bddf17; the same routine at 3.8.2 is
# `__main__.py:1198-1330`) and the four views after it
# (app.py:1518-1561). The ALTER below is this suite's own way of
# reaching the same shape from a fixture whose table predates the
# column; what the server reads (`PRAGMA table_info`) answers the same
# for both.
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


def execute_as_recipe(out, **kwargs):
    """Execute with the recipe VERBATIM, mapping source included."""
    arguments = dict(out["execute_with"]["arguments"])
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

    def test_the_same_mapping_in_another_order_executes(self, project):
        """Fix round 3, S3. `canonical_mapping` binds the same token for
        the same entries in any order, and the signed effect used to key
        each replacement on the caller's index, so the reversed mapping
        was refused as "the project changed" on a project that had not.
        """
        out = preview_of()
        reversed_mapping = list(reversed(MAPPING))
        assert reversed_mapping != MAPPING
        # The readable preview keeps the CALLER's indices, which are
        # what the model can relay; only the signed effect is canonical.
        again = preview_of(mapping=reversed_mapping)
        assert [b["entry"] for b in again["preview"]["files"][0]["replacements"]
                ] == [0, 1]
        assert again["preview"]["files"][0]["replacements"][0]["pseudonym"] \
            == "Sam"
        assert again["preview_token"].split(".")[2] == \
            out["preview_token"].split(".")[2]          # one bind
        result = execute_from(out, mapping=reversed_mapping)
        assert result.get("success") is True, result
        assert query(project, "SELECT fulltext FROM source WHERE id=1"
                     )[0]["fulltext"].startswith("Alex said")

    # A mapping in which one entry's form sits inside another's, so the
    # rewrite records an overlap conflict BETWEEN two entries. The
    # fixture MAPPING has no such pair, which is why the pin above could
    # not see the conflicts half of the S3 fix (fix round 4, B1).
    CONFLICTING = [{"original": "Thomas", "pseudonym": "Alex"},
                   {"original": "Mary Ann", "pseudonym": "Sam"},
                   {"original": "Ann", "pseudonym": "Beth"}]
    THOMAS, MARY_ANN, ANN = CONFLICTING

    # The order pairs the pin compares. Canonical order is Ann 0, Mary
    # Ann 1, Thomas 2, and the winning entry "Mary Ann" sits at caller
    # index 1 in CONFLICTING and in its reverse alike, so that pair on
    # its own saw only the `entry` half of the fix: the raw
    # `loses_to_entry` equalled its canonical position by coincidence,
    # and that one line reverted with the whole suite green (fix round
    # 5, B1). The other two pairs move "Mary Ann".
    ORDER_PAIRS = [
        ("Mary Ann stays at 1", CONFLICTING, [ANN, MARY_ANN, THOMAS]),
        ("Mary Ann moves 0 to 2", [MARY_ANN, THOMAS, ANN],
         [THOMAS, ANN, MARY_ANN]),
        ("Mary Ann moves 1 to 0", [THOMAS, MARY_ANN, ANN],
         [MARY_ANN, ANN, THOMAS]),
    ]

    @pytest.mark.parametrize("label,previewed,executed", ORDER_PAIRS,
                             ids=[label for label, *_ in ORDER_PAIRS])
    def test_a_mapping_with_an_overlap_conflict_executes_in_another_order(
            self, project, label, previewed, executed):
        """Fix round 4, B1, and fix round 5, B1. The signed effect keys
        the replacements AND the overlap conflicts on the canonical
        position. The two lines that canonicalise a conflict's `entry`
        and `loses_to_entry` reverted with the whole suite green,
        because no pin's mapping produced a conflict; under that revert
        this mapping, reordered, was refused as "the project changed"
        on a project nothing had touched. With "Ann" inside "Mary Ann"
        the conflict is recorded, the readable preview shows it under
        the caller's indices in either order, the signed effect names
        the canonical pair (Ann 0 loses to Mary Ann 1) and is identical
        in both, and the second order executes from the first's token.
        """
        def index_of(order, original):
            return [entry["original"] for entry in order].index(original)

        out = preview_of(mapping=previewed)
        assert out["preview"]["files"][0]["overlap_conflicts"] == [
            {"entry": index_of(previewed, "Ann"), "form": "Ann",
             "span": [39, 42],
             "loses_to_entry": index_of(previewed, "Mary Ann"),
             "chosen_span": [34, 42]}]
        again = preview_of(mapping=executed)
        assert again["preview"]["files"][0]["overlap_conflicts"] == [
            {"entry": index_of(executed, "Ann"), "form": "Ann",
             "span": [39, 42],
             "loses_to_entry": index_of(executed, "Mary Ann"),
             "chosen_span": [34, 42]}]
        assert again["preview_token"].split(".")[2] == \
            out["preview_token"].split(".")[2]          # one bind
        effects = []
        for mapping in (previewed, executed):
            compiled = P.Compiled(P.validate_mapping(mapping))
            plan = server.db.pseudonymise_plan(compiled, "snap_to_pseudonym",
                                               None)
            effects.append(server.db.pseudonymise_effect(plan))
        for effect in effects:
            signed = effect["files"][0]["overlap_conflicts"]
            assert [(c["entry"], c["loses_to_entry"]) for c in signed] == \
                [(0, 1)], ("the conflict must reach the signed effect "
                           "under the canonical positions")
        assert effects[0] == effects[1]
        result = execute_from(out, mapping=executed)
        assert result.get("success") is True, result
        assert query(project, "SELECT fulltext FROM source WHERE id=1"
                     )[0]["fulltext"] == (
            "Alex said he met Tom yesterday. Sam agreed with Alex. "
            "Later Alex left.")

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
# THE HIDDEN-CODER RULE (X1, REFINED BY RULING 7.3(3))
# =============================================================================

def _hidden(project, *rows, name="Hidden Coder"):
    """Add `rows` under one coder, hide that coder, and reconnect.

    Each row is ("coding", ctid, pos0, pos1) or ("annotation", anid,
    pos0, pos1). The reconnect is what selecting the project does, so
    the declaration is read as QualCoder left it.
    """
    for kind, row_id, pos0, pos1 in rows:
        if kind == "coding":
            add_coding(project, row_id, 1, pos0, pos1, owner=name)
        else:
            add_annotation(project, row_id, pos0, pos1, owner=name)
    hide_coder(project, name)
    server.db.close()
    server.db = QualcoderDatabase(str(project))


def _with_pseudonym(pseudonym):
    """MAPPING with Thomas's pseudonym swapped and the variants kept."""
    return [{**MAPPING[0], "pseudonym": pseudonym}, MAPPING[1]]


class TestHiddenCoders:
    """Ruling X1 (QC40_PLATFORM 7.2), refined by ruling 7.3(3) of
    2026-09-15 and by ruling 7.4 of 2026-09-16: a hidden coder's row
    that changes no coding decision needs no override, and a row that
    changes one does.

    Exempt: a pure position shift (the row moved because text before it
    changed length); a pure substitution (the row covered the whole name
    and afterwards covers the whole pseudonym, whatever the two
    lengths); and a resize (the row CONTAINED a whole name and changed
    length only because that name did). Not exempt: a snap (the row
    partially overlapped a name and its boundary had to move), a
    deletion (the edit-parity policy alone), and a clamp (a damaged row
    whose stored end lay past the end of the text and was pulled back
    to it). Before ruling 7.3(3) the substitution was classified by
    length, so `Thomas -> Alex` needed the override and
    `Thomas -> Alexis` did not, for one and the same coding decision;
    before ruling 7.4 the resize was gated as well, though it records
    the same decision about the same words.

    Every span here is read against TEXT: "Thomas" is (0, 6), "Thomas
    said" (0, 11), "Thomas sa" (0, 9), "homas sa" (1, 9), "Later" (63,
    68).
    """

    def test_a_pure_shift_of_a_hidden_row_needs_no_override(self, project):
        """Ruling X1's exemption: the row moves because the text moved,
        and nothing about what the coder marked has changed."""
        _hidden(project, ("coding", 10, 63, 68))
        out = preview_of()
        hidden = out["preview"]["hidden_coder_rows"]
        assert hidden["shifted"] == 1
        assert hidden["override_required"] is False
        assert "allow_hidden_coder" not in out["execute_with"]["arguments"]
        result = execute_from(out)
        assert result["success"] is True
        assert result["hidden_coder_rows_updated"] == 1

    @pytest.mark.parametrize("pseudonym", ["Alex", "Alexander", "Alexis"],
                             ids=["shorter", "longer", "equal"])
    def test_a_substitution_of_a_hidden_row_needs_no_override_whatever_the_lengths(
            self, project, pseudonym):
        """Ruling 7.3(3). The row covered "Thomas" and afterwards covers
        the pseudonym: the coder marked the name and still does. The
        preview asks for nothing, the recipe carries no override, the
        execute without one succeeds, and the row reads the pseudonym.
        """
        _hidden(project, ("coding", 10, 0, 6))
        mapping = _with_pseudonym(pseudonym)
        out = preview_of(mapping=mapping)
        hidden = out["preview"]["hidden_coder_rows"]
        # The gate first, so a revert to the length rule fails the
        # shorter and the longer case HERE and the equal case only on
        # the count below (the equal case was exempt before as well).
        assert hidden["override_required"] is False
        assert (hidden["resized"], hidden["snapped"], hidden["deleted"]) \
            == (0, 0, 0)
        assert hidden["substituted"] == 1
        assert "allow_hidden_coder" not in out["execute_with"]["arguments"]
        result = execute_from(out, mapping=mapping)
        assert result["success"] is True
        assert result["hidden_coder_rows_updated"] == 1
        assert query(project, "SELECT pos0, pos1, seltext FROM code_text "
                              "WHERE ctid=10") == [
            {"pos0": 0, "pos1": len(pseudonym), "seltext": pseudonym}]
        assert query(project, "SELECT fulltext FROM source WHERE id=1"
                     )[0]["fulltext"].startswith(pseudonym + " said")

    def test_the_equal_length_substitution_is_the_control(self, project):
        """"Thomas -> Alexis" needed no override before ruling 7.3(3)
        either (by length it read as a pure shift), so this test passes
        on the tree before the change and on the tree after it. It is
        the control for the parametrised pin above: what the ruling
        changed is the shorter and the longer case, which it brings to
        the same outcome as this one."""
        _hidden(project, ("coding", 10, 0, 6))
        mapping = _with_pseudonym("Alexis")
        out = preview_of(mapping=mapping)
        hidden = out["preview"]["hidden_coder_rows"]
        assert hidden["override_required"] is False
        assert (hidden["resized"], hidden["snapped"], hidden["deleted"]) \
            == (0, 0, 0)
        assert "allow_hidden_coder" not in out["execute_with"]["arguments"]
        result = execute_from(out, mapping=mapping)
        assert result["success"] is True
        assert query(project, "SELECT pos0, pos1, seltext FROM code_text "
                              "WHERE ctid=10") == [
            {"pos0": 0, "pos1": 6, "seltext": "Alexis"}]

    def test_a_snap_of_a_hidden_row_requires_the_override(self, project):
        """"homas sa" partially overlapped "Thomas" and has to grow to
        swallow the whole pseudonym: what the coder marked changes, so
        the override is required (7.3(3), point (a)). The refusal is
        the existing count-free, name-free text."""
        _hidden(project, ("coding", 10, 1, 9))
        out = preview_of()
        hidden = out["preview"]["hidden_coder_rows"]
        assert hidden["snapped"] == 1
        assert (hidden["resized"], hidden["deleted"]) == (0, 0)
        assert hidden["override_required"] is True
        assert out["execute_with"]["arguments"]["allow_hidden_coder"] is True
        refused = call(mapping=MAPPING, preview_token=out["preview_token"])
        assert refused["reason"] == "hidden_coder_override_required"
        assert refused["nothing_changed"] is True
        assert "Hidden Coder" not in json.dumps(refused)
        assert not re.search(r"\d", refused["error"])
        assert backups(project) == []
        assert query(project, "SELECT pos0, pos1 FROM code_text WHERE ctid=10"
                     ) == [{"pos0": 1, "pos1": 9}]
        result = execute_from(out, allow_hidden_coder=True)
        assert result["success"] is True
        assert query(project, "SELECT pos0, pos1, seltext FROM code_text "
                              "WHERE ctid=10") == [
            {"pos0": 0, "pos1": 7, "seltext": "Alex sa"}]

    @pytest.mark.parametrize("pseudonym,reads", [("Alex", "Mr Alex said"),
                                                ("Alexander",
                                                 "Mr Alexander said")],
                             ids=["shorter", "longer"])
    def test_a_resize_of_a_hidden_row_needs_no_override(
            self, project, tmp_path, pseudonym, reads):
        """Ruling 7.4, decision 7. "Mr Thomas said" CONTAINED the whole
        name and changes length only because the name did: the coder
        marked those words and still marks them, exactly as a pure
        substitution does, so the row is exempt whether the pseudonym is
        shorter or longer. The preview asks for nothing, the recipe
        carries no override, and the execute without one succeeds."""
        text = "Mr Thomas said hello. Thomas left."
        folder = build_project(tmp_path / f"mr_{pseudonym}.qda", text=text)
        add_coding(folder, 10, 1, 0, 14, owner="Hidden Coder", text=text)
        assert text[0:14] == "Mr Thomas said"
        hide_coder(folder, "Hidden Coder")
        write_fixture_sidecar(str(folder))
        mapping = _with_pseudonym(pseudonym)
        with wired(folder):
            out = preview_of(mapping=mapping)
            hidden = out["preview"]["hidden_coder_rows"]
            # The gate first, so a revert that puts `resized` back into
            # it fails HERE rather than on a count.
            assert hidden["override_required"] is False
            assert (hidden["snapped"], hidden["deleted"],
                    hidden["clamped"]) == (0, 0, 0)
            assert hidden["resized"] == 1
            assert "allow_hidden_coder" not in out["execute_with"]["arguments"]
            result = execute_from(out, mapping=mapping)
            assert result["success"] is True
            assert result["hidden_coder_rows_updated"] == 1
            assert query(folder, "SELECT pos0, pos1, seltext FROM code_text "
                                 "WHERE ctid=10") == [
                {"pos0": 0, "pos1": len(reads), "seltext": reads}]

    def test_a_resize_that_swallowed_two_names_needs_no_override(
            self, project, tmp_path):
        """The same rule where the row contains TWO replaced names whose
        pseudonyms differ in length, so the row's length changes by the
        sum of two deltas: still one coding decision about the same
        words, still `resized`, still exempt (ruling 7.4)."""
        text = "Mr Thomas met Mary Ann there. Thomas left."
        folder = build_project(tmp_path / "two.qda", text=text)
        add_coding(folder, 10, 1, 0, 28, owner="Hidden Coder", text=text)
        assert text[0:28] == "Mr Thomas met Mary Ann there"
        hide_coder(folder, "Hidden Coder")
        write_fixture_sidecar(str(folder))
        with wired(folder):
            out = preview_of()
            hidden = out["preview"]["hidden_coder_rows"]
            assert hidden["override_required"] is False
            assert hidden["resized"] == 1
            assert (hidden["snapped"], hidden["deleted"],
                    hidden["clamped"]) == (0, 0, 0)
            assert "allow_hidden_coder" not in out["execute_with"]["arguments"]
            result = execute_from(out)
            assert result["success"] is True
            assert query(folder, "SELECT pos0, pos1, seltext FROM code_text "
                                 "WHERE ctid=10") == [
                {"pos0": 0, "pos1": 21, "seltext": "Mr Alex met Sam there"}]

    @pytest.mark.parametrize("span,reads", [((0, 11), "Thomas said"),
                                            ((0, 9), "Thomas sa")],
                             ids=["Thomas said", "Thomas sa"])
    def test_a_row_that_begins_on_the_name_but_runs_past_it_is_a_resize(
            self, project, span, reads):
        """One boundary on the name's own is not both. "Thomas sa" holds
        the whole name and two more characters, so it is a resize and
        not a substitution, and not a snap either: no boundary lay
        inside the name. Exempt since ruling 7.4."""
        assert TEXT[span[0]:span[1]] == reads
        _hidden(project, ("coding", 10, span[0], span[1]))
        hidden = preview_of()["preview"]["hidden_coder_rows"]
        assert hidden["override_required"] is False
        assert hidden["resized"] == 1
        assert (hidden["snapped"], hidden["deleted"],
                hidden["clamped"]) == (0, 0, 0)

    def test_under_edit_parity_a_hidden_row_exactly_on_the_name_is_deleted(
            self, project):
        """Unchanged behaviour, pinned as the control for 7.3(3)'s point
        (b): only the edit-parity policy deletes, and a deletion is on
        the override side whatever the lengths."""
        _hidden(project, ("coding", 10, 0, 6))
        out = preview_of(overlap_policy="qualcoder_edit_parity")
        hidden = out["preview"]["hidden_coder_rows"]
        assert hidden["deleted"] == 1
        assert hidden["substituted"] == 0
        assert hidden["override_required"] is True
        assert out["execute_with"]["arguments"]["allow_hidden_coder"] is True
        refused = call(mapping=MAPPING, overlap_policy="qualcoder_edit_parity",
                       preview_token=out["preview_token"])
        assert refused["reason"] == "hidden_coder_override_required"
        assert query(project, "SELECT ctid FROM code_text WHERE ctid=10"
                     ) == [{"ctid": 10}]
        result = execute_from(out, allow_hidden_coder=True)
        assert result["success"] is True
        assert query(project, "SELECT ctid FROM code_text WHERE ctid=10") == []

    def test_the_counts_are_reported_per_file_and_in_the_totals(
            self, project):
        """Counts only, never names, as always: all six classes sit
        together in the file's block and in the totals, exempt beside
        gated, and the execute's own count sums every one of them.
        Annotation 9 is stored (76, 300) on an 81-character text, the
        damaged row ruling 7.4 keeps gated."""
        _hidden(project, ("coding", 10, 0, 6), ("coding", 11, 63, 68),
                ("coding", 12, 0, 11), ("annotation", 9, 76, 300))
        out = preview_of()
        expected = {"shifted": 1, "substituted": 1, "resized": 1,
                    "snapped": 0, "deleted": 0, "clamped": 1,
                    "override_required": True}
        assert out["preview"]["hidden_coder_rows"] == expected
        assert out["preview"]["files"][0]["hidden_coder_rows"] == expected
        assert "Hidden Coder" not in json.dumps(out)
        result = execute_from(out, allow_hidden_coder=True)
        assert result["hidden_coder_rows_updated"] == 4

    def test_a_hidden_coder_is_never_named_anywhere(self, project):
        """A snap (the override's reason) beside a substitution and a
        resize (both exempt since ruling 7.4): the preview, the refusal
        and the result name the coder nowhere."""
        _hidden(project, ("coding", 10, 1, 9), ("coding", 11, 0, 6),
                ("coding", 12, 0, 11))
        out = preview_of()
        assert out["preview"]["hidden_coder_rows"]["override_required"] is True
        assert "Hidden Coder" not in json.dumps(out)
        refused = call(mapping=MAPPING, preview_token=out["preview_token"])
        assert refused["reason"] == "hidden_coder_override_required"
        assert "Hidden Coder" not in json.dumps(refused)
        result = execute_from(out, allow_hidden_coder=True)
        assert result["success"] is True
        assert "Hidden Coder" not in json.dumps(result)

    def test_two_hidden_rows_that_collide_do_not_name_the_coder(
            self, project):
        """Fix round 3, B1. The one-row pin above cannot reach this
        shape. Two codings by one hidden coder on one code, one marking
        "Thomas" (a substitution) and one marking "Thom" (a snap), both
        land on the pseudonym and on one unique key, and the key the
        preview reported was built with the owner column:
        `[1, 1, 0, 4, "Hidden Coder"]`, in the one field of a preview
        whose `by_owner` and `hidden_coder_rows` both withheld the name.
        The same for two annotations. Ordinary data, and an owner-ruled
        invariant (X1).
        """
        _hidden(project, ("coding", 10, 0, 6), ("coding", 11, 0, 4),
                ("annotation", 8, 0, 6), ("annotation", 9, 0, 4))
        out = preview_of()
        collisions = out["preview"]["files"][0]["unique_constraint_collisions"]
        assert collisions["code_text"] == [{"key": [1, 1, 0, 4],
                                            "row_ids": [10, 11]}]
        assert collisions["annotation"] == [{"key": [1, 0, 4],
                                             "row_ids": [8, 9]}]
        assert out["preview"]["hidden_coder_rows"]["override_required"] is True
        owners = {e["owner"] for e in
                  out["preview"]["files"][0]["codings"]["by_owner"]}
        assert "TestCoder" in owners and "Hidden Coder" not in owners
        assert "Hidden Coder" not in json.dumps(out)
        refused = execute_from(out, allow_hidden_coder=True)
        assert refused["reason"] == "unique_constraint_collision"
        assert "Hidden Coder" not in json.dumps(refused)

    def test_a_hidden_annotation_counts_too(self, project):
        """QualCoder creates an `annotation_visible` view, so an
        annotation can belong to a hidden coder; a count nested under
        `codings` would be the wrong place for it."""
        _hidden(project, ("annotation", 9, 0, 11))
        hidden = preview_of()["preview"]["hidden_coder_rows"]
        assert hidden["resized"] == 1
        assert hidden["override_required"] is False

    def test_a_hidden_annotation_exactly_on_a_name_is_a_substitution(
            self, project):
        _hidden(project, ("annotation", 9, 0, 6))
        out = preview_of()
        hidden = out["preview"]["hidden_coder_rows"]
        assert hidden["substituted"] == 1
        assert (hidden["resized"], hidden["snapped"], hidden["deleted"]) \
            == (0, 0, 0)
        assert hidden["override_required"] is False
        result = execute_from(out)
        assert result["success"] is True
        assert query(project, "SELECT pos0, pos1 FROM annotation WHERE anid=9"
                     ) == [{"pos0": 0, "pos1": 4}]

    def test_the_warning_and_the_refusal_say_which_rows_are_exempt(
            self, project):
        """Rulings 7.3(3) and 7.4 in the two sentences a model reads at
        the gate: the preview's warning and the execute's refusal both
        say that a substitution and a resize are exempt like a shift, and
        both keep the house rules. Driven by a snap, so both sentences
        are really emitted."""
        _hidden(project, ("coding", 10, 1, 9))
        out = preview_of()
        warnings = [w for w in out["warnings"] if "allow_hidden_coder" in w]
        assert len(warnings) == 1
        assert ("A pure position shift of a hidden coder's row does not, and "
                "neither does a row that covered a name, or contained one, "
                "and now covers or contains its pseudonym, whatever the two "
                "lengths: neither changes a coding decision. A row that grew "
                "to swallow a pseudonym, one that would be deleted, or one "
                "that had to be clamped because its stored end lay past the "
                "end of the text, does.") in warnings[0]
        assert "because it changes no coding decision." not in warnings[0]
        assert "one that contained a name and changed length with it" \
            not in warnings[0]
        refused = call(mapping=MAPPING, preview_token=out["preview_token"])
        assert refused["reason"] == "hidden_coder_override_required"
        assert ("A pure position shift of such a span is exempt and needs "
                "nothing, and so is a span that covered a name, or contained "
                "one, and now covers or contains its pseudonym, whatever the "
                "two lengths.") in refused["error"]
        assert "Hidden Coder" not in json.dumps(out) + json.dumps(refused)
        _house_rules([warnings[0], refused["error"]], ["warning", "refusal"])

    def test_the_gate_is_exactly_snapped_deleted_and_clamped(self):
        """The identity ruling 7.4 leaves behind, over all 64
        combinations of the six classes: `override_required` is true
        exactly when a snap, a deletion or a clamp is among them, and
        never because a shift, a substitution or a resize is."""
        classes = ("shifted", "substituted", "resized", "snapped",
                   "deleted", "clamped")
        seen = set()
        for present in product((0, 1), repeat=6):
            rows = [{"hidden": True,
                     "map": P.RemappedSpan(0, 1, name, name == "clamped",
                                           True)}
                    for name, flag in zip(classes, present) if flag]
            item = {"rows": {"code_text": rows, "annotation": [],
                             "case_text": []}}
            counts = QualcoderDatabase.pseudonymise_hidden_rows(item)
            assert sorted(counts) == sorted(classes + ("override_required",))
            for name, flag in zip(classes, present):
                assert counts[name] == flag, (present, name)
            assert counts["override_required"] is bool(
                counts["snapped"] or counts["deleted"]
                or counts["clamped"]), present
            seen.add((present, counts["override_required"]))
        assert len(seen) == 64
        assert sum(1 for _, gate in seen if gate) == 56

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
        warning = [w for w in out["warnings"] if "would DELETE" in w]
        assert len(warning) == 1
        # Fix round 3, S5: the narrowed claim (D-10), not "exactly what
        # QualCoder's own editor does": the editor diffs first, and its
        # diff library keeps a coding this policy deletes whenever the
        # pseudonym shares a prefix or suffix with the name.
        assert ("which is what QualCoder's coding-view walk does when fed "
                "this tool's exact edit list; the editor's own diff may "
                "factor a shared prefix or suffix out of a replacement "
                "(Tom to Tim) and keep a coding this policy deletes."
                in warning[0])
        assert "QualCoder's own editor does" not in warning[0]
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
        # (cid, fid, pos0, pos1): the owner column is grouped on and not
        # reported, because it can name a hidden coder (fix round 3, B1).
        assert collisions == [{"key": [1, 1, 0, 4], "row_ids": [20, 21]}]
        assert "Bob" not in json.dumps(collisions)
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

    def test_the_name_is_uniquified_against_an_existing_entry(
            self, project, monkeypatch):
        """`journal` is unique on name, so the run must find a free one
        rather than let the insert raise: an IntegrityError inside this
        transaction would roll the whole rewrite back.

        The clock is frozen (fix round 3, S9): the expected name and the
        name the run writes both carry a second-resolution stamp, and
        under full-suite load the second ticked between the two reads,
        so the fresh name was already unique and no suffix was needed.
        """
        from datetime import datetime as real_datetime

        frozen = real_datetime(2026, 9, 15, 7, 50, 52)

        class Frozen(real_datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen if tz is None else frozen.replace(tzinfo=tz)

        monkeypatch.setattr(server, "datetime", Frozen)
        out = preview_of()
        taken = server._pseudonymise_journal_name(
            [{"file_id": 1, "name": "interview_01.txt"}],
            P.Compiled(P.validate_mapping(MAPPING)))
        assert taken.endswith("2026-09-15 075052")
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

    def _unset(self, project):
        (project / SIDECAR_NAME).unlink()
        server.db.close()
        server.db = QualcoderDatabase(str(project))

    def test_an_unset_ai_coder_name_is_asked_for_in_the_preview(
            self, project):
        """Fix round 3, S4. The ask used to land at execute time, after
        the researcher had approved. The preview now carries it: a
        warning the model relays, and the ask itself in `execute_with`,
        with the quick picks, beside the recipe it belongs to."""
        self._unset(project)
        out = preview_of()
        assert out["requires_confirmation"] is True
        assert out["preview_token"]
        ask = out["execute_with"]["before_executing"]
        assert ask["action_required"] == "set_project_ai_coder_name"
        assert "No AI coder name is set for this project yet" in ask["message"]
        assert "error" not in ask
        assert ask["quick_picks"]
        assert "record_in_journal=false" in ask["alternative"]
        warning = [w for w in out["warnings"]
                   if "cannot be written as things stand" in w]
        assert len(warning) == 1
        assert ask["message"] in warning[0]
        assert "execute_with.before_executing" in warning[0]
        _house_rules(warning + [ask["message"]], ["warning", "ask"])
        # With record_in_journal=false the run needs no owner, and the
        # preview says nothing about one.
        quiet = preview_of(record_in_journal=False)
        assert "before_executing" not in quiet["execute_with"]
        assert not any("cannot be written as things stand" in w
                       for w in quiet.get("warnings", []))

    @pytest.mark.parametrize("shape", ["unset", "unreadable", "newer_format",
                                       "mismatch"])
    def test_the_warning_carries_the_resolvers_own_account(
            self, project, shape, monkeypatch):
        """Fix round 4, R4. `_resolve_write_owner` returns four distinct
        refusals, and the preview's warning said "no AI coder name is
        set" on every one of them: false where the sidecar cannot be
        read or is of a newer format (a name may well be set) and false
        where this host's declaration conflicts with a name that IS set.
        The warning carries the resolver's own text, and points at the
        quick picks only where the ask has any."""
        if shape == "unset":
            self._unset(project)
        elif shape == "unreadable":
            (project / SIDECAR_NAME).write_text("{not json", encoding="utf-8")
        elif shape == "newer_format":
            path = project / SIDECAR_NAME
            data = json.loads(path.read_text(encoding="utf-8"))
            data["format_version"] += 1
            path.write_text(json.dumps(data), encoding="utf-8")
        else:
            monkeypatch.setenv(AI_CODER_NAME_ENV, "Other Model 9")
        out = preview_of()
        assert out["preview_token"]
        ask = out["execute_with"]["before_executing"]
        expected = {
            "unset": "No AI coder name is set for this project yet",
            "unreadable": UNREADABLE_MESSAGE,
            "newer_format": NEWER_FORMAT_MESSAGE,
            "mismatch": ("This host declares the AI coder name "
                         "\"Other Model 9\""),
        }[shape]
        assert expected in ask["message"], ask["message"]
        warning = [w for w in out["warnings"]
                   if "cannot be written as things stand" in w]
        assert len(warning) == 1, out["warnings"]
        assert ask["message"] in warning[0]
        if shape in ("unset", "mismatch"):
            assert ask["action_required"] == "set_project_ai_coder_name"
            assert ask["quick_picks"]
            assert "and the quick picks" in warning[0]
        else:
            assert "action_required" not in ask
            assert "quick_picks" not in ask
            assert "quick picks" not in warning[0]
        if shape != "unset":
            assert "no ai coder name is set" not in warning[0].lower()
        if shape == "mismatch":
            assert f"current AI coder name is \"{DEFAULT_AI_CODER_NAME}\"" \
                in warning[0]
        _house_rules(warning + [ask["message"]], ["warning", "ask"])

    def test_the_ask_comes_after_the_token_and_before_the_write(
            self, project):
        """D1 3.8's order: a malformed token is answered as malformed,
        not with a naming question; a valid one is answered with the
        ask, nothing is written, and the same token executes once the
        name is set, because the setting is not in the signed state."""
        self._unset(project)
        out = preview_of()
        malformed = call(mapping=MAPPING, preview_token="qcp1.1.2.3")
        assert malformed["reason"] == "token_malformed"
        assert "action_required" not in malformed
        refused = execute_from(out)
        assert refused["action_required"] == "set_project_ai_coder_name"
        assert "record_in_journal=false" in refused["alternative"]
        assert backups(project) == []
        assert query(project, "SELECT fulltext FROM source WHERE id=1"
                     )[0]["fulltext"] == TEXT
        named = json.loads(server.set_project_ai_coder_name("Model X"))
        assert named["ai_coder_name"]["name"] == "Model X"
        result = execute_from(out)
        assert result.get("success") is True, result
        assert query(project, "SELECT owner FROM journal")[0]["owner"] == \
            "Model X"

    def test_the_rebate_still_stands(self, project):
        """The rewrite writes no owner anywhere, so the only row that
        needs a coder name is the journal entry; turning it off is the
        alternative the ask names."""
        self._unset(project)
        out = preview_of()
        result = execute_from(out, record_in_journal=False)
        assert result["success"] is True
        assert result["journal_entry"] is None
        assert query(project, "SELECT jid FROM journal") == []


# =============================================================================
# NAMES IN DURABLE RECORDS (fix round 1: QA F-1 and F-5, Security S1 and S2)
# =============================================================================

# The separators a real transcript file name uses. The first is the shape
# the two original guarding tests both used, and it is the only one of
# these that is not a word character: every other row here was judged to
# carry no name at all, because the withholding test ran the REWRITER's
# whole-word pattern over the name. Keep the first row: it is the
# regression, and dropping it would hide one.
NAME_SHAPES = [
    "Thomas interview.txt",      # a space
    "Thomas_interview.txt",      # an underscore, the docstring's own example
    "Thomas-interview.txt",      # a hyphen
    "Thomas.interview.txt",      # a dot
    "Thomas2.txt",               # a digit after the name
    "2Thomas.txt",               # a digit before it
    "ThomasB.txt",               # a letter after it
    "interview_Thomas.txt",      # the name at the end
    "int_Thomas_01.txt",         # the name in the middle
    "THOMAS_P01.txt",            # another case
    "Tom_2.docx",                # a variant rather than an original
    "Mary_Ann.txt",              # a multi-word name, separator changed
    "MaryAnn_notes.txt",         # a multi-word name run together
    "Tho\u00admas_interview.txt",   # a soft hyphen inside the name (S7)
    "Tho\u200bmas_interview.txt",   # a zero-width space inside it (S7)
    "\uff34\uff48\uff4f\uff4d\uff41\uff53.txt",   # fullwidth (S7)
    "Tho\u180fmas_interview.txt",   # a default-ignorable mark the round-3
                                   # approximation stopped short of (R2)
    "Tho\u2065mas_interview.txt",   # a reserved default-ignorable (R2)
]

# Every spelling of a real name this fixture's mapping covers. Checked
# case-sensitively, because a case-insensitive sweep for "tom" would hit
# an unrelated word in a path one day and the test would be believed.
NAME_SPELLINGS = ("Thomas", "THOMAS", "thomas", "Tom", "Mary Ann",
                  "Mary_Ann", "MaryAnn", "Mary")


def assert_carries_no_name(text, label, extra=()):
    for forbidden in NAME_SPELLINGS + tuple(extra):
        assert forbidden not in text, (label, forbidden)


def rename_file(project, name, fid=1):
    """Rename a source and reopen the server's connection on it."""
    con = sqlite3.connect(str(project / "data.qda"))
    con.execute("UPDATE source SET name=? WHERE id=?", (name, fid))
    con.commit()
    con.close()
    server.db.close()
    server.db = QualcoderDatabase(str(project))


@contextmanager
def wired(folder):
    """Select `folder` as the project for the body of a with-block."""
    original_db, original_path = server.db, server.current_project_path
    server.db = QualcoderDatabase(str(folder))
    server.current_project_path = str(folder)
    try:
        yield folder
    finally:
        try:
            server.db.close()
        except Exception:
            pass
        server.db, server.current_project_path = original_db, original_path


class TestAFileNameThatCarriesAName:
    """Every durable record, over every separator, not just the space.

    The two tests this class replaces both used `Thomas interview.txt`,
    and the QA gate showed the whole safeguard could be replaced by a
    literal matching that one string with the suite staying green. The
    parametrisation is the pin: a detector that misses any separator
    fails here, and a literal that matches one fails twelve times.
    """

    @pytest.mark.parametrize("name", NAME_SHAPES)
    def test_the_journal_entry_carries_neither_the_name_nor_the_file(
            self, project, name):
        rename_file(project, name)
        result = execute_from(preview_of())
        row = query(project, "SELECT name,jentry FROM journal")[0]
        assert_carries_no_name(row["name"], "journal name", (name,))
        assert_carries_no_name(row["jentry"], "journal body", (name,))
        assert "name withheld" in row["jentry"]
        assert f"file id 1" in row["jentry"]
        assert result["journal_entry"] == row["name"]

    @pytest.mark.parametrize("name", NAME_SHAPES)
    def test_the_manifest_carries_neither(self, project, name):
        rename_file(project, name)
        result = execute_from(preview_of())
        body = Path(result["manifest_path"]).read_text(encoding="utf-8")
        assert_carries_no_name(body, "manifest", (name,))
        assert json.loads(body)["files"][0]["name"] is None

    @pytest.mark.parametrize("name", NAME_SHAPES)
    def test_the_log_carries_neither(self, project, name, caplog):
        import logging
        rename_file(project, name)
        caplog.set_level(logging.DEBUG)
        execute_from(preview_of())
        logged = "\n".join(record.getMessage() for record in caplog.records)
        assert_carries_no_name(logged, "log", (name,))

    @pytest.mark.parametrize("name", NAME_SHAPES)
    def test_the_residue_counts_the_file_name(self, project, name):
        """The field D1 6.3 names explicitly, and the one field where
        underscores are the norm. It was asserted nowhere, and it
        reported zero (QA F-5)."""
        rename_file(project, name)
        residue = preview_of()["preview"]["residue"]
        assert residue["file_names"] == 1

    def test_an_nfd_mapping_still_withholds_an_nfc_file_name(self, project):
        """Fix round 3, B2. The detector normalises the mapping's forms as
        well as the values; dropping the form side reverted green, and at
        tool level it put the NFC spelling of the name into the journal
        body and the manifest when the mapping was typed in NFD (which
        is what a Mac pasteboard gives). Both spellings, checked."""
        nfd, nfc = "Rene\u0301", "Ren\u00e9"
        assert nfd != nfc and len(nfd) == 5 and len(nfc) == 4
        text = f"{nfd} spoke. {nfd} left."
        folder = build_project(tmp_path_for(project) / "rene.qda", text)
        add_coding(folder, 1, 1, 0, 5, text=text)
        write_fixture_sidecar(str(folder))
        rename = f"{nfc}_interview.txt"
        with wired(folder):
            rename_file(folder, rename)
            mapping = [{"original": nfd, "pseudonym": "Alex"}]
            out = preview_of(mapping=mapping)
            assert out["preview"]["residue"]["file_names"] == 1
            result = execute_from(out, mapping=mapping)
            assert result.get("success") is True, result
            row = query(folder, "SELECT name,jentry FROM journal")[0]
            body = Path(result["manifest_path"]).read_text(encoding="utf-8")
            for spelling in (nfd, nfc, "Ren"):
                assert spelling not in row["name"], spelling
                assert spelling not in row["jentry"], spelling
                assert spelling not in body, spelling
            assert "name withheld" in row["jentry"]
            assert json.loads(body)["files"][0]["name"] is None

    def test_a_file_name_with_no_name_in_it_is_still_reported(self, project):
        """Over-withholding is cheap but not free: a researcher reading
        the journal should still see the file names that are safe."""
        result = execute_from(preview_of())
        row = query(project, "SELECT jentry FROM journal")[0]
        assert "interview_01.txt" in row["jentry"]
        assert "name withheld" not in row["jentry"]
        body = Path(result["manifest_path"]).read_text(encoding="utf-8")
        assert json.loads(body)["files"][0]["name"] == "interview_01.txt"
        assert preview_of()["preview"]["residue"]["file_names"] == 0

    def test_a_marker_run_in_a_file_name_cannot_truncate_the_audit(
            self, project):
        """Security S6. `a#####b.txt` planted a private zone in the
        journal entry, and everything after the marker -- the counts, the
        backup, the manifest name, the line saying the names are not
        recorded -- was silently dropped."""
        rename_file(project, "a#####b.txt")
        result = execute_from(preview_of())
        body = query(project, "SELECT jentry FROM journal")[0]["jentry"]
        assert "#####" not in body
        assert "a####b.txt" in body
        # Path().name, not split("/"): manifest_path is str(Path), so on
        # Windows it is backslash-separated and split("/") returns the whole
        # path, which is never in the body (the body carries the bare file
        # name). This assertion was the only path-string surgery on the
        # branch and it was red on both Windows jobs.
        assert Path(result["manifest_path"]).name in body
        assert "The names replaced are not recorded here" in body


class TestATwoWordFormCarryingAnInvisibleCharacter:
    """Fix round 4, R1, at tool level: the regression the judge measured.

    A sidecar or a paste can carry a zero-width space or a soft hyphen
    between the two words of a name. At `ac359e3` the residue counted the
    file, the case and the memo that spell the name with a separator; at
    `f664ab4` it counted none of them, while the rewrite fired on nothing
    and the preview said so. Both readings must count them.
    """

    @pytest.mark.parametrize("label,char", [
        ("zero-width space", "\u200b"), ("soft hyphen", "\u00ad"),
        ("plain space, the control", " ")],
        ids=["zwsp", "soft-hyphen", "plain"])
    def test_the_separated_spellings_are_counted(self, project, label, char):
        con = sqlite3.connect(str(project / "data.qda"))
        con.execute("UPDATE source SET name=? WHERE id=4", ("Mary_Ann.txt",))
        con.execute("UPDATE cases SET name=? WHERE caseid=1", ("Mary-Ann",))
        con.execute("UPDATE source SET memo=? WHERE id=2", ("about Mary Ann",))
        con.commit()
        con.close()
        server.db.close()
        server.db = QualcoderDatabase(str(project))
        out = preview_of(mapping=[{"original": f"Mary{char}Ann",
                                   "pseudonym": "Sam"}])
        residue = out["preview"]["residue"]
        assert residue["file_names"] == 1, label
        assert residue["case_names"] == 1, label
        assert residue["memos"]["source"] == 1, label


class TestTheLabelsAndMemosTheResidueCounts:
    """Every field of the residue block, in the shapes that defeated it.

    Reproduced by the Security gate as five names surviving in a project
    whose residue block reported one of them, and `residue_total` of 1
    where it should have been 5.
    """

    def _plant(self, project, value):
        con = sqlite3.connect(str(project / "data.qda"))
        con.execute("UPDATE source SET name=?, memo=? WHERE id=1",
                    (value, f"Interviewed {value} at home."))
        con.execute("UPDATE cases SET name=? WHERE caseid=1", (value,))
        con.execute("UPDATE code_name SET name=? WHERE cid=1", (value,))
        con.execute("INSERT INTO attribute_type (name,date,owner,memo,"
                    "caseOrFile,valuetype) VALUES ('Role','d','TestCoder',"
                    "'','case','character')")
        con.execute("INSERT INTO attribute (attrid,name,attr_type,value,id,"
                    "date,owner) VALUES (1,'Role','case',?,1,'d',"
                    "'TestCoder')", (value,))
        con.commit()
        con.close()
        server.db.close()
        server.db = QualcoderDatabase(str(project))

    @pytest.mark.parametrize("value", ["Thomas_P01", "Thomas-P01",
                                       "Thomas2", "ThomasB", "Mary_Ann",
                                       "MaryAnn", "THOMAS_P01",
                                       "Thomas P01"])
    def test_every_label_field_counts_the_name(self, project, value):
        self._plant(project, value)
        residue = preview_of()["preview"]["residue"]
        assert residue["file_names"] == 1
        assert residue["case_names"] == 1
        assert residue["code_names"] == 1
        assert residue["attribute_values"] == 1
        assert residue["memos"]["source"] == 1

    # Every field the residue block reads, with a way to plant a name in
    # that field ALONE. One case per field, because the pins lens showed
    # seven of the ten memo fields could be dropped from
    # `PSEUDONYMISE_MEMO_FIELDS` with the whole suite green (fix round 3,
    # B2), and the five fields S6 added would have joined them. Each
    # planter is a statement over the stock fixture, whose tables are
    # QualCoder's own; the two media-coding memos are the ones a media
    # project most needs counted.
    RESIDUE_FIELDS = [
        ("memos.code_text", "UPDATE code_text SET memo=? WHERE ctid=1"),
        ("memos.annotation", "UPDATE annotation SET memo=? WHERE anid=1"),
        ("memos.case_text", "UPDATE case_text SET memo=? WHERE id=1"),
        ("memos.source", "UPDATE source SET memo=? WHERE id=1"),
        ("memos.cases", "UPDATE cases SET memo=? WHERE caseid=1"),
        ("memos.code_name", "UPDATE code_name SET memo=? WHERE cid=1"),
        ("memos.code_cat", "INSERT INTO code_cat (catid,name,owner,date,"
                           "memo) VALUES (1,'Themes','TestCoder','d',?)"),
        ("memos.project", "UPDATE project SET memo=?"),
        ("memos.attribute_type", "INSERT INTO attribute_type (name,date,"
                                 "owner,memo,caseOrFile,valuetype) VALUES "
                                 "('Role','d','TestCoder',?,'case',"
                                 "'character')"),
        ("memos.journal", "INSERT INTO journal (jid,name,jentry,date,owner) "
                          "VALUES (1,'Entry 1',?,'d','TestCoder')"),
        ("memos.code_av", "INSERT INTO code_av (avid,id,pos0,pos1,cid,memo,"
                          "date,owner,important) VALUES (1,3,0,10,1,?,'d',"
                          "'TestCoder',0)"),
        ("memos.code_image", "INSERT INTO code_image (imid,id,x1,y1,width,"
                             "height,cid,memo,date,owner,important) VALUES "
                             "(1,2,0,0,10,10,1,?,'d','TestCoder',0)"),
        ("case_names", "UPDATE cases SET name=? WHERE caseid=1"),
        ("file_names", "UPDATE source SET name=? WHERE id=4"),
        ("code_names", "UPDATE code_name SET name=? WHERE cid=2"),
        ("category_names", "INSERT INTO code_cat (catid,name,owner,date,"
                           "memo) VALUES (1,?,'TestCoder','d','')"),
        ("attribute_names", "INSERT INTO attribute_type (name,date,owner,"
                            "memo,caseOrFile,valuetype) VALUES (?,'d',"
                            "'TestCoder','','case','character')"),
        ("journal_names", "INSERT INTO journal (jid,name,jentry,date,owner) "
                          "VALUES (1,?,'a clean entry','d','TestCoder')"),
        ("attribute_values", ["INSERT INTO attribute_type (name,date,owner,"
                              "memo,caseOrFile,valuetype) VALUES ('Role',"
                              "'d','TestCoder','','case','character')",
                              "INSERT INTO attribute (attrid,name,attr_type,"
                              "value,id,date,owner) VALUES (1,'Role','case',"
                              "?,1,'d','TestCoder')"]),
    ]

    @staticmethod
    def _count_at(residue, field):
        block = residue
        for part in field.split("."):
            block = block[part]
        return block

    @staticmethod
    def _every_count(residue):
        """Every count in the block, keyed the way RESIDUE_FIELDS is."""
        counts = {f"memos.{k}": v for k, v in residue["memos"].items()}
        for key, value in residue.items():
            if isinstance(value, int) and not isinstance(value, bool):
                counts[key] = value
        return counts

    @pytest.mark.parametrize("field,sql", RESIDUE_FIELDS,
                             ids=[f for f, _ in RESIDUE_FIELDS])
    def test_each_field_is_counted_on_its_own(self, project, field, sql):
        value = "Interviewed Thomas_Smith at home."
        con = sqlite3.connect(str(project / "data.qda"))
        for statement in ([sql] if isinstance(sql, str) else sql):
            con.execute(statement, (value,) if "?" in statement else ())
        con.commit()
        con.close()
        server.db.close()
        server.db = QualcoderDatabase(str(project))
        out = preview_of()
        residue = out["preview"]["residue"]
        assert self._count_at(residue, field) == 1, field
        # The field ALONE: every other count is zero, so the one above
        # is this field's and not a neighbour's.
        others = {k: v for k, v in self._every_count(residue).items()
                  if k != field and k != "memos_with_private_zones_not_scanned"}
        assert all(v == 0 for v in others.values()), others
        assert "unreadable" not in residue
        # And it reaches the warning the researcher is read, as one.
        warning = [w for w in out["warnings"]
                   if "attribute value(s) may still show" in w]
        assert len(warning) == 1 and warning[0].startswith("Warning: 1 ")

    def test_the_field_table_is_the_whole_scan(self):
        """So a field added to the scan cannot go unpinned: the table
        above and the two tuples in the database layer name the same
        fields, and the warning sums every label key."""
        memo_fields = {f"memos.{table}"
                       for table, _ in QualcoderDatabase.PSEUDONYMISE_MEMO_FIELDS}
        label_keys = set(QualcoderDatabase.PSEUDONYMISE_RESIDUE_LABEL_KEYS)
        assert {f for f, _ in self.RESIDUE_FIELDS} == memo_fields | label_keys
        assert len(QualcoderDatabase.PSEUDONYMISE_MEMO_FIELDS) == 12
        assert len(QualcoderDatabase.PSEUDONYMISE_LABEL_FIELDS) == 6

    def test_a_project_with_no_residue_counts_none_of_it(self, project):
        residue = preview_of()["preview"]["residue"]
        for key in QualcoderDatabase.PSEUDONYMISE_RESIDUE_LABEL_KEYS:
            assert residue[key] == 0, key
        assert set(residue["memos"]) == {
            table for table, _ in QualcoderDatabase.PSEUDONYMISE_MEMO_FIELDS}
        assert all(count == 0 for count in residue["memos"].values())
        assert not any("attribute value(s) may still show" in w
                       for w in preview_of().get("warnings", []))

    def test_the_block_says_which_reading_its_counts_are(self, project):
        residue = preview_of()["preview"]["residue"]
        note = residue["reading_note"]
        assert "wider than the rewrite" in note
        # Fix round 3, L1: a rule used as a proxy for "a name remains
        # here" is a heuristic in the sense this repository uses the
        # word, and heuristics say so.
        assert note.startswith("These counts are a heuristic")
        # Fix round 3, S7: the reading has a stated limit.
        assert ("compared after Unicode normalisation with invisible "
                "characters removed; a look-alike letter from another "
                "script is not caught." in note)
        assert "must never under-report" not in note
        # The example is the one that shows the cost, not a near miss:
        # 'Lee' inside 'Leeds' reads as bad luck, 'Ed' inside 'edited'
        # reads as what it is (re-verification 6.1).
        assert ("It does mean a short name is generous: an entry for 'Ed' "
                "counts every memo that says 'edited' or 'provided'." in note)
        assert ("Read a high count as a list of fields to check, not as a "
                "count of names." in note)

    def test_the_block_says_what_it_does_not_cover(self, project):
        """`residue` is presented as the answer to "where do the names
        remain", and it does not read the file text at all: a spelling
        the rewrite did not match is still in the text and is counted
        nowhere. A count for the fulltext is a v0.13 item; saying so is
        not (re-verification, declined item (b))."""
        note = preview_of()["preview"]["residue"]["scope_note"]
        assert "covers memos, labels and attribute values" in note
        assert "does NOT count what remains in the file text itself" in note
        # Fix round 3, L2: a count is a list of fields to check, and the
        # researcher needs a route to them.
        assert ("To find the memos and journal entries a count points at, "
                "call search_memos with the name." in note)

    def test_a_short_form_is_warned_about_as_a_heuristic(self, project):
        """Fix round 3, L3 (re-verification 6.2). QualCoder's minimum for
        an original is two characters, and at that length the wide
        reading turns generous; the researcher hears it before
        approving. Entry indices and no form, so the warning is the same
        on the sidecar path."""
        out = preview_of(mapping=[{"original": "Ed", "pseudonym": "Kim"},
                                  {"original": "Thomas", "pseudonym": "Alex",
                                   "variants": ["Tom"]},
                                  {"original": "Mary Ann",
                                   "pseudonym": "Sam"}])
        assert out["preview"]["short_forms"] == [
            {"entry": 0, "length": 2}, {"entry": 1, "length": 3}]
        warning = [w for w in out["warnings"] if "surface form of fewer" in w]
        assert len(warning) == 1, out["warnings"]
        assert warning[0].startswith(
            "Warning: mapping entries [0, 1] have a surface form of fewer "
            "than 4 characters. The residue counts are a heuristic that "
            "reads wider than the rewrite, so a short form makes them "
            "generous")
        assert "still replaces whole words only" in warning[0]
        for forbidden in ("Ed", "Tom", "Kim"):
            assert forbidden not in warning[0], forbidden
        _house_rules(warning, ["short-form warning"])
        # One entry: the verb agrees with the noun (fix round 4, R5). The
        # fixture MAPPING's "Tom" is that entry, so every preview in the
        # suite carries this form of the warning.
        out = preview_of()
        assert out["preview"]["short_forms"] == [{"entry": 0, "length": 3}]
        singular = [w for w in out["warnings"] if "surface form of fewer" in w]
        assert len(singular) == 1
        assert singular[0].startswith(
            "Warning: mapping entry [0] has a surface form of fewer than 4 "
            "characters.")
        assert " have " not in singular[0]
        # Nothing shorter than four: no block and no warning.
        out = preview_of(mapping=[{"original": "Thomas", "pseudonym": "Alex"}])
        assert "short_forms" not in out["preview"]
        assert not any("surface form of fewer" in w
                       for w in out.get("warnings", []))

    def test_the_counts_reach_the_warning_the_researcher_is_read(
            self, project):
        self._plant(project, "Thomas_P01")
        out = preview_of()
        assert any("memo(s), label(s) or attribute value(s)" in warning
                   for warning in out["warnings"])

    def test_the_warning_says_what_the_count_measures(self, project):
        """Re-verification 6.1. The warning is the one string the
        description tells the model to relay, and it was the one string
        fix round 1 did not update with the detector: it still said the
        names "occur" in N places. Under the wide reading that is a
        false statement about what was measured, and this project's
        standing rule is that shipped prose must not claim more than the
        code does.

        Driven at the shape that makes it false: an entry for 'Ed', a
        memo that merely says 'edited'. The name does not occur there.
        """
        con = sqlite3.connect(str(project / "data.qda"))
        con.execute("UPDATE source SET memo=? WHERE id=1",
                    ("I edited this transcript after the visit.",))
        con.commit()
        con.close()
        server.db.close()
        server.db = QualcoderDatabase(str(project))
        out = preview_of(mapping=[{"original": "Ed", "pseudonym": "Kim"}])
        assert out["preview"]["residue"]["memos"]["source"] == 1
        warning = [text for text in out["warnings"]
                   if "attribute value(s)" in text]
        assert len(warning) == 1, out["warnings"]
        warning = warning[0]
        assert warning.startswith(
            "Warning: 1 memo(s), label(s) or attribute value(s) may still "
            "show one of these names, and this tool does not rewrite any "
            "of them.")
        assert ("The count is a heuristic and deliberately wide: it "
                "reports anything a reader might see in the spelling you "
                "gave, including inside a longer word and in any letter "
                "case, so it over-reports rather than under-reports; a "
                "look-alike letter from another script is not caught."
                in warning)
        assert "tell the user which fields to check" in warning
        # The claim that is now false, in the exact words it used.
        assert "the names in this mapping also occur in" not in warning
        # Fix round 3: the absolute clause it used to make.
        assert "it reports anything a reader might see, including" \
            not in warning
        _house_rules([warning], ["residue warning"])


class TestTheSafeNameHelperOnWhatIsNotAName:
    """Re-verification of fix round 1, R-8: the empty-string guard in
    `_pseudonymise_safe_name` reverted with the suite green. A name that
    is empty, or not a string at all, is withheld (None) rather than
    passed through as itself: "" is not a file name, and a `Path(...).name`
    of "" would otherwise reach the journal body as an empty label while
    the detector, asked about "", answers that no name is in it. Taken in
    the 0.12 release preparation as a single test row (T7); no behaviour
    changes."""

    @pytest.mark.parametrize("value", ["", None, 5, b"Thomas", []],
                             ids=repr)
    def test_anything_that_is_not_a_non_empty_string_is_withheld(
            self, value):
        compiled = P.Compiled(P.validate_mapping(MAPPING))
        assert server._pseudonymise_safe_name(value, compiled) is None

    def test_a_real_name_gets_one_of_the_two_answers(self):
        compiled = P.Compiled(P.validate_mapping(MAPPING))
        assert server._pseudonymise_safe_name(
            "interview.txt", compiled) == "interview.txt"
        assert server._pseudonymise_safe_name(
            "Thomas_interview.txt", compiled) is None


class TestTheProjectFolderName:
    """The second route, which had no guard at all (Security S1).

    The backup folder's name is derived from the PROJECT folder's name,
    and a single-case study is called after its participant. Nothing
    tested it, because every fixture in the suite calls the project
    `study.qda`.
    """

    def _build(self, tmp_path, folder_name):
        folder = build_project(tmp_path / folder_name)
        add_coding(folder, 1, 1, 0, 6)
        write_fixture_sidecar(str(folder))
        return folder

    def test_the_backup_name_is_withheld_from_the_journal_body(
            self, tmp_path):
        with wired(self._build(tmp_path, "Thomas study.qda")) as folder:
            result = execute_from(preview_of())
            body = query(folder, "SELECT jentry FROM journal")[0]["jentry"]
            assert_carries_no_name(body, "journal body")
            assert "its name is withheld" in body
            assert "newest backup folder" in body
            # The backup itself is still there and still named for the
            # project: what is withheld is the RECORD of its name.
            assert backups(folder), "no backup was taken"
            assert result["backup_path"]

    def test_the_manifest_withholds_both_paths(self, tmp_path):
        with wired(self._build(tmp_path, "Thomas study.qda")):
            result = execute_from(preview_of())
            manifest = json.loads(
                Path(result["manifest_path"]).read_text(encoding="utf-8"))
            assert_carries_no_name(json.dumps(manifest), "manifest")
            assert manifest["project_path"] is None
            assert manifest["backup_path"] is None
            assert "token_bind identifies the project" in \
                manifest["paths_withheld"]
            assert re.fullmatch(r"[0-9a-f]{8}", manifest["token_bind"])

    def test_the_log_carries_the_project_folder_name_nowhere(
            self, tmp_path, caplog):
        """Re-verification R-5, the seventh route. `backup_project` logs
        the backup folder's path twice at INFO, and that path carries
        the PROJECT folder's own name, which is the route Security
        called wholly unmitigated and fix round 1 closed everywhere
        else.

        `TestAFileNameThatCarriesAName::test_the_log_carries_neither`
        passed over this because its fixture project is called
        `study.qda`: the leak is the folder's name, not the file's.
        """
        import logging
        with wired(self._build(tmp_path, "Thomas study.qda")) as folder:
            caplog.set_level(logging.DEBUG)
            result = execute_from(preview_of())
            assert result.get("success") is True, result
        logged = "\n".join(record.getMessage() for record in caplog.records)
        assert "Creating backup" in logged, "the run took no backup"
        assert_carries_no_name(logged, "log")
        # What is kept is the part that says WHICH backup.
        assert re.search(r"_backup_\d{8}_\d{6}(_\d+)?\.qda", logged)
        assert str(folder) not in logged

    def test_an_ordinary_project_folder_is_recorded_in_full(self, tmp_path):
        with wired(self._build(tmp_path, "fieldwork.qda")) as folder:
            result = execute_from(preview_of())
            manifest = json.loads(
                Path(result["manifest_path"]).read_text(encoding="utf-8"))
            assert "fieldwork.qda" in manifest["project_path"]
            assert "fieldwork" in manifest["backup_path"]
            assert "paths_withheld" not in manifest
            body = query(folder, "SELECT jentry FROM journal")[0]["jentry"]
            assert "Backup taken before the run: fieldwork_backup_" in body


# =============================================================================
# EVERY STRING IN EVERY SINK, ON A PROJECT NAMED AFTER THE PARTICIPANT
# (fix round 3, B1: the final-verification judge's own method as a pin)
# =============================================================================

# The participant everything in the project is named after, and the
# colleague whose work is hidden in QualCoder. Two names on purpose: the
# participant's name is DECLARED to be returned in a few places (the
# project path and each file's own name, because a preview whose files
# cannot be named cannot be relayed), so a walk for it needs an allow
# list; the hidden coder's name is owed to nobody and the walk for it
# has none.
PARTICIPANT = "Thomas"
HIDDEN_COLLEAGUE = "Helga"

# Where the participant's name MAY appear in the two ephemeral results,
# on the sidecar path, exactly as the tool description declares: the
# project path, each file's own name, and the backup folder (which is
# derived from the project folder's name and named in the result so the
# researcher can find it). Anything else is a route.
DECLARED_ROUTES = (
    re.compile(r"^\.preview\.project$"),
    re.compile(r"^\.preview\.files\[\d+\]\.name$"),
    re.compile(r"^\.preview\.skipped_files\[\d+\]\.name$"),
    re.compile(r"^\.files\[\d+\]\.name$"),
    re.compile(r"^\.backup_path$"),
    re.compile(r"^\.notes\[\d+\]$"),          # names the backup folder
)


def walk_strings(value, path=""):
    """Every string in a JSON-shaped value, with the path it sits at."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from walk_strings(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from walk_strings(item, f"{path}[{index}]")
    elif isinstance(value, str):
        yield path, value


def routes_carrying(value, name, allowed=()):
    """The paths at which `name` appears, less the declared ones."""
    return [(path, text[:80]) for path, text in walk_strings(value)
            if name.lower() in text.lower()
            and not any(rule.match(path) for rule in allowed)]


class TestEveryStringOfEverySinkOnAProjectNamedAfterTheParticipant:
    """The judge's method, kept as the test that catches the next B1.

    A project whose folder, file, case, code, category, attribute type
    and value, journal entry and media-coding memos are all named after
    the participant, coded by a colleague hidden in QualCoder who also
    owns the file, the code, the category, the attribute and the
    journal entry, with two of her codings and two of her annotations
    cut by the same name so that they collide. Then EVERY string in the
    preview, the execute result, the manifest, the journal row and the
    captured log is walked, not the fields a reader would think to
    check: B1 lived in `unique_constraint_collisions[].key`, which no
    earlier test read, on a preview whose `by_owner` and
    `hidden_coder_rows` had both withheld the name.

    Two walks. The hidden coder's name may appear nowhere at all. The
    participant's name may appear only where the description declares
    it (`DECLARED_ROUTES`), and in the three durable records nowhere.
    """

    def _build(self, tmp_path, collide=True):
        folder = build_project(tmp_path / f"{PARTICIPANT} study.qda")
        con = sqlite3.connect(str(folder / "data.qda"))
        con.execute("UPDATE source SET name=?, memo=?, owner=? WHERE id=1",
                    (f"{PARTICIPANT}_interview.txt",
                     f"{PARTICIPANT} at home, {HIDDEN_COLLEAGUE} present",
                     HIDDEN_COLLEAGUE))
        con.execute("UPDATE cases SET name=?, memo=?, owner=? WHERE caseid=1",
                    (f"{PARTICIPANT}_P01", f"{PARTICIPANT} lives alone",
                     HIDDEN_COLLEAGUE))
        con.execute("UPDATE code_name SET name=?, memo=?, owner=? "
                    "WHERE cid=1",
                    (f"{PARTICIPANT}_trust", f"{HIDDEN_COLLEAGUE}'s code",
                     HIDDEN_COLLEAGUE))
        con.execute("INSERT INTO code_cat (catid,name,owner,date,memo) "
                    "VALUES (1,?,?,'d',?)",
                    (f"{PARTICIPANT} themes", HIDDEN_COLLEAGUE,
                     f"{PARTICIPANT} category memo"))
        con.execute("INSERT INTO attribute_type (name,date,owner,memo,"
                    "caseOrFile,valuetype) VALUES (?,'d',?,?,'case',"
                    "'character')",
                    (f"{PARTICIPANT} flag", HIDDEN_COLLEAGUE,
                     f"{PARTICIPANT} attribute memo"))
        con.execute("INSERT INTO attribute (attrid,name,attr_type,value,id,"
                    "date,owner) VALUES (1,?,'case',?,1,'d',?)",
                    (f"{PARTICIPANT} flag", f"{PARTICIPANT}_Smith",
                     HIDDEN_COLLEAGUE))
        con.execute("INSERT INTO journal (jid,name,jentry,date,owner) "
                    "VALUES (1,?,?,'d',?)",
                    (f"{PARTICIPANT} notes",
                     f"{HIDDEN_COLLEAGUE} wrote about {PARTICIPANT}",
                     HIDDEN_COLLEAGUE))
        con.execute("INSERT INTO code_av (avid,id,pos0,pos1,cid,memo,date,"
                    "owner,important) VALUES (1,3,0,10,1,?,'d',?,0)",
                    (f"{PARTICIPANT} laughs here", HIDDEN_COLLEAGUE))
        con.execute("INSERT INTO code_image (imid,id,x1,y1,width,height,cid,"
                    "memo,date,owner,important) VALUES (1,2,0,0,10,10,1,?,"
                    "'d',?,0)",
                    (f"{PARTICIPANT} in the photo", HIDDEN_COLLEAGUE))
        con.commit()
        con.close()
        add_coding(folder, 1, 1, 0, 6)                          # TestCoder
        add_coding(folder, 5, 1, 0, 6, owner=HIDDEN_COLLEAGUE)  # "Thomas"
        add_coding(folder, 7, 1, 19, 22, owner=HIDDEN_COLLEAGUE,
                   memo=f"{HIDDEN_COLLEAGUE}: {PARTICIPANT} again")
        # "Thomas said": a resize, exempt since ruling 7.4 and counted.
        # The rows on a name alone are substitutions since ruling 7.3(3)
        # and ride along exempt too. Annotation 3 is the damaged row
        # stored past the end of the text: a clamp, which is what walks
        # the override path on the non-colliding build.
        add_coding(folder, 8, 1, 0, 11, owner=HIDDEN_COLLEAGUE)
        add_annotation(folder, 3, 76, 300, owner=HIDDEN_COLLEAGUE,
                       memo=f"{HIDDEN_COLLEAGUE} damaged row")
        add_annotation(folder, 1, 0, 6, owner=HIDDEN_COLLEAGUE,
                       memo=f"{HIDDEN_COLLEAGUE} annotation")
        add_case_link(folder, 1, 0, len(TEXT), owner=HIDDEN_COLLEAGUE)
        if collide:
            add_coding(folder, 6, 1, 0, 4, owner=HIDDEN_COLLEAGUE)  # "Thom"
            add_annotation(folder, 2, 0, 4, owner=HIDDEN_COLLEAGUE)
        hide_coder(folder, HIDDEN_COLLEAGUE)
        write_fixture_sidecar(str(folder))
        # QualCoder's own two keys per entry (pseudonyms.py at the pin),
        # so the sidecar path is driven with the shape QualCoder writes.
        (folder / "pseudonyms.json").write_text(json.dumps(
            [{"original": PARTICIPANT, "pseudonym": "Alex"},
             {"original": "Mary Ann", "pseudonym": "Sam"}]),
            encoding="utf-8")
        # A linked media folder named after the participant, pointing
        # outside the project, which the backup skips and reports (fix
        # round 4, R3). Where a symlink cannot be made (Windows without
        # the privilege) the walk runs without it; the POSIX-only test
        # below proves the route is really taken.
        outside = tmp_path / f"{PARTICIPANT} videos"
        outside.mkdir()
        (folder / "media").mkdir()
        try:
            os.symlink(str(outside),
                       str(folder / "media" / f"{PARTICIPANT} videos"),
                       target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError):
            pass
        return folder

    @staticmethod
    def _sidecar_preview():
        return preview_of(mapping=None, use_project_pseudonyms=True)

    @pytest.mark.parametrize("source", ["typed", "sidecar"])
    def test_the_preview_and_the_refusal_name_the_hidden_coder_nowhere(
            self, tmp_path, source):
        with wired(self._build(tmp_path, collide=True)):
            out = (preview_of() if source == "typed"
                   else self._sidecar_preview())
            preview = out["preview"]
            # The shape is reached: both collisions listed, the override
            # required, and the coder's own rows counted rather than named.
            collisions = preview["files"][0]["unique_constraint_collisions"]
            assert collisions["code_text"] == [{"key": [1, 1, 0, 4],
                                                "row_ids": [5, 6]}]
            assert collisions["annotation"] == [{"key": [1, 0, 4],
                                                 "row_ids": [1, 2]}]
            assert preview["hidden_coder_rows"]["override_required"] is True
            owners = {e["owner"] for e in
                      preview["files"][0]["codings"]["by_owner"]}
            assert owners == {"TestCoder"}
            assert routes_carrying(out, HIDDEN_COLLEAGUE) == []
            execute = execute_from if source == "typed" else execute_as_recipe
            refused = execute(out, allow_hidden_coder=True)
            assert refused["reason"] == "unique_constraint_collision"
            assert routes_carrying(refused, HIDDEN_COLLEAGUE) == []

    def test_the_participant_reaches_the_preview_by_the_declared_routes_only(
            self, tmp_path):
        with wired(self._build(tmp_path, collide=True)):
            out = self._sidecar_preview()
            assert routes_carrying(out, PARTICIPANT, DECLARED_ROUTES) == []
            # The declared routes are really taken, so the allow list is
            # not passing an empty walk.
            assert out["preview"]["files"][0]["name"] == \
                f"{PARTICIPANT}_interview.txt"
            assert PARTICIPANT in out["preview"]["project"]
            # And the residue reports the fields, as counts.
            residue = out["preview"]["residue"]
            assert residue["file_names"] == 1
            assert residue["case_names"] == 1
            assert residue["code_names"] == 1
            assert residue["attribute_values"] == 1

    def test_the_execute_result_manifest_journal_and_log_carry_neither_name(
            self, tmp_path, caplog):
        import logging
        with wired(self._build(tmp_path, collide=False)) as folder:
            caplog.set_level(logging.DEBUG)
            out = self._sidecar_preview()
            hidden = out["preview"]["hidden_coder_rows"]
            assert hidden["override_required"] is True
            assert hidden["clamped"] == 1            # annotation 3, the reason
            assert hidden["resized"] >= 1            # coding 8, exempt
            assert hidden["substituted"] >= 1        # codings 5 and 7
            result = execute_as_recipe(out, allow_hidden_coder=True)
            assert result.get("success") is True, result
            assert result["hidden_coder_rows_updated"] >= 1
            # The ephemeral result: the hidden coder nowhere, the
            # participant by the declared routes only.
            assert routes_carrying(result, HIDDEN_COLLEAGUE) == []
            assert routes_carrying(result, PARTICIPANT, DECLARED_ROUTES) == []
            # The three durable records: neither name, anywhere.
            manifest = Path(result["manifest_path"]).read_text(
                encoding="utf-8")
            assert HIDDEN_COLLEAGUE.lower() not in manifest.lower()
            assert PARTICIPANT.lower() not in manifest.lower()
            rows = query(folder, "SELECT name, jentry, owner FROM journal "
                                 "WHERE jid != 1")
            assert len(rows) == 1
            for column, text in rows[0].items():
                assert HIDDEN_COLLEAGUE.lower() not in text.lower(), column
                assert PARTICIPANT.lower() not in text.lower(), column
            logged = "\n".join(record.getMessage()
                                for record in caplog.records)
            assert "Creating backup" in logged, "the run took no backup"
            assert HIDDEN_COLLEAGUE.lower() not in logged.lower()
            assert PARTICIPANT.lower() not in logged.lower()

    @POSIX_ONLY
    def test_the_linked_folder_named_after_the_participant_is_withheld(
            self, tmp_path, caplog):
        """Fix round 4, R3. The backup skips the link and reports it; the
        name follows the rule the file names follow (withheld where a
        reader would see a name from the mapping, the count kept), and
        the WARNING line the backup writes carries the count and the
        reason, never the path. So the walk above finds nothing, and this
        test shows that it is not passing an empty route."""
        import logging
        with wired(self._build(tmp_path, collide=False)):
            caplog.set_level(logging.DEBUG)
            result = execute_as_recipe(self._sidecar_preview(),
                                       allow_hidden_coder=True)
            assert result.get("success") is True, result
            assert result["backup_skipped_symlinks"] == 1
            assert result["backup_skipped_symlink_names"] == [None]
            assert result["backup_skipped_symlink_names_withheld"] == 1
            assert "withheld (null)" in result["backup_skipped_symlinks_note"]
            skipping = [record for record in caplog.records
                        if "Skipping" in record.getMessage()]
            assert len(skipping) == 1
            assert skipping[0].levelname == "WARNING"
            line = skipping[0].getMessage()
            assert "1 skipped so far" in line
            assert "outside the project" in line
            assert PARTICIPANT.lower() not in line.lower()
            assert "videos" not in line

    def test_the_walk_would_notice(self):
        """A walk that read only the top level would have passed B1."""
        nested = {"preview": {"files": [{"unique_constraint_collisions": {
            "code_text": [{"key": [1, 1, 0, 4, HIDDEN_COLLEAGUE],
                           "row_ids": [5, 6]}]}}]}}
        assert routes_carrying(nested, HIDDEN_COLLEAGUE) == [
            (".preview.files[0].unique_constraint_collisions.code_text[0]"
             ".key[4]", HIDDEN_COLLEAGUE)]
        declared = {"preview": {"project": f"/x/{PARTICIPANT} study.qda",
                                "files": [{"name": f"{PARTICIPANT}.txt"}]}}
        assert routes_carrying(declared, PARTICIPANT, DECLARED_ROUTES) == []
        assert len(routes_carrying(declared, PARTICIPANT)) == 2


@POSIX_ONLY
class TestASymlinkNamedAfterTheParticipant:
    """Fix round 4, R3, at the two sites: the backup's log line (every
    tool that takes a backup) and the flagship's result."""

    @staticmethod
    def _link(project, tmp_path, name):
        outside = tmp_path / f"outside {name}"
        outside.mkdir()
        (project / "media").mkdir(exist_ok=True)
        os.symlink(str(outside), str(project / "media" / name),
                   target_is_directory=True)
        return os.path.join("media", name)

    def test_the_log_line_carries_the_count_and_the_reason_never_the_path(
            self, project, tmp_path, caplog):
        import logging
        from qualcoder_mcp import database
        rel = self._link(project, tmp_path, "Thomas videos")
        caplog.set_level(logging.DEBUG)
        report = {}
        backup = database.backup_project(project, report)
        assert report["skipped_symlinks"] == [rel]
        assert not (backup / "media" / "Thomas videos").exists()
        lines = [record for record in caplog.records
                 if "Skipping" in record.getMessage()]
        assert len(lines) == 1 and lines[0].levelname == "WARNING"
        text = lines[0].getMessage()
        assert "1 skipped so far" in text
        assert "points outside the project or dangles" in text
        assert "Thomas" not in text and "videos" not in text
        shutil.rmtree(backup)

    def test_the_flagship_withholds_the_name_and_keeps_the_count(
            self, project, tmp_path):
        """One link named with a space and one with the underscore a
        file system holds (fix round 5, S4): the rule is the detector's,
        not the rewriter's whole-word pattern, to which `_` is a word
        character and `Thomas_videos` carries no name."""
        named = self._link(project, tmp_path, "Thomas videos")
        underscored = self._link(project, tmp_path, "Thomas_videos")
        plain = self._link(project, tmp_path, "field videos")
        result = execute_from(preview_of())
        assert result.get("success") is True, result
        assert result["backup_skipped_symlinks"] == 3
        assert sorted(result["backup_skipped_symlink_names"],
                      key=str) == sorted([None, None, plain], key=str)
        assert result["backup_skipped_symlink_names_withheld"] == 2
        assert "2 of the names listed are withheld (null)" in \
            result["backup_skipped_symlinks_note"]
        assert routes_carrying(result, "Thomas", DECLARED_ROUTES) == []
        assert named not in json.dumps(result)
        assert underscored not in json.dumps(result)


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

        def probing_write(self, plan, fingerprints):
            con = sqlite3.connect(str(project / "data.qda"), timeout=0.1)
            try:
                con.execute("UPDATE source SET memo='x' WHERE id=4")
                con.commit()
                blocked.append(False)
            except sqlite3.OperationalError:
                blocked.append(True)
            finally:
                con.close()
            return original(self, plan, fingerprints)

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
# THE FIXES OF ROUND 1 (QA F-2, F-3, F-13; Security S3, S5, S7, S8, S9)
# =============================================================================

class TestAJournalFailureCannotBeReportedAsSuccess:
    """QA F-2. `add_journal_entry` rolled the connection back on two of
    its own error paths, the flagship swallowed the exception, and `_op`
    returned `{"success": true, "message": "Pseudonymised 1 file(s)"}`
    with new lengths and new sha256 values over a database that was
    byte-for-byte unchanged. Reachable on disk-full and on I/O errors.
    """

    @staticmethod
    def _state(project):
        return (query(project, "SELECT fulltext FROM source ORDER BY id"),
                query(project, "SELECT ctid,pos0,pos1,seltext FROM code_text "
                               "ORDER BY ctid"),
                query(project, "SELECT anid,pos0,pos1 FROM annotation "
                               "ORDER BY anid"),
                query(project, "SELECT jid FROM journal"))

    def test_a_failing_insert_no_longer_takes_the_rewrite_with_it(
            self, project):
        """The real code path, through SQLite: a BEFORE INSERT trigger
        standing in for any INSERT-time failure that is not a duplicate
        name. The statement fails, the transaction survives, and the
        honest outcome is the rewrite plus `journal_entry: null`."""
        con = sqlite3.connect(str(project / "data.qda"))
        con.execute("CREATE TRIGGER no_journal BEFORE INSERT ON journal "
                    "BEGIN SELECT RAISE(ABORT, 'simulated disk error'); END")
        con.commit()
        con.close()
        server.db.close()
        server.db = QualcoderDatabase(str(project))
        result = execute_from(preview_of())
        assert result["success"] is True
        assert result["journal_entry"] is None
        assert query(project, "SELECT jid FROM journal") == []
        text = query(project, "SELECT fulltext FROM source WHERE id=1"
                     )[0]["fulltext"]
        assert "Thomas" not in text and "Alex" in text
        assert text == result["files"][0]["new_length"] * "x" or True
        assert len(text) == result["files"][0]["new_length"]

    def test_a_rollback_under_the_run_is_refused_not_reported(
            self, project, monkeypatch):
        """The guard itself. Any future path that rolls the transaction
        back inside the run has to end as an error, never as a success
        report over an unchanged database."""
        def rollback_then_raise(self, name, entry, owner=None,
                                auto_commit=True):
            self.conn.rollback()
            raise RuntimeError("Failed to add journal entry: simulated "
                               "disk error")

        monkeypatch.setattr(QualcoderDatabase, "add_journal_entry",
                            rollback_then_raise)
        before = self._state(project)
        result = execute_from(preview_of())
        assert "error" in result, result
        assert "rolled back with it; nothing was changed" in result["error"]
        assert "record_in_journal=false" in result["error"]
        assert "success" not in result
        assert self._state(project) == before
        assert server.db.read_only is True

    def test_a_helper_told_not_to_commit_does_not_roll_back(self, project):
        """The other half of the fix, at the layer it belongs to:
        `auto_commit=False` says a caller owns this transaction."""
        db = QualcoderDatabase(str(project), read_only=False)
        try:
            db.begin_immediate()
            db.conn.execute("UPDATE source SET memo='keep me' WHERE id=1")
            db.add_journal_entry(name="Kept", entry="x", owner="TestCoder",
                                 auto_commit=False)
            with pytest.raises(ValueError):
                db.add_journal_entry(name="Kept", entry="y",
                                     owner="TestCoder", auto_commit=False)
            assert db.conn.in_transaction is True
            db.conn.commit()
        finally:
            db.close()
        assert query(project, "SELECT memo FROM source WHERE id=1"
                     )[0]["memo"] == "keep me"


class TestTheFingerprintCheckIsACheck:
    """QA F-3. The C7 loop compared a read to itself: `_op` rebuilt the
    plan on the write connection and then verified the plan's own
    fingerprints against a re-read on that same connection, inside the
    same transaction. It could not fail, and it would not have failed
    with the state guard deleted. It now takes the fingerprints the
    READ-ONLY phase captured, which is what D1 3.6 specifies.

    Driven at the database layer, because the tool-level route is
    shadowed: the signed state digests every eligible file as
    (id, length, sha256), so a fulltext change between the preview and
    the execute is refused before C7 is reached. That path has its own
    pin in `test_a_racing_write_is_caught_inside_the_transaction`.

    The last two tests in this class are about the CALLER rather than
    the check, and they exist because the database layer's tests could
    not tell: re-verification restored the tautology at
    `_op` alone, by taking the fingerprints from the write phase's own
    plan instead of the read-only phase's, and the whole suite stayed
    green. `pseudonymise_write`'s signature did not change, its own
    tests pass the fingerprints they choose, and nothing anywhere
    watched which dictionary the tool handed it. That is the second time
    this guard has been able to be a no-op on this feature.
    """

    def _plan(self, db):
        compiled = P.Compiled(P.validate_mapping(MAPPING))
        return db.pseudonymise_plan(compiled, "snap_to_pseudonym", None)

    def test_the_fingerprints_that_govern_are_the_ones_passed_in(
            self, project):
        """The plan's own fingerprints match the text on disk here, so
        the version that verified against them accepted this write. The
        preview's say the file moved, and that is the answer that
        counts."""
        db = QualcoderDatabase(str(project), read_only=False)
        try:
            plan = self._plan(db)
            stale = {1: (11, "0" * 64)}
            with pytest.raises(ValueError) as excinfo:
                db.pseudonymise_write(plan, stale)
            assert "file id 1 changed while this write was being prepared" \
                in str(excinfo.value)
        finally:
            db.close()
        assert query(project, "SELECT fulltext FROM source WHERE id=1"
                     )[0]["fulltext"] == TEXT

    def test_a_touched_file_the_preview_never_saw_is_refused(self, project):
        db = QualcoderDatabase(str(project), read_only=False)
        try:
            plan = self._plan(db)
            with pytest.raises(ValueError) as excinfo:
                db.pseudonymise_write(plan, {})
            assert "was not in the preview" in str(excinfo.value)
        finally:
            db.close()
        assert query(project, "SELECT fulltext FROM source WHERE id=1"
                     )[0]["fulltext"] == TEXT

    def test_the_matching_fingerprints_let_the_write_through(self, project):
        db = QualcoderDatabase(str(project), read_only=False)
        try:
            plan = self._plan(db)
            db.begin_immediate()
            report = db.pseudonymise_write(
                plan, {item["file_id"]: item["old_fingerprint"]
                       for item in plan["files"]})
            db.conn.commit()
        finally:
            db.close()
        assert report["files"][0]["replacements"] == 5
        assert "Thomas" not in query(
            project, "SELECT fulltext FROM source WHERE id=1"
            )[0]["fulltext"]

    def test_the_write_is_checked_against_the_read_phases_own_tuples(
            self, project, monkeypatch):
        """Which dictionary `_op` hands to `pseudonymise_write`, pinned
        by provenance rather than by value.

        On an unchanged project the read phase's fingerprints and the
        write phase's are EQUAL, which is why a value comparison cannot
        tell them apart and why the revert went unnoticed. The tuples
        are not the same objects, though: each plan builds its own. So
        the assertion is `is`, against the plan built on the read-only
        connection before the backup, and `is not` against the plan the
        state check built inside the transaction.
        """
        plans = []
        plan_original = QualcoderDatabase.pseudonymise_plan
        write_original = QualcoderDatabase.pseudonymise_write
        handed = []

        def remember(self, *args, **kwargs):
            plan = plan_original(self, *args, **kwargs)
            plans.append((bool(self.conn.in_transaction), plan))
            return plan

        def watch(self, plan, fingerprints):
            handed.append(fingerprints)
            return write_original(self, plan, fingerprints)

        out = preview_of()
        monkeypatch.setattr(QualcoderDatabase, "pseudonymise_plan", remember)
        monkeypatch.setattr(QualcoderDatabase, "pseudonymise_write", watch)
        assert execute_from(out)["success"] is True

        read_phase = [plan for in_transaction, plan in plans
                      if not in_transaction]
        write_phase = [plan for in_transaction, plan in plans
                       if in_transaction]
        assert len(read_phase) == 1 and len(write_phase) == 1, plans
        assert len(handed) == 1
        fingerprints = handed[0]

        read_tuples = {item["file_id"]: item["old_fingerprint"]
                       for item in read_phase[0]["files"]}
        write_tuples = {item["file_id"]: item["old_fingerprint"]
                        for item in write_phase[0]["files"]}
        assert set(fingerprints) == set(read_tuples) == {1}
        assert fingerprints == read_tuples == write_tuples, (
            "the two phases disagree about the text, so this test is "
            "measuring something other than provenance")
        for file_id, value in read_tuples.items():
            assert fingerprints[file_id] is value, (
                "the write was checked against fingerprints the READ-ONLY "
                "phase did not capture")
            assert fingerprints[file_id] is not write_tuples[file_id], (
                "the write was checked against its own plan's "
                "fingerprints, which is the F-3 tautology")

    def test_a_write_that_lands_after_the_read_phase_is_caught_by_c7(
            self, project, monkeypatch):
        """C7 at tool level, driven, with the signed state blinded.

        The signed state normally answers this first and more cheaply
        (`test_a_racing_write_is_caught_inside_the_transaction`), which
        is why the tautology could be restored here with the suite
        green. So both halves of the signed state are replaced by a
        constant for this test, in the preview call and in the execute
        call alike, which leaves C7 as the only thing between a racing
        writer and a rewrite computed over text that writer has already
        replaced.

        The race is committed from a second connection at
        BEGIN IMMEDIATE, so it lands after the read-only phase captured
        its fingerprints and before the write phase builds its plan. The
        read phase's fingerprints no longer describe the file; the write
        phase's describe it exactly. Only one of those two answers
        refuses.
        """
        monkeypatch.setattr(QualcoderDatabase, "pseudonymise_effect",
                            lambda self, plan: {"blinded": True})
        monkeypatch.setattr(QualcoderDatabase, "pseudonymise_row_digests",
                            lambda self, plan: {"blinded": True})
        out = preview_of()
        assert "preview_token" in out

        raced = TEXT + " A later note about Thomas."
        begin_original = QualcoderDatabase.begin_immediate
        fired = []

        def racing_begin(self):
            if not fired:
                fired.append(True)
                con = sqlite3.connect(str(project / "data.qda"))
                con.execute("UPDATE source SET fulltext = ? WHERE id = 1",
                            (raced,))
                con.commit()
                con.close()
            return begin_original(self)

        monkeypatch.setattr(QualcoderDatabase, "begin_immediate",
                            racing_begin)
        refused = execute_from(out)
        assert fired, "the race never happened; the test proves nothing"
        assert "success" not in refused, refused
        assert "The text of file id 1 changed while this write was being " \
               "prepared" in refused["error"]
        assert query(project, "SELECT fulltext FROM source WHERE id=1"
                     )[0]["fulltext"] == raced


class TestWhatTheRunRecomputes:
    """The performance shape, pinned rather than measured (QA F-14,
    Security section 7). Five plans and two residue scans per execute,
    one of the scans inside the write transaction while holding the
    RESERVED lock, was not a bug but it was not a design either.
    """

    @staticmethod
    def _instrument(monkeypatch):
        calls = {"plans": [], "residue": []}
        plan_original = QualcoderDatabase.pseudonymise_plan
        residue_original = QualcoderDatabase.pseudonymise_residue

        def counting_plan(self, *args, **kwargs):
            calls["plans"].append(bool(self.conn.in_transaction))
            return plan_original(self, *args, **kwargs)

        def counting_residue(self, *args, **kwargs):
            calls["residue"].append(bool(self.conn.in_transaction))
            return residue_original(self, *args, **kwargs)

        monkeypatch.setattr(QualcoderDatabase, "pseudonymise_plan",
                            counting_plan)
        monkeypatch.setattr(QualcoderDatabase, "pseudonymise_residue",
                            counting_residue)
        return calls

    def test_the_preview_builds_one_plan_and_scans_once(self, project,
                                                        monkeypatch):
        calls = self._instrument(monkeypatch)
        preview_of()
        assert calls["plans"] == [False]
        assert calls["residue"] == [False]

    def test_the_execute_builds_one_plan_per_connection(self, project,
                                                        monkeypatch):
        out = preview_of()
        calls = self._instrument(monkeypatch)
        assert execute_from(out)["success"] is True
        # One on the read-only connection (the preview, the row digests
        # and the C7 fingerprints), one inside the transaction (the
        # signed state and the write itself).
        assert calls["plans"] == [False, True]

    def test_no_residue_scan_happens_inside_the_write_transaction(
            self, project, monkeypatch):
        out = preview_of()
        calls = self._instrument(monkeypatch)
        execute_from(out)
        assert calls["residue"] == [False]

    def test_the_read_phase_plan_cache_is_keyed_on_the_connection(
            self, project, monkeypatch):
        """Re-verification R-7. The cache exists so the preview and the
        row digests describe ONE read of the project, and it is keyed on
        the connection that filled it so a cached plan can never be
        handed to the write connection inside the transaction: that
        would make the state check compare a read to itself, which is
        the shape of the defect this round exists to remove.

        Reverting the key to `if "plan" in read_phase` leaves the whole
        suite green today, because `state_of_fn` means the write
        connection never asks the cache. The property is therefore
        driven directly, on the closure the tool hands the gate: the
        same connection is answered from the cache, a different one is
        not.
        """
        captured = {}
        guard_original = server._guarded_destructive

        def capture(**kwargs):
            captured.update(kwargs)
            return guard_original(**kwargs)

        plans = []
        plan_original = QualcoderDatabase.pseudonymise_plan

        def remember(self, *args, **kwargs):
            plan = plan_original(self, *args, **kwargs)
            plans.append(plan)
            return plan

        monkeypatch.setattr(server, "_guarded_destructive", capture)
        monkeypatch.setattr(QualcoderDatabase, "pseudonymise_plan", remember)
        preview_of()
        assert len(plans) == 1, "the preview built more than one plan"
        preview_fn = captured["preview_fn"]

        preview_fn(server.db)
        assert len(plans) == 1, (
            "the connection that filled the cache asked again and was not "
            "answered from it")

        other = QualcoderDatabase(str(project))
        try:
            preview_fn(other)
        finally:
            other.close()
        assert len(plans) == 2, (
            "a DIFFERENT connection was handed the cached plan; that is the "
            "route by which the state check could compare a read to itself")
        assert plans[1] is not plans[0]

    def test_the_run_writes_the_plan_whose_effect_was_verified(
            self, project, monkeypatch):
        """Not a sixth recomputation of it: `_op` takes the plan the
        state check built, on the same connection, in the same
        transaction, with nothing written in between."""
        seen = []
        original = QualcoderDatabase.pseudonymise_write
        plan_original = QualcoderDatabase.pseudonymise_plan

        def remember(self, *args, **kwargs):
            plan = plan_original(self, *args, **kwargs)
            seen.append(id(plan))
            return plan

        written = []

        def watch(self, plan, fingerprints):
            written.append(id(plan))
            return original(self, plan, fingerprints)

        monkeypatch.setattr(QualcoderDatabase, "pseudonymise_plan", remember)
        monkeypatch.setattr(QualcoderDatabase, "pseudonymise_write", watch)
        execute_from(preview_of())
        assert written == [seen[-1]]


class TestTheClampIsNotAShift:
    """Security S5. `map_row` clamps a `pos1` past the end of the text
    BEFORE classifying, so a row that was clamped and then shifted was
    reported as a pure shift, which ruling X1 exempts. A hidden coder's
    annotation stored at (76, 200) on an 81-character text was written
    as (66, 71) with no override asked for: a 119-character truncation.

    Fix round 1 (B6) kept it gated by calling it a resize. Ruling 7.4 of
    2026-09-16 exempts the resize, so the clamp has its own class now and
    is gated on its own account.
    """

    def _damaged_hidden_row(self, project):
        hide_coder(project, "Hidden Helga")
        add_annotation(project, 2, 76, 200, owner="Hidden Helga")
        server.db.close()
        server.db = QualcoderDatabase(str(project))

    def test_a_clamped_row_is_classified_as_clamped_and_counted_once(
            self, project):
        """The per-table `clamped` key still counts every row whose end
        was pulled back, and the row is not counted twice now that
        `clamped` is also a class: the classes and `not_mapped` add up
        to `total`."""
        add_annotation(project, 2, 76, 200)
        server.db.close()
        server.db = QualcoderDatabase(str(project))
        counts = preview_of()["preview"]["files"][0]["annotations"]
        assert counts["clamped"] == 1
        assert counts["shifted"] == 0
        assert sum(counts[key] for key in
                   ("shifted", "substituted", "resized", "snapped",
                    "deleted", "unchanged", "clamped", "not_mapped")) \
            == counts["total"]

    def test_a_clamped_hidden_row_requires_the_override(self, project):
        """Ruling 7.4: `clamped` is its own count and it alone gates
        this row. A revert that folded it back into `resized` and
        exempted it lets the truncation through."""
        self._damaged_hidden_row(project)
        out = preview_of()
        hidden = out["preview"]["hidden_coder_rows"]
        assert hidden["override_required"] is True
        assert hidden["clamped"] == 1
        assert (hidden["resized"], hidden["shifted"]) == (0, 0)
        refused = call(mapping=MAPPING, preview_token=out["preview_token"])
        assert refused["reason"] == "hidden_coder_override_required"
        assert refused["nothing_changed"] is True
        assert backups(project) == []
        assert query(project, "SELECT pos0,pos1 FROM annotation "
                              "WHERE anid=2") == [{"pos0": 76, "pos1": 200}]

    def test_the_override_still_lets_it_through(self, project):
        self._damaged_hidden_row(project)
        result = execute_from(preview_of(), allow_hidden_coder=True)
        assert result["success"] is True
        assert query(project, "SELECT pos0,pos1 FROM annotation "
                              "WHERE anid=2") == [{"pos0": 66, "pos1": 71}]

    def test_a_clamp_that_lands_exactly_on_a_name_is_still_gated(
            self, project, tmp_path):
        """A hidden coder's row stored as (4, 200) on "say Thomas" is
        clamped to (4, 10), which is exactly the name; the coder never
        marked the name alone, so the truncation is neither a
        substitution nor an ordinary resize but a clamp, and the
        override is asked for (rulings 7.3(3) and 7.4)."""
        text = "say Thomas"
        folder = build_project(tmp_path / "end.qda", text=text)
        add_annotation(folder, 2, 4, 200, owner="Hidden Helga")
        hide_coder(folder, "Hidden Helga")
        write_fixture_sidecar(str(folder))
        with wired(folder):
            out = preview_of()
            hidden = out["preview"]["hidden_coder_rows"]
            assert hidden["clamped"] == 1
            assert (hidden["substituted"], hidden["resized"]) == (0, 0)
            assert hidden["override_required"] is True
            assert out["preview"]["files"][0]["annotations"]["clamped"] == 1
            refused = call(mapping=MAPPING, preview_token=out["preview_token"])
            assert refused["reason"] == "hidden_coder_override_required"
            assert query(folder, "SELECT pos0, pos1 FROM annotation "
                                 "WHERE anid=2") == [{"pos0": 4, "pos1": 200}]
            result = execute_from(out, allow_hidden_coder=True)
            assert result["success"] is True
            assert query(folder, "SELECT pos0, pos1 FROM annotation "
                                 "WHERE anid=2") == [{"pos0": 4, "pos1": 8}]

    def test_the_refusal_is_the_flagships_own_text(self, project):
        """QA F-4 and Security S10. The shared codebook text points at
        `hidden_coder_codings_affected`, a key this preview does not
        have, says "codings" for what may be an annotation, and does not
        say that a pure shift is exempt."""
        self._damaged_hidden_row(project)
        out = preview_of()
        refused = call(mapping=MAPPING, preview_token=out["preview_token"])
        error = refused["error"]
        assert "hidden_coder_rows in the preview counts them" in error
        assert "hidden_coder_codings_affected" not in error
        assert "snap, delete or clamp" in error
        assert "resize, snap or delete" not in error
        assert "pure position shift of such a span is exempt" in error
        assert "Hidden Helga" not in json.dumps(refused)
        _house_rules([error])


class TestTheParkingFloor:
    """QA F-9 and Security S7. The floor was taken over the MOVING rows
    only, so a stationary damaged row already stored at a negative span
    sat exactly where the parking was about to write, and the whole run
    failed on the UNIQUE constraint.
    """

    def _swap_project(self, project):
        text = "Thomas aa bb cc Thomas dd"
        folder = build_project(tmp_path_for(project) / "park.qda", text)
        add_coding(folder, 30, 1, 9, 12, owner="Bob", seltext=text[9:12],
                   text=text)
        add_coding(folder, 31, 1, 7, 10, owner="Bob", seltext=text[7:10],
                   text=text)
        write_fixture_sidecar(str(folder))
        server.db.close()
        server.db = QualcoderDatabase(str(folder))
        server.current_project_path = str(folder)
        return folder

    def test_a_stationary_damaged_row_does_not_break_the_parking(
            self, project):
        folder = self._swap_project(project)
        con = sqlite3.connect(str(folder / "data.qda"))
        con.execute("INSERT INTO code_text (ctid,cid,fid,seltext,pos0,pos1,"
                    "owner,date,memo,important) VALUES "
                    "(32,1,1,'',-4,-3,'Bob','d','',0)")
        con.commit()
        con.close()
        server.db.close()
        server.db = QualcoderDatabase(str(folder))
        mapping = [{"original": "Thomas", "pseudonym": "Alex"}]
        out = preview_of(mapping=mapping)
        # The damaged row is reported rather than mapped.
        assert 32 in out["preview"]["files"][0]["null_position_rows"][
            "code_text"]
        result = execute_from(out, mapping=mapping)
        assert result.get("success") is True, result
        moved = {r["ctid"]: (r["pos0"], r["pos1"]) for r in
                 query(folder, "SELECT ctid,pos0,pos1 FROM code_text")}
        assert moved == {30: (7, 10), 31: (5, 8), 32: (-4, -3)}


class TestWhatTheTokenSigns:

    def test_renaming_a_file_this_run_skips_keeps_the_token_valid(
            self, project):
        """QA F-13. `skipped_files` carried NAMES into the signed effect,
        so renaming an untouched PDF invalidated a live token as
        `project_changed`, with an explanation that was false."""
        out = preview_of()
        con = sqlite3.connect(str(project / "data.qda"))
        con.execute("UPDATE source SET name='renamed.pdf' WHERE id=2")
        con.commit()
        con.close()
        server.db.close()
        server.db = QualcoderDatabase(str(project))
        result = execute_from(out)
        assert result.get("success") is True, result

    def test_the_readable_preview_still_names_the_skipped_files(
            self, project):
        skipped = preview_of()["preview"]["skipped_files"]
        assert {item["file_id"]: item["reason"] for item in skipped} == {
            2: "pdf_source", 3: "no_fulltext"}
        assert any(item.get("name") == "paper.pdf" for item in skipped)

    def test_a_changed_text_still_invalidates_the_token(self, project):
        """The other direction, so the narrowing did not narrow too far."""
        out = preview_of()
        con = sqlite3.connect(str(project / "data.qda"))
        con.execute("UPDATE source SET fulltext='Thomas again' WHERE id=1")
        con.commit()
        con.close()
        server.db.close()
        server.db = QualcoderDatabase(str(project))
        assert execute_from(out)["reason"] == "project_changed"


class TestThePresentationArguments:
    """Security S8 and S9. `context_chars`' own cap was nullified by an
    uncapped `max_spans_per_entry`, and an unvalidated one leaked a raw
    Python message into the envelope.
    """

    @pytest.mark.parametrize("kwargs,fragment", [
        ({"max_spans_per_entry": "x"}, "must be a whole number"),
        ({"max_spans_per_entry": 1.5}, "must be a whole number"),
        ({"max_spans_per_entry": True}, "must be a whole number"),
        ({"max_spans_per_entry": 0}, "must be at least 1"),
        ({"max_spans_per_entry": -1}, "must be at least 1"),
        ({"context_chars": "5"}, "must be a whole number"),
        ({"context_chars": -1}, "must be at least 0"),
    ], ids=repr)
    def test_a_bad_presentation_argument_is_named_in_our_own_words(
            self, project, kwargs, fragment):
        out = preview_of(**kwargs)
        assert fragment in out["error"]
        assert "__index__" not in out["error"]
        _house_rules([out["error"]])

    def test_a_huge_span_request_is_capped_rather_than_refused(
            self, project):
        out = preview_of(max_spans_per_entry=10 ** 9)
        assert "error" not in out

    def test_the_span_cap_is_five_hundred(self, tmp_path):
        """Re-verification R-6. This cap was added in fix round 1 in
        answer to Security S8 and then appeared nowhere in `tests/`:
        lifting it from 500 to 1,000,000 left the whole suite green,
        which is F-20's own failure mode on the cap that round
        introduced. The other four caps pin the literal AND drive the
        boundary, so this one does too.

        It is a silent cap in the house `validate_limit` shape, not a
        refusal, so the boundary is driven by counting what comes back:
        600 matches, and no argument gets more than 500 of them.
        """
        assert P.MAX_SPANS_PER_ENTRY == 500
        folder = build_project(tmp_path / "many.qda", "Thomas. " * 600)
        write_fixture_sidecar(str(folder))
        with wired(folder):
            def block(requested):
                out = preview_of(mapping=[{"original": "Thomas",
                                           "pseudonym": "Alex"}],
                                 max_spans_per_entry=requested)
                return out["preview"]["files"][0]["replacements"][0]

            asked_for_everything = block(10 ** 9)
            assert asked_for_everything["count"] == 600
            assert len(asked_for_everything["spans"]) == 500
            assert asked_for_everything["spans_truncated"] is True
            # One below the cap, at it, and one above it.
            assert len(block(499)["spans"]) == 499
            assert len(block(500)["spans"]) == 500
            assert len(block(501)["spans"]) == 500

    def test_the_context_budget_bounds_the_whole_preview(self, tmp_path):
        """Not each window: the windows overlap, so 400 of them returned
        4.7 times the file."""
        text = ("Thomas said a great deal about the weather and the "
                "fieldwork. ") * 400
        folder = build_project(tmp_path / "long.qda", text)
        write_fixture_sidecar(str(folder))
        with wired(folder):
            out = preview_of(include_context=True, context_chars=120,
                             max_spans_per_entry=10 ** 9)
            blocks = out["preview"]["files"][0]["replacements"]
            returned = sum(len(window) for block in blocks
                           for window in block.get("context", []))
            assert returned <= P.MAX_CONTEXT_TOTAL_CHARS
            assert any(block.get("context_truncated") for block in blocks)
            assert returned < len(text)


class TestTheSidecarKeepsItsNamesOutOfTheConversation:
    """Security S3. D1 3.10 promises that reading `pseudonyms.json`
    pulls the real names into the process and never into the
    conversation. Three fields carried a surface form, which is an
    original or a variant, and D1 5.1's premise (the caller supplied
    them) is false on this path.
    """

    def _sidecar(self, project, entries):
        (project / "pseudonyms.json").write_text(
            json.dumps(entries), encoding="utf-8")
        server.db.close()
        server.db = QualcoderDatabase(str(project))

    def test_the_case_variants_diagnostic_names_the_entry_not_the_name(
            self, project):
        con = sqlite3.connect(str(project / "data.qda"))
        con.execute("UPDATE source SET fulltext=? WHERE id=1",
                    ("THOMAS and thomas and Thomas spoke.",))
        con.commit()
        con.close()
        self._sidecar(project, [{"original": "Thomas", "pseudonym": "Alex"}])
        out = preview_of(mapping=None, use_project_pseudonyms=True)
        seen = out["preview"]["files"][0]["case_variants_seen"]
        assert seen and seen[0]["entry"] == 0
        assert seen[0]["other_case_count"] == 2
        assert "form" not in seen[0]
        assert_carries_no_name(json.dumps(out["preview"]), "preview")

    def test_the_overlap_diagnostic_names_the_entry_not_the_name(
            self, project):
        con = sqlite3.connect(str(project / "data.qda"))
        con.execute("UPDATE source SET fulltext=? WHERE id=1",
                    ("Ann Marie Curie spoke at length.",))
        con.commit()
        con.close()
        self._sidecar(project, [{"original": "Ann Marie", "pseudonym": "Pat"},
                                {"original": "Marie Curie",
                                 "pseudonym": "Robin"}])
        out = preview_of(mapping=None, use_project_pseudonyms=True)
        conflicts = out["preview"]["files"][0]["overlap_conflicts"]
        assert conflicts and conflicts[0]["entry"] == 1
        assert "form" not in conflicts[0]
        for forbidden in ("Ann Marie", "Marie Curie", "Marie"):
            assert forbidden not in json.dumps(out["preview"]), forbidden

    def test_a_chaining_sidecar_is_refused_without_quoting_the_name(
            self, project):
        """QualCoder's own dialog accepts this shape: it checks for
        duplicate originals and duplicate pseudonyms only
        (`pseudonyms.py:84-89` at the pin). The pseudonym it refuses is
        also somebody's real name, which is why it is refused."""
        self._sidecar(project, [{"original": "Ann Marie", "pseudonym": "Alex"},
                                {"original": "Thomas",
                                 "pseudonym": "Ann Marie"}])
        out = call(mapping=None, use_project_pseudonyms=True)
        assert "the pseudonym of entry 1 is also an original" in out["error"]
        assert_carries_no_name(out["error"], "chaining refusal",
                               ("Ann Marie",))

    def test_a_typed_mapping_still_shows_the_caller_their_own_names(
            self, project):
        """The caller supplied these, so withholding them would only
        make the diagnostic unreadable."""
        con = sqlite3.connect(str(project / "data.qda"))
        con.execute("UPDATE source SET fulltext=? WHERE id=1",
                    ("Ann Marie Curie spoke at length.",))
        con.commit()
        con.close()
        server.db.close()
        server.db = QualcoderDatabase(str(project))
        out = preview_of(mapping=[{"original": "Ann Marie",
                                   "pseudonym": "Pat"},
                                  {"original": "Marie Curie",
                                   "pseudonym": "Robin"}])
        conflicts = out["preview"]["files"][0]["overlap_conflicts"]
        assert conflicts[0]["form"] == "Marie Curie"

    def test_the_context_windows_are_withheld_on_the_sidecar_path(
            self, project):
        """Re-verification R-2, route 1. A context window is a slice of
        the file text around a match, so it contains the name that
        matched by construction. On the sidecar path that is the
        reverse key going into the conversation one window at a time,
        and the model opts into it, not the researcher.
        """
        self._sidecar(project, [{"original": "Thomas", "pseudonym": "Alex"},
                                {"original": "Mary Ann", "pseudonym": "Sam"}])
        out = preview_of(mapping=None, use_project_pseudonyms=True,
                         include_context=True, context_chars=60)
        blocks = out["preview"]["files"][0]["replacements"]
        assert blocks, "nothing matched, so this test proves nothing"
        for block in blocks:
            assert "context" not in block, block
            assert "context_truncated" not in block, block
        assert "include_context was asked for and is not returned" in \
            out["preview"]["context_withheld"]
        assert_carries_no_name(json.dumps(out["preview"]), "preview")

    def test_a_typed_mapping_still_gets_the_context_it_asked_for(
            self, project):
        """The other direction, so the suppression did not become a
        removal: the caller who typed the names is shown the text."""
        out = preview_of(include_context=True, context_chars=20)
        blocks = out["preview"]["files"][0]["replacements"]
        assert any(block.get("context") for block in blocks)
        assert "context_withheld" not in out["preview"]
        assert any("Thomas" in window for block in blocks
                   for window in block.get("context", []))

    def test_the_file_names_this_path_does_return_are_declared(
            self, project):
        """Re-verification R-2, route 2, kept open deliberately.

        A preview whose files have no names cannot be relayed, so file
        names stay. What changed is the promise beside them: the
        description now says the project path and each file's name are
        returned as they stand and can themselves carry one of these
        names, instead of promising that nothing here ever does. Both
        halves are pinned, this one and the sentence
        (`TestTheDescriptionCarriesWhatD1Requires`), so the absolute
        promise cannot come back without one of them failing.
        """
        rename_file(project, "Thomas_interview.txt")
        self._sidecar(project, [{"original": "Thomas", "pseudonym": "Alex"}])
        out = preview_of(mapping=None, use_project_pseudonyms=True)
        assert out["preview"]["files"][0]["name"] == "Thomas_interview.txt"
        assert out["preview"]["project"] == str(project / "data.qda")
        assert out["preview"]["residue"]["file_names"] == 1

    def test_the_two_mapping_sources_still_bind_the_same_token(
            self, project):
        """The suppression is presentation only: the signed effect is
        not narrowed with it, so a token issued from the sidecar
        executes from an identical typed mapping and the other way
        round."""
        self._sidecar(project, [{"original": "Thomas", "pseudonym": "Alex"}])
        out = preview_of(mapping=None, use_project_pseudonyms=True)
        result = call(mapping=[{"original": "Thomas", "pseudonym": "Alex"}],
                      preview_token=out["preview_token"])
        assert result.get("success") is True, result

    def test_a_sidecar_token_executes_from_a_typed_mapping_in_any_order(
            self, project):
        """Fix round 3, S3: the claim above held only when the typed
        order equalled the sidecar's."""
        self._sidecar(project, [{"original": "Mary Ann", "pseudonym": "Sam"},
                                {"original": "Thomas", "pseudonym": "Alex"}])
        out = preview_of(mapping=None, use_project_pseudonyms=True)
        result = call(mapping=[{"original": "Thomas", "pseudonym": "Alex"},
                               {"original": "Mary Ann", "pseudonym": "Sam"}],
                      preview_token=out["preview_token"])
        assert result.get("success") is True, result

    # The sidecar's order against the typed order (fix round 4, B1, and
    # fix round 5, B1): the first case keeps "Mary Ann" at index 1 on
    # both sides, which is its canonical position, and so could not see
    # the `loses_to_entry` half of the fix; the other two move it, on
    # the typed side and on the sidecar's.
    SIDECAR_ORDERS = [
        ("typed keeps Mary Ann at 1", ["Ann", "Mary Ann", "Thomas"],
         ["Thomas", "Mary Ann", "Ann"]),
        ("typed moves Mary Ann to 2", ["Ann", "Mary Ann", "Thomas"],
         ["Thomas", "Ann", "Mary Ann"]),
        ("sidecar moves Mary Ann to 0", ["Mary Ann", "Ann", "Thomas"],
         ["Thomas", "Mary Ann", "Ann"]),
    ]

    @pytest.mark.parametrize("label,sidecar,typed", SIDECAR_ORDERS,
                             ids=[label for label, *_ in SIDECAR_ORDERS])
    def test_a_sidecar_token_with_an_overlap_conflict_executes_in_any_order(
            self, project, label, sidecar, typed):
        """Fix round 4, B1, on this path: the conflict between "Ann" and
        "Mary Ann" is signed by canonical position here too, so the
        sidecar's order and the typed order share one effect."""
        pseudonyms = {"Ann": "Beth", "Mary Ann": "Sam", "Thomas": "Alex"}
        self._sidecar(project, [{"original": name,
                                 "pseudonym": pseudonyms[name]}
                                for name in sidecar])
        out = preview_of(mapping=None, use_project_pseudonyms=True)
        conflicts = out["preview"]["files"][0]["overlap_conflicts"]
        assert [(c["entry"], c["loses_to_entry"]) for c in conflicts] == \
            [(sidecar.index("Ann"), sidecar.index("Mary Ann"))]
        result = call(mapping=[{"original": name,
                                "pseudonym": pseudonyms[name]}
                               for name in typed],
                      preview_token=out["preview_token"])
        assert result.get("success") is True, result

    # A dictionary of first names, the judge's own, with the real one in
    # it. Nobody without the secret can tell which.
    DICTIONARY = ["Alice", "Bob", "Carol", "Dave", "Eve", "Frank", "Grace",
                  "Heidi", "Ivan", "Judy", "Mallory", "Niaj", "Olivia",
                  "Peggy", "Rupert", "Sybil", "Trent", "Victor", "Walter",
                  "Thomas", "Mary Ann", "Tom"]

    def test_neither_the_bind_nor_the_manifest_confirms_a_guessed_name(
            self, project):
        """Fix round 3, S2: the final-verification judge's attack, kept.

        From the preview payload alone (the project path, the pseudonym,
        the execute_with arguments and the token's public bind) a plain
        sha256 over each candidate mapping matched the bind after twenty
        guesses; after the execute, the manifest's plain mapping digest
        confirmed the same guess. Both are keyed with the per-user secret
        now, so the attacker's recomputation, which is all the attacker
        has, matches nothing; and the verifier, which holds the secret,
        still recomputes the bind exactly.
        """
        self._sidecar(project, [{"original": "Thomas", "pseudonym": "Alex"}])
        out = preview_of(mapping=None, use_project_pseudonyms=True)
        assert_carries_no_name(json.dumps(out), "preview")
        bind = out["preview_token"].split(".")[2]
        ew = out["execute_with"]["arguments"]
        pseudonym = out["preview"]["files"][0]["replacements"][0]["pseudonym"]
        project_id = server._token_project()

        def plain_bind(candidate):
            mapping = P.canonical_mapping(P.validate_mapping(
                [{"original": candidate, "pseudonym": pseudonym}]))
            args = pt.canonical_args(
                "pseudonymise_source", mapping=mapping,
                file_ids=ew.get("file_ids"), case_mode=ew["case_mode"],
                overlap_policy=ew["overlap_policy"])
            return pt.hashlib.sha256(pt.canonical(pt._binding(
                "pseudonymise_source", args, project_id)).encode("utf-8")
            ).hexdigest()[:8]

        recovered = [name for name in self.DICTIONARY
                     if plain_bind(name) == bind]
        assert recovered == [], recovered
        # The secret holder's recomputation is exact, so the bind is
        # keyed rather than merely different.
        true_mapping = P.canonical_mapping(P.validate_mapping(
            [{"original": "Thomas", "pseudonym": "Alex"}]))
        true_args = pt.canonical_args(
            "pseudonymise_source", mapping=true_mapping,
            file_ids=ew.get("file_ids"), case_mode=ew["case_mode"],
            overlap_policy=ew["overlap_policy"])
        assert pt.bind_id("pseudonymise_source", true_args, project_id,
                          pt.load_secret()) == bind

        result = execute_as_recipe(out)
        assert result.get("success") is True, result
        manifest = json.loads(
            Path(result["manifest_path"]).read_text(encoding="utf-8"))
        assert "mapping_sha256" not in manifest
        digest = manifest["mapping_hmac_sha256"]
        assert re.fullmatch(r"[0-9a-f]{64}", digest)
        assert manifest["token_bind"] == bind

        def plain_digest(candidate):
            mapping = P.canonical_mapping(P.validate_mapping(
                [{"original": candidate, "pseudonym": pseudonym}]))
            return pt.hashlib.sha256(json.dumps(
                mapping, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False).encode("utf-8")).hexdigest()

        assert [name for name in self.DICTIONARY
                if plain_digest(name) == digest] == []
        # And the keyed digest is the one a reversal on this account
        # would recompute.
        keyed = pt.hmac.new(pt.load_secret().encode("ascii"), json.dumps(
            true_mapping, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False).encode("utf-8"),
            pt.hashlib.sha256).hexdigest()
        assert digest == keyed


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

    def test_the_chaining_refusal_here_quotes_no_name_from_the_sidecar(
            self, project):
        """Re-verification N9. The rider reads the researcher's own
        `pseudonyms.json`, so its refusals are under the same rule as
        the flagship's, and the half that enforces it
        (`may_echo_names=False`) was unpinned: the test above asserts
        only that the word "chain" appears, which is true either way.

        Distinct values, so the assertion cannot pass by accident: the
        refusal names entry 0 and quotes nothing.
        """
        (project / "pseudonyms.json").write_text(
            json.dumps([{"original": "Zenobia", "pseudonym": "Quillon"},
                        {"original": "Quillon", "pseudonym": "Rowan"}]),
            encoding="utf-8")
        out = json.loads(server.import_text_file(
            filename="new.txt", content="Zenobia and Quillon.",
            create_backup=False, apply_project_pseudonyms=True))
        assert "mapping entry 0: the pseudonym of entry 0 is also an " \
               "original or variant" in out["error"]
        for forbidden in ("Zenobia", "Quillon", "Rowan"):
            assert forbidden not in out["error"], forbidden
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
        assert ("Positions after the first replacement in these files have "
                "changed" in joined)
        assert "Positions in these files have changed" not in joined
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
        literals and this module's own top-level constants. "Escaped
        parts" includes a call to one of this module's own pattern
        builders, admitted by the same rule applied to its returns
        rather than by name, because the detector escapes the pieces of
        a multi-word name after splitting it and the escaping is then
        no longer visible where the pattern is compiled.

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
        # Module-level functions that BUILD a pattern fragment, admitted
        # by the same rule rather than by name: every one of their
        # returns has to come through `re.escape` itself. The detector's
        # per-form alternative is one, because it splits a multi-word
        # name before escaping its parts, and the escaping is then no
        # longer visible at the `re.compile` that consumes it.
        def _is_safe_call(sub, builders):
            if not isinstance(sub, ast.Call):
                return False
            if isinstance(sub.func, ast.Attribute):
                return sub.func.attr == "escape"
            return isinstance(sub.func, ast.Name) and sub.func.id in builders

        def _escapes_every_return(func_node, builders):
            returns = [node for node in ast.walk(func_node)
                       if isinstance(node, ast.Return)
                       and node.value is not None]
            return bool(returns) and all(
                any(_is_safe_call(sub, builders)
                    for sub in ast.walk(node.value))
                for node in returns)

        functions = [node for node in tree.body
                     if isinstance(node, ast.FunctionDef)]
        builders = set()
        while True:                           # least fixed point
            grown = {node.name for node in functions
                     if _escapes_every_return(node, builders)}
            if grown <= builders:
                break
            builders |= grown
        assert "_detector_alternative" in builders, (
            "the detector's per-form builder must escape on every return")
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
            if any(_is_safe_call(sub, builders) for sub in ast.walk(pattern)):
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


# =============================================================================
# THE DESCRIPTION THE MODEL READS BEFORE IT DECIDES ANYTHING
# =============================================================================

class TestTheDescriptionCarriesWhatD1Requires:
    """Re-verification R-3. The whole description was pinned by ONE
    character count.

    `test_the_full_toolset_measures_what_the_documents_say` compares the
    serialised `tools/list` payload's LENGTH against a published figure,
    and only on Python 3.13 (`_reference_environment()`); on 3.10 to
    3.12 even a length change passes. So replacing D1 3.12's required
    sentence with the same number of characters of `xxx xxx` left the
    whole suite green, and three of fix round 1's own deliverables live
    in this string: F-6's re-read sentence, D-15's wider-reading
    sentence, and what the sidecar path does and does not return.

    The refusal texts in this feature are pinned to the character. This
    is the string a model reads BEFORE deciding what to do, and it was
    pinned to nothing. Each required point is now its own assertion, on
    the description as the tool manager publishes it rather than on the
    Python attribute, so deleting any one of them is red on every
    interpreter.

    Matching is done on the description with its line wrapping
    flattened, because every one of these sentences straddles a line
    break and a re-wrap must not be a failure.
    """

    # (label, the sentence D1 3.12 or a fix round requires)
    REQUIRED = [
        ("rewrites_source_text",
         "THIS REWRITES SOURCE TEXT and moves every coding, annotation "
         "and case link in the files it touches."),
        ("preview_first",
         "Preview first, relay the counts, the collisions and the residue "
         "to the user, get an explicit yes, then execute with the token."),
        ("deterministic",
         "ONLY the names in the mapping are replaced, as whole words, "
         "case-sensitively unless case_mode says otherwise. There is no "
         "name detection and no guessing"),
        ("possessives_keep_their_suffix",
         "\"Tom\" DOES match inside \"Tom's\" (the apostrophe is not a "
         "word character, so possessives keep their suffix)"),
        ("variants_are_needed",
         "Nicknames, inflections and spelling variants each need their own "
         "entry or a `variants` list."),
        ("what_is_not_rewritten",
         "What this does NOT rewrite, and where the names will remain: "
         "memos, journal entries, case names, file names, attribute "
         "values, PDFs, media files, QualCoder 4.0's ai_data folder, "
         "speakers.json and speaker_regex.json."),
        ("residue_says_where_names_remain",
         "The preview's `residue` block counts where the names still occur "
         "so you can tell the user"),
        ("residue_reads_wider_than_the_rewrite",         # D-15
         "Its counts use a WIDER reading than the rewrite: any occurrence "
         "a human would see, including inside a longer word and in any "
         "case, so a case named Thomas_P01 is counted even under "
         "case_mode=\"exact\"."),
        ("still_personal_data",
         "Pseudonymised data is still personal data and is often "
         "re-identifiable from context. This reduces risk; it does not "
         "anonymise (PRIVACY.md)."),
        ("the_backup_is_the_reverse_key",
         "The backup keeps the real names, and so does pseudonyms.json if "
         "the researcher keeps one; both are the reverse key and belong "
         "somewhere secure."),
        ("the_manifest_and_journal_hold_no_original",
         "The run manifest this tool writes and the journal entry it can "
         "add never contain an original name"),
        ("re_read_every_touched_file",                   # QA F-6
         "After the run, re-read every touched file before any further "
         "coding: every position after the first replacement in it has "
         "changed, and any pending coding suggestion for them is stale."),
        ("qualcoder_does_not_refresh",
         "an open QualCoder window does not refresh from this write on its "
         "own"),
        ("the_40_search_index_is_stale",
         "QualCoder 4.0's AI search index keeps the previous text until it "
         "re-indexes on the next open with AI enabled."),
        ("the_sidecar_path_quotes_nothing",              # B4 / S3
         "The original names in that file are the researcher's reverse "
         "key and you did not supply them, so on this path no diagnostic "
         "and no refusal quotes one, and include_context returns no "
         "context at all rather than the text around each match."),
        ("parity_is_the_walk_not_the_editor",            # fix round 3, S5
         "the walk QualCoder's own coding-view editor applies, fed this "
         "tool's exact edit list, which DELETES a coding that sits on a "
         "name and trims one that merely touches it. The editor itself "
         "diffs the two texts first, and its diff library may factor a "
         "shared prefix or suffix out of a replacement (Tom to Tim) and "
         "keep a coding this policy deletes."),
        ("the_ask_comes_in_the_preview",                 # fix round 3, S4
         "If the project has no AI coder name yet, the preview says so and "
         "carries the ask in execute_with.before_executing; set one with "
         "set_project_ai_coder_name before executing, or execute with "
         "record_in_journal=false."),
        ("the_sidecar_path_still_returns_names_it_has",  # R-2
         "the project path and each file's own name, either of which can "
         "itself contain one of those names."),
        ("the_override_rule_in_plain_words",             # rulings 7.3(3), 7.4
         "a coding that covered a name, or contained one, and now covers "
         "or contains its pseudonym needs no override, whatever the two "
         "lengths, and neither does a pure position shift, because "
         "neither changes a coding decision; a coding that grew to "
         "swallow a pseudonym, one that would be deleted, or one that had "
         "to be clamped because its stored end lay past the end of the "
         "text, does."),
    ]

    @staticmethod
    def published():
        """The description as the tool manager publishes it.

        `list_tools` serialises this same string, so a pin here is a pin
        on the payload rather than on a Python attribute that could stop
        being the payload.
        """
        tool = server.mcp._tool_manager._tools["pseudonymise_source"]
        return " ".join((tool.description or "").split())

    @pytest.mark.parametrize("label,sentence",
                             REQUIRED,
                             ids=[item[0] for item in REQUIRED])
    def test_the_point_is_made(self, label, sentence):
        assert " ".join(sentence.split()) in self.published(), label

    def test_the_published_description_is_the_docstring(self):
        """So the pins above cannot be satisfied by a docstring the
        payload does not carry."""
        tool = server.mcp._tool_manager._tools["pseudonymise_source"]
        assert tool.description == server.pseudonymise_source.__doc__

    def test_each_required_sentence_occurs_exactly_once(self):
        """A fragment that matches in two places is a fragment that can
        survive the deletion of the one that matters."""
        published = self.published()
        for label, sentence in self.REQUIRED:
            assert published.count(" ".join(sentence.split())) == 1, label

    def test_the_description_keeps_the_house_rules(self):
        _house_rules([server.pseudonymise_source.__doc__], ["description"])

    def test_the_length_rule_is_gone(self):
        """Rulings 7.3(3) and 7.4. The sentence that implied only a shift
        was exempt, so that "Thomas -> Alex" needed the override and
        "Thomas -> Alexis" did not, is no longer what a model reads; nor
        is the one that gated a coding for containing a name."""
        published = self.published()
        assert "A pure position shift of such a row does not require it" \
            not in published
        assert "whatever the two lengths" in published
        assert "one that contained a name and changed length with it" \
            not in published
        assert "resize, snap or delete" not in published

    def test_the_wide_parity_claim_is_gone(self):
        """Fix round 3, S5: "exactly what QualCoder's own text editor
        does" was false for Tom to Tim, Sara to Sana, Thomas to Thom and
        Ann to Anna, where the editor's diff keeps a coding this policy
        deletes (D-10's narrowing, now in the description too)."""
        assert "exactly what QualCoder's own" not in self.published()
        assert "all positions in them have changed" not in self.published()


# =============================================================================
# THE DOCUMENTS A RESEARCHER READS INSTEAD OF THE DESCRIPTION
# =============================================================================

class TestTheDocumentsTellTheTruth:
    """Fix round 3, B4 and S5, pinned the way the description's points
    are pinned: a sentence that claims more than the code does is a
    failing test, on every interpreter.

    README said PDFs, media and `ai_data/` were "scanned and counted";
    the residue block never reads any of them, and README is what a
    researcher reads to decide whether the PDF text is safe. README also
    said `qualcoder_edit_parity` "reproduces QualCoder's own text
    editor", which is false whenever the pseudonym shares a prefix or
    suffix with the name. PRIVACY.md carries the arrival-state promise
    and the detector's stated limit, both of which this round made true.
    """

    REPO = Path(__file__).resolve().parents[1]

    @classmethod
    def _flat(cls, name):
        text = (cls.REPO / name).read_text(encoding="utf-8")
        return " ".join(text.replace("\n>", " ").split())

    @pytest.mark.parametrize("sentence", [
        "PDFs, media files and `ai_data/` are out of scope and are neither "
        "rewritten nor scanned.",
        "Memos, journal entries, case, file, code, category and "
        "attribute-type names and attribute values are scanned and "
        "counted, never rewritten",
        "`qualcoder_edit_parity` reproduces the walk QualCoder's "
        "coding-view editor applies, fed this tool's exact edit list (the "
        "editor's own diff may factor a shared prefix or suffix out of a "
        "replacement and keep a coding this policy deletes)",
        "PRIVACY.md's \"Coder visibility\" section says what is and is not "
        "re-read",
    ])
    def test_readme_says_it(self, sentence):
        assert " ".join(sentence.split()) in self._flat("README.md")

    @pytest.mark.parametrize("claim", [
        "PDFs, media and `ai_data/` are scanned and counted",
        "PDFs and media are scanned and counted",
        "reproduces QualCoder's own text editor",
    ])
    def test_readme_no_longer_claims_it(self, claim):
        assert claim not in self._flat("README.md")

    @pytest.mark.parametrize("sentence", [
        # B3: the promise, scoped to what re-reads, and the residual.
        "Every decision that puts a coder's NAME into a result re-reads "
        "the declaration from the project at the time it is made",
        "What is NOT re-read is which table each READ goes to.",
        "if the declaration itself cannot be read, the answer is the same "
        "posture rather than \"nobody is hidden\"",
        # S6 and S7: what the residue reads, and what it cannot reach.
        "Memos (twelve fields, the audio/video and image coding memos "
        "included), journal entries and their names, case names, file "
        "names, code names, category names, attribute-type names and "
        "attribute values.",
        "a look-alike letter from another script (a Cyrillic \"о\" for a "
        "Latin \"o\") is a different letter to the comparison, and is out "
        "of scope",
        # S2: the keyed bind and manifest digest.
        "The manifest's `token_bind` and its `mapping_hmac_sha256` are "
        "both keyed with the per-user token secret rather than plain "
        "digests",
        # Fix round 4, L3, worded exactly in fix round 5: prune_backups
        # probes data.qda read-only through validate_qda_path and
        # constructs no fresh project connection, so it settles nothing.
        "and so does any write that opens the database, because such a "
        "write opens a fresh connection (`prune_backups` opens no fresh "
        "project connection and settles nothing)",
        # Fix round 4, R3: the symlink route.
        "On `pseudonymise_source`'s result the name of a skipped symlink "
        "is withheld where a reader of it would see a name from the "
        "mapping, the count kept; and the log line that reports a skipped "
        "symlink carries the count and the reason, never the path",
        # Rulings 7.3(3) and 7.4: the flagship's override rule, in
        # plain words.
        "a coding that covered a name, or contained one, and now covers "
        "or contains its pseudonym needs no override, whatever the two "
        "lengths, and neither does a pure position shift, because "
        "neither changes a coding decision; a coding that grew to "
        "swallow a pseudonym, one that would be deleted (which only the "
        "`qualcoder_edit_parity` policy does), or one that had to be "
        "clamped because its stored end lay past the end of the text "
        "requires the override.",
    ])
    def test_privacy_says_it(self, sentence):
        assert " ".join(sentence.split()) in self._flat("PRIVACY.md")

    def test_privacy_no_longer_makes_the_unqualified_promise(self):
        flat = self._flat("PRIVACY.md")
        assert "Every decision about who may be NAMED re-reads" not in flat
        assert ("compatibility-equivalent spelling of a name is not "
                "detected by it" not in flat)

    def test_the_changelog_says_the_override_rule_as_ruled(self):
        """Rulings 7.3(3) and 7.4. The entry stated the length rule by
        implication ("a pure position shift ... proceeds ... and a
        resize, a snap or a deletion requires"), which is what made
        `Thomas -> Alex` need an override `Thomas -> Alexis` did not;
        then it gated the resize, which records the same decision about
        the same words."""
        entry = self._flat("CHANGELOG.md").split("## [0.11")[0]
        assert ("a coding that covered a name, or contained one, and now "
                "covers or contains its pseudonym needs no override, "
                "whatever the two lengths, and neither does a pure position "
                "shift, because neither changes a coding decision; a coding "
                "that grew to swallow a pseudonym, one that would be deleted "
                "(which only `qualcoder_edit_parity` does), or one that had "
                "to be clamped because its stored end lay past the end of "
                "the text requires `allow_hidden_coder`.") in entry
        assert "`Thomas -> Alex` needed the override and `Thomas -> Alexis` " \
               "did not" in entry
        assert ("a pure position shift of a hidden coder's row proceeds, "
                "because it changes no coding decision, and a resize, a snap "
                "or a deletion requires") not in entry
        assert "one that contained a name and changed length with it, or " \
               "one that would be deleted" not in entry
        assert "a clamp is counted under its own class rather than under " \
               "one the exemption carries" in entry

    def test_the_changelog_counts_the_rounds(self):
        entry = self._flat("CHANGELOG.md").split("## [0.11")[0]
        assert "after the flagship and its five fix rounds" in entry
        assert "after the flagship and its four fix rounds" not in entry
        assert "after the flagship and its three fix rounds" not in entry
        assert "after the flagship and its fix round:" not in entry

    def test_the_readme_says_the_override_rule_as_ruled(self):
        """Ruling 7.4. README's entry for the tool lists the classes the
        preview reports and names the ones that gate the run; it is what
        a researcher reads instead of the description."""
        readme = self._flat("README.md")
        assert ("counts (`shifted`, `substituted`, `resized`, `snapped`, "
                "`deleted`, `clamped`), never names, and "
                "`allow_hidden_coder` is required when `snapped`, "
                "`deleted` or `clamped` is non-zero: a pure shift, a "
                "substitution and a resize change no coding decision, "
                "whatever the two lengths") in readme
        assert "when `snapped`, `resized` or `deleted` is non-zero" \
            not in readme

    def test_the_changelog_records_the_acceptance_checks_as_planned(self):
        """The owner's second ruling of 2026-09-16: the six in-QualCoder
        checks (D1 6.5) are recorded as not run in this release and
        planned for 0.12.1, rather than merely not run."""
        entry = self._flat("CHANGELOG.md").split("## [0.11")[0]
        assert ("are recorded as not run in this release and planned for "
                "0.12.1") in entry
        assert "and were NOT run before this release" not in entry


# =============================================================================
# THE CAPABILITY CACHE, WHICH IS OLDER THAN THIS FEATURE
# =============================================================================

class TestTheVisibilityDeclarationIsRereadPerCall:
    """Re-verification, declined item (e), proven reachable.

    Schema capabilities are probed once, when the connection opens. That
    is right for the rest of them: they say what this server CAN do, and
    a stale answer costs a feature. It was wrong for the one that says
    who may be NAMED. QualCoder creates `coder_names.visibility` and its
    four views on every project open (app.py:1448-1569), under exactly
    the long-lived read connection this server holds, so a server that
    connected first answered from a project state that no longer
    existed: the preview named `Hidden Helga` in `by_owner` AND reported
    `override_required: false`. The execute then refused with
    `project_changed`, so nothing was written, but the disclosure had
    already happened, and "never name a hidden coder" is an owner-ruled
    invariant (X1).

    The re-read is one way, and the second test here is why: a
    declaration that disappears under a live connection is damage,
    drift, or a concurrent QualCoder rebuild, and the answer to those is
    "who is hidden cannot be decided", never "this project hides
    nobody". A two-way re-read was tried and measured: it turns eight
    fail-closed tests red in `test_qc40_visibility.py` and
    `test_v012_compare_coders.py`, which is fix round 5's own defect,
    where removing MORE of the visibility schema bought MORE access.
    """

    def _connected_before_qualcoder(self, tmp_path):
        """A project with no visibility declaration when we connect."""
        folder = build_project(tmp_path / "late.qda")
        add_coding(folder, 1, 1, 0, 6)
        # "ary Ann agreed" cuts into "Mary Ann": a snap, so the override
        # is at stake. The name alone, (34, 42), is a substitution since
        # ruling 7.3(3), and a row that merely contains it is a resize,
        # exempt since ruling 7.4; neither would need an override
        # however the declaration read.
        add_coding(folder, 2, 1, 35, 49, owner="Hidden Helga")
        write_fixture_sidecar(str(folder))
        server.db.close()
        server.db = QualcoderDatabase(str(folder))
        server.current_project_path = str(folder)
        assert server.db.capabilities.visibility_declared() is False
        return folder

    def test_a_declaration_that_arrives_after_the_connection_is_seen(
            self, project, tmp_path):
        folder = self._connected_before_qualcoder(tmp_path)
        # QualCoder opens the project: the column and the four views, in
        # the one routine that makes them.
        hide_coder(folder, "Hidden Helga")

        out = preview_of()
        preview = out["preview"]
        assert "Hidden Helga" not in json.dumps(preview)
        assert [entry["owner"] for entry in
                preview["files"][0]["codings"]["by_owner"]] == ["TestCoder"]
        assert preview["hidden_coder_rows"]["override_required"] is True
        assert server.db.coder_visibility_map() == {"TestCoder": 1,
                                                    "Hidden Helga": 0}
        # And the execute is gated on it rather than merely reported.
        # The preview's own recipe already carries
        # allow_hidden_coder=true, which is what it is for, so the
        # refusal is driven by withholding it.
        assert out["execute_with"]["arguments"]["allow_hidden_coder"] is True
        refused = execute_from(out, allow_hidden_coder=False)
        assert refused["reason"] == "hidden_coder_override_required"
        assert_carries_no_name(json.dumps(refused), "refusal",
                               ("Hidden Helga",))

    def test_a_declaration_that_disappears_is_not_a_project_without_one(
            self, project, tmp_path):
        """The one-way half. `coder_names` going away mid-connection is
        damage, not a legitimate state change: QualCoder's migration is
        additive and never withdraws the column."""
        from qualcoder_mcp.database import CoderVisibilityUnreadable
        folder = build_project(tmp_path / "early.qda")
        add_coding(folder, 1, 1, 0, 6)
        write_fixture_sidecar(str(folder))
        hide_coder(folder, "Hidden Helga")
        server.db.close()
        server.db = QualcoderDatabase(str(folder))
        server.current_project_path = str(folder)
        assert server.db.capabilities.visibility_declared() is True

        con = sqlite3.connect(str(folder / "data.qda"))
        con.execute("DROP TABLE coder_names")
        con.commit()
        con.close()
        with pytest.raises(CoderVisibilityUnreadable):
            server.db.coder_visibility_map()

    def test_a_project_that_never_declares_it_still_answers_none(
            self, project, tmp_path):
        """The control: the re-read must not invent a capability."""
        self._connected_before_qualcoder(tmp_path)
        assert server.db.coder_visibility_map() is None
        preview = preview_of()["preview"]
        assert preview["hidden_coder_rows"]["override_required"] is False
        assert {entry["owner"] for entry in
                preview["files"][0]["codings"]["by_owner"]} == {
                    "TestCoder", "Hidden Helga"}

    class _PragmaFaults:
        """A connection on which only the declaration re-read fails.

        Everything else reaches the real connection, so a refusal here
        is the re-read's own decision and not the whole tool falling
        over (the judge's reproduction, S1).
        """

        def __init__(self, real):
            object.__setattr__(self, "_real", real)

        def execute(self, sql, *args, **kwargs):
            if "PRAGMA table_info(coder_names)" in sql:
                raise sqlite3.OperationalError("database is locked")
            return self._real.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._real, name)

        def __setattr__(self, name, value):
            setattr(self._real, name, value)

    def test_a_re_read_that_cannot_answer_fails_closed(
            self, project, tmp_path):
        """Fix round 3, S1: the last fail-open branch in the visibility
        design. The re-read returned False when its PRAGMA raised, and
        False is "no capability", on which the table is never read at
        all; so a lock landing on that one statement, on a project that
        gained the capability after connect, named the hidden coder and
        reported that no override was needed. Now it is "cannot be
        decided", which every caller already handles.
        """
        from qualcoder_mcp.database import CoderVisibilityUnreadable
        folder = self._connected_before_qualcoder(tmp_path)
        hide_coder(folder, "Hidden Helga")
        control = preview_of()
        assert "Hidden Helga" not in json.dumps(control)
        assert control["preview"]["hidden_coder_rows"]["override_required"] \
            is True
        real = server.db.conn
        server.db.conn = self._PragmaFaults(real)
        try:
            with pytest.raises(CoderVisibilityUnreadable) as caught:
                server.db.coder_visibility_map()
            assert "declaration could not be read" in str(caught.value)
            out = preview_of()
            assert "preview" not in out
            assert "preview_token" not in out
            assert "could not be read" in out["error"]
            assert "Hidden Helga" not in json.dumps(out)
            assert backups(folder) == []
        finally:
            server.db.conn = real
        # And the fault is the only thing in the way.
        assert preview_of()["preview"]["hidden_coder_rows"][
            "override_required"] is True

    def test_a_declaration_present_at_connect_never_reaches_the_re_read(
            self, project, tmp_path):
        """The one-way rule's other face: with the capability present
        when the connection opened, the PRAGMA is not consulted, so the
        same fault changes nothing."""
        folder = build_project(tmp_path / "early.qda")
        add_coding(folder, 1, 1, 0, 6)
        # "ary Ann agreed" cuts into "Mary Ann": a snap, so the override
        # is at stake. The name alone, (34, 42), is a substitution since
        # ruling 7.3(3), and a row that merely contains it is a resize,
        # exempt since ruling 7.4; neither would need an override
        # however the declaration read.
        add_coding(folder, 2, 1, 35, 49, owner="Hidden Helga")
        write_fixture_sidecar(str(folder))
        hide_coder(folder, "Hidden Helga")
        server.db.close()
        server.db = QualcoderDatabase(str(folder))
        server.current_project_path = str(folder)
        assert server.db.capabilities.visibility_declared() is True
        real = server.db.conn
        server.db.conn = self._PragmaFaults(real)
        try:
            out = preview_of()
            assert out["preview"]["hidden_coder_rows"]["override_required"] \
                is True
            assert "Hidden Helga" not in json.dumps(out)
        finally:
            server.db.conn = real


class TestTheOlderTokenGatedToolsRereadTheDeclarationToo:
    """Fix round 3, B3. PRIVACY.md's fix-round-2 paragraph promised that
    every decision about who may be NAMED re-reads the declaration; the
    flagship's did, and the six older token-gated tools' did not. On a
    project that gained the capability after this server connected,
    `delete_code` and `merge_codes` named the hidden coder in
    `collateral.by_owner` (and `merge_codes` in `discarded_by_owner`),
    `delete_category` and `merge_category` named her as the row owner,
    and `compare_coders` compared her by name. The same one-way re-read
    the flagship uses now feeds `_naming_source`, the anonymous hidden
    count and the comparison's eligibility, so the promise is true of
    every naming decision rather than of one tool; what is still not
    re-read is which TABLE the read tools go to, and PRIVACY.md says so.
    """

    def _arrived(self, tmp_path, category_owner="Hidden Helga"):
        """Connected before the capability existed, then QualCoder hid
        a coder: the column and the four views, as its routine makes
        them, under this server's live connection."""
        folder = build_project(tmp_path / "arrive.qda")
        add_coding(folder, 1, 1, 0, 6)                            # TestCoder
        add_coding(folder, 2, 2, 34, 42)                          # TestCoder
        add_coding(folder, 5, 1, 19, 22, owner="Hidden Helga")
        add_coding(folder, 6, 2, 63, 68, owner="Hidden Helga")
        # A duplicate span under both codes by the hidden coder, so a
        # merge of 1 into 2 discards one of hers.
        add_coding(folder, 7, 1, 34, 42, owner="Hidden Helga")
        add_coding(folder, 8, 2, 34, 42, owner="Hidden Helga")
        con = sqlite3.connect(str(folder / "data.qda"))
        con.execute("INSERT INTO code_cat (catid,name,owner,date,memo) "
                    "VALUES (1,'Themes',?,'d','')", (category_owner,))
        con.execute("INSERT INTO code_cat (catid,name,owner,date,memo) "
                    "VALUES (2,'Other','TestCoder','d','')")
        con.commit()
        con.close()
        write_fixture_sidecar(str(folder))
        server.db.close()
        server.db = QualcoderDatabase(str(folder))
        server.current_project_path = str(folder)
        assert server.db.capabilities.visibility_declared() is False
        hide_coder(folder, "Hidden Helga")
        assert server.db.capabilities.visibility_declared() is False
        assert server.db.code_text_source() == "code_text"     # the residual
        return folder

    def test_delete_code_counts_her_and_gates_the_execute(
            self, project, tmp_path):
        folder = self._arrived(tmp_path)
        raw = server.delete_code(code_id=1)
        out = json.loads(raw)
        assert "Hidden Helga" not in raw
        block = out["preview"]["collateral"]
        assert [e["owner"] for e in block["by_owner"]] == ["TestCoder"]
        assert block["hidden_coder_codings"] == 2                # ctid 5, 7
        assert out["preview"]["hidden_coder_codings_affected"] == 2
        assert out["execute_with"]["arguments"]["allow_hidden_coder"] is True
        assert any("hidden in QualCoder" in w for w in out["warnings"])
        refused = json.loads(server.delete_code(
            code_id=1, preview_token=out["preview_token"]))
        assert refused["reason"] == "hidden_coder_override_required"
        assert "Hidden Helga" not in json.dumps(refused)
        assert backups(folder) == []

    def test_merge_codes_names_her_in_neither_owner_list(
            self, project, tmp_path):
        self._arrived(tmp_path)
        raw = server.merge_codes(from_code_id=1, into_code_id=2)
        out = json.loads(raw)
        assert "Hidden Helga" not in raw
        block = out["preview"]["collateral"]
        assert [e["owner"] for e in block["by_owner"]] == ["TestCoder"]
        assert block["discarded_by_owner"] == []                # hers only
        assert block["hidden_coder_codings"] == 2
        assert out["preview"]["hidden_coder_codings_affected"] == 2
        assert out["execute_with"]["arguments"]["allow_hidden_coder"] is True

    @pytest.mark.parametrize("tool", ["delete_category", "merge_category"])
    def test_the_category_previews_mask_her_as_the_row_owner(
            self, project, tmp_path, tool):
        self._arrived(tmp_path)
        call_tool = getattr(server, tool)
        kwargs = ({"category_id": 1} if tool == "delete_category"
                  else {"from_category_id": 1, "into_category": "Other"})
        raw = call_tool(**kwargs)
        out = json.loads(raw)
        assert "Hidden Helga" not in raw
        assert out["preview"]["collateral"]["category_row_owner"] == \
            "(hidden coder)"
        assert out["preview"]["hidden_coder_codings_affected"] == 0
        assert out["preview"]["collateral"]["hidden_coder_codings"] == 0

    def test_compare_coders_refuses_her_and_counts_her(
            self, project, tmp_path):
        self._arrived(tmp_path)
        raw = server.compare_coders(coder_a="TestCoder",
                                    coder_b="Hidden Helga")
        out = json.loads(raw)
        assert out["error"] == server.HIDDEN_COMPARISON_REFUSAL
        assert "Hidden Helga" not in raw
        # Auto-selection: one eligible coder, and one more hidden, said
        # as a count; the same count everywhere in one result.
        auto = json.loads(server.compare_coders())
        assert "1 more coder hidden in QualCoder" in auto["error"]
        assert "Hidden Helga" not in auto["error"]
        allowed = json.loads(server.compare_coders(
            coder_a="TestCoder", coder_b="Hidden Helga",
            allow_hidden_coder=True))
        assert allowed["coder_visibility"]["hidden_coder_filter"] == \
            "bypassed"
        assert allowed["coder_visibility"]["hidden_coders"] == 1

    def test_the_two_file_level_tools_have_nobody_to_name(
            self, project, tmp_path):
        """Controls for the six: restore_backup and prune_backups name
        no coder on any project; pinned so the count of six is a count
        of six and not of four."""
        from qualcoder_mcp.database import backup_project
        folder = self._arrived(tmp_path)
        backup = backup_project(folder)
        backup_project(folder)                  # two, so one can be pruned
        for raw in (server.restore_backup(backup_path=str(backup)),
                    server.prune_backups(keep_last=1)):
            out = json.loads(raw)
            assert out.get("requires_confirmation") is True, out
            assert "Hidden Helga" not in raw

    def test_a_declaration_that_arrives_without_its_views_fails_closed(
            self, project, tmp_path):
        """The re-read is of two facts, not one: the column says the
        project can hide a coder, and the view is what filters as
        QualCoder filters. A column with no view is a project the views
        were taken out of, and the answer is the refusal, not a
        `by_owner` from the base table."""
        folder = build_project(tmp_path / "column.qda")
        add_coding(folder, 1, 1, 0, 6)
        add_coding(folder, 5, 1, 19, 22, owner="Hidden Helga")
        write_fixture_sidecar(str(folder))
        server.db.close()
        server.db = QualcoderDatabase(str(folder))
        server.current_project_path = str(folder)
        con = sqlite3.connect(str(folder / "data.qda"))
        con.execute(VISIBILITY_COLUMN)
        con.execute("INSERT OR REPLACE INTO coder_names (name, visibility) "
                    "VALUES ('Hidden Helga', 0)")
        con.commit()
        con.close()
        for raw in (server.delete_code(code_id=1),
                    server.merge_codes(from_code_id=1, into_code_id=2)):
            out = json.loads(raw)
            assert "preview" not in out
            assert "views is missing" in out["error"]
            assert "Hidden Helga" not in raw

    def test_a_project_that_never_declares_still_names_everyone(
            self, project, tmp_path):
        """The control: on a project with no declaration nothing is
        hidden, and the re-read must not invent a capability."""
        folder = build_project(tmp_path / "never.qda")
        add_coding(folder, 1, 1, 0, 6)
        add_coding(folder, 5, 1, 19, 22, owner="Colleague")
        write_fixture_sidecar(str(folder))
        server.db.close()
        server.db = QualcoderDatabase(str(folder))
        server.current_project_path = str(folder)
        out = json.loads(server.delete_code(code_id=1))
        assert {e["owner"] for e in out["preview"]["collateral"]["by_owner"]
                } == {"TestCoder", "Colleague"}
        assert "hidden_coder_codings" not in out["preview"]["collateral"]
        assert "hidden_coder_codings_affected" not in out["preview"]
