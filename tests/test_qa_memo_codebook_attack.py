"""QA adversarial round: attacks on the memo/codebook tool surface.

Covers: destructive preview->confirm gate attacks, safety-backup
restorability, the heartbeat-lock refusal across ALL 12 new tools, TOCTOU
re-check, the v14 write gate, cycle-guard attacks on move_category,
unicode/hostile names, and session/loop interaction after merge/delete.
"""

import json
import shutil
import sqlite3
import time
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server
from qualcoder_mcp.database import (
    QualcoderDatabase,
    QUALCODER_LOCK_FILENAME,
    validate_qda_path,
)


FULLTEXT = "This is interview text. I feel stressed about deadlines. I cope by exercising."


def _db(project_path) -> Path:
    return Path(project_path) / "data.qda"


def _exec(project_path, sql, args=()):
    conn = sqlite3.connect(str(_db(project_path)))
    conn.execute(sql, args)
    conn.commit()
    conn.close()


def _one(project_path, sql, args=()):
    conn = sqlite3.connect(str(_db(project_path)))
    r = conn.execute(sql, args).fetchone()
    conn.close()
    return r


def _dump(project_path) -> list:
    conn = sqlite3.connect(str(_db(project_path)))
    lines = list(conn.iterdump())
    conn.close()
    return lines


def _backups(project_path):
    folder = Path(project_path)
    return sorted(folder.parent.glob(f"{folder.stem}_backup_*.qda"))


def _lock(project_path) -> Path:
    return validate_qda_path(project_path).parent / QUALCODER_LOCK_FILENAME


def _reload():
    server.switch_project(server.current_project_path)


# =============================================================================
# Destructive gates
# =============================================================================

class TestDestructiveGates:

    def test_preview_mutates_nothing_all_three(self, setup_server,
                                               qualcoder_db_path):
        before = _dump(qualcoder_db_path)
        n_backups = len(_backups(qualcoder_db_path))
        for out in (server.merge_codes(1, 2),
                    server.delete_code(1),
                    server.delete_category(1)):
            parsed = json.loads(out)
            assert parsed["requires_confirmation"] is True
            assert "preview" in parsed and "hint" in parsed
        assert _dump(qualcoder_db_path) == before
        assert len(_backups(qualcoder_db_path)) == n_backups  # previews are free

    def test_stale_preview_confirm_reports_fresh_counts(
            self, setup_server, qualcoder_db_path):
        """Counts shown at preview time must not be replayed stale at
        confirm time: the applied result reflects the CURRENT database."""
        preview = json.loads(server.delete_code(1))["preview"]
        assert preview["text_codings_to_delete"] == 1
        # the world changes between preview and confirm
        _exec(qualcoder_db_path,
              "INSERT INTO code_text (cid, fid, seltext, pos0, pos1, owner) "
              "VALUES (1, 1, ?, 0, 12, 'late_arrival')", (FULLTEXT[0:12],))
        _reload()
        out = json.loads(server.delete_code(1, confirm=True))
        assert out["success"] is True
        assert out["text_codings_to_delete"] == 2      # fresh, not stale
        assert _one(qualcoder_db_path,
                    "SELECT COUNT(*) FROM code_text WHERE cid = 1")[0] == 0

    def test_double_confirm_is_a_clean_no_target_error(self, setup_server):
        assert json.loads(server.merge_codes(1, 2, confirm=True))["success"]
        again = json.loads(server.merge_codes(1, 2, confirm=True))
        assert "error" in again and "does not exist" in again["error"]
        # delete after merge: same clean error, no traceback
        gone = json.loads(server.delete_code(1, confirm=True))
        assert "error" in gone and "does not exist" in gone["error"]

    def test_confirm_true_without_prior_preview_executes_with_backup(
            self, setup_server, qualcoder_db_path):
        """The gate is stateless BY DESIGN (same as restore_backup): a direct
        confirm=true call executes — but always behind a fresh backup. Pin
        the backup-always property, which is the actual safety net."""
        n = len(_backups(qualcoder_db_path))
        out = json.loads(server.delete_code(2, confirm=True))
        assert out["success"] is True
        assert "backup_path" in out
        assert len(_backups(qualcoder_db_path)) == n + 1

    def test_safety_backup_is_restorable_to_premerge_state(
            self, setup_server, qualcoder_db_path):
        pre = _dump(qualcoder_db_path)
        out = json.loads(server.merge_codes(1, 2, confirm=True))
        backup_path = out["backup_path"]
        assert _dump(qualcoder_db_path) != pre         # merge really happened
        restored = json.loads(server.restore_backup(backup_path, confirm=True))
        assert restored["success"] is True
        assert _dump(qualcoder_db_path) == pre         # bit-true recovery

    def test_invalid_targets_cost_nothing(self, setup_server,
                                          qualcoder_db_path):
        n = len(_backups(qualcoder_db_path))
        assert "error" in json.loads(server.merge_codes(1, 1, confirm=True))
        assert "error" in json.loads(server.merge_codes(1, 424242, confirm=True))
        assert "error" in json.loads(server.merge_codes(424242, 1, confirm=True))
        assert "error" in json.loads(server.delete_code(424242, confirm=True))
        assert "error" in json.loads(server.delete_category(424242, confirm=True))
        assert len(_backups(qualcoder_db_path)) == n   # no backup litter


