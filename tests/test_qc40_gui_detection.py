"""P1-5: best-effort QC 4.0 GUI-open detection.

QualCoder 4.0 deleted project_in_use.lock (no matches in the pinned
tree at 9bddf17), so the lock gate is blind against a 4.0 GUI. These
tests pin the heuristic signals evaluated against the pinned source
(data.qda write sidecars; RECENT ACTIVITY on the WAL-mode AI search
index, whose sidecars exist only during in-flight vectorstore work or
after an unclean exit because every upstream connection is
per-operation and closed in a finally block, ai_vectorstore.py:472-477
and :899/978/1017/1086/1112/1136/1149; chat-history activity,
ai_chat.py:1682-1718; a guarded process scan) and their WARN-only
wiring into the ladder:
warn on select, ask at session start, never a hard refusal, with the
C7 fingerprints unchanged as the write-time backstop.
"""

import json
import os
import time
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server
import qualcoder_mcp.database as database
from qualcoder_mcp.database import (
    GUI_SIGNAL_FRESH_SECONDS,
    _filter_qualcoder_processes,
    qualcoder_gui_signals,
)


@pytest.fixture
def no_process_hits(monkeypatch):
    """Make the process-scan signal deterministic (no live scan)."""
    monkeypatch.setattr(database, "_qualcoder_process_hits", lambda: [])


def _old(path: Path):
    """Backdate a file well past the freshness window."""
    stale = time.time() - (GUI_SIGNAL_FRESH_SECONDS * 4)
    os.utime(path, (stale, stale))


# =============================================================================
# SIGNAL EVALUATION
# =============================================================================

class TestSignals:

    def test_clean_project_has_no_signals(self, tmp_path, no_process_hits):
        proj = tmp_path / "clean.qda"
        proj.mkdir()
        (proj / "data.qda").write_bytes(b"db")
        assert qualcoder_gui_signals(proj) == []

    def test_data_qda_journal_signals_active_write(self, tmp_path,
                                                   no_process_hits):
        proj = tmp_path / "p.qda"
        proj.mkdir()
        (proj / "data.qda").write_bytes(b"db")
        (proj / "data.qda-journal").write_bytes(b"j")
        signals = qualcoder_gui_signals(proj)
        assert len(signals) == 1
        assert "data.qda-journal" in signals[0]
        assert "active or interrupted write" in signals[0]

    def test_fresh_search_index_wal_signals_recent_activity(
            self, tmp_path, no_process_hits):
        # A fresh sidecar means vectorstore work in flight or a recent
        # unclean exit; it is never phrased as an open window (F2/F17)
        proj = tmp_path / "p.qda"
        (proj / "ai_data").mkdir(parents=True)
        (proj / "ai_data" / "search.sqlite-wal").write_bytes(b"w")
        signals = qualcoder_gui_signals(proj)
        assert len(signals) == 1
        assert "search.sqlite-wal" in signals[0]
        assert "recently written" in signals[0]
        assert "is open" not in signals[0]
        assert "unclean" in signals[0]

    def test_stale_search_index_wal_softens_wording(self, tmp_path,
                                                    no_process_hits):
        proj = tmp_path / "p.qda"
        (proj / "ai_data").mkdir(parents=True)
        wal = proj / "ai_data" / "search.sqlite-wal"
        wal.write_bytes(b"w")
        _old(wal)
        signals = qualcoder_gui_signals(proj)
        assert len(signals) == 1
        assert "leftover" in signals[0]
        assert "unclean exit" in signals[0]
        assert "open window" not in signals[0]

    def test_recent_chat_history_signals_activity(self, tmp_path,
                                                  no_process_hits):
        proj = tmp_path / "p.qda"
        (proj / "ai_data").mkdir(parents=True)
        (proj / "ai_data" / "chat_history.sqlite").write_bytes(b"c")
        signals = qualcoder_gui_signals(proj)
        assert len(signals) == 1
        assert "chat history" in signals[0]

    def test_old_chat_history_is_silent(self, tmp_path, no_process_hits):
        proj = tmp_path / "p.qda"
        (proj / "ai_data").mkdir(parents=True)
        chat = proj / "ai_data" / "chat_history.sqlite"
        chat.write_bytes(b"c")
        _old(chat)
        assert qualcoder_gui_signals(proj) == []

    def test_process_hits_become_a_signal(self, tmp_path, monkeypatch):
        monkeypatch.setattr(database, "_qualcoder_process_hits",
                            lambda: ["python qualcoder"])
        proj = tmp_path / "p.qda"
        proj.mkdir()
        signals = qualcoder_gui_signals(proj)
        assert len(signals) == 1
        assert "looks like QualCoder is running" in signals[0]

    def test_signals_combine(self, tmp_path, monkeypatch):
        monkeypatch.setattr(database, "_qualcoder_process_hits",
                            lambda: ["python qualcoder"])
        proj = tmp_path / "p.qda"
        (proj / "ai_data").mkdir(parents=True)
        (proj / "data.qda-journal").write_bytes(b"j")
        (proj / "ai_data" / "search.sqlite-wal").write_bytes(b"w")
        (proj / "ai_data" / "chat_history.sqlite").write_bytes(b"c")
        assert len(qualcoder_gui_signals(proj)) == 4

    def test_never_raises_on_garbage_input(self, no_process_hits):
        # Nonexistent path, and a path value that cannot stat
        assert qualcoder_gui_signals("/nonexistent/nowhere.qda") == []
        assert qualcoder_gui_signals("\x00bad" if os.name != "nt"
                                     else "bad<>path") == []

    def test_wording_is_never_certain(self, tmp_path, monkeypatch):
        # Every signal at once (hot journal, fresh WAL, fresh chat
        # store, process hit): none may claim an open window as fact
        monkeypatch.setattr(database, "_qualcoder_process_hits",
                            lambda: ["qualcoder gui"])
        proj = tmp_path / "p.qda"
        (proj / "ai_data").mkdir(parents=True)
        (proj / "data.qda-journal").write_bytes(b"j")
        (proj / "ai_data" / "search.sqlite-wal").write_bytes(b"w")
        (proj / "ai_data" / "chat_history.sqlite").write_bytes(b"c")
        signals = qualcoder_gui_signals(proj)
        assert len(signals) == 4
        for signal in signals:
            assert "is open" not in signal
            assert "is open in QualCoder" not in signal


