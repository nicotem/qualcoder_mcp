"""Database interface for Qualcoder .qda files."""

import bisect
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

logger = logging.getLogger(__name__)

# Configuration constants
DEFAULT_LIMIT = 50
MAX_LIMIT = 5000
# Only v14 (QualCoder 3.8.x) is tested. Older versions log a warning; the
# functional gate is the required-column check in _check_required_columns().
SUPPORTED_DB_VERSIONS = ['v14']

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
        # A locked database is NOT corrupted — report it distinctly
        if _is_locked_error(e):
            raise DatabaseLockedError(DB_LOCKED_MESSAGE) from None
        raise ValueError(f"Cannot open SQLite database: {e}")
    except sqlite3.DatabaseError as e:
        raise ValueError(f"Invalid or corrupted SQLite database: {e}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    return path


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


def backup_project(project_path: Union[str, Path]) -> Path:
    """Create a timestamped backup of a Qualcoder project.

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
        # Never copy lock files into backups — QualCoder's own backups
        # exclude *.lock too (__main__.py:1371,1378); a lock file inside a
        # restored folder triggers its "not properly closed" prompt
        shutil.copytree(
            project_path, backup_path,
            ignore=shutil.ignore_patterns("*.lock")
        )
        logger.info(f"Backup created successfully: {backup_path}")
        return backup_path
    except Exception as e:
        logger.error(f"Failed to create backup: {e}")
        raise OSError(f"Backup failed: {e}") from None


def copy_project_to_workspace(
    source_path: Union[str, Path],
    workspace: Optional[Union[str, Path]] = None,
    new_name: Optional[str] = None
) -> Path:
    """Copy a Qualcoder project to the MCP workspace for safe modification.

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
        shutil.copytree(source_path, dest_path)
        logger.info(f"Project copied successfully: {dest_path}")
        return dest_path
    except Exception as e:
        logger.error(f"Failed to copy project: {e}")
        raise OSError(f"Copy failed: {e}") from None


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

        codes = []
        for row in cursor.fetchall():
            codes.append({
                "id": row["cid"],
                "name": row["name"],
                "memo": row["memo"] or "",
                "color": row["color"],
                "owner": row["owner"],
                "date": row["date"],
                "category": row["category_name"],
                "category_id": row["category_id"]
            })
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
            # get_coded_text_segments (QA F8)
            text_count = self.conn.execute(
                "SELECT COUNT(*) as cnt FROM code_text ct "
                "JOIN source s ON ct.fid = s.id WHERE ct.cid = ?",
                (code_id,)
            ).fetchone()["cnt"]

            image_count = self.conn.execute(
                "SELECT COUNT(*) as cnt FROM code_image ci "
                "JOIN source s ON ci.id = s.id WHERE ci.cid = ?",
                (code_id,)
            ).fetchone()["cnt"]

            av_count = self.conn.execute(
                "SELECT COUNT(*) as cnt FROM code_av ca "
                "JOIN source s ON ca.id = s.id WHERE ca.cid = ?",
                (code_id,)
            ).fetchone()["cnt"]

            return {
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
        except sqlite3.Error as e:
            _raise_query_error(e, "get_code_details", "Failed to retrieve code details")

    def get_coded_text_segments(self, code_id: int, limit: int = 100) -> List[Dict[str, Any]]:
        """Get text segments coded with a specific code.

        Args:
            code_id: The code ID (cid)
            limit: Maximum number of segments to return (max 5000)

        Returns:
            List of coded text segments with context

        Raises:
            TypeError: If parameters are wrong type
            ValueError: If parameters are invalid
            RuntimeError: If database operation fails
        """
        code_id = validate_id(code_id, "code_id")
        limit = validate_limit(limit)

        try:
            cursor = self.conn.execute("""
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
                FROM code_text ct
                JOIN source s ON ct.fid = s.id
                WHERE ct.cid = ?
                ORDER BY s.name, ct.pos0
                LIMIT ?
            """, (code_id, limit))

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

            # Count codes in this file
            code_count = self.conn.execute(
                "SELECT COUNT(DISTINCT cid) as cnt FROM code_text WHERE fid = ?",
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

            # Get all coded segments for this file
            segments_cursor = self.conn.execute("""
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
                FROM code_text ct
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

            # Get annotations
            annotations_cursor = self.conn.execute("""
                SELECT
                    anid,
                    pos0,
                    pos1,
                    memo,
                    owner,
                    date
                FROM annotation
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
                         limit: int = DEFAULT_LIMIT) -> List[Dict[str, Any]]:
        """Search for coded text segments.

        Args:
            query: Text to search for (wildcards % and _ are escaped)
            code_name: Optional code name to filter by
            limit: Maximum results to return (max 5000)

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

        try:
            if code_name:
                code_name = validate_string(code_name, "code_name")

                cursor = self.conn.execute("""
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
                    FROM code_text ct
                    JOIN source s ON ct.fid = s.id
                    JOIN code_name c ON ct.cid = c.cid
                    WHERE ct.seltext LIKE ? ESCAPE '\\' AND c.name = ?
                    ORDER BY s.name, ct.pos0
                    LIMIT ?
                """, (f"%{escaped_query}%", code_name, limit))
            else:
                cursor = self.conn.execute("""
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
                    FROM code_text ct
                    JOIN source s ON ct.fid = s.id
                    JOIN code_name c ON ct.cid = c.cid
                    WHERE ct.seltext LIKE ? ESCAPE '\\'
                    ORDER BY s.name, ct.pos0
                    LIMIT ?
                """, (f"%{escaped_query}%", limit))

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

    def get_coding_frequencies(self) -> Dict[str, Any]:
        """Get frequency counts for all codes.

        Returns:
            Dictionary with code frequencies
        """
        # COUNT(s.id): orphaned codings (fid pointing at a deleted source)
        # are excluded, consistent with get_coded_text_segments — previously
        # counting and listing tools disagreed on the same code (QA F8)
        cursor = self.conn.execute("""
            SELECT
                c.cid,
                c.name,
                c.color,
                cat.name as category,
                COUNT(s.id) as text_count
            FROM code_name c
            LEFT JOIN code_cat cat ON c.catid = cat.catid
            LEFT JOIN code_text ct ON c.cid = ct.cid
            LEFT JOIN source s ON ct.fid = s.id
            GROUP BY c.cid, c.name, c.color, cat.name
            ORDER BY text_count DESC, c.name
        """)

        frequencies = []
        for row in cursor.fetchall():
            frequencies.append({
                "code_id": row["cid"],
                "code_name": row["name"],
                "code_color": row["color"],
                "category": row["category"],
                "frequency": row["text_count"]
            })

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

        try:
            # Search code memos
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
                LIMIT ?
            """, (f"%{escaped_query}%", limit))

            for row in cursor.fetchall():
                results.append({
                    "type": row["type"],
                    "id": row["id"],
                    "name": row["name"],
                    "memo": row["memo"],
                    "owner": row["owner"],
                    "date": row["date"]
                })

            # Search file memos
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
                    LIMIT ?
                """, (f"%{escaped_query}%", limit - len(results)))

                for row in cursor.fetchall():
                    results.append({
                        "type": row["type"],
                        "id": row["id"],
                        "name": row["name"],
                        "memo": row["memo"],
                        "owner": row["owner"],
                        "date": row["date"]
                    })

            # Search annotations
            if len(results) < limit:
                cursor = self.conn.execute("""
                    SELECT
                        'annotation' as type,
                        a.anid as id,
                        s.name,
                        a.memo,
                        a.owner,
                        a.date,
                        a.pos0,
                        a.pos1
                    FROM annotation a
                    JOIN source s ON a.fid = s.id
                    WHERE a.memo LIKE ? ESCAPE '\\'
                    LIMIT ?
                """, (f"%{escaped_query}%", limit - len(results)))

                for row in cursor.fetchall():
                    results.append({
                        "type": row["type"],
                        "id": row["id"],
                        "name": row["name"],
                        "memo": row["memo"],
                        "owner": row["owner"],
                        "date": row["date"],
                        "position_start": row["pos0"],
                        "position_end": row["pos1"]
                    })

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

                # Search memo
                if search_memo:
                    memo_text = row["memo"] or ""
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

    # ============================================================================
    # CO-OCCURRENCE ANALYSIS - Codes appearing together
    # ============================================================================

    def find_code_cooccurrences(self, code_id: int, window_size: int = 0) -> List[Dict[str, Any]]:
        """Find codes that appear together with a specific code.

        Args:
            code_id: The code ID to find co-occurrences for
            window_size: If 0, finds codes in same segment (overlap).
                        If > 0, finds codes within N characters

        Returns:
            List of codes that co-occur with counts
        """
        code_id = validate_id(code_id, "code_id")

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
        try:
            rows = self.conn.execute("""
                SELECT ct.cid, ct.fid, ct.pos0, ct.pos1
                FROM code_text ct
                WHERE ct.fid IN (
                    SELECT DISTINCT fid FROM code_text WHERE cid = ?
                )
            """, (code_id,)).fetchall()

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

    def get_case_code_matrix(self) -> Dict[str, Any]:
        """Get a matrix showing which codes appear in which cases.

        Returns:
            Dictionary with cases, codes, and matrix data
        """
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
            matrix_cursor = self.conn.execute("""
                SELECT
                    cs.caseid,
                    ct.cid,
                    COUNT(*) as count
                FROM case_text cs
                JOIN code_text ct ON cs.fid = ct.fid
                    -- full CONTAINMENT, matching QualCoder's own reports
                    -- (report_codes.py:1640); note QualCoder's whole-file
                    -- case links end at len(fulltext)-1, so a coding that
                    -- includes the file's last character is excluded there
                    AND ct.pos0 >= cs.pos0 AND ct.pos1 <= cs.pos1
                GROUP BY cs.caseid, ct.cid
            """)

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

    def get_codes_by_case(self, case_id: int) -> List[Dict[str, Any]]:
        """Get all codes that appear in a specific case.

        Args:
            case_id: The case ID

        Returns:
            List of codes with occurrence counts
        """
        case_id = validate_id(case_id, "case_id")

        try:
            cursor = self.conn.execute("""
                SELECT
                    c.cid,
                    c.name as code_name,
                    c.color,
                    cat.name as category_name,
                    COUNT(*) as occurrence_count
                FROM case_text cs
                JOIN code_text ct ON cs.fid = ct.fid
                    -- full CONTAINMENT, matching QualCoder's own reports
                    -- (report_codes.py:1640); note QualCoder's whole-file
                    -- case links end at len(fulltext)-1, so a coding that
                    -- includes the file's last character is excluded there
                    AND ct.pos0 >= cs.pos0 AND ct.pos1 <= cs.pos1
                JOIN code_name c ON ct.cid = c.cid
                LEFT JOIN code_cat cat ON c.catid = cat.catid
                WHERE cs.caseid = ?
                GROUP BY c.cid, c.name, c.color, cat.name
                ORDER BY occurrence_count DESC
            """, (case_id,))

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

    def get_cases_by_code(self, code_id: int) -> List[Dict[str, Any]]:
        """Get all cases that contain a specific code.

        Args:
            code_id: The code ID

        Returns:
            List of cases with occurrence counts
        """
        code_id = validate_id(code_id, "code_id")

        try:
            cursor = self.conn.execute("""
                SELECT
                    cs.caseid,
                    c.name as case_name,
                    c.memo,
                    COUNT(*) as occurrence_count
                FROM case_text cs
                JOIN code_text ct ON cs.fid = ct.fid
                    -- full CONTAINMENT, matching QualCoder's own reports
                    -- (report_codes.py:1640); note QualCoder's whole-file
                    -- case links end at len(fulltext)-1, so a coding that
                    -- includes the file's last character is excluded there
                    AND ct.pos0 >= cs.pos0 AND ct.pos1 <= cs.pos1
                JOIN cases c ON cs.caseid = c.caseid
                WHERE ct.cid = ?
                GROUP BY cs.caseid, c.name, c.memo
                ORDER BY occurrence_count DESC
            """, (code_id,))

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
        if getattr(self, "db_version", None) != "v14":
            raise UnsupportedSchemaError(
                f"This project uses database schema "
                f"{getattr(self, 'db_version', None) or 'unknown'}; writes "
                f"require schema v14. Open and save the project in QualCoder "
                f"3.8 to upgrade it, then try again."
            )
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

        try:
            cursor = self.conn.execute("""
                INSERT INTO code_text (cid, fid, seltext, pos0, pos1, owner, date, memo, important)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (code_id, file_id, selected_text, start_pos, end_pos, owner, date_str, memo or "",
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

        # Default to a random QualCoder palette color (what the GUI does);
        # validate strictly - '#zzzzzz' passed the old prefix/length check
        # but renders black/undefined in QualCoder's QColor/luminance math
        if color is None:
            color = random.choice(QUALCODER_COLORS)
        if not isinstance(color, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
            raise ValueError(f"color must be hex format #RRGGBB, got {color}")

        # Current timestamp
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            cursor = self.conn.execute("""
                INSERT INTO code_name (name, memo, catid, owner, date, color)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (name, memo or "", category_id, owner, date_str, color))

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

        # Verify coding exists
        coding_check = self.conn.execute(
            "SELECT ctid FROM code_text WHERE ctid = ?", (coding_id,)
        ).fetchone()
        if not coding_check:
            raise ValueError(f"Coding ID {coding_id} does not exist")

        try:
            # Content-only: QualCoder's coded-text memo edit updates ONLY
            # memo, never date or owner (code_text.py:1636 /
            # code_in_all_files.py:399; memos-journals.md §2.4, gotcha #1).
            # The previous version stamped date — a fingerprint that mutated
            # the coding's "coded on" timestamp.
            self.conn.execute(
                "UPDATE code_text SET memo = ? WHERE ctid = ?",
                (memo, coding_id)
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
            The details of the deleted coding

        Raises:
            ValueError: If the coding does not exist
            RuntimeError: If database is read-only or the delete fails
        """
        self._require_write_access()
        existing = self.get_coding(coding_id)
        if existing is None:
            raise ValueError(f"Coding ID {coding_id} does not exist")

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

        # Validate owner
        if not isinstance(owner, str) or not owner.strip():
            raise ValueError("owner must be a non-empty string")

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
        content = content.replace("\r\n", "\n")

        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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

        Matches QualCoder's own "Case file manager" whole-file link exactly
        (case_file_manager.py:197-236): one case_text row with pos0=0 and
        pos1=len(fulltext)-1 for text sources (QualCoder's GUI convention -
        note the -1), or pos0=pos1=0 for non-text sources. Without this row
        the file is invisible to every case-based analysis.

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

        # Whole-file span, QualCoder GUI convention
        fulltext = file_row["fulltext"]
        pos0 = 0
        pos1 = len(fulltext) - 1 if fulltext else 0
        if pos1 < 0:
            pos1 = 0

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
            existing = self.conn.execute(
                "SELECT id, pos0, pos1 FROM case_text WHERE caseid = ? "
                "AND fid = ? AND pos0 <= 0 AND pos1 >= ?",
                (case_id, file_id, pos1)
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

        # Verify the row exists and capture a label for the confirmation
        select_cols = f"{name_col}" if name_col else id_col
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

        # Content-only: QualCoder's memo edits for code_name/code_cat/source/
        # code_text/cases touch ONLY the memo column — never date, never
        # owner (memos-journals.md §2, §5.1; the summary table). '' clears
        # the memo, matching QualCoder's empty-string convention (never NULL).
        # (code_av is the one date-on-edit exception upstream, but it is not
        # one of these targets.)
        try:
            self.conn.execute(
                f"UPDATE {table} SET memo = ? WHERE {id_col} = ?",
                (memo, target_id)
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

        return {
            "target_type": target_type,
            "target_id": target_id,
            "label": label,
            "memo": memo,
            "cleared": memo == "",
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
        try:
            if supercatid is not None:
                self._get_category_row(supercatid)  # parent must exist
            cursor = self.conn.execute(
                "INSERT INTO code_cat (name, owner, date, memo, supercatid) "
                "VALUES (?, ?, ?, ?, ?)",
                (name, owner, date_str, memo or "", supercatid)
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
            # 4. delete the merged-away code definition
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
        return {"merged": True, **preview}

    def preview_delete_code(self, code_id: int) -> Dict[str, Any]:
        """Count the coded data a code delete would destroy (read-only)."""
        code_id = validate_id(code_id, "code_id")
        code = self._get_code_row(code_id)
        text_n = self.conn.execute(
            "SELECT COUNT(*) FROM code_text WHERE cid = ?", (code_id,)
        ).fetchone()[0]
        av_n = self.conn.execute(
            "SELECT COUNT(*) FROM code_av WHERE cid = ?", (code_id,)
        ).fetchone()[0]
        img_n = self.conn.execute(
            "SELECT COUNT(*) FROM code_image WHERE cid = ?", (code_id,)
        ).fetchone()[0]
        return {
            "code": {"id": code_id, "name": code["name"]},
            "text_codings_to_delete": text_n,
            "av_codings_to_delete": av_n,
            "image_codings_to_delete": img_n,
            "total_codings_to_delete": text_n + av_n + img_n,
        }

    def delete_code(self, code_id: int,
                    auto_commit: bool = True) -> Dict[str, Any]:
        """Delete a code AND all its codings (code-edits.md §7).

        Bulk data destruction, matching QualCoder: removes the code_name row
        plus every code_text/code_av/code_image row for the cid. Categories,
        annotations, case links and graph rows are deliberately NOT touched
        (QualCoder leaves them; do not add cleanup it omits). Atomic.
        """
        self._require_write_access()
        preview = self.preview_delete_code(code_id)
        try:
            self.conn.execute("DELETE FROM code_text WHERE cid = ?", (code_id,))
            self.conn.execute("DELETE FROM code_av WHERE cid = ?", (code_id,))
            self.conn.execute("DELETE FROM code_image WHERE cid = ?", (code_id,))
            self.conn.execute("DELETE FROM code_name WHERE cid = ?", (code_id,))
            if auto_commit:
                self.conn.commit()
            logger.info(f"Deleted code {code_id} and its codings")
        except sqlite3.Error as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            _raise_query_error(e, "delete_code", "Failed to delete code")
        return {"deleted": True, **preview}

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

        if not memo.strip():
            # Clear = delete (matches QualCoder exactly)
            return {**self.delete_annotation(annotation_id,
                                             auto_commit=auto_commit),
                    "deleted_because_cleared": True}

        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            self.conn.execute(
                "UPDATE annotation SET memo = ?, date = ? WHERE anid = ?",
                (memo, date_str, annotation_id)
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
        return {**existing, "memo": memo, "date": date_str, "updated": True}

    def delete_annotation(self, annotation_id: int,
                          auto_commit: bool = True) -> Dict[str, Any]:
        """Delete an annotation by anid (never by pos0 — see §4.3 gotcha)."""
        self._require_write_access()
        existing = self.get_annotation(annotation_id)
        if existing is None:
            raise ValueError(f"Annotation ID {annotation_id} does not exist")
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
        return {
            "from_category": {"id": from_category_id, "name": src_cat["name"]},
            "into_category": target_desc,
            "codes_reparented": codes_n,
            "subcategories_reparented": subcats_n,
            "note": "Merging a category reparents its codes and direct "
                    "sub-categories to the target (codings are untouched — "
                    "they key on the code, not the category), then deletes "
                    "the source category.",
        }

    def merge_category(self, from_category_id: int,
                       into_category_id: Optional[int],
                       auto_commit: bool = True) -> Dict[str, Any]:
        """Merge one category into another (or into the top level).

        QualCoder recipe (category-tree.md §9): codes with catid=source ->
        target; sub-categories with supercatid=source -> target; delete the
        source code_cat row; dangling-supercatid sweep. into_category_id of
        None moves everything to the top level (QualCoder's blank option).
        Codings are never touched.
        """
        self._require_write_access()
        preview = self.preview_merge_category(from_category_id,
                                              into_category_id)
        try:
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
        return {"merged": True, **preview}

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
                (name, memo or "", owner, date_str)
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
