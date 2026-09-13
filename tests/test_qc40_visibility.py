"""P1-3: QC 4.0 coder-visibility reads (verdict c).

Fixture mirrors what QualCoder 4.0 creates in the project database on
open (app.py:1499-1562 at pin 9bddf17): the coder_names table with a
visibility column plus the code_text_visible / code_image_visible /
code_av_visible / annotation_visible views. Capability is probed from
those objects, never from version strings; pre-4.0 projects read base
tables as before, and the view is never hard-required.
"""

import json
import re
import sqlite3
import zipfile
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server
from qualcoder_mcp.database import DB_LOCKED_MESSAGE, QualcoderDatabase

HIDDEN = "Hidden Coder"

# The exact view DDL QualCoder 4.0 executes (app.py:1518-1561)
_VIEW_DDL = [
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


def _apply_visibility_schema(project_path, hidden_coder=HIDDEN):
    con = sqlite3.connect(str(Path(project_path) / "data.qda"))
    cur = con.cursor()
    for ddl in _VIEW_DDL:
        cur.execute(ddl)
    cur.executemany(
        "INSERT OR REPLACE INTO coder_names (name, visibility) VALUES (?, ?)",
        [("TestCoder", 1), (hidden_coder, 0)])
    # A hidden text coding on the same span as ctid 1 (different owner,
    # so the unique constraint allows it) plus one on the Coping span
    cur.execute(
        "INSERT INTO code_text (ctid, cid, fid, seltext, pos0, pos1, owner, "
        "date, memo, important) VALUES (3, 1, 1, "
        "'I feel stressed about deadlines', 24, 55, ?, '2024-01-15', "
        "'hidden memo', 0)", (hidden_coder,))
    cur.execute(
        "INSERT INTO code_text (ctid, cid, fid, seltext, pos0, pos1, owner, "
        "date, memo, important) VALUES (4, 2, 1, "
        "'I cope by exercising', 57, 77, ?, '2024-01-15', '', 0)",
        (hidden_coder,))
    # A hidden cid-2 coding OVERLAPPING the hidden cid-1 span (24-55),
    # so the co-occurrence coder override has something to find that
    # the default visible read must not (QA round 1, F14)
    cur.execute(
        "INSERT INTO code_text (ctid, cid, fid, seltext, pos0, pos1, owner, "
        "date, memo, important) VALUES (5, 2, 1, "
        "'stressed', 30, 40, ?, '2024-01-15', '', 0)", (hidden_coder,))
    # A hidden annotation and a hidden image coding
    cur.execute(
        "INSERT INTO annotation (fid, pos0, pos1, memo, owner, date) "
        "VALUES (1, 0, 4, 'hidden annotation', ?, '2024-01-15')",
        (hidden_coder,))
    cur.execute(
        "INSERT INTO code_image (imid, id, x1, y1, width, height, cid, memo, "
        "date, owner, important) VALUES (1, 2, 0, 0, 10, 10, 1, '', "
        "'2024-01-15', ?, 0)", (hidden_coder,))
    con.commit()
    con.close()


def _reopen(project_path):
    if server.db is not None:
        try:
            server.db.close()
        except Exception:
            pass
    server.db = QualcoderDatabase(project_path)


@pytest.fixture
def visibility_db(setup_server, qualcoder_db_path):
    """A 4.0-style project with one hidden coder and hidden rows."""
    _apply_visibility_schema(qualcoder_db_path)
    _reopen(qualcoder_db_path)
    return qualcoder_db_path


# =============================================================================
# CAPABILITY PROBE (objects, never version strings)
# =============================================================================

class TestVisibilityProbe:

    def test_probe_true_with_column_and_view(self, visibility_db):
        caps = server.db.capabilities
        assert caps.has_coder_visibility is True
        assert caps.to_dict()["has_coder_visibility"] is True

    def test_probe_false_without_views(self, setup_server):
        # The stock fixture has coder_names WITH visibility but no views
        assert server.db.capabilities.has_coder_visibility is False
        assert server.db.hidden_coder_count() == 0

    def test_probe_false_with_view_but_no_visibility_column(self, tmp_path):
        # Partial state: someone created the views but coder_names lacks
        # the column (CREATE VIEW does not validate referenced columns)
        proj = tmp_path / "partial.qda"
        proj.mkdir()
        con = sqlite3.connect(str(proj / "data.qda"))
        cur = con.cursor()
        cur.execute("CREATE TABLE project (databaseversion TEXT, date TEXT, "
                    "memo TEXT, about TEXT, codername TEXT)")
        cur.execute("INSERT INTO project VALUES ('v14','d','','','C')")
        cur.execute("CREATE TABLE code_text (ctid INTEGER PRIMARY KEY, "
                    "cid INTEGER, fid INTEGER, seltext TEXT, pos0 INTEGER, "
                    "pos1 INTEGER, owner TEXT, date TEXT, memo TEXT, "
                    "important INTEGER)")
        cur.execute("CREATE TABLE code_name (cid INTEGER PRIMARY KEY, "
                    "name TEXT UNIQUE, memo TEXT, catid INTEGER, owner TEXT, "
                    "date TEXT, color TEXT)")
        cur.execute("CREATE TABLE code_cat (catid INTEGER PRIMARY KEY, "
                    "name TEXT UNIQUE, memo TEXT, owner TEXT, date TEXT, "
                    "supercatid INTEGER)")
        cur.execute("CREATE TABLE source (id INTEGER PRIMARY KEY, name TEXT, "
                    "fulltext TEXT, mediapath TEXT, memo TEXT, owner TEXT, "
                    "date TEXT)")
        cur.execute("CREATE TABLE cases (caseid INTEGER PRIMARY KEY, "
                    "name TEXT, memo TEXT, owner TEXT, date TEXT)")
        cur.execute("CREATE TABLE coder_names (name TEXT UNIQUE NOT NULL)")
        cur.execute(_VIEW_DDL[1])
        con.commit()
        con.close()
        db = QualcoderDatabase(str(proj))
        try:
            assert db.capabilities.has_coder_visibility is False
            assert db.hidden_coder_count() == 0
        finally:
            db.close()

    def test_probe_false_with_views_but_no_coder_names_table(
            self, visibility_db, qualcoder_db_path):
        # Tampering-robustness pin: QualCoder 4.0 cannot produce this
        # state itself (app.py:1470 creates coder_names before
        # app.py:1518-1561 creates the views, in one try block), but a
        # hand-dropped table must still probe False and every read must
        # fall back to the base tables cleanly
        con = sqlite3.connect(str(Path(qualcoder_db_path) / "data.qda"))
        con.execute("DROP TABLE coder_names")
        con.commit()
        con.close()
        _reopen(qualcoder_db_path)
        assert server.db.capabilities.has_coder_visibility is False
        assert server.db.hidden_coder_count() == 0
        out = json.loads(server.get_coded_segments(1))
        assert out["segment_count"] == 2
        assert "coder_visibility" not in out

    def test_schema_block_reports_capability(self, visibility_db):
        out = json.loads(server.get_current_project())
        assert out["schema"]["capabilities"]["has_coder_visibility"] is True


# =============================================================================
# DEFAULT READS GO THROUGH THE VISIBLE VIEWS
# =============================================================================

class TestVisibleReads:

    def test_get_coded_segments_excludes_hidden(self, visibility_db):
        out = json.loads(server.get_coded_segments(1))
        owners = {s["owner"] for s in out["segments"]}
        assert HIDDEN not in owners
        assert out["segment_count"] == 1
        note = out["coder_visibility"]
        assert note["hidden_coder_filter"] == "applied"
        assert note["hidden_coders"] == 1
        assert note["codings_suppressed"] == 1
        # count suppressed is fine; hidden owners' NAMES are not leaked
        assert HIDDEN not in json.dumps(out)

    def test_coder_override_reads_base_tables(self, visibility_db):
        out = json.loads(server.get_coded_segments(1, coder=HIDDEN))
        assert out["segment_count"] == 1
        assert out["segments"][0]["owner"] == HIDDEN
        assert out["coder_visibility"]["hidden_coder_filter"] == "bypassed"

    def test_search_coded_text_excludes_hidden(self, visibility_db):
        out = json.loads(server.search_coded_text("cope"))
        owners = {r["owner"] for r in out["results"]}
        assert HIDDEN not in owners
        assert out["result_count"] == 1

    def test_search_coded_text_coder_override(self, visibility_db):
        out = json.loads(server.search_coded_text("cope", coder=HIDDEN))
        assert out["result_count"] == 1
        assert out["results"][0]["owner"] == HIDDEN

    def test_frequencies_exclude_hidden(self, visibility_db):
        out = json.loads(server.get_coding_frequencies())
        by_name = {c["code_name"]: c["frequency"] for c in out["codes"]}
        assert by_name["Stress"] == 1
        assert by_name["Coping"] == 1
        assert out["total_coded_segments"] == 2
        assert out["coder_visibility"]["hidden_coder_filter"] == "applied"

    def test_frequencies_coder_override(self, visibility_db):
        out = json.loads(server.get_coding_frequencies(coder=HIDDEN))
        by_name = {c["code_name"]: c["frequency"] for c in out["codes"]}
        assert by_name["Stress"] == 1
        assert by_name["Coping"] == 2  # ctid 4 and the F14 row, ctid 5
        assert out["coder_visibility"]["hidden_coder_filter"] == "bypassed"

    def test_matrix_excludes_hidden(self, visibility_db):
        out = json.loads(server.get_case_code_matrix())
        # Case 1 spans 0-100; visible codings: ctid1 (cid1), ctid2 (cid2)
        assert out["matrix"]["1"]["1"] == 1
        assert out["matrix"]["1"]["2"] == 1
        assert out["coder_visibility"]["hidden_coder_filter"] == "applied"

    def test_codes_by_case_wrapped_with_note(self, visibility_db):
        out = json.loads(server.get_codes_by_case(1))
        assert isinstance(out, dict)
        counts = {c["code_name"]: c["occurrence_count"] for c in out["codes"]}
        assert counts["Stress"] == 1
        assert out["coder_visibility"]["hidden_coder_filter"] == "applied"

    def test_codes_by_case_plain_list_without_capability(self, setup_server):
        out = json.loads(server.get_codes_by_case(1))
        assert isinstance(out, list)

    def test_cases_by_code_excludes_hidden(self, visibility_db):
        out = json.loads(server.get_cases_by_code(1))
        assert isinstance(out, dict)
        assert out["cases"][0]["occurrence_count"] == 1

    def test_cooccurrence_excludes_hidden(self, visibility_db):
        # Visible rows for cid 1 (24-55) and cid 2 (57-77) never overlap;
        # the hidden cid-2 row at 30-40 (ctid 5) overlaps the cid-1 span
        # but is hidden, so the default read must not see it
        out = json.loads(server.find_cooccurring_codes(1))
        assert isinstance(out, dict)
        assert out["cooccurrences"] == []
        assert out["coder_visibility"]["hidden_coder_filter"] == "applied"

    def test_cooccurrence_coder_override_reaches_base_rows(self,
                                                           visibility_db):
        # The override must change the DATA, not only the flag: the
        # hidden coder's cid-1 (24-55) and cid-2 (30-40) rows overlap
        out = json.loads(server.find_cooccurring_codes(1, coder=HIDDEN))
        assert out["coder_visibility"]["hidden_coder_filter"] == "bypassed"
        assert [c["code_id"] for c in out["cooccurrences"]] == [2]

    def test_matrix_coder_override(self, visibility_db):
        out = json.loads(server.get_case_code_matrix(coder=HIDDEN))
        assert out["matrix"]["1"]["1"] == 1
        assert out["matrix"]["1"]["2"] == 2
        assert out["coder_visibility"]["hidden_coder_filter"] == "bypassed"

    def test_codes_by_case_coder_override(self, visibility_db):
        out = json.loads(server.get_codes_by_case(1, coder=HIDDEN))
        counts = {c["code_name"]: c["occurrence_count"] for c in out["codes"]}
        assert counts == {"Stress": 1, "Coping": 2}
        assert out["coder_visibility"]["hidden_coder_filter"] == "bypassed"

    def test_cases_by_code_coder_override(self, visibility_db):
        out = json.loads(server.get_cases_by_code(1, coder=HIDDEN))
        assert out["cases"][0]["occurrence_count"] == 1
        assert out["coder_visibility"]["hidden_coder_filter"] == "bypassed"

    def test_blank_coder_means_no_filter(self, visibility_db):
        # Upstream parity (ai_chat.py strips and drops blank coder
        # names): a blank coder reads the visible view like an absent
        # one instead of filtering base tables by owner '' (F10)
        for blank in ("", "   "):
            out = json.loads(server.get_coded_segments(1, coder=blank))
            assert out["segment_count"] == 1, repr(blank)
            assert {s["owner"] for s in out["segments"]} == {"TestCoder"}
            assert out["coder_visibility"]["hidden_coder_filter"] == "applied"

    def test_file_content_code_count_follows_visibility(
            self, visibility_db, qualcoder_db_path):
        # A code applied to file 1 ONLY by the hidden coder must not
        # inflate the files resource's code_count (F11)
        con = sqlite3.connect(str(Path(qualcoder_db_path) / "data.qda"))
        con.execute(
            "INSERT INTO code_name VALUES (3, 'Ghostly', '', 1, ?, "
            "'2024-01-15', '#0000FF')", (HIDDEN,))
        con.execute(
            "INSERT INTO code_text (ctid, cid, fid, seltext, pos0, pos1, "
            "owner, date, memo, important) VALUES (6, 3, 1, 'This', 0, 4, "
            "?, '2024-01-15', '', 0)", (HIDDEN,))
        con.commit()
        base_count = con.execute(
            "SELECT COUNT(DISTINCT cid) FROM code_text WHERE fid = 1"
        ).fetchone()[0]
        con.close()
        assert base_count == 3
        _reopen(qualcoder_db_path)
        assert server.db.get_file_content(1)["code_count"] == 2
        assert json.loads(server.get_file_content(1))["code_count"] == 2

    def test_search_memos_annotations_honor_visibility(self, visibility_db):
        # The hidden coder's annotation is not returned and its owner
        # name never appears; the disclosure block is present (F13)
        out = json.loads(server.search_memos("hidden annotation"))
        assert out["result_count"] == 0
        assert out["coder_visibility"]["hidden_coder_filter"] == "applied"
        assert HIDDEN not in json.dumps(out)

    def test_analyze_file_excludes_hidden_segments_and_annotations(
            self, visibility_db):
        out = json.loads(server.analyze_file_with_coding(1))
        owners = {s["owner"] for s in out["coded_segments"]}
        assert HIDDEN not in owners
        ann_owners = {a["owner"] for a in out["annotations"]}
        assert HIDDEN not in ann_owners
        assert out["coder_visibility"]["hidden_coder_filter"] == "applied"

    def test_code_info_counts_visible_only(self, visibility_db):
        out = json.loads(server.get_code_info(1))
        assert out["statistics"]["text_segments"] == 1
        # the hidden image coding is excluded via code_image_visible
        assert out["statistics"]["image_segments"] == 0

    def test_project_summary_counts_visible_only(self, visibility_db):
        out = json.loads(server.get_project_summary())
        assert out["statistics"]["total_coded_segments"] == 2
        assert out["coder_visibility"]["hidden_coder_filter"] == "applied"

    def test_no_note_when_no_coder_hidden(self, visibility_db,
                                          qualcoder_db_path):
        con = sqlite3.connect(str(Path(qualcoder_db_path) / "data.qda"))
        con.execute("UPDATE coder_names SET visibility = 1")
        con.commit()
        con.close()
        _reopen(qualcoder_db_path)
        out = json.loads(server.get_coded_segments(1))
        assert "coder_visibility" not in out
        assert out["segment_count"] == 2  # everything visible again


# =============================================================================
# PRE-4.0 PROJECTS: coder filters work on base tables, shape stays plain
# =============================================================================

class TestPre40CoderFilter:

    def test_coder_filter_on_base_tables_keeps_plain_shape(self,
                                                           setup_server):
        out = json.loads(server.get_coded_segments(1, coder="TestCoder"))
        assert out["segment_count"] == 1
        assert "coder_visibility" not in out
        out = json.loads(server.get_coded_segments(1, coder="Nobody"))
        assert out["segment_count"] == 0
        assert "coder_visibility" not in out
        assert isinstance(json.loads(server.get_codes_by_case(
            1, coder="Nobody")), list)

    def test_blank_coder_is_no_filter_without_capability(self,
                                                         setup_server):
        out = json.loads(server.get_coded_segments(1, coder=""))
        assert out["segment_count"] == 1
        assert "coder_visibility" not in out

    def test_search_memos_reads_base_annotations_without_capability(
            self, setup_server, qualcoder_db_path):
        # Without the 4.0 view the annotation branch reads the base
        # table and no disclosure block appears (F13)
        con = sqlite3.connect(str(Path(qualcoder_db_path) / "data.qda"))
        con.execute(
            "INSERT INTO annotation (fid, pos0, pos1, memo, owner, date) "
            "VALUES (1, 0, 4, 'hidden annotation', ?, '2024-01-15')",
            (HIDDEN,))
        con.execute(
            "INSERT OR REPLACE INTO coder_names (name, visibility) "
            "VALUES (?, 0)", (HIDDEN,))
        con.commit()
        con.close()
        _reopen(qualcoder_db_path)
        out = json.loads(server.search_memos("hidden annotation"))
        assert out["result_count"] == 1
        assert out["results"][0]["owner"] == HIDDEN
        assert "coder_visibility" not in out


# =============================================================================
# FILE EXPORTS KEEP READING BASE TABLES (QualCoder export/report parity)
# =============================================================================

class TestExportsReadBaseTables:

    def test_coded_segments_report_includes_hidden(self, visibility_db,
                                                   tmp_path):
        out_file = tmp_path / "report.csv"
        out = json.loads(server.export_coded_segments_report(str(out_file)))
        assert out.get("success") is True, out
        content = out_file.read_text(encoding="utf-8-sig")
        assert HIDDEN in content

    def test_matrix_csv_includes_hidden(self, visibility_db, tmp_path):
        out_file = tmp_path / "matrix.csv"
        out = json.loads(server.export_case_code_matrix_csv(str(out_file)))
        assert out.get("success") is True, out
        content = out_file.read_text(encoding="utf-8-sig")
        # Stress row counts 2 (1 visible + 1 hidden) in case A
        stress_col = content.splitlines()[0].split(",").index("Stress")
        case_row = next(line for line in content.splitlines()
                        if line.startswith('"Case A"')
                        or line.startswith("Case A"))
        assert case_row.replace('"', "").split(",")[stress_col] == "2"

    def test_refi_export_includes_hidden_codings(self, visibility_db,
                                                 tmp_path):
        out_file = tmp_path / "full.qdpx"
        out = json.loads(server.export_refi_qda(output_path=str(out_file)))
        assert out.get("success") is True, out
        assert out["codings_exported"] == 5  # 2 visible + 3 hidden
        with zipfile.ZipFile(out_file) as zf:
            qde = zf.read("project.qde").decode("utf-8")
        assert qde.count("<PlainTextSelection") == 5


# =============================================================================
# WRITE GUARDS ON HIDDEN CODERS' ROWS (S-MAJ Tier 1 + Tier 2, owner-approved)
# =============================================================================

def _row(project_path, sql, args=()):
    con = sqlite3.connect(str(Path(project_path) / "data.qda"))
    try:
        return con.execute(sql, args).fetchone()
    finally:
        con.close()


def _backups(project_path):
    project = Path(project_path)
    return sorted(project.parent.glob(f"{project.stem}_backup_*"))


class TestWriteEchoesRedactHiddenTargets:
    """delete_coding, update_annotation, delete_annotation and set_memo
    ('coding') reach hidden coders' rows by id (upstream parity:
    ai_mcp_server.py:2243-2280 deletes by ctid with no owner check).
    Tier 2: they REFUSE unless allow_hidden_coder=true, with a refusal that
    names neither the coder nor a count. Tier 1: with the override the echo
    is ids only, as upstream's. The fixture's hidden rows: ctid 3 ('Stress'
    on 'I feel stressed about deadlines', 24-55) and anid 1 ('hidden
    annotation'), both by HIDDEN."""

    FORBIDDEN = (HIDDEN, "Stress", "I feel stressed", "hidden annotation",
                 "interview.txt", "hidden memo")

    def _assert_no_leak(self, raw: str):
        for needle in self.FORBIDDEN:
            assert needle not in raw, needle

    def _assert_refused(self, raw: str):
        self._assert_no_leak(raw)
        out = json.loads(raw)
        assert "error" in out, out
        assert "hidden in QualCoder" in out["error"]
        assert "allow_hidden_coder" in out["error"]
        assert out["refused"] == ["hidden_coder"]
        assert out["nothing_changed"] is True
        # count-free: no digit other than the row id appears
        digits = {c for c in out["error"] if c.isdigit()}
        assert digits <= {"1", "3"}, out["error"]
        assert "hides" not in out["error"]
        return out

    def _assert_redacted_success(self, raw: str):
        self._assert_no_leak(raw)
        out = json.loads(raw)
        assert out.get("success") is True, out
        assert out["coder_visibility"]["hidden_coder_filter"] == "applied"
        assert out["coder_visibility"]["hidden_coders"] == 1
        return out

    # -- refusals -------------------------------------------------------

    def test_delete_coding_hidden_target_refused_without_override(
            self, visibility_db):
        self._assert_refused(server.delete_coding(3, create_backup=False))
        assert _row(visibility_db,
                    "SELECT COUNT(*) FROM code_text WHERE ctid = 3")[0] == 1
        assert _backups(visibility_db) == []

    def test_update_annotation_hidden_target_refused_without_override(
            self, visibility_db):
        self._assert_refused(server.update_annotation(1, "probe",
                                                      create_backup=False))
        assert _row(visibility_db,
                    "SELECT memo FROM annotation WHERE anid = 1")[0] == \
            "hidden annotation"

    def test_delete_annotation_hidden_target_refused_without_override(
            self, visibility_db):
        self._assert_refused(server.delete_annotation(1, create_backup=False))
        assert _row(visibility_db,
                    "SELECT COUNT(*) FROM annotation WHERE anid = 1")[0] == 1

    def test_set_memo_on_hidden_coding_refused_without_override(
            self, visibility_db):
        self._assert_refused(server.set_memo("coding", 3, "probe",
                                             create_backup=False))
        assert _row(visibility_db,
                    "SELECT memo FROM code_text WHERE ctid = 3")[0] == \
            "hidden memo"

    def test_db_layer_refuses_on_its_own(self, visibility_db):
        # Defense in depth: the write methods repeat the server pre-check
        wdb = QualcoderDatabase(visibility_db, read_only=False)
        try:
            for call in (
                lambda: wdb.delete_coding(3),
                lambda: wdb.update_annotation(1, "x"),
                lambda: wdb.delete_annotation(1),
                lambda: wdb.set_memo("coding", 3, "x"),
            ):
                with pytest.raises(ValueError, match="hidden in QualCoder"):
                    call()
        finally:
            wdb.close()
        assert _row(visibility_db,
                    "SELECT COUNT(*) FROM code_text WHERE ctid = 3")[0] == 1

    # -- overrides: ids-only echo (Tier 1) ------------------------------

    def test_delete_coding_with_override_echoes_ids_only(self, visibility_db):
        raw = server.delete_coding(3, create_backup=False,
                                   allow_hidden_coder=True)
        out = self._assert_redacted_success(raw)
        assert out["message"] == "Deleted coding 3"
        assert out["deleted_coding"] == {
            "coding_id": 3, "code_id": 1, "file_id": 1,
            "hidden_coder_row": True}
        assert _row(visibility_db,
                    "SELECT COUNT(*) FROM code_text WHERE ctid = 3")[0] == 0

    def test_update_annotation_with_override_echoes_ids_only(
            self, visibility_db):
        raw = server.update_annotation(1, "probe", create_backup=False,
                                       allow_hidden_coder=True)
        out = self._assert_redacted_success(raw)
        assert out["annotation_id"] == 1
        assert out["file_id"] == 1
        assert out["memo"] == "probe"          # the AI's own text
        assert out["updated"] is True
        assert out["hidden_coder_row"] is True
        for absent in ("owner", "file_name", "position_start",
                       "position_end"):
            assert absent not in out, absent
        row = _row(visibility_db,
                   "SELECT memo, owner FROM annotation WHERE anid = 1")
        assert row[0] == "probe" and row[1] == HIDDEN

    def test_delete_annotation_with_override_echoes_ids_only(
            self, visibility_db):
        raw = server.delete_annotation(1, create_backup=False,
                                       allow_hidden_coder=True)
        out = self._assert_redacted_success(raw)
        assert out["annotation_id"] == 1
        assert out["file_id"] == 1
        assert out["deleted"] is True
        assert out["hidden_coder_row"] is True
        for absent in ("owner", "file_name", "memo", "position_start"):
            assert absent not in out, absent
        assert _row(visibility_db,
                    "SELECT COUNT(*) FROM annotation WHERE anid = 1")[0] == 0

    def test_clearing_hidden_annotation_with_override_echoes_ids_only(
            self, visibility_db):
        # update_annotation("") deletes the row; the redaction must hold
        # on that path too
        raw = server.update_annotation(1, "", create_backup=False,
                                       allow_hidden_coder=True)
        out = self._assert_redacted_success(raw)
        assert out["deleted"] is True
        assert out["deleted_because_cleared"] is True
        assert "owner" not in out and "memo" not in out

    def test_set_memo_on_hidden_coding_with_override(self, visibility_db):
        raw = server.set_memo("coding", 3, "probe", create_backup=False,
                              allow_hidden_coder=True)
        self._assert_no_leak(raw)
        out = json.loads(raw)
        assert out["success"] is True
        assert out["memo"] == "probe"
        assert out["label"] == "coding 3"
        assert _row(visibility_db,
                    "SELECT memo FROM code_text WHERE ctid = 3")[0] == "probe"

    # -- hidden AND private: both overrides required (Tier 2 + S-P2) -----

    def test_hidden_and_private_row_needs_both_overrides(self, visibility_db):
        secret = "quokka-private-zone"
        con = sqlite3.connect(str(Path(visibility_db) / "data.qda"))
        con.execute("UPDATE code_text SET memo = ? WHERE ctid = 3",
                    (f"pub#####{secret}",))
        con.commit()
        con.close()
        _reopen(visibility_db)

        raw = server.delete_coding(3, create_backup=False)
        self._assert_no_leak(raw)
        out = json.loads(raw)
        assert "error" in out
        assert secret not in raw and "pub" not in out["error"]
        assert out["refused"] == ["hidden_coder", "private_note"]
        assert "hidden in QualCoder" in out["error"]
        assert "private note" in out["error"]
        assert "confirm_private_note_deletion" in out["error"]

        # One override alone is not enough, either way round
        one = json.loads(server.delete_coding(3, create_backup=False,
                                              allow_hidden_coder=True))
        assert one["refused"] == ["private_note"]
        other = json.loads(server.delete_coding(
            3, create_backup=False, confirm_private_note_deletion=True))
        assert other["refused"] == ["hidden_coder"]
        assert _row(visibility_db,
                    "SELECT COUNT(*) FROM code_text WHERE ctid = 3")[0] == 1
        assert _backups(visibility_db) == []

        raw = server.delete_coding(3, create_backup=False,
                                   allow_hidden_coder=True,
                                   confirm_private_note_deletion=True)
        assert secret not in raw
        out = self._assert_redacted_success(raw)
        assert out["deleted_coding"]["hidden_coder_row"] is True
        assert out["deleted_coding"]["private_note_removed"] is True
        assert "memo" not in out["deleted_coding"]
        # S-P2 (a): a backup exists even though create_backup=False
        assert "backup_path" in out
        assert "backup_note" in out
        assert len(_backups(visibility_db)) == 1
        assert _row(visibility_db,
                    "SELECT COUNT(*) FROM code_text WHERE ctid = 3")[0] == 0

    # -- visible rows and pre-4.0 projects untouched ----------------------

    def test_visible_target_keeps_full_echo_on_same_project(
            self, visibility_db):
        # The guards are per row: the visible coding on the very same 4.0
        # project needs no override, keeps the full echo, carries no note
        out = json.loads(server.delete_coding(1, create_backup=False))
        assert out["deleted_coding"]["code_name"] == "Stress"
        assert out["deleted_coding"]["owner"] == "TestCoder"
        assert out["deleted_coding"]["text"] == "I feel stressed about deadlines"
        assert "hidden_coder_row" not in out["deleted_coding"]
        assert "coder_visibility" not in out
        assert out["message"].startswith("Deleted coding 1 ('Stress' on")

    def test_visible_annotation_keeps_full_echo(self, visibility_db):
        out = json.loads(server.add_annotation(1, 5, 9, "mine",
                                               create_backup=False))
        anid = out["annotation"]["annotation_id"]
        upd = json.loads(server.update_annotation(anid, "edited",
                                                  create_backup=False))
        assert upd["owner"] == server._default_owner()
        assert upd["file_name"] == "interview.txt"
        assert "coder_visibility" not in upd
        dele = json.loads(server.delete_annotation(anid, create_backup=False))
        assert dele["memo"] == "edited"
        assert "coder_visibility" not in dele

    def test_pre40_project_never_redacts_or_refuses(self, setup_server):
        # No visibility capability: every row is visible, full echo, no
        # note, no override needed
        out = json.loads(server.delete_coding(1, create_backup=False))
        assert out["deleted_coding"]["owner"] == "TestCoder"
        assert "coder_visibility" not in out
        assert server.db.coding_is_visible(2) is True
        assert server.db.annotation_is_visible(999) is True
        assert server.db.existing_row_status("coding", 2) == {
            "hidden": False, "private_note": False}
        assert server.db.existing_row_status("coding", 999) is None


class TestCascadePreviewsReportHiddenRows:
    """Tier 2: the confirm-gated cascades report, as a count only, how many
    affected coding rows belong to hidden coders (4.0 projects); the key is
    absent on pre-4.0 projects. Fixture hidden rows: ctid 3 (cid 1), ctid 4
    and 5 (cid 2), imid 1 (cid 1)."""

    def test_delete_code_preview(self, visibility_db):
        p1 = json.loads(server.delete_code(1))["preview"]
        assert p1["hidden_coder_codings_affected"] == 2   # ctid 3 + imid 1
        assert HIDDEN not in json.dumps(p1)
        p2 = json.loads(server.delete_code(2))["preview"]
        assert p2["hidden_coder_codings_affected"] == 2   # ctid 4, 5

    def test_merge_codes_preview(self, visibility_db):
        p = json.loads(server.merge_codes(1, 2))["preview"]
        assert p["hidden_coder_codings_affected"] == 2
        p = json.loads(server.merge_codes(2, 1))["preview"]
        assert p["hidden_coder_codings_affected"] == 2

    def test_category_cascades_touch_no_codings(self, visibility_db):
        p = json.loads(server.delete_category(1))["preview"]
        assert p["hidden_coder_codings_affected"] == 0
        p = json.loads(server.merge_category(1))["preview"]
        assert p["hidden_coder_codings_affected"] == 0

    def test_pre40_previews_carry_no_hidden_key(self, setup_server):
        for raw in (server.delete_code(1), server.merge_codes(1, 2),
                    server.delete_category(1), server.merge_category(1)):
            p = json.loads(raw)["preview"]
            assert "hidden_coder_codings_affected" not in p


# =============================================================================
# FAIL CLOSED WHEN THE VISIBILITY STATE CANNOT BE READ (fix round 3, R1)
# =============================================================================

# Two ways a *_visible view can be PRESENT (so the capability probe, which
# checks object names, still reports 4.0 visibility) yet unable to answer
# the guard's query: a view that lost its id column, and QualCoder's own
# DDL pointed at a table that is gone.
_BROKEN_VIEWS = {
    "no_id_column": {
        "code_text_visible": "SELECT cid, fid, owner FROM code_text",
        "annotation_visible": "SELECT fid, owner FROM annotation",
    },
    "missing_table": {
        "code_text_visible": (
            "SELECT t.* FROM code_text_gone t WHERE NOT EXISTS "
            "(SELECT 1 FROM coder_names c WHERE c.name = t.owner "
            "AND c.visibility = 0)"),
        "annotation_visible": (
            "SELECT t.* FROM annotation_gone t WHERE NOT EXISTS "
            "(SELECT 1 FROM coder_names c WHERE c.name = t.owner "
            "AND c.visibility = 0)"),
    },
}


def _break_views(project_path, variant):
    con = sqlite3.connect(str(Path(project_path) / "data.qda"))
    try:
        for view, body in _BROKEN_VIEWS[variant].items():
            con.execute(f"DROP VIEW {view}")
            con.execute(f"CREATE VIEW {view} AS {body}")
        con.commit()
    finally:
        con.close()


class _LockedOnVisibilityView:
    """A connection proxy on which only the guard's view query reports a
    locked database; everything else reaches the real connection."""

    def __init__(self, real):
        self._real = real

    def execute(self, sql, *args):
        if sql.startswith("SELECT 1 FROM ") and "_visible WHERE" in sql:
            raise sqlite3.OperationalError("database is locked")
        return self._real.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._real, name)


