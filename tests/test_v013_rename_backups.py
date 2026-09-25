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
import logging
import os
import re
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


def _in_a_backup(target):
    """Whether a connection target is a backup's database: by the backup
    folder's own name, not the whole path (a test's temporary folder is
    named after the test)."""
    # Either separator: the project's own connection is a plain path,
    # with backslashes on Windows; a backup's is a file: URI.
    parts = re.split(r"[\\/]", str(target).split("?")[0].rstrip("/\\"))
    if len(parts) < 2:
        return False
    return "_backup_" in parts[-2] or "_BKUP_" in parts[-2]


def _spy_connections(monkeypatch):
    seen = []
    real = sqlite3.connect

    def connect(target, *a, **kw):
        if _in_a_backup(target):
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
            lambda self, *a, **kw: calls.append(a) or real(self, *a, **kw))
        assert _file(1, "interview_b.txt", create_backup=False)["changed"]
        assert calls == []

    def test_read_once_per_call_not_again_inside_the_transaction(
            self, legacy, monkeypatch):
        """R1-4: the pre-check's answer is carried into the re-check that
        runs under BEGIN IMMEDIATE and the project lock."""
        assert _file(5, "legacy2.txt")["changed"]
        calls = []
        real = database.QualcoderDatabase.earlier_name

        def spy(self, *a, **kw):
            calls.append(self.conn.in_transaction)
            return real(self, *a, **kw)
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


class TestTheCarriedAnswerIsCheckedAgain:
    """Fix round 3, F2A-1: the answer carried from the pre-check into the
    re-check under BEGIN IMMEDIATE is tested again against the question
    the re-check asks. Checker A's probe h4, adopted."""

    def test_h4_a_recording_linked_before_the_lock(self, setup_server,
                                                   qualcoder_db_path,
                                                   monkeypatch):
        project = Path(qualcoder_db_path)
        _add(project, 5, "old.pdf")
        _exec(project, "INSERT INTO source (id, name, fulltext, mediapath, "
                       "memo, owner, date) VALUES (10, 'a', NULL, "
                       "'/audio/a.mp3', '', 'gui_user', "
                       "'2024-01-15 10:00:00')")
        bk = project.parent / f"{project.stem}_BKUP_20240101_10.qda"
        shutil.copytree(project, bk)                  # 5 was 'old.pdf'
        _exec(project, "UPDATE source SET name = 'a.txt' WHERE id = 5")
        _reload()
        real = database.QualcoderDatabase.begin_immediate

        def link_then_begin(self):
            # QualCoder's View AV relinks a recording by name (master
            # view_av.py:205-212), here in the window before the lock.
            _exec(project, "UPDATE source SET av_text_id = 5 WHERE id = 10")
            real(self)
        monkeypatch.setattr(database.QualcoderDatabase, "begin_immediate",
                            link_then_begin)
        out = _file(5, "a.pdf", create_backup=False)
        assert "as a broken link" in out.get("error", ""), out
        con = sqlite3.connect(str(project / "data.qda"))
        try:
            assert con.execute("SELECT name FROM source WHERE id = 5"
                               ).fetchone()[0] == "a.txt"
        finally:
            con.close()


