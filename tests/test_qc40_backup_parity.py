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
import os
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


# =============================================================================
# S-H5: a failed copy leaves no partial destination; a foreign folder is
# never touched
# =============================================================================

def _plant_unreadable_file(project_path):
    """A mid-tree permission error: copytree collects it into shutil.Error."""
    if os.name == "nt":
        pytest.skip("POSIX permission bits are not modelled on Windows")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root reads everything; the failure cannot be provoked")
    docs = Path(project_path) / "documents"
    docs.mkdir(exist_ok=True)
    locked = docs / "locked.txt"
    locked.write_text("secret", encoding="utf-8")
    locked.chmod(0)
    return locked


def _backup_siblings(project_path):
    project = Path(project_path)
    return sorted(project.parent.glob(f"{project.stem}_backup_*"))


class TestCopyFailureCleanup:

    def test_backup_failure_leaves_no_partial_folder(self, qualcoder_db_path):
        locked = _plant_unreadable_file(qualcoder_db_path)
        try:
            assert _backup_siblings(qualcoder_db_path) == []
            with pytest.raises(OSError, match="Backup failed"):
                backup_project(qualcoder_db_path)
            assert _backup_siblings(qualcoder_db_path) == []
        finally:
            locked.chmod(0o644)

    def test_workspace_copy_failure_leaves_no_partial_folder(
            self, qualcoder_db_path, tmp_path):
        locked = _plant_unreadable_file(qualcoder_db_path)
        try:
            ws = tmp_path / "ws"
            with pytest.raises(OSError, match="Copy failed"):
                copy_project_to_workspace(qualcoder_db_path, workspace=ws)
            assert list(ws.iterdir()) == []
        finally:
            locked.chmod(0o644)

    def test_partial_backup_is_not_listed_as_restorable(
            self, setup_server, qualcoder_db_path, monkeypatch):
        import shutil
        import qualcoder_mcp.database as database

        def half_copy(src, dst, **_kwargs):
            Path(dst).mkdir()
            shutil.copy2(Path(src) / "data.qda", Path(dst) / "data.qda")
            raise shutil.Error([(str(src), str(dst), "disk full")])

        monkeypatch.setattr(database.shutil, "copytree", half_copy)
        with pytest.raises(OSError, match="Backup failed"):
            backup_project(qualcoder_db_path)
        assert _backup_siblings(qualcoder_db_path) == []
        out = json.loads(server.list_backups())
        assert out["backup_count"] == 0

    def test_file_exists_error_leaves_foreign_folder_intact(
            self, qualcoder_db_path, tmp_path, monkeypatch):
        # copytree's makedirs raising FileExistsError means the folder
        # appeared under someone else's hand; it must never be removed
        import qualcoder_mcp.database as database
        foreign = {}

        def someone_elses_folder(src, dst, **_kwargs):
            Path(dst).mkdir()
            (Path(dst) / "theirs.txt").write_text("keep me", encoding="utf-8")
            foreign["path"] = Path(dst)
            raise FileExistsError(17, "File exists", str(dst))

        monkeypatch.setattr(database.shutil, "copytree", someone_elses_folder)
        with pytest.raises(OSError, match="Backup failed"):
            backup_project(qualcoder_db_path)
        assert (foreign["path"] / "theirs.txt").read_text(
            encoding="utf-8") == "keep me"

        foreign.clear()
        ws = tmp_path / "ws"
        with pytest.raises(OSError, match="Copy failed"):
            copy_project_to_workspace(qualcoder_db_path, workspace=ws)
        assert (foreign["path"] / "theirs.txt").read_text(
            encoding="utf-8") == "keep me"

    def test_write_tool_refuses_cleanly_and_leaves_no_litter(
            self, setup_server, qualcoder_db_path):
        # The write path already refused when the backup failed; now it
        # also leaves no half-copied backup folder behind
        locked = _plant_unreadable_file(qualcoder_db_path)
        try:
            out = json.loads(server.set_memo("code", 1, "note",
                                             create_backup=True))
            assert "error" in out
            assert "Nothing was written" in out["error"]
            assert _backup_siblings(qualcoder_db_path) == []
        finally:
            locked.chmod(0o644)


# =============================================================================
# S-P1 (owner-approved deviation from save_backup parity): symlinks that
# resolve outside the project, or dangle, are skipped and reported
# =============================================================================

