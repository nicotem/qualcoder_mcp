"""Database interface for Qualcoder .qda files."""

import bisect
import os
import sqlite3
from pathlib import Path
from typing import Optional, List, Dict, Any, Union
import json
import re
import time
import random
import getpass
import logging
import hashlib
import unicodedata
import uuid
import shutil
from datetime import datetime
from contextlib import contextmanager

from .memo_privacy import (
    PERSONAL_NOTE_MARK,
    extract_ai_memo,
    merge_public_memo,
    neutralize_marker,
    split_public_private_memo,
)

logger = logging.getLogger(__name__)

# Configuration constants
DEFAULT_LIMIT = 50
MAX_LIMIT = 5000
# Schemas verified for reading AND writing: v14 (QualCoder 3.8.x) through
# v17 (unreleased QualCoder master, version string "QualCoder 4.0 Beta",
# pinned commit 9bddf17). The version string is INFORMATIONAL: the write
# gate and every version-dependent recipe key on capability probes (column
# and table existence, SchemaCapabilities below), exactly as upstream's own
# migration ladder does (master __main__.py:2296-2346). The one place the
# string still has teeth is the forward guard: a version newer than the
# verified ceiling refuses writes unless explicitly overridden, because
# probes prove the columns we know about, never the absence of a future
# semantic change.
SUPPORTED_DB_VERSIONS = ['v14', 'v15', 'v16', 'v17']
MAX_VERIFIED_SCHEMA = 17
VERIFIED_MASTER_COMMIT = "9bddf17"
_VERSION_STRING_RE = re.compile(r"^v(\d+)$")
# Environment override for the forward guard (v18+/unparseable versions)
ALLOW_UNKNOWN_SCHEMA_ENV = "QUALCODER_MCP_ALLOW_UNKNOWN_SCHEMA"


class SchemaCapabilities:
    """What this database can do, derived from column/table EXISTENCE.

    Probed once per connection (one sqlite_master scan plus PRAGMA
    table_info per probed table). All QualCoder migrations v14 to v17 are
    purely additive, so existence is a sound and stable discriminator
    (master __main__.py:2296-2346). Marks in comments are informational;
    behaviour keys on the individual booleans, never on the version string.
    """

    def __init__(self, has_coder_names=False, has_av_bookmarks=False,
                 has_avbookmarktextpos=False, has_supercid=False,
                 has_graph_labels=False, has_gr_memo_item=False,
                 has_coder_visibility=False, tables=frozenset()):
        self.has_coder_names = has_coder_names          # v14 floor
        self.has_av_bookmarks = has_av_bookmarks        # v15 (informational)
        self.has_avbookmarktextpos = has_avbookmarktextpos  # v15 repair
        self.has_supercid = has_supercid                # v16 SUB-CODES switch
        self.has_graph_labels = has_graph_labels        # v17 (informational)
        self.has_gr_memo_item = has_gr_memo_item        # v17 (informational)
        # QC 4.0 per-coder visibility (P1-3): coder_names.visibility
        # column AND the code_text_visible view, both created in the
        # project database on 4.0 project open (app.py:1499-1562 at pin
        # 9bddf17), so the setting travels with the project. Partial
        # states (column without view, view without column) probe False.
        self.has_coder_visibility = has_coder_visibility
        self.tables = frozenset(tables)                 # for guarded cleanups

    def table_exists(self, name: str) -> bool:
        return name in self.tables

    def to_dict(self) -> Dict[str, bool]:
        return {
            "has_coder_names": self.has_coder_names,
            "has_av_bookmarks": self.has_av_bookmarks,
            "has_avbookmarktextpos": self.has_avbookmarktextpos,
            "has_supercid": self.has_supercid,
            "has_graph_labels": self.has_graph_labels,
            "has_gr_memo_item": self.has_gr_memo_item,
            "has_coder_visibility": self.has_coder_visibility,
        }

# Columns this server reads or writes that older QualCoder schemas lack.
# If any are missing, the project must be opened and saved in QualCoder 3.8
# (which migrates the schema) before this server can use it.
# code_text.important: added in schema v3; project.codername: added in v5
# and selected unconditionally by get_project_info.
REQUIRED_COLUMNS = {
    "code_text": ["important"],
    "project": ["codername"],
}

# QualCoder's code color palette (color_selector.py:53-65, QualCoder 3.8.2).
# New codes get a random pick from this palette, exactly like codes created
# in the QualCoder GUI.
QUALCODER_COLORS = [
    "#F5F6CE", "#F2F5A9", "#F4FA58", "#F7FE2E", "#DDE600", "#F8ECE0", "#F6E3CE", "#F5D0A9", "#F7BE81", "#FAAC58",
    "#F5ECCE", "#F3E2A9", "#F5DA81", "#F7D358", "#FACC2E", "#FFE2CC", "#FFC599", "#FFA866", "#FF8B33", "#FF6F00",
    "#F8E6E0", "#F6D8CE", "#F5BCA9", "#F79F81", "#FA8258", "#FADCCC", "#F5B999", "#F09666", "#EB7333", "#E65100",
    "#F8E0E0", "#F6CECE", "#F5A9A9", "#F78181", "#FA5858", "#F0D1D1", "#E2A4A4", "#D37676", "#C54949", "#B71C1C",
    "#F2D6CE", "#E5AE9D", "#D8866D", "#CB5E3C", "#BF360C", "#E7CEDB", "#CF9EB8", "#B76E95", "#9F3E72", "#880E4F",
    "#F8E0E6", "#F6CED8", "#F5A9BC", "#F7819F", "#FA5882", "#F8E0F7", "#F6CEF5", "#F5A9F2", "#F781F3", "#FA58F4",
    "#D1DED2", "#A3BEA5", "#769E78", "#487E4B", "#1B5E20", "#DEE9E4", "#BED3C9", "#9EBDAE", "#7EA793", "#5E9179",
    "#CEF6E3", "#A9F5D0", "#81F7BE", "#58FAAC", "#00FF7F", "#E0F8E0", "#CEF6CE", "#A9F5A9", "#81F781", "#58FA58",
    "#D0F5A9", "#BEF781", "#ACFA58", "#9AFE2E", "#80FF00", "#CEF6F5", "#A9F5F2", "#81F7F3", "#58FAF4", "#00F0F0",
    "#E4D3F5", "#CAA8EB", "#B07CE1", "#9651D7", "#7D26CD", "#ECE0F8", "#E3CEF6", "#D0A9F5", "#BE81F7", "#AC58FA",
    "#DADAF5", "#B5B5EC", "#9090E3", "#6B6BDA", "#4646D1", "#CEE3F6", "#A9D0F5", "#81BEF7", "#3498DB", "#5882FA",
    "#CEDAEC", "#9EB5D9", "#6D91C6", "#3D6CB3", "#0D47A1", "#E8E8E8", "#D8D8D8", "#C8C8C8", "#B8B8B8", "#A8A8A8",
]

DB_LOCKED_MESSAGE = (
    "The project database is locked — QualCoder may have it open. "
    "Close the project in QualCoder (or wait a moment) and try again."
)


class DatabaseLockedError(RuntimeError):
    """Raised when the SQLite database is locked by another process."""


class UnsupportedSchemaError(RuntimeError):
    """Raised when the project database schema is too old for this server."""


class DatabaseOpenError(ValueError):
    """A well-formed project location whose data.qda SQLite would not open.

    Raised by validate_qda_path for the non-locked OperationalError and
    the DatabaseError cases (a locked database is DatabaseLockedError).
    It lets callers tell "the database refused to open" (genuine
    corruption, or the hot journal a QualCoder 4.0 window leaves
    mid-write, which fails a read-only open exactly like corruption)
    apart from a plain wrong path. A ValueError subclass, so every
    existing handler keeps working unchanged (QA round 1, F3).
    """


def _sqlite_ro_uri(path: Union[str, Path]) -> str:
    """Build a valid read-only SQLite file: URI for the given path.

    ``f"file:{path}?mode=ro"`` is a POSIX-only shortcut: on Windows a path is
    ``C:\\Users\\...`` (backslashes + a drive colon), which is not a valid
    file: URI and makes sqlite3 fail to open the database. ``Path.as_uri()``
    produces a spec-compliant, percent-encoded URI from an absolute path
    (``file:///C:/Users/...`` on Windows, ``file:///Users/...`` on POSIX);
    the read-only query parameter is appended to that.
    """
    return f"{Path(path).resolve().as_uri()}?mode=ro"


def _is_locked_error(e: sqlite3.Error) -> bool:
    """Check whether a sqlite3 error indicates a locked/busy database."""
    msg = str(e).lower()
    return "locked" in msg or "busy" in msg


# ----------------------------------------------------------------------------
# QualCoder application-level lock protocol (project_in_use.lock)
#
# QualCoder holds NO SQLite lock while a project is merely open — its only
# concurrency control is a lock file with a 5-second heartbeat, considered
# stale after 30 seconds (QualCoder 3.8.2 __main__.py:131-171). SQLite-level
# lock detection therefore says nothing about whether QualCoder has the
# project open; writes into a live QualCoder session succeed at the SQLite
# level and are then silently corrupted by QualCoder's snapshot-based text
# editor or deleted by its open-time hygiene. Every MCP write must respect
# this lock file.
# ----------------------------------------------------------------------------

QUALCODER_LOCK_FILENAME = "project_in_use.lock"
QUALCODER_LOCK_TIMEOUT = 30.0  # seconds; QualCoder __main__.py:131 — do not change
LOCK_READ_MAX_BYTES = 4096  # a real lock is two short lines; cap the read


def qualcoder_open_message(holder: Optional[str]) -> str:
    """Actionable message for a project currently open in QualCoder."""
    return (
        f"This project is open in QualCoder (user "
        f"{holder or 'unknown'}). Close the project in QualCoder, then retry."
    )


def qualcoder_lock_state(project_dir: Union[str, Path]) -> tuple:
    """Read QualCoder's project_in_use.lock heartbeat.

    The lock file contains two lines: the username and an epoch timestamp,
    refreshed every 5 seconds while QualCoder has the project open.

    Returns:
        (state, holder) where state is 'absent', 'active' (heartbeat within
        30 s — QualCoder is running with this project open) or 'stale'
        (QualCoder crashed or the file is unreadable).
    """
    lock = Path(project_dir) / QUALCODER_LOCK_FILENAME
    if not lock.exists():
        return "absent", None

    def _read():
        # Read only a bounded prefix: a real lock is two short lines
        # (username + epoch). A crafted/corrupt multi-gigabyte lock in a
        # shared project must not be slurped into memory (SEC S-3).
        with open(lock, "r", encoding="utf-8", errors="replace") as f:
            head = f.read(LOCK_READ_MAX_BYTES)
        lines = head.splitlines()
        return lines[0], time.time() - float(lines[1])

    try:
        holder, age = _read()
    except Exception:
        # QualCoder itself retries once after 0.5 s (__main__.py:2647-2651)
        time.sleep(0.5)
        try:
            holder, age = _read()
        except Exception:
            # Unreadable lock = treated as a dead process, like QualCoder's
            # own break-the-lock fallback (__main__.py:2652-2656)
            return "stale", "unknown"

    return ("active" if age <= QUALCODER_LOCK_TIMEOUT else "stale"), holder


# ----------------------------------------------------------------------------
# QC 4.0 GUI-open heuristics (P1-5)
#
# QualCoder 4.0 deleted the project_in_use.lock protocol entirely (no
# matches in the pinned tree at 9bddf17), so the lock gate above is blind
# against a 4.0 GUI. These heuristics give the concurrency ladder a
# best-effort WARN signal. They are HEURISTICS: wording must always say
# "appears", they never hard-refuse on their own, and the C7
# in-transaction text fingerprints remain the write-time backstop.
# Signals evaluated against the pinned source:
# - data.qda sidecars: 4.0 connects plainly (no WAL pragma), so a
#   data.qda-journal exists only during an active write or after a
#   crash; -wal/-shm would mean some tool switched the DB to WAL mode.
# - ai_data/search.sqlite-wal/-shm: the AI vector store opens
#   search.sqlite in WAL mode (ai_vectorstore.py:472-477), but every
#   connection is per-operation and closed in a finally block (the
#   open-time index check or build in _open_db, imports, deletes, chat
#   retrieval; ai_mcp_server.py opens per request as well), and SQLite
#   removes -wal/-shm on the last clean close. The sidecars therefore
#   mean RECENT ACTIVITY (vectorstore work in flight, which can run
#   for minutes on a large project) or an unclean exit, never that an
#   idle window has the project open. An idle 4.0 window with no
#   recent AI activity leaves no file trace at all and is visible only
#   to the process scan below (QA round 1, F2).
# - ai_data/chat_history.sqlite mtime: the chat panel keeps this store
#   open and writes on every message (ai_chat.py:1682-1718); a recent
#   mtime means recent AI chat activity on this project.
# - A best-effort local process scan for a running QualCoder
#   (platform-guarded, optional, cached; never a hard dependency and
#   never allowed to crash or block, including on Windows CI runners).
# ----------------------------------------------------------------------------

# Recency window for the mtime signals. In-flight vectorstore work keeps
# rewriting the WAL (age near zero) and every chat message commits to the
# chat store, so the window's only job is to separate "activity in the
# last little while" from the leftover of an earlier crash. Fifteen
# minutes keeps a user who used the AI a short while ago (and most
# plausibly still has the window open) on the warn side without treating
# last week's crash leftover as activity; nothing in the pinned source
# argues for a tighter or looser value (re-examined in QA round 1, F2).
GUI_SIGNAL_FRESH_SECONDS = 15 * 60
_PROCESS_SCAN_CACHE_SECONDS = 5.0
_process_scan_cache: Dict[str, Any] = {"at": 0.0, "lines": []}


def _mtime_age_seconds(path: Path) -> Optional[float]:
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def _filter_qualcoder_processes(lines) -> List[str]:
    """Pure filter: process lines that look like a running QualCoder.

    This server's own name contains 'qualcoder', so every spelling of
    the server/package name is blanked out of a line before matching;
    what remains must still say 'qualcoder' to count.
    """
    hits = []
    for line in lines:
        try:
            folded = str(line).lower()
        except Exception:
            continue
        for own in ("qualcoder_mcp", "qualcoder-mcp", "qualcoder mcp"):
            folded = folded.replace(own, "")
        if "qualcoder" in folded:
            hits.append(str(line).strip()[:200])
    return hits


def _qualcoder_process_hits() -> List[str]:
    """Best-effort scan for running QualCoder processes (cached).

    psutil when available, else one short-lived ps/tasklist call with a
    hard timeout. Any failure whatsoever returns no hits.
    """
    now = time.monotonic()
    if now - _process_scan_cache["at"] < _PROCESS_SCAN_CACHE_SECONDS:
        return _process_scan_cache["lines"]
    lines: List[str] = []
    try:
        try:
            import psutil  # optional, never a hard dependency
        except ImportError:
            psutil = None
        if psutil is not None:
            own_pid = os.getpid()
            for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                try:
                    if proc.info.get("pid") == own_pid:
                        continue
                    cmdline = " ".join(proc.info.get("cmdline") or [])
                    lines.append(f"{proc.info.get('name', '')} {cmdline}")
                except Exception:
                    continue
        else:
            import subprocess
            if os.name == "nt":
                cmd = ["tasklist", "/fo", "csv"]
            else:
                cmd = ["ps", "-axo", "args"]
            completed = subprocess.run(
                cmd, capture_output=True, timeout=3, check=False)
            lines = completed.stdout.decode(
                "utf-8", errors="replace").splitlines()
    except Exception:
        lines = []
    hits = _filter_qualcoder_processes(lines)
    _process_scan_cache["at"] = now
    _process_scan_cache["lines"] = hits
    return hits


def qualcoder_gui_signals(project_dir: Union[str, Path],
                          include_process_scan: bool = True) -> List[str]:
    """Best-effort signals that QualCoder may have this project open.

    Returns human-readable signal descriptions (empty when nothing
    suggests an open GUI). Heuristic by design: a signal means the
    project APPEARS to be open, never that it certainly is. The
    file-based signals are traces of recent activity (a hot write,
    recent AI indexing or chat); an idle window leaves none, so only
    the process scan can see one. This function never raises.
    """
    signals: List[str] = []
    try:
        project = Path(project_dir)

        # 1. Hot write on the project database itself
        for sidecar in ("data.qda-journal", "data.qda-wal", "data.qda-shm"):
            if (project / sidecar).exists():
                signals.append(
                    f"the project database has an active or interrupted "
                    f"write ({sidecar} present)")
                break

        # 2. Recent activity on the 4.0 AI search index (its WAL
        #    sidecars exist only while vectorstore work is in flight or
        #    after an unclean exit; never proof of an open window)
        ai_data = project / "ai_data"
        for sidecar in ("search.sqlite-wal", "search.sqlite-shm"):
            sc_path = ai_data / sidecar
            if sc_path.exists():
                age = _mtime_age_seconds(sc_path)
                if age is not None and age <= GUI_SIGNAL_FRESH_SECONDS:
                    signals.append(
                        "the QualCoder 4.0 AI search index shows recent "
                        f"activity ({sidecar} present and recently "
                        "written: AI indexing appears to be in flight, "
                        "or the last exit was unclean)")
                else:
                    signals.append(
                        "the QualCoder 4.0 AI search index has a leftover "
                        f"{sidecar} (an interrupted index build or an "
                        "unclean exit)")
                break

        # 3. Recent AI chat activity
        chat = ai_data / "chat_history.sqlite"
        age = _mtime_age_seconds(chat) if chat.exists() else None
        if age is not None and age <= GUI_SIGNAL_FRESH_SECONDS:
            signals.append(
                f"the QualCoder 4.0 AI chat history was modified "
                f"{int(age // 60)} minute(s) ago")

        # 4. A QualCoder process is running on this machine
        if include_process_scan:
            hits = _qualcoder_process_hits()
            if hits:
                signals.append(
                    f"a process that looks like QualCoder is running on "
                    f"this machine ({len(hits)} match(es))")
    except Exception as e:  # never let a heuristic break a tool
        logger.debug(f"GUI-open heuristics failed: {e}")
    return signals


@contextmanager
def hold_project_lock(project_dir: Union[str, Path]):
    """Hold QualCoder's project lock for the duration of an MCP write.

    Mirrors QualCoder's own protocol: refuse when the lock is active; when
    it is absent, create it (username + epoch, mode 'x') so a QualCoder
    launched mid-write politely refuses to open the project; delete it on
    exit. A stale foreign lock is left alone (QualCoder's next open shows
    its "not properly closed" prompt) and we proceed WITHOUT holding —
    callers must re-check the lock state immediately before committing.

    Yields:
        True when the lock is held by us, False when proceeding over a
        stale foreign lock.

    Raises:
        DatabaseLockedError: If QualCoder has the project open
    """
    project_dir = Path(project_dir)
    lock = project_dir / QUALCODER_LOCK_FILENAME

    state, holder = qualcoder_lock_state(project_dir)
    if state == "active":
        raise DatabaseLockedError(qualcoder_open_message(holder))

    held = False
    if state == "absent":
        try:
            with open(lock, "x", encoding="utf-8") as f:
                # Same two-line format QualCoder writes (__main__.py:2546-2547)
                f.write(f"{getpass.getuser()}\n{time.time()}")
            held = True
        except FileExistsError:
            # Race: someone created the lock between check and create
            state2, holder2 = qualcoder_lock_state(project_dir)
            if state2 == "active":
                raise DatabaseLockedError(qualcoder_open_message(holder2)) from None
            # stale — leave the file alone and proceed unheld
        except OSError as e:
            logger.warning(f"Could not create project lock file: {e}")

    try:
        yield held
    finally:
        if held:
            try:
                lock.unlink()
            except OSError as e:
                logger.warning(f"Could not remove project lock file: {e}")


def position_safe(fulltext: str) -> bool:
    """Check whether Qt (GUI) and code-point positions coincide for a text.

    QualCoder stores offsets from two coordinate systems into the same
    pos0/pos1 columns: code-point offsets (every programmatic path, all
    reports, this server) and Qt document offsets (manual GUI coding).
    They coincide iff the text contains no \r\n sequences and no astral
    code points (> U+FFFF, e.g. most emoji). On "unsafe" files,
    GUI-created rows drift and MCP-written rows may render shifted or
    unhighlighted in the QualCoder GUI (upstream-documented emoji bug;
    ground truth: text-positions.md §7).
    """
    return "\r\n" not in fulltext and all(ord(c) <= 0xFFFF for c in fulltext)


def _raise_query_error(e: sqlite3.Error, where: str, message: str) -> None:
    """Convert a sqlite3 error from a query into a typed, sanitized error.

    Locked databases get a distinct, actionable error; everything else is
    logged in full and re-raised as a generic sanitized RuntimeError.
    """
    if isinstance(e, sqlite3.OperationalError) and _is_locked_error(e):
        raise DatabaseLockedError(DB_LOCKED_MESSAGE) from None
    logger.error(f"Database error in {where}: {e}")
    raise RuntimeError(message) from None

# Workspace configuration
# Users should work in this folder to keep MCP-modified projects separate from originals
DEFAULT_WORKSPACE = Path.home() / "Documents" / "Qualcoder MCP Projects"


def _detect_file_type(mediapath: str) -> str:
    """Detect file type from QualCoder mediapath prefix convention.

    QualCoder uses path prefixes to indicate file type:
    - NULL/empty: text created in QualCoder
    - /docs/ or docs: : imported/linked text document
    - /images/ or images: : image file
    - /audio/ or audio: : audio file
    - /video/ or video: : video file
    """
    if not mediapath:
        return "text"
    if mediapath.startswith('/docs/') or mediapath.startswith('docs:'):
        if mediapath.lower().endswith('.pdf'):
            return "pdf"
        return "text"
    if mediapath.startswith('/images/') or mediapath.startswith('images:'):
        return "image"
    if mediapath.startswith('/audio/') or mediapath.startswith('audio:'):
        return "audio"
    if mediapath.startswith('/video/') or mediapath.startswith('video:'):
        return "video"
    return "media"


