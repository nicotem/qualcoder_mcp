# SPDX-License-Identifier: LGPL-3.0-or-later
"""v0.14, creating a project: the companions (brief C, part 7).

The project memo as a `set_memo` target (never the private part); the
AI coder name setter's warning when the project's own coder name is not
known; a warning on selecting a project whose path holds '|'; a plain
refusal of a database with no project row, and the unfinished folders a
creation leaves, named in the listing and on selection; the rebuilt
test-project script. The chat-history wording and the process scan are
in tests/test_qc40_gui_detection.py.
"""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server
from qualcoder_mcp import new_project
from qualcoder_mcp.database import (NO_PROJECT_ROW_MESSAGE,
                                    QualcoderDatabase,
                                    UnsupportedSchemaError)
from track5_helpers import write_fixture_sidecar

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _restore_selection():
    saved = (server.db, server.current_project_path)
    yield
    if server.db is not None and server.db is not saved[0]:
        try:
            server.db.close()
        except Exception:
            pass
    server.db, server.current_project_path = saved


def _project(parent: Path, name="Study", coder="carol") -> Path:
    folder = parent / f"{name}.qda"
    new_project.write_project(folder, new_project.creation_statements(
        coder, new_project.about_line("0.14.0"), "2026-09-26 10:00:00"))
    return folder


def _memo(folder: Path) -> str:
    conn = sqlite3.connect(str(folder / "data.qda"))
    try:
        return conn.execute("SELECT memo FROM project").fetchone()[0]
    finally:
        conn.close()


def _set_memo_text(folder: Path, text: str) -> None:
    conn = sqlite3.connect(str(folder / "data.qda"))
    try:
        conn.execute("UPDATE project SET memo = ?", (text,))
        conn.commit()
    finally:
        conn.close()


class TestTheProjectMemo:

    def test_written_into_an_empty_memo(self, tmp_path):
        folder = _project(tmp_path)
        server.select_project(str(folder))
        out = json.loads(server.set_memo(
            "project", None, "Research question: how carers cope."))
        assert out["success"] is True and out["target_type"] == "project"
        assert out["memo"] == "Research question: how carers cope."
        assert _memo(folder) == "Research question: how carers cope."

    def test_the_private_part_survives_and_is_never_returned(self, tmp_path):
        folder = _project(tmp_path)
        _set_memo_text(folder, "Old topic\n#####\nmy own note: P3 is my aunt")
        server.select_project(str(folder))
        out = json.loads(server.set_memo("project", None, "New topic"))
        assert _memo(folder) == "New topic\n#####\nmy own note: P3 is my aunt"
        assert "aunt" not in json.dumps(out)
        # a marker in the new text is not written, so it cannot open a
        # private part of its own
        json.loads(server.set_memo("project", None, "A ##### B"))
        assert _memo(folder).startswith("A \n#####\nmy own note")
        # clearing keeps the private part
        json.loads(server.set_memo("project", None, ""))
        assert _memo(folder) == "#####\nmy own note: P3 is my aunt"
        info = json.loads(server.get_current_project())
        assert "aunt" not in json.dumps(info)

    def test_the_id_is_not_used_for_the_project(self, tmp_path):
        folder = _project(tmp_path)
        server.select_project(str(folder))
        json.loads(server.set_memo("project", 7, "Any id"))
        assert _memo(folder) == "Any id"

    def test_other_targets_still_need_their_id(self, tmp_path):
        server.select_project(str(_project(tmp_path)))
        out = json.loads(server.set_memo("code", None, "x"))
        assert "target_id is required" in out["error"]
        out = json.loads(server.set_memo("bogus", 1, "x"))
        assert "project" in out["error"]

    def test_a_project_with_no_project_row_is_refused(self, tmp_path):
        folder = _project(tmp_path)
        server.select_project(str(folder))
        write = server.get_db(read_only=False)
        write.conn.execute("DELETE FROM project")
        with pytest.raises(ValueError) as caught:
            write.set_memo("project", None, "x")
        assert "no project record" in str(caught.value)
        write.conn.rollback()


class TestTheAiCoderNameWarning:

    def test_warns_when_the_researchers_name_is_not_known(self, tmp_path):
        folder = _project(tmp_path, coder="")
        server.select_project(str(folder))
        out = json.loads(server.set_project_ai_coder_name("AI Helper"))
        assert out["success"] is True
        assert server.RESEARCHER_CODER_NAME_UNKNOWN in out["warnings"]

    def test_no_warning_and_the_refusal_when_it_is_known(self, tmp_path):
        folder = _project(tmp_path, coder="carol")
        server.select_project(str(folder))
        out = json.loads(server.set_project_ai_coder_name("AI Helper"))
        assert server.RESEARCHER_CODER_NAME_UNKNOWN not in out["warnings"]
        refused = json.loads(server.set_project_ai_coder_name("carol"))
        assert "own coder name" in refused["error"]


