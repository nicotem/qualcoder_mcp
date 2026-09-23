"""Pytest configuration and shared fixtures."""

import gc
import os
import pytest
import sqlite3
import sys
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
# The tests directory itself, so conftest can share one helper module
# with the test files that import it by name (track5_helpers).
sys.path.insert(0, str(Path(__file__).parent))

import qualcoder_mcp.server as server
from qualcoder_mcp import database as _database
from qualcoder_mcp.database import QualcoderDatabase
from qualcoder_mcp.project_settings import DEFAULT_AI_CODER_NAME, SIDECAR_NAME
from track5_helpers import (write_fixture_sidecar, REAL_WORKSPACE,
                            WORKSPACE_UNREADABLE, real_workspace_entries,
                            GUARDED_REAL_FOLDERS, SHARING_VIOLATION,
                            open_paths_under)
from qualcoder_mcp.sessions import SessionManager, AICodingSession, CodingSuggestion
from hypothesis import settings as _hypothesis_settings


# v0.13 fix round 2 (the lead's ruling on the re-verification's CORR-1):
# every property in this suite runs derandomised in CI, so a push is
# never red by the luck of a seed; locally the seed stays random, and a
# chosen one is `--hypothesis-seed=N`. GitHub Actions sets CI=true on
# every runner. HYPOTHESIS_PROFILE picks a profile by name either way.
_hypothesis_settings.register_profile("ci", derandomize=True,
                                      database=None, print_blob=True)


def hypothesis_profile_for(environ) -> str:
    """The profile this run loads, from its environment."""
    return environ.get("HYPOTHESIS_PROFILE") or (
        "ci" if environ.get("CI") else "default")


_hypothesis_settings.load_profile(hypothesis_profile_for(os.environ))


@pytest.fixture(autouse=True)
def _sandbox_patch():
    """A MonkeyPatch the sandbox owns, which a test cannot undo.

    Every fixture below redirects a home-derived binding, and they used
    to do it through the shared `monkeypatch` fixture. A test that calls
    `monkeypatch.undo()` part way through, which two of them do to stop
    a fault injection before asserting, therefore undid THE WHOLE
    SANDBOX as well: for the rest of that test the MRU file, the preview
    secret, the workspace constant, the session manager and the AI coder
    name environment were all pointing back at the researcher's own
    folders. Nothing had made that visible, because the two tests wrote
    nothing afterwards. This instance is separate, so `undo()` in a test
    reaches only the test's own patches.
    """
    patcher = pytest.MonkeyPatch()
    yield patcher
    patcher.undo()


@pytest.fixture(autouse=True)
def _isolate_mru_state(tmp_path, _sandbox_patch):
    """Keep the P1-6 MRU state file out of the real ~/.qualcoder_mcp.

    select_project records the most-recently-used project on disk;
    without this, every test that selects a fixture project would
    overwrite the developer's real MRU state.
    """
    _sandbox_patch.setattr(server, "_MRU_FILE",
                           tmp_path / "mru_state" / "mru_project.json")


