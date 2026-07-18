# Folded into the main suite from the v0.6.0-alpha parallel test campaign
# (track 5b, fault injection); adapted paths/fixtures only — test logic unchanged.
"""Fault-injection tests for qualcoder_mcp write-failure recovery paths.

Track 5b — targets the uncovered recovery arcs found by the property-testing
track (coverage run at v0.6.0-alpha):

  R1: server.py:2399-2421   import_text_file mid-write exception region
  R2: server.py:2513-2516, 2532-2546 (link_file_to_case),
      server.py:2610-2613, 2623-2638 (delete_coding)
  R3: database.py:2498-2502 (add_coding), 2572-2576 (add_code),
      2616-2619 (add_memo_to_coding), 2694-2699 (delete_coding),
      2868-2878 (import_text_file), 2965-2970 (link_file_to_case)
  R4: server.py:2847-2867   restore_backup confirmed-restore faults
  R5: server.py:2238-2239, 2247-2250  apply_codings batch-commit except +
      DatabaseLockedError between check and commit

Method: faults are injected at the precise seams with pytest monkeypatching —
a proxy around the write connection makes cursor.execute / conn.commit /
conn.rollback raise sqlite3 errors mid-operation; backup_before_write raises
OSError; a QualCoder project_in_use.lock flips from stale to fresh between
the pre-write check and the commit (TOCTOU); shutil.copytree fails partway
through a restore swap.

For every injected fault the tests assert:
  1. the tool returns structured error JSON (no raw traceback),
  2. the database is logically identical to its pre-call state (iterdump hash),
  3. the global connection ends up READ-ONLY and the server is not bricked
     (a write through the connection fails with the read-only error, a
     subsequent read tool call succeeds),
  4. no stray backup litter beyond what the design specifies, and foreign
     lock files are left untouched,
  5. for restore_backup: the live project is never left half-replaced
     (either fully old, or fully new, with a safety backup of the old state).

Self-contained: builds its own v14 projects (same schema as tests/conftest.py)
and does not use any fixtures from the main test suite. Runs against the
qualcoder_mcp package importable from the active environment.
"""

import json
import shutil
import sqlite3
import hashlib
import time
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import qualcoder_mcp.server as server
from qualcoder_mcp.database import (
    QualcoderDatabase,
    QUALCODER_LOCK_FILENAME,
)
from qualcoder_mcp.sessions import (
    SessionManager,
    AICodingSession,
    CodingSuggestion,
)


FULLTEXT = "alpha bravo charlie delta echo foxtrot golf hotel india juliet"

SCHEMA_SQL = [
    """CREATE TABLE project (databaseversion TEXT, date TEXT, memo TEXT, about TEXT,
        bookmarkfile INTEGER, bookmarkpos INTEGER, codername TEXT, recently_used_codes TEXT)""",
    """CREATE TABLE code_cat (catid INTEGER PRIMARY KEY, name TEXT UNIQUE, memo TEXT,
        owner TEXT, date TEXT, supercatid INTEGER)""",
    """CREATE TABLE code_name (cid INTEGER PRIMARY KEY, name TEXT UNIQUE, memo TEXT,
        catid INTEGER, owner TEXT, date TEXT, color TEXT)""",
    """CREATE TABLE source (id INTEGER PRIMARY KEY, name TEXT, fulltext TEXT, mediapath TEXT,
        memo TEXT, owner TEXT, date TEXT, av_text_id INTEGER, risid INTEGER, UNIQUE(name))""",
    """CREATE TABLE code_text (ctid INTEGER PRIMARY KEY, cid INTEGER, fid INTEGER,
        seltext TEXT, pos0 INTEGER, pos1 INTEGER, owner TEXT, date TEXT, memo TEXT,
        avid INTEGER, important INTEGER, UNIQUE(cid, fid, pos0, pos1, owner))""",
    """CREATE TABLE cases (caseid INTEGER PRIMARY KEY, name TEXT, memo TEXT, owner TEXT,
        date TEXT, CONSTRAINT ucm UNIQUE(name))""",
    """CREATE TABLE case_text (id INTEGER PRIMARY KEY, caseid INTEGER, fid INTEGER,
        pos0 INTEGER, pos1 INTEGER, memo TEXT, owner TEXT, date TEXT)""",
    """CREATE TABLE annotation (anid INTEGER PRIMARY KEY, fid INTEGER, pos0 INTEGER,
        pos1 INTEGER, memo TEXT, owner TEXT, date TEXT)""",
    """CREATE TABLE journal (jid INTEGER PRIMARY KEY, name TEXT, jentry TEXT, date TEXT, owner TEXT)""",
    """CREATE TABLE attribute_type (name TEXT PRIMARY KEY, date TEXT, owner TEXT,
        memo TEXT, caseOrFile TEXT, valuetype TEXT)""",
    """CREATE TABLE attribute (attrid INTEGER PRIMARY KEY, name TEXT, attr_type TEXT,
        value TEXT, id INTEGER, date TEXT, owner TEXT)""",
    """CREATE TABLE code_image (imid INTEGER PRIMARY KEY, id INTEGER, x1 INTEGER, y1 INTEGER,
        width INTEGER, height INTEGER, cid INTEGER, memo TEXT, date TEXT, owner TEXT,
        important INTEGER, pdf_page INTEGER)""",
    """CREATE TABLE code_av (avid INTEGER PRIMARY KEY, cid INTEGER, id INTEGER, pos0 INTEGER,
        pos1 INTEGER, memo TEXT, owner TEXT, date TEXT, important INTEGER DEFAULT 0)""",
]


