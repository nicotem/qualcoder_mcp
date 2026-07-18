"""QA round-3: adversarial verification of the session-start QualCoder check.

Feature under test: e9cc67a — analyze_for_coding asks (session still created,
banner + action_required), get_current_project is the re-check endpoint,
write tools keep the hard refusal. These tests extend the developer's 7
happy-path tests with the crafted-lock arsenal and gate-consistency sweeps.
"""

import json
import os
import time
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server
from qualcoder_mcp.database import (
    QUALCODER_LOCK_FILENAME,
    validate_qda_path,
)


FULLTEXT = "This is interview text. I feel stressed about deadlines. I cope by exercising."


def _lock(project_path) -> Path:
    return validate_qda_path(project_path).parent / QUALCODER_LOCK_FILENAME


def _fresh(project_path, holder="livecoder"):
    _lock(project_path).write_text(f"{holder}\n{time.time()}", encoding="utf-8")


def _stale(project_path, holder="crashed"):
    _lock(project_path).write_text(f"{holder}\n{time.time() - 31}", encoding="utf-8")


def _sid(out: str) -> str:
    return out.split("Session ID: `")[1].split("`")[0]


def _approved_session(project_path):
    sid = _sid(server.analyze_for_coding([1]))
    rec = json.loads(server.record_suggestions(sid, [{
        "file_id": 1, "code_name": "Stress",
        "start_pos": 24, "end_pos": 55, "segment_text": FULLTEXT[24:55],
    }]))
    assert rec["recorded_count"] == 1, rec
    server.update_suggestion_status(sid, approve=[rec["recorded"][0]["guid"]])
    return sid


# =============================================================================
# Crafted-lock arsenal against the session-start path
# =============================================================================

class TestSessionStartCraftedLocks:

    def test_empty_lock_treated_stale_no_banner(self, setup_server,
                                                qualcoder_db_path):
        _lock(qualcoder_db_path).write_text("", encoding="utf-8")
        try:
            out = server.analyze_for_coding([1])
            # QA6-1: qualcoder_open is ALWAYS present (false when clear) —
            # consistent with get_current_project; no banner, no directive
            env = json.loads(out)
            assert env["qualcoder_open"] is False
            assert "action_required" not in env
            assert "STOP" not in out
            assert "Session ID: `" in out          # session created normally
            cur = json.loads(server.get_current_project())
            assert cur["qualcoder_open"] is False
            assert cur["qualcoder_lock"]["state"] == "stale"
        finally:
            _lock(qualcoder_db_path).unlink()

    def test_garbage_bytes_lock_treated_stale(self, setup_server,
                                              qualcoder_db_path):
        _lock(qualcoder_db_path).write_bytes(b"\xff\xfe\x00garbage\x9c\n\xba\xdd")
        try:
            out = server.analyze_for_coding([1])   # must not raise
            assert "STOP" not in out
            cur = json.loads(server.get_current_project())
            assert cur["qualcoder_open"] is False
        finally:
            _lock(qualcoder_db_path).unlink()

    def test_huge_lock_capped_fast_and_stale(self, setup_server,
                                             qualcoder_db_path):
        """8 MiB single-line lock: the 4 KiB read cap (SEC S-3) must keep the
        session-start check fast and memory-safe, and treat it stale."""
        _lock(qualcoder_db_path).write_bytes(b"A" * (8 * 1024 * 1024))
        try:
            t0 = time.perf_counter()
            out = server.analyze_for_coding([1])
            elapsed = time.perf_counter() - t0
            assert "STOP" not in out
            assert "Session ID: `" in out
            assert elapsed < 3.0                    # retry sleep only, no slurp
        finally:
            _lock(qualcoder_db_path).unlink()

    def test_huge_lock_with_valid_header_still_active(self, setup_server,
                                                      qualcoder_db_path):
        """Valid two-line header followed by megabytes of junk: the capped
        read still sees the heartbeat -> correctly reported as OPEN."""
        payload = f"qc_user\n{time.time()}\n".encode() + b"B" * (2 * 1024 * 1024)
        _lock(qualcoder_db_path).write_bytes(payload)
        try:
            out = server.analyze_for_coding([1])
            assert "qualcoder_open: true" in out
            assert "qc_user" in out
        finally:
            _lock(qualcoder_db_path).unlink()

    def test_symlinked_lock_resolves_target_content(self, setup_server,
                                                    qualcoder_db_path, tmp_path):
        target = tmp_path / "elsewhere.lock"
        target.write_text(f"remote_user\n{time.time()}", encoding="utf-8")
        os.symlink(target, _lock(qualcoder_db_path))
        try:
            out = server.analyze_for_coding([1])
            assert "qualcoder_open: true" in out    # conservative: treat as open
            cur = json.loads(server.get_current_project())
            assert cur["qualcoder_open"] is True
            assert cur["qualcoder_lock"]["holder"] == "remote_user"
        finally:
            _lock(qualcoder_db_path).unlink()

    def test_broken_symlink_lock_treated_absent(self, setup_server,
                                                qualcoder_db_path, tmp_path):
        os.symlink(tmp_path / "ghost.lock", _lock(qualcoder_db_path))
        try:
            out = server.analyze_for_coding([1])
            assert "STOP" not in out
            cur = json.loads(server.get_current_project())
            assert cur["qualcoder_open"] is False
            assert "qualcoder_lock" not in cur      # absent, no noise
        finally:
            _lock(qualcoder_db_path).unlink()

    def test_future_timestamp_lock_is_active(self, setup_server,
                                             qualcoder_db_path):
        """Clock skew: a heartbeat 10 s in the future is within the 30 s
        window (negative age) — must read as OPEN, not crash."""
        _lock(qualcoder_db_path).write_text(f"skewed\n{time.time() + 10}", encoding="utf-8")
        try:
            out = server.analyze_for_coding([1])
            assert "qualcoder_open: true" in out
        finally:
            _lock(qualcoder_db_path).unlink()


