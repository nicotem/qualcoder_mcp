# SPDX-License-Identifier: LGPL-3.0-or-later
# Folded into the main suite from the v0.6.0-alpha parallel test campaign
# (track 5, property-based/Hypothesis); adapted paths/fixtures only — test logic unchanged.
"""Helpers for track5 property-based testing of qualcoder_mcp.

Builds random-but-VALID v14 QualCoder projects, wires the server globals to
them, and provides direct (out-of-band) invariant checks that read the SQLite
file with a *fresh* read-only connection so the checks never depend on the
server's own connection state.

All paths are absolute. Generated projects live under
scratchpad/tests-out/track5/tmp/ (created on demand) and are cleaned up by the
caller.
"""

import os
import stat as stat_module
import sys
import uuid
import shutil
import sqlite3
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional

# --- make the package importable from the repo source tree ---
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import qualcoder_mcp.server as server  # noqa: E402
from qualcoder_mcp import database as _database  # noqa: E402
from qualcoder_mcp import preview_tokens as _preview_tokens  # noqa: E402
from qualcoder_mcp.database import QualcoderDatabase  # noqa: E402
from qualcoder_mcp.project_settings import (  # noqa: E402
    DEFAULT_AI_CODER_NAME,
    SIDECAR_NAME,
    write_ai_coder_name,
)
from qualcoder_mcp.sessions import (  # noqa: E402
    SessionManager,
    AICodingSession,
    CodingSuggestion,
)

TMP_ROOT = Path(__file__).resolve().parent / "tmp"

# The researcher's own folders, read at IMPORT time, before any fixture
# has moved HOME or rebound a constant: these are the folders the
# shipped server would really write into, and nothing in the suite may
# write there (QA round 1, F1). conftest's session-wide guard and
# tests/test_suite_hygiene.py both work from these names.
#
# Both are read through the module's own bindings rather than rebuilt
# from Path.home() here, so the guard watches what the code actually
# resolves: the workspace through `default_workspace()`, called once at
# import while the home is still the real one (the binding itself is
# late since the 0.12 release preparation), the state home from its
# constant. The workspace takes project copies; the state home takes the
# MRU hint, the preview-token secret and the AI coding sessions, and it
# is watched in FULL rather than at its top level, because a leaked
# session file lands one directory down (fix round 2, the widening the
# gate asked for).
REAL_WORKSPACE = Path(_database.default_workspace())
REAL_STATE_HOME = Path(_preview_tokens.STATE_HOME)
WORKSPACE_UNREADABLE = "unreadable"

GUARDED_REAL_FOLDERS = {
    "workspace": REAL_WORKSPACE,
    "state home": REAL_STATE_HOME,
}
# v0.14: when the environment sets QUALCODER_MCP_WORKSPACE (the desktop
# extension's folder for projects), REAL_WORKSPACE above is that folder,
# and the standard one is watched as well, since the sandbox clears the
# variable and a leak would then land there.
if Path(_database.standard_workspace()) != REAL_WORKSPACE:
    GUARDED_REAL_FOLDERS["standard workspace"] = Path(
        _database.standard_workspace())


def real_workspace_entries(workspace=None):
    """Every path inside one of the researcher's real folders.

    Relative, recursive, as posix strings. Recursive because the state
    home's leak is a session file inside `sessions/`, which a top-level
    listing cannot see.

    None when the folder does not exist (a folder that is not there is
    not an empty folder: creating it is itself a write into the
    researcher's own directories); the `WORKSPACE_UNREADABLE` marker
    when it cannot be listed (permissions, a network volume), since
    inventing an empty set there would make the guard pass vacuously.
    """
    folder = Path(workspace) if workspace is not None else REAL_WORKSPACE
    try:
        if not folder.is_dir():
            raise FileNotFoundError(str(folder))
        return {p.relative_to(folder).as_posix() for p in folder.rglob("*")}
    except FileNotFoundError:
        return None
    except OSError:
        return WORKSPACE_UNREADABLE