def build_project(parent: Path, stem: str) -> Path:
    """Build a valid v14 QualCoder project (same shape as tests/conftest.py)."""
    folder = parent / f"{stem}.qda"
    folder.mkdir()
    conn = sqlite3.connect(str(folder / "data.qda"))
    cur = conn.cursor()
    for ddl in SCHEMA_SQL:
        cur.execute(ddl)
    cur.execute(
        "INSERT INTO project (databaseversion, date, memo, about, codername) "
        "VALUES ('v14', '2024-01-15', 'fault-injection project', 'About', 'TestCoder')"
    )
    cur.execute("INSERT INTO code_cat VALUES (1, 'Category A', '', 'TestCoder', '2024-01-15', NULL)")
    cur.execute("INSERT INTO code_name VALUES (1, 'Stress', '', 1, 'TestCoder', '2024-01-15', '#FF0000')")
    cur.execute("INSERT INTO code_name VALUES (2, 'Coping', '', 1, 'TestCoder', '2024-01-15', '#00FF00')")
    cur.execute(
        "INSERT INTO source (id, name, fulltext, mediapath, memo, owner, date) "
        "VALUES (1, 'interview.txt', ?, NULL, '', 'TestCoder', '2024-01-15')",
        (FULLTEXT,),
    )
    cur.execute(
        "INSERT INTO source (id, name, fulltext, mediapath, memo, owner, date) "
        "VALUES (2, 'notes.txt', 'Field notes from observation.', NULL, '', 'TestCoder', '2024-01-16')"
    )
    cur.execute(
        "INSERT INTO code_text (ctid, cid, fid, seltext, pos0, pos1, owner, date, memo, important) "
        "VALUES (1, 1, 1, 'alpha', 0, 5, 'TestCoder', '2024-01-15', 'existing coding', NULL)"
    )
    cur.execute("INSERT INTO cases VALUES (1, 'Case A', '', 'TestCoder', '2024-01-15')")
    cur.execute(
        "INSERT INTO attribute_type VALUES ('Source', '2024-01-15', 'TestCoder', '', 'file', 'character')"
    )
    conn.commit()
    conn.close()
    # a non-database asset so partial folder copies are detectable
    media = folder / "images"
    media.mkdir()
    (media / "marker.bin").write_bytes(b"fault-injection-media-marker")
    return folder


# ---------------------------------------------------------------------------
# out-of-band inspection helpers (always a fresh read-only connection)
# ---------------------------------------------------------------------------

def db_hash(project_folder: Path) -> str:
    """Deterministic hash of the full logical DB content (schema + rows)."""
    data = Path(project_folder) / "data.qda"
    conn = sqlite3.connect(f"file:{data}?mode=ro", uri=True)
    try:
        h = hashlib.sha256()
        for line in conn.iterdump():
            h.update(line.encode("utf-8", "surrogatepass"))
            h.update(b"\n")
        return h.hexdigest()
    finally:
        conn.close()


def folder_state(folder: Path) -> dict:
    """Map of relative file path -> content hash for a project folder.

    data.qda is hashed logically (iterdump) so byte-level SQLite noise
    (freelist layout etc.) cannot cause false mismatches; every other file
    is hashed by bytes. Lock files are ignored (never part of project state).
    """
    out = {}
    folder = Path(folder)
    for p in sorted(folder.rglob("*")):
        if not p.is_file() or p.suffix == ".lock":
            continue
        rel = str(p.relative_to(folder))
        if rel == "data.qda":
            out[rel] = db_hash(folder)
        else:
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


# ---------------------------------------------------------------------------
# fault-injection machinery
# ---------------------------------------------------------------------------

@dataclass
class ExecFault:
    """Raise `exc` on the `nth` execute() whose SQL contains `needle`."""
    needle: str
    exc: Exception
    nth: int = 1
    hits: int = field(default=0, compare=False)


class FaultyConn:
    """Proxy around a real sqlite3.Connection that injects faults at the
    exact seams: cursor.execute, conn.commit, conn.rollback.

    rollback_from: first rollback() call number (1-based) from which
    rollback_exc is raised — lets a database-layer rollback succeed while
    the server-layer rollback that follows it fails."""

    def __init__(self, real, execute_faults=(), commit_exc=None,
                 rollback_exc=None, rollback_from=1):
        self._real = real
        self._execute_faults = list(execute_faults)
        self._commit_exc = commit_exc
        self._rollback_exc = rollback_exc
        self._rollback_from = rollback_from
        self.commit_calls = 0
        self.rollback_calls = 0

    def execute(self, sql, *args, **kwargs):
        norm = " ".join(str(sql).split()).lower()
        for f in self._execute_faults:
            if f.needle in norm:
                f.hits += 1
                if f.hits == f.nth:
                    raise f.exc
        return self._real.execute(sql, *args, **kwargs)

    def commit(self):
        self.commit_calls += 1
        if self._commit_exc is not None:
            raise self._commit_exc
        return self._real.commit()

    def rollback(self):
        self.rollback_calls += 1
        if (self._rollback_exc is not None
                and self.rollback_calls >= self._rollback_from):
            raise self._rollback_exc
        return self._real.rollback()

    def __getattr__(self, name):
        return getattr(self._real, name)


class Env:
    """A server-wired scratch project."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.path = str(folder)
        self.work = folder.parent

    def hash(self) -> str:
        return db_hash(self.folder)

    def state(self) -> dict:
        return folder_state(self.folder)

    def backup_names(self) -> set:
        return {p.name for p in self.work.glob(f"{self.folder.stem}_backup_*.qda")}

    @property
    def lock(self) -> Path:
        return self.folder / QUALCODER_LOCK_FILENAME

    def plant_lock(self, fresh: bool, holder: str = "qc_foreign") -> None:
        """Write a QualCoder heartbeat lock file: fresh (=active) or stale."""
        epoch = time.time() if fresh else time.time() - 3600.0
        self.lock.write_text(f"{holder}\n{epoch}", encoding="utf-8")


@pytest.fixture
def fi_env(tmp_path, monkeypatch):
    """Fresh v14 project wired into the server globals (read-only), with a
    private temp HOME and session store. Restores server state afterwards."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows home for expanduser()
    monkeypatch.delenv("QUALCODER_PROJECT_PATH", raising=False)

    work = tmp_path / "work"
    work.mkdir()
    folder = build_project(work, "fi_project")

    saved = (server.db, server.current_project_path, server.session_manager)
    server.db = QualcoderDatabase(str(folder), read_only=True)
    server.current_project_path = str(folder)
    server.session_manager = SessionManager(str(tmp_path / "sessions"))

    yield Env(folder)

    if server.db is not None:
        try:
            server.db.close()
        except Exception:
            pass
    server.db, server.current_project_path, server.session_manager = saved


@pytest.fixture
def raw_project(tmp_path):
    """A standalone project for database-layer tests (no server wiring)."""
    work = tmp_path / "raw"
    work.mkdir()
    return build_project(work, "raw_project")


