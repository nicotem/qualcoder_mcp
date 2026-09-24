"""v0.13 rename tools, fix round 2: how rename_file reads the project's
backups to recognise a rename back (the lead's ruling on QA-4).

The pins R1 drafted (REVERIFY_RENAME_R1.md, probes/test_zz_rrv1_pins.py),
adopted and adapted to the reader's new shape (`earlier_name`, one
question, first match), and the fixes of R1-1 (the same entry is the same
id AND the same date), R1-2 (opened read-only and immutable) and R1-4
(read once per call, stopping at the first match).
"""

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server
import qualcoder_mcp.database as database


def _exec(project, sql, args=()):
    con = sqlite3.connect(str(Path(project) / "data.qda"))
    try:
        con.execute(sql, args)
        con.commit()
    finally:
        con.close()


def _add(project, fid, name, text="Some text.", date="2024-01-15 10:00:00"):
    _exec(project, "INSERT INTO source (id, name, fulltext, mediapath, memo, "
                   "owner, date) VALUES (?, ?, ?, NULL, '', 'gui_user', ?)",
          (fid, name, text, date))


def _reload():
    server.switch_project(server.current_project_path)


def _file(fid, name, **kw):
    return json.loads(server.rename_file(fid, name, **kw))


def _backups(project):
    project = Path(project)
    return sorted(list(project.parent.glob(f"{project.stem}_backup_*")) +
                  list(project.parent.glob(f"{project.stem}_BKUP_*")))


def _snapshot(paths):
    snap = {}
    for root in paths:
        for p in sorted(Path(root).rglob("*")) + [Path(root)]:
            st = p.lstat()
            snap[str(p)] = (st.st_mtime_ns, st.st_size,
                            hashlib.sha256(p.read_bytes()).hexdigest()
                            if p.is_file() else None)
    return snap


@pytest.fixture
def legacy(setup_server, qualcoder_db_path):
    """Entry 5, a legacy text QualCoder finds by name in documents/."""
    project = Path(qualcoder_db_path)
    (project / "documents").mkdir(exist_ok=True)
    (project / "documents" / "legacy.txt").write_text("original")
    _add(project, 5, "legacy.txt")
    _reload()
    return project


def _spy_connections(monkeypatch):
    seen = []
    real = sqlite3.connect

    def connect(target, *a, **kw):
        if "_backup_" in str(target) or "_BKUP_" in str(target):
            seen.append((str(target), kw.get("uri")))
        return real(target, *a, **kw)
    monkeypatch.setattr(database.sqlite3, "connect", connect)
    return seen


class TestTheSameEntryIsTheSameIdAndDate:
    """R1-1: QualCoder's source.id has no AUTOINCREMENT, so a deleted last
    entry's id goes to the next one. A backup's row is this entry's only
    when its date matches too."""

    def test_P_reuse_a_reused_id_is_not_the_same_entry(
            self, setup_server, qualcoder_db_path):
        """R1's pin, as drafted: a new text that takes a deleted text's id
        may not take that text's orphaned copy in documents/."""
        project = Path(qualcoder_db_path)
        (project / "documents").mkdir(exist_ok=True)
        (project / "documents" / "legacy.txt").write_text("participant A")
        _add(project, 3, "legacy.txt")
        _reload()
        assert _file(3, "P03.txt")["changed"]
        _exec(project, "DELETE FROM source WHERE id = 3")
        _reload()
        new = json.loads(server.import_text_file(
            "fresh.txt", "participant B", create_backup=False))
        assert new["file_id"] == 3
        out = _file(3, "legacy.txt", create_backup=False)
        assert "already holds" in out.get("error", ""), out

    def test_the_ending_half_asks_the_same(self, setup_server,
                                           qualcoder_db_path):
        project = Path(qualcoder_db_path)
        _add(project, 3, "Thomas.Jones")
        _reload()
        assert _file(3, "P03 notes")["changed"]              # a backup
        _exec(project, "DELETE FROM source WHERE id = 3")
        _add(project, 3, "P03 notes", date="2026-09-24 12:00:00")
        _reload()
        out = _file(3, "Thomas.Jones", create_backup=False)
        assert out["error"].startswith("This text has no stored file"), out

    def test_the_same_entry_is_still_recognised(self, legacy):
        assert _file(5, "legacy2.txt")["changed"]
        assert _file(5, "legacy.txt", create_backup=False)["changed"]

    def test_no_date_no_evidence(self, legacy):
        _exec(legacy, "UPDATE source SET date = NULL WHERE id = 5")
        _reload()
        assert _file(5, "legacy2.txt")["changed"]
        out = _file(5, "legacy.txt", create_backup=False)
        assert "already holds" in out.get("error", ""), out


