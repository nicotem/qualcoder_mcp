"""v0.8 Phase C — backup retention (contract C.1/C.2).

Covers the list_backups age extension and prune_backups: policy shapes,
the conservative intersection of both criteria, the keep-newest floor,
the _BKUP_ untouchability guarantee, the _prerestore warning note, and
the preview→confirm gate.
"""

import json
import os
import shutil
import time
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server


def _make_backup(project_path, suffix, age_days=0.0):
    """Create a sibling backup folder with a controlled mtime."""
    folder = Path(project_path)
    backup = folder.parent / f"{folder.stem}{suffix}.qda"
    shutil.copytree(project_path, backup)
    if age_days:
        ts = time.time() - age_days * 86400
        os.utime(backup, (ts, ts))
    return backup


@pytest.fixture
def retention_env(setup_server, qualcoder_db_path):
    """Five MCP backups aged 0/2/5/10/20 days, one _prerestore at 5 days,
    and one QualCoder _BKUP_ at 30 days."""
    made = {
        "b0": _make_backup(qualcoder_db_path, "_backup_20260722_000005", 0),
        "b2": _make_backup(qualcoder_db_path, "_backup_20260720_000004", 2),
        "b5": _make_backup(qualcoder_db_path, "_backup_20260717_000003", 5),
        "pre5": _make_backup(qualcoder_db_path,
                             "_backup_20260717_000002_prerestore", 5.5),
        "b10": _make_backup(qualcoder_db_path, "_backup_20260712_000001", 10),
        "b20": _make_backup(qualcoder_db_path, "_backup_20260702_000000", 20),
        "qc30": _make_backup(qualcoder_db_path, "_BKUP_2026062200", 30),
    }
    return qualcoder_db_path, made


class TestListBackupsAges:

    def test_age_days_present_and_ordered(self, retention_env):
        out = json.loads(server.list_backups())
        assert out["backup_count"] == 7
        for entry in out["backups"]:
            assert isinstance(entry["age_days"], (int, float))
        ages = [b["age_days"] for b in out["backups"]]
        assert ages == sorted(ages)  # newest (smallest age) first

    def test_retention_note_present(self, retention_env):
        """The D7 disclosure: accumulation + the prune tool are named."""
        out = json.loads(server.list_backups())
        joined = " ".join(out["notes"]).lower()
        assert "prune" in joined


class TestPruneBackupsPolicy:

    def test_policy_required(self, setup_server, qualcoder_db_path):
        out = json.loads(server.prune_backups())
        assert "Refusing a policy-less prune" in out["error"]

    def test_invalid_params(self, setup_server, qualcoder_db_path):
        assert "non-negative integer" in json.loads(
            server.prune_backups(keep_last=-1))["error"]
        assert "non-negative number" in json.loads(
            server.prune_backups(older_than_days=-2))["error"]

    def test_preview_then_confirm_keep_last(self, retention_env):
        project, made = retention_env
        preview = json.loads(server.prune_backups(keep_last=2))
        assert preview["requires_confirmation"] is True
        names = {b["name"] for b in preview["would_remove"]}
        # 6 mcp backups, keep newest 2 -> remove 4 (incl. prerestore + oldest)
        assert len(names) == 4
        assert made["b0"].name not in names and made["b2"].name not in names
        assert made["qc30"].name not in names  # never the _BKUP_ family
        # nothing removed yet
        assert made["b20"].exists()

        result = json.loads(server.prune_backups(keep_last=2, confirm=True))
        assert result["success"] is True
        assert len(result["removed"]) == 4
        assert not made["b20"].exists()
        assert made["b0"].exists() and made["b2"].exists()
        assert made["qc30"].exists()  # QualCoder backup untouched

    def test_older_than_days_only(self, retention_env):
        project, made = retention_env
        result = json.loads(server.prune_backups(older_than_days=7,
                                                 confirm=True))
        removed = set(result["removed"])
        assert removed == {made["b10"].name, made["b20"].name}
        assert made["b5"].exists() and made["pre5"].exists()

    def test_both_criteria_conservative_intersection(self, retention_env):
        """Pruned only if BEYOND keep_last AND older than the age bound."""
        project, made = retention_env
        result = json.loads(server.prune_backups(keep_last=1,
                                                 older_than_days=7,
                                                 confirm=True))
        removed = set(result["removed"])
        # beyond-newest-1 covers all but b0; older-than-7 covers b10, b20
        # -> intersection is exactly b10 + b20
        assert removed == {made["b10"].name, made["b20"].name}
        assert made["b2"].exists() and made["b5"].exists()

    def test_newest_kept_floor(self, retention_env):
        """older_than_days that matches everything still keeps the newest."""
        project, made = retention_env
        # age all backups by touching mtimes far in the past
        for key, path in made.items():
            if "_BKUP_" not in path.name:
                ts = time.time() - 100 * 86400
                os.utime(path, (ts, ts))
        server.switch_project(project)
        result = json.loads(server.prune_backups(older_than_days=1,
                                                 confirm=True))
        # exactly one MCP backup survives: the newest one
        survivors = [p for p in made.values()
                     if p.exists() and "_BKUP_" not in p.name]
        assert len(survivors) == 1

    def test_keep_last_zero_explicit_removes_all(self, retention_env):
        project, made = retention_env
        result = json.loads(server.prune_backups(keep_last=0, confirm=True))
        assert result["success"] is True
        remaining_mcp = [p for p in made.values()
                         if p.exists() and "_BKUP_" not in p.name]
        assert remaining_mcp == []
        assert made["qc30"].exists()

    def test_prerestore_removal_flagged(self, retention_env):
        project, made = retention_env
        preview = json.loads(server.prune_backups(keep_last=0))
        assert any("pre-restore" in n for n in preview.get("notes", []))

    def test_nothing_to_prune(self, retention_env):
        out = json.loads(server.prune_backups(keep_last=10))
        assert out["success"] is True
        assert "Nothing to prune" in out["message"]

    def test_no_project_selected(self, setup_server, monkeypatch):
        monkeypatch.setattr(server, "current_project_path", None)
        out = json.loads(server.prune_backups(keep_last=1))
        assert "No Qualcoder project selected" in out["error"]

    def test_works_while_qualcoder_open(self, retention_env):
        """Pruning never touches the live DB, so an active QualCoder lock
        must NOT block it (contract C.2 gate class)."""
        project, made = retention_env
        lock = Path(project) / "project_in_use.lock"
        lock.write_text(f"gemma\n{time.time()}", encoding="utf-8")
        try:
            result = json.loads(server.prune_backups(older_than_days=15,
                                                     confirm=True))
            assert result["success"] is True
            assert made["b20"].name in result["removed"]
        finally:
            lock.unlink()
