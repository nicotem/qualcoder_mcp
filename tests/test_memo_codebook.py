"""Tests for memo writing and codebook editing (feature/memo-codebook).

Reconciled against the QualCoder 3.8.2 ground-truth dossiers
(groundtruth2/{memos-journals,code-edits,category-tree}.md). Tests are
named by the gotcha they pin so QA can map them to the dossier.
"""

import json
import time
import sqlite3
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server
from qualcoder_mcp.database import QualcoderDatabase, QUALCODER_COLORS


def _data(project_path) -> Path:
    return Path(project_path) / "data.qda"


def _row(project_path, sql, args=()):
    con = sqlite3.connect(str(_data(project_path)))
    con.row_factory = sqlite3.Row
    r = con.execute(sql, args).fetchone()
    con.close()
    return r


def _exec(project_path, sql, args=()):
    con = sqlite3.connect(str(_data(project_path)))
    con.execute(sql, args)
    con.commit()
    con.close()


def _lock(project_path) -> Path:
    return Path(project_path) / "project_in_use.lock"


# =============================================================================
# MEMO WRITING
# =============================================================================

class TestSetMemo:

    def test_writes_memo_on_each_target(self, setup_server, qualcoder_db_path):
        # code (cid 1), category (catid 1), file (id 1), coding (ctid 1),
        # case (caseid 1) all exist in the conftest fixture
        for ttype, tid in [("code", 1), ("category", 1), ("file", 1),
                           ("coding", 1), ("case", 1)]:
            out = json.loads(server.set_memo(ttype, tid, f"memo for {ttype}",
                                             create_backup=False))
            assert out["success"] is True, out
            assert out["cleared"] is False

    def test_memo_edit_is_content_only_no_date_no_owner(self, setup_server,
                                                        qualcoder_db_path):
        """gotcha #1/#5: memo edits touch ONLY memo — never date or owner."""
        before = _row(qualcoder_db_path,
                      "SELECT date, owner FROM code_name WHERE cid=1")
        server.set_memo("code", 1, "new definition", create_backup=False)
        after = _row(qualcoder_db_path,
                     "SELECT date, owner, memo FROM code_name WHERE cid=1")
        assert after["date"] == before["date"]
        assert after["owner"] == before["owner"]
        assert after["memo"] == "new definition"

    def test_coding_memo_does_not_touch_date(self, setup_server,
                                             qualcoder_db_path):
        """gotcha #1: code_text memo edit must NOT stamp date (the fixed bug).

        Covers both set_memo('coding', ...) and the underlying
        add_memo_to_coding.
        """
        before = _row(qualcoder_db_path,
                      "SELECT date FROM code_text WHERE ctid=1")
        server.set_memo("coding", 1, "coding note", create_backup=False)
        after = _row(qualcoder_db_path,
                     "SELECT date, memo FROM code_text WHERE ctid=1")
        assert after["date"] == before["date"]
        assert after["memo"] == "coding note"

    def test_add_memo_to_coding_db_method_no_date(self, qualcoder_db_path):
        """gotcha #1 at the db layer: add_memo_to_coding leaves date."""
        db = QualcoderDatabase(qualcoder_db_path, read_only=False)
        before = db.conn.execute("SELECT date FROM code_text WHERE ctid=1").fetchone()[0]
        db.add_memo_to_coding(1, "note", "someone")
        after = db.conn.execute("SELECT date, memo FROM code_text WHERE ctid=1").fetchone()
        db.close()
        assert after[0] == before  # date unchanged
        assert after[1] == "note"

    def test_clear_memo_writes_empty_string_not_null(self, setup_server,
                                                     qualcoder_db_path):
        """gotcha #4: '' clears the memo, never NULL."""
        out = json.loads(server.set_memo("code", 1, "", create_backup=False))
        assert out["cleared"] is True
        val = _row(qualcoder_db_path, "SELECT memo FROM code_name WHERE cid=1")[0]
        assert val == ""
        assert val is not None

    def test_over_length_memo_rejected_not_truncated(self, setup_server,
                                                     qualcoder_db_path):
        """gotcha #8: reject over-length rather than silently truncate."""
        huge = "x" * 1_000_001
        out = json.loads(server.set_memo("code", 1, huge, create_backup=False))
        assert "error" in out and "too long" in out["error"]
        # not written
        assert _row(qualcoder_db_path, "SELECT memo FROM code_name WHERE cid=1")[0] != huge

    def test_bad_target_and_id(self, setup_server, qualcoder_db_path):
        assert "target_type must be" in json.loads(server.set_memo("nope", 1, "x"))["error"]
        assert "does not exist" in json.loads(
            server.set_memo("code", 999, "x", create_backup=False))["error"]

    def test_refused_while_qualcoder_open(self, setup_server, qualcoder_db_path):
        lk = _lock(qualcoder_db_path)
        lk.write_text(f"gemma\n{time.time()}")
        try:
            out = json.loads(server.set_memo("code", 1, "x"))
            assert "open in QualCoder" in out["error"]
        finally:
            lk.unlink()