class TestVisibilityGuardFailsClosed:
    """R1 (fix round 3): when the visibility capability probes as present
    but a *_visible view cannot answer, the by-id write guards raise a
    sanitized error instead of treating the row as visible. Nothing is
    written, no backup is taken and the hidden coder's data never enters
    the response, with or without allow_hidden_coder; an unreadable
    state is never reported as a hidden coder either (that would let the
    override write through it). A locked database surfaces as the locked
    error. Fixture hidden rows: ctid 3 and anid 1, both by HIDDEN."""

    FORBIDDEN = TestWriteEchoesRedactHiddenTargets.FORBIDDEN

    def _assert_failed_closed(self, raw):
        for needle in self.FORBIDDEN:
            assert needle not in raw, needle
        out = json.loads(raw)
        assert "error" in out and "success" not in out, out
        assert "hidden in QualCoder" not in out["error"]
        assert "refused" not in out
        return out

    def _rows_intact(self, project):
        assert _row(project,
                    "SELECT memo FROM code_text WHERE ctid = 3")[0] == \
            "hidden memo"
        assert _row(project,
                    "SELECT memo FROM annotation WHERE anid = 1")[0] == \
            "hidden annotation"
        assert _backups(project) == []

    @pytest.mark.parametrize("variant", sorted(_BROKEN_VIEWS))
    def test_helpers_raise_instead_of_answering_visible(self, visibility_db,
                                                        variant):
        _break_views(visibility_db, variant)
        _reopen(visibility_db)
        assert server.db.capabilities.has_coder_visibility is True
        with pytest.raises(RuntimeError, match="nothing was changed"):
            server.db.coding_is_visible(3)
        with pytest.raises(RuntimeError, match="nothing was changed"):
            server.db.annotation_is_visible(1)
        with pytest.raises(RuntimeError):
            server.db.existing_row_status("coding", 3)
        with pytest.raises(RuntimeError):
            server.db.existing_row_status("annotation", 1)

    # The by-id calls below use the DEFAULT create_backup: the guard must
    # refuse before the backup a default call would take, so
    # _rows_intact's "no backup" assertion is meaningful (fix round 4)

    @pytest.mark.parametrize("variant", sorted(_BROKEN_VIEWS))
    def test_by_id_writes_error_out_without_override(self, visibility_db,
                                                     variant):
        _break_views(visibility_db, variant)
        _reopen(visibility_db)
        for raw in (
            server.delete_coding(3),
            server.set_memo("coding", 3, "probe"),
            server.update_annotation(1, "probe"),
            server.delete_annotation(1),
        ):
            out = self._assert_failed_closed(raw)
            assert "visible in QualCoder" in out["error"]
            assert "nothing was changed" in out["error"]
        self._rows_intact(visibility_db)

    @pytest.mark.parametrize("variant", sorted(_BROKEN_VIEWS))
    def test_override_does_not_write_through_an_unreadable_state(
            self, visibility_db, variant):
        _break_views(visibility_db, variant)
        _reopen(visibility_db)
        for raw in (
            server.delete_coding(3, allow_hidden_coder=True),
            server.set_memo("coding", 3, "probe", allow_hidden_coder=True),
            server.update_annotation(1, "probe", allow_hidden_coder=True),
            server.delete_annotation(1, allow_hidden_coder=True),
        ):
            self._assert_failed_closed(raw)
        self._rows_intact(visibility_db)

    @pytest.mark.parametrize("variant", sorted(_BROKEN_VIEWS))
    def test_guard_runs_before_any_backup(self, visibility_db, variant,
                                          monkeypatch):
        # Timestamp-independent form of the no-backup assertion: with the
        # default create_backup, without and with the override, the
        # pre-check refuses before backup_before_write is ever reached
        _break_views(visibility_db, variant)
        _reopen(visibility_db)
        reached = []

        def record(self):
            reached.append(str(self.db_path))
            raise AssertionError("backup_before_write reached")

        monkeypatch.setattr(QualcoderDatabase, "backup_before_write", record)
        for override in (False, True):
            for raw in (
                server.delete_coding(3, allow_hidden_coder=override),
                server.set_memo("coding", 3, "probe",
                                allow_hidden_coder=override),
                server.update_annotation(1, "probe",
                                         allow_hidden_coder=override),
                server.delete_annotation(1, allow_hidden_coder=override),
            ):
                out = self._assert_failed_closed(raw)
                assert "nothing was changed" in out["error"]
        assert reached == []
        self._rows_intact(visibility_db)

    def test_visible_rows_are_guarded_the_same_way(self, visibility_db):
        # On a broken view the guard cannot tell a visible row from a
        # hidden one, so it refuses both: ctid 1 is TestCoder's own coding
        _break_views(visibility_db, "no_id_column")
        _reopen(visibility_db)
        self._assert_failed_closed(server.delete_coding(1,
                                                        create_backup=False))
        assert _row(visibility_db,
                    "SELECT COUNT(*) FROM code_text WHERE ctid = 1")[0] == 1

    def test_db_layer_fails_closed_on_its_own(self, visibility_db):
        _break_views(visibility_db, "missing_table")
        wdb = QualcoderDatabase(visibility_db, read_only=False)
        try:
            # All four writers, each without and with the override: the
            # db layer queries the view in every cell (set_memo with the
            # override short-circuited it until fix round 4)
            for call in (
                lambda: wdb.delete_coding(3),
                lambda: wdb.delete_coding(3, allow_hidden_coder=True),
                lambda: wdb.set_memo("coding", 3, "x"),
                lambda: wdb.set_memo("coding", 3, "x", allow_hidden_coder=True),
                lambda: wdb.update_annotation(1, "x"),
                lambda: wdb.update_annotation(1, "x", allow_hidden_coder=True),
                lambda: wdb.delete_annotation(1),
                lambda: wdb.delete_annotation(1, allow_hidden_coder=True),
            ):
                with pytest.raises(RuntimeError, match="nothing was changed"):
                    call()
        finally:
            wdb.close()
        self._rows_intact(visibility_db)

    def test_override_refuses_when_the_view_breaks_after_the_pre_check(
            self, visibility_db, monkeypatch):
        # The pre-check passes on an intact view; the view breaks before
        # the write runs. The db layer queries the view again, with the
        # override, and refuses (the set_memo cell that was open)
        real_perform_write = server._perform_write

        def break_then_write(op, **kwargs):
            _break_views(visibility_db, "missing_table")
            return real_perform_write(op, **kwargs)

        monkeypatch.setattr(server, "_perform_write", break_then_write)
        raw = server.set_memo("coding", 3, "probe", create_backup=False,
                              allow_hidden_coder=True)
        out = self._assert_failed_closed(raw)
        assert "nothing was changed" in out["error"]
        self._rows_intact(visibility_db)

    def test_locked_database_surfaces_as_locked_not_hidden(
            self, visibility_db, monkeypatch):
        monkeypatch.setattr(server.db, "conn",
                            _LockedOnVisibilityView(server.db.conn))
        for raw in (
            server.delete_coding(3),
            server.delete_coding(3, allow_hidden_coder=True),
            server.delete_annotation(1),
        ):
            out = self._assert_failed_closed(raw)
            assert out["error"] == DB_LOCKED_MESSAGE
        self._rows_intact(visibility_db)

    def test_cascade_preview_errors_rather_than_undercounting(
            self, visibility_db):
        # delete_code(1) would remove ctid 3 and imid 1 (both hidden); with
        # the text view unable to answer, the preview must not say 1
        _break_views(visibility_db, "missing_table")
        _reopen(visibility_db)
        for raw in (server.delete_code(1), server.merge_codes(1, 2)):
            out = self._assert_failed_closed(raw)
            assert "hidden coders" in out["error"]
            assert "preview" not in out
        assert _row(visibility_db,
                    "SELECT COUNT(*) FROM code_text WHERE cid = 1")[0] == 2

    def test_reads_and_writes_agree_on_a_broken_view(self, visibility_db):
        # The read side already failed loud on such a project; the write
        # guard no longer contradicts it
        _break_views(visibility_db, "missing_table")
        _reopen(visibility_db)
        assert "error" in json.loads(server.get_coded_segments(1))
        self._assert_failed_closed(server.delete_coding(3,
                                                        create_backup=False))


