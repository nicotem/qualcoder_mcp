"""P1-4: ai_data backup parity (verdict d, resolved by verification).

QualCoder's save_backup copies the whole project tree INCLUDING
ai_data/ with ignore patterns exactly ('search.sqlite',
'search.sqlite-*', '*.sqlite-shm', '*.sqlite-wal', '*.sqlite-journal')
(app.py:1619-1625 at pin 9bddf17). Our backups and tool-created
project copies mirror that set (plus *.lock, our standing 3.8.x-parity
exclusion), and restore treats a backup without search.sqlite as
normal, never as corruption.
"""

import json
import sqlite3
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server
from qualcoder_mcp.database import (
    BACKUP_IGNORE_PATTERNS,
    QUALCODER_BACKUP_IGNORE_PATTERNS,
    backup_project,
    copy_project_to_workspace,
)


def _plant_ai_data(project_path):
    """Create a realistic QC 4.0 ai_data/ tree plus stray sidecars."""
    project = Path(project_path)
    ai_data = project / "ai_data"
    (ai_data / "ai_prompts").mkdir(parents=True)
    (ai_data / "vectorstore").mkdir()
    # Non-regenerable user data: MUST be backed up
    (ai_data / "ai_prompts.yaml").write_text("prompts: []\n",
                                             encoding="utf-8")
    (ai_data / "ai_prompts" / "my-method.md").write_text(
        "# my prompt\n", encoding="utf-8")
    (ai_data / "chat_history.sqlite").write_bytes(b"SQLite chat")
    (ai_data / "vectorstore" / "faiss_store.bin").write_bytes(b"legacy")
    # Regenerable index + sidecars: MUST be excluded
    (ai_data / "search.sqlite").write_bytes(b"SQLite search")
    (ai_data / "search.sqlite-wal").write_bytes(b"wal")
    (ai_data / "search.sqlite-shm").write_bytes(b"shm")
    (ai_data / "chat_history.sqlite-wal").write_bytes(b"wal")
    (ai_data / "chat_history.sqlite-shm").write_bytes(b"shm")
    (ai_data / "chat_history.sqlite-journal").write_bytes(b"jrnl")
    # A stale lock file: excluded by our standing *.lock rule
    (project / "project_in_use.lock").write_text("ghost\n0",
                                                 encoding="utf-8")


def _assert_parity_tree(copy_root: Path):
    ai = copy_root / "ai_data"
    # Included: everything non-regenerable
    assert (ai / "ai_prompts.yaml").is_file()
    assert (ai / "ai_prompts" / "my-method.md").is_file()
    assert (ai / "chat_history.sqlite").is_file()
    assert (ai / "vectorstore" / "faiss_store.bin").is_file()
    # Excluded: QualCoder's exact ignore set
    assert not (ai / "search.sqlite").exists()
    assert not (ai / "search.sqlite-wal").exists()
    assert not (ai / "search.sqlite-shm").exists()
    assert not (ai / "chat_history.sqlite-wal").exists()
    assert not (ai / "chat_history.sqlite-shm").exists()
    assert not (ai / "chat_history.sqlite-journal").exists()
    # Excluded: lock files (our standing rule)
    assert not (copy_root / "project_in_use.lock").exists()
    # The database itself is there
    assert (copy_root / "data.qda").is_file()


class TestIgnoreSetPinned:

    def test_qualcoder_set_is_byte_exact_with_upstream(self):
        # app.py:1619-1625 at pin 9bddf17, order and spelling included
        assert QUALCODER_BACKUP_IGNORE_PATTERNS == (
            "search.sqlite",
            "search.sqlite-*",
            "*.sqlite-shm",
            "*.sqlite-wal",
            "*.sqlite-journal",
        )

    def test_our_set_adds_only_lock_files(self):
        assert BACKUP_IGNORE_PATTERNS == (
            ("*.lock",) + QUALCODER_BACKUP_IGNORE_PATTERNS)


class TestBackupIncludesAiData:

    def test_backup_project_parity(self, qualcoder_db_path):
        _plant_ai_data(qualcoder_db_path)
        backup = backup_project(qualcoder_db_path)
        _assert_parity_tree(backup)

    def test_pre_write_backup_parity(self, setup_server, qualcoder_db_path):
        _plant_ai_data(qualcoder_db_path)
        out = json.loads(server.set_memo("code", 1, "note",
                                         create_backup=True))
        assert out["success"] is True
        _assert_parity_tree(Path(out["backup_path"]))


class TestWorkspaceCopyParity:

    def test_copy_to_workspace_uses_same_ignore_set(self, qualcoder_db_path,
                                                    tmp_path):
        _plant_ai_data(qualcoder_db_path)
        dest = copy_project_to_workspace(qualcoder_db_path,
                                         workspace=tmp_path / "ws")
        _assert_parity_tree(dest)


class TestRestoreWithoutSearchSqlite:

    def test_restore_from_backup_lacking_search_sqlite(self, setup_server,
                                                       qualcoder_db_path):
        _plant_ai_data(qualcoder_db_path)
        # The stale lock would make the write path warn; remove it so the
        # backup comes from a clean write
        (Path(qualcoder_db_path) / "project_in_use.lock").unlink()
        out = json.loads(server.set_memo("code", 1, "before restore",
                                         create_backup=True))
        backup_path = out["backup_path"]
        assert not (Path(backup_path) / "ai_data" / "search.sqlite").exists()

        restored = json.loads(server.restore_backup(backup_path,
                                                    confirm=True))
        assert restored.get("success") is True, restored
        # The restored project has ai_data WITHOUT search.sqlite; that is
        # upstream-normal (QualCoder rebuilds it on open), and the server
        # keeps working against the restored project
        project = Path(qualcoder_db_path)
        assert (project / "ai_data" / "chat_history.sqlite").is_file()
        assert not (project / "ai_data" / "search.sqlite").exists()
        current = json.loads(server.get_current_project())
        assert "error" not in current
        assert current["project_info"]["coder_name"] == "TestCoder"


class TestBackupToolDescriptionsDisclose:

    def test_backup_tool_descriptions_disclose_ai_data_policy(self):
        # The pre-call surface an MCP client reads (the tool description)
        # must carry the ai_data/search.sqlite disclosure, not only the
        # runtime notes (QA round 1, F15)
        for tool in (server.list_backups, server.prune_backups,
                     server.restore_backup,
                     server.copy_project_to_workspace):
            doc = tool.__doc__ or ""
            assert "ai_data" in doc, tool.__name__
            assert "search.sqlite" in doc, tool.__name__