@pytest.fixture
def write_faults(monkeypatch):
    """Installer that intercepts the read-write connection upgrade.

    Wraps server.get_db so that when a tool upgrades to read-write mode the
    fresh write connection is replaced with a FaultyConn (and/or a fresh
    foreign QualCoder lock is planted right after the upgrade, i.e. between
    the pre-write gate check and hold_project_lock)."""
    installed = {}

    def install(env: Optional[Env] = None, execute_faults=(), commit_exc=None,
                rollback_exc=None, rollback_from=1, plant_fresh_lock=False):
        real_get_db = server.get_db

        def fake_get_db(read_only=True):
            db_obj = real_get_db(read_only=read_only)
            if not read_only:
                if plant_fresh_lock and env is not None and "planted" not in installed:
                    env.plant_lock(fresh=True)
                    installed["planted"] = True
                if ((execute_faults or commit_exc or rollback_exc)
                        and not isinstance(db_obj.conn, FaultyConn)):
                    db_obj.conn = FaultyConn(
                        db_obj.conn,
                        execute_faults=execute_faults,
                        commit_exc=commit_exc,
                        rollback_exc=rollback_exc,
                        rollback_from=rollback_from,
                    )
                    installed["conn"] = db_obj.conn
            return db_obj

        monkeypatch.setattr(server, "get_db", fake_get_db)
        return installed

    return install


@pytest.fixture
def lock_flip(monkeypatch):
    """TOCTOU injection: a STALE foreign lock exists at tool entry (so the
    write gate passes and hold_project_lock proceeds without holding), then
    the lock becomes FRESH (QualCoder just opened the project) while the
    database-layer write runs — before _recheck_lock_before_commit."""

    def install(env: Env, method_name: str):
        env.plant_lock(fresh=False)  # stale at entry
        orig = getattr(QualcoderDatabase, method_name)

        def wrapper(self, *args, **kwargs):
            env.plant_lock(fresh=True)  # QualCoder grabs the project mid-write
            return orig(self, *args, **kwargs)

        monkeypatch.setattr(QualcoderDatabase, method_name, wrapper)

    return install


# ---------------------------------------------------------------------------
# shared assertions
# ---------------------------------------------------------------------------

def assert_structured_error(result: str, *needles: str) -> dict:
    """Assertion 1: structured error JSON, no raw traceback."""
    assert isinstance(result, str)
    assert "Traceback (most recent call last)" not in result
    assert '\n  File "' not in result
    data = json.loads(result)
    assert isinstance(data, dict)
    assert "error" in data, f"no error key in: {data}"
    for needle in needles:
        assert needle.lower() in data["error"].lower(), (
            f"expected {needle!r} in error: {data['error']!r}"
        )
    return data


def assert_connection_recovered(env: Env):
    """Assertion 3: global connection is read-only and the server still
    answers read tools (not bricked)."""
    assert server.db is not None, "global db connection is None after fault"
    assert server.db.read_only is True, "connection was left in read-write mode"
    with pytest.raises(sqlite3.OperationalError, match="readonly|read-only"):
        server.db.conn.execute("UPDATE project SET memo = 'fi write probe'")
    summary = json.loads(server.get_project_summary())
    assert "project_info" in summary, f"read tool failed after fault: {summary}"


def make_approved_session(env: Env) -> AICodingSession:
    sess = AICodingSession(
        project_path=env.path,
        description="fault injection",
        file_ids=[1],
        code_names=["Stress", "Coping"],
        instruction="fi",
        min_confidence=0.5,
    )
    for cid, cname, p0, p1, seg in [
        (1, "Stress", 0, 5, "alpha"),
        (2, "Coping", 6, 11, "bravo"),
    ]:
        sess.add_suggestion(CodingSuggestion(
            file_id=1, file_name="interview.txt",
            code_id=cid, code_name=cname,
            start_pos=p0, end_pos=p1, segment_text=seg,
            reasoning="fault injection", confidence=0.9,
            status="approved",
        ))
    server.session_manager.save_session(sess)
    return sess


DISK_ERR = "Injected: disk I/O error"


# ===========================================================================
# R5 — apply_codings: batch-commit failure + lock between check and commit
# ===========================================================================

class TestApplyCodingsFaults:

    def test_commit_and_rollback_both_fail(self, fi_env, write_faults):
        """conn.commit raises mid-batch AND conn.rollback raises too
        (server.py:2234-2246 incl. the except-pass at 2238-2239)."""
        env = fi_env
        sess = make_approved_session(env)
        pre = env.hash()
        before = env.backup_names()
        installed = write_faults(
            commit_exc=sqlite3.OperationalError(DISK_ERR + " on commit"),
            rollback_exc=sqlite3.OperationalError("Injected: cannot rollback"),
        )

        result = server.apply_codings(sess.session_id, create_backup=False)

        data = assert_structured_error(result, "Failed to apply codings",
                                       "rolled back")
        assert data["applied_before_failure"] == 2
        proxy = installed["conn"]
        assert proxy.commit_calls == 1 and proxy.rollback_calls == 1
        assert env.hash() == pre, "DB changed despite failed commit"
        assert env.backup_names() == before
        assert not env.lock.exists(), "our own lock file left behind"
        assert_connection_recovered(env)
        # the session must NOT be marked applied
        reloaded = server.session_manager.load_session(sess.session_id)
        assert len(reloaded.filter_by_status("approved")) == 2
        assert len(reloaded.filter_by_status("applied")) == 0

    def test_second_insert_fails_mid_batch(self, fi_env, write_faults):
        """cursor.execute raises sqlite3.OperationalError on the SECOND
        insert of the batch (database.py:2499-2502 through apply_codings).
        The pre-write backup is kept by design and must equal pre-call state."""
        env = fi_env
        sess = make_approved_session(env)
        pre = env.hash()
        before = env.backup_names()
        write_faults(execute_faults=[
            ExecFault("insert into code_text",
                      sqlite3.OperationalError(DISK_ERR), nth=2),
        ])

        result = server.apply_codings(sess.session_id, create_backup=True)

        data = assert_structured_error(result, "rolled back")
        assert data["applied_before_failure"] == 1
        assert env.hash() == pre, "first insert of the batch was not rolled back"
        new = env.backup_names() - before
        assert len(new) == 1, f"expected exactly the pre-write backup, got {new}"
        assert db_hash(env.work / new.pop()) == pre
        assert not env.lock.exists()
        assert_connection_recovered(env)
        reloaded = server.session_manager.load_session(sess.session_id)
        assert len(reloaded.filter_by_status("applied")) == 0

    def test_qualcoder_grabs_project_during_connection_upgrade(
            self, fi_env, write_faults):
        """A FRESH project_in_use.lock appears between the pre-write gate
        check and hold_project_lock (server.py:2247-2250)."""
        env = fi_env
        sess = make_approved_session(env)
        pre = env.hash()
        before = env.backup_names()
        write_faults(env=env, plant_fresh_lock=True)

        result = server.apply_codings(sess.session_id, create_backup=True)

        assert_structured_error(result, "open in QualCoder")
        assert env.hash() == pre
        assert env.backup_names() == before, "backup created despite lock refusal"
        assert env.lock.exists(), "foreign lock file was removed"
        assert env.lock.read_text().startswith("qc_foreign")
        assert_connection_recovered(env)
        reloaded = server.session_manager.load_session(sess.session_id)
        assert len(reloaded.filter_by_status("applied")) == 0

    def test_stale_lock_becomes_active_before_commit(self, fi_env, lock_flip):
        """Stale foreign lock at entry, refreshed mid-write: the TOCTOU
        re-check before commit must abort and roll back real inserts."""
        env = fi_env
        sess = make_approved_session(env)
        lock_flip(env, "add_coding")
        pre = env.hash()
        before = env.backup_names()

        result = server.apply_codings(sess.session_id, create_backup=False)

        assert_structured_error(result, "open in QualCoder")
        assert env.hash() == pre, "batch inserts survived the lock re-check abort"
        assert env.backup_names() == before
        assert env.lock.exists(), "foreign lock file was removed"
        assert_connection_recovered(env)