class TestTheDocumentsHalfAsksForTheSameText:
    """Fix round 3, F2A-2 (the lead's ruling): QualCoder's Merge projects
    copies each text's date, so for the documents/ half a backup's row is
    evidence only when its id, date AND text equal the current row's,
    compared inside SQLite. The ending half keeps id and date."""

    def test_replay_a_a_merged_text_with_the_same_date(self, setup_server,
                                                       qualcoder_db_path):
        """Checker A's replay a: texts 5 and 6 share a creation second; 6
        is renamed here, deleted in QualCoder, and a merge brings coder
        B's copy of text 5 with that date; it takes id 6."""
        project = Path(qualcoder_db_path)
        docs = project / "documents"
        docs.mkdir(exist_ok=True)
        stamp = "2021-03-02 11:22:33"
        (docs / "p5.txt").write_text("participant five, original")
        (docs / "p6.txt").write_text("participant six, original")
        _add(project, 5, "p5.txt", "participant five", date=stamp)
        _add(project, 6, "p6.txt", "participant six", date=stamp)
        _reload()
        assert _file(6, "P06.txt")["changed"]            # a backup
        _exec(project, "DELETE FROM source WHERE id = 6")
        # QualCoder's merge (master merge_projects.py:858-862, replayed).
        _exec(project, "INSERT INTO source (name, fulltext, mediapath, "
                       "memo, owner, date) VALUES ('p5-coderB.txt', "
                       "'participant five, coder B''s copy', NULL, '', "
                       "'coderB', ?)", (stamp,))
        _reload()
        out = _file(6, "p6.txt", create_backup=False)
        assert "already holds a file called 'p6.txt'" in \
            out.get("error", ""), out

    def test_the_same_text_is_still_recognised(self, legacy):
        assert _file(5, "legacy2.txt")["changed"]
        assert _file(5, "legacy.txt", create_backup=False)["changed"]

    def test_an_edited_text_is_refused_its_old_copy(self, legacy):
        """The safe direction the ruling accepts: the copy holds the text
        as it was, and QualCoder's own Rename remains."""
        assert _file(5, "legacy2.txt")["changed"]
        _exec(legacy, "UPDATE source SET fulltext = 'pseudonymised' "
                      "WHERE id = 5")
        _reload()
        out = _file(5, "legacy.txt", create_backup=False)
        assert "already holds" in out.get("error", ""), out

    def test_the_ending_half_does_not_ask_for_the_text(self, setup_server,
                                                       qualcoder_db_path):
        project = Path(qualcoder_db_path)
        _add(project, 5, "Thomas.Jones")
        _reload()
        assert _file(5, "P05 notes")["changed"]           # a backup
        _exec(project, "UPDATE source SET fulltext = 'edited' WHERE id = 5")
        _reload()
        assert _file(5, "Thomas.Jones", create_backup=False)["changed"]

    def test_no_backup_text_is_read_into_python(self, legacy, monkeypatch):
        assert _file(5, "legacy2.txt")["changed"]
        seen = []
        real = sqlite3.connect

        class Spy:
            def __init__(self, con):
                self._con = con

            def execute(self, sql, args=()):
                seen.append(sql)
                return self._con.execute(sql, args)

            def close(self):
                self._con.close()

        def connect(target, *a, **kw):
            con = real(target, *a, **kw)
            return Spy(con) if _in_a_backup(target) else con
        monkeypatch.setattr(database.sqlite3, "connect", connect)
        assert _file(5, "legacy.txt", create_backup=False)["changed"]
        # Fix round 4, F3A-2: the text compared as the database's bytes.
        assert seen == ["SELECT name, date FROM source WHERE id = ? AND "
                        "CAST(fulltext AS BLOB) IS ?"], seen


class TestTheDateToTheSecondAndTheEmptyDate:
    """Fix round 3, F2A-3 and F2A-7: the date is compared to the second,
    not the day, and an empty date is never evidence."""

    def test_two_dates_one_second_apart_on_one_day(self, setup_server,
                                                   qualcoder_db_path):
        project = Path(qualcoder_db_path)
        (project / "documents").mkdir(exist_ok=True)
        (project / "documents" / "legacy.txt").write_text("participant A")
        _add(project, 3, "legacy.txt", "same text",
             date="2026-09-24 10:00:00")
        _reload()
        assert _file(3, "P03.txt")["changed"]            # a backup
        _exec(project, "DELETE FROM source WHERE id = 3")
        _add(project, 3, "fresh.txt", "same text",
             date="2026-09-24 10:00:01")
        _reload()
        out = _file(3, "legacy.txt", create_backup=False)
        assert "already holds" in out.get("error", ""), out

    def test_an_empty_date_is_no_evidence(self, legacy):
        _exec(legacy, "UPDATE source SET date = '' WHERE id = 5")
        _reload()
        assert _file(5, "legacy2.txt")["changed"]         # backup: ''
        out = _file(5, "legacy.txt", create_backup=False)
        assert "already holds" in out.get("error", ""), out


class TestAHalfWrittenBackupIsSkipped:
    """Fix round 3, F2A-5: a backup with a -journal or -wal file beside its
    database may hold a state that was never committed; immutable would
    read it as it stands, so it is skipped."""

    @pytest.mark.parametrize("side", ["data.qda-journal", "data.qda-wal"])
    def test_skipped(self, legacy, side):
        assert _file(5, "legacy2.txt")["changed"]
        [b1] = _backups(legacy)
        (b1 / side).write_bytes(b"")
        out = _file(5, "legacy.txt", create_backup=False)
        assert "already holds" in out.get("error", ""), out
        (b1 / side).unlink()
        assert _file(5, "legacy.txt", create_backup=False)["changed"]


