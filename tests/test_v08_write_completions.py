"""v0.8 Phase D1 — write-surface completions (contract D.1–D.5).

Annotations per groundtruth2/memos-journals.md §4 (insert-only-if-nonempty,
empty-memo-deletes-row, delete/edit by anid, date-on-edit, owner immutable),
merge_category per category-tree.md §9 (reparent-to-target + descendant
guard), create_case per schema-writes.md §5.1 (unique name, memo='',
attribute placeholders).
"""

import json
import time
import sqlite3
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server


def _row(project_path, sql, args=()):
    con = sqlite3.connect(str(Path(project_path) / "data.qda"))
    con.row_factory = sqlite3.Row
    r = con.execute(sql, args).fetchone()
    con.close()
    return r


def _exec(project_path, sql, args=()):
    con = sqlite3.connect(str(Path(project_path) / "data.qda"))
    con.execute(sql, args)
    con.commit()
    con.close()


# =============================================================================
# D.1 add_annotation
# =============================================================================

class TestAddAnnotation:

    def test_creates_with_owner_and_date(self, setup_server, qualcoder_db_path):
        out = json.loads(server.add_annotation(1, 0, 10, "check this opener",
                                               create_backup=False))
        assert out["success"] is True
        ann = out["annotation"]
        r = _row(qualcoder_db_path,
                 "SELECT fid, pos0, pos1, memo, owner FROM annotation "
                 "WHERE anid = ?", (ann["annotation_id"],))
        assert (r["fid"], r["pos0"], r["pos1"]) == (1, 0, 10)
        assert r["memo"] == "check this opener"
        assert r["owner"] == "TestCoder"  # project codername

    def test_empty_memo_refused_no_empty_state(self, setup_server,
                                               qualcoder_db_path):
        """§4.1: an annotation never exists with memo='' — the memo IS the
        annotation."""
        for empty in ("", "   "):
            out = json.loads(server.add_annotation(1, 0, 10, empty,
                                                   create_backup=False))
            assert "error" in out and "non-empty" in out["error"]
        assert _row(qualcoder_db_path,
                    "SELECT COUNT(*) FROM annotation")[0] == 0

    def test_unique_span_per_owner_precheck(self, setup_server,
                                            qualcoder_db_path):
        json.loads(server.add_annotation(1, 0, 10, "first",
                                         create_backup=False))
        out = json.loads(server.add_annotation(1, 0, 10, "second",
                                               create_backup=False))
        assert "already exists" in out["error"]
        assert "update_annotation" in out["error"]  # points to the edit path

    def test_position_validation(self, setup_server, qualcoder_db_path):
        assert "0 <= start_pos < end_pos" in json.loads(
            server.add_annotation(1, 10, 5, "x", create_backup=False))["error"]
        assert "exceeds file length" in json.loads(
            server.add_annotation(1, 0, 10_000, "x",
                                  create_backup=False))["error"]

    def test_media_source_refused(self, setup_server, qualcoder_db_path):
        _exec(qualcoder_db_path,
              "INSERT INTO source (id, name, fulltext, mediapath) "
              "VALUES (30, 'pic.jpg', NULL, '/images/pic.jpg')")
        server.switch_project(qualcoder_db_path)
        out = json.loads(server.add_annotation(30, 0, 5, "note",
                                               create_backup=False))
        assert "not a text source" in out["error"]

    def test_position_safety_relay_on_unsafe_file(self, setup_server,
                                                  qualcoder_db_path):
        _exec(qualcoder_db_path,
              "INSERT INTO source (id, name, fulltext) "
              "VALUES (31, 'emoji.txt', ?)", ("😀 grinning here today",))
        server.switch_project(qualcoder_db_path)
        out = json.loads(server.add_annotation(31, 2, 10, "note on unsafe",
                                               create_backup=False))
        assert out["success"] is True
        assert "position_safety_warning" in out

    def test_refused_while_qualcoder_open(self, setup_server,
                                          qualcoder_db_path):
        lock = Path(qualcoder_db_path) / "project_in_use.lock"
        lock.write_text(f"gemma\n{time.time()}", encoding="utf-8")
        try:
            out = json.loads(server.add_annotation(1, 0, 10, "note"))
            assert "open in QualCoder" in out["error"]
        finally:
            lock.unlink()