@pytest.fixture(autouse=True)
def _isolate_preview_secret(tmp_path, _sandbox_patch):
    """Keep the B2 preview-token secret out of the real ~/.qualcoder_mcp.

    The same reasoning as the MRU isolation above: a test that previews a
    destructive operation would otherwise create or rotate the
    developer's own secret, and a rotation invalidates tokens the real
    server issued.
    """
    from qualcoder_mcp import preview_tokens
    _sandbox_patch.setattr(preview_tokens, "STATE_HOME",
                           tmp_path / "token_state")


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, _sandbox_patch):
    """Move the home directory itself into tmp_path, and with it every
    path the server resolves from it at call time.

    `copy_project_to_workspace`'s `workspace` is not a tool argument, so
    a tool call falls through to `database.default_workspace()`. Until
    the 0.12 release preparation that was the constant
    `DEFAULT_WORKSPACE`, computed from Path.home() at IMPORT time, so a
    test that moved HOME redirected nothing and the copy landed in the
    researcher's own workspace folder, one project tree per suite run;
    the fixture that stood here then patched the constant object. The
    binding is late now, so the sandbox moves the home: HOME on POSIX,
    USERPROFILE on Windows, both through the sandbox's own MonkeyPatch.
    The import-bound state paths (the MRU file, the preview secret, the
    session manager) are still patched as objects by the fixtures around
    this one, because a moved home cannot reach a value already frozen.
    The two assertions are the guard's own liveness check: a sandbox that
    moved nothing is worse than none.
    """
    # A name no test uses for a home of its own: several fixtures make
    # `tmp_path / "home"` themselves, without exist_ok, and must go on
    # being able to.
    home = tmp_path / "qc_sandbox_home"
    home.mkdir(exist_ok=True)
    _sandbox_patch.setenv("HOME", str(home))
    _sandbox_patch.setenv("USERPROFILE", str(home))
    assert Path.home().resolve() == home.resolve()
    assert _database.default_workspace().resolve().is_relative_to(
        tmp_path.resolve())


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Print the file-text count's measured rate at the end of the run.

    v0.13, ruling 7: the work budget of `pseudonymise_source`'s file-text
    count is "about two seconds", sanity-checked on the slowest CI
    platform before the constant is frozen. The performance guard
    records its rate, and the worst case of the two budgets it implies,
    as a user property, and a passing test's own output is captured and
    never shown (CI runs `-ra -q`), so the line is written here, into the
    summary every CI log carries, with the platform and the interpreter
    it was measured on; the CI workflow copies it into the step summary.
    """
    for key in ("passed", "failed"):
        for report in terminalreporter.stats.get(key, []):
            for name, value in getattr(report, "user_properties", []):
                if name == "file_text_rate":
                    terminalreporter.write_line(
                        f"file-text count rate: {value} ({sys.platform}, "
                        f"Python {sys.version_info[0]}."
                        f"{sys.version_info[1]}.{sys.version_info[2]}, "
                        f"test {key})")


# The baseline for the guard below, and the proof that it was taken
# before anything could write. Both are filled by `pytest_sessionstart`.
WORKSPACE_BASELINE = {}
BASELINE_PRECEDED_TEST_IMPORTS = None


def pytest_sessionstart(session):
    """Snapshot the researcher's real folders BEFORE collection.

    A session-scoped fixture body runs at the SETUP OF THE FIRST TEST,
    by which time every test module has been imported. Anything a module
    wrote at import time would therefore sit inside `before` and never
    be reported, and the function-scoped isolation fixtures are not in
    force during collection either, so such a write would land for real.
    This is not hypothetical: `tests/test_scale_media.py` already does
    filesystem work at import. `pytest_sessionstart` fires after the
    root conftest is loaded and before any test module is imported,
    which is the earliest point a snapshot can be taken at all.
    """
    global BASELINE_PRECEDED_TEST_IMPORTS
    BASELINE_PRECEDED_TEST_IMPORTS = not any(
        name.startswith("test_") for name in list(sys.modules))
    for label, folder in GUARDED_REAL_FOLDERS.items():
        WORKSPACE_BASELINE[label] = real_workspace_entries(folder)


@pytest.fixture(autouse=True, scope="session")
def _nothing_is_written_to_the_real_workspace():
    """Fail the run if any test creates anything in the researcher's own
    folders.

    The companion guard to the isolation fixtures above: they redirect
    the constants, and this one proves that no route into the
    researcher's own `~/Documents/Qualcoder MCP Projects` or
    `~/.qualcoder_mcp` survived. In the spirit of the test-rot guard, it
    pins the ABSENCE, so a future test that reaches a real folder by
    another route (a hard-coded path, a `workspace=` argument built from
    Path.home(), an import-bound object nobody redirected, a re-import
    that rebinds a constant) is reported instead of quietly leaving
    project copies or session files behind.

    The state home is watched in full rather than at its top level: the
    leak it is guarding against is a session file one directory down.

    The paths are read at IMPORT time (track5_helpers), before any
    fixture has moved HOME or a constant, so they are the folders the
    shipped server would really use, and the baseline is taken before
    collection. A missing folder is recorded as missing: creating it is
    itself a write into the researcher's own directories.
    """
    assert WORKSPACE_BASELINE, (
        "the pre-collection baseline was never taken; this guard cannot "
        "report anything and must not pass")
    yield
    problems = []
    for label, folder in GUARDED_REAL_FOLDERS.items():
        before = WORKSPACE_BASELINE.get(label)
        after = real_workspace_entries(folder)
        if WORKSPACE_UNREADABLE in (before, after):
            continue
        if before is None and after is not None:
            problems.append(
                f"the suite created the real {label} folder {folder}")
            continue
        added = sorted((after or set()) - (before or set()))
        if added:
            noun = "entry" if len(added) == 1 else "entries"
            problems.append(
                f"the suite created {len(added)} {noun} in the real "
                f"{label} {folder}: {added[:10]}")
    if problems:
        raise AssertionError(
            "; ".join(problems) + "; tests must stay inside tmp_path")


@pytest.fixture(autouse=True, scope="session")
def _no_database_connection_outlives_the_run():
    """Close the server's global connection, and pin that nothing else
    is left open.

    Measured rather than assumed: sampled every 300 tests, this suite
    holds between nought and two open sqlite3 connections at any point
    and one at the end, so it does not ACCUMULATE them. Sampling is the
    wrong instrument for the leak itself, and this sentence used to end
    by saying the reported leak was not reproducible on this tree, which
    later work in the same round disproved: five connections really were
    opened with nothing that could close them, and a connection created,
    dropped and collected a few tests later is one a sample almost never
    sees. All five are closed, and what pins the class now is
    TestNoConnectionIsOpenedWithNothingToCloseIt in
    tests/test_suite_hygiene.py, which reads the syntax rather than
    waiting for a finalisation. This fixture is the other half of that
    pin, at runtime and at the end of the run.

    The one this fixture was written for: `server.db` is a module-level
    global, the last test to select a project leaves it set, and it is
    an open handle on a file inside tmp_path. On Windows that blocks the
    removal of the directory holding it, which is the same rule the
    sharing guard below emulates.

    The assertion is the point rather than the close: it pins the
    ABSENCE, so a future fixture that stops closing its own connection
    is reported here by name instead of being found by a runner.
    """
    yield
    if server.db is not None:
        try:
            server.db.close()
        except Exception:
            pass
        server.db = None
    gc.collect()
    still_open = []
    for obj in gc.get_objects():
        if not isinstance(obj, sqlite3.Connection):
            continue
        try:
            obj.execute("SELECT 1")
        except Exception:
            continue                      # already closed
        holders = []
        for referrer in gc.get_referrers(obj):
            if isinstance(referrer, dict):
                holders += [k for k, v in referrer.items() if v is obj]
            else:
                holders.append(type(referrer).__name__)
        still_open.append(holders[:6] or ["<no named holder>"])
    assert not still_open, (
        f"{len(still_open)} database connection(s) are still open at the "
        f"end of the run, held by {still_open[:5]}; a test or fixture is "
        f"not closing what it opened, and on Windows an open handle stops "
        f"its directory being removed")


@pytest.fixture(autouse=True, scope="session")
def _windows_sharing_semantics():
    """Make POSIX refuse what Windows refuses, so the suite can see it.

    Windows will not rename or remove a directory that still holds an
    OPEN file, and will not rename, replace or UNLINK an open file:
    SQLite, like most writers, opens without FILE_SHARE_DELETE. POSIX
    allows all of them, so a test that moves or deletes a project while
    the server still holds its connection is green on ubuntu and macOS
    and fails on BOTH Windows jobs with WinError 32,
    version-independently. A CI runner is the slowest possible place to
    learn that, and the whole class is invisible on the machine the code
    is written on, so this guard reproduces the rule here instead.

    Unlink was missing from the list until fix round 5, and fix round 4
    fell through the gap: a cleanup unlinked a temp file whose
    descriptor `os.fdopen` had failed to take, which POSIX allows and
    Windows refuses, so both Windows jobs went red on a pin that is
    green here. A guard that covers three of the four operations is a
    guard that says the class is handled when it is not.

    It is emulation, not policy: on Windows the operating system already
    enforces it and the fixture stands aside. `ignore_errors=True`
    likewise stands aside, because Windows does not raise there either.
    """
    if sys.platform == "win32":
        yield
        return

    real = {
        "rmtree": shutil.rmtree,
        "os_rename": os.rename,
        "os_replace": os.replace,
        # Path.rename resolves os.rename through a class-level accessor
        # on 3.10 and 3.11 and calls it directly from 3.12, so both
        # spellings are patched rather than relying on either.
        "path_rename": Path.rename,
        "path_replace": Path.replace,
        # Deletion was the hole in this guard, and fix round 4 fell
        # straight through it: Windows refuses to UNLINK an open file
        # for the same reason it refuses to rename one, so a cleanup
        # that deletes a temp whose descriptor is still open is silently
        # fine here and leaves litter there. `os.remove` is a second
        # name for the same call and is patched as its own attribute,
        # and `Path.unlink` is patched for the accessor reason above.
        "os_unlink": os.unlink,
        "os_remove": os.remove,
        "path_unlink": Path.unlink,
    }

    def _refuse(operation, *targets):
        for target in targets:
            if target is None:
                continue
            held = open_paths_under(target)
            if held:
                raise PermissionError(
                    f"{operation} {target}: {SHARING_VIOLATION}"
                    f"{held[:5]}")

    def rmtree(path, ignore_errors=False, *args, **kwargs):
        if not ignore_errors:
            _refuse("rmtree", path)
        return real["rmtree"](path, ignore_errors, *args, **kwargs)

    def os_rename(src, dst, **kwargs):
        _refuse("rename", src, dst)
        return real["os_rename"](src, dst, **kwargs)

    def os_replace(src, dst, **kwargs):
        _refuse("replace", src, dst)
        return real["os_replace"](src, dst, **kwargs)

    def path_rename(self, target):
        _refuse("rename", self, target)
        return real["path_rename"](self, target)

    def path_replace(self, target):
        _refuse("replace", self, target)
        return real["path_replace"](self, target)

    def os_unlink(path, **kwargs):
        _refuse("unlink", path)
        return real["os_unlink"](path, **kwargs)

    def os_remove(path, **kwargs):
        _refuse("remove", path)
        return real["os_remove"](path, **kwargs)

    def path_unlink(self, *args, **kwargs):
        _refuse("unlink", self)
        return real["path_unlink"](self, *args, **kwargs)

    shutil.rmtree = rmtree
    os.rename = os_rename
    os.replace = os_replace
    Path.rename = path_rename
    Path.replace = path_replace
    os.unlink = os_unlink
    os.remove = os_remove
    Path.unlink = path_unlink
    try:
        yield
    finally:
        shutil.rmtree = real["rmtree"]
        os.rename = real["os_rename"]
        os.replace = real["os_replace"]
        Path.rename = real["path_rename"]
        Path.replace = real["path_replace"]
        os.unlink = real["os_unlink"]
        os.remove = real["os_remove"]
        Path.unlink = real["path_unlink"]


@pytest.fixture(autouse=True)
def _isolate_session_manager(tmp_path, _sandbox_patch):
    """Keep AI coding sessions out of the real ~/.qualcoder_mcp/sessions.

    `server.session_manager` is an INSTANCE built at import time, and
    its storage directory is `expanduser`d in the constructor, so a test
    that moves HOME redirects nothing: the sessions land in the
    researcher's own state directory. Several fixtures replace the
    object already, which is why the leak has not been loud; the ones
    that do not, and any tool call made outside them, wrote for real.
    Patch the OBJECT, the way the other three home-derived bindings are
    patched above.
    """
    _sandbox_patch.setattr(server, "session_manager",
                           SessionManager(str(tmp_path / "sessions")))


@pytest.fixture(autouse=True)
def _isolate_ai_coder_name(_sandbox_patch):
    """Keep an ambient QUALCODER_MCP_AI_CODER_NAME out of the suite.

    The P1-2 attribution config is read from the environment on every
    write and many assertions pin the default owner string; a developer
    or CI shell that exports the variable must not turn those into
    spurious failures (QA round 1, F21). Tests that exercise the
    variable set it themselves through monkeypatch.
    """
    _sandbox_patch.delenv("QUALCODER_MCP_AI_CODER_NAME", raising=False)


# =============================================================================
# SESSION FIXTURES (used by test_sessions.py, test_integration_ai_coding.py)
# =============================================================================

@pytest.fixture
def temp_session_dir():
    """Create a temporary directory for session storage."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir)