# ===========================================================================
# R1 — import_text_file mid-write exception region (server.py:2399-2421)
#      + R3 database.py:2868-2878
# ===========================================================================

class TestImportTextFileFaults:

    def test_lock_flip_between_check_and_commit(self, fi_env, lock_flip):
        """DatabaseLockedError from the pre-commit re-check
        (server.py:2399-2404 + 2419-2421)."""
        env = fi_env
        lock_flip(env, "import_text_file")
        pre = env.hash()
        before = env.backup_names()

        result = server.import_text_file(
            "brand_new.txt", "Some new content.", create_backup=False)

        assert_structured_error(result, "open in QualCoder")
        assert env.hash() == pre, "import survived the lock re-check abort"
        assert env.backup_names() == before
        assert env.lock.exists(), "foreign lock file was removed"
        assert_connection_recovered(env)

    def test_unique_race_integrity_error(self, fi_env, write_faults):
        """sqlite3.IntegrityError (UNIQUE) on the insert — e.g. the file was
        created by another writer after validation (database.py:2868-2873 ->
        ValueError -> server.py:2405-2411)."""
        env = fi_env
        pre = env.hash()
        before = env.backup_names()
        write_faults(execute_faults=[
            ExecFault("insert into source",
                      sqlite3.IntegrityError("UNIQUE constraint failed: source.name")),
        ])

        result = server.import_text_file(
            "brand_new.txt", "Some new content.", create_backup=False)

        assert_structured_error(result, "already exists")
        assert env.hash() == pre
        assert env.backup_names() == before
        assert not env.lock.exists()
        assert_connection_recovered(env)

    def test_non_unique_integrity_error(self, fi_env, write_faults):
        """sqlite3.IntegrityError that is NOT a unique violation
        (database.py:2874 -> RuntimeError -> server.py:2412-2418)."""
        env = fi_env
        pre = env.hash()
        before = env.backup_names()
        write_faults(execute_faults=[
            ExecFault("insert into source",
                      sqlite3.IntegrityError("NOT NULL constraint failed: source.owner")),
        ])

        result = server.import_text_file(
            "brand_new.txt", "Some new content.", create_backup=False)

        assert_structured_error(result, "Database error",
                                "Failed to import text file")
        assert env.hash() == pre
        assert env.backup_names() == before
        assert not env.lock.exists()
        assert_connection_recovered(env)

    def test_lock_flip_and_rollback_failure(self, fi_env, lock_flip,
                                            write_faults):
        """Lock re-check aborts the write AND conn.rollback raises too
        (server.py:2402-2403, the except-pass inside the locked path). The
        uncommitted INSERT must still be discarded when the connection is
        downgraded."""
        env = fi_env
        lock_flip(env, "import_text_file")
        write_faults(rollback_exc=sqlite3.OperationalError("Injected: cannot rollback"))
        pre = env.hash()
        before = env.backup_names()

        result = server.import_text_file(
            "brand_new.txt", "Some new content.", create_backup=False)

        assert_structured_error(result, "open in QualCoder")
        assert env.hash() == pre, "uncommitted INSERT survived the failed rollback"
        assert env.backup_names() == before
        assert env.lock.exists()
        assert_connection_recovered(env)

    def test_unique_race_and_rollback_failure(self, fi_env, write_faults):
        """ValueError path where the SERVER-side rollback fails (the
        database-layer rollback succeeded first): server.py:2408-2409."""
        env = fi_env
        pre = env.hash()
        before = env.backup_names()
        write_faults(
            execute_faults=[ExecFault(
                "insert into source",
                sqlite3.IntegrityError("UNIQUE constraint failed: source.name"))],
            rollback_exc=sqlite3.OperationalError("Injected: cannot rollback"),
            rollback_from=2,  # db-layer rollback (call 1) succeeds
        )

        result = server.import_text_file(
            "brand_new.txt", "Some new content.", create_backup=False)

        assert_structured_error(result, "already exists")
        assert env.hash() == pre
        assert env.backup_names() == before
        assert not env.lock.exists()
        assert_connection_recovered(env)

    def test_disk_error_and_rollback_failure(self, fi_env, write_faults):
        """RuntimeError path where the SERVER-side rollback fails:
        server.py:2415-2416."""
        env = fi_env
        pre = env.hash()
        before = env.backup_names()
        write_faults(
            execute_faults=[ExecFault(
                "insert into source", sqlite3.OperationalError(DISK_ERR))],
            rollback_exc=sqlite3.OperationalError("Injected: cannot rollback"),
            rollback_from=2,  # db-layer rollback (call 1) succeeds
        )

        result = server.import_text_file(
            "brand_new.txt", "Some new content.", create_backup=False)

        assert_structured_error(result, "Database error",
                                "Failed to import text file")
        assert env.hash() == pre
        assert env.backup_names() == before
        assert not env.lock.exists()
        assert_connection_recovered(env)

    def test_disk_error_mid_insert_keeps_backup(self, fi_env, write_faults):
        """Generic sqlite3.OperationalError mid-write (database.py:2875-2878
        -> server.py:2412-2418) with create_backup=True: exactly one backup,
        equal to the pre-call state."""
        env = fi_env
        pre = env.hash()
        before = env.backup_names()
        write_faults(execute_faults=[
            ExecFault("insert into source", sqlite3.OperationalError(DISK_ERR)),
        ])

        result = server.import_text_file(
            "brand_new.txt", "Some new content.", create_backup=True)

        assert_structured_error(result, "Database error",
                                "Failed to import text file")
        assert env.hash() == pre
        new = env.backup_names() - before
        assert len(new) == 1, f"expected exactly the pre-write backup, got {new}"
        assert db_hash(env.work / new.pop()) == pre
        assert not env.lock.exists()
        assert_connection_recovered(env)


