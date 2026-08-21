"""v17 WS3: sub-code-aware reads and exports (T7-T11 / S5-S9).

Fixture: v16 project with Category A > Stress > Sub stress > Deep sub,
codings on the leaf, built with the same master-DDL replay as
test_v17_support. Oracles implement the upstream algorithms directly
(categories_of_code report_codes.py:1131-1175; REFI importer linkage
refi.py:242-254).
"""

import csv
import json
import sqlite3
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import qualcoder_mcp.server as server
from qualcoder_mcp.sessions import SessionManager

from test_v17_support import make_project, add_subcode, FULLTEXT  # noqa: E402,F401


@pytest.fixture
def subcode_env(tmp_path, monkeypatch):
    """v16 project: Category A > Stress(1) > Sub stress(10) > Deep sub(11),
    Coping(2) in Category A, coding on Stress (fixture) + on Deep sub."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    saved = (server.db, server.current_project_path, server.session_manager)
    server.db = None
    server.current_project_path = None
    server.session_manager = SessionManager(str(tmp_path / "sessions"))

    folder = make_project(tmp_path, "v16")
    add_subcode(folder, 10, "Sub stress", supercid=1)
    add_subcode(folder, 11, "Deep sub", supercid=10)
    conn = sqlite3.connect(str(folder / "data.qda"))
    conn.execute(
        "INSERT INTO code_text (cid, fid, seltext, pos0, pos1, owner, date, memo) "
        "VALUES (11, 1, 'I cope by exercising', 57, 77, 'V17Test', '2024-01-15', '')")
    conn.commit()
    conn.close()
    out = json.loads(server.select_project(str(folder)))
    assert out.get("success") is True

    yield folder

    if server.db is not None:
        try:
            server.db.close()
        except Exception:
            pass
    server.db, server.current_project_path, server.session_manager = saved


def _upstream_categories_of_code(folder, cid):
    """Oracle: master report_codes.py:1131-1175. Parent CODE names first
    (immediate parent upward), then the top ancestor's category lineage,
    leaf to root."""
    conn = sqlite3.connect(str(folder / "data.qda"))
    conn.row_factory = sqlite3.Row
    codes = {r["cid"]: dict(r) for r in conn.execute(
        "SELECT cid, name, catid, supercid FROM code_name")}
    cats = {r["catid"]: dict(r) for r in conn.execute(
        "SELECT catid, name, supercatid FROM code_cat")}
    conn.close()
    path_codes = []
    current = cid
    seen = set()
    while current in codes and current not in seen:
        seen.add(current)
        parent = codes[current]["supercid"]
        if parent is None or parent not in codes:
            break
        path_codes.append(codes[parent]["name"])
        current = parent
    chain = list(path_codes)
    catid = codes[current]["catid"]
    cseen = set()
    while catid in cats and catid not in cseen:
        cseen.add(catid)
        chain.append(cats[catid]["name"])
        catid = cats[catid]["supercatid"]
    return chain


# ===========================================================================
# T11 / S5: listings expose the hierarchy
# ===========================================================================

class TestT11Listings:

    def test_code_details_show_parent_and_path(self, subcode_env):
        # get_code_details feeds the qualcoder://codes/{id} resource and
        # export_code_report; asserted at the shared DB layer
        out = server.db.get_code_details(11)
        assert out["parent_code_id"] == 10
        assert out["parent_code_name"] == "Sub stress"
        assert out["path"] == "Category A > Stress > Sub stress > Deep sub"

    def test_no_subcode_presented_as_merely_uncategorised(self, subcode_env):
        codes = server.db.list_codes()
        for c in codes:
            if c["category"] is None:
                assert c.get("parent_code_id") is not None, c["name"]

    def test_top_level_code_fields(self, subcode_env):
        out = server.db.get_code_details(1)
        assert out["parent_code_id"] is None
        assert out["path"] == "Category A > Stress"


# ===========================================================================
# T10 / S6: effective-category attribution in frequencies
# ===========================================================================

class TestT10Frequencies:

    def test_subcode_attributed_to_top_ancestor_category(self, subcode_env):
        out = json.loads(server.get_coding_frequencies())
        by_id = {c["code_id"]: c for c in out["codes"]}
        # Deep sub's top ancestor (Stress) sits in Category A
        assert by_id[11]["category"] == "Category A"
        assert by_id[11]["frequency"] == 1     # counts stay per-cid
        assert by_id[1]["frequency"] == 1

    def test_frequencies_csv_nests_and_rolls_up(self, subcode_env, tmp_path):
        out = json.loads(server.export_frequencies_csv(
            str(tmp_path / "freq.csv")))
        assert out["success"] is True
        with open(out["output_path"], encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.reader(fh))
        tree_cells = [r[0] for r in rows[1:]]
        # nesting: category depth 0, Stress depth 1, Sub depth 2, Deep 3
        assert "Category A" in tree_cells
        assert "--Stress" in tree_cells
        assert "----Sub stress" in tree_cells
        assert "------Deep sub" in tree_cells
        # category total includes the whole sub-code branch (1 + 1)
        cat_row = rows[1 + tree_cells.index("Category A")]
        assert cat_row[-1] == "2"
        # per-code rows stay per-cid
        deep_row = rows[1 + tree_cells.index("------Deep sub")]
        assert deep_row[-1] == "1"


# ===========================================================================
# T7 / S7: codebook export nests sub-codes
# ===========================================================================

class TestT7Codebook:

    def test_csv_nesting_depths(self, subcode_env, tmp_path):
        out = json.loads(server.export_codebook(str(tmp_path / "cb.csv")))
        with open(out["output_path"], encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.reader(fh))
        tree = {r[0]: r for r in rows[1:]}
        assert "Category A" in tree
        assert "...Stress" in tree
        assert "......Sub stress" in tree
        assert ".........Deep sub" in tree
        # no sub-code at depth 0
        assert "Sub stress" not in tree and "Deep sub" not in tree

    def test_txt_nesting(self, subcode_env, tmp_path):
        out = json.loads(server.export_codebook(str(tmp_path / "cb.txt"),
                                                format="txt"))
        text = Path(out["output_path"]).read_text(encoding="utf-8-sig")
        assert "......Code: Sub stress" in text
        assert ".........Code: Deep sub, Count: 1" in text


# ===========================================================================
# T8 / S8: report category chain follows categories_of_code
# ===========================================================================

class TestT8ReportChain:

    def test_subcode_chain_matches_upstream_oracle(self, subcode_env,
                                                   tmp_path):
        out = json.loads(server.export_coded_segments_report(
            str(tmp_path / "seg.csv")))
        assert out["success"] is True
        with open(out["output_path"], encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.reader(fh))
        header = rows[0]
        n_cat = header.count("Category")
        oracle = _upstream_categories_of_code(subcode_env, 11)
        assert n_cat == len(oracle)  # deepest chain sets the width
        deep_row = next(r for r in rows[1:] if r[4] == "Deep sub")
        chain_cells = [c for c in deep_row[6:6 + n_cat] if c]
        assert chain_cells == oracle
        # parent code names come FIRST, then the category
        assert chain_cells[0] == "Sub stress"
        assert chain_cells[-1] == "Category A"

    def test_top_level_code_chain_unchanged(self, subcode_env, tmp_path):
        out = json.loads(server.export_coded_segments_report(
            str(tmp_path / "seg2.csv")))
        with open(out["output_path"], encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.reader(fh))
        stress_row = next(r for r in rows[1:] if r[4] == "Stress")
        chain_cells = [c for c in stress_row[6:] if c]
        assert chain_cells == _upstream_categories_of_code(subcode_env, 1)
        assert chain_cells == ["Category A"]


# ===========================================================================
# T9 / S9: REFI export preserves sub-code nesting
# ===========================================================================

NS = "urn:QDA-XML:project:1.0"


def _replay_master_import_linkage(codes_elem):
    """Oracle: master's importer makes codable children of codable codes
    sub-codes (refi.py:242-254). Returns {code name: parent code name}."""
    linkage = {}

    def visit(elem, parent_name, parent_codable):
        for child in elem.findall(f"{{{NS}}}Code"):
            name = child.get("name")
            codable = child.get("isCodable", "true") == "true"
            if codable and parent_codable and parent_name is not None:
                linkage[name] = parent_name
            elif codable:
                linkage[name] = None
            visit(child, name, codable)

    visit(codes_elem, None, False)
    return linkage


class TestT9RefiExport:

    def test_subcodes_nested_and_reimportable(self, subcode_env, tmp_path):
        out_path = tmp_path / "proj.qdpx"
        out = json.loads(server.export_refi_qda(str(out_path)))
        assert out.get("success") is True, out
        with zipfile.ZipFile(out_path) as zf:
            qde = zf.read("project.qde").decode("utf-8")
        root = ET.fromstring(qde)
        codes_elem = root.find(f"{{{NS}}}CodeBook/{{{NS}}}Codes")
        assert codes_elem is not None

        # structural: Deep sub nested inside Sub stress inside Stress,
        # all codable
        def find_code(elem, name):
            for c in elem.iter(f"{{{NS}}}Code"):
                if c.get("name") == name:
                    return c
            return None

        stress = find_code(codes_elem, "Stress")
        sub = find_code(stress, "Sub stress")
        deep = find_code(sub, "Deep sub")
        assert sub is not None and deep is not None
        assert stress.get("isCodable") == "true"
        assert sub.get("isCodable") == "true"
        assert deep.get("isCodable") == "true"

        # oracle: master's import linkage reconstructs the fixture graph
        linkage = _replay_master_import_linkage(codes_elem)
        assert linkage["Deep sub"] == "Sub stress"
        assert linkage["Sub stress"] == "Stress"
        assert linkage["Stress"] is None       # top ancestor: category code