# ---------------------------------------------------------------------------
# Windows sharing semantics, emulated on POSIX
# ---------------------------------------------------------------------------
# Windows refuses to rename or remove a directory that still contains an
# OPEN file, and refuses to rename or replace an open file, because
# SQLite (like most writers) opens without FILE_SHARE_DELETE. POSIX
# allows all three, so a test that moves or deletes a project while the
# server still holds its connection passes here and fails there with
# WinError 32, on both Windows jobs and on no other job. That is a whole
# class of defect that only a Windows runner can see, and a runner is
# the slowest place to find it, so the suite emulates the rule on POSIX
# instead (conftest's `_windows_sharing_semantics`).
SHARING_VIOLATION = (
    "the process cannot access the file because it is being used by "
    "another process (WinError 32, emulated): ")


def _open_regular_file_keys():
    """(st_dev, st_ino) of every regular file this process has open.

    Read through `fstat` on the descriptor numbers the kernel lists, so
    it does not depend on what `stat` of a `/dev/fd` entry means on a
    given POSIX; Linux and macOS spell the directory differently and
    both are handled.
    """
    fd_dir = "/proc/self/fd" if os.path.isdir("/proc/self/fd") else "/dev/fd"
    keys = set()
    try:
        names = os.listdir(fd_dir)
    except OSError:
        return keys
    for name in names:
        try:
            st = os.fstat(int(name))
        except (OSError, ValueError):
            continue
        if stat_module.S_ISREG(st.st_mode):
            keys.add((st.st_dev, st.st_ino))
    return keys


def open_paths_under(target) -> List[str]:
    """Paths at or under `target` that this process still holds open.

    Empty when nothing is open, which is the only state in which Windows
    would allow the caller's rename, replace or removal.
    """
    keys = _open_regular_file_keys()
    if not keys:
        return []
    target = Path(target)
    found: List[str] = []
    try:
        is_dir = target.is_dir()
    except OSError:
        return found
    candidates = target.rglob("*") if is_dir else iter((target,))
    for path in candidates:
        try:
            st = path.stat()
        except OSError:
            continue
        if stat_module.S_ISREG(st.st_mode) and (st.st_dev, st.st_ino) in keys:
            found.append(str(path))
    return sorted(found)