# ===========================================================================
# R2 — link_file_to_case backup-failure abort + rollback/downgrade
#      (server.py:2513-2516, 2532-2546) + R3 database.py:2965-2970
# ===========================================================================

class TestLinkFileToCaseFaults:

    def test_backup_failure_aborts_before_write(self, fi_env, monkeypatch):
        """backup_before_write raises OSError (server.py:2513-2520)."""
        env = fi_env
        pre = env.hash()
        before = env.backup_names()

        def boom(self):
            raise OSError("Injected: No space left on device")

        monkeypatch.setattr(QualcoderDatabase, "backup_before_write", boom)

        result = server.link_file_to_case(
            file_id=2, case_name="Case A", create_backup=True)

        data = assert_structured_error(result, "Failed to create a backup")
        assert "nothing was linked" in data.get("message", "")
        assert env.hash() == pre, "backup failed but something was written"
        assert env.backup_names() == before
        assert not env.lock.exists()
        assert_connection_recovered(env)

    def test_lock_flip_between_check_and_commit(self, fi_env, lock_flip):
        """DatabaseLockedError from the pre-commit re-check
        (server.py:2531-2536 + 2544-2546)."""
        env = fi_env
        lock_flip(env, "link_file_to_case")
        pre = env.hash()
        before = env.backup_names()

        result = server.link_file_to_case(
            file_id=2, case_name="Case A", create_backup=False)

        assert_structured_error(result, "open in QualCoder")
        assert env.hash() == pre, "case link survived the lock re-check abort"
        assert env.backup_names() == before
        assert env.lock.exists(), "foreign lock file was removed"
        assert_connection_recovered(env)

    def test_disk_error_on_insert(self, fi_env, write_faults):
        """Generic sqlite3.Error tail (database.py:2965-2970 -> RuntimeError
        -> server.py:2537-2543)."""
        env = fi_env
        pre = env.hash()
        before = env.backup_names()
        write_faults(execute_faults=[
            ExecFault("insert into case_text", sqlite3.OperationalError(DISK_ERR)),
        ])

        result = server.link_file_to_case(
            file_id=2, case_name="Case A", create_backup=False)

        assert_structured_error(result, "Failed to link file to case")
        assert env.hash() == pre
        assert env.backup_names() == before
        assert not env.lock.exists()
        assert_connection_recovered(env)

    def test_locked_error_on_insert(self, fi_env, write_faults):
        """sqlite3.OperationalError('database is locked') on the insert:
        database.py:2965-2970 raises DatabaseLockedError -> server.py:
        2531-2536 + 2544-2546 -> sanitized locked-DB message."""
        env = fi_env
        pre = env.hash()
        before = env.backup_names()
        write_faults(execute_faults=[
            ExecFault("insert into case_text",
                      sqlite3.OperationalError("database is locked")),
        ])

        result = server.link_file_to_case(
            file_id=2, case_name="Case A", create_backup=False)

        assert_structured_error(result, "locked")
        assert env.hash() == pre
        assert env.backup_names() == before
        assert not env.lock.exists()
        assert_connection_recovered(env)

    def test_locked_insert_and_rollback_failure(self, fi_env, write_faults):
        """Locked insert AND every rollback fails: covers the except-pass in
        the database layer (database.py:2968-2969) and in the server's
        locked path (server.py:2534-2535)."""
        env = fi_env
        pre = env.hash()
        before = env.backup_names()
        write_faults(
            execute_faults=[ExecFault(
                "insert into case_text",
                sqlite3.OperationalError("database is locked"))],
            rollback_exc=sqlite3.OperationalError("Injected: cannot rollback"),
        )

        result = server.link_file_to_case(
            file_id=2, case_name="Case A", create_backup=False)

        assert_structured_error(result, "locked")
        assert env.hash() == pre
        assert env.backup_names() == before
        assert not env.lock.exists()
        assert_connection_recovered(env)

    def test_disk_error_and_rollback_failure(self, fi_env, write_faults):
        """Disk error on insert AND every rollback fails: server.py:2540-2541
        (and database.py:2968-2969)."""
        env = fi_env
        pre = env.hash()
        before = env.backup_names()
        write_faults(
            execute_faults=[ExecFault(
                "insert into case_text", sqlite3.OperationalError(DISK_ERR))],
            rollback_exc=sqlite3.OperationalError("Injected: cannot rollback"),
        )

        result = server.link_file_to_case(
            file_id=2, case_name="Case A", create_backup=False)

        assert_structured_error(result, "Failed to link file to case")
        assert env.hash() == pre
        assert env.backup_names() == before
        assert not env.lock.exists()
        assert_connection_recovered(env)


# ===========================================================================
# R2 — delete_coding backup-failure abort + rollback/downgrade
#      (server.py:2610-2613, 2623-2638) + R3 database.py:2694-2699
# ===========================================================================

def _count_codings(env: Env) -> int:
    conn = sqlite3.connect(f"file:{env.folder / 'data.qda'}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT COUNT(*) FROM code_text").fetchone()[0]
    finally:
        conn.close()