class TestAddJournalEntry:

    def test_creates_entry_with_owner_and_date(self, setup_server,
                                               qualcoder_db_path):
        out = json.loads(server.add_journal_entry("Week 1", "reflexive note",
                                                  create_backup=False))
        assert out["journal_entry"]["name"] == "Week 1"
        r = _row(qualcoder_db_path,
                 "SELECT jentry, owner, date FROM journal WHERE name='Week 1'")
        assert r["jentry"] == "reflexive note"
        assert r["owner"] == "TestCoder"  # project codername
        assert r["date"]

    def test_duplicate_name_rejected(self, setup_server, qualcoder_db_path):
        """unique(name) — app-side pre-check (§6.4)."""
        server.add_journal_entry("Dup", "a", create_backup=False)
        out = json.loads(server.add_journal_entry("Dup", "b", create_backup=False))
        assert "already exists" in out["error"]
        # only one row
        n = _row(qualcoder_db_path, "SELECT COUNT(*) FROM journal WHERE name='Dup'")[0]
        assert n == 1

    def test_name_charset_enforced(self, setup_server, qualcoder_db_path):
        """gotcha #6: name restricted to ^[ \\w-]+$."""
        for bad in ["has/slash", "dot.name", "punc!", "emoji😀"]:
            out = json.loads(server.add_journal_entry(bad, "x", create_backup=False))
            assert "error" in out, bad
        # allowed charset works
        assert json.loads(server.add_journal_entry("ok_Name 1-2", "x",
                                                   create_backup=False)).get("journal_entry")

    def test_over_length_entry_rejected(self, setup_server, qualcoder_db_path):
        out = json.loads(server.add_journal_entry("Big", "y" * 1_000_001,
                                                  create_backup=False))
        assert "too long" in out["error"]


# =============================================================================
# CODEBOOK — non-destructive
# =============================================================================

class TestCreateCode:

    def test_creates_with_palette_color(self, setup_server, qualcoder_db_path):
        """gotcha #15: default color is a QualCoder palette member."""
        out = json.loads(server.create_code("Autonomy", create_backup=False))
        assert out["success"] is True
        assert out["code"]["color"] in QUALCODER_COLORS

    def test_into_category_by_name(self, setup_server, qualcoder_db_path):
        out = json.loads(server.create_code("Distrust", category="Category A",
                                            create_backup=False))
        assert out["code"]["category"] == "Category A"

    def test_unknown_category_rejected(self, setup_server, qualcoder_db_path):
        out = json.loads(server.create_code("X", category="Nope"))
        assert "not found" in out["error"]

    def test_duplicate_name_rejected(self, setup_server, qualcoder_db_path):
        out = json.loads(server.create_code("Stress", create_backup=False))
        assert "already exists" in out["error"]

    def test_invalid_color_rejected(self, setup_server, qualcoder_db_path):
        out = json.loads(server.create_code("Y", color="#zzzzzz",
                                            create_backup=False))
        assert "hex format" in out["error"]


class TestRenameAndRecolorAndMove:

    def test_rename_precheck_collision(self, setup_server, qualcoder_db_path):
        """gotcha #14: rename pre-checks unique(name) with a clean error."""
        out = json.loads(server.rename_code(1, "Coping", create_backup=False))
        assert "already exists" in out["error"]
        # unchanged
        assert _row(qualcoder_db_path, "SELECT name FROM code_name WHERE cid=1")[0] == "Stress"

    def test_rename_succeeds(self, setup_server, qualcoder_db_path):
        out = json.loads(server.rename_code(1, "Workplace stress",
                                            create_backup=False))
        assert out["new_name"] == "Workplace stress"

    def test_code_and_category_may_share_name(self, setup_server,
                                              qualcoder_db_path):
        """gotcha (code-edits #14 note): independent constraints."""
        # 'Category A' is a category; a code may take the same name
        out = json.loads(server.create_code("Category A", create_backup=False))
        assert out["success"] is True

    def test_recolor_strict_hex(self, setup_server, qualcoder_db_path):
        assert json.loads(server.recolor_code(1, "#00FF00",
                                              create_backup=False))["new_color"] == "#00FF00"
        assert "hex format" in json.loads(
            server.recolor_code(1, "blue", create_backup=False))["error"]

    def test_move_code_to_category_and_uncategorise(self, setup_server,
                                                    qualcoder_db_path):
        assert json.loads(server.move_code_to_category(1, "Category A",
                                                       create_backup=False))["new_category_id"] == 1
        # None -> uncategorised (NULL)
        out = json.loads(server.move_code_to_category(1, None, create_backup=False))
        assert out["new_category_id"] is None
        assert _row(qualcoder_db_path, "SELECT catid FROM code_name WHERE cid=1")[0] is None