# ---------------------------------------------------------------------------
# v14 schema (identical column layout to tests/conftest.py's fixture)
# ---------------------------------------------------------------------------
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
        pos1 INTEGER, memo TEXT, owner TEXT, date TEXT, unique(fid,pos0,pos1,owner))""",
    """CREATE TABLE journal (jid INTEGER PRIMARY KEY, name TEXT, jentry TEXT, date TEXT, owner TEXT, unique(name))""",
    """CREATE TABLE coder_names (name TEXT UNIQUE NOT NULL)""",
    """CREATE TABLE attribute_type (name TEXT PRIMARY KEY, date TEXT, owner TEXT,
        memo TEXT, caseOrFile TEXT, valuetype TEXT)""",
    """CREATE TABLE attribute (attrid INTEGER PRIMARY KEY, name TEXT, attr_type TEXT,
        value TEXT, id INTEGER, date TEXT, owner TEXT, unique(name,attr_type,id))""",
    """CREATE TABLE code_image (imid INTEGER PRIMARY KEY, id INTEGER, x1 INTEGER, y1 INTEGER,
        width INTEGER, height INTEGER, cid INTEGER, memo TEXT, date TEXT, owner TEXT,
        important INTEGER, pdf_page INTEGER)""",
    """CREATE TABLE code_av (avid INTEGER PRIMARY KEY, cid INTEGER, id INTEGER, pos0 INTEGER,
        pos1 INTEGER, memo TEXT, owner TEXT, date TEXT, important INTEGER DEFAULT 0)""",
]

U2029 = " "


def build_project(spec: Dict[str, Any], parent: Optional[Path] = None) -> str:
    """Materialise a project spec into a `<name>.qda/data.qda` folder.

    `spec` is a fully-resolved dict (ids assigned by list order, 1-based):
      name, files[], categories[], codes[], codings[], cases[]
    Returns the absolute path to the .qda project folder.
    """
    if parent is None:
        parent = TMP_ROOT
    parent = Path(parent)
    parent.mkdir(parents=True, exist_ok=True)
    stem = spec.get("name") or f"proj_{uuid.uuid4().hex[:10]}"
    project_folder = parent / f"{stem}.qda"
    # ensure a unique folder
    while project_folder.exists():
        project_folder = parent / f"{stem}_{uuid.uuid4().hex[:6]}.qda"
    project_folder.mkdir()
    data = project_folder / "data.qda"

    conn = sqlite3.connect(str(data))
    cur = conn.cursor()
    for ddl in SCHEMA_SQL:
        cur.execute(ddl)

    cur.execute(
        "INSERT INTO project (databaseversion, date, memo, about, codername) "
        "VALUES ('v14', '2024-01-15', ?, 'QualCoder project', 'TestCoder')",
        (spec.get("project_memo", "prop test"),),
    )

    for idx, f in enumerate(spec.get("files", []), start=1):
        cur.execute(
            "INSERT INTO source (id, name, fulltext, mediapath, memo, owner, date) "
            "VALUES (?, ?, ?, ?, ?, 'TestCoder', '2024-01-15')",
            (idx, f["name"], f.get("fulltext"), f.get("mediapath"), f.get("memo")),
        )

    for idx, c in enumerate(spec.get("categories", []), start=1):
        cur.execute(
            "INSERT INTO code_cat (catid, name, memo, owner, date, supercatid) "
            "VALUES (?, ?, ?, 'TestCoder', '2024-01-15', ?)",
            (idx, c["name"], c.get("memo"), c.get("supercatid")),
        )

    for idx, c in enumerate(spec.get("codes", []), start=1):
        cur.execute(
            "INSERT INTO code_name (cid, name, memo, catid, owner, date, color) "
            "VALUES (?, ?, ?, ?, 'TestCoder', '2024-01-15', ?)",
            (idx, c["name"], c.get("memo"), c.get("catid"), c.get("color", "#FF0000")),
        )

    for idx, cx in enumerate(spec.get("codings", []), start=1):
        cur.execute(
            "INSERT INTO code_text (ctid, cid, fid, seltext, pos0, pos1, owner, date, memo, important) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, '2024-01-15', ?, ?)",
            (idx, cx["cid"], cx["fid"], cx["seltext"], cx["pos0"], cx["pos1"],
             cx.get("owner", "TestCoder"), cx.get("memo"), cx.get("important")),
        )

    for idx, cs in enumerate(spec.get("cases", []), start=1):
        cur.execute(
            "INSERT INTO cases (caseid, name, memo, owner, date) "
            "VALUES (?, ?, ?, 'TestCoder', '2024-01-15')",
            (idx, cs["name"], cs.get("memo")),
        )

    # one file-type attribute type so import_text_file exercises attribute
    # placeholder creation
    if spec.get("with_file_attribute", True):
        cur.execute(
            "INSERT INTO attribute_type (name, date, owner, memo, caseOrFile, valuetype) "
            "VALUES ('Source', '2024-01-15', 'TestCoder', '', 'file', 'character')"
        )

    conn.commit()
    conn.close()
    return str(project_folder)


# ---------------------------------------------------------------------------
# server wiring
# ---------------------------------------------------------------------------
def execute_destructive(tool, *args, **kwargs) -> str:
    """Run a token-gated destructive tool the way a caller must: in two steps.

    v0.12 replaced `confirm=True` with a preview token, so a test that
    used to say "do it" now has to preview, take the token, show nothing
    to nobody, and execute. This helper does exactly that and returns
    what the tool returns, so the assertions around the old call sites
    stay as they were and keep testing what they were written for.

    A preview that comes back without a token (a refusal, a validation
    error) is handed back unchanged, because that is the answer the test
    is about.
    """
    import json as _json

    preview = _json.loads(tool(*args, **kwargs))
    token = preview.get("preview_token")
    if token is None:
        return _json.dumps(preview, indent=2)
    return tool(*args, preview_token=token, **kwargs)


def write_fixture_sidecar(project_path, name: str = DEFAULT_AI_CODER_NAME,
                          note: str = "test fixture") -> None:
    """Make a fixture project one that has been asked for its AI coder name.

    v0.12 refuses every database write on a project whose AI coder name is
    unset and asks the user to choose one (D7), so a fixture project
    without the sidecar models a project on its FIRST contact with this
    release, not a project in use. Every fixture that writes through the
    eleven write tools therefore records the built-in default here, which
    is the owner string those tests already assert, and the ask, mismatch
    and owner-argument tests use a project with no sidecar instead
    (B1.18). The real writer is used deliberately: it then runs on every
    platform, thousands of times, for free.
    """
    folder = Path(project_path)
    if folder.suffix != ".qda" and folder.name == "data.qda":
        folder = folder.parent
    write_ai_coder_name(folder, name, note=note, host_declaration=None)


def set_server_project(project_path: str, sessions_dir: str) -> None:
    """Point the server's globals at `project_path` (read-only), fresh session mgr."""
    if server.db is not None:
        try:
            server.db.close()
        except Exception:
            pass
    write_fixture_sidecar(project_path)
    server.db = QualcoderDatabase(project_path, read_only=True)
    server.current_project_path = project_path
    server.session_manager = SessionManager(sessions_dir)


