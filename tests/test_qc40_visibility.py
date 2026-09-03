"""P1-3: QC 4.0 coder-visibility reads (verdict c).

Fixture mirrors what QualCoder 4.0 creates in the project database on
open (app.py:1499-1562 at pin 9bddf17): the coder_names table with a
visibility column plus the code_text_visible / code_image_visible /
code_av_visible / annotation_visible views. Capability is probed from
those objects, never from version strings; pre-4.0 projects read base
tables as before, and the view is never hard-required.
"""

import json
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

    @pytest.mark.parametrize("variant", sorted(_BROKEN_VIEWS))
    def test_by_id_writes_error_out_without_override(self, visibility_db,
                                                     variant):
        _break_views(visibility_db, variant)
        _reopen(visibility_db)
        for raw in (
            server.delete_coding(3, create_backup=False),
            server.set_memo("coding", 3, "probe", create_backup=False),
            server.update_annotation(1, "probe", create_backup=False),
            server.delete_annotation(1, create_backup=False),
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
            server.delete_coding(3, create_backup=False,
                                 allow_hidden_coder=True),
            server.set_memo("coding", 3, "probe", create_backup=False,
                            allow_hidden_coder=True),
            server.update_annotation(1, "probe", create_backup=False,
                                     allow_hidden_coder=True),
            server.delete_annotation(1, create_backup=False,
                                     allow_hidden_coder=True),
        ):
            self._assert_failed_closed(raw)
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
            server.delete_coding(3, create_backup=False),
            server.delete_coding(3, create_backup=False,
                                 allow_hidden_coder=True),
            server.delete_annotation(1, create_backup=False),
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