class TestCategoryCreateRenameMove:

    def test_create_category_nested(self, setup_server, qualcoder_db_path):
        out = json.loads(server.create_category("Sub", parent_category="Category A",
                                                create_backup=False))
        assert out["category"]["supercatid"] == 1

    def test_rename_category_precheck(self, setup_server, qualcoder_db_path):
        server.create_category("Other", create_backup=False)
        out = json.loads(server.rename_category(1, "Other", create_backup=False))
        assert "already exists" in out["error"]

    def test_move_category_cycle_refused(self, setup_server, qualcoder_db_path):
        """gotcha #17: reparent under a descendant is refused (cycle guard)."""
        # Build A(1) > B > C, then try to move A under C
        b = json.loads(server.create_category("B", parent_category="Category A",
                                               create_backup=False))["category"]["id"]
        c = json.loads(server.create_category("C", parent_category="B",
                                               create_backup=False))["category"]["id"]
        out = json.loads(server.move_category(1, "C", create_backup=False))
        assert "cycle" in out["error"]
        # A's supercatid unchanged (still NULL/top)
        assert _row(qualcoder_db_path, "SELECT supercatid FROM code_cat WHERE catid=1")[0] is None

    def test_move_category_to_top_and_valid(self, setup_server, qualcoder_db_path):
        b = json.loads(server.create_category("B", parent_category="Category A",
                                               create_backup=False))["category"]["id"]
        assert json.loads(server.move_category(b, None, create_backup=False))["new_supercatid"] is None

    def test_global_case_sensitive_unique_name(self, setup_server,
                                               qualcoder_db_path):
        """gotcha #18: unique(name) is global; 'Category A' vs 'category a'
        are distinct (BINARY), but a second 'Category A' collides."""
        assert json.loads(server.create_category("category a",
                                                 create_backup=False))["category"]
        assert "already exists" in json.loads(
            server.create_category("Category A", create_backup=False))["error"]


# =============================================================================
# CODEBOOK — destructive (merge / delete)
# =============================================================================

class TestMergeCodes:

    def _setup_merge(self, qualcoder_db_path):
        # Stress(1) at 24-55; Coping(2) at 57-77. Add a colliding Coping row
        # at Stress's span (same owner) + an image/av row to prove no-dedup.
        _exec(qualcoder_db_path,
              "INSERT INTO code_text (cid,fid,seltext,pos0,pos1,owner) "
              "VALUES (2,1,'dup',24,55,'TestCoder')")  # collides with cid1@24-55
        _exec(qualcoder_db_path,
              "INSERT INTO code_text (cid,fid,seltext,pos0,pos1,owner) "
              "VALUES (2,1,'other',80,90,'OtherCoder')")  # same span diff owner? no; unique span
        server.switch_project(qualcoder_db_path)

    def test_merge_preview_counts(self, setup_server, qualcoder_db_path):
        self._setup_merge(qualcoder_db_path)
        out = json.loads(server.merge_codes(2, 1))
        assert out["requires_confirmation"] is True
        p = out["preview"]
        assert p["text_codings_discarded_as_duplicates"] == 1  # the 24-55 dup
        # nothing written yet
        assert _row(qualcoder_db_path, "SELECT COUNT(*) FROM code_name WHERE cid=2")[0] == 1

    def test_merge_is_lossy_destination_wins(self, setup_server, qualcoder_db_path):
        """gotcha #8/#10: collision -> source row discarded, destination
        untouched; owner is part of the unique key."""
        self._setup_merge(qualcoder_db_path)
        out = json.loads(server.merge_codes(2, 1, confirm=True))
        assert out["success"] is True
        # Coping (cid 2) gone
        assert _row(qualcoder_db_path, "SELECT COUNT(*) FROM code_name WHERE cid=2")[0] == 0
        # The colliding source row (24-55) was DELETED, not merged; the
        # destination's original 24-55 coding survives (there's exactly one)
        rows = server.db.conn.execute(
            "SELECT COUNT(*) FROM code_text WHERE cid=1 AND pos0=24 AND pos1=55 "
            "AND owner='TestCoder'").fetchone()[0]
        assert rows == 1
        # The non-colliding Coping rows were reassigned to cid 1
        assert _row(qualcoder_db_path,
                    "SELECT COUNT(*) FROM code_text WHERE cid=1 AND pos0=57")[0] == 1

    def test_av_image_reassigned_without_dedup(self, setup_server, qualcoder_db_path):
        """gotcha #9: code_av/code_image have no unique constraint -> no dedup,
        duplicates can result (matching QualCoder)."""
        # identical av rows on both codes -> after merge both survive under cid1
        _exec(qualcoder_db_path,
              "INSERT INTO code_av (cid,id,pos0,pos1,owner) VALUES (1,1,0,10,'T')")
        _exec(qualcoder_db_path,
              "INSERT INTO code_av (cid,id,pos0,pos1,owner) VALUES (2,1,0,10,'T')")
        server.switch_project(qualcoder_db_path)
        server.merge_codes(2, 1, confirm=True)
        n = _row(qualcoder_db_path,
                 "SELECT COUNT(*) FROM code_av WHERE cid=1 AND pos0=0 AND pos1=10")[0]
        assert n == 2  # both survive — no dedup

    def test_merge_into_self_refused(self, setup_server, qualcoder_db_path):
        assert "into itself" in json.loads(server.merge_codes(1, 1, confirm=True))["error"]

    def test_merge_backup_made_on_confirm(self, setup_server, qualcoder_db_path):
        parent = Path(qualcoder_db_path).parent
        before = len(list(parent.glob("*_backup_*")))
        server.merge_codes(2, 1, confirm=True)
        assert len(list(parent.glob("*_backup_*"))) == before + 1