def _replay_a(project, text5, text6, merged_text):
    """Checker A's replay a with the texts given (REVERIFY_RENAME_F3_A.md,
    probe n1): legacy texts 5 and 6 share a second; 6 renamed here (a
    backup), deleted in QualCoder, and QualCoder's Merge projects (master
    merge_projects.py:858-862, replayed) inserts coder B's text with the
    same date: it takes id 6. Then rename 6 back to 'p6.txt'."""
    stamp = "2021-03-02 11:22:33"
    docs = project / "documents"
    docs.mkdir(exist_ok=True)
    (docs / "p5.txt").write_text("participant five, original")
    (docs / "p6.txt").write_text("participant six, original")
    for fid, name, text in ((5, "p5.txt", text5), (6, "p6.txt", text6)):
        _exec(project, "INSERT INTO source (id, name, fulltext, mediapath, "
                       "memo, owner, date) VALUES (?, ?, ?, NULL, '', "
                       "'gui_user', ?)", (fid, name, text, stamp))
    _reload()
    assert _file(6, "P06.txt")["changed"]
    _exec(project, "DELETE FROM source WHERE id = 6")
    _exec(project, "INSERT INTO source (name, fulltext, mediapath, memo, "
                   "owner, date) VALUES ('p5-coderB.txt', ?, NULL, '', "
                   "'coderB', ?)", (merged_text, stamp))
    _reload()
    return _file(6, "p6.txt", create_backup=False)


class TestAnEmptyOrUnreadableTextIsNoEvidence:
    """Fix round 4, F3A-1 to F3A-3."""

    @pytest.mark.parametrize("text", ["", None])
    def test_n1_replay_a_with_empty_texts(self, setup_server,
                                          qualcoder_db_path, text):
        out = _replay_a(Path(qualcoder_db_path), text, text, text)
        assert "already holds a file called 'p6.txt'" in \
            out.get("error", ""), out

    def test_n2_n4_a_text_that_is_not_utf8(self, legacy, caplog):
        """Compared as the database's own bytes: the rename back of the
        same text is recognised, and nothing of the text is logged."""
        body = b"Thomas Jones, 14 Mill Lane, said \xff\xfe hello"
        _exec(legacy, "UPDATE source SET fulltext = CAST(? AS TEXT) "
                      "WHERE id = 5", (body,))
        _reload()
        caplog.set_level(logging.DEBUG)
        assert _file(5, "P05.txt")["changed"]            # a backup
        out = _file(5, "legacy.txt", create_backup=False)
        assert out.get("changed") is True, out
        assert not [r for r in caplog.records
                    if "Mill Lane" in r.getMessage()]

    def test_a_text_that_cannot_be_read_is_the_ordinary_refusal(
            self, legacy, monkeypatch, caplog):
        assert _file(5, "legacy2.txt")["changed"]
        real = database.QualcoderDatabase.current_text

        def unreadable(self, file_id):
            return self.TEXT_UNREADABLE
        monkeypatch.setattr(database.QualcoderDatabase, "current_text",
                            unreadable)
        out = _file(5, "legacy.txt", create_backup=False)
        assert out["error"].startswith(
            "The project's documents folder already holds"), out
        assert real is not None

    def test_n3_null_and_the_text_None_differ(self, legacy, monkeypatch):
        """The race of probe n3: the backup shows 5 with the text 'None';
        the text becomes NULL in the window before the lock."""
        _exec(legacy, "UPDATE source SET fulltext = 'None' WHERE id = 5")
        _reload()
        assert _file(5, "P05.txt")["changed"]
        real = database.QualcoderDatabase.begin_immediate

        def begin(self_db):
            _exec(legacy, "UPDATE source SET fulltext = NULL WHERE id = 5")
            real(self_db)
        monkeypatch.setattr(database.QualcoderDatabase, "begin_immediate",
                            begin)
        out = _file(5, "legacy.txt", create_backup=False)
        assert "already holds" in out.get("error", ""), out

    def test_the_key_carries_the_texts_type(self):
        key = server._text_evidence_key
        assert key(None) != key("None")
        assert key(b"None") != key("None")
        assert key(b"abc") == key(b"abc")