class TestDeleteCodingFaults:

    def test_backup_failure_aborts_before_delete(self, fi_env, monkeypatch):
        """backup_before_write raises OSError (server.py:2610-2617)."""
        env = fi_env
        pre = env.hash()
        before = env.backup_names()

        def boom(self):
            raise OSError("Injected: No space left on device")

        monkeypatch.setattr(QualcoderDatabase, "backup_before_write", boom)

        result = server.delete_coding(1, create_backup=True)

        data = assert_structured_error(result, "Failed to create a backup")
        assert "nothing was deleted" in data.get("message", "")
        assert env.hash() == pre
        assert _count_codings(env) == 1, "coding was deleted despite backup failure"
        assert env.backup_names() == before
        assert not env.lock.exists()
        assert_connection_recovered(env)

    def test_lock_flip_between_check_and_commit(self, fi_env, lock_flip):
        """DatabaseLockedError from the pre-commit re-check
        (server.py:2623-2628 + 2636-2638): the real DELETE must be rolled
        back and the coding must survive."""
        env = fi_env
        lock_flip(env, "delete_coding")
        pre = env.hash()
        before = env.backup_names()

        result = server.delete_coding(1, create_backup=False)

        assert_structured_error(result, "open in QualCoder")
        assert env.hash() == pre, "DELETE survived the lock re-check abort"
        assert _count_codings(env) == 1
        assert env.backup_names() == before
        assert env.lock.exists(), "foreign lock file was removed"
        assert_connection_recovered(env)

    def test_disk_error_on_delete(self, fi_env, write_faults):
        """Generic sqlite3.Error tail (database.py:2694-2699 -> RuntimeError
        -> server.py:2629-2635)."""
        env = fi_env
        pre = env.hash()
        before = env.backup_names()
        write_faults(execute_faults=[
            ExecFault("delete from code_text", sqlite3.OperationalError(DISK_ERR)),
        ])

        result = server.delete_coding(1, create_backup=False)

        assert_structured_error(result, "Failed to delete coding")
        assert env.hash() == pre
        assert _count_codings(env) == 1
        assert env.backup_names() == before
        assert not env.lock.exists()
        assert_connection_recovered(env)

    def test_locked_error_on_delete(self, fi_env, write_faults):
        """Locked-DB flavour of the same tail: database.py:2694-2699 raises
        DatabaseLockedError -> server.py:2623-2628 + 2636-2638."""
        env = fi_env
        pre = env.hash()
        before = env.backup_names()
        write_faults(execute_faults=[
            ExecFault("delete from code_text",
                      sqlite3.OperationalError("database is locked")),
        ])

        result = server.delete_coding(1, create_backup=False)

        assert_structured_error(result, "locked")
        assert env.hash() == pre
        assert _count_codings(env) == 1
        assert env.backup_names() == before
        assert not env.lock.exists()
        assert_connection_recovered(env)

    def test_locked_delete_and_rollback_failure(self, fi_env, write_faults):
        """Locked DELETE and every rollback fails: covers the except-pass in
        the database layer (database.py:2697-2698) and the server's locked
        path (server.py:2626-2627)."""
        env = fi_env
        pre = env.hash()
        before = env.backup_names()
        write_faults(
            execute_faults=[ExecFault(
                "delete from code_text",
                sqlite3.OperationalError("database is locked"))],
            rollback_exc=sqlite3.OperationalError("Injected: cannot rollback"),
        )

        result = server.delete_coding(1, create_backup=False)

        assert_structured_error(result, "locked")
        assert env.hash() == pre
        assert _count_codings(env) == 1
        assert env.backup_names() == before
        assert not env.lock.exists()
        assert_connection_recovered(env)

    def test_disk_error_and_rollback_failure(self, fi_env, write_faults):
        """Disk error on DELETE and every rollback fails: server.py:2632-2633
        (and database.py:2697-2698)."""
        env = fi_env
        pre = env.hash()
        before = env.backup_names()
        write_faults(
            execute_faults=[ExecFault(
                "delete from code_text", sqlite3.OperationalError(DISK_ERR))],
            rollback_exc=sqlite3.OperationalError("Injected: cannot rollback"),
        )

        result = server.delete_coding(1, create_backup=False)

        assert_structured_error(result, "Failed to delete coding")
        assert env.hash() == pre
        assert _count_codings(env) == 1
        assert env.backup_names() == before
        assert not env.lock.exists()
        assert_connection_recovered(env)


# ===========================================================================
# R3 — database-layer generic sqlite3.Error rollback tails, called directly
#      (add_coding 2498-2502, add_code 2572-2576, add_memo_to_coding 2616-2619)
# ===========================================================================

class TestDatabaseLayerRollbackTails:

    @staticmethod
    def _write_db_with_fault(project: Path, needle: str, exc: Exception):
        wdb = QualcoderDatabase(str(project), read_only=False)
        proxy = FaultyConn(wdb.conn, execute_faults=[ExecFault(needle, exc)])
        wdb.conn = proxy
        return wdb, proxy

    def test_add_coding_non_unique_integrity_error(self, raw_project):
        """database.py:2493-2494 + 2498 (IntegrityError, not a unique clash)."""
        pre = db_hash(raw_project)
        wdb, proxy = self._write_db_with_fault(
            raw_project, "insert into code_text",
            sqlite3.IntegrityError("NOT NULL constraint failed: code_text.cid"))
        with pytest.raises(RuntimeError, match="Failed to add coding"):
            wdb.add_coding(file_id=1, code_id=2, start_pos=6, end_pos=11,
                           selected_text="bravo", owner="tester")
        assert proxy.rollback_calls == 1
        wdb.close()
        assert db_hash(raw_project) == pre

    def test_add_coding_generic_sqlite_error(self, raw_project):
        """database.py:2499-2502 (generic sqlite3.Error tail)."""
        pre = db_hash(raw_project)
        wdb, proxy = self._write_db_with_fault(
            raw_project, "insert into code_text",
            sqlite3.OperationalError(DISK_ERR))
        with pytest.raises(RuntimeError, match="Failed to add coding"):
            wdb.add_coding(file_id=1, code_id=2, start_pos=6, end_pos=11,
                           selected_text="bravo", owner="tester")
        assert proxy.rollback_calls == 1
        wdb.close()
        assert db_hash(raw_project) == pre

    def test_add_code_non_unique_integrity_error(self, raw_project):
        """database.py:2568-2572 (IntegrityError, not a unique clash)."""
        pre = db_hash(raw_project)
        wdb, proxy = self._write_db_with_fault(
            raw_project, "insert into code_name",
            sqlite3.IntegrityError("NOT NULL constraint failed: code_name.owner"))
        with pytest.raises(RuntimeError, match="Failed to add code"):
            wdb.add_code(name="Resilience", owner="tester")
        assert proxy.rollback_calls == 1
        wdb.close()
        assert db_hash(raw_project) == pre

    def test_add_code_generic_sqlite_error(self, raw_project):
        """database.py:2573-2576 (generic sqlite3.Error tail)."""
        pre = db_hash(raw_project)
        wdb, proxy = self._write_db_with_fault(
            raw_project, "insert into code_name",
            sqlite3.OperationalError(DISK_ERR))
        with pytest.raises(RuntimeError, match="Failed to add code"):
            wdb.add_code(name="Resilience", owner="tester")
        assert proxy.rollback_calls == 1
        wdb.close()
        assert db_hash(raw_project) == pre

    def test_add_memo_to_coding_generic_sqlite_error(self, raw_project):
        """database.py:2616-2619 (generic sqlite3.Error tail)."""
        pre = db_hash(raw_project)
        wdb, proxy = self._write_db_with_fault(
            raw_project, "update code_text",
            sqlite3.OperationalError(DISK_ERR))
        with pytest.raises(RuntimeError, match="Failed to update memo"):
            wdb.add_memo_to_coding(coding_id=1, memo="new memo", owner="tester")
        assert proxy.rollback_calls == 1
        wdb.close()
        assert db_hash(raw_project) == pre