def _symlink(target, link):
    # target_is_directory matters only on Windows (a file-type link to a
    # directory does not resolve there); it is ignored elsewhere
    target = os.fspath(target)
    resolved = (target if os.path.isabs(target)
                else os.path.join(os.path.dirname(os.fspath(link)), target))
    try:
        os.symlink(target, link, target_is_directory=os.path.isdir(resolved))
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("symlinks unavailable on this platform")


class TestOutwardSymlinksNotFollowed:

    def _outside(self, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "id_ed25519").write_text("SECRET-KEY-BYTES",
                                             encoding="utf-8")
        (outside / "other.txt").write_text("other study", encoding="utf-8")
        return outside

    def test_outward_file_symlink_skipped_and_reported(self, qualcoder_db_path,
                                                       tmp_path):
        outside = self._outside(tmp_path)
        docs = Path(qualcoder_db_path) / "documents"
        docs.mkdir()
        _symlink(outside / "id_ed25519", docs / "readme.txt")
        report = {}
        backup = backup_project(qualcoder_db_path, report=report)
        assert not (backup / "documents" / "readme.txt").exists()
        assert report["skipped_symlinks"] == [
            os.path.join("documents", "readme.txt")]
        # The link target is untouched and nothing outside was read into
        # the backup
        assert (outside / "id_ed25519").read_text(encoding="utf-8") == \
            "SECRET-KEY-BYTES"
        assert "SECRET-KEY-BYTES" not in "".join(
            p.read_text(encoding="utf-8", errors="ignore")
            for p in backup.rglob("*") if p.is_file())

    def test_outward_directory_symlink_skipped(self, qualcoder_db_path,
                                               tmp_path):
        outside = self._outside(tmp_path)
        ai = Path(qualcoder_db_path) / "ai_data"
        ai.mkdir()
        _symlink(outside, ai / "notes")
        report = {}
        backup = backup_project(qualcoder_db_path, report=report)
        assert not (backup / "ai_data" / "notes").exists()
        assert report["skipped_symlinks"] == [os.path.join("ai_data", "notes")]
        assert (outside / "other.txt").is_file()

    def test_inward_symlink_still_copied(self, qualcoder_db_path):
        project = Path(qualcoder_db_path)
        docs = project / "documents"
        docs.mkdir()
        (docs / "real.txt").write_text("inside", encoding="utf-8")
        _symlink(docs / "real.txt", docs / "alias.txt")           # absolute
        _symlink(Path("real.txt"), docs / "relative.txt")         # relative
        report = {}
        backup = backup_project(qualcoder_db_path, report=report)
        assert report["skipped_symlinks"] == []
        for name in ("alias.txt", "relative.txt"):
            copied = backup / "documents" / name
            assert copied.is_file() and not copied.is_symlink()
            assert copied.read_text(encoding="utf-8") == "inside"

    def test_dangling_symlink_does_not_abort(self, qualcoder_db_path):
        docs = Path(qualcoder_db_path) / "documents"
        docs.mkdir()
        _symlink(Path(qualcoder_db_path) / "nowhere", docs / "gone.txt")
        report = {}
        backup = backup_project(qualcoder_db_path, report=report)
        assert (backup / "data.qda").is_file()
        assert not (backup / "documents" / "gone.txt").exists()
        assert report["skipped_symlinks"] == [os.path.join("documents", "gone.txt")]

    def test_workspace_copy_applies_the_same_rule(self, setup_server,
                                                  qualcoder_db_path,
                                                  tmp_path, monkeypatch):
        outside = self._outside(tmp_path)
        project = Path(qualcoder_db_path)
        (project / "documents").mkdir()
        _symlink(outside / "id_ed25519", project / "documents" / "key.txt")
        (project / "documents" / "real.txt").write_text("r", encoding="utf-8")
        _symlink(Path("real.txt"), project / "documents" / "in.txt")
        _symlink(project / "nowhere", project / "documents" / "dangling.txt")
        import qualcoder_mcp.database as database
        monkeypatch.setattr(database, "DEFAULT_WORKSPACE", tmp_path / "ws")
        out = json.loads(server.copy_project_to_workspace(qualcoder_db_path))
        assert out["success"] is True
        assert out["skipped_symlinks"] == 2
        assert sorted(out["skipped_symlink_names"]) == sorted([
            os.path.join("documents", "key.txt"),
            os.path.join("documents", "dangling.txt")])
        copy = Path(out["workspace_copy"])
        assert not (copy / "documents" / "key.txt").exists()
        assert (copy / "documents" / "in.txt").read_text(encoding="utf-8") == "r"

    def test_clean_copy_reports_zero(self, setup_server, qualcoder_db_path,
                                     tmp_path, monkeypatch):
        import qualcoder_mcp.database as database
        monkeypatch.setattr(database, "DEFAULT_WORKSPACE", tmp_path / "ws")
        out = json.loads(server.copy_project_to_workspace(qualcoder_db_path))
        assert out["skipped_symlinks"] == 0
        assert "skipped_symlink_names" not in out

    def test_pre_write_backup_reports_skipped(self, setup_server,
                                              qualcoder_db_path, tmp_path):
        outside = self._outside(tmp_path)
        docs = Path(qualcoder_db_path) / "documents"
        docs.mkdir()
        _symlink(outside / "id_ed25519", docs / "leak.txt")
        out = json.loads(server.set_memo("code", 1, "note",
                                         create_backup=True))
        assert out["success"] is True
        assert out["backup_skipped_symlinks"] == 1
        assert out["backup_skipped_symlink_names"] == [
            os.path.join("documents", "leak.txt")]
        assert not (Path(out["backup_path"]) / "documents" / "leak.txt").exists()
        # A clean project carries no such keys
        docs.joinpath("leak.txt").unlink()
        out = json.loads(server.set_memo("code", 1, "note2",
                                         create_backup=True))
        assert "backup_skipped_symlinks" not in out

    def test_tool_descriptions_disclose_the_rule(self):
        for tool in (server.copy_project_to_workspace, server.list_backups,
                     server.restore_backup):
            assert "symlink" in (tool.__doc__ or "").lower(), tool.__name__