class TestSelectingAPathWithAPipe:

    @pytest.mark.skipif(os.name == "nt",
                        reason="Windows cannot hold '|' in a folder name")
    def test_warned(self, tmp_path):
        parent = tmp_path / "a|b"
        parent.mkdir()
        folder = _project(parent)
        out = json.loads(server.select_project(str(folder)))
        assert out["success"] is True
        assert server.PIPE_PATH_WARNING in out["warning"]

    def test_not_warned_without_one(self, tmp_path):
        out = json.loads(server.select_project(str(_project(tmp_path))))
        assert server.PIPE_PATH_WARNING not in out.get("warning", "")


class TestNoProjectRow:

    def _rowless(self, tmp_path) -> Path:
        folder = _project(tmp_path, name="Rowless")
        conn = sqlite3.connect(str(folder / "data.qda"))
        try:
            conn.execute("DELETE FROM project")
            conn.commit()
        finally:
            conn.close()
        return folder

    def test_refused_plainly_on_connection(self, tmp_path):
        with pytest.raises(UnsupportedSchemaError) as caught:
            QualcoderDatabase(str(self._rowless(tmp_path)))
        assert str(caught.value) == NO_PROJECT_ROW_MESSAGE

    def test_select_project_says_so_without_upgrade_advice(self, tmp_path):
        out = json.loads(server.select_project(str(self._rowless(tmp_path))))
        assert out["success"] is False
        assert out["error"] == NO_PROJECT_ROW_MESSAGE
        for wrong in ("pre-v14", "3.8", "ALLOW_UNKNOWN_SCHEMA", "newer"):
            assert wrong not in out["error"]

    def test_the_refused_connection_is_closed(self, tmp_path):
        """No handle left for the collector: on Windows it would keep the
        folder from being moved or removed."""
        import gc
        folder = self._rowless(tmp_path)
        gc.collect()
        before = sum(1 for o in gc.get_objects()
                     if isinstance(o, sqlite3.Connection) and _is_open(o))
        with pytest.raises(UnsupportedSchemaError) as caught:
            QualcoderDatabase(str(folder))
        # `caught` keeps the traceback, and with it the half-made object,
        # alive: its connection must already be closed, not waiting for
        # the collector
        after = sum(1 for o in gc.get_objects()
                    if isinstance(o, sqlite3.Connection) and _is_open(o))
        assert caught.value is not None
        assert after == before


def _is_open(conn) -> bool:
    try:
        conn.execute("SELECT 1")
        return True
    except Exception:
        return False


class TestUnfinishedFolders:

    def _unfinished(self, parent: Path) -> Path:
        folder = parent / "Half.qda"
        folder.mkdir()
        (folder / "data.qda").write_bytes(b"")
        return folder

    def test_marked_in_the_listing(self, tmp_path):
        half = self._unfinished(tmp_path)
        whole = _project(tmp_path)
        listing = json.loads(server.list_available_projects([str(tmp_path)]))
        by_name = {p["name"]: p for p in listing["projects"]}
        assert by_name["Half"]["usable"] is False
        assert by_name["Half"]["note"] == server.NO_USABLE_DATABASE
        assert "usable" not in by_name["Study"]
        assert (half / "data.qda").read_bytes() == b""
        assert whole.exists()

    def test_named_on_selection(self, tmp_path):
        half = self._unfinished(tmp_path)
        for path in (half, half / "data.qda"):
            out = json.loads(server.select_project(str(path)))
            assert out["error"].startswith(server.NO_USABLE_DATABASE)
            assert "list_available_projects" in out["error"]
        (half / "data.qda").unlink()
        out = json.loads(server.select_project(str(half)))
        assert out["error"].startswith(server.NO_USABLE_DATABASE)


class TestTheTestProjectScript:
    """`scripts/create_test_project.py`, rebuilt on the creation code: it
    used to delete ~/Documents/QDA Projects/test_project.qda first and
    then build a project neither QualCoder nor this server would open."""

    SCRIPT = REPO / "scripts" / "create_test_project.py"

    def _run(self, *args):
        return subprocess.run([sys.executable, str(self.SCRIPT), *args],
                              capture_output=True, text=True, timeout=120,
                              encoding="utf-8", errors="replace")

    def test_it_makes_a_project_this_server_opens(self, tmp_path):
        target = tmp_path / "sample.qda"
        proc = self._run(str(target))
        assert proc.returncode == 0, proc.stderr
        db = QualcoderDatabase(str(target))
        try:
            assert db.db_version == "v17" and db.qualcoder_about_ok
            assert db.write_support()[0] is True
            assert len(db.list_files()) == 3 and len(db.list_codes()) == 10
        finally:
            db.close()

    def test_it_never_replaces_anything(self, tmp_path):
        target = tmp_path / "sample.qda"
        target.mkdir()
        (target / "keep.txt").write_text("mine")
        proc = self._run(str(target))
        assert proc.returncode == 1
        assert (target / "keep.txt").read_text() == "mine"
        assert sorted(p.name for p in target.iterdir()) == ["keep.txt"]

    def test_it_needs_a_folder_and_names_no_fixed_one(self):
        assert self._run().returncode == 2
        source = self.SCRIPT.read_text(encoding="utf-8")
        assert "rmtree" not in source and "QDA Projects" not in source
