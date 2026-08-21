"""v17 support: capability-probe gate (WS1/T1/S1) + sub-code write
correctness (WS2/T2-T4, T6/S2-S4/S10).

Fixture matrix v13-v17 built by replaying QualCoder master's own
migration DDL (__main__.py:2296-2346, pinned commit 7b074d2) onto a
3.8.2-shaped v14 base. The oracle for "a master open would not undo our
write" is master's literal open-time repair SQL (__main__.py:2376-2381)
replayed against the database after each write.
"""

import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server
from qualcoder_mcp.database import QualcoderDatabase, UnsupportedSchemaError
from qualcoder_mcp.sessions import SessionManager

FULLTEXT = ("This is interview text. I feel stressed about deadlines. "
            "I cope by exercising.")

# 3.8.2-shaped v14 base: the 13 core tables + coder_names (the v14 marker)
# + the pre-v14-era graph tables in their 3.8.2 shape (no label/arrow_mode),
# exactly what a real project carried into the v15-v17 migrations.
BASE_SCHEMA = """
CREATE TABLE project (databaseversion TEXT, date TEXT, memo TEXT, about TEXT, bookmarkfile INTEGER, bookmarkpos INTEGER, codername TEXT, recently_used_codes TEXT);
CREATE TABLE code_cat (catid INTEGER PRIMARY KEY, name TEXT UNIQUE, memo TEXT, owner TEXT, date TEXT, supercatid INTEGER);
CREATE TABLE code_name (cid INTEGER PRIMARY KEY, name TEXT UNIQUE, memo TEXT, catid INTEGER, owner TEXT, date TEXT, color TEXT);
CREATE TABLE source (id INTEGER PRIMARY KEY, name TEXT, fulltext TEXT, mediapath TEXT, memo TEXT, owner TEXT, date TEXT, av_text_id INTEGER, risid INTEGER, UNIQUE(name));
CREATE TABLE code_text (ctid INTEGER PRIMARY KEY, cid INTEGER, fid INTEGER, seltext TEXT, pos0 INTEGER, pos1 INTEGER, owner TEXT, date TEXT, memo TEXT, avid INTEGER, important INTEGER, UNIQUE(cid, fid, pos0, pos1, owner));
CREATE TABLE cases (caseid INTEGER PRIMARY KEY, name TEXT, memo TEXT, owner TEXT, date TEXT, CONSTRAINT ucm UNIQUE(name));
CREATE TABLE case_text (id INTEGER PRIMARY KEY, caseid INTEGER, fid INTEGER, pos0 INTEGER, pos1 INTEGER, memo TEXT, owner TEXT, date TEXT);
CREATE TABLE annotation (anid INTEGER PRIMARY KEY, fid INTEGER, pos0 INTEGER, pos1 INTEGER, memo TEXT, owner TEXT, date TEXT);
CREATE TABLE journal (jid INTEGER PRIMARY KEY, name TEXT, jentry TEXT, date TEXT, owner TEXT);
CREATE TABLE coder_names (name TEXT UNIQUE NOT NULL, visibility INTEGER NOT NULL DEFAULT 1 CHECK (visibility IN (0, 1)));
CREATE TABLE attribute_type (name TEXT PRIMARY KEY, date TEXT, owner TEXT, memo TEXT, caseOrFile TEXT, valuetype TEXT);
CREATE TABLE attribute (attrid INTEGER PRIMARY KEY, name TEXT, attr_type TEXT, value TEXT, id INTEGER, date TEXT, owner TEXT);
CREATE TABLE code_image (imid INTEGER PRIMARY KEY, id INTEGER, x1 INTEGER, y1 INTEGER, width INTEGER, height INTEGER, cid INTEGER, memo TEXT, date TEXT, owner TEXT, important INTEGER, pdf_page INTEGER);
CREATE TABLE code_av (avid INTEGER PRIMARY KEY, cid INTEGER, id INTEGER, pos0 INTEGER, pos1 INTEGER, memo TEXT, owner TEXT, date TEXT, important INTEGER DEFAULT 0);
CREATE TABLE graph (grid integer primary key, name text, description text, date text, scene_width integer, scene_height integer, unique(name));
CREATE TABLE gr_cdct_text_item (gtextid integer primary key, grid integer, x integer, y integer, supercatid integer, catid integer, cid integer, font_size integer, bold integer, isvisible integer, displaytext text);
CREATE TABLE gr_cdct_line_item (glineid integer primary key, grid integer, fromcatid integer, fromcid integer, tocatid integer, tocid integer, color text, linewidth real, linetype text, isvisible integer);
CREATE TABLE gr_free_line_item (gflineid integer primary key, grid integer, fromfreetextid integer, fromcatid integer, fromcid integer, fromcaseid integer, fromfileid integer, fromimid integer, fromavid integer, tofreetextid integer, tocatid integer, tocid integer, tocaseid integer, tofileid integer, toimid integer, toavid integer, color text, linewidth real, linetype text);
"""