class TestProcessFilter:

    def test_own_server_spellings_do_not_match(self):
        lines = [
            "/venv/bin/python -m qualcoder_mcp",
            "qualcoder-mcp --stdio",
            "/Users/x/GitHub/qualcoder_mcp/venv/bin/python -m pytest",
            "some editor /home/y/qualcoder-mcp/README.md",
        ]
        assert _filter_qualcoder_processes(lines) == []

    def test_real_qualcoder_matches(self):
        lines = [
            "python3 -m qualcoder",
            "C:\\Python\\Scripts\\qualcoder.exe",
            "/usr/bin/python3 /usr/local/bin/qualcoder",
        ]
        assert len(_filter_qualcoder_processes(lines)) == 3

    def test_mixed_lines_filtered_correctly(self):
        lines = ["python -m qualcoder",
                 "/x/qualcoder_mcp/venv/bin/python -m pytest",
                 "unrelated process"]
        hits = _filter_qualcoder_processes(lines)
        assert hits == ["python -m qualcoder"]

    def test_garbage_lines_tolerated(self):
        assert _filter_qualcoder_processes([None, 42, b"bytes"]) == []

    def test_scan_never_raises(self, monkeypatch):
        # Whatever the platform offers, the scan returns a list
        database._process_scan_cache["at"] = 0.0
        hits = database._qualcoder_process_hits()
        assert isinstance(hits, list)


# =============================================================================
# LADDER WIRING (WARN on select, ask at session start, never a refusal)
# =============================================================================