class TestTheCapabilityIsNotAVersion:
    """B1.16 (ruling 13), fix round 1 (QA round 1, F2 and F7).

    Coder visibility is not a 4.0 feature: QualCoder 3.8.2 and 4.0,
    schema v14 and later, create the table, the column and the views
    (3.8.2:__main__.py:1198-1319; the same harvest and view code stands
    at 9bddf17 app.py:1468-1540), and this server probes for the objects
    rather than asking a version. The wording pass reworded the
    docstrings and the guides but missed the runtime string every
    visibility-shaped read returns, which is the sentence a researcher
    actually sees, and one README line. Nothing pinned either, so the
    grep the gate runs by hand is written down here instead.
    """

    DOCS = ("README.md", "PRIVACY.md", "INSTALL.md", "AI_CODING_GUIDE.md",
            "AI_CODING_WORKFLOW.md")

    # Sentences where 4.0 is genuinely about 4.0 and not a stand-in for
    # the capability: its own assistant, its own rebuilds, its own lock
    # behaviour.
    GENUINELY_4_0 = ("QualCoder 4.0's own AI", "QualCoder 4.0's own",
                     "QualCoder 4.0's built-in assistant",
                     "4.0 builds no longer use a lock file",
                     "QualCoder 4.0 rebuilds", "4.0 detection",
                     # the README's own section title, quoted as a
                     # cross-reference rather than as a qualifier
                     '"Working alongside QualCoder 4.0"')

    VISIBILITY_WORDS = ("visibility", "visible", "hidden", "hides",
                        "hide ")

    @staticmethod
    def _sentences(text):
        """Sentence-ish units. The rule is about one claim at a time: a
        paragraph that happens to mention 4.0 in one sentence and
        visibility in another is not a version standing in for the
        capability."""
        flat = " ".join(text.split())
        return [s for s in re.split(r"(?<=[.:;])\s+", flat) if s]

    @classmethod
    def _offends(cls, text):
        return any(cls._offends_sentence(s) for s in cls._sentences(text))

    @classmethod
    def _offends_sentence(cls, sentence):
        # Remove the phrases where 4.0 is genuinely 4.0 rather than
        # exempting the whole sentence: a cross-reference to the README's
        # "Working alongside QualCoder 4.0" section can sit in the same
        # sentence as the claim the rule is about, and an exemption would
        # then hide it.
        for phrase in cls.GENUINELY_4_0:
            sentence = sentence.replace(phrase, "")
        lowered = sentence.lower()
        if "4.0" not in sentence:
            return False
        if not any(word in lowered for word in cls.VISIBILITY_WORDS):
            return False
        return "3.8.2" not in sentence

    def test_the_disclosure_every_read_returns_names_the_capability(
            self, visibility_db):
        out = json.loads(server.get_coded_segments(1))
        note = out["coder_visibility"]["note"]
        assert "3.8.2" in note, note
        assert not self._offends(note), note

    def test_the_override_disclosure_keeps_its_4_0_reference(
            self, visibility_db):
        """The sibling branch is about the override QualCoder 4.0's own
        AI uses, which is a fact about 4.0 rather than a version standing
        in for the capability. It stays as it is."""
        out = json.loads(server.get_coded_segments(1, coder=HIDDEN))
        note = out["coder_visibility"]["note"]
        assert "QualCoder 4.0's own AI" in note
        assert not self._offends(note), note

    def test_no_shipped_prose_makes_4_0_the_qualifier(self):
        root = Path(__file__).resolve().parents[1]
        offenders = []
        for name in self.DOCS:
            text = (root / name).read_text(encoding="utf-8")
            for paragraph in text.split("\n\n"):
                if self._offends(paragraph):
                    offenders.append((name, " ".join(paragraph.split())[:120]))
        assert offenders == [], offenders

    def test_the_sweep_would_notice(self):
        # A guard that cannot fire is not a guard: the released CHANGELOG
        # entries, which this rule deliberately does not rewrite, are
        # full of the pattern it looks for.
        assert self._offends(
            "On QualCoder 4.0 projects that hide coders, tools with a "
            "coder argument read visible coders' work by default.")
        assert not self._offends(
            "On projects with the coder-visibility capability (QualCoder "
            "3.8.2 and 4.0, schema v14 and later) that hide coders.")