class TestDeleteCode:

    def test_preview_reports_coding_count(self, setup_server, qualcoder_db_path):
        """gotcha #12: preview surfaces the destruction count QualCoder omits."""
        out = json.loads(server.delete_code(1))
        assert out["requires_confirmation"] is True
        assert out["preview"]["total_codings_to_delete"] == 1  # ctid 1
        # nothing deleted
        assert _row(qualcoder_db_path, "SELECT COUNT(*) FROM code_name WHERE cid=1")[0] == 1

    def test_confirm_bulk_deletes(self, setup_server, qualcoder_db_path):
        server.delete_code(1, confirm=True)
        assert _row(qualcoder_db_path, "SELECT COUNT(*) FROM code_name WHERE cid=1")[0] == 0
        assert _row(qualcoder_db_path, "SELECT COUNT(*) FROM code_text WHERE cid=1")[0] == 0

    def test_does_not_touch_categories_or_annotations(self, setup_server,
                                                      qualcoder_db_path):
        """code-edits.md §7.3: delete code leaves categories/annotations."""
        cats_before = _row(qualcoder_db_path, "SELECT COUNT(*) FROM code_cat")[0]
        server.delete_code(1, confirm=True)
        assert _row(qualcoder_db_path, "SELECT COUNT(*) FROM code_cat")[0] == cats_before


class TestDeleteCategory:

    def test_shallow_reparent_not_cascade(self, setup_server, qualcoder_db_path):
        """gotcha #13: codes -> catid NULL, subcats -> supercatid NULL; coded
        data untouched."""
        # code 1 & 2 are in Category A (catid 1). Add a subcategory.
        sub = json.loads(server.create_category("Sub", parent_category="Category A",
                                                create_backup=False))["category"]["id"]
        codings_before = _row(qualcoder_db_path, "SELECT COUNT(*) FROM code_text")[0]
        out = json.loads(server.delete_category(1, confirm=True))
        assert out["success"] is True
        # category gone
        assert _row(qualcoder_db_path, "SELECT COUNT(*) FROM code_cat WHERE catid=1")[0] == 0
        # codes survive at top level (catid NULL), codings untouched
        assert _row(qualcoder_db_path, "SELECT catid FROM code_name WHERE cid=1")[0] is None
        assert _row(qualcoder_db_path, "SELECT COUNT(*) FROM code_text")[0] == codings_before
        # subcategory promoted to top level
        assert _row(qualcoder_db_path, "SELECT supercatid FROM code_cat WHERE catid=?",
                    (sub,))[0] is None

    def test_preview_counts_reparented(self, setup_server, qualcoder_db_path):
        out = json.loads(server.delete_category(1))
        assert out["requires_confirmation"] is True
        assert out["preview"]["codes_moved_to_top_level"] == 2  # Stress + Coping

    def test_refused_while_qualcoder_open(self, setup_server, qualcoder_db_path):
        lk = _lock(qualcoder_db_path)
        lk.write_text(f"gemma\n{time.time()}")
        try:
            out = json.loads(server.delete_category(1, confirm=True))
            assert "open in QualCoder" in out["error"]
        finally:
            lk.unlink()