def validate_qda_path(db_path: str) -> Path:
    """Validate that the path is a legitimate Qualcoder project.

    Qualcoder projects can be either:
    - A .qda folder containing data.qda file (standard structure)
    - A direct path to data.qda file

    Args:
        db_path: Path to validate (can be project folder or data.qda file)

    Returns:
        Resolved Path object to the data.qda file

    Raises:
        ValueError: If path is invalid
        FileNotFoundError: If database file doesn't exist
    """
    try:
        # Resolve to absolute path, following symlinks
        path = Path(db_path).resolve(strict=False)
    except (OSError, RuntimeError) as e:
        raise ValueError(f"Invalid path: {e}")

    # Check if path exists
    if not path.exists():
        raise FileNotFoundError(f"Path not found: {path}")

    # Handle different path formats. QualCoder can ONLY open a directory
    # whose name ends in lowercase '.qda' and that contains a 'data.qda'
    # SQLite file (QualCoder 3.8.2 __main__.py:306, 2635) — accepting
    # anything else (bare .qda files, uppercase .QDA) produces "projects"
    # QualCoder can never open.
    if path.is_dir():
        # Path is a directory - look for data.qda inside
        if path.suffix == '.qda':
            # This is a .qda project folder
            data_file = path / "data.qda"
            if not data_file.exists():
                raise FileNotFoundError(f"No data.qda file found in project folder: {path}")
            if not data_file.is_file():
                raise ValueError(f"data.qda exists but is not a file: {data_file}")
            path = data_file
        elif path.suffix.lower() == '.qda':
            raise ValueError(
                f"Project folder must have a lowercase .qda extension "
                f"(QualCoder cannot open '{path.name}')"
            )
        else:
            raise ValueError(f"Directory must have .qda extension: {path}")
    elif path.is_file():
        # Path is a file - only the data.qda inside a .qda project folder
        # is a valid QualCoder database
        if path.name != "data.qda":
            raise ValueError(
                f"Invalid file: must be the data.qda inside a .qda project "
                f"folder, got {path.name}"
            )
        if path.parent.suffix != '.qda':
            raise ValueError(
                f"data.qda must live inside a project folder ending in "
                f"lowercase .qda (got '{path.parent.name}')"
            )
    else:
        raise ValueError(f"Path is neither a file nor a directory: {path}")

    # Basic SQLite validation (read-only check)
    conn = None
    try:
        conn = sqlite3.connect(_sqlite_ro_uri(path), uri=True)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 1")
        cursor.fetchone()
    except sqlite3.OperationalError as e:
        # A locked database is NOT corrupted: report it distinctly
        if _is_locked_error(e):
            raise DatabaseLockedError(DB_LOCKED_MESSAGE) from None
        raise DatabaseOpenError(f"Cannot open SQLite database: {e}")
    except sqlite3.DatabaseError as e:
        raise DatabaseOpenError(f"Invalid or corrupted SQLite database: {e}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    return path


def normalize_coder(coder: Optional[str]) -> Optional[str]:
    """Normalize an explicit coder filter: None or blank means no filter.

    QualCoder 4.0's own AI strips coder names and drops empty ones
    before choosing the base table over the visible view (ai_chat.py at
    pin 9bddf17), so a blank coder must read the visible view exactly
    like an absent one rather than filter the base tables by owner ''.
    A non-string value passes through for validate_string to reject
    (QA round 1, F10).
    """
    if coder is None:
        return None
    if isinstance(coder, str):
        stripped = coder.strip()
        return stripped if stripped else None
    return coder


def validate_limit(limit: int, max_limit: int = MAX_LIMIT) -> int:
    """Validate and cap limit parameter.

    Args:
        limit: Requested limit
        max_limit: Maximum allowed limit

    Returns:
        Validated limit value

    Raises:
        ValueError: If limit is invalid
    """
    if not isinstance(limit, int):
        raise TypeError(f"Limit must be an integer, got {type(limit).__name__}")

    if limit < 1:
        raise ValueError(f"Limit must be positive, got {limit}")

    if limit > max_limit:
        logger.warning(f"Limit {limit} exceeds maximum {max_limit}, capping to {max_limit}")
        return max_limit

    return limit


MAX_STRING_LENGTH = 10000  # Maximum allowed string length for user inputs
MAX_TEXT_CONTENT_LENGTH = 1_000_000  # Maximum text content for file import (approx 1MB)
# Journal titles: QualCoder restricts to letters/digits/underscore/space/hyphen
# (journals.py:607, ^[\ \w-]+$). QualCoder's validator is PCRE2 whose \w is
# ASCII-only, so re.ASCII keeps us from accepting names (e.g. accented or CJK
# letters) that the GUI would refuse (QA5-2).
JOURNAL_NAME_RE = re.compile(r"^[ \w-]+$", re.ASCII)


def _reject_if_too_long(value: str, param_name: str,
                        max_length: int = MAX_TEXT_CONTENT_LENGTH) -> None:
    """Reject an over-length write instead of silently truncating it.

    validate_string() truncates at MAX_STRING_LENGTH, which is fine for
    short identifiers but would silently corrupt a long memo/journal on
    round-trip (QualCoder imposes no length limit). Memo/journal WRITES use
    this to fail loudly instead (memos-journals.md §6.7).
    """
    if len(value) > max_length:
        raise ValueError(
            f"{param_name} is too long ({len(value)} characters; limit "
            f"{max_length}). Shorten it rather than have it silently truncated."
        )


def validate_string(value: str, param_name: str = "value",
                    max_length: int = MAX_STRING_LENGTH) -> str:
    """Validate a string parameter.

    Args:
        value: The string to validate
        param_name: Parameter name for error messages
        max_length: Maximum allowed length (default: MAX_STRING_LENGTH)

    Returns:
        The validated (and possibly truncated) string

    Raises:
        TypeError: If not a string
    """
    if not isinstance(value, str):
        raise TypeError(f"{param_name} must be a string, got {type(value).__name__}")

    if len(value) > max_length:
        logger.warning(
            f"{param_name} length {len(value)} exceeds maximum {max_length}, truncating"
        )
        return value[:max_length]

    return value


# Coder names appear in every coding row and in QualCoder's coder lists;
# 80 characters is generous for a display name and short enough to keep
# those lists readable (P1-2).
MAX_CODER_NAME_LENGTH = 80

# Bidirectional formatting characters (embeddings, overrides, isolates).
# Category Cf as a whole is NOT rejected: U+200C ZWNJ and U+200D ZWJ are
# legitimate in Persian/Indic orthography and emoji sequences (S-H2).
_BIDI_CONTROL_CHARS = frozenset(
    [chr(c) for c in range(0x202A, 0x202F)]      # LRE, RLE, PDF, LRO, RLO
    + [chr(c) for c in range(0x2066, 0x206A)])   # LRI, RLI, FSI, PDI


def _forbidden_coder_name_char(name: str) -> Optional[str]:
    """Name the first class of forbidden character in a coder name, or None."""
    for ch in name:
        cat = unicodedata.category(ch)
        if cat == "Cc":
            return "control characters (newlines, tabs or similar)"
        if cat in ("Zl", "Zp"):
            return "line or paragraph separator characters"
        if ch in _BIDI_CONTROL_CHARS:
            return "bidirectional formatting characters"
    return None


def validate_coder_name(value: Any, param_name: str = "owner") -> str:
    """The one rule set for every coder name this server writes (P1-2).

    Shared by the QUALCODER_MCP_AI_CODER_NAME configuration and the
    tool-supplied owner arguments (apply_codings, import_text_file), so
    no owner column can receive what the configured name may not be
    (S-H3): the name is stripped, must be non-empty, at most
    MAX_CODER_NAME_LENGTH characters, plain single-line text (no
    Unicode control characters, C1 included; no line or paragraph
    separators; no bidirectional formatting characters), and must not
    contain the '#####' memo-privacy marker, because coder names are
    written verbatim into merge provenance memos (S-M1). Ordinary names
    in any script, including ZWJ/ZWNJ sequences, are accepted.

    Returns:
        The stripped name.

    Raises:
        ValueError: With param_name in the message, on any violation.
    """
    if not isinstance(value, str):
        raise ValueError(f"{param_name} must be a string")
    name = value.strip()
    if not name:
        raise ValueError(f"{param_name} must be a non-empty coder name")
    if len(name) > MAX_CODER_NAME_LENGTH:
        raise ValueError(
            f"{param_name} is {len(name)} characters long; the maximum is "
            f"{MAX_CODER_NAME_LENGTH}. Coder names appear in every coding "
            f"row and in QualCoder's coder lists; keep them short.")
    forbidden = _forbidden_coder_name_char(name)
    if forbidden is not None:
        raise ValueError(
            f"{param_name} contains {forbidden}; a QualCoder coder name "
            f"must be plain single-line text.")
    if PERSONAL_NOTE_MARK in name:
        raise ValueError(
            f"{param_name} contains '{PERSONAL_NOTE_MARK}', which QualCoder "
            f"4.0 reserves as the private-memo marker. Coder names are "
            f"written into merge provenance notes, so the marker is refused.")
    return name


def validate_id(id_value: int, param_name: str = "id") -> int:
    """Validate an ID parameter.

    Args:
        id_value: The ID to validate
        param_name: Parameter name for error messages

    Returns:
        The validated ID

    Raises:
        TypeError: If not an integer
        ValueError: If negative
    """
    if not isinstance(id_value, int):
        raise TypeError(f"{param_name} must be an integer, got {type(id_value).__name__}")

    if id_value < 0:
        raise ValueError(f"{param_name} must be non-negative, got {id_value}")

    return id_value


# P1-4 backup parity (verdict d): project copies include ai_data/ whole
# (whole-tree copytree), because it holds NON-REGENERABLE user data (the
# prompt library ai_prompts/ + ai_prompts.yaml and the AI chat history,
# chat_history.sqlite), while excluding exactly QualCoder's own backup
# ignore set (save_backup, app.py:1619-1625 at pin 9bddf17): the
# regenerable vector-search DB search.sqlite and sqlite sidecar files.
# search.sqlite also duplicates full source plaintext
# (ai_vectorstore.py:479-552), so excluding it keeps backups from
# multiplying plaintext copies. We add *.lock on top: QualCoder's own
# 3.8.x backups exclude *.lock too (__main__.py:1371,1378) and a copied
# lock file triggers its "not properly closed" prompt.
QUALCODER_BACKUP_IGNORE_PATTERNS = (
    "search.sqlite",
    "search.sqlite-*",
    "*.sqlite-shm",
    "*.sqlite-wal",
    "*.sqlite-journal",
)
BACKUP_IGNORE_PATTERNS = ("*.lock",) + QUALCODER_BACKUP_IGNORE_PATTERNS


def backup_project(project_path: Union[str, Path]) -> Path:
    """Create a timestamped backup of a Qualcoder project.

    The whole project tree is copied, ai_data/ included, minus
    BACKUP_IGNORE_PATTERNS (QualCoder's own backup ignore set plus
    *.lock; see the constant above).

    Args:
        project_path: Path to the .qda project folder

    Returns:
        Path to the backup folder

    Raises:
        FileNotFoundError: If project doesn't exist
        OSError: If backup fails
    """
    project_path = Path(project_path)

    # If given path to data.qda, get the parent folder
    if project_path.name == "data.qda":
        project_path = project_path.parent

    if not project_path.exists():
        raise FileNotFoundError(f"Project not found: {project_path}")

    if not project_path.is_dir():
        raise ValueError(f"Project path must be a directory: {project_path}")

    # Create backup with timestamp; uniquify on collision so two writes in
    # the same second cannot abort each other (QA F2 / SEC D-3)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"{project_path.stem}_backup_{timestamp}.qda"
    backup_path = project_path.parent / backup_name
    counter = 2
    while backup_path.exists():
        backup_name = f"{project_path.stem}_backup_{timestamp}_{counter}.qda"
        backup_path = project_path.parent / backup_name
        counter += 1

    logger.info(f"Creating backup: {backup_path}")

    try:
        shutil.copytree(
            project_path, backup_path,
            ignore=shutil.ignore_patterns(*BACKUP_IGNORE_PATTERNS)
        )
        logger.info(f"Backup created successfully: {backup_path}")
        return backup_path
    except FileExistsError as e:
        # copytree's makedirs failed before anything was written: the
        # folder appeared under someone else's hand and is never ours to
        # remove (S-H5)
        logger.error(f"Failed to create backup: {e}")
        raise OSError(f"Backup failed: {e}") from None
    except Exception as e:
        logger.error(f"Failed to create backup: {e}")
        # Never leave a partial tree behind: list_backups would present
        # it as a restorable backup (S-H5)
        shutil.rmtree(backup_path, ignore_errors=True)
        raise OSError(f"Backup failed: {e}") from None


def copy_project_to_workspace(
    source_path: Union[str, Path],
    workspace: Optional[Union[str, Path]] = None,
    new_name: Optional[str] = None
) -> Path:
    """Copy a Qualcoder project to the MCP workspace for safe modification.

    P1-4: the copy carries the whole tree, ai_data/ included (prompt
    library and chat history are user data), minus
    BACKUP_IGNORE_PATTERNS, the same set project backups use: the
    regenerable search.sqlite (QualCoder rebuilds it on open), sqlite
    sidecar files that may be mid-write, and lock files (a copied
    project_in_use.lock would trigger QualCoder's "not properly
    closed" prompt on the copy).

    Args:
        source_path: Path to the source .qda project
        workspace: Workspace directory (defaults to DEFAULT_WORKSPACE)
        new_name: Optional new name for the project

    Returns:
        Path to the copied project in workspace

    Raises:
        FileNotFoundError: If source doesn't exist
        ValueError: If new_name is not a plain filename
        OSError: If copy fails
    """
    source_path = Path(source_path)

    # If given path to data.qda, get the parent folder
    if source_path.name == "data.qda":
        source_path = source_path.parent

    if not source_path.exists():
        raise FileNotFoundError(f"Source project not found: {source_path}")

    # Setup workspace
    if workspace is None:
        workspace = DEFAULT_WORKSPACE
    else:
        workspace = Path(workspace)

    workspace.mkdir(parents=True, exist_ok=True)

    # Determine destination name. new_name is untrusted (model-supplied):
    # it must be a plain filename, never a path that escapes the workspace
    # (SEC S-1 — separators/'..'/absolute/control chars all rejected, the
    # same confinement every other file-writing tool in this server uses).
    if new_name:
        if not isinstance(new_name, str) or not new_name.strip():
            raise ValueError("new_name must be a non-empty string")
        candidate = unicodedata.normalize("NFC", new_name.strip())
        if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in candidate):
            raise ValueError("new_name must not contain control characters")
        if ('/' in candidate or '\\' in candidate or '..' in candidate
                or candidate != Path(candidate).name):
            raise ValueError(
                "new_name must be a plain filename without path separators "
                "or '..'"
            )
        dest_name = candidate if candidate.endswith('.qda') else f"{candidate}.qda"
    else:
        dest_name = source_path.name

    dest_path = workspace / dest_name

    # Check if destination already exists
    if dest_path.exists():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest_name = f"{dest_path.stem}_{timestamp}.qda"
        dest_path = workspace / dest_name

    # Defense in depth: the resolved destination MUST stay inside the
    # workspace, whatever new_name was
    workspace_resolved = workspace.resolve()
    dest_resolved = dest_path.resolve()
    if workspace_resolved != dest_resolved.parent and \
            workspace_resolved not in dest_resolved.parents:
        raise ValueError("refusing to copy the project outside the workspace")

    logger.info(f"Copying project to workspace: {dest_path}")

    try:
        shutil.copytree(
            source_path, dest_path,
            ignore=shutil.ignore_patterns(*BACKUP_IGNORE_PATTERNS)
        )
        logger.info(f"Project copied successfully: {dest_path}")
        return dest_path
    except FileExistsError as e:
        # The destination appeared under someone else's hand between the
        # existence check and the copy; never touch it (S-H5)
        logger.error(f"Failed to copy project: {e}")
        raise OSError(f"Copy failed: {e}") from None
    except Exception as e:
        logger.error(f"Failed to copy project: {e}")
        # Never leave a partial project copy behind (S-H5)
        shutil.rmtree(dest_path, ignore_errors=True)
        raise OSError(f"Copy failed: {e}") from None


def _append_provenance_block(target_memo: Any, block: str) -> str:
    """Place a merge provenance block into a target memo (P1-1 recipe).

    Upstream's GUI merges compute (target_memo + block).strip()
    (code_tree.py:1413 for categories, :1521-1538 for codes at pin
    9bddf17). This server applies the same recipe to the target's PUBLIC
    zone only: with no '#####' suffix on the target the result is
    byte-identical to upstream; with one, the block lands before the
    marker and the private suffix survives verbatim at the end, re-joined
    by the whitespace run that separated it. The block itself may carry
    the source memo whole, marker included: anything after a marker the
    SOURCE brought along stays AI-hidden in the target, exactly where
    upstream puts it. Shared by merge_codes and merge_category so the
    two can never drift (S-M2).
    """
    target_public, target_private = split_public_private_memo(
        target_memo or "")
    if target_private == "":
        return (target_public + block).strip()
    trimmed = target_public.rstrip(" \t\r\n")
    separator = target_public[len(trimmed):]
    return (target_public + block).strip() + separator + target_private


def escape_like_pattern(pattern: str) -> str:
    """Escape SQLite LIKE wildcards in user input.

    Args:
        pattern: User input string

    Returns:
        Escaped pattern safe for LIKE queries
    """
    if not isinstance(pattern, str):
        raise TypeError(f"Pattern must be a string, got {type(pattern).__name__}")

    # Escape backslash first, then wildcards
    pattern = pattern.replace('\\', '\\\\')
    pattern = pattern.replace('%', '\\%')
    pattern = pattern.replace('_', '\\_')
    return pattern