# ===========================================================================
# R4 — restore_backup confirmed-restore fault behaviour (server.py:2847-2867)
# ===========================================================================

@pytest.fixture
def restore_env(fi_env):
    """fi_env plus a valid sibling backup that differs from the live project."""
    env = fi_env
    backup = env.work / f"{env.folder.stem}_backup_20240101_000000.qda"
    shutil.copytree(env.folder, backup)
    # diverge the live project from the backup
    conn = sqlite3.connect(str(env.folder / "data.qda"))
    conn.execute(
        "INSERT INTO journal (name, jentry, date, owner) "
        "VALUES ('post-backup', 'written after the backup', '2024-02-02', 'TestCoder')"
    )
    conn.commit()
    conn.close()
    env.backup_folder = backup
    return env


def _partial_copytree_patch(monkeypatch, env: Env, unrecoverable: bool = False):
    """Make shutil.copytree fail on the backup->project swap.

    partial (default): creates the destination folder, copies data.qda only,
    then raises — simulating disk-full partway through the copy.
    unrecoverable=True: raises immediately for ANY copy into the project
    folder (so the safety-backup recovery copy fails too)."""
    real_copytree = shutil.copytree

    def fake(src, dst, *args, **kwargs):
        src_p, dst_p = Path(src), Path(dst)
        if unrecoverable:
            if dst_p == env.folder:
                raise OSError(28, "Injected: no space left on device")
            return real_copytree(src, dst, *args, **kwargs)
        if dst_p == env.folder and src_p == env.backup_folder:
            dst_p.mkdir()
            shutil.copy2(src_p / "data.qda", dst_p / "data.qda")
            raise OSError(28, "Injected: no space left on device (mid-copy)")
        return real_copytree(src, dst, *args, **kwargs)

    monkeypatch.setattr(shutil, "copytree", fake)


class TestRestoreBackupFaults:

    def test_successful_restore_is_fully_new_with_safety_backup(self, restore_env):
        """Baseline contract (also covers the stray-lock unlink OSError at
        server.py:2847-2848 via an un-unlinkable *.lock directory inside the
        backup): project == backup afterwards, safety backup == old state."""
        env = restore_env
        (env.backup_folder / "stuck.lock").mkdir()  # dir: unlink() -> OSError
        pre_hash = env.hash()
        pre_state = env.state()
        backup_hash = db_hash(env.backup_folder)
        before = env.backup_names()

        result = server.restore_backup(str(env.backup_folder), confirm=True)

        data = json.loads(result)
        assert data.get("success") is True, data
        assert env.hash() == backup_hash, "project is not fully the backup state"
        new = env.backup_names() - before
        assert len(new) == 1 and "_prerestore" in next(iter(new))
        safety = env.work / next(iter(new))
        assert db_hash(safety) == pre_hash, "safety backup != pre-restore state"
        assert folder_state(safety) == pre_state
        assert_connection_recovered(env)

    def test_lock_appears_between_probe_and_swap(self, restore_env, monkeypatch):
        """QualCoder opens the project between the write-lock probe and the
        folder swap (server.py:2849-2852): nothing is replaced, the project
        stays fully old, the connection is restored."""
        env = restore_env
        pre_hash = env.hash()
        pre_state = env.state()
        before = env.backup_names()

        real_backup_project = server.backup_project

        def backup_then_lock(project_path):
            out = real_backup_project(project_path)
            env.plant_lock(fresh=True)  # QualCoder grabs the project now
            return out

        monkeypatch.setattr(server, "backup_project", backup_then_lock)

        result = server.restore_backup(str(env.backup_folder), confirm=True)

        assert_structured_error(result, "open in QualCoder")
        assert env.hash() == pre_hash
        assert env.state() == pre_state, "project folder changed despite refusal"
        # the safety backup made before the refusal is by design; it must
        # hold the pre-call state
        new = env.backup_names() - before
        assert len(new) == 1
        assert db_hash(env.work / next(iter(new))) == pre_hash
        assert env.lock.exists(), "foreign lock file was removed"
        assert_connection_recovered(env)

    def test_swap_fails_before_any_copy_recovers_from_safety_backup(
            self, restore_env, monkeypatch):
        """copytree(backup -> project) fails before creating anything
        (server.py:2853-2864): the project is recovered from the safety
        backup — fully old."""
        env = restore_env
        pre_hash = env.hash()
        pre_state = env.state()
        before = env.backup_names()

        real_copytree = shutil.copytree

        def fake(src, dst, *args, **kwargs):
            if Path(dst) == env.folder and Path(src) == env.backup_folder:
                raise OSError(28, "Injected: no space left on device")
            return real_copytree(src, dst, *args, **kwargs)

        monkeypatch.setattr(shutil, "copytree", fake)

        result = server.restore_backup(str(env.backup_folder), confirm=True)

        data = assert_structured_error(result, "recovered")
        assert "safety_backup" in data
        assert env.folder.exists()
        assert env.hash() == pre_hash, "recovered project != pre-restore state"
        assert env.state() == pre_state
        new = env.backup_names() - before
        assert len(new) == 1  # only the safety backup
        assert_connection_recovered(env)

    def test_swap_fails_midway_project_never_half_replaced(
            self, restore_env, monkeypatch):
        """Invariant (fault-injection D1, fixed): after ANY failed restore
        the live project is either fully the old state or fully the backup
        state — never half-replaced. The recovery handler now clears a
        partial destination before restoring from the safety backup."""
        env = restore_env
        pre_state = env.state()
        backup_state = folder_state(env.backup_folder)

        _partial_copytree_patch(monkeypatch, env)

        server.restore_backup(str(env.backup_folder), confirm=True)

        assert env.folder.exists()
        post = env.state()
        assert post == pre_state or post == backup_state, (
            "project folder is neither fully old nor fully new"
        )

    def test_swap_and_recovery_both_fail(self, restore_env, monkeypatch):
        """copytree fails for the swap AND for the safety-backup recovery
        (server.py:2865-2867): the error must point at the safety backup,
        which holds the complete pre-restore state, and the server must
        still answer with structured errors (not bricked)."""
        env = restore_env
        pre_hash = env.hash()
        pre_state = env.state()
        before = env.backup_names()

        _partial_copytree_patch(monkeypatch, env, unrecoverable=True)

        result = server.restore_backup(str(env.backup_folder), confirm=True)

        data = assert_structured_error(result, "safety backup")
        assert "safety_backup" in data
        safety = Path(data["safety_backup"])
        assert safety.exists()
        assert db_hash(safety) == pre_hash
        assert folder_state(safety) == pre_state
        new = env.backup_names() - before
        assert len(new) == 1  # only the safety backup, no other litter
        # the live project folder is gone (worst case, by design the user
        # copies the safety backup back); subsequent tool calls must return
        # structured errors rather than raising
        assert not env.folder.exists()
        summary = json.loads(server.get_project_summary())
        assert "error" in summary