# =============================================================================
# No bypass / no stale caching introduced by the session-start check
# =============================================================================

class TestNoBypassNoCaching:

    def test_lock_appearing_after_analyze_still_blocks_apply(
            self, setup_server, qualcoder_db_path):
        """The ask-at-session-start must not cache 'closed' state: a lock
        appearing later still triggers the hard write-time refusal."""
        sid = _approved_session(qualcoder_db_path)   # no lock anywhere here
        _fresh(qualcoder_db_path, holder="latecomer")
        try:
            out = json.loads(server.apply_codings(sid))
            assert "latecomer" in out["error"]
            # record_suggestions is session-file-only (no DB write) and by
            # design has no lock gate — but the re-check endpoint tells the
            # truth at this moment:
            cur = json.loads(server.get_current_project())
            assert cur["qualcoder_open"] is True
        finally:
            _lock(qualcoder_db_path).unlink()

    def test_lock_disappearing_after_analyze_allows_apply(
            self, setup_server, qualcoder_db_path):
        """Banner at session start must not poison the session: once the
        user closes QualCoder, the same session applies cleanly."""
        _fresh(qualcoder_db_path)
        out = server.analyze_for_coding([1])
        assert "qualcoder_open: true" in out
        sid = _sid(out)
        _lock(qualcoder_db_path).unlink()            # user closed QualCoder

        cur = json.loads(server.get_current_project())
        assert cur["qualcoder_open"] is False        # re-check endpoint agrees

        rec = json.loads(server.record_suggestions(sid, [{
            "file_id": 1, "code_name": "Stress",
            "start_pos": 24, "end_pos": 55, "segment_text": FULLTEXT[24:55],
        }]))
        server.update_suggestion_status(sid, approve=[rec["recorded"][0]["guid"]])
        assert "CODINGS APPLIED" in server.apply_codings(sid)

    def test_banner_session_is_fully_usable_after_close(self, setup_server,
                                                        qualcoder_db_path):
        """The session created under a banner is a normal session (no hidden
        flag): stale-ward transition also unblocks it."""
        _fresh(qualcoder_db_path)
        sid = _sid(server.analyze_for_coding([1]))
        _stale(qualcoder_db_path)                    # heartbeat aged out
        try:
            rec = json.loads(server.record_suggestions(sid, [{
                "file_id": 1, "code_name": "Coping",
                "segment_text": "I cope by exercising",
            }]))
            assert rec["recorded_count"] == 1
            server.update_suggestion_status(
                sid, approve=[rec["recorded"][0]["guid"]])
            assert "CODINGS APPLIED" in server.apply_codings(sid)
        finally:
            _lock(qualcoder_db_path).unlink()


# =============================================================================
# The full concurrency ladder from one seeded state
# =============================================================================

class TestConcurrencyLadder:

    def test_warn_ask_refuse_all_fire_from_one_lock(self, setup_server,
                                                    qualcoder_db_path):
        """One fresh lock: select_project WARNS, analyze_for_coding ASKS,
        every gated write tool REFUSES — and no gate was lost in the
        refactor."""
        # prepare an approved session and a backup before seeding the lock
        sid = _approved_session(qualcoder_db_path)
        json.loads(server.import_text_file("ladder_marker.txt", "content"))
        folder = Path(qualcoder_db_path)
        backup = sorted(folder.parent.glob(f"{folder.stem}_backup_*.qda"))[-1]

        _fresh(qualcoder_db_path, holder="ladder_user")
        try:
            # rung 1: select_project warns but succeeds
            sel = json.loads(server.select_project(qualcoder_db_path))
            assert sel["success"] is True
            assert "ladder_user" in sel["warning"]

            # rung 2: analyze_for_coding asks but creates the session
            out = server.analyze_for_coding([1])
            assert "qualcoder_open: true" in out
            assert "action_required" in out
            assert server.session_manager.session_exists(_sid(out))

            # rung 2.5: the re-check endpoint reports open + guidance
            cur = json.loads(server.get_current_project())
            assert cur["qualcoder_open"] is True
            assert "refused" in cur["qualcoder_lock"]["note"]

            # rung 3: every gated write tool refuses, naming the holder
            gated = [
                server.apply_codings(sid),
                server.import_text_file("ladder_refused.txt", "nope"),
                server.link_file_to_case(2, case_id=1),
                server.delete_coding(1),
                server.restore_backup(str(backup), confirm=True),
            ]
            for out in gated:
                parsed = json.loads(out)
                assert "ladder_user" in parsed["error"], parsed

            # session-side tools (no DB writes) still function under the lock
            rec = json.loads(server.record_suggestions(_sid(
                server.analyze_for_coding([1])), [{
                    "file_id": 1, "code_name": "Coping",
                    "segment_text": "I cope by exercising"}]))
            assert rec["recorded_count"] == 1
        finally:
            _lock(qualcoder_db_path).unlink()

    def test_get_current_project_stale_guidance_matches_reality(
            self, setup_server, qualcoder_db_path):
        """Stale detail says writes proceed — verify they actually do."""
        _stale(qualcoder_db_path)
        try:
            cur = json.loads(server.get_current_project())
            assert cur["qualcoder_open"] is False
            assert cur["qualcoder_lock"]["state"] == "stale"
            assert "proceed" in cur["qualcoder_lock"]["note"]
            sid = _approved_session(qualcoder_db_path)
            assert "CODINGS APPLIED" in server.apply_codings(sid)
        finally:
            _lock(qualcoder_db_path).unlink()