class QualcoderDatabase:
    """Interface to read data from a Qualcoder SQLite database."""

    def __init__(self, db_path: str, read_only: bool = True):
        """Initialize connection to Qualcoder database.

        Args:
            db_path: Path to the .qda database file or project folder
            read_only: Open in read-only mode (default: True).
                      Write access requires explicit read_only=False.

        Raises:
            ValueError: If path validation fails
            FileNotFoundError: If database file doesn't exist
        """
        # Validate path before opening
        self.db_path = validate_qda_path(db_path)
        self.read_only = read_only

        try:
            if read_only:
                # Open in read-only mode via URI to prevent accidental writes
                self.conn = sqlite3.connect(_sqlite_ro_uri(self.db_path), uri=True)
            else:
                self.conn = sqlite3.connect(str(self.db_path), uri=False)
            self.conn.row_factory = sqlite3.Row  # Access columns by name
            # Enable foreign key constraints
            self.conn.execute("PRAGMA foreign_keys = ON")
            # Set busy timeout for concurrent access (5 seconds)
            self.conn.execute("PRAGMA busy_timeout = 5000")
        except sqlite3.OperationalError as e:
            if _is_locked_error(e):
                raise DatabaseLockedError(DB_LOCKED_MESSAGE) from None
            raise RuntimeError(f"Failed to open database: {e}") from e
        except sqlite3.Error as e:
            raise RuntimeError(f"Failed to open database: {e}") from e

        # Validate this is a Qualcoder database
        self._validate_schema()

        # Check database version
        self._check_version()

        # Probe schema capabilities (column/table existence: the real gate)
        self._probe_capabilities()

        # Gate on required columns (older QualCoder schemas lack them)
        self._check_required_columns()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
        return False

    def close(self):
        """Explicitly close the database connection."""
        if hasattr(self, 'conn') and self.conn:
            try:
                self.conn.close()
            except Exception as e:
                logger.warning(f"Error closing database connection: {e}")
            finally:
                self.conn = None

    def __del__(self):
        """Close database connection on cleanup."""
        self.close()

    def _validate_schema(self):
        """Verify this is a Qualcoder database with required tables."""
        required_tables = ['project', 'code_name', 'code_text', 'source', 'cases']
        try:
            cursor = self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            existing_tables = {row[0] for row in cursor.fetchall()}

            missing_tables = set(required_tables) - existing_tables
            if missing_tables:
                raise ValueError(
                    f"Invalid Qualcoder database: missing tables {missing_tables}"
                )
        except sqlite3.OperationalError as e:
            if _is_locked_error(e):
                raise DatabaseLockedError(DB_LOCKED_MESSAGE) from None
            raise RuntimeError(f"Failed to validate database schema: {e}") from e
        except sqlite3.Error as e:
            raise RuntimeError(f"Failed to validate database schema: {e}") from e

    def _check_version(self):
        """Check database version and log warnings if unsupported.

        Also records whether project.about identifies the database as a
        QualCoder project — QualCoder's own open check requires the
        substring "QualCoder" in about and refuses otherwise with "This is
        not a QualCoder database" (__main__.py:2698-2709, COMPAT V3).
        """
        self.db_version = None
        self.qualcoder_about_ok = True
        try:
            cursor = self.conn.execute("SELECT databaseversion, about FROM project")
            row = cursor.fetchone()
            if row:
                version = row[0]
                self.db_version = version
                self.qualcoder_about_ok = "QualCoder" in (row[1] or "")
                if version not in SUPPORTED_DB_VERSIONS:
                    logger.warning(
                        f"Untested database version: {version}. "
                        f"Supported versions: {SUPPORTED_DB_VERSIONS}"
                    )
                else:
                    logger.info(f"Connected to Qualcoder database version {version}")
        except sqlite3.OperationalError as e:
            if _is_locked_error(e):
                raise DatabaseLockedError(DB_LOCKED_MESSAGE) from None
            logger.warning(f"Could not determine database version: {e}")
        except sqlite3.Error as e:
            logger.warning(f"Could not determine database version: {e}")

    def _probe_capabilities(self):
        """Populate self.capabilities from column/table existence.

        One sqlite_master scan plus PRAGMA table_info on the probed tables.
        Additive migrations make existence a stable discriminator; the
        probes mirror upstream's own detection (e.g. master probes
        sub-code support by selecting supercid, __main__.py:2326-2329).
        """
        self.capabilities = SchemaCapabilities()
        try:
            tables = {row[0] for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            ).fetchall()}

            def cols(table):
                if table not in tables:
                    return set()
                return {r[1] for r in self.conn.execute(
                    f"PRAGMA table_info({table})").fetchall()}

            project_cols = cols("project")
            code_cols = cols("code_name")
            line_cols = cols("gr_cdct_line_item")
            coder_cols = cols("coder_names")
            self.capabilities = SchemaCapabilities(
                has_coder_names="coder_names" in tables,
                has_av_bookmarks="avbookmarkfile" in project_cols,
                has_avbookmarktextpos="avbookmarktextpos" in project_cols,
                has_supercid="supercid" in code_cols,
                has_graph_labels="label" in line_cols,
                has_gr_memo_item="gr_memo_item" in tables,
                # Column AND view, never a version string (P1-3). The
                # sqlite_master scan above includes views.
                has_coder_visibility=("visibility" in coder_cols
                                      and "code_text_visible" in tables),
                tables=tables,
            )
        except sqlite3.OperationalError as e:
            if _is_locked_error(e):
                raise DatabaseLockedError(DB_LOCKED_MESSAGE) from None
            logger.warning(f"Could not probe schema capabilities: {e}")
        except sqlite3.Error as e:
            logger.warning(f"Could not probe schema capabilities: {e}")

    def _unknown_future_schema(self) -> bool:
        """True when databaseversion names a schema newer than the verified
        ceiling, or does not parse as v{N} at all. The probes prove the
        columns we know about; they cannot prove the absence of a future
        semantic change (v16 changed the MEANING of catid rows without
        touching a column we write), so the unknown is refused, not
        allowlisted."""
        match = _VERSION_STRING_RE.match(self.db_version or "")
        if not match:
            return True
        return int(match.group(1)) > MAX_VERIFIED_SCHEMA

    def write_support(self):
        """(supported, reason, overridden) for writing to this database.

        Capability-probe gate (S1): v14 floor = the coder_names table
        exists (upstream's own v14 trigger); anything at or above the
        floor is writable through the verified ceiling. Versions beyond
        the ceiling (or unparseable version strings) are refused unless
        the QUALCODER_MCP_ALLOW_UNKNOWN_SCHEMA=1 environment override is
        set, in which case writes proceed but carry a prominent warning.
        """
        caps = getattr(self, "capabilities", None)
        if caps is None or not caps.has_coder_names:
            return (False,
                    "This project uses a pre-v14 schema; open it once in "
                    "QualCoder 3.8 or newer to migrate it, then retry.",
                    False)
        if self._unknown_future_schema():
            version = self.db_version or "unknown"
            if os.environ.get(ALLOW_UNKNOWN_SCHEMA_ENV, "") == "1":
                return (True, self._unknown_schema_warning(), True)
            return (False,
                    f"This project reports database schema '{version}', "
                    f"newer than the schemas this server is verified "
                    f"against (v14 through v{MAX_VERIFIED_SCHEMA}, "
                    f"QualCoder master commit {VERIFIED_MASTER_COMMIT}). "
                    f"Writes are refused to protect the data. Set "
                    f"{ALLOW_UNKNOWN_SCHEMA_ENV}=1 in the server "
                    f"environment to override at your own risk.",
                    False)
        return (True, None, False)

    def _unknown_schema_warning(self) -> str:
        return (f"WARNING: this project reports database schema "
                f"'{self.db_version or 'unknown'}', newer than the verified "
                f"ceiling (v{MAX_VERIFIED_SCHEMA}, QualCoder master commit "
                f"{VERIFIED_MASTER_COMMIT}); writes proceeded only because "
                f"{ALLOW_UNKNOWN_SCHEMA_ENV}=1 is set. Verify results in "
                f"QualCoder and keep backups.")

    def schema_write_warning(self):
        """The override warning when writing to an unknown-future schema,
        else None. Every write result must carry it (plan section 1.2)."""
        supported, reason, overridden = self.write_support()
        if supported and overridden:
            return reason
        return None

    def _check_required_columns(self):
        """Ensure the schema has the columns this server reads and writes.

        Older QualCoder schemas (pre-3.8 / pre-v14) lack columns such as
        code_text.important, which every coding read and write here uses.
        Rather than crashing mid-operation (or half-working), refuse the
        connection with instructions to upgrade the project in QualCoder.

        Raises:
            UnsupportedSchemaError: If any required column is missing
        """
        try:
            for table, columns in REQUIRED_COLUMNS.items():
                cursor = self.conn.execute(f"PRAGMA table_info({table})")
                existing = {row[1] for row in cursor.fetchall()}
                missing = [c for c in columns if c not in existing]
                if missing:
                    version = self.db_version or "unknown"
                    raise UnsupportedSchemaError(
                        f"This project was created with an older QualCoder "
                        f"(database schema {version}; missing column(s) "
                        f"{', '.join(table + '.' + c for c in missing)}). "
                        f"Open and save the project in QualCoder 3.8 to "
                        f"upgrade it, then try again."
                    )
        except sqlite3.OperationalError as e:
            if _is_locked_error(e):
                raise DatabaseLockedError(DB_LOCKED_MESSAGE) from None
            raise RuntimeError(f"Failed to check database schema: {e}") from e
        except sqlite3.Error as e:
            raise RuntimeError(f"Failed to check database schema: {e}") from e

    def get_project_info(self) -> Dict[str, Any]:
        """Get project metadata."""
        cursor = self.conn.execute(
            "SELECT databaseversion, date, memo, about, codername FROM project"
        )
        row = cursor.fetchone()
        if row:
            return {
                "database_version": row["databaseversion"],
                "date": row["date"],
                "memo": row["memo"],
                "about": row["about"],
                "coder_name": row["codername"]
            }
        return {}

    # ------------------------------------------------------------------
    # C7: data-precondition re-check for text-anchored writes.
    # The lock gate detects released QualCoder (3.x) only; QualCoder 4.0
    # development builds removed the lock protocol entirely, so a
    # concurrent editor there is undetectable. Verifying that the
    # fulltext still matches what positions were validated against,
    # INSIDE the write transaction (after the write statements have
    # taken SQLite's reserved lock, so no other writer can slip in
    # afterwards), converts the undetectable-writer race into a
    # detectable data-precondition failure for the one write class that
    # can actually corrupt (the snapshot-rewrite editor,
    # edit_textfile.py:596). Probes data, not versions: works against
    # any QualCoder.
    # ------------------------------------------------------------------

    @staticmethod
    def fingerprint_of_text(text: str):
        """(length, sha256) of a fulltext, as captured at validation."""
        return (len(text),
                hashlib.sha256(text.encode("utf-8")).hexdigest())

    def fulltext_fingerprint(self, file_id: int):
        """Current (length, sha256) of a file's fulltext, or None."""
        row = self.conn.execute(
            "SELECT fulltext FROM source WHERE id = ?", (file_id,)
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return self.fingerprint_of_text(row[0])

    def verify_fulltext_unchanged(self, file_id: int, fingerprint) -> None:
        """Raise if the file's fulltext no longer matches the captured
        fingerprint (C7). Call inside the write transaction, after the
        write statements, before commit."""
        current = self.fulltext_fingerprint(file_id)
        if current != fingerprint:
            raise ValueError(
                f"The text of file id {file_id} changed while this write "
                f"was being prepared (another program, possibly an open "
                f"QualCoder window, edited it). Nothing was written: "
                f"re-read the file and retry. Note that QualCoder 4.0 "
                f"builds write no lock file, so the lock gate cannot see "
                f"them; check qualcoder_gui_signals in "
                f"get_current_project and confirm with the user.")

    def _hierarchy_maps(self):
        """(codes, cats) lookup maps for path/chain building.

        codes: cid -> {name, catid, supercid} (supercid None when the
        column is absent, i.e. v14/v15); cats: catid -> {name, supercatid}.
        """
        caps = getattr(self, "capabilities", None)
        has_supercid = caps is not None and caps.has_supercid
        if has_supercid:
            code_rows = self.conn.execute(
                "SELECT cid, name, catid, supercid FROM code_name").fetchall()
        else:
            code_rows = self.conn.execute(
                "SELECT cid, name, catid FROM code_name").fetchall()
        codes = {}
        for row in code_rows:
            codes[row["cid"]] = {
                "name": row["name"],
                "catid": row["catid"],
                "supercid": row["supercid"] if has_supercid else None,
            }
        cats = {}
        for row in self.conn.execute(
                "SELECT catid, name, supercatid FROM code_cat").fetchall():
            cats[row["catid"]] = {"name": row["name"],
                                  "supercatid": row["supercatid"]}
        return codes, cats

    def _top_ancestor_cid(self, cid, codes) -> int:
        """Walk supercid to the top ancestor CODE (cycle-safe)."""
        seen = set()
        current = cid
        while current in codes and current not in seen:
            seen.add(current)
            parent = codes[current]["supercid"]
            if parent is None or parent not in codes:
                return current
            current = parent
        return current

    def effective_category(self, cid, maps=None):
        """(catid, name) of the code's TOP ANCESTOR code's category — how
        master attributes sub-code counts (reports.py:287-313). (None,
        None) for uncategorised chains; identical to the direct category
        on v14/v15 (no supercid)."""
        codes, cats = maps if maps is not None else self._hierarchy_maps()
        top = self._top_ancestor_cid(cid, codes)
        catid = codes.get(top, {}).get("catid")
        if catid is None or catid not in cats:
            return None, None
        return catid, cats[catid]["name"]

    def code_path(self, cid, maps=None) -> str:
        """Rendered location like master's memo.py:255-274:
        "Category > Sub-category > Parent code > Code"."""
        codes, cats = maps if maps is not None else self._hierarchy_maps()
        code_names = []
        seen = set()
        current = cid
        while current in codes and current not in seen:
            seen.add(current)
            code_names.append(codes[current]["name"])
            parent = codes[current]["supercid"]
            if parent is None:
                break
            current = parent
        top = current
        cat_names = []
        catid = codes.get(top, {}).get("catid")
        cseen = set()
        while catid in cats and catid not in cseen:
            cseen.add(catid)
            cat_names.append(cats[catid]["name"])
            catid = cats[catid]["supercatid"]
        return " > ".join(list(reversed(cat_names))
                          + list(reversed(code_names)))

    # ------------------------------------------------------------------
    # P1-3: coder-visibility reads (QC 4.0). When the project carries
    # 4.0's per-coder visibility state (probe: coder_names.visibility
    # column plus the code_text_visible view, created in the project DB
    # at app.py:1499-1562), coded-segment reads and analytics go through
    # the *_visible views by default so this server reads what the user
    # sees in QualCoder. An explicit coder filter reads the base tables
    # instead (upstream does the same, ai_chat.py:3922); file exports
    # pass honor_visibility=False for QualCoder export/report parity
    # (upstream's reports and refi.py read base tables). Pre-4.0
    # projects lack the objects and always read base tables; the view is
    # never hard-required (their server errors on a missing view,
    # ai_mcp_server.py:5281-5283; we degrade gracefully by doctrine).
    # ------------------------------------------------------------------

    def _visible_source(self, base: str, view: str,
                        honor_visibility: bool = True) -> str:
        """The table or view a read should select from."""
        caps = getattr(self, "capabilities", None)
        if (honor_visibility and caps is not None
                and caps.has_coder_visibility and caps.table_exists(view)):
            return view
        return base

    def code_text_source(self, honor_visibility: bool = True) -> str:
        return self._visible_source("code_text", "code_text_visible",
                                    honor_visibility)

    def hidden_coder_count(self) -> int:
        """How many coders this project currently hides (0 without the
        visibility capability). Used for result disclosure; hidden
        coders' NAMES are never disclosed."""
        caps = getattr(self, "capabilities", None)
        if caps is None or not caps.has_coder_visibility:
            return 0
        try:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM coder_names WHERE visibility = 0"
            ).fetchone()
            return int(row[0]) if row else 0
        except sqlite3.Error:
            return 0

    def _row_is_visible(self, base: str, view: str, id_col: str,
                        row_id: int) -> bool:
        """Whether a row the AI targets by id is one the user sees.

        True on projects without the visibility capability (nothing is
        hidden there) or when the view is absent; otherwise the row must
        appear in the *_visible view. Table, view and column names come
        from fixed internal constants, never from input (S-MAJ).
        """
        source = self._visible_source(base, view)
        if source == base:
            return True
        try:
            row = self.conn.execute(
                f"SELECT 1 FROM {source} WHERE {id_col} = ?", (row_id,)
            ).fetchone()
        except sqlite3.Error:
            return True
        return row is not None

    def coding_is_visible(self, coding_id: int) -> bool:
        """True unless a 4.0 visibility setting hides this coding's coder."""
        return self._row_is_visible("code_text", "code_text_visible",
                                    "ctid", coding_id)

    def annotation_is_visible(self, annotation_id: int) -> bool:
        """True unless a 4.0 visibility setting hides this annotation's coder."""
        return self._row_is_visible("annotation", "annotation_visible",
                                    "anid", annotation_id)

    @staticmethod
    def _validate_coder(coder: Optional[str]) -> Optional[str]:
        """Validate an explicit coder filter argument.

        None and blank values mean "no filter" and read the visible
        view (upstream parity, see normalize_coder; QA round 1, F10).
        """
        coder = normalize_coder(coder)
        if coder is None:
            return None
        return validate_string(coder, "coder")

    def list_codes(self) -> List[Dict[str, Any]]:
        """Get all codes with their categories.

        Returns:
            List of codes with id, name, memo, category, color, owner, date
        """
        cursor = self.conn.execute("""
            SELECT
                c.cid,
                c.name,
                c.memo,
                c.color,
                c.owner,
                c.date,
                cat.name as category_name,
                cat.catid as category_id
            FROM code_name c
            LEFT JOIN code_cat cat ON c.catid = cat.catid
            ORDER BY cat.name, c.name
        """)

        caps = getattr(self, "capabilities", None)
        has_supercid = caps is not None and caps.has_supercid
        maps = self._hierarchy_maps() if has_supercid else None
        code_map = maps[0] if maps else {}

        codes = []
        for row in cursor.fetchall():
            entry = {
                "id": row["cid"],
                "name": row["name"],
                "memo": row["memo"] or "",
                "color": row["color"],
                "owner": row["owner"],
                "date": row["date"],
                "category": row["category_name"],
                "category_id": row["category_id"]
            }
            if has_supercid:
                # S5: a sub-code is never presented as merely
                # "category: null" — its real location is exposed
                supercid = code_map.get(row["cid"], {}).get("supercid")
                entry["parent_code_id"] = supercid
                entry["parent_code_name"] = (
                    code_map.get(supercid, {}).get("name")
                    if supercid is not None else None)
                entry["path"] = self.code_path(row["cid"], maps)
            codes.append(entry)
        return codes

    def list_categories(self) -> List[Dict[str, Any]]:
        """Get all code categories with hierarchy.

        Returns:
            List of categories with id, name, memo, parent info
        """
        cursor = self.conn.execute("""
            SELECT
                catid,
                name,
                memo,
                owner,
                date,
                supercatid
            FROM code_cat
            ORDER BY name
        """)

        categories = []
        for row in cursor.fetchall():
            categories.append({
                "id": row["catid"],
                "name": row["name"],
                "memo": row["memo"] or "",
                "owner": row["owner"],
                "date": row["date"],
                "parent_id": row["supercatid"]
            })
        return categories

    def get_code_details(self, code_id: int) -> Optional[Dict[str, Any]]:
        """Get detailed information about a specific code.

        Args:
            code_id: The code ID (cid)

        Returns:
            Code details including statistics, or None if not found

        Raises:
            TypeError: If code_id is not an integer
            ValueError: If code_id is negative
            RuntimeError: If database operation fails
        """
        code_id = validate_id(code_id, "code_id")

        try:
            cursor = self.conn.execute("""
                SELECT
                    c.cid,
                    c.name,
                    c.memo,
                    c.color,
                    c.owner,
                    c.date,
                    cat.name as category_name
                FROM code_name c
                LEFT JOIN code_cat cat ON c.catid = cat.catid
                WHERE c.cid = ?
            """, (code_id,))

            row = cursor.fetchone()
            if not row:
                return None

            # Count coded segments — joined to source so orphaned codings
            # (deleted files) are excluded, consistent with
            # get_coded_text_segments (QA F8). P1-3: counts go through the
            # visibility views when the project has them, so they agree
            # with what the listing tools return.
            ct_source = self.code_text_source()
            ci_source = self._visible_source("code_image",
                                             "code_image_visible")
            ca_source = self._visible_source("code_av", "code_av_visible")
            text_count = self.conn.execute(
                f"SELECT COUNT(*) as cnt FROM {ct_source} ct "
                f"JOIN source s ON ct.fid = s.id WHERE ct.cid = ?",
                (code_id,)
            ).fetchone()["cnt"]

            image_count = self.conn.execute(
                f"SELECT COUNT(*) as cnt FROM {ci_source} ci "
                f"JOIN source s ON ci.id = s.id WHERE ci.cid = ?",
                (code_id,)
            ).fetchone()["cnt"]

            av_count = self.conn.execute(
                f"SELECT COUNT(*) as cnt FROM {ca_source} ca "
                f"JOIN source s ON ca.id = s.id WHERE ca.cid = ?",
                (code_id,)
            ).fetchone()["cnt"]

            details = {
                "id": row["cid"],
                "name": row["name"],
                "memo": row["memo"] or "",
                "color": row["color"],
                "owner": row["owner"],
                "date": row["date"],
                "category": row["category_name"],
                "statistics": {
                    "text_segments": text_count,
                    "image_segments": image_count,
                    "av_segments": av_count,
                    "total": text_count + image_count + av_count
                }
            }
            caps = getattr(self, "capabilities", None)
            if caps is not None and caps.has_supercid:
                maps = self._hierarchy_maps()
                supercid = maps[0].get(code_id, {}).get("supercid")
                details["parent_code_id"] = supercid
                details["parent_code_name"] = (
                    maps[0].get(supercid, {}).get("name")
                    if supercid is not None else None)
                details["path"] = self.code_path(code_id, maps)
            return details
        except sqlite3.Error as e:
            _raise_query_error(e, "get_code_details", "Failed to retrieve code details")

    def get_coded_text_segments(self, code_id: int, limit: int = 100,
                                coder: Optional[str] = None,
                                honor_visibility: bool = True
                                ) -> List[Dict[str, Any]]:
        """Get text segments coded with a specific code.

        Args:
            code_id: The code ID (cid)
            limit: Maximum number of segments to return (max 5000)
            coder: Explicit coder filter; reads the BASE table filtered
                   to this owner (P1-3 override, as upstream)
            honor_visibility: Read through code_text_visible when the
                   project has QC 4.0 coder visibility (default True;
                   file exports pass False for export parity)

        Returns:
            List of coded text segments with context

        Raises:
            TypeError: If parameters are wrong type
            ValueError: If parameters are invalid
            RuntimeError: If database operation fails
        """
        code_id = validate_id(code_id, "code_id")
        limit = validate_limit(limit)
        coder = self._validate_coder(coder)
        source = self.code_text_source(honor_visibility and coder is None)
        owner_sql = " AND ct.owner = ?" if coder is not None else ""
        params = ((code_id, coder, limit) if coder is not None
                  else (code_id, limit))

        try:
            cursor = self.conn.execute(f"""
                SELECT
                    ct.ctid,
                    ct.seltext,
                    ct.pos0,
                    ct.pos1,
                    ct.memo,
                    ct.owner,
                    ct.date,
                    ct.important,
                    s.name as file_name,
                    s.id as file_id
                FROM {source} ct
                JOIN source s ON ct.fid = s.id
                WHERE ct.cid = ?{owner_sql}
                ORDER BY s.name, ct.pos0
                LIMIT ?
            """, params)

            segments = []
            for row in cursor.fetchall():
                segments.append({
                    "id": row["ctid"],
                    "text": row["seltext"],
                    "position_start": row["pos0"],
                    "position_end": row["pos1"],
                    "memo": row["memo"] or "",
                    "owner": row["owner"],
                    "date": row["date"],
                    "important": bool(row["important"]),
                    "file_name": row["file_name"],
                    "file_id": row["file_id"]
                })
            return segments
        except sqlite3.Error as e:
            _raise_query_error(e, "get_coded_text_segments", "Failed to retrieve coded text segments")

    def count_codings_for_code(self, code_id: int,
                               honor_visibility: bool = True) -> int:
        """Count a code's text codings (visible view or base table),
        joined to source like the listing so counts agree with it."""
        code_id = validate_id(code_id, "code_id")
        source = self.code_text_source(honor_visibility)
        try:
            row = self.conn.execute(
                f"SELECT COUNT(*) FROM {source} ct "
                f"JOIN source s ON ct.fid = s.id WHERE ct.cid = ?",
                (code_id,)
            ).fetchone()
            return int(row[0]) if row else 0
        except sqlite3.Error as e:
            _raise_query_error(e, "count_codings_for_code",
                               "Failed to count codings")

    def list_files(self) -> List[Dict[str, Any]]:
        """Get all source files in the project.

        Returns:
            List of files with metadata
        """
        cursor = self.conn.execute("""
            SELECT id, name, memo, owner, date, mediapath
            FROM source
            ORDER BY name
        """)

        files = []
        for row in cursor.fetchall():
            files.append({
                "id": row["id"],
                "name": row["name"],
                "memo": row["memo"] or "",
                "owner": row["owner"],
                "date": row["date"],
                "type": _detect_file_type(row["mediapath"]),
                "media_path": row["mediapath"]
            })
        return files

    def get_file_content(self, file_id: int) -> Optional[Dict[str, Any]]:
        """Get the content of a text file.

        Args:
            file_id: The file ID

        Returns:
            File content and metadata

        Raises:
            TypeError: If file_id is not an integer
            ValueError: If file_id is negative
            RuntimeError: If database operation fails
        """
        file_id = validate_id(file_id, "file_id")

        try:
            cursor = self.conn.execute("""
                SELECT
                    id,
                    name,
                    fulltext,
                    memo,
                    owner,
                    date,
                    mediapath
                FROM source
                WHERE id = ?
            """, (file_id,))

            row = cursor.fetchone()
            if not row:
                return None

            # Count codes in this file (P1-3: through code_text_visible
            # when present, so the files resource agrees with the
            # visibility-honoring reads; QA round 1, F11)
            code_count = self.conn.execute(
                f"SELECT COUNT(DISTINCT cid) as cnt "
                f"FROM {self.code_text_source()} WHERE fid = ?",
                (file_id,)
            ).fetchone()["cnt"]

            return {
                "id": row["id"],
                "name": row["name"],
                "content": row["fulltext"] or "",
                "memo": row["memo"] or "",
                "owner": row["owner"],
                "date": row["date"],
                "media_path": row["mediapath"],
                "is_text": _detect_file_type(row["mediapath"]) in ("text", "pdf"),
                # False when the text contains \r\n or astral characters:
                # QualCoder's GUI positions diverge on such files (QA2-4)
                "position_safe": position_safe(row["fulltext"] or ""),
                "code_count": code_count
            }
        except sqlite3.Error as e:
            _raise_query_error(e, "get_file_content", "Failed to retrieve file content")

    def get_file_with_coding(self, file_id: int) -> Optional[Dict[str, Any]]:
        """Get a file with all its coded segments for rich context analysis.

        This method retrieves the full text along with all coding information,
        allowing AI analysis that considers both coded segments and full context.

        Args:
            file_id: The file ID

        Returns:
            Dictionary with:
            - file_info: Basic file metadata
            - full_text: Complete file text
            - coded_segments: All coded segments with positions, codes, memos
            - codes_used: Summary of codes applied to this file
            - annotations: Any annotations on the file

        Raises:
            TypeError: If file_id is not an integer
            ValueError: If file_id is negative
            RuntimeError: If database operation fails
        """
        file_id = validate_id(file_id, "file_id")

        try:
            # Get file info
            file_cursor = self.conn.execute("""
                SELECT
                    id,
                    name,
                    fulltext,
                    mediapath,
                    memo,
                    owner,
                    date
                FROM source
                WHERE id = ?
            """, (file_id,))

            file_row = file_cursor.fetchone()
            if not file_row:
                return None

            file_type = _detect_file_type(file_row["mediapath"])
            is_text = file_type in ("text", "pdf")

            # Get all coded segments for this file (P1-3: through the
            # visibility view when the project has one, so this analysis
            # view matches what the user sees in QualCoder)
            segments_cursor = self.conn.execute(f"""
                SELECT
                    ct.ctid,
                    ct.pos0,
                    ct.pos1,
                    ct.seltext,
                    ct.memo as segment_memo,
                    ct.owner,
                    ct.date,
                    ct.important,
                    c.cid,
                    c.name as code_name,
                    c.color as code_color,
                    cat.name as category_name
                FROM {self.code_text_source()} ct
                JOIN code_name c ON ct.cid = c.cid
                LEFT JOIN code_cat cat ON c.catid = cat.catid
                WHERE ct.fid = ?
                ORDER BY ct.pos0
            """, (file_id,))

            coded_segments = []
            codes_used = {}

            for seg_row in segments_cursor.fetchall():
                segment = {
                    "segment_id": seg_row["ctid"],
                    "position_start": seg_row["pos0"],
                    "position_end": seg_row["pos1"],
                    "text": seg_row["seltext"],
                    "memo": seg_row["segment_memo"] or "",
                    "owner": seg_row["owner"],
                    "date": seg_row["date"],
                    "important": bool(seg_row["important"]),
                    "code": {
                        "id": seg_row["cid"],
                        "name": seg_row["code_name"],
                        "color": seg_row["code_color"],
                        "category": seg_row["category_name"]
                    }
                }
                coded_segments.append(segment)

                # Track codes used
                code_name = seg_row["code_name"]
                if code_name not in codes_used:
                    codes_used[code_name] = {
                        "count": 0,
                        "category": seg_row["category_name"],
                        "color": seg_row["code_color"]
                    }
                codes_used[code_name]["count"] += 1

            # Get annotations (P1-3: annotation_visible when present)
            annotations_cursor = self.conn.execute(f"""
                SELECT
                    anid,
                    pos0,
                    pos1,
                    memo,
                    owner,
                    date
                FROM {self._visible_source("annotation",
                                           "annotation_visible")}
                WHERE fid = ?
                ORDER BY pos0
            """, (file_id,))

            annotations = []
            for ann_row in annotations_cursor.fetchall():
                annotations.append({
                    "annotation_id": ann_row["anid"],
                    "position_start": ann_row["pos0"],
                    "position_end": ann_row["pos1"],
                    # GUI-created annotations always have a non-empty memo,
                    # but REFI-imported rows can carry '' or NULL
                    # (cases-attributes.md §7.5) — tolerate and normalize
                    "memo": ann_row["memo"] or "",
                    "owner": ann_row["owner"],
                    "date": ann_row["date"]
                })

            return {
                "file_info": {
                    "id": file_row["id"],
                    "name": file_row["name"],
                    "type": file_type,
                    "is_text": is_text,
                    "memo": file_row["memo"] or "",
                    "owner": file_row["owner"],
                    "date": file_row["date"]
                },
                "full_text": file_row["fulltext"] or "",
                "coded_segments": coded_segments,
                "codes_used": codes_used,
                "annotations": annotations,
                "statistics": {
                    "total_segments": len(coded_segments),
                    "unique_codes": len(codes_used),
                    "total_annotations": len(annotations),
                    "text_length": len(file_row["fulltext"] or "")
                }
            }

        except sqlite3.Error as e:
            _raise_query_error(e, "get_file_with_coding", "Failed to retrieve file with coding")

    def list_cases(self) -> List[Dict[str, Any]]:
        """Get all cases in the project.

        Returns:
            List of cases with metadata
        """
        cursor = self.conn.execute("""
            SELECT
                caseid,
                name,
                memo,
                owner,
                date
            FROM cases
            ORDER BY name
        """)

        cases = []
        for row in cursor.fetchall():
            # Count text segments for this case
            text_count = self.conn.execute(
                "SELECT COUNT(*) as cnt FROM case_text WHERE caseid = ?",
                (row["caseid"],)
            ).fetchone()["cnt"]

            cases.append({
                "id": row["caseid"],
                "name": row["name"],
                "memo": row["memo"] or "",
                "owner": row["owner"],
                "date": row["date"],
                "text_segment_count": text_count
            })
        return cases

    def get_case_details(self, case_id: int) -> Optional[Dict[str, Any]]:
        """Get detailed information about a specific case.

        Args:
            case_id: The case ID

        Returns:
            Case details with associated text segments
        """
        cursor = self.conn.execute("""
            SELECT
                caseid,
                name,
                memo,
                owner,
                date
            FROM cases
            WHERE caseid = ?
        """, (case_id,))

        row = cursor.fetchone()
        if not row:
            return None

        # Get associated text segments
        segments_cursor = self.conn.execute("""
            SELECT
                ct.id,
                ct.pos0,
                ct.pos1,
                ct.memo,
                s.name as file_name,
                s.id as file_id,
                substr(s.fulltext, ct.pos0 + 1, ct.pos1 - ct.pos0) as text_excerpt
            FROM case_text ct
            JOIN source s ON ct.fid = s.id
            WHERE ct.caseid = ?
            ORDER BY s.name, ct.pos0
        """, (case_id,))

        segments = []
        for seg_row in segments_cursor.fetchall():
            segments.append({
                "id": seg_row["id"],
                "file_name": seg_row["file_name"],
                "file_id": seg_row["file_id"],
                "position_start": seg_row["pos0"],
                "position_end": seg_row["pos1"],
                "text": seg_row["text_excerpt"] or "",
                "memo": seg_row["memo"] or ""
            })

        return {
            "id": row["caseid"],
            "name": row["name"],
            "memo": row["memo"] or "",
            "owner": row["owner"],
            "date": row["date"],
            "text_segments": segments
        }

    def search_coded_text(self, query: str, code_name: Optional[str] = None,
                         limit: int = DEFAULT_LIMIT,
                         coder: Optional[str] = None) -> List[Dict[str, Any]]:
        """Search for coded text segments.

        Args:
            query: Text to search for (wildcards % and _ are escaped)
            code_name: Optional code name to filter by
            limit: Maximum results to return (max 5000)
            coder: Explicit coder filter; reads the BASE table filtered
                   to this owner (P1-3 override). Default reads through
                   code_text_visible when the project has QC 4.0 coder
                   visibility.

        Returns:
            List of matching coded segments

        Raises:
            TypeError: If parameters are wrong type
            ValueError: If parameters are invalid
            RuntimeError: If database operation fails
        """
        # Validate and escape inputs
        query = validate_string(query, "query")
        escaped_query = escape_like_pattern(query)
        limit = validate_limit(limit)
        coder = self._validate_coder(coder)
        source = self.code_text_source(coder is None)
        owner_sql = " AND ct.owner = ?" if coder is not None else ""
        owner_params = (coder,) if coder is not None else ()

        try:
            if code_name:
                code_name = validate_string(code_name, "code_name")

                cursor = self.conn.execute(f"""
                    SELECT
                        ct.ctid,
                        ct.seltext,
                        ct.pos0,
                        ct.pos1,
                        ct.memo,
                        ct.owner,
                        ct.date,
                        s.name as file_name,
                        c.name as code_name,
                        c.color as code_color
                    FROM {source} ct
                    JOIN source s ON ct.fid = s.id
                    JOIN code_name c ON ct.cid = c.cid
                    WHERE ct.seltext LIKE ? ESCAPE '\\' AND c.name = ?{owner_sql}
                    ORDER BY s.name, ct.pos0
                    LIMIT ?
                """, (f"%{escaped_query}%", code_name) + owner_params + (limit,))
            else:
                cursor = self.conn.execute(f"""
                    SELECT
                        ct.ctid,
                        ct.seltext,
                        ct.pos0,
                        ct.pos1,
                        ct.memo,
                        ct.owner,
                        ct.date,
                        s.name as file_name,
                        c.name as code_name,
                        c.color as code_color
                    FROM {source} ct
                    JOIN source s ON ct.fid = s.id
                    JOIN code_name c ON ct.cid = c.cid
                    WHERE ct.seltext LIKE ? ESCAPE '\\'{owner_sql}
                    ORDER BY s.name, ct.pos0
                    LIMIT ?
                """, (f"%{escaped_query}%",) + owner_params + (limit,))

            results = []
            for row in cursor.fetchall():
                results.append({
                    "id": row["ctid"],
                    "text": row["seltext"],
                    "position_start": row["pos0"],
                    "position_end": row["pos1"],
                    "memo": row["memo"] or "",
                    "owner": row["owner"],
                    "date": row["date"],
                    "file_name": row["file_name"],
                    "code_name": row["code_name"],
                    "code_color": row["code_color"]
                })
            return results
        except sqlite3.Error as e:
            _raise_query_error(e, "search_coded_text", "Failed to search coded text")

    def get_coding_frequencies(self, coder: Optional[str] = None,
                               honor_visibility: bool = True
                               ) -> Dict[str, Any]:
        """Get frequency counts for all codes.

        Args:
            coder: Explicit coder filter; counts the BASE table rows of
                   this owner only (P1-3 override)
            honor_visibility: Count through code_text_visible when the
                   project has QC 4.0 coder visibility (default True;
                   exports pass False for parity)

        Returns:
            Dictionary with code frequencies
        """
        coder = self._validate_coder(coder)
        source = self.code_text_source(honor_visibility and coder is None)
        owner_sql = " AND ct.owner = ?" if coder is not None else ""
        params = (coder,) if coder is not None else ()
        # COUNT(s.id): orphaned codings (fid pointing at a deleted source)
        # are excluded, consistent with get_coded_text_segments — previously
        # counting and listing tools disagreed on the same code (QA F8)
        cursor = self.conn.execute(f"""
            SELECT
                c.cid,
                c.name,
                c.color,
                cat.name as category,
                COUNT(s.id) as text_count
            FROM code_name c
            LEFT JOIN code_cat cat ON c.catid = cat.catid
            LEFT JOIN {source} ct ON c.cid = ct.cid{owner_sql}
            LEFT JOIN source s ON ct.fid = s.id
            GROUP BY c.cid, c.name, c.color, cat.name
            ORDER BY text_count DESC, c.name
        """, params)

        caps = getattr(self, "capabilities", None)
        has_supercid = caps is not None and caps.has_supercid
        maps = self._hierarchy_maps() if has_supercid else None

        frequencies = []
        for row in cursor.fetchall():
            entry = {
                "code_id": row["cid"],
                "code_name": row["name"],
                "code_color": row["color"],
                "category": row["category"],
                "frequency": row["text_count"]
            }
            if has_supercid:
                # S6: attribute each code to its TOP ANCESTOR code's
                # category, as master's frequencies report does
                # (reports.py:287-313); counts stay per-cid
                _catid, cat_name = self.effective_category(row["cid"], maps)
                entry["category"] = cat_name
            frequencies.append(entry)

        total = sum(f["frequency"] for f in frequencies)

        return {
            "total_coded_segments": total,
            "codes": frequencies
        }

    def search_memos(self, query: str, limit: int = DEFAULT_LIMIT) -> List[Dict[str, Any]]:
        """Search for memos and annotations.

        Args:
            query: Text to search for (wildcards % and _ are escaped)
            limit: Maximum results (max 5000)

        Returns:
            List of matching memos

        Raises:
            TypeError: If parameters are wrong type
            ValueError: If parameters are invalid
            RuntimeError: If database operation fails
        """
        query = validate_string(query, "query")
        escaped_query = escape_like_pattern(query)
        limit = validate_limit(limit)

        results = []

        # Memo privacy ('#####'): match against the PUBLIC part only and
        # return the public part only. The SQL LIKE over the full column
        # is a cheap SUPERSET filter and carries NO LIMIT: the cap is
        # enforced in Python only after the public-part check, so a row
        # whose match lives only in the private suffix never consumes
        # result budget. result_count therefore depends on public
        # content alone (and result_count < limit means the search was
        # exhaustive, as on the pre-privacy code), which is what keeps
        # the search from being a count oracle on private content
        # (QA round 1, F1).
        query_folded = query.lower()

        def _public_hit(raw_memo):
            """(matches, public_text) for one candidate memo."""
            public = extract_ai_memo(raw_memo or "")
            return query_folded in public.lower(), public

        pattern = f"%{escaped_query}%"

        try:
            # Search code memos (cursor iterated, cap applied in Python
            # after the public-part check; see the note above)
            cursor = self.conn.execute("""
                SELECT
                    'code' as type,
                    cid as id,
                    name,
                    memo,
                    owner,
                    date
                FROM code_name
                WHERE memo LIKE ? ESCAPE '\\'
                ORDER BY cid
            """, (pattern,))

            for row in cursor:
                matches, public_memo = _public_hit(row["memo"])
                if not matches:
                    continue
                results.append({
                    "type": row["type"],
                    "id": row["id"],
                    "name": row["name"],
                    "memo": public_memo,
                    "owner": row["owner"],
                    "date": row["date"]
                })
                if len(results) >= limit:
                    break

            # Search file memos (only while budget remains)
            if len(results) < limit:
                cursor = self.conn.execute("""
                    SELECT
                        'file' as type,
                        id,
                        name,
                        memo,
                        owner,
                        date
                    FROM source
                    WHERE memo LIKE ? ESCAPE '\\'
                    ORDER BY id
                """, (pattern,))

                for row in cursor:
                    matches, public_memo = _public_hit(row["memo"])
                    if not matches:
                        continue
                    results.append({
                        "type": row["type"],
                        "id": row["id"],
                        "name": row["name"],
                        "memo": public_memo,
                        "owner": row["owner"],
                        "date": row["date"]
                    })
                    if len(results) >= limit:
                        break

            # Search annotations (only while budget remains)
            if len(results) < limit:
                # P1-3: annotations honor QC 4.0 coder visibility here
                # too (annotation_visible when present, base table
                # otherwise; QA round 1, F13)
                annotation_source = self._visible_source(
                    "annotation", "annotation_visible")
                cursor = self.conn.execute(f"""
                    SELECT
                        'annotation' as type,
                        a.anid as id,
                        s.name,
                        a.memo,
                        a.owner,
                        a.date,
                        a.pos0,
                        a.pos1
                    FROM {annotation_source} a
                    JOIN source s ON a.fid = s.id
                    WHERE a.memo LIKE ? ESCAPE '\\'
                    ORDER BY a.anid
                """, (pattern,))

                for row in cursor:
                    matches, public_memo = _public_hit(row["memo"])
                    if not matches:
                        continue
                    results.append({
                        "type": row["type"],
                        "id": row["id"],
                        "name": row["name"],
                        "memo": public_memo,
                        "owner": row["owner"],
                        "date": row["date"],
                        "position_start": row["pos0"],
                        "position_end": row["pos1"]
                    })
                    if len(results) >= limit:
                        break

            return results
        except sqlite3.Error as e:
            _raise_query_error(e, "search_memos", "Failed to search memos")

    def search_file_content(
        self,
        query: str,
        case_sensitive: bool = False,
        limit: int = DEFAULT_LIMIT,
        context_chars: int = 100
    ) -> List[Dict[str, Any]]:
        """Search through full text content of all text files.

        WARNING: This searches ALL file content and can be slow for large projects
        with many files. Consider using more specific search methods if possible.

        Args:
            query: Text to search for
            case_sensitive: Whether to perform case-sensitive search (default: False)
            limit: Maximum number of files to return (default: DEFAULT_LIMIT)
            context_chars: Number of characters of context around each match (default: 100)

        Returns:
            List of dictionaries containing:
            - file_id: The file ID
            - file_name: The file name
            - file_type: The file type
            - match_count: Number of matches in this file
            - matches: List of match dictionaries with:
                - position: Character position of match
                - preview: Text snippet with context around match
        """
        limit = validate_limit(limit)

        if not query:
            return []

        try:
            # Search through text files only
            cursor = self.conn.execute("""
                SELECT
                    id,
                    name,
                    fulltext,
                    mediapath,
                    memo,
                    owner,
                    date
                FROM source
                WHERE mediapath IS NULL OR mediapath = ''
                    OR mediapath LIKE '/docs/%' OR mediapath LIKE 'docs:%'
                ORDER BY name
            """)

            results = []
            files_checked = 0

            for row in cursor.fetchall():
                files_checked += 1
                file_text = row["fulltext"] or ""

                # Perform search
                search_text = file_text if case_sensitive else file_text.lower()
                search_query = query if case_sensitive else query.lower()

                # Find all matches
                matches = []
                start_pos = 0

                while True:
                    pos = search_text.find(search_query, start_pos)
                    if pos == -1:
                        break

                    # Extract context around match
                    context_start = max(0, pos - context_chars)
                    context_end = min(len(file_text), pos + len(query) + context_chars)
                    preview = file_text[context_start:context_end]

                    # Add ellipsis if truncated
                    if context_start > 0:
                        preview = "..." + preview
                    if context_end < len(file_text):
                        preview = preview + "..."

                    matches.append({
                        "position": pos,
                        "preview": preview
                    })

                    start_pos = pos + 1  # Continue searching

                # If matches found, add to results
                if matches:
                    results.append({
                        "file_id": row["id"],
                        "file_name": row["name"],
                        "file_type": _detect_file_type(row["mediapath"]),
                        "memo": row["memo"] or "",
                        "match_count": len(matches),
                        "matches": matches[:10]  # Limit to first 10 matches per file
                    })

                # Stop if we've reached the limit
                if len(results) >= limit:
                    break

            logger.info(f"Content search found {len(results)} files with matches "
                       f"(searched {files_checked} files)")
            return results

        except sqlite3.Error as e:
            _raise_query_error(e, "search_file_content", "Failed to search file content")

    def search_files(
        self,
        pattern: str,
        search_filename: bool = True,
        search_content: bool = False,
        search_memo: bool = False,
        case_sensitive: bool = False,
        limit: int = DEFAULT_LIMIT,
        context_chars: int = 100
    ) -> Dict[str, Any]:
        """Search for files across multiple locations (filename, content, memo).

        This is a comprehensive search method that can search in different parts
        of the file data and aggregates results showing where matches were found.

        Args:
            pattern: Text to search for
            search_filename: Search in file names (default: True)
            search_content: Search in file content/fulltext (default: False)
            search_memo: Search in file memos (default: False)
            case_sensitive: Case-sensitive matching (default: False)
            limit: Maximum number of files to return (default: DEFAULT_LIMIT)
            context_chars: Characters of context around content matches (default: 100)

        Returns:
            Dictionary containing:
            - search_parameters: Dict of search settings used
            - performance_info: Dict with search performance details
            - total_files_searched: Number of files examined
            - total_matches: Number of files with matches
            - results: List of matching files with detailed match information
        """
        limit = validate_limit(limit)

        if not pattern:
            return {
                "search_parameters": {},
                "total_matches": 0,
                "results": []
            }

        try:
            # Get all files
            cursor = self.conn.execute("""
                SELECT
                    id,
                    name,
                    fulltext,
                    mediapath,
                    memo,
                    owner,
                    date
                FROM source
                ORDER BY name
            """)

            all_files = cursor.fetchall()
            results = []
            files_searched = 0
            files_skipped_no_text = 0

            search_pattern = pattern if case_sensitive else pattern.lower()

            for row in all_files:
                files_searched += 1
                matched_in = {
                    "filename": False,
                    "content": False,
                    "memo": False
                }
                matches = []
                match_count = 0

                # Search filename (a NULL name must not abort the search;
                # QA F5: one unnamed row previously killed every query)
                raw_name = row["name"] or ""
                if search_filename:
                    file_name = raw_name if case_sensitive else raw_name.lower()
                    if search_pattern in file_name:
                        matched_in["filename"] = True
                        matches.append({
                            "location": "filename",
                            "preview": raw_name
                        })
                        match_count += 1

                # Search content of ANY source that has text — this includes
                # imported documents and PDFs (mediapath '/docs/...'), which
                # were previously skipped silently, producing false negatives
                # (QA F10). Sources without text (image/audio/video) are
                # counted so the caller can see what was not searched.
                if search_content:
                    file_text = row["fulltext"] or ""
                    if not file_text:
                        files_skipped_no_text += 1
                    search_text = file_text if case_sensitive else file_text.lower()

                    # Find all content matches
                    start_pos = 0
                    content_matches = 0

                    while True:
                        pos = search_text.find(search_pattern, start_pos)
                        if pos == -1 or content_matches >= 5:  # Limit to 5 content matches per file
                            break

                        matched_in["content"] = True

                        # Extract context
                        context_start = max(0, pos - context_chars)
                        context_end = min(len(file_text), pos + len(pattern) + context_chars)
                        preview = file_text[context_start:context_end]

                        if context_start > 0:
                            preview = "..." + preview
                        if context_end < len(file_text):
                            preview = preview + "..."

                        matches.append({
                            "location": "content",
                            "position": pos,
                            "preview": preview
                        })

                        content_matches += 1
                        match_count += 1
                        start_pos = pos + 1

                # Search memo. Memo privacy ('#####'): match against the
                # public part only and preview the public part only, so a
                # private suffix can neither be found nor shown.
                if search_memo:
                    memo_text = extract_ai_memo(row["memo"] or "")
                    search_memo_text = memo_text if case_sensitive else memo_text.lower()

                    if search_pattern in search_memo_text:
                        matched_in["memo"] = True
                        matches.append({
                            "location": "memo",
                            "preview": memo_text[:200] + ("..." if len(memo_text) > 200 else "")
                        })
                        match_count += 1

                # If any matches, add to results
                if any(matched_in.values()):
                    file_type = _detect_file_type(row["mediapath"])

                    results.append({
                        "file_id": row["id"],
                        "file_name": row["name"],
                        "file_type": file_type,
                        "matched_in": matched_in,
                        "match_count": match_count,
                        "matches": matches
                    })

                if len(results) >= limit:
                    break

            # Performance info
            performance_info = {
                "files_examined": files_searched,
                "searched_content": search_content
            }

            if search_content:
                performance_info["note"] = "Content search can be slow for many files"
                performance_info["files_skipped_no_text"] = files_skipped_no_text
                if files_skipped_no_text:
                    performance_info["skip_note"] = (
                        f"{files_skipped_no_text} source(s) without text content "
                        f"(e.g. image/audio/video) were not content-searched"
                    )

            logger.info(f"File search found {len(results)} matches (searched {files_searched} files)")

            return {
                "search_parameters": {
                    "pattern": pattern,
                    "searched_filename": search_filename,
                    "searched_content": search_content,
                    "searched_memo": search_memo,
                    "case_sensitive": case_sensitive
                },
                "performance_info": performance_info,
                "total_files_searched": files_searched,
                "total_matches": len(results),
                "results": results
            }

        except sqlite3.Error as e:
            _raise_query_error(e, "search_files", "Failed to search files")

    def count_media_codings(self) -> Dict[str, int]:
        """Count the project's audio/video and image codings.

        REFI export covers text codings only; the export tool uses these
        counts to disclose what a mixed project loses (track6 finding).
        """
        try:
            av = self.conn.execute(
                "SELECT COUNT(*) FROM code_av").fetchone()[0]
            image = self.conn.execute(
                "SELECT COUNT(*) FROM code_image").fetchone()[0]
            return {"av": av, "image": image}
        except sqlite3.Error as e:
            _raise_query_error(e, "count_media_codings",
                               "Failed to count media codings")

    def get_journal_entries(self) -> List[Dict[str, Any]]:
        """Get all journal entries.

        Returns:
            List of journal entries
        """
        cursor = self.conn.execute("""
            SELECT
                jid,
                name,
                jentry,
                date,
                owner
            FROM journal
            ORDER BY date DESC
        """)

        entries = []
        for row in cursor.fetchall():
            entries.append({
                "id": row["jid"],
                "name": row["name"],
                "content": row["jentry"],
                "date": row["date"],
                "owner": row["owner"]
            })
        return entries

    # ============================================================================
    # ATTRIBUTES - Demographics and metadata
    # ============================================================================

    def list_attribute_types(self) -> List[Dict[str, Any]]:
        """Get all attribute type definitions.

        Returns:
            List of attribute types with their properties
        """
        try:
            cursor = self.conn.execute("""
                SELECT
                    name,
                    date,
                    owner,
                    memo,
                    caseOrFile,
                    valuetype
                FROM attribute_type
                ORDER BY name
            """)

            attributes = []
            for row in cursor.fetchall():
                attributes.append({
                    "name": row["name"],
                    "date": row["date"],
                    "owner": row["owner"],
                    "memo": row["memo"] or "",
                    # Real domain set is 'case' | 'file' | 'journal' — there
                    # is no 'both' anywhere in QualCoder (cases-attributes.md
                    # §1; the old comment here claiming 'both' was wrong)
                    "applies_to": row["caseOrFile"],
                    "value_type": row["valuetype"]  # 'character' or 'numeric'
                })
            return attributes
        except sqlite3.Error as e:
            _raise_query_error(e, "list_attribute_types", "Failed to retrieve attribute types")

    def get_file_attributes(self, file_id: int) -> List[Dict[str, Any]]:
        """Get all attributes for a specific file.

        Args:
            file_id: The file ID

        Returns:
            List of attributes with names and values
        """
        file_id = validate_id(file_id, "file_id")

        try:
            cursor = self.conn.execute("""
                SELECT
                    a.attrid,
                    a.name,
                    a.value,
                    a.date,
                    a.owner,
                    at.valuetype,
                    at.memo
                FROM attribute a
                JOIN attribute_type at ON a.name = at.name
                WHERE a.attr_type = 'file' AND a.id = ?
                ORDER BY a.name
            """, (file_id,))

            attributes = []
            for row in cursor.fetchall():
                attributes.append({
                    "attribute_id": row["attrid"],
                    "name": row["name"],
                    "value": row["value"],
                    "value_type": row["valuetype"],
                    "memo": row["memo"] or "",
                    "date": row["date"],
                    "owner": row["owner"]
                })
            return attributes
        except sqlite3.Error as e:
            _raise_query_error(e, "get_file_attributes", "Failed to retrieve file attributes")

    def get_case_attributes(self, case_id: int) -> List[Dict[str, Any]]:
        """Get all attributes for a specific case.

        Args:
            case_id: The case ID

        Returns:
            List of attributes with names and values
        """
        case_id = validate_id(case_id, "case_id")

        try:
            cursor = self.conn.execute("""
                SELECT
                    a.attrid,
                    a.name,
                    a.value,
                    a.date,
                    a.owner,
                    at.valuetype,
                    at.memo
                FROM attribute a
                JOIN attribute_type at ON a.name = at.name
                WHERE a.attr_type = 'case' AND a.id = ?
                ORDER BY a.name
            """, (case_id,))

            attributes = []
            for row in cursor.fetchall():
                attributes.append({
                    "attribute_id": row["attrid"],
                    "name": row["name"],
                    "value": row["value"],
                    "value_type": row["valuetype"],
                    "memo": row["memo"] or "",
                    "date": row["date"],
                    "owner": row["owner"]
                })
            return attributes
        except sqlite3.Error as e:
            _raise_query_error(e, "get_case_attributes", "Failed to retrieve case attributes")

    # Operator -> SQL condition over the attribute value. The SQL text is
    # selected from this FIXED mapping (never user input); values are bound
    # as parameters. Attribute values are stored as TEXT even for numeric
    # attributes, and SQLite CAST('' AS REAL) = 0.0 — so a bare CAST would
    # make every UNSET placeholder ('' value) match gt/lt comparisons as
    # zero (the old comment claiming non-numeric values never match was
    # FALSE). Numeric operators therefore exclude ''-value rows explicitly
    # (cases-attributes.md §3.4/§6.4; NULL values are excluded by CAST(NULL)
    # comparing as NULL). QualCoder's own attribute report shares the
    # empty-matches-as-zero flaw; this is a deliberate, documented fix.
    _ATTRIBUTE_OPERATORS = {
        "equals": "a.value = ?",
        "contains": "a.value LIKE ? ESCAPE '\\'",
        "gt": "(a.value != '' AND CAST(a.value AS REAL) > ?)",
        "gte": "(a.value != '' AND CAST(a.value AS REAL) >= ?)",
        "lt": "(a.value != '' AND CAST(a.value AS REAL) < ?)",
        "lte": "(a.value != '' AND CAST(a.value AS REAL) <= ?)",
    }

    # 'equals' on a NUMERIC attribute compares numerically (so '5' matches
    # a stored '5.0'), because plain string equality on numerics is a trap.
    # 'equals' with '' stays string comparison — it is the legitimate way
    # to find UNSET attributes (cases-attributes.md §6.4: don't fix that
    # away).
    _NUMERIC_EQUALS_CONDITION = "(a.value != '' AND CAST(a.value AS REAL) = ?)"

    def query_by_attribute(self, attr_name: str, attr_value: str,
                           attr_type: str = "case",
                           operator: str = "equals") -> List[Dict[str, Any]]:
        """Query cases or files by attribute value.

        Args:
            attr_name: The attribute name to filter by
            attr_value: The attribute value to match (a number for the
                        gt/gte/lt/lte operators)
            attr_type: 'case' or 'file'
            operator: 'equals' (exact match, default; compares numerically
                      for numeric attributes so '5' finds '5.0'; '' finds
                      unset attributes), 'contains' (case-insensitive
                      substring), or 'gt'/'gte'/'lt'/'lte' (numeric
                      comparison; unset ''-value rows never match)

        Returns:
            List of cases or files matching the attribute criteria
        """
        if not isinstance(attr_name, str) or not isinstance(attr_value, str):
            raise TypeError("attr_name and attr_value must be strings")

        if attr_type not in ['case', 'file']:
            raise ValueError("attr_type must be 'case' or 'file'")

        if operator not in self._ATTRIBUTE_OPERATORS:
            raise ValueError(
                f"operator must be one of: "
                f"{', '.join(sorted(self._ATTRIBUTE_OPERATORS))}"
            )
        condition = self._ATTRIBUTE_OPERATORS[operator]

        if operator == "contains":
            bound_value: Any = f"%{escape_like_pattern(attr_value)}%"
        elif operator in ("gt", "gte", "lt", "lte"):
            try:
                bound_value = float(attr_value)
            except ValueError:
                raise ValueError(
                    f"attr_value must be a number for operator '{operator}', "
                    f"got '{attr_value}'"
                ) from None
        elif operator == "equals":
            # Numeric attributes: compare numerically so '5' finds '5.0'
            # (values are stored as TEXT; plain string equality would miss
            # every formatting variant). '' keeps string semantics — it is
            # how unset attributes are found.
            bound_value = attr_value
            if attr_value != "":
                try:
                    vt_row = self.conn.execute(
                        "SELECT valuetype FROM attribute_type WHERE name = ?",
                        (attr_name,)
                    ).fetchone()
                except sqlite3.Error as e:
                    _raise_query_error(e, "query_by_attribute",
                                       "Failed to query by attribute")
                if vt_row and vt_row["valuetype"] == "numeric":
                    try:
                        bound_value = float(attr_value)
                        condition = self._NUMERIC_EQUALS_CONDITION
                    except ValueError:
                        # Non-numeric probe against a numeric attribute can
                        # only string-match (and never will match a numeric
                        # value) — keep string equality
                        pass
        else:
            bound_value = attr_value

        try:
            if attr_type == 'case':
                cursor = self.conn.execute(f"""
                    SELECT
                        c.caseid,
                        c.name,
                        c.memo,
                        a.value as attr_value
                    FROM cases c
                    JOIN attribute a ON c.caseid = a.id AND a.attr_type = 'case'
                    WHERE a.name = ? AND {condition}
                    ORDER BY c.name
                """, (attr_name, bound_value))

                results = []
                for row in cursor.fetchall():
                    results.append({
                        "case_id": row["caseid"],
                        "name": row["name"],
                        "memo": row["memo"] or "",
                        "attribute_value": row["attr_value"]
                    })
            else:  # file
                cursor = self.conn.execute(f"""
                    SELECT
                        s.id,
                        s.name,
                        s.memo,
                        a.value as attr_value
                    FROM source s
                    JOIN attribute a ON s.id = a.id AND a.attr_type = 'file'
                    WHERE a.name = ? AND {condition}
                    ORDER BY s.name
                """, (attr_name, bound_value))

                results = []
                for row in cursor.fetchall():
                    results.append({
                        "file_id": row["id"],
                        "name": row["name"],
                        "memo": row["memo"] or "",
                        "attribute_value": row["attr_value"]
                    })

            return results
        except sqlite3.Error as e:
            _raise_query_error(e, "query_by_attribute", "Failed to query by attribute")

    # ========================================================================
    # REPORT-EXPORT READS (v0.8 phase B) — QualCoder-parity row sources
    # ========================================================================

    def get_codebook_frequencies(self) -> Dict[int, int]:
        """Per-code coding counts exactly as QualCoder's Codebook export
        computes them (codebook.py:249-267): code_text + code_image +
        code_av rows, ALL coders, ALL files, no filters, no source join
        (orphaned codings count, as upstream)."""
        try:
            cursor = self.conn.execute("""
                SELECT cid, COUNT(*) AS n FROM (
                    SELECT cid FROM code_text
                    UNION ALL SELECT cid FROM code_image
                    UNION ALL SELECT cid FROM code_av
                ) GROUP BY cid
            """)
            return {row["cid"]: row["n"] for row in cursor.fetchall()}
        except sqlite3.Error as e:
            _raise_query_error(e, "get_codebook_frequencies",
                               "Failed to compute codebook frequencies")

    def get_raw_coding_counts(self) -> List[Dict[str, Any]]:
        """Per-(code, coder) raw row counts over all three coding tables —
        the exact number source of QualCoder's Code Frequencies report
        (reports.py:177-280): no source join (orphaned fids count), one
        count per coding row regardless of length or medium."""
        try:
            cursor = self.conn.execute("""
                SELECT cid, owner, COUNT(*) AS n FROM (
                    SELECT cid, owner FROM code_text
                    UNION ALL SELECT cid, owner FROM code_image
                    UNION ALL SELECT cid, owner FROM code_av
                ) GROUP BY cid, owner
            """)
            return [{"code_id": row["cid"], "owner": row["owner"],
                     "count": row["n"]} for row in cursor.fetchall()]
        except sqlite3.Error as e:
            _raise_query_error(e, "get_raw_coding_counts",
                               "Failed to compute coding counts")

    def get_coding_report_rows(
        self,
        code_ids: Optional[List[int]] = None,
        file_ids: Optional[List[int]] = None,
        case_ids: Optional[List[int]] = None,
        coder: str = "",
        search_text: str = "",
        important: bool = False,
    ) -> List[Dict[str, Any]]:
        """Text-coding rows for the coded-segments report, using the exact
        SQL semantics of QualCoder's Coding Report (report_codes.py).

        File mode (no case_ids, :1504-1519): one row per code_text row,
        joined to code_name and source; ordered code name, file name,
        pos0. Case mode (:1628-1649): joined through case_text with the
        CONTAINMENT rule — a coding belongs to a case iff fully inside
        one of the case's case_text spans on the same fid
        (pos0 >= case.pos0 AND pos1 <= case.pos1) — ordered code name,
        case name. Coder filter is an EXACT owner match (never LIKE);
        search_text is a seltext substring; important filters
        important=1. Text codings only (image/AV excluded — disclosed by
        the caller). Orphaned codings are excluded by the source join,
        exactly as upstream.
        """
        if coder is not None and not isinstance(coder, str):
            raise TypeError("coder must be a string")
        if search_text is not None and not isinstance(search_text, str):
            raise TypeError("search_text must be a string")
        params: List[Any] = []
        where = []
        if code_ids:
            code_ids = [validate_id(c, "code_id") for c in code_ids]
            where.append(
                f"code_name.cid IN ({','.join('?' * len(code_ids))})")
            params.extend(code_ids)
        if file_ids:
            file_ids = [validate_id(f, "file_id") for f in file_ids]
            where.append(
                f"source.id IN ({','.join('?' * len(file_ids))})")
            params.extend(file_ids)
        if coder:
            where.append("code_text.owner = ?")
            params.append(coder)
        if search_text:
            where.append("code_text.seltext LIKE ? ESCAPE '\\'")
            params.append(f"%{escape_like_pattern(search_text)}%")
        if important:
            where.append("code_text.important = 1")

        try:
            if case_ids:
                case_ids = [validate_id(c, "case_id") for c in case_ids]
                where.append(
                    f"cases.caseid IN ({','.join('?' * len(case_ids))})")
                params.extend(case_ids)
                sql = f"""
                    SELECT code_name.name AS codename, code_name.cid,
                           code_name.color,
                           cases.name AS casename, cases.caseid,
                           source.name AS filename, source.id AS fid,
                           code_text.pos0, code_text.pos1,
                           code_text.seltext, code_text.owner,
                           code_text.ctid,
                           ifnull(code_text.memo, '') AS coded_memo
                    FROM code_text
                    JOIN code_name ON code_name.cid = code_text.cid
                    JOIN source ON code_text.fid = source.id
                    JOIN case_text ON case_text.fid = code_text.fid
                    JOIN cases ON cases.caseid = case_text.caseid
                    WHERE (code_text.pos0 >= case_text.pos0
                           AND code_text.pos1 <= case_text.pos1)
                      {"AND " + " AND ".join(where) if where else ""}
                    ORDER BY code_name.name, cases.name, code_text.pos0
                """
            else:
                sql = f"""
                    SELECT code_name.name AS codename, code_name.cid,
                           code_name.color,
                           source.name AS filename, source.id AS fid,
                           code_text.pos0, code_text.pos1,
                           code_text.seltext, code_text.owner,
                           code_text.ctid,
                           ifnull(code_text.memo, '') AS coded_memo
                    FROM code_text
                    JOIN code_name ON code_name.cid = code_text.cid
                    JOIN source ON code_text.fid = source.id
                    {"WHERE " + " AND ".join(where) if where else ""}
                    ORDER BY code_name.name, source.name, code_text.pos0
                """
            cursor = self.conn.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            _raise_query_error(e, "get_coding_report_rows",
                               "Failed to build the coding report")

    # ============================================================================
    # CO-OCCURRENCE ANALYSIS - Codes appearing together
    # ============================================================================

    def find_code_cooccurrences(self, code_id: int, window_size: int = 0,
                                coder: Optional[str] = None
                                ) -> List[Dict[str, Any]]:
        """Find codes that appear together with a specific code.

        Args:
            code_id: The code ID to find co-occurrences for
            window_size: If 0, finds codes in same segment (overlap).
                        If > 0, finds codes within N characters
            coder: Explicit coder filter; analyzes the BASE table rows
                   of this owner only (P1-3 override). Default reads
                   through code_text_visible when the project has QC
                   4.0 coder visibility.

        Returns:
            List of codes that co-occur with counts
        """
        code_id = validate_id(code_id, "code_id")
        coder = self._validate_coder(coder)

        if not isinstance(window_size, int) or window_size < 0:
            raise ValueError("window_size must be a non-negative integer")

        # The old SQL self-join on code_text was O(n^2) in codings-per-file
        # (3.2 s at 12k codings in one dense document — track6). The user DB
        # schema is QualCoder's and must not gain indexes, so the join is
        # built in Python instead: one pass to group rows by fid, then a
        # sorted-array bisect per candidate row — O(n log n) per file.
        # Semantics are identical to the old SQL: window_size == 0 counts
        # closed-interval intersections (the three OR conditions reduce to
        # o.pos0 <= t.pos1 AND o.pos1 >= t.pos0); window_size > 0 counts
        # |o.pos0 - t.pos0| <= window; NULL positions never match.
        source = self.code_text_source(coder is None)
        owner_sql = " AND ct.owner = ?" if coder is not None else ""
        owner_inner = " AND owner = ?" if coder is not None else ""
        params = ((code_id, coder, coder) if coder is not None
                  else (code_id,))
        try:
            rows = self.conn.execute(f"""
                SELECT ct.cid, ct.fid, ct.pos0, ct.pos1
                FROM {source} ct
                WHERE ct.fid IN (
                    SELECT DISTINCT fid FROM {source} WHERE cid = ?{owner_inner}
                ){owner_sql}
            """, params).fetchall()

            # Group by file, separating target-code rows from candidates
            targets_by_fid: Dict[Any, List] = {}
            others_by_fid: Dict[Any, List] = {}
            for r in rows:
                pos0, pos1 = r["pos0"], r["pos1"]
                if not isinstance(pos0, int) or not isinstance(pos1, int):
                    continue  # damaged row; SQL NULL comparisons never matched
                if r["cid"] == code_id:
                    targets_by_fid.setdefault(r["fid"], []).append((pos0, pos1))
                elif r["cid"] is not None:
                    others_by_fid.setdefault(r["fid"], []).append(
                        (r["cid"], pos0, pos1))

            counts: Dict[int, int] = {}
            for fid, targets in targets_by_fid.items():
                others = others_by_fid.get(fid)
                if not others:
                    continue
                starts = sorted(p0 for p0, _ in targets)
                ends = sorted(p1 for _, p1 in targets)
                for cid, o0, o1 in others:
                    if window_size == 0:
                        # targets with pos0 <= o1, minus targets with pos1 < o0
                        n = (bisect.bisect_right(starts, o1)
                             - bisect.bisect_left(ends, o0))
                    else:
                        n = (bisect.bisect_right(starts, o0 + window_size)
                             - bisect.bisect_left(starts, o0 - window_size))
                    if n > 0:
                        counts[cid] = counts.get(cid, 0) + n

            code_info = {c["id"]: c for c in self.list_codes()}
            cooccurrences = []
            for cid, n in counts.items():
                info = code_info.get(cid)
                if info is None:
                    continue  # orphaned cid — the old JOIN dropped these too
                cooccurrences.append({
                    "code_id": cid,
                    "code_name": info["name"],
                    "color": info["color"],
                    "category": info["category"],
                    "cooccurrence_count": n,
                })
            cooccurrences.sort(key=lambda c: c["cooccurrence_count"],
                               reverse=True)
            return cooccurrences
        except sqlite3.Error as e:
            _raise_query_error(e, "find_code_cooccurrences", "Failed to find co-occurrences")

    # ============================================================================
    # CASE-CODE MATRIX - Cross-tabulation
    # ============================================================================

    def get_case_code_matrix(self, coder: Optional[str] = None,
                             honor_visibility: bool = True
                             ) -> Dict[str, Any]:
        """Get a matrix showing which codes appear in which cases.

        Args:
            coder: Explicit coder filter; counts the BASE table rows of
                   this owner only (P1-3 override)
            honor_visibility: Count through code_text_visible when the
                   project has QC 4.0 coder visibility (default True;
                   the CSV export passes False for parity)

        Returns:
            Dictionary with cases, codes, and matrix data
        """
        coder = self._validate_coder(coder)
        source = self.code_text_source(honor_visibility and coder is None)
        owner_sql = " AND ct.owner = ?" if coder is not None else ""
        params = (coder,) if coder is not None else ()
        try:
            # Get all cases
            cases_cursor = self.conn.execute("""
                SELECT caseid, name
                FROM cases
                ORDER BY name
            """)
            cases = [{"id": row["caseid"], "name": row["name"]}
                    for row in cases_cursor.fetchall()]

            # Get all codes
            codes_cursor = self.conn.execute("""
                SELECT cid, name
                FROM code_name
                ORDER BY name
            """)
            codes = [{"id": row["cid"], "name": row["name"]}
                    for row in codes_cursor.fetchall()]

            # Get the matrix data
            matrix_cursor = self.conn.execute(f"""
                SELECT
                    cs.caseid,
                    ct.cid,
                    COUNT(*) as count
                FROM case_text cs
                JOIN {source} ct ON cs.fid = ct.fid{owner_sql}
                    -- full CONTAINMENT, matching QualCoder's own reports
                    -- (report_codes.py:1640). The end-of-file caveat is
                    -- PER ROW, not per version: on an old-convention
                    -- whole-file link (pos1 = len-1) a coding including
                    -- the file's last character is excluded; on a
                    -- pos1 = len row it is included
                    AND ct.pos0 >= cs.pos0 AND ct.pos1 <= cs.pos1
                GROUP BY cs.caseid, ct.cid
            """, params)

            matrix = {}
            for row in matrix_cursor.fetchall():
                case_id = row["caseid"]
                code_id = row["cid"]
                if case_id not in matrix:
                    matrix[case_id] = {}
                matrix[case_id][code_id] = row["count"]

            return {
                "cases": cases,
                "codes": codes,
                "matrix": matrix
            }
        except sqlite3.Error as e:
            _raise_query_error(e, "get_case_code_matrix", "Failed to generate case-code matrix")

    def get_codes_by_case(self, case_id: int,
                          coder: Optional[str] = None
                          ) -> List[Dict[str, Any]]:
        """Get all codes that appear in a specific case.

        Args:
            case_id: The case ID
            coder: Explicit coder filter; counts the BASE table rows of
                   this owner only (P1-3 override). Default counts
                   through code_text_visible when the project has QC
                   4.0 coder visibility.

        Returns:
            List of codes with occurrence counts
        """
        case_id = validate_id(case_id, "case_id")
        coder = self._validate_coder(coder)
        source = self.code_text_source(coder is None)
        owner_sql = " AND ct.owner = ?" if coder is not None else ""
        params = ((case_id, coder) if coder is not None else (case_id,))

        try:
            cursor = self.conn.execute(f"""
                SELECT
                    c.cid,
                    c.name as code_name,
                    c.color,
                    cat.name as category_name,
                    COUNT(*) as occurrence_count
                FROM case_text cs
                JOIN {source} ct ON cs.fid = ct.fid
                    -- full CONTAINMENT, matching QualCoder's own reports
                    -- (report_codes.py:1640). The end-of-file caveat is
                    -- PER ROW, not per version: on an old-convention
                    -- whole-file link (pos1 = len-1) a coding including
                    -- the file's last character is excluded; on a
                    -- pos1 = len row it is included
                    AND ct.pos0 >= cs.pos0 AND ct.pos1 <= cs.pos1
                JOIN code_name c ON ct.cid = c.cid
                LEFT JOIN code_cat cat ON c.catid = cat.catid
                WHERE cs.caseid = ?{owner_sql}
                GROUP BY c.cid, c.name, c.color, cat.name
                ORDER BY occurrence_count DESC
            """, params)

            codes = []
            for row in cursor.fetchall():
                codes.append({
                    "code_id": row["cid"],
                    "code_name": row["code_name"],
                    "color": row["color"],
                    "category": row["category_name"],
                    "occurrence_count": row["occurrence_count"]
                })

            return codes
        except sqlite3.Error as e:
            _raise_query_error(e, "get_codes_by_case", "Failed to get codes by case")

    def get_cases_by_code(self, code_id: int,
                          coder: Optional[str] = None
                          ) -> List[Dict[str, Any]]:
        """Get all cases that contain a specific code.

        Args:
            code_id: The code ID
            coder: Explicit coder filter; counts the BASE table rows of
                   this owner only (P1-3 override). Default counts
                   through code_text_visible when the project has QC
                   4.0 coder visibility.

        Returns:
            List of cases with occurrence counts
        """
        code_id = validate_id(code_id, "code_id")
        coder = self._validate_coder(coder)
        source = self.code_text_source(coder is None)
        owner_sql = " AND ct.owner = ?" if coder is not None else ""
        params = ((code_id, coder) if coder is not None else (code_id,))

        try:
            cursor = self.conn.execute(f"""
                SELECT
                    cs.caseid,
                    c.name as case_name,
                    c.memo,
                    COUNT(*) as occurrence_count
                FROM case_text cs
                JOIN {source} ct ON cs.fid = ct.fid
                    -- full CONTAINMENT, matching QualCoder's own reports
                    -- (report_codes.py:1640). The end-of-file caveat is
                    -- PER ROW, not per version: on an old-convention
                    -- whole-file link (pos1 = len-1) a coding including
                    -- the file's last character is excluded; on a
                    -- pos1 = len row it is included
                    AND ct.pos0 >= cs.pos0 AND ct.pos1 <= cs.pos1
                JOIN cases c ON cs.caseid = c.caseid
                WHERE ct.cid = ?{owner_sql}
                GROUP BY cs.caseid, c.name, c.memo
                ORDER BY occurrence_count DESC
            """, params)

            cases = []
            for row in cursor.fetchall():
                cases.append({
                    "case_id": row["caseid"],
                    "case_name": row["case_name"],
                    "memo": row["memo"] or "",
                    "occurrence_count": row["occurrence_count"]
                })

            return cases
        except sqlite3.Error as e:
            _raise_query_error(e, "get_cases_by_code", "Failed to get cases by code")

    # ============================================================================
    # GUID Management for REFI-QDA Export
    # ============================================================================

    def generate_deterministic_guid(self, entity_type: str, entity_id: Union[int, str]) -> str:
        """Generate consistent GUID for Qualcoder entities.

        Uses UUID v5 (namespace-based) to generate deterministic GUIDs that
        will be consistent across multiple exports for the same entity.

        Args:
            entity_type: Type of entity ("code", "file", "user", "coding", "case")
            entity_id: The ID or name of the entity

        Returns:
            UUID string that will be consistent for this entity
        """
        # Create a namespace UUID from the project path
        # This ensures GUIDs are unique to this project
        project_hash = hashlib.sha256(str(self.db_path).encode()).hexdigest()[:32]

        # Format as valid UUID
        namespace_str = f"{project_hash[:8]}-{project_hash[8:12]}-{project_hash[12:16]}-{project_hash[16:20]}-{project_hash[20:32]}"
        namespace = uuid.UUID(namespace_str)

        # Generate UUID v5 based on entity type and ID
        entity_string = f"{entity_type}_{entity_id}"
        return str(uuid.uuid5(namespace, entity_string))

    def get_code_guids(self) -> Dict[int, str]:
        """Get mapping of code_id -> GUID for all codes.

        Returns:
            Dict mapping code_id (cid) to GUID (UUID string)
        """
        try:
            cursor = self.conn.execute("SELECT cid FROM code_name")
            guids = {}
            for row in cursor.fetchall():
                code_id = row["cid"]
                guids[code_id] = self.generate_deterministic_guid("code", code_id)
            return guids
        except sqlite3.Error as e:
            _raise_query_error(e, "get_code_guids", "Failed to get code GUIDs")

    def get_file_guids(self) -> Dict[int, str]:
        """Get mapping of file_id -> GUID for all source files.

        Returns:
            Dict mapping file_id (id) to GUID (UUID string)
        """
        try:
            cursor = self.conn.execute("SELECT id FROM source")
            guids = {}
            for row in cursor.fetchall():
                file_id = row["id"]
                guids[file_id] = self.generate_deterministic_guid("file", file_id)
            return guids
        except sqlite3.Error as e:
            _raise_query_error(e, "get_file_guids", "Failed to get file GUIDs")

    def get_case_guids(self) -> Dict[int, str]:
        """Get mapping of case_id -> GUID for all cases.

        Returns:
            Dict mapping case_id (caseid) to GUID (UUID string)
        """
        try:
            cursor = self.conn.execute("SELECT caseid FROM cases")
            guids = {}
            for row in cursor.fetchall():
                case_id = row["caseid"]
                guids[case_id] = self.generate_deterministic_guid("case", case_id)
            return guids
        except sqlite3.Error as e:
            _raise_query_error(e, "get_case_guids", "Failed to get case GUIDs")

    def get_or_create_user_guid(self, username: str) -> str:
        """Get or create GUID for a user.

        Args:
            username: The coder name

        Returns:
            UUID string for this user
        """
        return self.generate_deterministic_guid("user", username)

    # ============================================================================
    # WRITE OPERATIONS
    # ============================================================================
    # These methods modify the database. Users should work on project copies
    # in the MCP workspace (~/Documents/Qualcoder MCP Projects/)

    def _require_write_access(self) -> None:
        """Check that database was opened with write access on a v14 schema.

        Writes are only supported against the tested v14 schema (QualCoder
        3.8.x). Older versions may connect for reading, but pre-v14 schemas
        differ in ways that make writes unsafe (e.g. pre-v4 lacks the
        code_text unique constraint, silently losing duplicate protection).

        Raises:
            RuntimeError: If database is in read-only mode
            UnsupportedSchemaError: If the schema is older than v14
        """
        supported, reason, _overridden = self.write_support()
        if not supported:
            raise UnsupportedSchemaError(reason)
        if self.read_only:
            raise RuntimeError(
                "Database is in read-only mode. To modify data, reopen with "
                "read_only=False. Write operations should only be performed "
                "on project copies in the MCP workspace."
            )

    def add_coding(
        self,
        file_id: int,
        code_id: int,
        start_pos: int,
        end_pos: int,
        selected_text: str,
        owner: str,
        memo: Optional[str] = None,
        important: Optional[int] = None,
        auto_commit: bool = True
    ) -> int:
        """Add a new coding to a text segment.

        Args:
            file_id: ID of the file being coded
            code_id: ID of the code being applied
            start_pos: Starting character position
            end_pos: Ending character position
            selected_text: The actual text being coded
            owner: Name of the coder (e.g., "AI Coding Assistant")
            memo: Optional memo explaining the coding
            important: Importance flag - stored as 1 or NULL
                       (QualCoder's domain is {NULL, 1}, never 0)
            auto_commit: Commit after insert (default True). Set False for batch operations.

        Returns:
            The ctid (coding ID) of the newly created coding

        Raises:
            ValueError: If validation fails
            RuntimeError: If database is read-only or operation fails
        """
        self._require_write_access()
        # Validate inputs
        file_id = validate_id(file_id, "file_id")
        code_id = validate_id(code_id, "code_id")

        if not isinstance(start_pos, int) or start_pos < 0:
            raise ValueError(f"start_pos must be non-negative integer, got {start_pos}")

        if not isinstance(end_pos, int) or end_pos <= start_pos:
            raise ValueError(f"end_pos must be greater than start_pos ({start_pos}), got {end_pos}")

        if not owner or not isinstance(owner, str):
            raise ValueError("owner must be a non-empty string")

        # Verify file exists
        file_check = self.conn.execute("SELECT id FROM source WHERE id = ?", (file_id,)).fetchone()
        if not file_check:
            raise ValueError(f"File ID {file_id} does not exist")

        # Verify code exists
        code_check = self.conn.execute("SELECT cid FROM code_name WHERE cid = ?", (code_id,)).fetchone()
        if not code_check:
            raise ValueError(f"Code ID {code_id} does not exist")

        # Get file content and enforce write invariants:
        # - text codings only on text sources that have text content (QA F6:
        #   junk codings on image/audio/video sources were accepted silently)
        # - positions in range, and selected_text must equal the file text at
        #   [start_pos:end_pos] (QA F7): QualCoder renders highlights from the
        #   positions, so a mismatch makes the project display the wrong text
        file_content = self.get_file_content(file_id)
        fulltext = (file_content or {}).get("content") or ""
        if not fulltext or not (file_content or {}).get("is_text"):
            raise ValueError(
                f"File ID {file_id} is not a text source with text content - "
                f"text codings can only be added to text files"
            )
        content_length = len(fulltext)
        if end_pos > content_length:
            raise ValueError(f"end_pos ({end_pos}) exceeds file length ({content_length})")
        actual_text = fulltext[start_pos:end_pos]
        if actual_text != selected_text:
            def _snip(t: str) -> str:
                return t if len(t) <= 80 else t[:80] + "…"
            raise ValueError(
                f"selected_text does not match the file text at positions "
                f"{start_pos}-{end_pos}. File contains: '{_snip(actual_text)}' "
                f"- provided: '{_snip(selected_text)}'"
            )

        # Current timestamp
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Memo privacy ('#####'): a fresh AI-written memo is reduced to its
        # public part, so an AI write can never create a private zone
        # (upstream applies _memo_update_text on every create)
        memo = extract_ai_memo(memo or "")

        try:
            cursor = self.conn.execute("""
                INSERT INTO code_text (cid, fid, seltext, pos0, pos1, owner, date, memo, important)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (code_id, file_id, selected_text, start_pos, end_pos, owner, date_str, memo,
                  1 if important else None))

            if auto_commit:
                self.conn.commit()

            ctid = cursor.lastrowid
            logger.info(f"Added coding: ctid={ctid}, file={file_id}, code={code_id}, pos={start_pos}-{end_pos}")
            return ctid

        except sqlite3.IntegrityError as e:
            self.conn.rollback()
            # Check for unique constraint violation
            if "unique" in str(e).lower():
                raise ValueError(f"Coding already exists at this position for this user") from None
            raise RuntimeError(f"Failed to add coding: {e}") from None
        except sqlite3.Error as e:
            self.conn.rollback()
            logger.error(f"Database error in add_coding: {e}")
            raise RuntimeError(f"Failed to add coding: {e}") from None

    def add_code(
        self,
        name: str,
        owner: str,
        memo: Optional[str] = None,
        category_id: Optional[int] = None,
        color: Optional[str] = None,
        parent_code_id: Optional[int] = None,
        auto_commit: bool = True
    ) -> int:
        """Add a new code to the project.

        Args:
            name: Code name (must be unique)
            owner: Name of the person creating the code
            memo: Optional description/definition of the code
            category_id: Optional category ID to place code in
            color: Hex color code #RRGGBB (default: random pick from
                   QualCoder's own palette, like GUI-created codes)
            auto_commit: Commit immediately (default True). Pass False to
                         defer the commit to the caller (batch/lock recheck).

        Returns:
            The cid (code ID) of the newly created code

        Raises:
            ValueError: If validation fails
            RuntimeError: If database is read-only or operation fails
        """
        self._require_write_access()
        # Strip and reject whitespace-only names (QA5-3), consistent with
        # add_category/rename_code/rename_category
        if not name or not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty string")
        name = name.strip()
        validate_string(name, "name")

        if not owner or not isinstance(owner, str):
            raise ValueError("owner must be a non-empty string")

        # Validate category if provided
        if category_id is not None:
            category_id = validate_id(category_id, "category_id")
            cat_check = self.conn.execute(
                "SELECT catid FROM code_cat WHERE catid = ?", (category_id,)
            ).fetchone()
            if not cat_check:
                raise ValueError(f"Category ID {category_id} does not exist")

        # Sub-code creation (S10, v16+ only): parent code XOR category,
        # mirroring upstream's own MCP contract (ai_mcp_server.py:963-975,
        # 1480-1525; GUI insert code_tree.py:753-756)
        if parent_code_id is not None:
            if category_id is not None:
                raise ValueError(
                    "A code cannot have both a parent code and a category; "
                    "give parent_code_id or category_id, not both")
            caps = getattr(self, "capabilities", None)
            if caps is None or not caps.has_supercid:
                raise ValueError(
                    "Sub-codes need a project with schema v16 or newer "
                    "(the code_name.supercid column); this project does "
                    "not have it. Open the project in a QualCoder version "
                    "that supports sub-codes to migrate it first.")
            parent_code_id = validate_id(parent_code_id, "parent_code_id")
            parent_check = self.conn.execute(
                "SELECT cid FROM code_name WHERE cid = ?", (parent_code_id,)
            ).fetchone()
            if not parent_check:
                raise ValueError(
                    f"Parent code ID {parent_code_id} does not exist")

        # Default to a random QualCoder palette color (what the GUI does);
        # validate strictly - '#zzzzzz' passed the old prefix/length check
        # but renders black/undefined in QualCoder's QColor/luminance math
        if color is None:
            color = random.choice(QUALCODER_COLORS)
        if not isinstance(color, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
            raise ValueError(f"color must be hex format #RRGGBB, got {color}")

        # Current timestamp
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Memo privacy ('#####'): new memos are public-text only (see
        # add_coding)
        memo = extract_ai_memo(memo or "")

        try:
            if parent_code_id is not None:
                cursor = self.conn.execute("""
                    INSERT INTO code_name
                        (name, memo, catid, owner, date, color, supercid)
                    VALUES (?, ?, NULL, ?, ?, ?, ?)
                """, (name, memo, owner, date_str, color,
                      parent_code_id))
            else:
                cursor = self.conn.execute("""
                    INSERT INTO code_name (name, memo, catid, owner, date, color)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (name, memo, category_id, owner, date_str, color))

            if auto_commit:
                self.conn.commit()

            cid = cursor.lastrowid
            logger.info(f"Added code: cid={cid}, name={name}, category={category_id}")
            return cid

        except sqlite3.IntegrityError as e:
            self.conn.rollback()
            if "unique" in str(e).lower():
                raise ValueError(f"Code name '{name}' already exists") from None
            raise RuntimeError(f"Failed to add code: {e}") from None
        except sqlite3.Error as e:
            self.conn.rollback()
            logger.error(f"Database error in add_code: {e}")
            raise RuntimeError(f"Failed to add code: {e}") from None

    def add_memo_to_coding(self, coding_id: int, memo: str, owner: str) -> None:
        """Add or update memo on an existing coding.

        Args:
            coding_id: The ctid of the coding
            memo: Memo text to add
            owner: Name of person adding memo

        Raises:
            ValueError: If validation fails
            RuntimeError: If database is read-only or operation fails
        """
        self._require_write_access()
        coding_id = validate_id(coding_id, "coding_id")

        if not isinstance(memo, str):
            raise ValueError("memo must be a string")

        # Verify coding exists (and read the memo for the privacy merge)
        coding_check = self.conn.execute(
            "SELECT ctid, memo FROM code_text WHERE ctid = ?", (coding_id,)
        ).fetchone()
        if not coding_check:
            raise ValueError(f"Coding ID {coding_id} does not exist")

        # Memo privacy ('#####'): preserve any private suffix, drop any
        # marker in the AI-provided text (see set_memo)
        stored_memo = merge_public_memo(coding_check["memo"], memo)

        try:
            # Content-only: QualCoder's coded-text memo edit updates ONLY
            # memo, never date or owner (code_text.py:1636 /
            # code_in_all_files.py:399; memos-journals.md §2.4, gotcha #1).
            # The previous version stamped date — a fingerprint that mutated
            # the coding's "coded on" timestamp.
            self.conn.execute(
                "UPDATE code_text SET memo = ? WHERE ctid = ?",
                (stored_memo, coding_id)
            )

            self.conn.commit()
            logger.info(f"Updated memo for coding {coding_id}")

        except sqlite3.Error as e:
            self.conn.rollback()
            logger.error(f"Database error in add_memo_to_coding: {e}")
            raise RuntimeError(f"Failed to update memo: {e}") from None

    def get_coding(self, coding_id: int) -> Optional[Dict[str, Any]]:
        """Get a single coded segment (code_text row) by its ctid.

        Args:
            coding_id: The ctid of the coding

        Returns:
            Coding details with code and file names, or None if not found
        """
        coding_id = validate_id(coding_id, "coding_id")
        try:
            row = self.conn.execute("""
                SELECT
                    ct.ctid, ct.cid, ct.fid, ct.seltext, ct.pos0, ct.pos1,
                    ct.owner, ct.date, ct.memo, ct.important,
                    c.name as code_name,
                    s.name as file_name
                FROM code_text ct
                LEFT JOIN code_name c ON ct.cid = c.cid
                LEFT JOIN source s ON ct.fid = s.id
                WHERE ct.ctid = ?
            """, (coding_id,)).fetchone()
        except sqlite3.Error as e:
            _raise_query_error(e, "get_coding", "Failed to retrieve coding")

        if not row:
            return None
        return {
            "coding_id": row["ctid"],
            "code_id": row["cid"],
            "code_name": row["code_name"],
            "file_id": row["fid"],
            "file_name": row["file_name"],
            "text": row["seltext"],
            "position_start": row["pos0"],
            "position_end": row["pos1"],
            "memo": row["memo"] or "",
            "owner": row["owner"],
            "date": row["date"],
            "important": bool(row["important"]),
        }

    def delete_coding(self, coding_id: int, auto_commit: bool = True) -> Dict[str, Any]:
        """Delete a single coded segment (code_text row).

        This removes ONE coding (the assignment of a code to a text span),
        never the code itself or the source file.

        Args:
            coding_id: The ctid of the coding to delete
            auto_commit: Commit immediately (default True). Pass False when
                         the caller wants to re-check preconditions (e.g.
                         the QualCoder lock file) before committing.

        Returns:
            The details of the deleted coding. When the row belongs to a
            coder the project hides (QC 4.0 visibility, P1-3), only its
            ids come back, matching upstream's ids-only echo
            (ai_mcp_server.py:2243-2280): owner, code name, span, text
            and memo of a hidden coder never enter the conversation.

        Raises:
            ValueError: If the coding does not exist
            RuntimeError: If database is read-only or the delete fails
        """
        self._require_write_access()
        existing = self.get_coding(coding_id)
        if existing is None:
            raise ValueError(f"Coding ID {coding_id} does not exist")
        visible = self.coding_is_visible(coding_id)

        try:
            self.conn.execute(
                "DELETE FROM code_text WHERE ctid = ?", (coding_id,)
            )
            if auto_commit:
                self.conn.commit()
            logger.info(f"Deleted coding ctid={coding_id}")
        except sqlite3.Error as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            _raise_query_error(e, "delete_coding", "Failed to delete coding")

        if not visible:
            return {
                "coding_id": existing["coding_id"],
                "code_id": existing["code_id"],
                "file_id": existing["file_id"],
                "hidden_coder_row": True,
            }
        return existing

    def validate_text_file_import(
        self,
        name: str,
        content: str,
        owner: str,
        memo: str = ""
    ) -> str:
        """Validate inputs for import_text_file without writing anything.

        Safe to call on a read-only connection. The server calls this BEFORE
        upgrading to read-write and creating a backup, so rejected imports
        never produce a full-project backup copy (SEC D-2).

        Args:
            name: Filename with extension
            content: Full text content
            owner: Creator name
            memo: Optional file memo

        Returns:
            The normalized (NFC, stripped) filename to store

        Raises:
            ValueError: If any input is invalid or the filename exists
            TypeError: If content is not a string
        """
        # Validate name
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty string")
        # Normalize so visually identical names compare equal (SEC D-1)
        name = unicodedata.normalize("NFC", name.strip())
        # Reject NUL and other control characters: they bypass both the
        # duplicate pre-check and the UNIQUE(name) constraint while
        # displaying as an existing filename (SEC D-1)
        if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in name):
            raise ValueError("filename must not contain control characters")
        if '.' not in name:
            raise ValueError("filename must have an extension (e.g., .txt)")
        if '/' in name or '\\' in name or '..' in name:
            raise ValueError("filename must not contain path separators or '..'")
        validate_string(name, "name")

        # Validate content
        if not isinstance(content, str):
            raise TypeError("content must be a string")
        if not content.strip():
            raise ValueError("content must not be empty")
        # Emptiness must also hold AFTER the BOM/CRLF normalization the
        # write path applies: U+FEFF is not stripped by str.strip(), so
        # BOM-only content previously passed validation and produced an
        # empty, uncodable source (QA2-1)
        normalized = content[1:] if content.startswith("\ufeff") else content
        if not normalized.strip():
            raise ValueError(
                "content must not be empty (it contains only a byte-order "
                "mark and/or whitespace)"
            )
        if len(content) > MAX_TEXT_CONTENT_LENGTH:
            raise ValueError(
                f"content length {len(content)} exceeds maximum "
                f"{MAX_TEXT_CONTENT_LENGTH}"
            )

        # Validate owner: the same rules as the configured AI coder name
        # (defense in depth behind the server's check, S-H3)
        validate_coder_name(owner, "owner")

        # Validate memo
        if memo:
            validate_string(memo, "memo")

        # Check name uniqueness
        try:
            existing = self.conn.execute(
                "SELECT id FROM source WHERE name = ?", (name,)
            ).fetchone()
        except sqlite3.Error as e:
            _raise_query_error(e, "validate_text_file_import",
                               "Failed to validate import")
        if existing:
            raise ValueError(
                f"A file named '{name}' already exists (id={existing['id']})"
            )

        return name

    def import_text_file(
        self,
        name: str,
        content: str,
        owner: str,
        memo: str = "",
        auto_commit: bool = True
    ) -> Dict[str, Any]:
        """Import text content as a new source file in the QualCoder project.

        Creates a new source record with mediapath=NULL, matching QualCoder's
        "create text file" behavior. Also creates attribute placeholders for
        any existing file-type attribute types.

        Args:
            name: Filename with extension (e.g., "interview_04.txt")
            content: Full text content of the file
            owner: Creator name for attribution
            memo: Optional description/memo for the file
            auto_commit: Whether to commit immediately (default True)

        Returns:
            Dict with id, name, content_length, owner, date, attributes_created

        Raises:
            RuntimeError: If database is read-only or write fails
            ValueError: If inputs are invalid or filename already exists
            TypeError: If content is not a string
        """
        self._require_write_access()

        # Full validation (raises on any problem); returns the normalized name
        name = self.validate_text_file_import(name, content, owner, memo)

        # Normalize the text the way QualCoder's own import pipeline leaves
        # it: strip one leading BOM (manage_files.py:2015-2016) and store
        # LF-only newlines (every converter path emits \n, and QualCoder's
        # editor rewrites CRLF to \n on any in-app edit). CRLF content
        # would otherwise create a file where GUI and code-point positions
        # diverge from birth (text-positions.md RISK-TP2).
        if content.startswith("\ufeff"):
            content = content[1:]
        # Master normalizes BOTH \r\n and lone \r on every non-PDF text
        # import (manage_files.py:3337-3343); upstream parity (P7/T13)
        content = content.replace("\r\n", "\n").replace("\r", "\n")

        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Memo privacy ('#####'): new memos are public-text only (see
        # add_coding). The file CONTENT is untouched: fulltext is data,
        # not a memo.
        memo = extract_ai_memo(memo or "")

        try:
            cursor = self.conn.execute("""
                INSERT INTO source (name, fulltext, mediapath, memo, owner, date)
                VALUES (?, ?, NULL, ?, ?, ?)
            """, (name, content, memo, owner, date_str))
            file_id = cursor.lastrowid

            # Create attribute placeholders for file-type attribute types —
            # driven by caseOrFile='file' exactly like QualCoder's own file
            # writers (manage_files.py:1387-1392). The real domain set is
            # case|file|journal; 'both' does not exist (cases-attributes.md
            # §1), so the old IN ('file','both') superset was wrong.
            attr_types = self.conn.execute(
                "SELECT name FROM attribute_type WHERE caseOrFile = 'file'"
            ).fetchall()
            for attr_type_row in attr_types:
                self.conn.execute("""
                    INSERT INTO attribute (name, attr_type, value, id, date, owner)
                    VALUES (?, 'file', '', ?, ?, ?)
                """, (attr_type_row["name"], file_id, date_str, owner))

            if auto_commit:
                self.conn.commit()

            logger.info(
                f"Imported text file: id={file_id}, name={name}, "
                f"length={len(content)}"
            )
            return {
                "id": file_id,
                "name": name,
                "content_length": len(content),
                "owner": owner,
                "date": date_str,
                "attributes_created": len(attr_types)
            }

        except sqlite3.IntegrityError as e:
            self.conn.rollback()
            if "unique" in str(e).lower():
                raise ValueError(
                    f"A file named '{name}' already exists"
                ) from None
            raise RuntimeError(f"Failed to import text file: {e}") from None
        except sqlite3.Error as e:
            self.conn.rollback()
            logger.error(f"Database error in import_text_file: {e}")
            raise RuntimeError(f"Failed to import text file: {e}") from None

    def link_file_to_case(
        self,
        case_id: int,
        file_id: int,
        owner: str,
        auto_commit: bool = True
    ) -> Dict[str, Any]:
        """Link a whole source file to a case (QualCoder case_text row).

        Writes one case_text row with pos0=0 and pos1=len(fulltext) for
        text sources (the convention QualCoder master unified on, and the
        one 3.8.2's file manager already used), or pos0=pos1=0 for
        non-text sources. 3.8.2's case file manager wrote len-1; the
        duplicate check treats both spellings as the same link. Without
        this row the file is invisible to every case-based analysis.

        case_text has NO unique constraint, so the duplicate check here is
        the only protection against double-linking (matching QualCoder's
        app-side check).

        Args:
            case_id: The case to link to
            file_id: The source file to link
            owner: Coder name for attribution
            auto_commit: Commit immediately (default True)

        Returns:
            Dict with case/file names and the linked span

        Raises:
            ValueError: If the case or file doesn't exist, or the link
                        already exists
            RuntimeError: If database is read-only or the insert fails
        """
        self._require_write_access()
        case_id = validate_id(case_id, "case_id")
        file_id = validate_id(file_id, "file_id")
        if not owner or not isinstance(owner, str) or not owner.strip():
            raise ValueError("owner must be a non-empty string")

        try:
            case_row = self.conn.execute(
                "SELECT caseid, name FROM cases WHERE caseid = ?", (case_id,)
            ).fetchone()
            file_row = self.conn.execute(
                "SELECT id, name, fulltext FROM source WHERE id = ?", (file_id,)
            ).fetchone()
        except sqlite3.Error as e:
            _raise_query_error(e, "link_file_to_case", "Failed to link file to case")

        if not case_row:
            raise ValueError(f"Case ID {case_id} does not exist")
        if not file_row:
            raise ValueError(f"File ID {file_id} does not exist")

        # Whole-file span. Write convention standardized on
        # pos1 = len(fulltext): master's unified choice
        # (case_file_manager.py:208-210) and already 3.8.2's own file
        # manager convention (3.8.2:manage_files.py:755-764). The old
        # 3.8.2 case-manager convention (len-1) coexists in real data;
        # the duplicate pre-check below treats both as the same link.
        fulltext = file_row["fulltext"]
        pos0 = 0
        pos1 = len(fulltext) if fulltext else 0

        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            # Overlap-aware duplicate check (cases-attributes.md §2.6):
            # upstream has TWO conflicting whole-file conventions — the case
            # file manager writes pos1 = len(fulltext)-1, while Manage Files
            # "Assign case" and survey import write pos1 = len(fulltext) —
            # and each GUI path's own probe only matches its own convention,
            # silently double-linking across paths. case_text has NO unique
            # constraint, so this app-side probe is the only protection:
            # treat ANY existing row that already covers the whole file
            # (either convention, or a superset span) as a duplicate.
            # (0, len-1) and (0, len) are the SAME whole-file link (W12):
            # the threshold is the smaller convention so either spelling,
            # from any QualCoder version or this server, is caught.
            dedupe_floor = max(0, pos1 - 1)
            existing = self.conn.execute(
                "SELECT id, pos0, pos1 FROM case_text WHERE caseid = ? "
                "AND fid = ? AND pos0 <= 0 AND pos1 >= ?",
                (case_id, file_id, dedupe_floor)
            ).fetchone()
            if existing:
                convention = ""
                if (existing["pos0"], existing["pos1"]) != (pos0, pos1):
                    convention = (
                        f" (existing span {existing['pos0']}-"
                        f"{existing['pos1']}, QualCoder's other whole-file "
                        f"convention)"
                    )
                raise ValueError(
                    f"File '{file_row['name']}' is already linked to case "
                    f"'{case_row['name']}'{convention}"
                )

            self.conn.execute(
                "INSERT INTO case_text (caseid, fid, pos0, pos1, owner, date, memo) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (case_id, file_id, pos0, pos1, owner, date_str, "")
            )
            if auto_commit:
                self.conn.commit()
            logger.info(f"Linked file {file_id} to case {case_id} ({pos0}-{pos1})")
        except ValueError:
            raise
        except sqlite3.Error as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            _raise_query_error(e, "link_file_to_case", "Failed to link file to case")

        return {
            "case_id": case_id,
            "case_name": case_row["name"],
            "file_id": file_id,
            "file_name": file_row["name"],
            "position_start": pos0,
            "position_end": pos1,
        }

    # ========================================================================
    # MEMO WRITE OPERATIONS
    # ========================================================================
    # Memo-bearing objects and their (table, id column, name column). Every
    # target row uses a `memo TEXT` column that QualCoder stores as '' (empty
    # string), never NULL, on creation (schema-writes.md §2.1/§3). Clearing a
    # memo therefore stores '' to match.
    _MEMO_TARGETS = {
        "code": ("code_name", "cid", "name"),
        "category": ("code_cat", "catid", "name"),
        "file": ("source", "id", "name"),
        "coding": ("code_text", "ctid", None),
        "case": ("cases", "caseid", "name"),
    }

    def set_memo(
        self,
        target_type: str,
        target_id: int,
        memo: str,
        auto_commit: bool = True
    ) -> Dict[str, Any]:
        """Set (or clear) the memo on a memo-bearing object.

        Args:
            target_type: One of 'code', 'category', 'file', 'coding', 'case'
            target_id: The row id (cid/catid/source id/ctid/caseid)
            memo: The memo text. '' clears it (QualCoder's empty-string
                  convention — memos are never NULL).
            auto_commit: Commit immediately (default True)

        Returns:
            Dict describing the updated object

        Raises:
            ValueError: If target_type/id is invalid or the row doesn't exist
            RuntimeError: If database is read-only or the update fails
        """
        self._require_write_access()
        if target_type not in self._MEMO_TARGETS:
            raise ValueError(
                f"target_type must be one of: "
                f"{', '.join(sorted(self._MEMO_TARGETS))}"
            )
        target_id = validate_id(target_id, "target_id")
        if not isinstance(memo, str):
            raise ValueError("memo must be a string")
        # Reject over-length rather than silently truncate (memos have no
        # length limit in QualCoder; validate_string would truncate — a
        # silent corruption of the user's note, memos-journals.md §6.7/#8)
        _reject_if_too_long(memo, "memo")

        table, id_col, name_col = self._MEMO_TARGETS[target_type]

        # Verify the row exists, capture a label for the confirmation, and
        # read the existing memo for the privacy-preserving merge
        select_cols = (f"{name_col}, memo" if name_col else f"{id_col}, memo")
        try:
            row = self.conn.execute(
                f"SELECT {select_cols} FROM {table} WHERE {id_col} = ?",
                (target_id,)
            ).fetchone()
        except sqlite3.Error as e:
            _raise_query_error(e, "set_memo", "Failed to set memo")
        if not row:
            raise ValueError(
                f"{target_type} with id {target_id} does not exist"
            )
        label = row[0] if name_col else f"{target_type} {target_id}"

        # Memo privacy (QC 4.0 '#####' convention): replace only the
        # AI-visible public text; an existing private suffix survives
        # verbatim, and a marker in the NEW text is dropped (an AI write
        # can never create, read, replace, or delete the private zone).
        # Mirrors upstream ai_memo.merge_public_memo semantics exactly.
        stored_memo = merge_public_memo(row["memo"], memo)
        public_memo = extract_ai_memo(stored_memo)

        # Content-only: QualCoder's memo edits for code_name/code_cat/source/
        # code_text/cases touch ONLY the memo column — never date, never
        # owner (memos-journals.md §2, §5.1; the summary table). '' clears
        # the memo, matching QualCoder's empty-string convention (never NULL).
        # (code_av is the one date-on-edit exception upstream, but it is not
        # one of these targets.)
        try:
            self.conn.execute(
                f"UPDATE {table} SET memo = ? WHERE {id_col} = ?",
                (stored_memo, target_id)
            )
            if auto_commit:
                self.conn.commit()
            logger.info(f"Set memo on {target_type} {target_id}")
        except sqlite3.Error as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            _raise_query_error(e, "set_memo", "Failed to set memo")

        # The result echoes only the public text (silent strip): echoing
        # the stored memo would hand the private suffix back to the AI.
        return {
            "target_type": target_type,
            "target_id": target_id,
            "label": label,
            "memo": public_memo,
            "cleared": public_memo == "",
        }

    def add_journal_entry(
        self,
        name: str,
        entry: str,
        owner: str,
        auto_commit: bool = True
    ) -> Dict[str, Any]:
        """Add a research journal entry.

        Args:
            name: Journal entry name/title (must be unique — journal has
                  unique(name))
            entry: The journal text (jentry)
            owner: Coder name for attribution
            auto_commit: Commit immediately (default True)

        Returns:
            Dict with the new entry's id, name and date

        Raises:
            ValueError: If validation fails or the name already exists
            RuntimeError: If database is read-only or the insert fails
        """
        self._require_write_access()
        if not name or not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty string")
        name = name.strip()
        # QualCoder's journal-name charset: letters, digits, underscore,
        # space, hyphen (journals.py:607; memos-journals.md §6.4). Enforce it
        # so MCP journals are GUI-editable.
        if not JOURNAL_NAME_RE.match(name):
            raise ValueError(
                "journal name may contain only letters, digits, spaces, "
                "underscores and hyphens"
            )
        _reject_if_too_long(name, "name", max_length=MAX_STRING_LENGTH)
        if not isinstance(entry, str):
            raise ValueError("entry must be a string")
        # Journals routinely exceed 10k chars; reject over-length rather than
        # silently truncate (memos-journals.md §6.7)
        _reject_if_too_long(entry, "entry")
        # Memo privacy ('#####'): journal entries follow the same
        # convention as memos on read, so AI-written entries are reduced
        # to their public part too - the AI can never author journal text
        # it cannot later read back
        entry = extract_ai_memo(entry)
        if not owner or not isinstance(owner, str) or not owner.strip():
            raise ValueError("owner must be a non-empty string")

        # App-side duplicate pre-check (journal has unique(name) in the real
        # v14 schema; this also protects on schemas that lack the constraint)
        try:
            existing = self.conn.execute(
                "SELECT jid FROM journal WHERE name = ?", (name,)
            ).fetchone()
        except sqlite3.Error as e:
            _raise_query_error(e, "add_journal_entry",
                               "Failed to add journal entry")
        if existing:
            raise ValueError(f"A journal entry named '{name}' already exists")

        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            cursor = self.conn.execute(
                "INSERT INTO journal (name, jentry, date, owner) "
                "VALUES (?, ?, ?, ?)",
                (name, entry, date_str, owner)
            )
            if auto_commit:
                self.conn.commit()
            jid = cursor.lastrowid
            logger.info(f"Added journal entry: jid={jid}, name={name}")
        except sqlite3.IntegrityError as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            if "unique" in str(e).lower():
                raise ValueError(
                    f"A journal entry named '{name}' already exists"
                ) from None
            raise RuntimeError(f"Failed to add journal entry: {e}") from None
        except sqlite3.Error as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            _raise_query_error(e, "add_journal_entry",
                               "Failed to add journal entry")

        return {"id": jid, "name": name, "date": date_str, "owner": owner}

    # ========================================================================
    # CODEBOOK WRITE OPERATIONS (non-destructive)
    # ========================================================================

    def _get_code_row(self, code_id: int):
        row = self.conn.execute(
            "SELECT cid, name, catid, color FROM code_name WHERE cid = ?",
            (code_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Code ID {code_id} does not exist")
        return row

    def _get_category_row(self, category_id: int):
        row = self.conn.execute(
            "SELECT catid, name, supercatid FROM code_cat WHERE catid = ?",
            (category_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Category ID {category_id} does not exist")
        return row

    def rename_code(self, code_id: int, new_name: str,
                    auto_commit: bool = True) -> Dict[str, Any]:
        """Rename a code (code_name.name — unique among codes)."""
        self._require_write_access()
        code_id = validate_id(code_id, "code_id")
        if not new_name or not isinstance(new_name, str) or not new_name.strip():
            raise ValueError("new_name must be a non-empty string")
        new_name = new_name.strip()
        validate_string(new_name, "new_name")
        try:
            old = self._get_code_row(code_id)
            # Pre-check the unique(name) collision (code-edits.md gotcha #14):
            # QualCoder relies on an app-side pre-check, not the DB exception.
            clash = self.conn.execute(
                "SELECT cid FROM code_name WHERE name = ? AND cid <> ?",
                (new_name, code_id)
            ).fetchone()
            if clash:
                raise ValueError(f"A code named '{new_name}' already exists")
            self.conn.execute(
                "UPDATE code_name SET name = ? WHERE cid = ?",
                (new_name, code_id)
            )
            if auto_commit:
                self.conn.commit()
            logger.info(f"Renamed code {code_id}: '{old['name']}' -> '{new_name}'")
        except sqlite3.IntegrityError as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            if "unique" in str(e).lower():
                raise ValueError(f"A code named '{new_name}' already exists") from None
            raise RuntimeError(f"Failed to rename code: {e}") from None
        except sqlite3.Error as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            _raise_query_error(e, "rename_code", "Failed to rename code")
        return {"code_id": code_id, "old_name": old["name"], "new_name": new_name}

    def recolor_code(self, code_id: int, color: str,
                     auto_commit: bool = True) -> Dict[str, Any]:
        """Set a code's color (strict #RRGGBB, QualCoder's format)."""
        self._require_write_access()
        code_id = validate_id(code_id, "code_id")
        if not isinstance(color, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
            raise ValueError(f"color must be hex format #RRGGBB, got {color}")
        try:
            old = self._get_code_row(code_id)
            self.conn.execute(
                "UPDATE code_name SET color = ? WHERE cid = ?", (color, code_id)
            )
            if auto_commit:
                self.conn.commit()
            logger.info(f"Recolored code {code_id} -> {color}")
        except sqlite3.Error as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            _raise_query_error(e, "recolor_code", "Failed to recolor code")
        return {"code_id": code_id, "name": old["name"],
                "old_color": old["color"], "new_color": color}

    # ------------------------------------------------------------------
    # Sub-code hierarchy helpers (v16+ code_name.supercid; master parity)
    # ------------------------------------------------------------------

    def _supercid_children(self) -> Dict[int, List[int]]:
        """parent cid -> child cids map; empty when supercid is absent."""
        caps = getattr(self, "capabilities", None)
        if caps is None or not caps.has_supercid:
            return {}
        children: Dict[int, List[int]] = {}
        for row in self.conn.execute(
                "SELECT cid, supercid FROM code_name "
                "WHERE supercid IS NOT NULL").fetchall():
            children.setdefault(row[1], []).append(row[0])
        return children

    def code_is_descendant(self, candidate_cid: int,
                           ancestor_cid: int) -> bool:
        """True if candidate is the ancestor itself or one of its
        transitive sub-codes (upstream code_tree.py:619-641; cycle-safe)."""
        if candidate_cid == ancestor_cid:
            return True
        children = self._supercid_children()
        stack = list(children.get(ancestor_cid, []))
        seen = set()
        while stack:
            cid = stack.pop()
            if cid == candidate_cid:
                return True
            if cid in seen:
                continue
            seen.add(cid)
            stack.extend(children.get(cid, []))
        return False

    def get_branch_cids(self, root_cid: int) -> List[int]:
        """The code plus every transitive sub-code, read fresh from the DB
        (upstream code_tree.py:796-818; iterative, cycle-safe). On schemas
        without supercid this is just [root_cid]."""
        caps = getattr(self, "capabilities", None)
        if caps is None or not caps.has_supercid:
            return [root_cid]
        rows = self.conn.execute(
            "SELECT cid, supercid FROM code_name").fetchall()
        cids = [root_cid]
        i = 0
        while i < len(cids):
            for cid, supercid in rows:
                if supercid == cids[i] and cid not in cids:
                    cids.append(cid)
            i += 1
        return cids

    def _cleanup_graph_rows_for_cid(self, cid: int) -> None:
        """Master-parity saved-graph cleanup after a code disappears
        (code_tree.py:855-858, 1567-1570). Part of the v16+ recipe set:
        gated on the supercid probe so v14/v15 deletes stay byte-exact
        with QualCoder 3.8.2 (which leaves graph rows alone), and
        additionally guarded by table existence."""
        caps = getattr(self, "capabilities", None)
        if caps is None or not caps.has_supercid:
            return
        if caps.table_exists("gr_cdct_text_item"):
            self.conn.execute(
                "DELETE FROM gr_cdct_text_item WHERE cid = ?", (cid,))
        if caps.table_exists("gr_cdct_line_item"):
            self.conn.execute(
                "DELETE FROM gr_cdct_line_item WHERE fromcid = ? OR tocid = ?",
                (cid, cid))
        if caps.table_exists("gr_free_line_item"):
            self.conn.execute(
                "DELETE FROM gr_free_line_item WHERE fromcid = ? OR tocid = ?",
                (cid, cid))

    def move_code_to_category(self, code_id: int,
                              category_id: Optional[int],
                              auto_commit: bool = True) -> Dict[str, Any]:
        """Move a code into a category (or None = uncategorised)."""
        self._require_write_access()
        code_id = validate_id(code_id, "code_id")
        if category_id is not None:
            category_id = validate_id(category_id, "category_id")
        try:
            old = self._get_code_row(code_id)
            if category_id is not None:
                self._get_category_row(category_id)  # existence check
            caps = getattr(self, "capabilities", None)
            if caps is not None and caps.has_supercid:
                # S2: parent pointers are mutually exclusive; every move
                # writes BOTH in one statement (upstream code_tree.py:
                # 1235/1245; open-time repair would otherwise DISCARD our
                # catid on a sub-code, __main__.py:2380-2381)
                self.conn.execute(
                    "UPDATE code_name SET catid = ?, supercid = NULL "
                    "WHERE cid = ?",
                    (category_id, code_id)
                )
            else:
                self.conn.execute(
                    "UPDATE code_name SET catid = ? WHERE cid = ?",
                    (category_id, code_id)
                )
            if auto_commit:
                self.conn.commit()
            logger.info(f"Moved code {code_id} to category {category_id}")
        except sqlite3.Error as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            _raise_query_error(e, "move_code_to_category",
                               "Failed to move code")
        return {"code_id": code_id, "name": old["name"],
                "old_category_id": old["catid"], "new_category_id": category_id}

    def add_category(self, name: str, owner: str,
                     supercatid: Optional[int] = None,
                     memo: Optional[str] = None,
                     auto_commit: bool = True) -> Dict[str, Any]:
        """Create a code category (code_cat)."""
        self._require_write_access()
        if not name or not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty string")
        name = name.strip()
        validate_string(name, "name")
        if not owner or not isinstance(owner, str) or not owner.strip():
            raise ValueError("owner must be a non-empty string")
        if memo:
            validate_string(memo, "memo")
        if supercatid is not None:
            supercatid = validate_id(supercatid, "supercatid")

        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # Memo privacy ('#####'): new memos are public-text only (see
        # add_coding)
        memo = extract_ai_memo(memo or "")
        try:
            if supercatid is not None:
                self._get_category_row(supercatid)  # parent must exist
            cursor = self.conn.execute(
                "INSERT INTO code_cat (name, owner, date, memo, supercatid) "
                "VALUES (?, ?, ?, ?, ?)",
                (name, owner, date_str, memo, supercatid)
            )
            if auto_commit:
                self.conn.commit()
            catid = cursor.lastrowid
            logger.info(f"Added category: catid={catid}, name={name}")
        except sqlite3.IntegrityError as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            if "unique" in str(e).lower():
                raise ValueError(f"A category named '{name}' already exists") from None
            raise RuntimeError(f"Failed to add category: {e}") from None
        except sqlite3.Error as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            _raise_query_error(e, "add_category", "Failed to add category")
        return {"id": catid, "name": name, "supercatid": supercatid}

    def rename_category(self, category_id: int, new_name: str,
                        auto_commit: bool = True) -> Dict[str, Any]:
        """Rename a category (code_cat.name — unique among categories)."""
        self._require_write_access()
        category_id = validate_id(category_id, "category_id")
        if not new_name or not isinstance(new_name, str) or not new_name.strip():
            raise ValueError("new_name must be a non-empty string")
        new_name = new_name.strip()
        validate_string(new_name, "new_name")
        try:
            old = self._get_category_row(category_id)
            # Pre-check the global, case-sensitive unique(name) collision
            # (category-tree.md §2, gotcha #3)
            clash = self.conn.execute(
                "SELECT catid FROM code_cat WHERE name = ? AND catid <> ?",
                (new_name, category_id)
            ).fetchone()
            if clash:
                raise ValueError(f"A category named '{new_name}' already exists")
            self.conn.execute(
                "UPDATE code_cat SET name = ? WHERE catid = ?",
                (new_name, category_id)
            )
            if auto_commit:
                self.conn.commit()
            logger.info(f"Renamed category {category_id}: "
                        f"'{old['name']}' -> '{new_name}'")
        except sqlite3.IntegrityError as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            if "unique" in str(e).lower():
                raise ValueError(f"A category named '{new_name}' already exists") from None
            raise RuntimeError(f"Failed to rename category: {e}") from None
        except sqlite3.Error as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            _raise_query_error(e, "rename_category", "Failed to rename category")
        return {"category_id": category_id, "old_name": old["name"],
                "new_name": new_name}

    def would_create_category_cycle(self, category_id: int,
                                    new_supercatid: Optional[int]) -> bool:
        """Check whether reparenting category_id under new_supercatid cycles.

        QualCoder's coding-tree move guards only the direct self-loop and its
        open-time hygiene never detects cycles (category-tree.md §3a/§5), so a
        reparent can silently make categories and all their codes vanish from
        the tree. This is the full id-based ancestor walk QualCoder lacks
        (category-tree.md §7).
        """
        if new_supercatid is None:
            return False  # top level is always safe
        if new_supercatid == category_id:
            return True  # direct self-loop
        ancestor = new_supercatid
        seen: set = set()
        while ancestor is not None:
            if ancestor == category_id:
                return True  # walking up reaches the moved node -> cycle
            if ancestor in seen:
                return True  # already-corrupt data: treat as unsafe
            seen.add(ancestor)
            row = self.conn.execute(
                "SELECT supercatid FROM code_cat WHERE catid = ?", (ancestor,)
            ).fetchone()
            if row is None:
                return False  # dangling parent -> not a cycle
            ancestor = row[0]
        return False

    def move_category(self, category_id: int,
                      new_supercatid: Optional[int],
                      auto_commit: bool = True) -> Dict[str, Any]:
        """Reparent a category (set supercatid), refusing any cycle.

        new_supercatid=None moves the category to the top level.
        """
        self._require_write_access()
        category_id = validate_id(category_id, "category_id")
        if new_supercatid is not None:
            new_supercatid = validate_id(new_supercatid, "new_supercatid")
        try:
            old = self._get_category_row(category_id)
            if new_supercatid is not None:
                self._get_category_row(new_supercatid)  # parent must exist
            if self.would_create_category_cycle(category_id, new_supercatid):
                raise ValueError(
                    "That move would make the category its own ancestor "
                    "(a cycle), which would hide it and its codes from "
                    "QualCoder's tree — refusing."
                )
            self.conn.execute(
                "UPDATE code_cat SET supercatid = ? WHERE catid = ?",
                (new_supercatid, category_id)
            )
            if auto_commit:
                self.conn.commit()
            logger.info(f"Moved category {category_id} under {new_supercatid}")
        except sqlite3.Error as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            _raise_query_error(e, "move_category", "Failed to move category")
        return {"category_id": category_id, "name": old["name"],
                "old_supercatid": old["supercatid"],
                "new_supercatid": new_supercatid}

    # ---- Destructive codebook ops (preview counts + guarded mutations) ----

    def preview_merge_codes(self, from_code_id: int,
                            into_code_id: int) -> Dict[str, Any]:
        """Count what a merge would move/discard (read-only preview)."""
        from_code_id = validate_id(from_code_id, "from_code_id")
        into_code_id = validate_id(into_code_id, "into_code_id")
        if from_code_id == into_code_id:
            raise ValueError("Cannot merge a code into itself")
        # S3 cycle guard (v16+): merging into one of the source's own
        # descendant sub-codes would orphan the chain (upstream refuses,
        # code_tree.py:1505-1510). No-op on schemas without supercid.
        if self.code_is_descendant(into_code_id, from_code_id):
            raise ValueError(
                "Cannot merge a code into itself or one of its own "
                "sub-codes")
        src = self._get_code_row(from_code_id)
        dest = self._get_code_row(into_code_id)
        text_n = self.conn.execute(
            "SELECT COUNT(*) FROM code_text WHERE cid = ?", (from_code_id,)
        ).fetchone()[0]
        av_n = self.conn.execute(
            "SELECT COUNT(*) FROM code_av WHERE cid = ?", (from_code_id,)
        ).fetchone()[0]
        img_n = self.conn.execute(
            "SELECT COUNT(*) FROM code_image WHERE cid = ?", (from_code_id,)
        ).fetchone()[0]
        # Collisions: source text codings the destination already has at the
        # same (fid,pos0,pos1,owner) — these source rows are DISCARDED
        collisions = self.conn.execute(
            "SELECT COUNT(*) FROM code_text s WHERE s.cid = ? AND EXISTS ("
            "  SELECT 1 FROM code_text d WHERE d.cid = ? AND d.fid = s.fid "
            "  AND d.pos0 = s.pos0 AND d.pos1 = s.pos1 AND d.owner = s.owner)",
            (from_code_id, into_code_id)
        ).fetchone()[0]
        return {
            "from_code": {"id": from_code_id, "name": src["name"]},
            "into_code": {"id": into_code_id, "name": dest["name"]},
            "text_codings_reassigned": text_n - collisions,
            "text_codings_discarded_as_duplicates": collisions,
            "av_codings_reassigned": av_n,
            "image_codings_reassigned": img_n,
        }

    def merge_codes(self, from_code_id: int, into_code_id: int,
                    auto_commit: bool = True) -> Dict[str, Any]:
        """Merge one code into another (code-edits.md §6).

        Lossy BY DESIGN, matching QualCoder exactly: on a code_text UNIQUE
        (cid,fid,pos0,pos1,owner) collision the destination row wins untouched
        and the source row is DELETED (no memo-concat, no important OR-ing).
        code_av/code_image have no unique constraint and are reassigned
        unconditionally (no dedup — true duplicates can result, as upstream).
        The source code_name row is deleted last. One atomic transaction.
        """
        self._require_write_access()
        preview = self.preview_merge_codes(from_code_id, into_code_id)
        try:
            # 1. code_text: pre-delete colliders (destination wins), then
            #    bulk-reassign the survivors. Set-based equivalent of
            #    QualCoder's per-row try/except loop (code-edits.md §6.8).
            self.conn.execute(
                "DELETE FROM code_text WHERE cid = ? AND EXISTS ("
                "  SELECT 1 FROM code_text d WHERE d.cid = ? "
                "  AND d.fid = code_text.fid AND d.pos0 = code_text.pos0 "
                "  AND d.pos1 = code_text.pos1 AND d.owner = code_text.owner)",
                (from_code_id, into_code_id)
            )
            self.conn.execute(
                "UPDATE code_text SET cid = ? WHERE cid = ?",
                (into_code_id, from_code_id)
            )
            # 2 & 3. code_av / code_image: no unique constraint -> reassign
            #        unconditionally (no dedup, matching QualCoder)
            self.conn.execute(
                "UPDATE code_av SET cid = ? WHERE cid = ?",
                (into_code_id, from_code_id)
            )
            self.conn.execute(
                "UPDATE code_image SET cid = ? WHERE cid = ?",
                (into_code_id, from_code_id)
            )
            # 4 (v16+). Master-parity extras, gated on the supercid probe:
            #    reparent the source's sub-codes onto the target (S3, no
            #    orphans: code_tree.py:1564-1566), append the provenance
            #    block to the target memo (code_tree.py:1521-1538), and
            #    clean saved-graph rows for the dead cid. On v14/v15 the
            #    3.8.2-parity recipe stays byte-exact (no memo concat).
            caps = getattr(self, "capabilities", None)
            subcodes_reparented = 0
            provenance_memo_added = False
            if caps is not None and caps.has_supercid:
                cur = self.conn.execute(
                    "UPDATE code_name SET supercid = ?, catid = NULL "
                    "WHERE supercid = ?",
                    (into_code_id, from_code_id))
                subcodes_reparented = cur.rowcount
                source_row = self.conn.execute(
                    "SELECT name, memo, owner FROM code_name WHERE cid = ?",
                    (from_code_id,)).fetchone()
                target_memo_row = self.conn.execute(
                    "SELECT memo FROM code_name WHERE cid = ?",
                    (into_code_id,)).fetchone()
                if source_row is not None and target_memo_row is not None:
                    merge_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    source_memo = (source_row["memo"] or "").strip()
                    # The name and owner components carry no privacy
                    # semantics, so a '#####' inside them is neutralized
                    # before it can plant a private zone in the target
                    # (S-M1). Only the source MEMO may legitimately carry
                    # the marker.
                    block = (f"\n\n[Merged from code: "
                             f"{neutralize_marker(source_row['name'])}, "
                             f"Coder: {neutralize_marker(source_row['owner'])}, "
                             f"Merger date: {merge_date}]")
                    if source_memo:
                        block += f"\n{source_memo}"
                    # Memo privacy ('#####'): the provenance block lands in
                    # the TARGET memo's public zone, before any private
                    # suffix, which survives verbatim at the end. Without a
                    # suffix this is byte-identical to the 3.8.2/master
                    # parity recipe. The source memo travels whole (its own
                    # suffix included, so nothing private is destroyed);
                    # any marker it carries keeps everything after it
                    # hidden from AI reads. Recipe shared with
                    # merge_category (_append_provenance_block).
                    new_memo = _append_provenance_block(
                        target_memo_row["memo"], block)
                    self.conn.execute(
                        "UPDATE code_name SET memo = ? WHERE cid = ?",
                        (new_memo, into_code_id))
                    provenance_memo_added = True
                self._cleanup_graph_rows_for_cid(from_code_id)
            # 5. delete the merged-away code definition
            self.conn.execute(
                "DELETE FROM code_name WHERE cid = ?", (from_code_id,)
            )
            if auto_commit:
                self.conn.commit()
            logger.info(f"Merged code {from_code_id} into {into_code_id}")
        except sqlite3.Error as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            _raise_query_error(e, "merge_codes", "Failed to merge codes")
        result = {"merged": True, **preview}
        if subcodes_reparented:
            result["subcodes_reparented_to_target"] = subcodes_reparented
        if provenance_memo_added:
            result["provenance_memo_added"] = True
        return result

    def preview_delete_code(self, code_id: int) -> Dict[str, Any]:
        """Count the coded data a code delete would destroy (read-only).

        On v16+ (supercid probe) the preview always reports the WHOLE
        BRANCH: the code plus every transitive sub-code, and coding counts
        over all branch cids, so the confirm gate never undercounts the
        blast radius (S4; upstream get_branch_cids code_tree.py:796-818).
        """
        code_id = validate_id(code_id, "code_id")
        code = self._get_code_row(code_id)
        branch = self.get_branch_cids(code_id)
        marks = ",".join("?" * len(branch))
        text_n = self.conn.execute(
            f"SELECT COUNT(*) FROM code_text WHERE cid IN ({marks})", branch
        ).fetchone()[0]
        av_n = self.conn.execute(
            f"SELECT COUNT(*) FROM code_av WHERE cid IN ({marks})", branch
        ).fetchone()[0]
        img_n = self.conn.execute(
            f"SELECT COUNT(*) FROM code_image WHERE cid IN ({marks})", branch
        ).fetchone()[0]
        preview = {
            "code": {"id": code_id, "name": code["name"]},
            "text_codings_to_delete": text_n,
            "av_codings_to_delete": av_n,
            "image_codings_to_delete": img_n,
            "total_codings_to_delete": text_n + av_n + img_n,
        }
        if len(branch) > 1:
            names = self.conn.execute(
                f"SELECT name FROM code_name WHERE cid IN ({marks}) "
                f"AND cid != ?", branch + [code_id]).fetchall()
            preview["subcode_count"] = len(branch) - 1
            preview["subcodes"] = sorted(r[0] for r in names)
            preview["note"] = (
                f"{len(branch) - 1} sub-code(s) hang under this code. "
                f"Deleting requires cascade=true (the whole branch and all "
                f"its codings die, exactly as QualCoder's own delete), or "
                f"move the sub-codes first if they are needed.")
        return preview

    def delete_code(self, code_id: int, cascade: bool = False,
                    auto_commit: bool = True) -> Dict[str, Any]:
        """Delete a code AND all its codings (code-edits.md §7; S4 on v16+).

        Bulk data destruction, matching QualCoder: removes the code_name row
        plus every code_text/code_av/code_image row for the cid. On v16+
        (supercid probe), a code with sub-codes is REFUSED unless
        cascade=True, which then deletes the WHOLE BRANCH and its codings
        in one transaction exactly as upstream (code_tree.py:820-883),
        including saved-graph row cleanup per deleted cid. No delete may
        ever leave a dangling supercid. recently_used_codes is left alone
        (W14: no project-table writes; QualCoder self-heals). Categories,
        annotations and case links are never touched. Atomic.
        """
        self._require_write_access()
        preview = self.preview_delete_code(code_id)
        branch = self.get_branch_cids(code_id)
        if len(branch) > 1 and not cascade:
            raise ValueError(
                f"{len(branch) - 1} sub-code(s) will also be deleted. Move "
                f"the sub-codes first if they are needed, or call again "
                f"with cascade=true to delete the whole branch and all its "
                f"codings.")
        try:
            for cid in branch:
                self.conn.execute("DELETE FROM code_text WHERE cid = ?", (cid,))
                self.conn.execute("DELETE FROM code_av WHERE cid = ?", (cid,))
                self.conn.execute("DELETE FROM code_image WHERE cid = ?", (cid,))
                self.conn.execute("DELETE FROM code_name WHERE cid = ?", (cid,))
                self._cleanup_graph_rows_for_cid(cid)
            if auto_commit:
                self.conn.commit()
            logger.info(f"Deleted code branch {branch} and its codings")
        except sqlite3.Error as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            _raise_query_error(e, "delete_code", "Failed to delete code")
        result = {"deleted": True, **preview}
        if len(branch) > 1:
            result["branch_deleted"] = True
        return result

    def preview_delete_category(self, category_id: int) -> Dict[str, Any]:
        """Count what a category delete would reparent (read-only)."""
        category_id = validate_id(category_id, "category_id")
        cat = self._get_category_row(category_id)
        codes_n = self.conn.execute(
            "SELECT COUNT(*) FROM code_name WHERE catid = ?", (category_id,)
        ).fetchone()[0]
        subcats_n = self.conn.execute(
            "SELECT COUNT(*) FROM code_cat WHERE supercatid = ?", (category_id,)
        ).fetchone()[0]
        return {
            "category": {"id": category_id, "name": cat["name"]},
            "codes_moved_to_top_level": codes_n,
            "subcategories_moved_to_top_level": subcats_n,
            "note": "Deleting a category is SHALLOW: its codes and direct "
                    "sub-categories move to the top level (never deleted, "
                    "never reparented to a grandparent). Coded data is "
                    "untouched.",
        }

    def delete_category(self, category_id: int,
                        auto_commit: bool = True) -> Dict[str, Any]:
        """Delete a category, reparenting its children to top level.

        Shallow and non-destructive to codes (category-tree.md §4): codes in
        the category get catid=NULL, direct sub-categories get
        supercatid=NULL, then the category row is deleted and a dangling-
        parent sweep runs. Coded data is never touched.
        """
        self._require_write_access()
        preview = self.preview_delete_category(category_id)
        try:
            self.conn.execute(
                "UPDATE code_name SET catid = NULL WHERE catid = ?",
                (category_id,)
            )
            self.conn.execute(
                "UPDATE code_cat SET supercatid = NULL WHERE supercatid = ?",
                (category_id,)
            )
            self.conn.execute(
                "DELETE FROM code_cat WHERE catid = ?", (category_id,)
            )
            # Safety sweep: null any now-dangling supercatid (matches
            # QualCoder's post-delete + open-time hygiene)
            self.conn.execute(
                "UPDATE code_cat SET supercatid = NULL WHERE supercatid IS "
                "NOT NULL AND supercatid NOT IN (SELECT catid FROM code_cat)"
            )
            if auto_commit:
                self.conn.commit()
            logger.info(f"Deleted category {category_id}, reparented children")
        except sqlite3.Error as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            _raise_query_error(e, "delete_category", "Failed to delete category")
        return {"deleted": True, **preview}

    # ========================================================================
    # ANNOTATIONS (v0.8 D1 — memos-journals.md §4: the memo IS the
    # annotation; no empty state exists)
    # ========================================================================

    def get_annotation(self, annotation_id: int) -> Optional[Dict[str, Any]]:
        """Get one annotation by anid (with its file name)."""
        annotation_id = validate_id(annotation_id, "annotation_id")
        try:
            row = self.conn.execute(
                "SELECT a.anid, a.fid, a.pos0, a.pos1, a.memo, a.owner, "
                "a.date, s.name AS file_name "
                "FROM annotation a LEFT JOIN source s ON a.fid = s.id "
                "WHERE a.anid = ?", (annotation_id,)
            ).fetchone()
        except sqlite3.Error as e:
            _raise_query_error(e, "get_annotation",
                               "Failed to retrieve annotation")
        if not row:
            return None
        return {
            "annotation_id": row["anid"],
            "file_id": row["fid"],
            "file_name": row["file_name"],
            "position_start": row["pos0"],
            "position_end": row["pos1"],
            # REFI-born rows can carry '' or NULL memos (cases-attributes.md
            # §7.5) — tolerate on read; our writers never create them
            "memo": row["memo"] or "",
            "owner": row["owner"],
            "date": row["date"],
        }

    def add_annotation(self, file_id: int, start_pos: int, end_pos: int,
                       memo: str, owner: str,
                       auto_commit: bool = True) -> Dict[str, Any]:
        """Create an annotation on a text span.

        QualCoder contract (memos-journals.md §4.1): insert ONLY when the
        memo is non-empty — an annotation never exists with memo='' (the
        memo is the annotation). Positions are character offsets into the
        file's fulltext; unique(fid,pos0,pos1,owner) is pre-checked
        app-side for a clean error.
        """
        self._require_write_access()
        file_id = validate_id(file_id, "file_id")
        if not isinstance(memo, str) or not memo.strip():
            raise ValueError(
                "memo must be a non-empty string — an annotation IS its "
                "note; there is no empty annotation"
            )
        _reject_if_too_long(memo, "memo")
        # Memo privacy ('#####'): new memos are public-text only (see
        # add_coding). An annotation must still have text after that.
        memo = extract_ai_memo(memo)
        if not memo.strip():
            raise ValueError(
                "annotation text is empty after removing the '#####' "
                "private-note marker: text from the marker onward is "
                "reserved for the researcher's own notes and is never "
                "written by AI tools"
            )
        if not owner or not isinstance(owner, str) or not owner.strip():
            raise ValueError("owner must be a non-empty string")
        if (not isinstance(start_pos, int) or isinstance(start_pos, bool)
                or not isinstance(end_pos, int) or isinstance(end_pos, bool)):
            raise ValueError("start_pos and end_pos must be integers")
        if start_pos < 0 or end_pos <= start_pos:
            raise ValueError(
                f"positions must satisfy 0 <= start_pos < end_pos, got "
                f"{start_pos}-{end_pos}"
            )

        file_content = self.get_file_content(file_id)
        if file_content is None:
            raise ValueError(f"File ID {file_id} does not exist")
        fulltext = file_content.get("content") or ""
        if not file_content.get("is_text") or not fulltext:
            raise ValueError(
                f"File ID {file_id} is not a text source with text content - "
                f"annotations attach to text spans"
            )
        if end_pos > len(fulltext):
            raise ValueError(
                f"end_pos ({end_pos}) exceeds file length ({len(fulltext)})"
            )

        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            # unique(fid,pos0,pos1,owner) pre-check for a clean error
            existing = self.conn.execute(
                "SELECT anid FROM annotation WHERE fid = ? AND pos0 = ? "
                "AND pos1 = ? AND owner = ?",
                (file_id, start_pos, end_pos, owner)
            ).fetchone()
            if existing:
                raise ValueError(
                    f"An annotation by '{owner}' already exists on this exact "
                    f"span (anid={existing['anid']}) — edit it with "
                    f"update_annotation instead"
                )
            # Overlap check (cases-attributes.md §7.1): the GUI never
            # creates a second annotation overlapping an existing one by
            # the same coder — it switches to editing the existing one.
            # The DB only blocks exact duplicates, but overlapping rows
            # are hazardous in the GUI (its clear-path deletes by pos0
            # alone, taking collateral) and its bold-range display merges
            # them. Mirror the GUI: refuse and point at the existing row.
            overlapping = self.conn.execute(
                "SELECT anid, pos0, pos1 FROM annotation WHERE fid = ? "
                "AND owner = ? AND pos0 < ? AND pos1 > ? ORDER BY pos0",
                (file_id, owner, end_pos, start_pos)
            ).fetchone()
            if overlapping:
                raise ValueError(
                    f"An annotation by '{owner}' already overlaps this span "
                    f"(anid={overlapping['anid']}, "
                    f"{overlapping['pos0']}-{overlapping['pos1']}). "
                    f"QualCoder never keeps overlapping annotations by one "
                    f"coder — edit the existing one with update_annotation, "
                    f"or delete it first"
                )
            cursor = self.conn.execute(
                "INSERT INTO annotation (fid, pos0, pos1, memo, owner, date) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (file_id, start_pos, end_pos, memo, owner, date_str)
            )
            # C7: the INSERT above took the write lock; re-verify the
            # fulltext the positions were validated against before commit
            self.verify_fulltext_unchanged(
                file_id, self.fingerprint_of_text(fulltext))
            if auto_commit:
                self.conn.commit()
            anid = cursor.lastrowid
            logger.info(f"Added annotation anid={anid} on file {file_id}")
        except ValueError:
            raise
        except sqlite3.IntegrityError:
            try:
                self.conn.rollback()
            except Exception:
                pass
            raise ValueError(
                "An annotation already exists on this exact span"
            ) from None
        except sqlite3.Error as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            _raise_query_error(e, "add_annotation", "Failed to add annotation")

        return {
            "annotation_id": anid,
            "file_id": file_id,
            "file_name": file_content["name"],
            "position_start": start_pos,
            "position_end": end_pos,
            "memo": memo,
            "owner": owner,
            "date": date_str,
        }

    def update_annotation(self, annotation_id: int, memo: str,
                          auto_commit: bool = True) -> Dict[str, Any]:
        """Edit an annotation's note by anid; an EMPTY memo DELETES the row.

        QualCoder contract (memos-journals.md §4.2/§4.3): annotation is one
        of the three date-on-edit objects (memo AND date updated; owner and
        the span untouched); clearing the memo deletes the annotation —
        never leave an empty one. Keyed by anid, never pos0 (the upstream
        delete-by-pos0 bug is documented; do not replicate it).
        """
        self._require_write_access()
        if not isinstance(memo, str):
            raise ValueError("memo must be a string")
        _reject_if_too_long(memo, "memo")
        existing = self.get_annotation(annotation_id)
        if existing is None:
            raise ValueError(f"Annotation ID {annotation_id} does not exist")
        visible = self.annotation_is_visible(annotation_id)

        # Memo privacy ('#####'): replace only the public text; a private
        # suffix the researcher put on this annotation survives verbatim
        # and is never echoed back
        stored_memo = merge_public_memo(existing.get("memo"), memo)
        public_memo = extract_ai_memo(stored_memo)

        if not stored_memo.strip():
            # Clear = delete (matches QualCoder exactly). Only reached
            # when there is no private suffix: clearing the public text of
            # an annotation that carries one keeps the row (deleting it
            # would destroy the researcher's private note).
            return {**self.delete_annotation(annotation_id,
                                             auto_commit=auto_commit),
                    "deleted_because_cleared": True}

        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            self.conn.execute(
                "UPDATE annotation SET memo = ?, date = ? WHERE anid = ?",
                (stored_memo, date_str, annotation_id)
            )
            if auto_commit:
                self.conn.commit()
            logger.info(f"Updated annotation anid={annotation_id}")
        except sqlite3.Error as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            _raise_query_error(e, "update_annotation",
                               "Failed to update annotation")
        if not visible:
            # Hidden coder's row (QC 4.0 visibility): echo ids plus the
            # public text the AI itself just supplied, never the row's
            # owner, span or file name (S-MAJ; upstream echoes ids only)
            result = {"annotation_id": existing["annotation_id"],
                      "file_id": existing["file_id"],
                      "memo": public_memo, "date": date_str,
                      "updated": True, "hidden_coder_row": True}
        else:
            result = {**existing, "memo": public_memo, "date": date_str,
                      "updated": True}
        if public_memo == "":
            # Public text cleared but the row was kept for its private
            # suffix; from the AI's side the note reads as cleared
            result["cleared"] = True
        return result

    def delete_annotation(self, annotation_id: int,
                          auto_commit: bool = True) -> Dict[str, Any]:
        """Delete an annotation by anid (never by pos0 — see §4.3 gotcha).

        A hidden coder's row (QC 4.0 visibility) echoes ids only (S-MAJ).
        """
        self._require_write_access()
        existing = self.get_annotation(annotation_id)
        if existing is None:
            raise ValueError(f"Annotation ID {annotation_id} does not exist")
        visible = self.annotation_is_visible(annotation_id)
        try:
            self.conn.execute(
                "DELETE FROM annotation WHERE anid = ?", (annotation_id,)
            )
            if auto_commit:
                self.conn.commit()
            logger.info(f"Deleted annotation anid={annotation_id}")
        except sqlite3.Error as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            _raise_query_error(e, "delete_annotation",
                               "Failed to delete annotation")
        if not visible:
            return {"annotation_id": existing["annotation_id"],
                    "file_id": existing["file_id"],
                    "deleted": True, "hidden_coder_row": True}
        return {**existing, "deleted": True}

    # ========================================================================
    # MERGE CATEGORY (v0.8 D1 — category-tree.md §9)
    # ========================================================================

    def preview_merge_category(self, from_category_id: int,
                               into_category_id: Optional[int]
                               ) -> Dict[str, Any]:
        """Count what a category merge would reparent (read-only)."""
        from_category_id = validate_id(from_category_id, "from_category_id")
        src_cat = self._get_category_row(from_category_id)
        if into_category_id is not None:
            into_category_id = validate_id(into_category_id,
                                           "into_category_id")
            if into_category_id == from_category_id:
                raise ValueError("Cannot merge a category into itself")
            dest = self._get_category_row(into_category_id)
            # Target must not be a DESCENDANT of the source (the cycle-guard
            # intent of QualCoder's picker, id-based; category-tree.md §9)
            if self.would_create_category_cycle(from_category_id,
                                                into_category_id):
                raise ValueError(
                    "Cannot merge a category into its own descendant — "
                    "that would orphan the subtree"
                )
            target_desc = {"id": into_category_id, "name": dest["name"]}
        else:
            target_desc = {"id": None, "name": "(top level)"}

        codes_n = self.conn.execute(
            "SELECT COUNT(*) FROM code_name WHERE catid = ?",
            (from_category_id,)
        ).fetchone()[0]
        subcats_n = self.conn.execute(
            "SELECT COUNT(*) FROM code_cat WHERE supercatid = ?",
            (from_category_id,)
        ).fetchone()[0]
        carries_memo = self._category_merge_carries_memo(into_category_id)
        if carries_memo:
            memo_note = (" The source category's memo is carried into the "
                         "target category's memo under a provenance note "
                         "(master parity, code_tree.py:1413); a '#####' "
                         "private section on the target stays private.")
        elif into_category_id is None:
            memo_note = (" Merging to the top level removes the source "
                         "category's memo with its row (as QualCoder does); "
                         "the mandatory backup keeps a copy.")
        else:
            memo_note = (" On this project's schema (pre-sub-code, matching "
                         "QualCoder 3.8.2 exactly) the source category's "
                         "memo is removed with its row; the mandatory backup "
                         "keeps a copy.")
        return {
            "from_category": {"id": from_category_id, "name": src_cat["name"]},
            "into_category": target_desc,
            "codes_reparented": codes_n,
            "subcategories_reparented": subcats_n,
            "source_memo_carried_to_target": carries_memo,
            "note": "Merging a category reparents its codes and direct "
                    "sub-categories to the target (codings are untouched — "
                    "they key on the code, not the category), then deletes "
                    "the source category." + memo_note,
        }

    def _category_merge_carries_memo(self,
                                     into_category_id: Optional[int]) -> bool:
        """Whether merge_category carries the source memo into the target.

        Master parity (code_tree.py:1395-1413 at pin 9bddf17, landed
        post-3.8.2): the source category's memo is appended to a REAL
        target's memo under a provenance block, never when merging to the
        top level. Gated on the supercid probe exactly like merge_codes'
        provenance block, so v14/v15 projects stay byte-exact with
        QualCoder 3.8.2 (the v17 WS rule) and the two merge tools follow
        one consistent recipe (S-M2).
        """
        caps = getattr(self, "capabilities", None)
        return (into_category_id is not None
                and caps is not None and bool(caps.has_supercid))

    def merge_category(self, from_category_id: int,
                       into_category_id: Optional[int],
                       auto_commit: bool = True) -> Dict[str, Any]:
        """Merge one category into another (or into the top level).

        QualCoder recipe (category-tree.md §9): codes with catid=source ->
        target; sub-categories with supercatid=source -> target; delete the
        source code_cat row; dangling-supercatid sweep. into_category_id of
        None moves everything to the top level (QualCoder's blank option).
        Codings are never touched.

        Master parity extra (S-M2), gated like merge_codes' provenance
        block: on v16+ schemas a merge into a real target appends
        "[Merged from category: ..., Coder: ..., Merger date: ...]" plus
        the whole source memo to the target's memo (code_tree.py:1413),
        placed with the shared _append_provenance_block recipe so a
        '#####' private section on the target survives verbatim and one
        the source carries stays AI-hidden where upstream puts it.
        """
        self._require_write_access()
        preview = self.preview_merge_category(from_category_id,
                                              into_category_id)
        provenance_memo_added = False
        try:
            if self._category_merge_carries_memo(into_category_id):
                source_row = self.conn.execute(
                    "SELECT name, memo, owner FROM code_cat WHERE catid = ?",
                    (from_category_id,)).fetchone()
                target_memo_row = self.conn.execute(
                    "SELECT memo FROM code_cat WHERE catid = ?",
                    (into_category_id,)).fetchone()
                if source_row is not None and target_memo_row is not None:
                    merge_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    source_memo = (source_row["memo"] or "").strip()
                    # Name and owner neutralized as in merge_codes (S-M1);
                    # only the source MEMO may carry the marker
                    block = (f"\n\n[Merged from category: "
                             f"{neutralize_marker(source_row['name'])}, "
                             f"Coder: {neutralize_marker(source_row['owner'])}, "
                             f"Merger date: {merge_date}]")
                    if source_memo:
                        block += f"\n{source_memo}"
                    new_memo = _append_provenance_block(
                        target_memo_row["memo"], block)
                    self.conn.execute(
                        "UPDATE code_cat SET memo = ? WHERE catid = ?",
                        (new_memo, into_category_id))
                    provenance_memo_added = True
            self.conn.execute(
                "UPDATE code_name SET catid = ? WHERE catid = ?",
                (into_category_id, from_category_id)
            )
            self.conn.execute(
                "UPDATE code_cat SET supercatid = ? WHERE supercatid = ?",
                (into_category_id, from_category_id)
            )
            self.conn.execute(
                "DELETE FROM code_cat WHERE catid = ?", (from_category_id,)
            )
            self.conn.execute(
                "UPDATE code_cat SET supercatid = NULL WHERE supercatid IS "
                "NOT NULL AND supercatid NOT IN (SELECT catid FROM code_cat)"
            )
            if auto_commit:
                self.conn.commit()
            logger.info(f"Merged category {from_category_id} into "
                        f"{into_category_id}")
        except sqlite3.Error as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            _raise_query_error(e, "merge_category",
                               "Failed to merge category")
        result = {"merged": True, **preview}
        if provenance_memo_added:
            result["provenance_memo_added"] = True
        return result

    # ========================================================================
    # CASES (v0.8 D1 — schema-writes.md §5.1)
    # ========================================================================

    def add_case(self, name: str, owner: str, memo: Optional[str] = None,
                 auto_commit: bool = True) -> Dict[str, Any]:
        """Create a case (participant/subject).

        QualCoder contract (schema-writes.md §5.1): INSERT INTO cases with
        memo='' default (never NULL), unique(name) pre-checked app-side,
        then one empty attribute placeholder row per existing CASE
        attribute type (attr_type='case', attribute.id = the new caseid).
        """
        self._require_write_access()
        if not name or not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty string")
        name = name.strip()
        validate_string(name, "name")
        if not owner or not isinstance(owner, str) or not owner.strip():
            raise ValueError("owner must be a non-empty string")
        if memo:
            _reject_if_too_long(memo, "memo")
        # Memo privacy ('#####'): new memos are public-text only (see
        # add_coding)
        memo = extract_ai_memo(memo or "")

        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            existing = self.conn.execute(
                "SELECT caseid FROM cases WHERE name = ?", (name,)
            ).fetchone()
            if existing:
                raise ValueError(f"A case named '{name}' already exists")

            cursor = self.conn.execute(
                "INSERT INTO cases (name, memo, owner, date) "
                "VALUES (?, ?, ?, ?)",
                (name, memo, owner, date_str)
            )
            case_id = cursor.lastrowid

            # Attribute placeholders for case attribute types — the exact
            # rows QualCoder's own add_case writes (cases.py:584-590,
            # driven by caseOrFile='case'). The real domain set is
            # case|file|journal; 'both' does not exist anywhere in
            # QualCoder (cases-attributes.md §1), so no superset matching.
            attr_types = self.conn.execute(
                "SELECT name FROM attribute_type WHERE caseOrFile = 'case'"
            ).fetchall()
            for attr_type_row in attr_types:
                self.conn.execute(
                    "INSERT INTO attribute (name, attr_type, value, id, "
                    "date, owner) VALUES (?, 'case', '', ?, ?, ?)",
                    (attr_type_row["name"], case_id, date_str, owner)
                )

            if auto_commit:
                self.conn.commit()
            logger.info(f"Added case: caseid={case_id}, name={name}")
        except ValueError:
            raise
        except sqlite3.IntegrityError:
            try:
                self.conn.rollback()
            except Exception:
                pass
            raise ValueError(f"A case named '{name}' already exists") from None
        except sqlite3.Error as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            _raise_query_error(e, "add_case", "Failed to add case")

        return {
            "id": case_id,
            "name": name,
            "memo": memo or "",
            "owner": owner,
            "date": date_str,
            "attributes_created": len(attr_types),
        }

    # Reserved attribute names (cases-attributes.md §3.3): QualCoder's own
    # dialog reserves the singular forms, but its RIS importer actually
    # creates Ref_Authors (plural) — an upstream inconsistency. Reserve BOTH
    # spellings so a user-created attribute can never collide with the RIS
    # importer's later insert-if-missing.
    RESERVED_ATTRIBUTE_NAMES = frozenset({
        "Ref_Type", "Ref_Author", "Ref_Authors", "Ref_Title", "Ref_Year",
        "Ref_Journal",
    })

    # attribute_type.caseOrFile / attribute.attr_type domain -> the entity
    # table each domain's overloaded attribute.id points into
    # (cases-attributes.md §1). There is no 'both'.
    _ATTRIBUTE_DOMAINS = {
        "case": ("cases", "caseid"),
        "file": ("source", "id"),
        "journal": ("journal", "jid"),
    }

    def add_attribute_type(self, name: str, owner: str, applies_to: str,
                           value_type: str = "character",
                           memo: Optional[str] = None,
                           auto_commit: bool = True) -> Dict[str, Any]:
        """Define a new attribute (cases-attributes.md §3.1/§3.2).

        Writes the attribute_type row exactly as every QualCoder entry
        point does, then performs the placeholder back-fill: one empty
        ('' value) attribute row per existing entity of the domain. The
        back-fill is load-bearing — QualCoder's GUI table, exports and
        sorting assume every entity has a row for every attribute of its
        domain, and the case-side auto-heal is a no-op in 3.8.2, so
        skipping it here would leave cases silently dropped from
        attribute joins.

        Attribute names are GLOBAL (attribute_type.name is the primary
        key across all three domains). Both Ref_Author and Ref_Authors
        spellings are reserved (upstream reserves only the singular but
        its RIS importer creates the plural).
        """
        self._require_write_access()
        if not name or not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty string")
        name = name.strip()
        validate_string(name, "name")
        if not owner or not isinstance(owner, str) or not owner.strip():
            raise ValueError("owner must be a non-empty string")
        if applies_to not in self._ATTRIBUTE_DOMAINS:
            raise ValueError(
                "applies_to must be 'case', 'file' or 'journal' (QualCoder's "
                "real domain set — there is no 'both')"
            )
        if value_type not in ("character", "numeric"):
            raise ValueError("value_type must be 'character' or 'numeric'")
        if name in self.RESERVED_ATTRIBUTE_NAMES:
            raise ValueError(
                f"'{name}' is reserved for QualCoder's reference importer "
                f"(Ref_* attributes are created automatically by RIS/nbib "
                f"import) — choose another name"
            )
        if memo:
            _reject_if_too_long(memo, "memo")
        # Memo privacy ('#####'): new memos are public-text only (see
        # add_coding)
        memo = extract_ai_memo(memo or "")

        entity_table, entity_id_col = self._ATTRIBUTE_DOMAINS[applies_to]
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            existing = self.conn.execute(
                "SELECT name, caseOrFile FROM attribute_type WHERE name = ?",
                (name,)
            ).fetchone()
            if existing:
                raise ValueError(
                    f"An attribute named '{name}' already exists (as a "
                    f"{existing['caseOrFile']} attribute) — attribute names "
                    f"are global across cases, files and journals"
                )

            self.conn.execute(
                "INSERT INTO attribute_type (name, date, owner, memo, "
                "caseOrFile, valuetype) VALUES (?, ?, ?, ?, ?, ?)",
                (name, date_str, owner, memo or "", applies_to, value_type)
            )

            # Placeholder back-fill (§3.2): value='' (never NULL), one row
            # per existing entity of this domain
            entity_ids = self.conn.execute(
                f"SELECT {entity_id_col} FROM {entity_table}"
            ).fetchall()
            for row in entity_ids:
                self.conn.execute(
                    "INSERT INTO attribute (name, value, id, attr_type, "
                    "date, owner) VALUES (?, '', ?, ?, ?, ?)",
                    (name, row[0], applies_to, date_str, owner)
                )

            if auto_commit:
                self.conn.commit()
            logger.info(
                f"Added attribute type '{name}' ({applies_to}/{value_type}), "
                f"{len(entity_ids)} placeholder(s)"
            )
        except ValueError:
            raise
        except sqlite3.IntegrityError:
            try:
                self.conn.rollback()
            except Exception:
                pass
            raise ValueError(
                f"An attribute named '{name}' already exists — attribute "
                f"names are global across cases, files and journals"
            ) from None
        except sqlite3.Error as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            _raise_query_error(e, "add_attribute_type",
                               "Failed to add attribute type")

        return {
            "name": name,
            "applies_to": applies_to,
            "value_type": value_type,
            "memo": memo or "",
            "owner": owner,
            "date": date_str,
            "placeholders_created": len(entity_ids),
        }

    def set_attribute_value(self, target_type: str, target_id: int,
                            attr_name: str, value: str, owner: str,
                            auto_commit: bool = True) -> Dict[str, Any]:
        """Set an attribute value for a case, file or journal.

        QualCoder contract (cases-attributes.md §4.1/§4.2): input is
        stripped; the domain-filtered valuetype gates numeric values;
        the write is insert-if-missing then update, keyed
        (id, name, attr_type) — never assume the placeholder row exists
        (QualCoder's case-side placeholder heal is a no-op in 3.8.2).
        Byte-fidelity per domain: the case path refreshes owner+date on
        update, the file/journal paths write value only, exactly like the
        three GUI paths.

        Deliberate deviation (documented): a non-castable value for a
        numeric attribute is REJECTED with an error — QualCoder silently
        replaces it with '' (interactive data loss). '' itself is the
        canonical "unset" and is always accepted.
        """
        self._require_write_access()
        if target_type not in self._ATTRIBUTE_DOMAINS:
            raise ValueError(
                "target_type must be 'case', 'file' or 'journal'"
            )
        target_id = validate_id(target_id, "target_id")
        if not isinstance(attr_name, str) or not attr_name.strip():
            raise ValueError("attr_name must be a non-empty string")
        attr_name = attr_name.strip()
        if not isinstance(value, str):
            raise ValueError("value must be a string ('' clears/unsets)")
        value = value.strip()
        _reject_if_too_long(value, "value")
        if not owner or not isinstance(owner, str) or not owner.strip():
            raise ValueError("owner must be a non-empty string")

        entity_table, entity_id_col = self._ATTRIBUTE_DOMAINS[target_type]
        try:
            entity = self.conn.execute(
                f"SELECT {entity_id_col} AS eid, name FROM {entity_table} "
                f"WHERE {entity_id_col} = ?", (target_id,)
            ).fetchone()
            if not entity:
                raise ValueError(
                    f"{target_type.capitalize()} ID {target_id} does not exist"
                )

            att = self.conn.execute(
                "SELECT valuetype, caseOrFile FROM attribute_type "
                "WHERE name = ?", (attr_name,)
            ).fetchone()
            if not att:
                raise ValueError(
                    f"Attribute '{attr_name}' does not exist — create it "
                    f"first with create_attribute_type"
                )
            if att["caseOrFile"] != target_type:
                raise ValueError(
                    f"'{attr_name}' is a {att['caseOrFile']} attribute — it "
                    f"cannot be set on a {target_type}"
                )
            if att["valuetype"] == "numeric" and value != "":
                try:
                    float(value)
                except ValueError:
                    raise ValueError(
                        f"'{attr_name}' is a numeric attribute and "
                        f"'{value}' is not a number (QualCoder would "
                        f"silently blank it; refusing instead). Pass '' to "
                        f"unset."
                    ) from None

            date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            existing = self.conn.execute(
                "SELECT attrid, value FROM attribute "
                "WHERE id = ? AND name = ? AND attr_type = ?",
                (target_id, attr_name, target_type)
            ).fetchone()
            if existing is None:
                # Insert-if-missing: placeholder rows can be absent
                # (older writers, no-op case heal) — §3.8/§4.1
                self.conn.execute(
                    "INSERT INTO attribute (name, value, id, attr_type, "
                    "date, owner) VALUES (?, ?, ?, ?, ?, ?)",
                    (attr_name, value, target_id, target_type, date_str,
                     owner)
                )
                previous = None
            elif target_type == "case":
                # Case path refreshes owner and date (cases.py:670-679)
                self.conn.execute(
                    "UPDATE attribute SET value = ?, date = ?, owner = ? "
                    "WHERE attrid = ?",
                    (value, date_str, owner, existing["attrid"])
                )
                previous = existing["value"]
            else:
                # File/journal paths write value only
                # (manage_files.py:1470-1471, journals.py:747-748)
                self.conn.execute(
                    "UPDATE attribute SET value = ? WHERE attrid = ?",
                    (value, existing["attrid"])
                )
                previous = existing["value"]

            if auto_commit:
                self.conn.commit()
            logger.info(
                f"Set {target_type} attribute '{attr_name}' on "
                f"{target_type} {target_id}"
            )
        except ValueError:
            raise
        except sqlite3.Error as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            _raise_query_error(e, "set_attribute_value",
                               "Failed to set attribute value")

        return {
            "target_type": target_type,
            "target_id": target_id,
            "target_name": entity["name"],
            "attribute": attr_name,
            "value_type": att["valuetype"],
            "value": value,
            "previous_value": (previous if previous is not None
                               else "" if existing else None),
            "row_created": existing is None,
        }

    def backup_before_write(self) -> Path:
        """Create a backup of the current project before making changes.

        Returns:
            Path to the backup folder

        Raises:
            OSError: If backup fails
        """
        return backup_project(self.db_path)
