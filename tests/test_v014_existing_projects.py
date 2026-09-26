# SPDX-License-Identifier: LGPL-3.0-or-later
"""v0.14, existing projects handled honestly (brief D).

Saved graphs after a category is deleted or merged; the PDF guard rails;
region and audio/video codings disclosed in reads; backups made
consistently and unclean ones named; the backup tools on a project set
in the host's configuration.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import qualcoder_mcp.server as server
import track5_helpers as H
from qualcoder_mcp.sessions import SessionManager
from test_v17_support import make_project

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def opened(tmp_path, monkeypatch):
    """Open a fixture project at a schema rung; restore the selection."""
    monkeypatch.delenv("QUALCODER_MCP_ALLOW_UNKNOWN_SCHEMA", raising=False)
    saved = (server.db, server.current_project_path, server.session_manager)
    server.db = None
    server.current_project_path = None
    server.session_manager = SessionManager(str(tmp_path / "sessions"))

    def open_version(version: str) -> Path:
        folder = make_project(tmp_path, version)
        return folder

    yield open_version

    if server.db is not None:
        try:
            server.db.close()
        except Exception:
            pass
    server.db, server.current_project_path, server.session_manager = saved


def _rows(folder, sql, args=()):
    conn = sqlite3.connect(str(folder / "data.qda"))
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


# ===========================================================================
# 1. Saved graphs after a category is deleted or merged
# ===========================================================================

def _seed_graph(folder: Path) -> None:
    """A saved graph in the shape QualCoder's Graph window writes it
    (the graphs study's probe project, reduced): category 1's node with
    its two codes under it and a tree line to each; a line between the
    two codes; a case linked by a free line to the category node and to
    code 1; and another category's node, which nothing here touches."""
    conn = sqlite3.connect(str(folder / "data.qda"))
    conn.execute("INSERT INTO code_cat VALUES (2, 'Other', '', 'V17Test', "
                 "'2024-01-15', NULL)")
    conn.execute("INSERT INTO cases (caseid, name, memo, owner, date) "
                 "VALUES (1, 'P1', '', 'V17Test', '2024-01-15')")
    conn.execute("INSERT INTO graph VALUES (1, 'tree', '', '2024-01-15', "
                 "1200, 900)")
    nodes = [(1, None, 1, None, 'Category A'), (2, 1, 1, 1, 'Stress'),
             (3, 1, 1, 2, 'Coping'), (4, None, 2, None, 'Other')]
    for gtextid, supercatid, catid, cid, text in nodes:
        conn.execute(
            "INSERT INTO gr_cdct_text_item (gtextid, grid, x, y, supercatid, "
            "catid, cid, font_size, bold, isvisible, displaytext) "
            "VALUES (?, 1, 10, 10, ?, ?, ?, 9, 0, 1, ?)",
            (gtextid, supercatid, catid, cid, text))
    # tree lines, category node to each code node
    for glineid, tocid in ((1, 1), (2, 2)):
        conn.execute(
            "INSERT INTO gr_cdct_line_item (glineid, grid, fromcatid, "
            "fromcid, tocatid, tocid, color, linewidth, linetype, "
            "isvisible) VALUES (?, 1, 1, NULL, 1, ?, 'gray', 2, 'solid', 1)",
            (glineid, tocid))
    # code 1 to code 2 (both carry category 1 in their rows)
    conn.execute(
        "INSERT INTO gr_free_line_item (gflineid, grid, fromcatid, fromcid, "
        "tocatid, tocid, color, linewidth, linetype) "
        "VALUES (1, 1, 1, 1, 1, 2, 'blue', 3, 'solid')")
    # case to the category node, and case to code 1
    conn.execute(
        "INSERT INTO gr_free_line_item (gflineid, grid, fromcaseid, "
        "tocatid, tocid, color, linewidth, linetype) "
        "VALUES (2, 1, 1, 1, NULL, 'gray', 2, 'dotted')")
    conn.execute(
        "INSERT INTO gr_free_line_item (gflineid, grid, fromcaseid, "
        "tocatid, tocid, color, linewidth, linetype) "
        "VALUES (3, 1, 1, 1, 1, 'gray', 2, 'dotted')")
    conn.commit()
    conn.close()


def _graph_state(folder: Path):
    return (
        sorted(r[0] for r in _rows(folder,
                                   "SELECT gtextid FROM gr_cdct_text_item")),
        sorted(r[0] for r in _rows(folder,
                                   "SELECT glineid FROM gr_cdct_line_item")),
        sorted(r[0] for r in _rows(folder,
                                   "SELECT gflineid FROM gr_free_line_item")),
    )


# What stays after either operation on v16 and later: the two code nodes
# and the other category's node; no tree line (both end on the category
# node); the code-to-code line and the case-to-code line.
KEPT = ([2, 3, 4], [], [1, 3])
UNTOUCHED = ([1, 2, 3, 4], [1, 2], [1, 2, 3])


class TestSavedGraphsAfterACategoryGoes:

    @pytest.mark.parametrize("version", ["v16", "v17"])
    def test_delete_category_removes_its_node_and_its_lines(self, opened,
                                                            version):
        folder = opened(version)
        _seed_graph(folder)
        server.select_project(str(folder))
        preview = json.loads(server.delete_category(1))
        graphs = preview["preview"]["saved_graph_rows_removed"]
        assert (graphs["category_nodes"], graphs["lines"]) == (1, 3)
        out = json.loads(H.execute_destructive(server.delete_category, 1))
        assert out.get("success") is True, out
        assert _graph_state(folder) == KEPT
        # the codes survive, at the top level, still on the graph
        assert _rows(folder, "SELECT cid, catid FROM code_name "
                             "ORDER BY cid") == [(1, None), (2, None)]

    @pytest.mark.parametrize("version", ["v16", "v17"])
    def test_merge_category_keeps_the_codes_nodes_and_lines(self, opened,
                                                            version):
        folder = opened(version)
        _seed_graph(folder)
        server.select_project(str(folder))
        preview = json.loads(server.merge_category(1, into_category="Other"))
        graphs = preview["preview"]["saved_graph_rows_removed"]
        assert (graphs["category_nodes"], graphs["lines"]) == (1, 3)
        out = json.loads(H.execute_destructive(
            server.merge_category, 1, into_category="Other"))
        assert out.get("success") is True, out
        assert _graph_state(folder) == KEPT
        assert _rows(folder, "SELECT cid, catid FROM code_name "
                             "ORDER BY cid") == [(1, 2), (2, 2)]

    @pytest.mark.parametrize("version", ["v14", "v15"])
    def test_older_projects_keep_every_graph_row(self, opened, version):
        """3.8.2 cleans no graph row after either operation, and our v14
        and v15 writes stay byte-exact with it (the v16 gate)."""
        folder = opened(version)
        _seed_graph(folder)
        server.select_project(str(folder))
        preview = json.loads(server.delete_category(1))
        assert "saved_graph_rows_removed" not in preview["preview"]
        out = json.loads(H.execute_destructive(server.delete_category, 1))
        assert out.get("success") is True, out
        assert _graph_state(folder) == UNTOUCHED

    def test_merge_to_the_top_level_cleans_the_same_rows(self, opened):
        folder = opened("v17")
        _seed_graph(folder)
        server.select_project(str(folder))
        out = json.loads(H.execute_destructive(server.merge_category, 1))
        assert out.get("success") is True, out
        assert _graph_state(folder) == KEPT