# =============================================================================
# D.2 update_annotation
# =============================================================================

class TestUpdateAnnotation:

    def _make(self, qualcoder_db_path) -> int:
        out = json.loads(server.add_annotation(1, 0, 10, "original note",
                                               create_backup=False))
        return out["annotation"]["annotation_id"]

    def test_updates_memo_and_date_not_owner_or_span(self, setup_server,
                                                     qualcoder_db_path):
        """§4.2: annotation is a date-on-edit object; owner and span are
        immutable."""
        anid = self._make(qualcoder_db_path)
        before = _row(qualcoder_db_path,
                      "SELECT owner, pos0, pos1 FROM annotation WHERE anid=?",
                      (anid,))
        out = json.loads(server.update_annotation(anid, "revised note",
                                                  create_backup=False))
        assert out["updated"] is True
        after = _row(qualcoder_db_path,
                     "SELECT memo, owner, pos0, pos1, date FROM annotation "
                     "WHERE anid=?", (anid,))
        assert after["memo"] == "revised note"
        assert after["owner"] == before["owner"]
        assert (after["pos0"], after["pos1"]) == (before["pos0"],
                                                  before["pos1"])
        assert after["date"]  # date refreshed (annotation is date-on-edit)

    def test_clearing_deletes_the_row(self, setup_server, qualcoder_db_path):
        """§4.3: clear = delete; QualCoder never keeps an empty annotation."""
        anid = self._make(qualcoder_db_path)
        out = json.loads(server.update_annotation(anid, "",
                                                  create_backup=False))
        assert out["deleted"] is True
        assert out["deleted_because_cleared"] is True
        assert _row(qualcoder_db_path,
                    "SELECT COUNT(*) FROM annotation WHERE anid=?",
                    (anid,))[0] == 0

    def test_unknown_anid(self, setup_server, qualcoder_db_path):
        out = json.loads(server.update_annotation(999, "x",
                                                  create_backup=False))
        assert "does not exist" in out["error"]


# =============================================================================
# D.3 delete_annotation (by anid, never pos0)
# =============================================================================

class TestDeleteAnnotation:

    def test_deletes_exactly_one_by_anid(self, setup_server,
                                         qualcoder_db_path):
        """§4.3 gotcha: two annotations sharing pos0 (different spans) —
        deleting one must never remove the other (the upstream
        delete-by-pos0 bug this surface must not replicate)."""
        a1 = json.loads(server.add_annotation(
            1, 0, 10, "note one", create_backup=False))["annotation"]
        a2 = json.loads(server.add_annotation(
            1, 0, 20, "note two — same pos0",
            create_backup=False))["annotation"]
        out = json.loads(server.delete_annotation(a1["annotation_id"],
                                                  create_backup=False))
        assert out["deleted"] is True
        remaining = _row(qualcoder_db_path,
                         "SELECT anid, memo FROM annotation")
        assert remaining["anid"] == a2["annotation_id"]
        assert remaining["memo"] == "note two — same pos0"

    def test_unknown_anid(self, setup_server, qualcoder_db_path):
        out = json.loads(server.delete_annotation(999, create_backup=False))
        assert "does not exist" in out["error"]


# =============================================================================
# D.4 merge_category
# =============================================================================