def test_the_description_says_the_text_must_be_the_same():
    """Fix round 4, F3A-4."""
    flat = " ".join(server.rename_file.__doc__.split())
    assert "may take back its own copy in the documents folder under a " \
           "name such a backup shows it with and, for its documents copy, " \
           "the same text." in flat


class TestBothTextGuards:
    """Fix round 4, F3A-1: an empty or NULL text is refused by the
    pre-check before any backup is read, and the reader refuses it too."""

    @pytest.mark.parametrize("text", ["", None])
    def test_no_backup_is_read_for_an_empty_text(self, legacy, monkeypatch,
                                                 text):
        _exec(legacy, "UPDATE source SET fulltext = ? WHERE id = 5", (text,))
        _reload()
        assert _file(5, "legacy2.txt")["changed"]
        calls = []
        real = database.QualcoderDatabase.earlier_name
        monkeypatch.setattr(
            database.QualcoderDatabase, "earlier_name",
            lambda self, *a, **kw: calls.append(kw) or real(self, *a, **kw))
        out = _file(5, "legacy.txt", create_backup=False)
        assert "already holds" in out.get("error", ""), out
        assert calls == []

    @pytest.mark.parametrize("text", [b"", None])
    def test_the_reader_refuses_it_too(self, legacy, text):
        _exec(legacy, "UPDATE source SET fulltext = '' WHERE id = 5")
        _reload()
        assert _file(5, "legacy2.txt")["changed"]         # backup: ''
        db = server.get_db()
        found = db.earlier_name(5, "2024-01-15 10:00:00",
                                lambda name: name == "legacy.txt",
                                same_text=True, text=text)
        assert found is None


class _RecordingConnection:
    """Stands in for a QualcoderDatabase's connection: records each SQL
    statement, and can make the first one matching `fail` raise."""

    def __init__(self, con, fail=None):
        self._con, self.sql, self._fail = con, [], fail

    def execute(self, sql, args=()):
        self.sql.append(sql)
        if self._fail and self._fail in sql:
            raise sqlite3.OperationalError("simulated read failure")
        return self._con.execute(sql, args)

    def __getattr__(self, name):
        return getattr(self._con, name)


class TestHowTheTextAndTheSavedRowsAreRead:
    """Fix round 4, F3A-2 and F3B-1: the two read paths pinned as reads."""

    def test_a_text_read_that_fails_is_unreadable_not_an_error(self,
                                                                legacy):
        db = server.get_db()
        real = db.conn
        db.conn = _RecordingConnection(real, fail="CAST(fulltext AS BLOB)")
        try:
            assert db.current_text(5) is db.TEXT_UNREADABLE
        finally:
            db.conn = real

    def test_saved_rows_are_read_as_text_first(self, legacy):
        con = sqlite3.connect(str(legacy / "data.qda"))
        con.execute("CREATE TABLE files_filter (filterid integer primary "
                    "key, name text, filter text, owner text)")
        con.execute("INSERT INTO files_filter (name, filter) VALUES "
                    "('f', 'legacy.txt only')")
        con.commit()
        con.close()
        _reload()
        db = server.get_db()
        real = db.conn
        db.conn = _RecordingConnection(real)
        try:
            found = db.old_name_left_in("file", 5, "legacy.txt")
            sql = db.conn.sql
        finally:
            db.conn = real
        assert found.get("saved_filters") == 1
        assert not [s for s in sql if "CAST(" in s], sql

    def test_the_bytes_only_when_the_text_read_raises(self, legacy):
        con = sqlite3.connect(str(legacy / "data.qda"))
        con.execute("CREATE TABLE files_filter (filterid integer primary "
                    "key, name text, filter text, owner text)")
        con.execute("INSERT INTO files_filter (name, filter) VALUES "
                    "('f', 'legacy.txt only')")
        con.commit()
        con.close()
        _reload()
        db = server.get_db()
        real = db.conn
        db.conn = _RecordingConnection(real,
                                       fail="SELECT name, filter FROM")
        try:
            found = db.old_name_left_in("file", 5, "legacy.txt")
            sql = db.conn.sql
        finally:
            db.conn = real
        assert found.get("saved_filters") == 1
        assert any("CAST(name AS BLOB), CAST(filter AS BLOB)" in s
                   for s in sql), sql
