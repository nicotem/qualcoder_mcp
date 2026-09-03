"""P1-6: MRU hint on "no project selected" errors (approved 2026-08-28).

Every no-project error path appends the machine's most-recently-used
project ("The last project used on this machine was <path>.") so a
client whose host recycled the server process recovers with one
deterministic select_project call. The MRU is recorded on successful
select_project in ~/.qualcoder_mcp/ (patched to a temp dir in tests);
missing, corrupt, or stale (deleted-project) state degrades to the
plain error text. The selection is NEVER auto-restored (owner-rejected:
explicit-selection semantics; concurrent hosts would clobber).
"""

import json
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server


HINT_PHRASE = "The last project used on this machine was"


@pytest.fixture
def no_project(monkeypatch):
    """A server with no project selected and no env fallback."""
    monkeypatch.setattr(server, "db", None)
    monkeypatch.setattr(server, "current_project_path", None)
    monkeypatch.delenv("QUALCODER_PROJECT_PATH", raising=False)


class TestMruRecording:

    def test_select_project_records_canonical_path(self, setup_server,
                                                   qualcoder_db_path):
        out = json.loads(server.select_project(qualcoder_db_path))
        assert out["success"] is True
        data = json.loads(server._MRU_FILE.read_text(encoding="utf-8"))
        recorded = Path(data["project_path"])
        assert recorded.name == "data.qda"
        assert recorded.parent == Path(qualcoder_db_path).resolve()
        assert "updated" in data

    def test_failed_select_does_not_record(self, setup_server, tmp_path):
        out = json.loads(server.select_project(str(tmp_path / "no.qda")))
        assert out["success"] is False
        assert not server._MRU_FILE.exists()

    def test_recording_failure_never_breaks_selection(self, setup_server,
                                                      qualcoder_db_path,
                                                      monkeypatch):
        # An unwritable state location (a directory path routed under a
        # regular FILE) makes the recording helper fail internally; the
        # selection must still succeed
        monkeypatch.setattr(
            server, "_MRU_FILE",
            Path(qualcoder_db_path) / "data.qda" / "impossible" / "x.json")
        out = json.loads(server.select_project(qualcoder_db_path))
        assert out["success"] is True


class TestMruHint:

    def _record(self, path):
        server._remember_mru_project(str(Path(path) / "data.qda"))

    def test_no_state_gives_plain_error(self, no_project):
        out = json.loads(server.get_project_summary())
        assert "No Qualcoder project selected" in out["error"]
        assert HINT_PHRASE not in out["error"]

    def test_hint_appears_on_get_db_error_path(self, no_project,
                                               qualcoder_db_path):
        self._record(qualcoder_db_path)
        out = json.loads(server.get_project_summary())
        assert HINT_PHRASE in out["error"]
        assert str(Path(qualcoder_db_path).resolve()) in out["error"]
        assert "select_project" in out["error"]

    def test_hint_appears_on_inline_error_paths(self, no_project,
                                                qualcoder_db_path):
        self._record(qualcoder_db_path)
        for fn, args in [
            (server.list_backups, ()),
            (server.prune_backups, (1,)),
            (server.restore_backup, ("/x/y_backup_1.qda",)),
        ]:
            out = json.loads(fn(*args))
            assert HINT_PHRASE in out["error"], fn.__name__

    def test_hint_appears_in_get_current_project_message(self, no_project,
                                                         qualcoder_db_path):
        self._record(qualcoder_db_path)
        out = json.loads(server.get_current_project())
        assert out["current_project"] is None
        assert HINT_PHRASE in out["message"]

    def test_stale_mru_path_gives_plain_error(self, no_project, tmp_path):
        gone = tmp_path / "gone.qda"
        gone.mkdir()
        (gone / "data.qda").write_bytes(b"db")
        self._record(gone)
        import shutil
        shutil.rmtree(gone)
        out = json.loads(server.get_project_summary())
        assert HINT_PHRASE not in out["error"]
        assert "No Qualcoder project selected" in out["error"]

    def test_corrupt_mru_state_degrades_silently(self, no_project):
        server._MRU_FILE.parent.mkdir(parents=True, exist_ok=True)
        server._MRU_FILE.write_text("not json{", encoding="utf-8")
        out = json.loads(server.get_project_summary())
        assert HINT_PHRASE not in out["error"]
        assert "No Qualcoder project selected" in out["error"]

    def test_wrong_shape_mru_state_degrades_silently(self, no_project):
        server._MRU_FILE.parent.mkdir(parents=True, exist_ok=True)
        server._MRU_FILE.write_text(json.dumps({"project_path": 42}),
                                    encoding="utf-8")
        out = json.loads(server.get_project_summary())
        assert HINT_PHRASE not in out["error"]


class TestNoAutoRestore:

    def test_selection_is_not_auto_restored(self, no_project,
                                            qualcoder_db_path):
        # Owner explicitly rejected auto-restore: the MRU is a HINT, and
        # the server stays projectless until an explicit select_project
        server._remember_mru_project(
            str(Path(qualcoder_db_path) / "data.qda"))
        out = json.loads(server.get_project_summary())
        assert "error" in out
        assert server.current_project_path is None
        assert server.db is None


class TestSessionProjectCheckHint:

    def test_hint_on_session_bound_write_path(self, session_with_suggestions,
                                              qualcoder_db_path,
                                              monkeypatch):
        # _check_session_project fronts the apply_codings family; its
        # no-project error must carry the hint too (QA round 1, F19)
        server._remember_mru_project(
            str(Path(qualcoder_db_path) / "data.qda"))
        monkeypatch.setattr(server, "db", None)
        monkeypatch.setattr(server, "current_project_path", None)
        monkeypatch.delenv("QUALCODER_PROJECT_PATH", raising=False)
        out = json.loads(server.apply_codings(
            session_with_suggestions.session_id, create_backup=False))
        assert "No Qualcoder project selected" in out["error"]
        assert HINT_PHRASE in out["error"]
        assert str(Path(qualcoder_db_path).resolve()) in out["error"]


class TestMruWriteAtomicity:

    def test_temp_name_is_per_process(self, monkeypatch):
        # Concurrent servers must never share one temp name (QA F20)
        monkeypatch.setattr(server.os, "getpid", lambda: 4242)
        tmp = server._mru_tmp_file()
        assert tmp.name == "mru_project.json.4242.tmp"
        assert tmp.parent == server._MRU_FILE.parent

    def test_no_temp_file_left_after_record(self, qualcoder_db_path):
        server._remember_mru_project(
            str(Path(qualcoder_db_path) / "data.qda"))
        assert server._MRU_FILE.exists()
        assert list(server._MRU_FILE.parent.glob("*.tmp")) == []

    def test_failed_write_removes_its_temp_file(self, monkeypatch):
        def boom(*_args, **_kwargs):
            raise OSError("disk full")
        monkeypatch.setattr(server.json, "dump", boom)
        server._remember_mru_project("/some/where/data.qda")
        assert not server._MRU_FILE.exists()
        assert list(server._MRU_FILE.parent.glob("*.tmp")) == []