class TestTheBackupsAreOnlyRead:

    def test_P_ro_backups_opened_read_only_and_unchanged(self, legacy,
                                                         monkeypatch):
        assert _file(5, "legacy2.txt")["changed"]
        seen = _spy_connections(monkeypatch)
        before = _snapshot(_backups(legacy))
        assert _file(5, "legacy.txt", create_backup=False)["changed"]
        assert seen and all("?mode=ro&immutable=1" in t and uri
                            for t, uri in seen), seen
        assert _snapshot(_backups(legacy)) == before

    def test_P_wal_a_wal_backup_gains_no_files(self, legacy):
        """R1-2: a read-only open of a WAL database makes -shm and -wal;
        an immutable one makes nothing."""
        assert _file(5, "legacy2.txt")["changed"]
        [b1] = _backups(legacy)
        c = sqlite3.connect(str(b1 / "data.qda"))
        assert c.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        c.close()
        before = sorted(p.name for p in b1.iterdir())
        assert _file(5, "legacy.txt", create_backup=False)["changed"]
        assert sorted(p.name for p in b1.iterdir()) == before

    def test_P_corrupt_an_unreadable_backup_is_skipped(self, legacy):
        assert _file(5, "legacy2.txt")["changed"]
        bad = legacy.parent / f"{legacy.stem}_backup_29990101.qda"
        bad.mkdir()
        (bad / "data.qda").write_bytes(b"not a database")
        now = time.time() + 60
        os.utime(bad, (now, now))                    # the newest
        out = _file(5, "legacy.txt", create_backup=False)
        assert out.get("changed") is True, out


class TestWhichBackupsAreRead:

    def test_P_id_another_entrys_earlier_name_is_not_evidence(self, legacy):
        (legacy / "documents" / "shared.txt").write_text("entry 6's original")
        _add(legacy, 6, "shared.txt")
        _reload()
        assert _file(6, "P06.txt")["changed"]          # backup: 6 = shared.txt
        out = _file(5, "shared.txt", create_backup=False)
        assert "already holds" in out.get("error", ""), out

    def test_P_depth_a_rename_back_after_later_writes(self, legacy):
        assert _file(5, "legacy2.txt")["changed"]
        for i in range(3):
            assert json.loads(server.rename_case(1, f"Case {i}"))["changed"]
        assert _file(5, "legacy.txt", create_backup=False)["changed"]

    def test_P_order_the_newest_backups_are_read(self, legacy):
        """201 backups: only the newest shows the earlier name."""
        assert _file(5, "legacy2.txt", create_backup=False)["changed"]
        base = time.time() - 86400
        for i in range(200):                             # older: legacy2.txt
            dest = legacy.parent / f"{legacy.stem}_backup_2000{i:04d}.qda"
            shutil.copytree(legacy, dest)
            os.utime(dest, (base + i, base + i))
        newest = legacy.parent / f"{legacy.stem}_backup_29990101.qda"
        shutil.copytree(legacy, newest)
        _exec(newest, "UPDATE source SET name = 'legacy.txt' WHERE id = 5")
        os.utime(newest, (base + 1000, base + 1000))
        assert _file(5, "legacy.txt", create_backup=False)["changed"]

    def test_P_bkup_qualcoders_own_backups_count(self, legacy):
        assert _file(5, "legacy2.txt", create_backup=False)["changed"]
        dest = legacy.parent / f"{legacy.stem}_BKUP_20240101_10.qda"
        shutil.copytree(legacy, dest)
        _exec(dest, "UPDATE source SET name = 'legacy.txt' WHERE id = 5")
        assert _file(5, "legacy.txt", create_backup=False)["changed"]


class TestTheBackupsAreReadOnlyWhenNeededAndOnce:

    def test_P_lazy_an_ordinary_rename_reads_no_backup(self, legacy,
                                                       monkeypatch):
        assert _file(5, "legacy2.txt")["changed"]
        calls = []
        real = database.QualcoderDatabase.earlier_name
        monkeypatch.setattr(
            database.QualcoderDatabase, "earlier_name",
            lambda self, *a: calls.append(a) or real(self, *a))
        assert _file(1, "interview_b.txt", create_backup=False)["changed"]
        assert calls == []

    def test_read_once_per_call_not_again_inside_the_transaction(
            self, legacy, monkeypatch):
        """R1-4: the pre-check's answer is carried into the re-check that
        runs under BEGIN IMMEDIATE and the project lock."""
        assert _file(5, "legacy2.txt")["changed"]
        calls = []
        real = database.QualcoderDatabase.earlier_name

        def spy(self, *a):
            calls.append(self.conn.in_transaction)
            return real(self, *a)
        monkeypatch.setattr(database.QualcoderDatabase, "earlier_name", spy)
        assert _file(5, "legacy.txt")["changed"]
        assert calls == [False]

    def test_the_scan_stops_at_the_first_match(self, legacy, monkeypatch):
        assert _file(5, "legacy2.txt", create_backup=False)["changed"]
        base = time.time() - 86400
        for i in range(5):                   # older ones show legacy.txt
            dest = legacy.parent / f"{legacy.stem}_backup_2000{i:04d}.qda"
            shutil.copytree(legacy, dest)
            _exec(dest, "UPDATE source SET name = 'legacy.txt' WHERE id = 5")
            os.utime(dest, (base + i, base + i))
        seen = _spy_connections(monkeypatch)
        assert _file(5, "legacy.txt", create_backup=False)["changed"]
        assert len(seen) == 1, seen