# =============================================================================
# THE PERMISSIVE PER-NAME LOOKUP, AND THE SITES IT REACHED (fix round 2)
# =============================================================================

class TestNoSiteDecidesVisibilityPermissively:
    """The class F4 fixed in `compare_coders`, closed everywhere else.

    `coder_name_visibility` answered None for BOTH "this project has no
    visibility capability" and "the capability is present and
    `coder_names` did not answer", and `None != 0` reads as visible. Four
    more sites this batch added made that read: the frequencies export
    named every hidden coder and lost its disclosure block with them,
    the AI-coder-name setter stopped refusing a hidden coder's name, its
    case-variant warning named a hidden coder three lines under a
    comment citing the count-only rule, and the cascade preview's row
    owner lost its mask. All four now go through `server._visibility_map`
    and branch on all three answers; the per-name read is gone from the
    package.

    Every test here carries its INTACT control, and every damaged-table
    test also asserts that the tool still did its job, so a refusal
    cannot be passing because the call fell over for an unrelated
    reason.
    """

    @pytest.fixture
    def intact(self, setup_server, qualcoder_db_path):
        _apply_visibility_schema(qualcoder_db_path)
        _reopen(qualcoder_db_path)
        return qualcoder_db_path

    @staticmethod
    def _damage(project):
        """Drop `coder_names` AFTER the capability probe has run.

        The probe happens at connection time, so capabilities still
        report visibility while every read of the table raises. Schema
        drift, damaged pages and a concurrent QualCoder rebuilding the
        table all look like this.
        """
        con = sqlite3.connect(str(Path(project) / "data.qda"))
        con.execute("DROP TABLE coder_names")
        con.commit()
        con.close()

    def test_the_probe_still_says_the_capability_is_present(self, intact):
        """Otherwise nothing below would prove anything: a project with
        no capability hides nobody and may name everyone."""
        self._damage(intact)
        assert server.db.capabilities.has_coder_visibility is True

    # -- the frequencies export (B3.7, ruling Q11) ------------------------

    def test_the_export_names_visible_coders_and_counts_the_rest(
            self, intact, tmp_path):
        raw = server.export_frequencies_csv(str(tmp_path / "freq.csv"))
        out = json.loads(raw)
        assert out["coders"] == ["TestCoder"]
        assert out["coder_visibility"]["hidden_coders"] == 1
        assert HIDDEN not in raw
        # ... and the FILE keeps QualCoder parity either way
        assert HIDDEN in (tmp_path / "freq.csv").read_text(encoding="utf-8-sig")

    def test_the_export_names_nobody_when_the_table_does_not_answer(
            self, intact, tmp_path):
        self._damage(intact)
        raw = server.export_frequencies_csv(str(tmp_path / "freq.csv"))
        out = json.loads(raw)
        assert HIDDEN not in raw
        assert "coders" not in out
        assert out["coder_visibility"]["hidden_coder_filter"] == "unknown"
        # The export still ran: the refusal is the visibility decision,
        # not the whole tool falling over.
        assert out["success"] is True
        assert (tmp_path / "freq.csv").exists()
        assert out["codes"] == 2 and out["categories"] == 1
        assert HIDDEN in (tmp_path / "freq.csv").read_text(encoding="utf-8-sig")

    # -- the AI coder name setter (D7 4.1) --------------------------------

    def test_the_setter_refuses_a_hidden_name_and_takes_the_override(
            self, intact):
        refused = json.loads(server.set_project_ai_coder_name(HIDDEN))
        assert "currently hidden in QualCoder" in refused["error"]
        allowed = json.loads(
            server.set_project_ai_coder_name(HIDDEN, allow_hidden_coder=True))
        assert allowed["ai_coder_name"]["name"] == HIDDEN

    def test_the_setter_refuses_when_the_table_does_not_answer(self, intact):
        before = json.loads(server.get_current_project())
        self._damage(intact)
        raw = server.set_project_ai_coder_name(HIDDEN)
        out = json.loads(raw)
        assert "did not answer" in out["error"]
        assert "cannot be decided" in out["error"]
        assert HIDDEN not in out["error"]        # name-free
        assert not re.search(r"\b\d+ coder", out["error"])   # count-free
        assert "success" not in out
        # Nothing was written: the setting is still what it was.
        assert json.loads(server.get_current_project()).get(
            "ai_coder_name") == before.get("ai_coder_name")
        # And the override still works, so the refusal is the visibility
        # decision rather than a tool that can no longer write at all.
        allowed = json.loads(
            server.set_project_ai_coder_name(HIDDEN, allow_hidden_coder=True))
        assert allowed["ai_coder_name"]["name"] == HIDDEN

    # -- the case-variant warning (7.1: a count, never a name) ------------

    def test_the_case_warning_names_a_visible_coder_and_not_a_hidden_one(
            self, intact):
        visible = json.loads(server.set_project_ai_coder_name("testcoder"))
        assert any("\"TestCoder\"" in w for w in visible["warnings"])

        hidden_variant = HIDDEN.lower()
        out = json.loads(server.set_project_ai_coder_name(
            hidden_variant, allow_hidden_coder=True))
        warning = [w for w in out["warnings"] if "letter case" in w]
        assert warning and HIDDEN not in warning[0]

    def test_the_case_warning_names_nobody_when_the_table_is_gone(
            self, intact):
        self._damage(intact)
        raw = server.set_project_ai_coder_name("testcoder",
                                               allow_hidden_coder=True)
        out = json.loads(raw)
        warning = [w for w in out["warnings"] if "letter case" in w]
        # The warning is still RAISED (so this is not passing because it
        # vanished) and it no longer names the other spelling.
        assert warning, out["warnings"]
        assert "TestCoder" not in warning[0]
        assert out["ai_coder_name"]["name"] == "testcoder"

    # -- the cascade preview's row owner (ruling Q6) ----------------------

    @staticmethod
    def _give_category_to(project, owner):
        con = sqlite3.connect(str(Path(project) / "data.qda"))
        con.execute("UPDATE code_cat SET owner = ? WHERE catid = 1",
                    (owner,))
        con.commit()
        con.close()

    def test_a_hidden_row_owner_is_masked_and_a_visible_one_is_named(
            self, intact):
        control = json.loads(server.delete_category(1))
        assert control["preview"]["collateral"]["category_row_owner"] == \
            "TestCoder"
        assert control["preview"]["category"]["name"] == "Category A"

        self._give_category_to(intact, HIDDEN)
        _reopen(intact)
        raw = server.delete_category(1)
        out = json.loads(raw)
        assert out["preview"]["collateral"]["category_row_owner"] == \
            "(hidden coder)"
        assert HIDDEN not in raw

    def test_the_mask_goes_on_when_the_table_does_not_answer(self, intact):
        """The category previews are where this is reachable.

        `collateral_for_cids` is called with no cids there (deleting or
        merging a category touches no coding row), so the count helper
        that would otherwise fail closed first is never called and the
        row owner is the only visibility decision left in the block.
        """
        self._give_category_to(intact, HIDDEN)
        _reopen(intact)
        self._damage(intact)
        raw = server.delete_category(1)
        out = json.loads(raw)
        assert HIDDEN not in raw
        assert out["preview"]["collateral"]["category_row_owner"] == \
            "(hidden coder)"
        # The preview still computed, so the mask is not standing in for
        # a failed call.
        assert out["requires_confirmation"] is True
        assert out["preview"]["category"]["name"] == "Category A"

    def test_the_code_previews_fail_closed_one_step_earlier(self, intact):
        """Why the test above uses a category rather than a code: with
        cids to count, the anonymous hidden-coding count runs first and
        raises on the damaged table, so the whole preview refuses before
        the mask is reached. Both orders end name-free; only one of them
        is the mask's doing, and this says which."""
        self._damage(intact)
        out = json.loads(server.delete_code(1))
        assert "preview" not in out
        assert HIDDEN not in json.dumps(out)