def _seed(conn):
    conn.execute("INSERT INTO project (databaseversion, date, memo, about, codername) "
                 "VALUES ('v14', '2024-01-15', '', 'QualCoder 3.8.2', 'V17Test')")
    conn.execute("INSERT INTO code_cat VALUES (1, 'Category A', '', 'V17Test', '2024-01-15', NULL)")
    conn.execute("INSERT INTO code_name VALUES (1, 'Stress', 'stress memo', 1, 'V17Test', '2024-01-15', '#FF0000')")
    conn.execute("INSERT INTO code_name VALUES (2, 'Coping', '', 1, 'V17Test', '2024-01-15', '#00FF00')")
    conn.execute("INSERT INTO source (id, name, fulltext, mediapath, memo, owner, date) "
                 "VALUES (1, 'interview.txt', ?, NULL, '', 'V17Test', '2024-01-15')", (FULLTEXT,))
    conn.execute("INSERT INTO code_text (ctid, cid, fid, seltext, pos0, pos1, owner, date, memo, important) "
                 "VALUES (1, 1, 1, 'I feel stressed about deadlines', 24, 55, 'V17Test', '2024-01-15', '', NULL)")
    conn.execute("INSERT INTO coder_names (name) VALUES ('V17Test')")


# --- master's migration DDL, replayed verbatim (__main__.py:2296-2346) ---

def _migrate_v15(conn):
    conn.execute("alter table project add avbookmarkfile integer")
    conn.execute("alter table project add avbookmarkmsec integer")
    conn.execute("alter table project add avbookmarktextpos integer")
    conn.execute("update project set databaseversion='v15'")


def _migrate_v16(conn):
    conn.execute("alter table code_name add supercid integer")
    conn.execute("update project set databaseversion='v16'")


def _migrate_v17(conn):
    conn.execute("alter table gr_cdct_line_item add label text")
    conn.execute("alter table gr_cdct_line_item add arrow_mode text")
    conn.execute("alter table gr_free_line_item add label text")
    conn.execute("alter table gr_free_line_item add arrow_mode text")
    conn.execute("CREATE TABLE IF NOT EXISTS gr_memo_item (gmemoid integer primary key, grid integer, "
                 "memo_source_type text, memo_source_id integer, x integer, y integer, "
                 "color text, font_size integer);")
    conn.execute("update project set databaseversion='v17'")


def make_project(parent: Path, version: str) -> Path:
    """Build a fixture at the given schema rung of the migration ladder."""
    folder = parent / f"proj_{version}.qda"
    folder.mkdir()
    conn = sqlite3.connect(str(folder / "data.qda"))
    conn.executescript(BASE_SCHEMA)
    _seed(conn)
    if version == "v13":
        conn.execute("DROP TABLE coder_names")  # the v14 capability marker
        conn.execute("UPDATE project SET databaseversion='v13'")
    if version in ("v15", "v16", "v17"):
        _migrate_v15(conn)
    if version in ("v16", "v17"):
        _migrate_v16(conn)
    if version == "v17":
        _migrate_v17(conn)
    if version == "v18":
        _migrate_v15(conn)
        _migrate_v16(conn)
        _migrate_v17(conn)
        conn.execute("UPDATE project SET databaseversion='v18'")
    conn.commit()
    conn.close()
    return folder


def add_subcode(folder: Path, cid, name, supercid, color="#0000FF"):
    conn = sqlite3.connect(str(folder / "data.qda"))
    conn.execute(
        "INSERT INTO code_name (cid, name, memo, catid, owner, date, color, supercid) "
        "VALUES (?, ?, '', NULL, 'V17Test', '2024-01-15', ?, ?)",
        (cid, name, color, supercid))
    conn.commit()
    conn.close()