# =============================================================================
# Heartbeat lock + TOCTOU + v14 gate across the new surface
# =============================================================================

class TestWriteDisciplineAcrossNewSurface:

    ALL_12 = [
        ("set_memo", lambda: server.set_memo("code", 1, "locked out")),
        ("add_journal_entry", lambda: server.add_journal_entry("Lock probe", "x")),
        ("create_code", lambda: server.create_code("Lock probe code")),
        ("rename_code", lambda: server.rename_code(1, "Lock renamed")),
        ("recolor_code", lambda: server.recolor_code(1, "#F5F6CE")),
        ("move_code_to_category", lambda: server.move_code_to_category(1)),
        ("create_category", lambda: server.create_category("Lock probe cat")),
        ("rename_category", lambda: server.rename_category(1, "Lock renamed cat")),
        ("move_category", lambda: server.move_category(1)),
        ("merge_codes", lambda: server.merge_codes(1, 2, confirm=True)),
        ("delete_code", lambda: server.delete_code(1, confirm=True)),
        ("delete_category", lambda: server.delete_category(1, confirm=True)),
    ]

    def test_fresh_lock_refuses_all_12_no_mutation_no_backup(
            self, setup_server, qualcoder_db_path):
        before = _dump(qualcoder_db_path)
        n_backups = len(_backups(qualcoder_db_path))
        lock = _lock(qualcoder_db_path)
        lock.write_text(f"gui_user\n{time.time()}", encoding="utf-8")
        try:
            for name, call in self.ALL_12:
                out = json.loads(call())
                assert "error" in out, name
                assert "gui_user" in out["error"], (name, out)
        finally:
            lock.unlink()
        assert _dump(qualcoder_db_path) == before
        assert len(_backups(qualcoder_db_path)) == n_backups

    def test_toctou_lock_appearing_mid_write_rolls_back(
            self, setup_server, qualcoder_db_path, monkeypatch):
        """Stale foreign lock -> write proceeds unheld; QualCoder 'opening'
        between the mutation and the commit must abort with rollback."""
        lock = _lock(qualcoder_db_path)
        lock.write_text(f"crashed\n{time.time() - 31}", encoding="utf-8")

        original = QualcoderDatabase.set_memo

        def mutate_then_qualcoder_arrives(self, *a, **k):
            result = original(self, *a, **k)
            lock.write_text(f"qc_live\n{time.time()}", encoding="utf-8")
            return result

        monkeypatch.setattr(QualcoderDatabase, "set_memo",
                            mutate_then_qualcoder_arrives)
        try:
            out = json.loads(server.set_memo("code", 1, "toctou probe"))
            assert "error" in out and "qc_live" in out["error"]
            assert _one(qualcoder_db_path,
                        "SELECT memo FROM code_name WHERE cid = 1")[0] \
                == "Stress code"                       # rolled back
            # the foreign lock is not ours: left in place
            assert lock.read_text(encoding="utf-8").startswith("qc_live")
        finally:
            lock.unlink()

    def test_v14_gate_refuses_writes_on_old_version_string(
            self, setup_server, qualcoder_db_path, tmp_path):
        old = tmp_path / "old_version.qda"
        shutil.copytree(qualcoder_db_path, old)
        conn = sqlite3.connect(str(old / "data.qda"))
        conn.execute("DROP TABLE coder_names")  # REAL pre-v14 (probe gate, S1)
        conn.execute("UPDATE project SET databaseversion = 'v13'")
        conn.commit()
        conn.close()
        assert json.loads(server.select_project(str(old)))["success"] is True

        n_backups = len(sorted(old.parent.glob(f"{old.stem}_backup_*.qda")))
        for name, call in [("set_memo", lambda: server.set_memo("code", 1, "x")),
                           ("merge_codes", lambda: server.merge_codes(1, 2, confirm=True)),
                           ("delete_category", lambda: server.delete_category(1, confirm=True))]:
            out = json.loads(call())
            assert "error" in out and "pre-v14" in out["error"], name
        assert len(sorted(old.parent.glob(f"{old.stem}_backup_*.qda"))) == n_backups

    def test_connection_read_only_after_every_new_tool(self, setup_server,
                                                       qualcoder_db_path):
        json.loads(server.set_memo("code", 1, "x"))
        json.loads(server.create_code("RO check"))
        json.loads(server.merge_codes(1, 2, confirm=True))
        json.loads(server.delete_category(1, confirm=True))
        assert server.db is not None and server.db.read_only