class TestMergeCategory:

    @pytest.fixture
    def tree(self, setup_server, qualcoder_db_path):
        """Category A(1, fixture) > B > C; code 1 in A, code 2 in B."""
        b = json.loads(server.create_category(
            "B", parent_category="Category A",
            create_backup=False))["category"]["id"]
        c = json.loads(server.create_category(
            "C", parent_category="B", create_backup=False))["category"]["id"]
        server.move_code_to_category(1, "Category A", create_backup=False)
        server.move_code_to_category(2, "B", create_backup=False)
        return qualcoder_db_path, {"A": 1, "B": b, "C": c}

    def test_preview_counts_without_writing(self, tree):
        project, ids = tree
        out = json.loads(server.merge_category(ids["B"],
                                               into_category="Category A"))
        assert out["requires_confirmation"] is True
        p = out["preview"]
        assert p["codes_reparented"] == 1        # code 2
        assert p["subcategories_reparented"] == 1  # C
        assert _row(project, "SELECT COUNT(*) FROM code_cat WHERE catid=?",
                    (ids["B"],))[0] == 1  # nothing written

    def test_merge_reparents_to_target_not_top_level(self, tree):
        """§9: merge is reparent-to-TARGET (delete_category is
        orphan-to-top-level — the two must differ)."""
        project, ids = tree
        codings_before = _row(project, "SELECT COUNT(*) FROM code_text")[0]
        out = json.loads(server.merge_category(ids["B"],
                                               into_category="Category A",
                                               confirm=True))
        assert out["success"] is True
        # source gone; code 2 now in A; C now under A
        assert _row(project, "SELECT COUNT(*) FROM code_cat WHERE catid=?",
                    (ids["B"],))[0] == 0
        assert _row(project, "SELECT catid FROM code_name WHERE cid=2")[0] == ids["A"]
        assert _row(project, "SELECT supercatid FROM code_cat WHERE catid=?",
                    (ids["C"],))[0] == ids["A"]
        # codings untouched
        assert _row(project, "SELECT COUNT(*) FROM code_text")[0] == codings_before

    def test_merge_into_top_level(self, tree):
        project, ids = tree
        out = json.loads(server.merge_category(ids["B"], confirm=True))
        assert out["success"] is True
        assert _row(project, "SELECT catid FROM code_name WHERE cid=2")[0] is None
        assert _row(project, "SELECT supercatid FROM code_cat WHERE catid=?",
                    (ids["C"],))[0] is None

    def test_merge_into_descendant_refused(self, tree):
        """§9 cycle guard: target must not be the source's descendant."""
        project, ids = tree
        out = json.loads(server.merge_category(ids["A"], into_category="C",
                                               confirm=True))
        assert "descendant" in out["error"]
        assert _row(project, "SELECT COUNT(*) FROM code_cat WHERE catid=1")[0] == 1

    def test_merge_into_self_refused(self, tree):
        project, ids = tree
        out = json.loads(server.merge_category(ids["B"], into_category="B",
                                               confirm=True))
        assert "into itself" in out["error"]

    def test_ambiguous_target_name_refused(self, tree):
        project, ids = tree
        server.create_category("theme", create_backup=False)
        server.create_category("Theme", create_backup=False)
        out = json.loads(server.merge_category(ids["B"],
                                               into_category="THEME"))
        assert "ambiguous" in out["error"].lower()


# =============================================================================
# D.5 create_case
# =============================================================================

class TestCreateCase:

    def test_creates_with_placeholders(self, setup_server, qualcoder_db_path):
        """§5.1: memo='' default, owner=codername, one empty attribute row
        per case attribute type (the fixture defines 'Age')."""
        out = json.loads(server.create_case("Dana", create_backup=False))
        assert out["success"] is True
        case = out["case"]
        assert case["attributes_created"] == 1  # fixture has case attr 'Age'
        r = _row(qualcoder_db_path,
                 "SELECT memo, owner FROM cases WHERE caseid = ?",
                 (case["id"],))
        assert r["memo"] == ""      # empty string, never NULL
        assert r["owner"] == "TestCoder"
        attr = _row(qualcoder_db_path,
                    "SELECT value, attr_type FROM attribute "
                    "WHERE name='Age' AND id = ? AND attr_type='case'",
                    (case["id"],))
        assert attr["value"] == ""  # placeholder

    def test_duplicate_name_refused(self, setup_server, qualcoder_db_path):
        out = json.loads(server.create_case("Case A", create_backup=False))
        assert "already exists" in out["error"]

    def test_whitespace_name_refused(self, setup_server, qualcoder_db_path):
        out = json.loads(server.create_case("   ", create_backup=False))
        assert "non-empty" in out["error"]

    def test_links_compose_with_case_tools(self, setup_server,
                                           qualcoder_db_path):
        """create_case -> link_file_to_case -> case analysis end to end."""
        case = json.loads(server.create_case("Dana",
                                             create_backup=False))["case"]
        out = json.loads(server.link_file_to_case(1, case_name="Dana",
                                                  create_backup=False))
        assert out["success"] is True
        codes = json.loads(server.get_codes_by_case(case["id"]))
        assert isinstance(codes, list) and len(codes) >= 1