def teardown_server(saved) -> None:
    if server.db is not None:
        try:
            server.db.close()
        except Exception:
            pass
    server.db, server.current_project_path, server.session_manager = saved


def save_server_state():
    return (server.db, server.current_project_path, server.session_manager)


# ---------------------------------------------------------------------------
# out-of-band DB inspection (fresh read-only connection every time)
# ---------------------------------------------------------------------------
def _ro_conn(project_path: str) -> sqlite3.Connection:
    data = Path(project_path) / "data.qda"
    # POSIX-and-Windows-safe read-only URI (see database._sqlite_ro_uri)
    conn = sqlite3.connect(f"{data.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def query(project_path: str, sql: str, args=()) -> List[sqlite3.Row]:
    """Read rows out of band, on a fresh read-only connection.

    Used by the property tests to compute a reference answer without
    going through the server's own connection state (v0.12 B4).
    """
    conn = _ro_conn(project_path)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def execute(project_path: str, sql: str, args=()) -> None:
    """Write directly, out of band, to set up a drift scenario."""
    data = Path(project_path) / "data.qda"
    conn = sqlite3.connect(str(data))
    try:
        conn.execute(sql, args)
        conn.commit()
    finally:
        conn.close()


def core_invariant_problems(project_path: str) -> List[str]:
    """Return a list of invariant-violation descriptions (empty == all good).

    Checks, out-of-band:
      * PRAGMA integrity_check == ok  and it is still a parseable v14 project
      * no orphan code_text (cid resolves to code_name, fid resolves to source)
      * no duplicate violating UNIQUE(cid,fid,pos0,pos1,owner)
      * every code_text row: seltext == fulltext[pos0:pos1]  (U+2029->\\n tolerated)
    """
    problems: List[str] = []
    conn = _ro_conn(project_path)
    try:
        integ = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integ != "ok":
            problems.append(f"integrity_check={integ!r}")

        prow = conn.execute("SELECT databaseversion FROM project").fetchone()
        if prow is None or prow[0] != "v14":
            problems.append(f"databaseversion={None if prow is None else prow[0]!r}")

        # required tables still present
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for req in ("project", "code_name", "code_text", "source", "cases"):
            if req not in tables:
                problems.append(f"missing table {req}")

        fulltexts = {r["id"]: (r["fulltext"] if r["fulltext"] is not None else "")
                     for r in conn.execute("SELECT id, fulltext FROM source")}
        code_ids = {r["cid"] for r in conn.execute("SELECT cid FROM code_name")}

        seen = set()
        for r in conn.execute(
            "SELECT ctid, cid, fid, seltext, pos0, pos1, owner FROM code_text"
        ):
            ctid = r["ctid"]
            if r["fid"] not in fulltexts:
                problems.append(f"orphan code_text ctid={ctid}: fid={r['fid']} not in source")
            if r["cid"] not in code_ids:
                problems.append(f"orphan code_text ctid={ctid}: cid={r['cid']} not in code_name")
            key = (r["cid"], r["fid"], r["pos0"], r["pos1"], r["owner"])
            if key in seen:
                problems.append(f"duplicate UNIQUE key {key} (ctid={ctid})")
            seen.add(key)
            if r["fid"] in fulltexts:
                ft = fulltexts[r["fid"]]
                p0, p1 = r["pos0"], r["pos1"]
                sel = r["seltext"] if r["seltext"] is not None else ""
                sl = ft[p0:p1] if (isinstance(p0, int) and isinstance(p1, int)) else None
                if sl is None:
                    problems.append(f"ctid={ctid}: non-int positions {p0},{p1}")
                elif sel != sl and sel.replace(U2029, "\n") != sl:
                    problems.append(
                        f"ctid={ctid}: seltext!=fulltext[{p0}:{p1}] "
                        f"(seltext={sel!r} slice={sl!r} owner={r['owner']!r})"
                    )
    finally:
        conn.close()
    return problems


def db_content_hash(project_path: str) -> str:
    """Deterministic hash of the full logical DB content (schema + all rows)."""
    conn = _ro_conn(project_path)
    try:
        h = hashlib.sha256()
        for line in conn.iterdump():
            h.update(line.encode("utf-8", "surrogatepass"))
            h.update(b"\n")
        return h.hexdigest()
    finally:
        conn.close()


def count_mcp_backups(project_path: str) -> int:
    folder = Path(project_path)
    stem = folder.stem
    return len(list(folder.parent.glob(f"{stem}_backup_*.qda")))


def row_counts(project_path: str) -> Dict[str, int]:
    conn = _ro_conn(project_path)
    try:
        out = {}
        for t in ("source", "code_name", "code_cat", "code_text", "cases",
                  "case_text", "attribute"):
            out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        return out
    finally:
        conn.close()


def list_text_files(project_path: str) -> List[Dict[str, Any]]:
    """Text files with non-empty fulltext (codable), out-of-band."""
    conn = _ro_conn(project_path)
    try:
        rows = conn.execute("SELECT id, name, fulltext, mediapath FROM source").fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        mp = r["mediapath"]
        is_text = not mp or mp.startswith("/docs/") or mp.startswith("docs:")
        ft = r["fulltext"] or ""
        if is_text and ft:
            out.append({"id": r["id"], "name": r["name"], "fulltext": ft})
    return out


def list_codes(project_path: str) -> List[Dict[str, Any]]:
    conn = _ro_conn(project_path)
    try:
        rows = conn.execute("SELECT cid, name FROM code_name").fetchall()
    finally:
        conn.close()
    return [{"id": r["cid"], "name": r["name"]} for r in rows]


def list_cases(project_path: str) -> List[Dict[str, Any]]:
    conn = _ro_conn(project_path)
    try:
        rows = conn.execute("SELECT caseid, name FROM cases").fetchall()
    finally:
        conn.close()
    return [{"id": r["caseid"], "name": r["name"]} for r in rows]


def list_ctids(project_path: str) -> List[int]:
    conn = _ro_conn(project_path)
    try:
        return [r[0] for r in conn.execute("SELECT ctid FROM code_text")]
    finally:
        conn.close()


def existing_coding_keys(project_path: str):
    """Set of (cid,fid,pos0,pos1,owner) already present."""
    conn = _ro_conn(project_path)
    try:
        return {(r["cid"], r["fid"], r["pos0"], r["pos1"], r["owner"])
                for r in conn.execute(
                    "SELECT cid, fid, pos0, pos1, owner FROM code_text")}
    finally:
        conn.close()


def make_session(project_path: str, suggestions: List[CodingSuggestion]) -> AICodingSession:
    sess = AICodingSession(
        project_path=project_path,
        description="track5",
        file_ids=sorted({s.file_id for s in suggestions}),
        code_names=sorted({s.code_name for s in suggestions}),
        instruction="prop",
        min_confidence=0.0,
    )
    for s in suggestions:
        sess.add_suggestion(s)
    server.session_manager.save_session(sess)
    return sess


def run_pytest_in_a_child(target: str, tmp_path: Path, cwd, timeout=300):
    """`pytest -ra -q` over `target` in a fresh interpreter, the way CI
    runs the suite; returns the finished process and the folder the child
    was given as its system temporary directory.

    v0.14 (the workbench): the child used to take pytest's default base
    folder, `pytest-of-<user>` in the system's temporary directory, and
    leave a `pytest-<n>` folder there on every run. It is now given one
    under the parent's `tmp_path` (`--basetemp`), which the parent's own
    run cleans up, and every variable that names the system temporary
    directory (TMPDIR, TEMP, TMP, and pytest's PYTEST_DEBUG_TEMPROOT)
    points at an empty folder beside it, so the caller can show that the
    child left nothing there.
    """
    import subprocess
    system_tmp = tmp_path / "child-system-tmp"
    system_tmp.mkdir()
    env = dict(os.environ)
    for name in ("TMPDIR", "TEMP", "TMP", "PYTEST_DEBUG_TEMPROOT"):
        env[name] = str(system_tmp)
    result = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "-ra", "-q",
         "-p", "no:cacheprovider", f"--basetemp={tmp_path / 'child-base'}",
         target],
        cwd=str(cwd), env=env, capture_output=True, text=True,
        timeout=timeout)
    return result, system_tmp