# =============================================================================
# Cycle-guard attacks on move_category
# =============================================================================

class TestCycleGuardAttack:

    def _build_chain(self, qualcoder_db_path):
        """Category chain: 1 (fixture, top) <- 2 <- 3 <- 4, plus sibling 5."""
        for catid, name, parent in ((2, "B", 1), (3, "C", 2), (4, "D", 3),
                                    (5, "Sibling", None)):
            _exec(qualcoder_db_path,
                  "INSERT INTO code_cat (catid, name, supercatid) VALUES (?,?,?)",
                  (catid, name, parent))
        _reload()

    def test_direct_and_deep_cycles_refused(self, setup_server,
                                            qualcoder_db_path):
        self._build_chain(qualcoder_db_path)
        attacks = [
            (1, "Category A"),   # self-parent
            (1, "B"),            # direct cycle: child as parent
            (1, "D"),            # deep cycle via great-grandchild (A->B->C->D->A)
            (2, "C"),            # A->B under B's child C
            (2, "D"),            # under own grandchild
        ]
        for catid, parent_name in attacks:
            out = json.loads(server.move_category(catid, parent_name))
            assert "error" in out, (catid, parent_name)
            assert "cycle" in out["error"] or "ancestor" in out["error"], out
        # tree unchanged after all attacks
        rows = {r[0]: r[1] for r in sqlite3.connect(str(_db(qualcoder_db_path)))
                .execute("SELECT catid, supercatid FROM code_cat")}
        assert rows == {1: None, 2: 1, 3: 2, 4: 3, 5: None}

    def test_qualcoder_legal_moves_still_work(self, setup_server,
                                              qualcoder_db_path):
        self._build_chain(qualcoder_db_path)
        # move a subtree between branches: D under Sibling
        assert json.loads(server.move_category(4, "Sibling"))["success"] is True
        # move to top level (NULL)
        assert json.loads(server.move_category(3))["success"] is True
        # re-attach the now-top-level C under D (D is no longer C's descendant
        # after the move above — must be allowed)
        assert json.loads(server.move_category(3, "D"))["success"] is True
        rows = {r[0]: r[1] for r in sqlite3.connect(str(_db(qualcoder_db_path)))
                .execute("SELECT catid, supercatid FROM code_cat")}
        assert rows[4] == 5 and rows[3] == 4

    def test_pre_existing_cycle_treated_unsafe_not_hang(
            self, setup_server, qualcoder_db_path):
        """Corrupt data (a cycle written by another tool) must neither hang
        the ancestor walk nor be extended further."""
        _exec(qualcoder_db_path,
              "INSERT INTO code_cat (catid, name, supercatid) VALUES (2, 'B', 3)")
        _exec(qualcoder_db_path,
              "INSERT INTO code_cat (catid, name, supercatid) VALUES (3, 'C', 2)")
        _reload()
        # attaching anything under a member of the corrupt loop must refuse
        # (walk detects the repeat and treats it as unsafe), quickly
        t0 = time.perf_counter()
        out = json.loads(server.move_category(1, "B"))
        assert (time.perf_counter() - t0) < 2.0
        assert "error" in out

    def test_move_code_is_not_cycle_constrained(self, setup_server,
                                                qualcoder_db_path):
        """Codes are leaves: moving a code anywhere legal must never trip the
        category cycle guard."""
        self._build_chain(qualcoder_db_path)
        assert json.loads(server.move_code_to_category(1, "D"))["success"] is True
        assert json.loads(server.move_code_to_category(1))["success"] is True


# =============================================================================
# Unicode / hostile inputs
# =============================================================================