class TestLadderWiring:

    def test_select_project_warns_on_signals(self, setup_server,
                                             qualcoder_db_path, tmp_path,
                                             no_process_hits):
        import shutil
        dest = tmp_path / "warn.qda"
        shutil.copytree(qualcoder_db_path, dest)
        (dest / "ai_data").mkdir()
        (dest / "ai_data" / "search.sqlite-wal").write_bytes(b"w")
        out = json.loads(server.select_project(str(dest)))
        assert out["success"] is True
        assert out["qualcoder_gui_signals"]
        assert "APPEARS to be open" in out["warning"]
        assert "reopened" in out["warning"]  # the 4.0 refresh limitation

    def test_select_project_hot_journal_names_open_gui(
            self, setup_server, qualcoder_db_path, tmp_path,
            no_process_hits):
        # A 4.0 window mid-write leaves a hot journal that makes the
        # read-only open fail like corruption would; the error must
        # point at the open GUI, not at a damaged database
        import shutil
        dest = tmp_path / "hot.qda"
        shutil.copytree(qualcoder_db_path, dest)
        (dest / "data.qda-journal").write_bytes(b"j")
        out = json.loads(server.select_project(str(dest)))
        assert out["success"] is False
        assert "APPEARS to be open" in out["error"]
        assert out["qualcoder_gui_signals"]

    def test_select_project_limitation_note_when_clean(
            self, setup_server, qualcoder_db_path, tmp_path,
            no_process_hits):
        import shutil
        dest = tmp_path / "clean.qda"
        shutil.copytree(qualcoder_db_path, dest)
        out = json.loads(server.select_project(str(dest)))
        assert out["qualcoder_gui_signals"] == []
        assert "best-effort" in out["warning"]

    def test_get_current_project_reports_signals(self, setup_server,
                                                 qualcoder_db_path,
                                                 no_process_hits):
        ai = Path(qualcoder_db_path) / "ai_data"
        ai.mkdir()
        (ai / "chat_history.sqlite").write_bytes(b"c")
        out = json.loads(server.get_current_project())
        assert out["qualcoder_gui_signals"]
        assert "APPEARS to be open" in out["qualcoder_gui_hint"]
        # Signals are WARN-level: the hard qualcoder_open stays false
        assert out["qualcoder_open"] is False

    def test_session_start_asks_on_signals(self, setup_server,
                                           qualcoder_db_path,
                                           no_process_hits):
        ai = Path(qualcoder_db_path) / "ai_data"
        ai.mkdir()
        (ai / "chat_history.sqlite").write_bytes(b"c")
        out = json.loads(server.analyze_for_coding([1]))
        assert out["qualcoder_open"] is False
        assert out["qualcoder_gui_signals"]
        assert "ASK THE USER" in out["qualcoder_gui_hint"]

    def test_restore_preview_reports_signals_and_asks(self, setup_server,
                                                      qualcoder_db_path,
                                                      no_process_hits):
        # The confirm=false preview is restore_backup's own ask rung:
        # it reports the heuristics and asks, and stays a preview
        # (QA round 1, F18)
        import shutil
        parent = Path(qualcoder_db_path).parent
        stem = Path(qualcoder_db_path).stem
        backup = parent / f"{stem}_backup_20260101_000001.qda"
        shutil.copytree(qualcoder_db_path, backup)
        ai = Path(qualcoder_db_path) / "ai_data"
        ai.mkdir()
        (ai / "chat_history.sqlite").write_bytes(b"c")
        out = json.loads(server.restore_backup(str(backup)))
        assert out["requires_confirmation"] is True
        assert out["qualcoder_gui_signals"]
        assert "APPEARS to be open" in out["qualcoder_gui_hint"]
        assert "ASK THE USER" in out["qualcoder_gui_hint"]

    def test_signals_do_not_refuse_writes(self, setup_server,
                                          qualcoder_db_path,
                                          no_process_hits):
        # WARN-level only: a heuristic signal must never hard-refuse a
        # write on its own (C7 fingerprints remain the backstop)
        ai = Path(qualcoder_db_path) / "ai_data"
        ai.mkdir()
        (ai / "search.sqlite-wal").write_bytes(b"w")
        out = json.loads(server.set_memo("code", 1, "still writable",
                                         create_backup=False))
        assert out["success"] is True


# =============================================================================
# SELECT_PROJECT FAILURE WORDING (QA round 1, F3/F22)
# =============================================================================