def replay_master_open_repair(folder: Path) -> int:
    """Master's literal open-time hygiene SQL (__main__.py:2376-2381).
    Returns the number of rows the repair changed: 0 means a master open
    would keep our writes exactly as written."""
    conn = sqlite3.connect(str(folder / "data.qda"))
    before = conn.total_changes
    conn.execute(
        "update code_name set supercid=null where supercid is not null "
        "and supercid not in (select cid from code_name)")
    conn.execute(
        "update code_name set catid=null where supercid is not null "
        "and catid is not null")
    changed = conn.total_changes - before
    conn.rollback()
    conn.close()
    return changed


def _rows(folder, sql, args=()):
    conn = sqlite3.connect(str(folder / "data.qda"))
    conn.row_factory = sqlite3.Row
    out = conn.execute(sql, args).fetchall()
    conn.close()
    return out


@pytest.fixture
def v17_env(tmp_path, monkeypatch):
    """Server wired to a private session store; yields a project opener."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.delenv("QUALCODER_MCP_ALLOW_UNKNOWN_SCHEMA", raising=False)
    saved = (server.db, server.current_project_path, server.session_manager)
    server.db = None
    server.current_project_path = None
    server.session_manager = SessionManager(str(tmp_path / "sessions"))

    def open_version(version: str) -> Path:
        folder = make_project(tmp_path, version)
        out = json.loads(server.select_project(str(folder)))
        assert out.get("success") is True, out
        return folder

    yield open_version

    if server.db is not None:
        try:
            server.db.close()
        except Exception:
            pass
    server.db, server.current_project_path, server.session_manager = saved


# ===========================================================================
# T1 / S1: the probe gate across the fixture matrix
# ===========================================================================

class TestT1ProbeGateMatrix:

    @pytest.mark.parametrize("version", ["v14", "v15", "v16", "v17"])
    def test_writes_proceed_v14_through_v17(self, v17_env, version):
        v17_env(version)
        out = json.loads(server.set_memo("code", 1, f"memo on {version}",
                                         create_backup=False))
        assert out.get("success") is True, (version, out)

    def test_v13_refused_with_corrected_message(self, v17_env):
        v17_env("v13")
        out = json.loads(server.set_memo("code", 1, "x", create_backup=False))
        assert "pre-v14 schema" in out["error"]
        assert "QualCoder 3.8 or newer" in out["error"]
        # the old backwards advice must be gone: a v16+ user must never be
        # told to "upgrade" via 3.8
        assert "to upgrade it" not in out["error"]

    def test_v18_refused_without_override(self, v17_env):
        v17_env("v18")
        out = json.loads(server.set_memo("code", 1, "x", create_backup=False))
        assert "v18" in out["error"]
        assert "7b074d2" in out["error"]
        assert "QUALCODER_MCP_ALLOW_UNKNOWN_SCHEMA" in out["error"]

    def test_v18_allowed_with_override_and_warned(self, v17_env,
                                                  monkeypatch):
        v17_env("v18")
        monkeypatch.setenv("QUALCODER_MCP_ALLOW_UNKNOWN_SCHEMA", "1")
        out = json.loads(server.set_memo("code", 1, "override write",
                                         create_backup=False))
        assert out.get("success") is True
        assert "WARNING" in out["schema_warning"]
        assert "v18" in out["schema_warning"]

    def test_capability_probes_reported(self, v17_env):
        v17_env("v16")
        out = json.loads(server.get_current_project())
        schema = out["schema"]
        assert schema["databaseversion"] == "v16"
        assert schema["write_support"] is True
        caps = schema["capabilities"]
        assert caps["has_coder_names"] is True
        assert caps["has_supercid"] is True
        assert caps["has_graph_labels"] is False   # v17-only column
        v17_env("v17")
        caps = json.loads(server.get_current_project())["schema"]["capabilities"]
        assert caps["has_supercid"] is True
        assert caps["has_graph_labels"] is True
        assert caps["has_gr_memo_item"] is True

    def test_v13_reads_stay_permissive(self, v17_env):
        v17_env("v13")
        out = json.loads(server.search_coded_text("stressed"))
        assert out["result_count"] == 1
        schema = json.loads(server.get_current_project())["schema"]
        assert schema["write_support"] is False
        assert "pre-v14" in schema["reason"]


# ===========================================================================
# T2 / S2: move writes both parent pointers on v16+
# ===========================================================================

class TestT2MoveSubcode:

    def test_move_subcode_to_category_survives_master_open(self, v17_env):
        folder = v17_env("v16")
        add_subcode(folder, 10, "Sub stress", supercid=1)
        server.switch_project(str(folder))
        out = json.loads(server.move_code_to_category(10, category="Category A",
                                                      create_backup=False))
        assert out.get("success") is True, out
        row = _rows(folder, "SELECT catid, supercid FROM code_name WHERE cid=10")[0]
        assert row["catid"] == 1
        assert row["supercid"] is None
        # a master open changes NOTHING (the old single-column write would
        # have left both parents set and master would DISCARD our catid)
        assert replay_master_open_repair(folder) == 0

    def test_move_subcode_to_top_level_is_not_a_noop(self, v17_env):
        folder = v17_env("v16")
        add_subcode(folder, 10, "Sub stress", supercid=1)
        server.switch_project(str(folder))
        out = json.loads(server.move_code_to_category(10, category=None,
                                                      create_backup=False))
        assert out.get("success") is True, out
        row = _rows(folder, "SELECT catid, supercid FROM code_name WHERE cid=10")[0]
        assert row["catid"] is None and row["supercid"] is None

    def test_v14_move_unchanged(self, v17_env):
        folder = v17_env("v14")
        out = json.loads(server.move_code_to_category(2, category=None,
                                                      create_backup=False))
        assert out.get("success") is True
        row = _rows(folder, "SELECT catid FROM code_name WHERE cid=2")[0]
        assert row["catid"] is None


# ===========================================================================
# T3 / S3: merge reparents sub-codes and refuses cycles
# ===========================================================================

class TestT3MergeSubcodes:

    def test_merge_reparents_subcodes_no_orphans(self, v17_env):
        folder = v17_env("v17")
        add_subcode(folder, 10, "Sub one", supercid=1)
        add_subcode(folder, 11, "Sub two", supercid=1)
        # seed graph rows for the merged-away cid (v17 recipe cleans them)
        conn = sqlite3.connect(str(folder / "data.qda"))
        conn.execute("INSERT INTO gr_cdct_text_item (grid, cid) VALUES (1, 1)")
        conn.execute("INSERT INTO gr_cdct_line_item (grid, fromcid, tocid) VALUES (1, 1, 2)")
        conn.execute("INSERT INTO gr_free_line_item (grid, fromcid, tocid) VALUES (1, 2, 1)")
        conn.commit()
        conn.close()
        server.switch_project(str(folder))

        out = json.loads(server.merge_codes(1, 2, confirm=True))
        assert out.get("success") is True, out
        assert out["subcodes_reparented_to_target"] == 2
        assert out["provenance_memo_added"] is True
        subs = _rows(folder, "SELECT cid, catid, supercid FROM code_name "
                             "WHERE cid IN (10, 11)")
        for row in subs:
            assert row["supercid"] == 2
            assert row["catid"] is None
        # graph rows for the dead cid are gone; unrelated rows survive
        assert _rows(folder, "SELECT * FROM gr_cdct_text_item WHERE cid=1") == []
        assert _rows(folder, "SELECT * FROM gr_cdct_line_item "
                             "WHERE fromcid=1 OR tocid=1") == []
        assert _rows(folder, "SELECT * FROM gr_free_line_item "
                             "WHERE fromcid=1 OR tocid=1") == []
        # provenance block on the target memo, master shape
        memo = _rows(folder, "SELECT memo FROM code_name WHERE cid=2")[0][0]
        assert "[Merged from code: Stress, Coder: V17Test" in memo
        assert "stress memo" in memo
        # zero rows would change at a master open
        assert replay_master_open_repair(folder) == 0

    def test_merge_into_own_subcode_refused(self, v17_env):
        folder = v17_env("v16")
        add_subcode(folder, 10, "Sub one", supercid=1)
        add_subcode(folder, 11, "Sub sub", supercid=10)
        server.switch_project(str(folder))
        before = _rows(folder, "SELECT cid FROM code_name")
        out = json.loads(server.merge_codes(1, 11, confirm=True))
        assert "sub-codes" in out["error"]
        assert _rows(folder, "SELECT cid FROM code_name") == before
        # refused at preview time too, before any backup
        assert not list(folder.parent.glob(f"{folder.stem}_backup_*"))

    def test_v14_merge_stays_382_exact(self, v17_env):
        """No provenance memo, no graph cleanup on v14 (3.8.2 parity)."""
        folder = v17_env("v14")
        out = json.loads(server.merge_codes(1, 2, confirm=True))
        assert out.get("success") is True
        assert "subcodes_reparented_to_target" not in out
        assert "provenance_memo_added" not in out
        memo = _rows(folder, "SELECT memo FROM code_name WHERE cid=2")[0][0]
        assert "Merged from code" not in memo


# ===========================================================================
# T4 / S4: delete respects the branch
# ===========================================================================

class TestT4DeleteBranch:

    def test_preview_reports_branch(self, v17_env):
        folder = v17_env("v16")
        add_subcode(folder, 10, "Sub one", supercid=1)
        add_subcode(folder, 11, "Sub sub", supercid=10)
        conn = sqlite3.connect(str(folder / "data.qda"))
        conn.execute("INSERT INTO code_text (cid, fid, seltext, pos0, pos1, owner, date, memo) "
                     "VALUES (11, 1, 'I cope by exercising', 57, 77, 'V17Test', '2024-01-15', '')")
        conn.commit()
        conn.close()
        server.switch_project(str(folder))
        out = json.loads(server.delete_code(1))
        preview = out["preview"]
        assert preview["subcode_count"] == 2
        assert sorted(preview["subcodes"]) == ["Sub one", "Sub sub"]
        assert preview["text_codings_to_delete"] == 2  # branch-wide count
        assert "cascade=true" in preview["note"]

    def test_delete_without_cascade_refused(self, v17_env):
        folder = v17_env("v16")
        add_subcode(folder, 10, "Sub one", supercid=1)
        server.switch_project(str(folder))
        out = json.loads(server.delete_code(1, confirm=True))
        assert "cascade=true" in out["error"]
        assert _rows(folder, "SELECT cid FROM code_name WHERE cid IN (1, 10)")

    def test_cascade_deletes_whole_branch_no_dangling(self, v17_env):
        folder = v17_env("v16")
        add_subcode(folder, 10, "Sub one", supercid=1)
        add_subcode(folder, 11, "Sub sub", supercid=10)
        conn = sqlite3.connect(str(folder / "data.qda"))
        conn.execute("INSERT INTO code_text (cid, fid, seltext, pos0, pos1, owner, date, memo) "
                     "VALUES (11, 1, 'I cope by exercising', 57, 77, 'V17Test', '2024-01-15', '')")
        conn.commit()
        conn.close()
        server.switch_project(str(folder))
        out = json.loads(server.delete_code(1, confirm=True, cascade=True))
        assert out.get("success") is True, out
        assert out["branch_deleted"] is True
        assert _rows(folder, "SELECT cid FROM code_name WHERE cid IN (1, 10, 11)") == []
        assert _rows(folder, "SELECT * FROM code_text WHERE cid IN (1, 10, 11)") == []
        # S4 invariant: no code delete may ever leave a dangling supercid
        assert _rows(folder, "SELECT cid FROM code_name WHERE supercid IS NOT NULL "
                             "AND supercid NOT IN (SELECT cid FROM code_name)") == []
        assert replay_master_open_repair(folder) == 0

    def test_v14_delete_unchanged(self, v17_env):
        folder = v17_env("v14")
        out = json.loads(server.delete_code(1, confirm=True))
        assert out.get("success") is True
        assert "branch_deleted" not in out


# ===========================================================================
# T6 / S10: sub-code creation
# ===========================================================================

class TestT6CreateSubcode:

    def test_create_subcode_on_v16(self, v17_env):
        folder = v17_env("v16")
        out = json.loads(server.create_code("Sub stress", parent_code_id=1,
                                            create_backup=False))
        assert out.get("success") is True, out
        cid = out["code"]["id"]
        row = _rows(folder, "SELECT catid, supercid FROM code_name WHERE cid=?",
                    (cid,))[0]
        assert row["supercid"] == 1 and row["catid"] is None
        assert replay_master_open_repair(folder) == 0

    def test_both_parents_refused(self, v17_env):
        v17_env("v16")
        out = json.loads(server.create_code("X", category="Category A",
                                            parent_code_id=1,
                                            create_backup=False))
        assert "not both" in out["error"]

    def test_unknown_parent_refused(self, v17_env):
        v17_env("v16")
        out = json.loads(server.create_code("X", parent_code_id=999,
                                            create_backup=False))
        assert "does not exist" in out["error"]

    def test_refused_on_v14(self, v17_env):
        v17_env("v14")
        out = json.loads(server.create_code("X", parent_code_id=1,
                                            create_backup=False))
        assert "v16 or newer" in out["error"]