class TestUnicodeAndHostileNames:

    def test_emoji_and_cjk_names_and_memos_roundtrip(self, setup_server,
                                                     qualcoder_db_path):
        out = json.loads(server.create_code("Stress 😰 反応",
                                            memo="memo 🎯 with émojis\nsecond line"))
        assert out["success"] is True
        cid = out["code"]["id"]
        assert _one(qualcoder_db_path,
                    "SELECT name FROM code_name WHERE cid = ?",
                    (cid,))[0] == "Stress 😰 反応"
        json.loads(server.set_memo("code", cid, "更新 🔥 memo"))
        assert _one(qualcoder_db_path,
                    "SELECT memo FROM code_name WHERE cid = ?",
                    (cid,))[0] == "更新 🔥 memo"
        assert json.loads(server.rename_code(cid, "توتر — stress"))["success"] is True
        assert json.loads(server.create_category("Catégorie 📁"))["success"] is True
        assert json.loads(
            server.move_code_to_category(cid, "catégorie 📁"))["success"] is True

    def test_sql_metacharacter_names(self, setup_server, qualcoder_db_path):
        hostile = "Robert'); DROP TABLE code_name;--"
        out = json.loads(server.create_code(hostile))
        assert out["success"] is True
        assert json.loads(server.rename_category(1, "cat'; DELETE--"))["success"]
        assert json.loads(server.set_memo(
            "code", out["code"]["id"], "memo with '; -- and \"quotes\""))["success"]
        # tables intact
        assert _one(qualcoder_db_path, "SELECT COUNT(*) FROM code_name")[0] >= 3

    def test_merge_by_ids_unaffected_by_hostile_names(self, setup_server,
                                                      qualcoder_db_path):
        hostile = json.loads(server.create_code("evil'); --"))["code"]["id"]
        out = json.loads(server.merge_codes(1, hostile, confirm=True))
        assert out["success"] is True
        assert _one(qualcoder_db_path,
                    "SELECT COUNT(*) FROM code_text WHERE cid = ?",
                    (hostile,))[0] == 1


# =============================================================================
# Session / AI-coding-loop interaction with codebook edits
# =============================================================================

class TestSessionInteraction:

    def _pending_approved_suggestion(self):
        sid = server.analyze_for_coding([1]).split("Session ID: `")[1].split("`")[0]
        rec = json.loads(server.record_suggestions(sid, [{
            "file_id": 1, "code_name": "Stress",
            "start_pos": 24, "end_pos": 55, "segment_text": FULLTEXT[24:55],
        }]))
        guid = rec["recorded"][0]["guid"]
        server.update_suggestion_status(sid, approve=[guid])
        return sid, guid

    def test_merge_invalidates_pending_suggestion_cleanly(
            self, setup_server, qualcoder_db_path):
        """A suggestion recorded against code X, approved, then X merged
        away: apply must refuse cleanly (stale code_id), write nothing,
        create no backup — not corrupt the destination code."""
        sid, guid = self._pending_approved_suggestion()
        assert json.loads(server.merge_codes(1, 2, confirm=True))["success"]

        n_backups = len(_backups(qualcoder_db_path))
        before = _one(qualcoder_db_path, "SELECT COUNT(*) FROM code_text")[0]
        out = json.loads(server.apply_codings(sid))
        assert "error" in out
        assert out["failures"][0]["guid"] == guid
        assert "code_id 1 does not exist" in out["failures"][0]["reason"]
        assert _one(qualcoder_db_path,
                    "SELECT COUNT(*) FROM code_text")[0] == before
        assert len(_backups(qualcoder_db_path)) == n_backups

    def test_delete_code_invalidates_pending_suggestion_cleanly(
            self, setup_server, qualcoder_db_path):
        sid, guid = self._pending_approved_suggestion()
        assert json.loads(server.delete_code(1, confirm=True))["success"]
        out = json.loads(server.apply_codings(sid))
        assert "error" in out and out["failures"][0]["guid"] == guid

    def test_record_after_delete_rejects_with_available_codes(
            self, setup_server):
        assert json.loads(server.delete_code(1, confirm=True))["success"]
        sid = server.analyze_for_coding([1]).split("Session ID: `")[1].split("`")[0]
        rec = json.loads(server.record_suggestions(sid, [{
            "file_id": 1, "code_name": "Stress",
            "segment_text": FULLTEXT[24:55],
        }]))
        assert rec["recorded_count"] == 0
        assert "not found" in rec["rejected"][0]["reason"]
        assert "available_codes" in rec["rejected"][0]

    def test_rename_between_record_and_apply_is_harmless(
            self, setup_server, qualcoder_db_path):
        """Suggestions bind by code_id: renaming the code must not break an
        approved suggestion (it applies under the new name)."""
        sid, guid = self._pending_approved_suggestion()
        assert json.loads(server.rename_code(1, "Strain"))["success"]
        result = server.apply_codings(sid)
        assert "CODINGS APPLIED" in result
        assert _one(qualcoder_db_path,
                    "SELECT COUNT(*) FROM code_text WHERE cid = 1 "
                    "AND owner = 'AI Coding Assistant'")[0] == 1


# =============================================================================
# Ambiguity finding (open): case-colliding names resolve silently
# =============================================================================

class TestNameResolutionAmbiguity:

    def test_qa5_1_case_ambiguous_category_refused(self, setup_server,
                                                   qualcoder_db_path):
        assert json.loads(server.create_category("Theme"))["success"]
        assert json.loads(server.create_category("theme"))["success"]
        out = json.loads(server.move_code_to_category(1, "THEME"))
        # desired: explicit refusal naming both candidates
        assert "error" in out and "ambiguous" in out["error"].lower()