@pytest.fixture
def sample_suggestion_data():
    """Sample data for creating CodingSuggestion instances."""
    return {
        "file_id": 1,
        "file_name": "interview_01.txt",
        "code_id": 10,
        "code_name": "Workplace Stress",
        "start_pos": 100,
        "end_pos": 250,
        "segment_text": "I often feel overwhelmed with the workload and tight deadlines.",
        "reasoning": "Clear expression of stress related to workload",
        "confidence": 0.85,
        "status": "pending"
    }


@pytest.fixture
def sample_session_data():
    """Sample data for creating AICodingSession instances."""
    return {
        "project_path": "/home/user/test_project.qda",
        "description": "Test coding session",
        "file_ids": [1, 2, 3],
        "code_names": ["Workplace Stress", "Coping Strategies"],
        "instruction": "Code all relevant segments",
        "min_confidence": 0.6
    }


# =============================================================================
# DATABASE FIXTURES (used by test_server_tools.py, test_resources.py,
#                    test_security.py)
# =============================================================================

@pytest.fixture
def qualcoder_db_path(tmp_path):
    """Create a complete QualCoder-compatible SQLite database.

    This is the most comprehensive fixture with all 13 tables and realistic
    test data. Used by server tool tests, resource tests, and security tests.
    """
    project_folder = tmp_path / "test_project.qda"
    project_folder.mkdir()
    db_file = project_folder / "data.qda"

    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE project (
            databaseversion TEXT, date TEXT, memo TEXT, about TEXT,
            bookmarkfile INTEGER, bookmarkpos INTEGER, codername TEXT,
            recently_used_codes TEXT
        )
    """)
    cursor.execute("""INSERT INTO project (databaseversion, date, memo, about, codername)
        VALUES ('v14', '2024-01-15', 'Test project', 'About', 'TestCoder')""")

    cursor.execute("""
        CREATE TABLE code_cat (
            catid INTEGER PRIMARY KEY, name TEXT UNIQUE, memo TEXT,
            owner TEXT, date TEXT, supercatid INTEGER
        )
    """)
    cursor.execute("INSERT INTO code_cat VALUES (1, 'Category A', '', 'TestCoder', '2024-01-15', NULL)")

    cursor.execute("""
        CREATE TABLE code_name (
            cid INTEGER PRIMARY KEY, name TEXT UNIQUE, memo TEXT,
            catid INTEGER, owner TEXT, date TEXT, color TEXT
        )
    """)
    codes = [
        (1, "Stress", "Stress code", 1, "TestCoder", "2024-01-15", "#FF0000"),
        (2, "Coping", "Coping code", 1, "TestCoder", "2024-01-15", "#00FF00"),
    ]
    cursor.executemany("INSERT INTO code_name VALUES (?, ?, ?, ?, ?, ?, ?)", codes)

    cursor.execute("""
        CREATE TABLE source (
            id INTEGER PRIMARY KEY, name TEXT, fulltext TEXT, mediapath TEXT,
            memo TEXT, owner TEXT, date TEXT, av_text_id INTEGER, risid INTEGER,
            UNIQUE(name)
        )
    """)
    cursor.execute("""INSERT INTO source (id, name, fulltext, mediapath, memo, owner, date)
        VALUES (1, 'interview.txt',
        'This is interview text. I feel stressed about deadlines. I cope by exercising.',
        NULL, 'Test memo', 'TestCoder', '2024-01-15')""")
    cursor.execute("""INSERT INTO source (id, name, fulltext, mediapath, memo, owner, date)
        VALUES (2, 'notes.txt',
        'Field notes from observation session.',
        NULL, '', 'TestCoder', '2024-01-16')""")

    cursor.execute("""
        CREATE TABLE code_text (
            ctid INTEGER PRIMARY KEY, cid INTEGER, fid INTEGER,
            seltext TEXT, pos0 INTEGER, pos1 INTEGER,
            owner TEXT, date TEXT, memo TEXT, avid INTEGER,
            important INTEGER,
            UNIQUE(cid, fid, pos0, pos1, owner)
        )
    """)
    cursor.execute("""INSERT INTO code_text (ctid, cid, fid, seltext, pos0, pos1, owner, date, memo, important)
        VALUES (1, 1, 1,
        'I feel stressed about deadlines', 24, 55, 'TestCoder', '2024-01-15', 'key passage', 1)""")
    cursor.execute("""INSERT INTO code_text (ctid, cid, fid, seltext, pos0, pos1, owner, date, memo, important)
        VALUES (2, 2, 1,
        'I cope by exercising', 57, 77, 'TestCoder', '2024-01-15', '', 0)""")

    cursor.execute("""
        CREATE TABLE cases (
            caseid INTEGER PRIMARY KEY, name TEXT, memo TEXT, owner TEXT, date TEXT,
            CONSTRAINT ucm UNIQUE(name)
        )
    """)
    cursor.execute("INSERT INTO cases VALUES (1, 'Case A', 'First case', 'TestCoder', '2024-01-15')")

    cursor.execute("""
        CREATE TABLE case_text (
            id INTEGER PRIMARY KEY, caseid INTEGER, fid INTEGER,
            pos0 INTEGER, pos1 INTEGER, memo TEXT, owner TEXT, date TEXT
        )
    """)
    cursor.execute("INSERT INTO case_text VALUES (1, 1, 1, 0, 100, '', 'TestCoder', '2024-01-15')")

    cursor.execute("""
        CREATE TABLE annotation (
            anid INTEGER PRIMARY KEY, fid INTEGER, pos0 INTEGER,
            pos1 INTEGER, memo TEXT, owner TEXT, date TEXT
        , unique(fid,pos0,pos1,owner))
    """)

    cursor.execute("""
        CREATE TABLE journal (
            jid INTEGER PRIMARY KEY, name TEXT, jentry TEXT, date TEXT, owner TEXT
        , unique(name))
    """)
    cursor.execute("CREATE TABLE coder_names (name TEXT UNIQUE NOT NULL)")
    cursor.execute("INSERT INTO journal VALUES (1, 'Entry 1', 'Some notes', '2024-01-15', 'TestCoder')")

    cursor.execute("""
        CREATE TABLE attribute_type (
            name TEXT PRIMARY KEY, date TEXT, owner TEXT,
            memo TEXT, caseOrFile TEXT, valuetype TEXT
        )
    """)
    cursor.execute("INSERT INTO attribute_type VALUES ('Age', '2024-01-15', 'TestCoder', '', 'case', 'numeric')")

    cursor.execute("""
        CREATE TABLE attribute (
            attrid INTEGER PRIMARY KEY, name TEXT, attr_type TEXT,
            value TEXT, id INTEGER, date TEXT, owner TEXT
        , unique(name,attr_type,id))
    """)
    cursor.execute("INSERT INTO attribute VALUES (1, 'Age', 'case', '30', 1, '2024-01-15', 'TestCoder')")

    cursor.execute("""
        CREATE TABLE code_image (
            imid INTEGER PRIMARY KEY, id INTEGER,
            x1 INTEGER, y1 INTEGER, width INTEGER, height INTEGER,
            cid INTEGER, memo TEXT, date TEXT, owner TEXT,
            important INTEGER, pdf_page INTEGER
        )
    """)

    cursor.execute("""
        CREATE TABLE code_av (
            avid INTEGER PRIMARY KEY, cid INTEGER, id INTEGER,
            pos0 INTEGER, pos1 INTEGER,
            memo TEXT, owner TEXT, date TEXT, important INTEGER DEFAULT 0
        )
    """)

    conn.commit()
    conn.close()

    # A freshly built fixture project has NO AI coder name sidecar: it is
    # a project that has never been asked. The server fixtures below write
    # one deliberately; this assertion is the leak guard that keeps a
    # stray sidecar (from a test that wrote one into a shared folder)
    # from making an ask-flow test pass for the wrong reason (D7 10.1).
    assert not (project_folder / SIDECAR_NAME).exists()

    yield str(project_folder)


@pytest.fixture
def empty_db_path(tmp_path):
    """Create an empty QualCoder database (schema only, no data rows except project).

    Used for testing empty-state behavior in resources and tools.
    """
    project_folder = tmp_path / "empty_project.qda"
    project_folder.mkdir()
    db_file = project_folder / "data.qda"

    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()

    cursor.execute("CREATE TABLE project (databaseversion TEXT, date TEXT, memo TEXT, about TEXT, bookmarkfile INTEGER, bookmarkpos INTEGER, codername TEXT, recently_used_codes TEXT)")
    cursor.execute("INSERT INTO project (databaseversion, date, memo, about, codername) VALUES ('v14', '2024-01-15', '', '', 'TestCoder')")
    cursor.execute("CREATE TABLE code_cat (catid INTEGER PRIMARY KEY, name TEXT UNIQUE, memo TEXT, owner TEXT, date TEXT, supercatid INTEGER)")
    cursor.execute("CREATE TABLE code_name (cid INTEGER PRIMARY KEY, name TEXT UNIQUE, memo TEXT, catid INTEGER, owner TEXT, date TEXT, color TEXT)")
    cursor.execute("CREATE TABLE source (id INTEGER PRIMARY KEY, name TEXT, fulltext TEXT, mediapath TEXT, memo TEXT, owner TEXT, date TEXT, av_text_id INTEGER, risid INTEGER, UNIQUE(name))")
    cursor.execute("CREATE TABLE code_text (ctid INTEGER PRIMARY KEY, cid INTEGER, fid INTEGER, seltext TEXT, pos0 INTEGER, pos1 INTEGER, owner TEXT, date TEXT, memo TEXT, avid INTEGER, important INTEGER, UNIQUE(cid, fid, pos0, pos1, owner))")
    cursor.execute("CREATE TABLE cases (caseid INTEGER PRIMARY KEY, name TEXT, memo TEXT, owner TEXT, date TEXT, CONSTRAINT ucm UNIQUE(name))")
    cursor.execute("CREATE TABLE case_text (id INTEGER PRIMARY KEY, caseid INTEGER, fid INTEGER, pos0 INTEGER, pos1 INTEGER, memo TEXT, owner TEXT, date TEXT)")
    cursor.execute("CREATE TABLE annotation (anid INTEGER PRIMARY KEY, fid INTEGER, pos0 INTEGER, pos1 INTEGER, memo TEXT, owner TEXT, date TEXT, unique(fid,pos0,pos1,owner))")
    cursor.execute("CREATE TABLE journal (jid INTEGER PRIMARY KEY, name TEXT, jentry TEXT, date TEXT, owner TEXT, unique(name))")
    cursor.execute("CREATE TABLE coder_names (name TEXT UNIQUE NOT NULL)")
    cursor.execute("CREATE TABLE attribute_type (name TEXT PRIMARY KEY, date TEXT, owner TEXT, memo TEXT, caseOrFile TEXT, valuetype TEXT)")
    cursor.execute("CREATE TABLE attribute (attrid INTEGER PRIMARY KEY, name TEXT, attr_type TEXT, value TEXT, id INTEGER, date TEXT, owner TEXT, unique(name,attr_type,id))")
    cursor.execute("CREATE TABLE code_image (imid INTEGER PRIMARY KEY, id INTEGER, x1 INTEGER, y1 INTEGER, width INTEGER, height INTEGER, cid INTEGER, memo TEXT, date TEXT, owner TEXT, important INTEGER, pdf_page INTEGER)")
    cursor.execute("CREATE TABLE code_av (avid INTEGER PRIMARY KEY, cid INTEGER, id INTEGER, pos0 INTEGER, pos1 INTEGER, memo TEXT, owner TEXT, date TEXT, important INTEGER DEFAULT 0)")

    conn.commit()
    conn.close()

    assert not (project_folder / SIDECAR_NAME).exists()   # D7 10.1

    yield str(project_folder)


# =============================================================================
# SERVER FIXTURES (used by test_server_tools.py, test_resources.py,
#                  test_security.py)
# =============================================================================

@pytest.fixture
def setup_server(qualcoder_db_path, tmp_path):
    """Set up server globals for testing.

    Saves and restores original server state to avoid cross-test contamination.
    """
    original_db = server.db
    original_path = server.current_project_path
    original_sm = server.session_manager

    write_fixture_sidecar(qualcoder_db_path)
    server.db = QualcoderDatabase(qualcoder_db_path)
    server.current_project_path = qualcoder_db_path
    server.session_manager = SessionManager(str(tmp_path / "sessions"))

    yield server

    if server.db is not None:
        try:
            server.db.close()
        except Exception:
            pass
    server.db = original_db
    server.current_project_path = original_path
    server.session_manager = original_sm


@pytest.fixture
def setup_empty_server(empty_db_path, tmp_path):
    """Set up server with empty database.

    Used for testing empty-state behavior in resources.
    """
    original_db = server.db
    original_path = server.current_project_path
    original_sm = server.session_manager

    write_fixture_sidecar(empty_db_path)
    server.db = QualcoderDatabase(empty_db_path)
    server.current_project_path = empty_db_path
    server.session_manager = SessionManager(str(tmp_path / "sessions"))

    yield server

    if server.db is not None:
        try:
            server.db.close()
        except Exception:
            pass
    server.db = original_db
    server.current_project_path = original_path
    server.session_manager = original_sm


@pytest.fixture
def setup_server_unset(qualcoder_db_path, tmp_path):
    """The same server on a project that has NEVER been asked.

    No sidecar is written, so the first write tool that needs an owner
    returns the ASK refusal. Used by the ask-flow, migration and getter
    tests (B1.18 point 2).
    """
    original_db = server.db
    original_path = server.current_project_path
    original_sm = server.session_manager

    assert not (Path(qualcoder_db_path) / SIDECAR_NAME).exists()
    server.db = QualcoderDatabase(qualcoder_db_path)
    server.current_project_path = qualcoder_db_path
    server.session_manager = SessionManager(str(tmp_path / "sessions"))

    yield server

    if server.db is not None:
        try:
            server.db.close()
        except Exception:
            pass
    server.db = original_db
    server.current_project_path = original_path
    server.session_manager = original_sm


@pytest.fixture
def session_with_suggestions(setup_server, qualcoder_db_path):
    """Create a session with pre-populated suggestions.

    Used for testing review, update, and apply workflows.
    """
    session = AICodingSession(
        project_path=qualcoder_db_path,
        description="Test session",
        file_ids=[1],
        code_names=["Stress"],
        instruction="Test",
        min_confidence=0.6
    )

    s1 = CodingSuggestion(
        file_id=1, file_name="interview.txt",
        code_id=1, code_name="Stress",
        start_pos=0, end_pos=10,
        segment_text="This is in",
        reasoning="Test reasoning", confidence=0.85,
        status="pending"
    )
    s2 = CodingSuggestion(
        file_id=1, file_name="interview.txt",
        code_id=2, code_name="Coping",
        start_pos=57, end_pos=77,
        segment_text="I cope by exercising",
        reasoning="Coping behavior", confidence=0.9,
        status="pending"
    )
    session.add_suggestion(s1)
    session.add_suggestion(s2)

    setup_server.session_manager.save_session(session)
    return session