# =============================================================================
# Fix round 3, R2: in-project symlink LOOPS are skipped and reported, never
# followed into a project nested into itself
# =============================================================================

def _one_of_each(backup, name):
    return [p for p in backup.rglob(name) if p.is_file()]


class TestSymlinkLoopsNotFollowed:
    """A directory link that points back into a folder the copy is already
    inside made the S-P1 callback re-enter the project until the OS
    symlink-follow limit (33x on macOS, about 40x on Linux) and then
    report the deepest link as dangling. Such loops are now detected
    against the whole traversal path, skipped and reported; sibling and
    descendant links are still copied as real folders."""

    def _project(self, qualcoder_db_path):
        project = Path(qualcoder_db_path)
        docs = project / "documents"
        docs.mkdir()
        (docs / "note.txt").write_text("a note", encoding="utf-8")
        return project, docs

    def test_relative_link_to_the_root_is_skipped(self, qualcoder_db_path):
        project, docs = self._project(qualcoder_db_path)
        _symlink(Path(".."), docs / "up")
        report = {}
        backup = backup_project(qualcoder_db_path, report=report)
        assert report["skipped_symlinks"] == [os.path.join("documents", "up")]
        assert not (backup / "documents" / "up").exists()
        assert len(_one_of_each(backup, "data.qda")) == 1
        assert (backup / "documents" / "note.txt").read_text(
            encoding="utf-8") == "a note"

    def test_absolute_link_to_the_root_is_skipped(self, qualcoder_db_path):
        project, docs = self._project(qualcoder_db_path)
        _symlink(project, docs / "loop")
        report = {}
        backup = backup_project(qualcoder_db_path, report=report)
        assert report["skipped_symlinks"] == [os.path.join("documents", "loop")]
        assert not (backup / "documents" / "loop").exists()
        assert len(_one_of_each(backup, "data.qda")) == 1

    def test_link_to_an_ancestor_below_the_root_is_skipped(
            self, qualcoder_db_path):
        project = Path(qualcoder_db_path)
        (project / "a" / "b").mkdir(parents=True)
        (project / "a" / "fa.txt").write_text("fa", encoding="utf-8")
        _symlink(Path(".."), project / "a" / "b" / "back")
        report = {}
        backup = backup_project(qualcoder_db_path, report=report)
        assert report["skipped_symlinks"] == [os.path.join("a", "b", "back")]
        assert not (backup / "a" / "b" / "back").exists()
        assert len(_one_of_each(backup, "fa.txt")) == 1

    def test_mutual_cycle_between_two_folders_is_cut_once(
            self, qualcoder_db_path):
        # Neither link points at an ancestor of its own folder, so an
        # ancestors-only check would miss this shape
        project = Path(qualcoder_db_path)
        (project / "a").mkdir()
        (project / "b").mkdir()
        (project / "a" / "fa.txt").write_text("fa", encoding="utf-8")
        (project / "b" / "fb.txt").write_text("fb", encoding="utf-8")
        _symlink(Path("..") / "b", project / "a" / "to_b")
        _symlink(Path("..") / "a", project / "b" / "to_a")
        report = {}
        backup = backup_project(qualcoder_db_path, report=report)
        assert sorted(report["skipped_symlinks"]) == sorted([
            os.path.join("a", "to_b", "to_a"),
            os.path.join("b", "to_a", "to_b")])
        # Each sibling link is materialized exactly once, then the loop
        # is cut
        assert (backup / "a" / "to_b" / "fb.txt").is_file()
        assert (backup / "b" / "to_a" / "fa.txt").is_file()
        assert not (backup / "a" / "to_b" / "to_a").exists()
        assert not (backup / "b" / "to_a" / "to_b").exists()
        assert len(_one_of_each(backup, "fa.txt")) == 2
        assert len(_one_of_each(backup, "fb.txt")) == 2
        assert len(_one_of_each(backup, "data.qda")) == 1

    def test_self_referential_link_is_skipped_without_error(
            self, qualcoder_db_path):
        project, docs = self._project(qualcoder_db_path)
        _symlink(Path("self"), docs / "self")
        report = {}
        backup = backup_project(qualcoder_db_path, report=report)
        assert report["skipped_symlinks"] == [os.path.join("documents", "self")]
        assert not (backup / "documents" / "self").exists()

    def test_sibling_and_descendant_directory_links_still_copied(
            self, qualcoder_db_path):
        project, docs = self._project(qualcoder_db_path)
        media = project / "media"
        media.mkdir()
        (media / "clip.txt").write_text("clip", encoding="utf-8")
        sub = docs / "sub"
        sub.mkdir()
        (sub / "deep.txt").write_text("deep", encoding="utf-8")
        _symlink(Path("..") / "media", docs / "media_alias")   # sibling
        _symlink(Path("sub"), docs / "down")                   # descendant
        report = {}
        backup = backup_project(qualcoder_db_path, report=report)
        assert report["skipped_symlinks"] == []
        alias = backup / "documents" / "media_alias"
        assert alias.is_dir() and not alias.is_symlink()
        assert (alias / "clip.txt").read_text(encoding="utf-8") == "clip"
        assert (backup / "documents" / "down" / "deep.txt").read_text(
            encoding="utf-8") == "deep"

    def test_workspace_copy_and_pre_write_backup_report_the_loop(
            self, setup_server, qualcoder_db_path, tmp_path, monkeypatch):
        project, docs = self._project(qualcoder_db_path)
        _symlink(Path(".."), docs / "up")
        import qualcoder_mcp.database as database
        monkeypatch.setattr(database, "DEFAULT_WORKSPACE", tmp_path / "ws")
        out = json.loads(server.copy_project_to_workspace(qualcoder_db_path))
        assert out["skipped_symlinks"] == 1
        assert out["skipped_symlink_names"] == [os.path.join("documents", "up")]
        assert "loop" in out["skipped_symlinks_note"]
        copy = Path(out["workspace_copy"])
        assert len(_one_of_each(copy, "data.qda")) == 1
        out = json.loads(server.set_memo("code", 1, "note",
                                         create_backup=True))
        assert out["success"] is True
        assert out["backup_skipped_symlinks"] == 1
        assert out["backup_skipped_symlink_names"] == [
            os.path.join("documents", "up")]
        assert len(_one_of_each(Path(out["backup_path"]), "data.qda")) == 1

    def test_loop_is_logged_with_its_own_reason(self, qualcoder_db_path,
                                                caplog):
        import logging
        project, docs = self._project(qualcoder_db_path)
        _symlink(Path(".."), docs / "up")
        with caplog.at_level(logging.WARNING, logger="qualcoder_mcp.database"):
            backup_project(qualcoder_db_path)
        assert any("already being copied" in r.getMessage()
                   for r in caplog.records)

    def test_docs_disclose_the_loop_rule(self):
        for tool in (server.copy_project_to_workspace, server.list_backups,
                     server.restore_backup):
            assert "loop" in (tool.__doc__ or ""), tool.__name__