class TestSelectProjectFailureWording:
    """The appears-open rewrite fires only for a genuine sqlite-open
    failure, is decided on PROJECT-scoped evidence (the machine-wide
    process scan says nothing about this project), and never drops
    the recovery advice: a wrong path keeps its list_available_projects
    hint, and a database that will not open always keeps the
    damaged-database fallback."""

    @pytest.fixture
    def one_process_hit(self, monkeypatch):
        monkeypatch.setattr(database, "_qualcoder_process_hits",
                            lambda: ["python -m qualcoder"])

    def test_plain_directory_keeps_path_hint_despite_process_hit(
            self, setup_server, tmp_path, one_process_hit):
        out = json.loads(server.select_project(str(tmp_path)))
        assert out["success"] is False
        assert "list_available_projects" in out["error"]
        assert "APPEARS" not in out["error"]

    def test_qda_folder_without_data_keeps_path_hint(
            self, setup_server, tmp_path, one_process_hit):
        hollow = tmp_path / "hollow.qda"
        hollow.mkdir()
        out = json.loads(server.select_project(str(hollow)))
        assert out["success"] is False
        assert "list_available_projects" in out["error"]
        assert "APPEARS" not in out["error"]

    def test_corrupt_database_is_reported_as_damaged_not_open(
            self, setup_server, tmp_path, one_process_hit):
        proj = tmp_path / "corrupt.qda"
        proj.mkdir()
        (proj / "data.qda").write_bytes(b"this is not a sqlite database")
        out = json.loads(server.select_project(str(proj)))
        assert out["success"] is False
        assert "damaged" in out["error"]
        assert "backup" in out["error"]
        # a process hit alone is machine-wide evidence, not project-scoped
        assert "APPEARS" not in out["error"]

    def test_corrupt_database_with_stale_wal_keeps_damage_fallback(
            self, setup_server, tmp_path, no_process_hits):
        proj = tmp_path / "corrupt.qda"
        (proj / "ai_data").mkdir(parents=True)
        (proj / "data.qda").write_bytes(b"this is not a sqlite database")
        wal = proj / "ai_data" / "search.sqlite-wal"
        wal.write_bytes(b"w")
        _old(wal)
        out = json.loads(server.select_project(str(proj)))
        assert out["success"] is False
        assert "APPEARS to be open" in out["error"]
        assert "damaged database" in out["error"]
        assert "backup" in out["error"]

    def test_hot_journal_names_open_gui_and_keeps_fallback(
            self, setup_server, qualcoder_db_path, tmp_path,
            no_process_hits):
        import shutil
        dest = tmp_path / "hot.qda"
        shutil.copytree(qualcoder_db_path, dest)
        (dest / "data.qda-journal").write_bytes(b"j")
        out = json.loads(server.select_project(str(dest)))
        assert out["success"] is False
        assert "APPEARS to be open" in out["error"]
        assert "damaged database" in out["error"]
        assert out["qualcoder_gui_signals"]


# =============================================================================
# S-H4: DatabaseOpenError text outside select_project is generic
# =============================================================================

class TestDatabaseOpenErrorIsGeneric:
    """A data.qda that will not open (garbage bytes, or the hot journal a
    mid-write 4.0 window leaves) used to surface its raw sqlite message
    ("file is not a database", "attempt to write a readonly database")
    through every tool but select_project, via _tool_guard's ValueError
    branch and get_current_project's catch-all. Both now route to one
    fixed, path-free text; the sqlite text goes to the log only."""

    LEAKS = ("sqlite", "readonly", "not a database", "malformed",
             "Traceback")

    def _damage(self, project, how):
        if how == "journal":
            (Path(project) / "data.qda-journal").write_bytes(b"j")
        else:
            (Path(project) / "data.qda").write_bytes(
                b"this is not a sqlite database")

    def _assert_generic(self, raw, project):
        out = json.loads(raw)
        assert "error" in out, out
        low = out["error"].lower()
        for leak in self.LEAKS:
            assert leak.lower() not in low, (leak, out["error"])
        assert str(project) not in out["error"]
        assert Path(project).name not in out["error"]
        assert out["error"] == server.DB_UNAVAILABLE_ERROR
        return out

    @pytest.mark.parametrize("how", ["journal", "garbage"])
    def test_tools_return_fixed_text(self, setup_server, qualcoder_db_path,
                                     no_process_hits, how, caplog):
        self._damage(qualcoder_db_path, how)
        with caplog.at_level("ERROR", logger="qualcoder_mcp.server"):
            for call in (
                lambda: server.get_current_project(),
                lambda: server.list_backups(),
                lambda: server.create_code("Doomed", create_backup=False),
                lambda: server.copy_project_to_workspace(qualcoder_db_path),
            ):
                self._assert_generic(call(), qualcoder_db_path)
        # The diagnostic detail is logged, not returned
        logged = " ".join(r.getMessage() for r in caplog.records).lower()
        assert "readonly" in logged or "not a database" in logged, logged

    def test_select_project_keeps_its_scoped_wording(self, setup_server,
                                                     qualcoder_db_path,
                                                     no_process_hits):
        # Unchanged by S-H4: select_project's own project-scoped payload
        self._damage(qualcoder_db_path, "journal")
        out = json.loads(server.select_project(qualcoder_db_path))
        assert out["success"] is False
        assert "APPEARS to be open" in out["error"]
        assert "sqlite" not in out["error"].lower()
        assert "readonly" not in out["error"].lower()

    def test_guard_text_has_no_em_dash(self):
        assert "—" not in server.DB_UNAVAILABLE_ERROR
        assert server.DB_UNAVAILABLE_ERROR.startswith("Database error")