# ---------------------------------------------------------------------------
# SEC M-1 regression: commit-time sqlite3.Error through _perform_write
# ---------------------------------------------------------------------------

class TestPerformWriteUnconditionalCleanup:
    """_perform_write (the shared write helper behind the memo/codebook
    tools) previously rolled back and downgraded only for
    DatabaseLockedError/ValueError/RuntimeError — a BARE sqlite3.Error at
    commit time (disk I/O, SQLITE_BUSY) escaped both, leaving the GLOBAL
    connection read-write with an open transaction; the next write reused
    it and could silently co-commit the failed op's changes (Security M-1,
    fault-injected via set_memo). Cleanup now runs in a finally block.
    """

    def _inject_commit_error(self, monkeypatch):
        def boom(folder, held):
            raise sqlite3.OperationalError("disk I/O error")
        monkeypatch.setattr(server, "_recheck_lock_before_commit", boom)

    def test_commit_time_sqlite_error_leaves_clean_state(self, fi_env,
                                                         monkeypatch):
        env = fi_env
        pre_hash = env.hash()
        self._inject_commit_error(monkeypatch)

        out = server.set_memo("code", 1, "will not land", create_backup=False)

        # 1. sanitized structured error, no raw traceback
        data = json.loads(out)
        assert "error" in data
        assert "Traceback" not in out
        # 2. the failed op's change was rolled back
        assert env.hash() == pre_hash
        # 3. global connection is READ-ONLY again with NO open transaction
        assert server.db is not None
        assert server.db.read_only is True
        assert server.db.conn.in_transaction is False

    def test_next_write_does_not_co_commit_failed_op(self, fi_env,
                                                     monkeypatch):
        """The exact M-1 exploit shape: after the failed write, a subsequent
        UNRELATED write must not carry the failed op's changes with it."""
        env = fi_env
        self._inject_commit_error(monkeypatch)
        server.set_memo("code", 1, "ghost memo", create_backup=False)
        monkeypatch.setattr(server, "_recheck_lock_before_commit",
                            _ORIG_RECHECK)

        out = json.loads(server.set_memo("case", 1, "legit memo",
                                         create_backup=False))
        assert out.get("success") is True

        con = sqlite3.connect(str(env.folder / "data.qda"))
        code_memo = con.execute(
            "SELECT memo FROM code_name WHERE cid=1").fetchone()[0]
        case_memo = con.execute(
            "SELECT memo FROM cases WHERE caseid=1").fetchone()[0]
        con.close()
        assert case_memo == "legit memo"
        assert code_memo != "ghost memo"  # never co-committed

    def test_guarded_destructive_inherits_cleanup(self, fi_env, monkeypatch):
        """merge/delete route through the same helper: same guarantee."""
        env = fi_env
        pre_hash = env.hash()
        self._inject_commit_error(monkeypatch)

        out = json.loads(server.delete_code(1, confirm=True))
        assert "error" in out
        assert env.hash() == pre_hash                 # nothing destroyed
        assert server.db.read_only is True
        assert server.db.conn.in_transaction is False


_ORIG_RECHECK = server._recheck_lock_before_commit


class TestPerformWriteUnexpectedException:
    """Security's second probe shape: op raises something entirely outside
    the anticipated classes (KeyError). The exception propagates past
    _tool_guard (FastMCP's catch-all handles it in production), but the
    finally block must STILL roll back and downgrade — state is never left
    dirty regardless of exception class."""

    def test_unexpected_exception_class_still_cleans_up(self, fi_env,
                                                        monkeypatch):
        env = fi_env
        pre_hash = env.hash()

        def keyboom(self, *a, **k):
            raise KeyError("unexpected internal key")

        monkeypatch.setattr(QualcoderDatabase, "set_memo", keyboom)
        with pytest.raises(KeyError):
            server.set_memo("code", 1, "x", create_backup=False)

        assert env.hash() == pre_hash
        assert server.db is not None
        assert server.db.read_only is True
        assert server.db.conn.in_transaction is False
        # server not bricked: a normal write works afterwards
        monkeypatch.undo()
        out = json.loads(server.set_memo("code", 1, "after", create_backup=False))
        assert out.get("success") is True
