"""P1-5: best-effort QC 4.0 GUI-open detection.

QualCoder 4.0 deleted project_in_use.lock (no matches in the pinned
tree at 9bddf17), so the lock gate is blind against a 4.0 GUI. These
tests pin the heuristic signals evaluated against the pinned source
(data.qda write sidecars; the WAL-mode AI search index,
ai_vectorstore.py:472-477; chat-history activity, ai_chat.py:1682-1718;
a guarded process scan) and their WARN-only wiring into the ladder:
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

    def test_fresh_search_index_wal_signals_open_gui(self, tmp_path,
                                                     no_process_hits):
        proj = tmp_path / "p.qda"
        (proj / "ai_data").mkdir(parents=True)
        (proj / "ai_data" / "search.sqlite-wal").write_bytes(b"w")
        signals = qualcoder_gui_signals(proj)
        assert len(signals) == 1
        assert "search.sqlite-wal" in signals[0]
        assert "recently written" in signals[0]

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
        monkeypatch.setattr(database, "_qualcoder_process_hits",
                            lambda: ["qualcoder gui"])
        proj = tmp_path / "p.qda"
        (proj / "ai_data").mkdir(parents=True)
        (proj / "data.qda-journal").write_bytes(b"j")
        for signal in qualcoder_gui_signals(proj):
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
