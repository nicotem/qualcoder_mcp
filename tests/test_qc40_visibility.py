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
from qualcoder_mcp.database import QualcoderDatabase

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
        assert by_name["Coping"] == 1
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
        # Hidden ctid 4 (cid 2, 57-77) does not overlap visible cid 1
        # span; visible cooccurrence for cid 1 is empty on this fixture
        out = json.loads(server.find_cooccurring_codes(1))
        assert isinstance(out, dict)
        assert out["cooccurrences"] == []
        assert out["coder_visibility"]["hidden_coder_filter"] == "applied"

    def test_cooccurrence_sees_hidden_via_base_on_pre40(self,
                                                        visibility_db):
        # Sanity: on the BASE tables the hidden cid2@57-77 does overlap
        # the visible cid2 coding but not cid1; use coder filter to
        # verify the override reaches base data
        out = json.loads(server.find_cooccurring_codes(2, coder=HIDDEN))
        assert out["coder_visibility"]["hidden_coder_filter"] == "bypassed"

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
        assert out["codings_exported"] == 4  # 2 visible + 2 hidden
        with zipfile.ZipFile(out_file) as zf:
            qde = zf.read("project.qde").decode("utf-8")
        assert qde.count("<PlainTextSelection") == 4