class TestTheClassCannotComeBack:
    """One reader of `coder_names.visibility`, and a pin that says so.

    The per-name lookup was removed rather than documented, because a
    documented trap is still a trap and this one was reached for five
    times. These pin that the package keeps exactly one decision-making
    reader of the table, so a sixth arrives as a failing test rather
    than as a review finding.
    """

    PACKAGE = Path(__file__).resolve().parents[1] / "src" / "qualcoder_mcp"
    PATTERN = re.compile(r"FROM\s+coder_names", re.IGNORECASE)

    # Every SQL read of the table in the package, with the reason each
    # is allowed to exist. `hidden_coder_count` produces a COUNT for a
    # disclosure note on results whose ROWS come from the *_visible
    # views, which fail closed on their own, so its permissive zero
    # costs a disclosure block and never a disclosure (the argument the
    # gate upheld for the frequency, summary and cascade paths).
    EXPECTED = {
        "database.py": {
            "SELECT COUNT(*) FROM coder_names WHERE visibility = 0",
            "SELECT name, visibility FROM coder_names",
        },
    }

    QUOTED = re.compile(r"""["']([^"']*FROM\s+coder_names[^"']*)["']""",
                        re.IGNORECASE)

    def _reads(self):
        found = {}
        for path in sorted(self.PACKAGE.rglob("*.py")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not self.PATTERN.search(line):
                    continue
                quoted = self.QUOTED.search(line)
                found.setdefault(path.name, set()).add(
                    quoted.group(1).strip() if quoted else line.strip())
        return found

    def test_the_table_is_read_in_exactly_the_two_known_places(self):
        assert self._reads() == self.EXPECTED

    def test_the_sweep_finds_something(self):
        # An empty sweep would make the assertion above pass whatever
        # the source says.
        assert sum(len(v) for v in self._reads().values()) == 2

    def test_the_sweep_would_notice_a_per_name_read(self):
        assert self.PATTERN.search(
            '"SELECT visibility FROM coder_names WHERE name = ?"')

    def test_a_non_integer_visibility_fails_closed_rather_than_raising(
            self, setup_server, qualcoder_db_path):
        """The table answered, but not with the integer its own schema
        declares. `int()` would raise ValueError straight past every
        caller's `except CoderVisibilityUnreadable`."""
        from qualcoder_mcp.database import CoderVisibilityUnreadable
        _apply_visibility_schema(qualcoder_db_path)
        con = sqlite3.connect(str(Path(qualcoder_db_path) / "data.qda"))
        con.execute("PRAGMA writable_schema = ON")
        con.execute("UPDATE sqlite_master SET sql = replace(sql, "
                    "'CHECK (visibility IN (0, 1))', '') "
                    "WHERE name = 'coder_names'")
        con.execute("PRAGMA writable_schema = OFF")
        con.commit()
        con.close()
        con = sqlite3.connect(str(Path(qualcoder_db_path) / "data.qda"))
        con.execute("UPDATE coder_names SET visibility = 'yes' "
                    "WHERE name = ?", (HIDDEN,))
        con.commit()
        con.close()
        _reopen(qualcoder_db_path)
        with pytest.raises(CoderVisibilityUnreadable) as caught:
            server.db.coder_visibility_map()
        assert "yes" not in str(caught.value)      # value-free text
        assert HIDDEN not in str(caught.value)
