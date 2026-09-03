"""Qualcoder MCP Server - Expose Qualcoder data via Model Context Protocol."""

import os
import re
import sys
import json
import shutil
import logging
import sqlite3
import tempfile
import functools
import unicodedata
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp import Context

from .database import (
    QualcoderDatabase,
    DatabaseLockedError,
    DatabaseOpenError,
    UnsupportedSchemaError,
    DB_LOCKED_MESSAGE,
    validate_qda_path,
    validate_coder_name,
    hidden_coder_refusal,
    private_note_refusal,
    MAX_CODER_NAME_LENGTH,
    backup_project,
    qualcoder_lock_state,
    qualcoder_open_message,
    qualcoder_gui_signals,
    normalize_coder,
    hold_project_lock,
    QUALCODER_LOCK_FILENAME,
    position_safe as db_position_safe,
)
from .memo_privacy import extract_ai_memo, strip_private_memos
from .sessions import (SessionManager, AICodingSession, CodingSuggestion,
                       ProposedCode)

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize MCP server
mcp = FastMCP("Qualcoder")
# Advertise OUR version in the MCP handshake (serverInfo.version) instead of
# the mcp SDK's own version, which FastMCP falls back to (track3 L-1). The
# FastMCP constructor has no version parameter in this SDK line, so set it on
# the wrapped low-level server, which reads the attribute at initialize time.
from . import __version__ as _package_version  # noqa: E402
mcp._mcp_server.version = _package_version

# Global database instance and current project path
db: Optional[QualcoderDatabase] = None
current_project_path: Optional[str] = None

# Global session manager for AI coding
session_manager = SessionManager()

# P1-6: most-recently-used project hint. Recorded on every successful
# select_project in the existing on-disk state home (~/.qualcoder_mcp/),
# and appended to "no project selected" errors so a client whose host
# recycled the server process (observed with LM Studio) can recover with
# ONE deterministic select_project call. The selection itself is NEVER
# auto-restored (owner-rejected: it would break explicit-selection
# semantics, and concurrent hosts would clobber each other).
_MRU_FILE = Path.home() / ".qualcoder_mcp" / "mru_project.json"
# The payload is about 120 bytes; anything larger is not ours (S-H1)
MRU_READ_MAX_BYTES = 4096


def _open_mru_tmp():
    """Create the MRU temp file beside the MRU file; returns (fd, Path).

    tempfile.mkstemp opens with O_CREAT|O_EXCL at mode 0600 under an
    unpredictable name: concurrent servers (several MCP hosts on one
    machine) can never share a temp name (QA round 1, F20), a symlink
    pre-planted at a would-be name is refused rather than written
    through, crash litter is never reopened, and the file is
    owner-readable only (S-H1). Mode bits are ignored on Windows.
    """
    fd, name = tempfile.mkstemp(dir=_MRU_FILE.parent,
                                prefix=f"{_MRU_FILE.name}.", suffix=".tmp")
    return fd, Path(name)


def _remember_mru_project(project_path: str) -> None:
    """Record the machine's most-recently-used project (best effort).

    Failures never break project selection; a corrupt or unwritable
    state file just means no hint later. The write goes through an
    exclusively created per-process temp file and an atomic replace,
    and a failed write removes its own temp file.
    """
    tmp = None
    try:
        _MRU_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "project_path": str(project_path),
            "updated": datetime.now().isoformat(timespec="seconds"),
        }
        fd, tmp = _open_mru_tmp()
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        tmp.replace(_MRU_FILE)
    except Exception as e:
        logger.debug(f"Could not record MRU project: {e}")
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass


def _mru_path_is_canonical(path: Any) -> bool:
    """Only the shape select_project itself records may be echoed.

    select_project records validate_qda_path's canonical result, always
    <folder>.qda/data.qda. Anything else in the state file (a tampered
    or foreign entry) is not echoed into the conversation, and a path
    carrying control, line-separator or bidirectional formatting
    characters is refused outright, so the hint can never smuggle
    instruction-like text (S-H6). Pure function; no filesystem access.
    """
    if not isinstance(path, str) or not path.strip():
        return False
    for ch in path:
        cat = unicodedata.category(ch)
        if cat in ("Cc", "Zl", "Zp") or 0x202A <= ord(ch) <= 0x202E \
                or 0x2066 <= ord(ch) <= 0x2069:
            return False
    p = Path(path)
    return p.name == "data.qda" and p.parent.suffix == ".qda"


def _mru_hint() -> str:
    """A recovery hint naming the machine's last-used project, or ''.

    The hint appears only when the recorded path has the canonical shape
    select_project records and still exists as a file; missing, corrupt,
    oversized or wrong-shaped MRU state degrades silently to the plain
    error text.
    """
    try:
        with open(_MRU_FILE, "r", encoding="utf-8") as f:
            raw = f.read(MRU_READ_MAX_BYTES + 1)
        if len(raw) > MRU_READ_MAX_BYTES:
            return ""
        data = json.loads(raw)
        path = data.get("project_path")
        if _mru_path_is_canonical(path) and Path(path).is_file():
            return (f" The last project used on this machine was {path}. "
                    f"Use select_project with that path to continue "
                    f"with it.")
    except Exception:
        pass
    return ""


def _no_project_message() -> str:
    """The uniform "no project selected" error text (with MRU hint)."""
    return ("No Qualcoder project selected. Use 'list_available_projects' "
            "to discover projects, then 'select_project' to choose one. "
            "Or set QUALCODER_PROJECT_PATH environment variable."
            + _mru_hint())


# Fixed, path-free text for any database that will not open or errors
# mid-read outside select_project (which has project-scoped wording of its
# own). The sqlite message itself is logged, never returned (S-H4).
DB_UNAVAILABLE_ERROR = (
    "Database error: the project file may be locked or corrupted. If "
    "QualCoder is open, close it and retry; otherwise consider restoring a "
    "backup (see list_backups)."
)


def _tool_guard(fn):
    """Convert anticipated exceptions into sanitized error JSON.

    Applied to every MCP tool so that failures (no project selected, locked
    database, old schema, validation errors, corruption) reach the client as
    actionable error JSON instead of raw tracebacks.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except DatabaseLockedError as e:
            return json.dumps({"error": str(e)})
        except UnsupportedSchemaError as e:
            return json.dumps({"error": str(e)})
        except DatabaseOpenError as e:
            # Before the generic ValueError branch (it is a subclass): the
            # sqlite text goes to the log, never into the conversation
            # (S-H4). select_project keeps its own project-scoped wording.
            logger.error(f"Database would not open in {fn.__name__}: {e}")
            return json.dumps({"error": DB_UNAVAILABLE_ERROR})
        except (ValueError, TypeError) as e:
            return json.dumps({"error": str(e)})
        except FileNotFoundError as e:
            logger.error(f"Not found in {fn.__name__}: {e}")
            return json.dumps({"error": "File or project not found."})
        except OSError as e:
            logger.error(f"OS error in {fn.__name__}: {e}")
            return json.dumps({"error": "File system operation failed — check "
                                         "disk space and permissions."})
        except sqlite3.Error as e:
            logger.error(f"SQLite error in {fn.__name__}: {e}")
            return json.dumps({"error": DB_UNAVAILABLE_ERROR})
        except RuntimeError as e:
            logger.error(f"Runtime error in {fn.__name__}: {e}")
            return json.dumps({"error": str(e)})
    return wrapper


def discover_projects(search_paths: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Discover .qda files in common locations.

    Args:
        search_paths: Optional list of paths to search. If None, uses defaults.

    Returns:
        List of discovered projects with path, name, and size info
    """
    if search_paths is None:
        home = Path.home()
        search_paths = [
            str(home / "Documents" / "QualCoder_projects"),
            str(home / "Documents" / "QualCoder"),
            str(home / "QualCoder"),
            str(home / "Documents"),
        ]

    projects = []
    seen_paths = set()

    for search_path in search_paths:
        path = Path(search_path)
        if not path.exists():
            continue

        # Search recursively for .qda files (max 3 levels deep)
        try:
            for qda_file in path.rglob("*.qda"):
                # Avoid duplicates and limit depth
                if qda_file in seen_paths:
                    continue

                # Check depth (don't go too deep)
                try:
                    relative = qda_file.relative_to(path)
                    if len(relative.parts) > 3:
                        continue
                except ValueError:
                    continue

                # Skip the data.qda INSIDE a .qda project folder — the folder
                # itself is the project and is listed separately (previously
                # every project appeared twice, once as "data")
                if (qda_file.name == "data.qda"
                        and qda_file.parent.suffix.lower() == ".qda"):
                    continue

                # Skip backup folders: this server's *_backup_* snapshots and
                # QualCoder's own *_BKUP_* copies are not working projects
                # (they polluted the list — 2 projects showed as 10 entries)
                if "_backup_" in qda_file.stem or "_BKUP_" in qda_file.stem:
                    continue

                seen_paths.add(qda_file)

                try:
                    stat = qda_file.stat()
                    projects.append({
                        "path": str(qda_file),
                        "name": qda_file.stem,
                        "directory": str(qda_file.parent),
                        "size_mb": round(stat.st_size / (1024 * 1024), 2),
                        "modified": stat.st_mtime
                    })
                except (OSError, PermissionError) as e:
                    logger.debug(f"Cannot access {qda_file}: {e}")
                    continue

        except (PermissionError, OSError) as e:
            logger.debug(f"Cannot search {search_path}: {e}")
            continue

    # Sort by most recently modified
    projects.sort(key=lambda x: x["modified"], reverse=True)
    return projects


def switch_project(project_path: str, read_only: bool = True) -> None:
    """Switch to a different project.

    Args:
        project_path: Path to the .qda file
        read_only: Open in read-only mode (default: True)

    Raises:
        ValueError: If path is invalid
        FileNotFoundError: If file doesn't exist
        RuntimeError: If database connection fails
    """
    global db, current_project_path

    # Close existing connection
    if db is not None:
        try:
            db.close()
        except Exception as e:
            logger.warning(f"Error closing previous connection: {e}")
        finally:
            db = None

    # Connect to new project (read-only by default)
    db = QualcoderDatabase(project_path, read_only=read_only)
    current_project_path = project_path
    logger.info(f"Switched to project: {Path(project_path).name} (read_only={read_only})")


def get_db(read_only: bool = True) -> QualcoderDatabase:
    """Get or initialize the database connection.

    Args:
        read_only: If True (default), opens in read-only mode.
                  Pass False only for write operations like apply_codings.

    Raises:
        ValueError: If no project specified or invalid
        FileNotFoundError: If database file doesn't exist
        RuntimeError: If database connection fails
    """
    global db, current_project_path

    # If we need write access but current connection is read-only, reopen.
    # IMPORTANT: open the new connection BEFORE closing the old one — if the
    # upgrade fails (e.g. QualCoder holds a lock), the existing read-only
    # connection must remain usable rather than leaving a dead global (F1).
    if db is not None and not read_only and db.read_only:
        logger.info("Upgrading database connection to read-write mode")
        new_db = QualcoderDatabase(current_project_path, read_only=False)
        old_db, db = db, new_db
        try:
            old_db.close()
        except Exception:
            pass
        return db

    # If we have a project path set but db is None, try to reconnect
    if db is None and current_project_path is not None:
        logger.warning(f"Database connection lost but project path exists: {Path(current_project_path).name}. Attempting to reconnect...")
        try:
            db = QualcoderDatabase(current_project_path, read_only=read_only)
            logger.info(f"Successfully reconnected to: {Path(current_project_path).name}")
            return db
        except Exception as e:
            logger.error(f"Failed to reconnect to database: {e}")
            # Fall through to normal error handling

    if db is None:
        # Try environment variable first
        db_path = os.environ.get("QUALCODER_PROJECT_PATH")

        if not db_path:
            raise ValueError(_no_project_message())

        try:
            db = QualcoderDatabase(db_path, read_only=read_only)
            current_project_path = db_path
            # Log only filename, not full path (security best practice)
            logger.info(f"Connected to Qualcoder database: {Path(db_path).name}")
        except (ValueError, FileNotFoundError, RuntimeError) as e:
            logger.error(f"Failed to connect to database: {e}")
            raise

    return db


def _downgrade_to_readonly():
    """Downgrade the global database connection back to read-only mode.

    Called after write operations complete (success or failure) to ensure
    subsequent read operations don't accidentally hold a writable connection.
    """
    global db
    if db is not None and not db.read_only:
        logger.info("Downgrading database connection back to read-only mode")
        try:
            db.close()
        except Exception:
            pass
        try:
            db = QualcoderDatabase(current_project_path, read_only=True)
        except Exception as e:
            logger.error(f"Failed to downgrade to read-only: {e}")
            db = None


def _snippet(text: Optional[str], max_len: int = 80) -> str:
    """Truncate text for inclusion in error messages."""
    if not text:
        return ""
    return text if len(text) <= max_len else text[:max_len] + "…"


def _coder_visibility_note(coder: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Disclosure block for reads shaped by QC 4.0 coder visibility (P1-3).

    Returned only when the project actually hides coders. Reports the
    COUNT of hidden coders, never their names. With an explicit coder
    filter the read went to the base tables, and the note says so
    instead (methodological transparency either way).
    """
    coder = normalize_coder(coder)  # blank means no filter (F10)
    try:
        hidden = get_db().hidden_coder_count()
    except Exception:
        return None
    if hidden <= 0:
        return None
    if coder is not None:
        return {
            "hidden_coder_filter": "bypassed",
            "hidden_coders": hidden,
            "note": f"This project hides {hidden} coder(s) in QualCoder, "
                    f"but the explicit coder filter read the full base "
                    f"data for that coder instead (the same override "
                    f"QualCoder 4.0's own AI uses).",
        }
    return {
        "hidden_coder_filter": "applied",
        "hidden_coders": hidden,
        "note": f"This project hides {hidden} coder(s) (a QualCoder 4.0 "
                f"per-coder visibility setting stored in the project). "
                f"Results reflect what the user sees in QualCoder; tools "
                f"that take a coder argument (get_coded_segments, for "
                f"example) read a specific coder's rows from the full "
                f"data instead.",
    }


def _refuse_existing_row_change(kind: str, row_id: int, *,
                                allow_hidden_coder: bool,
                                deleting: bool,
                                confirm_private_note_deletion: bool = False
                                ) -> Optional[Dict[str, Any]]:
    """Pre-check, on the read-only connection and BEFORE any backup, the
    two owner-ruled guards on writes that target an existing coding or
    annotation row by id.

    Tier 2: a row owned by a coder the project hides is refused unless
    allow_hidden_coder. S-P2: a DELETE of a row whose memo carries a
    '#####' private note is refused unless confirm_private_note_deletion.
    When both apply, both refusals are reported in one response. The
    texts are count-free, name-free and content-free (see
    hidden_coder_refusal / private_note_refusal). Returns None when the
    write may proceed, an error dict otherwise; a missing row yields the
    usual "does not exist" error. The db layer repeats the same checks.
    """
    label = kind.capitalize()
    status = get_db().existing_row_status(kind, row_id)
    if status is None:
        return {"error": f"{label} ID {row_id} does not exist"}
    refusals = []
    refused = []
    if status["hidden"] and not allow_hidden_coder:
        refusals.append(hidden_coder_refusal(label, row_id))
        refused.append("hidden_coder")
    if deleting and status["private_note"] and not confirm_private_note_deletion:
        refusals.append(private_note_refusal(label, row_id))
        refused.append("private_note")
    if refusals:
        return {"error": " ".join(refusals), "refused": refused,
                "nothing_changed": True}
    return None


def _private_note_backup_note(result: Any, status: Dict[str, bool],
                              create_backup: bool) -> None:
    """State in a delete result that a backup was taken for a row carrying
    a private note (S-P2 (a)); create_backup=false does not apply there."""
    if not isinstance(result, dict) or "error" in result:
        return
    if status.get("private_note"):
        result["backup_note"] = (
            "A backup was taken before this delete because the row carried "
            "a private note; create_backup=false does not apply to such "
            "rows." if not create_backup else
            "A backup was taken before this delete; the row carried a "
            "private note, for which a backup is always taken.")


def _attach_hidden_target_note(result: Any, key: Optional[str] = None) -> None:
    """Add the coder-visibility disclosure to a write echo for a hidden row.

    Write tools that target a row by id (delete_coding, update_annotation,
    delete_annotation) reach hidden coders' rows too, by upstream parity;
    when the db layer flags the row as hidden_coder_row the echo is ids
    only and this note explains why (count of hidden coders, never a
    name). No-op for visible rows and error results (S-MAJ).
    """
    if not isinstance(result, dict) or "error" in result:
        return
    target = result.get(key) if key else result
    if isinstance(target, dict) and target.get("hidden_coder_row"):
        note = _coder_visibility_note()
        if note is not None:
            result["coder_visibility"] = note


def _ai_json(payload: Any, **dumps_kwargs) -> str:
    """json.dumps for AI-facing results, with memo privacy applied.

    Every string under a 'memo' key is reduced to its public part: the
    QC 4.0 convention keeps everything from the first '#####' marker
    onward private from the AI (see memo_privacy.py). The strip is
    silent by owner ruling. Used by every tool and resource that can
    return memo content into the conversation; the file-export tools
    (REFI-QDA, codebook, report and CSV files) deliberately do NOT use
    it, because QualCoder's own exports carry full memos (parity).
    """
    return json.dumps(strip_private_memos(payload), **dumps_kwargs)


def _current_project_folder() -> Path:
    """The .qda folder of the currently open project."""
    return validate_qda_path(current_project_path).parent


def _qualcoder_open_error() -> Optional[Dict[str, Any]]:
    """Error dict when QualCoder currently has this project open.

    QualCoder's only concurrency control is its project_in_use.lock
    heartbeat file — it holds NO SQLite lock while idle, so writes would
    succeed at the SQLite level and then be silently corrupted or deleted
    by QualCoder (snapshot-based text editor, open-time orphan cleanup and
    VACUUM). Every write path must call this before touching the database.
    """
    state, holder = qualcoder_lock_state(_current_project_folder())
    if state == "active":
        return {"error": qualcoder_open_message(holder)}
    return None


def _schema_block() -> Dict[str, Any]:
    """The schema report for get_current_project / get_project_summary:
    version string (informational), the capability probes that actually
    decide behaviour, and the write-support verdict with its reason, so
    an AI consumer can explain the situation instead of guessing."""
    db = get_db()
    supported, reason, overridden = db.write_support()
    caps = getattr(db, "capabilities", None)
    block: Dict[str, Any] = {
        "databaseversion": getattr(db, "db_version", None),
        "capabilities": caps.to_dict() if caps is not None else {},
        "write_support": supported,
    }
    if reason:
        block["reason"] = reason
    if overridden:
        block["override_active"] = True
    return block


def _write_gate_error() -> Optional[Dict[str, Any]]:
    """Combined pre-write gate: schema capabilities + QualCoder lock file.

    Returns an error dict when the project's schema is below the v14
    capability floor (the coder_names table, upstream's own v14 marker),
    or reports a version newer than the verified ceiling without the
    explicit override, or when QualCoder currently has the project open.
    Capability probes, never version strings, decide support (S1); the
    database layer enforces the same gate in _require_write_access
    (defense in depth); the early check here produces a clean error
    before any backup is made.
    """
    supported, reason, _overridden = get_db().write_support()
    if not supported:
        return {"error": reason}
    return _qualcoder_open_error()


def _recheck_lock_before_commit(project_folder: Path, held: bool) -> None:
    """Close the TOCTOU window between pre-write checks and commit.

    When our own lock is held, QualCoder cannot have opened the project in
    between (it refuses on a fresh lock). When we proceeded over a stale
    foreign lock we hold nothing, so re-check right before committing.

    Raises:
        DatabaseLockedError: If QualCoder opened the project mid-write
    """
    if held:
        return
    state, holder = qualcoder_lock_state(project_folder)
    if state == "active":
        raise DatabaseLockedError(qualcoder_open_message(holder))


def _resolve_category_by_name(name: str):
    """Resolve a category name to its catid, refusing ambiguous matches.

    Exact (case-sensitive) match wins; otherwise a UNIQUE case-insensitive
    match is used. code_cat's unique(name) is BINARY, so 'Theme' and
    'theme' can legally coexist — with both present a case-insensitive
    lookup must refuse and list the candidates instead of silently picking
    the first one (QA5-1).

    Returns:
        (category_id, None) on success, (None, error_dict) otherwise.
    """
    cats = get_db().list_categories()
    exact = [c for c in cats if c["name"] == name]
    if len(exact) == 1:
        return exact[0]["id"], None
    ci = [c for c in cats if c["name"].lower() == str(name).lower()]
    if len(ci) == 1:
        return ci[0]["id"], None
    if len(ci) > 1:
        return None, {
            "error": f"Category name '{name}' is ambiguous — {len(ci)} "
                     f"categories differ only by letter case. Use the exact "
                     f"spelling of the one you mean (their ids are listed).",
            "candidates": [{"id": c["id"], "name": c["name"]} for c in ci],
        }
    return None, {
        "error": f"Category '{name}' not found",
        "available_categories": sorted(c["name"] for c in cats)[:50],
    }


# P1-2 attribution config: one configurable coder name for every row this
# server writes. Distinct-by-default (owner verdict b): the default stays
# "AI Coding Assistant" for continuity with existing projects; setting the
# env var to "AI Agent" (QualCoder 4.0's own AI owner string,
# ai_mcp_server.py:85) groups this server's writes with the built-in
# assistant's under 4.0's per-coder visibility, undo and report tooling.
AI_CODER_NAME_ENV = "QUALCODER_MCP_AI_CODER_NAME"
DEFAULT_AI_CODER_NAME = "AI Coding Assistant"
MAX_AI_CODER_NAME_LENGTH = MAX_CODER_NAME_LENGTH


def _ai_coder_name() -> str:
    """The configured coder name for rows this server writes.

    Reads QUALCODER_MCP_AI_CODER_NAME; unset means the default. A set
    value goes through validate_coder_name, the rule set shared with the
    tool-supplied owner arguments (non-empty after trimming, at most 80
    characters, no control, line-separator or bidirectional formatting
    characters, no '#####' marker), and an invalid one raises, so main()
    refuses to start rather than writing rows under a broken name.

    Raises:
        ValueError: If the configured value is invalid.
    """
    raw = os.environ.get(AI_CODER_NAME_ENV)
    if raw is None:
        return DEFAULT_AI_CODER_NAME
    if not raw.strip():
        raise ValueError(
            f"{AI_CODER_NAME_ENV} is set but empty. Set it to the coder "
            f"name this server should write under (for example "
            f"\"{DEFAULT_AI_CODER_NAME}\" or QualCoder 4.0's \"AI Agent\"), "
            f"or unset it to use the default.")
    return validate_coder_name(raw, AI_CODER_NAME_ENV)


def _default_owner() -> str:
    """Attribution owner for MCP-authored rows.

    Every row this server writes (codings, memos with provenance,
    annotations, journal entries, imports, cases, attributes) carries
    the configured AI coder name, so AI work is distinguishable from
    the researcher's and manageable as one coder in QualCoder (verdict
    b; 4.0 attributes all its AI writes the same way). Configure via
    QUALCODER_MCP_AI_CODER_NAME; default "AI Coding Assistant".
    """
    return _ai_coder_name()


def _attach_skipped_symlinks(result: Any, report: Optional[Dict[str, Any]],
                             prefix: str = "", always: bool = False) -> None:
    """Surface the symlinks a backup or project copy skipped (S-P1).

    Adds "<prefix>skipped_symlinks" (a count) and, when any were skipped,
    "<prefix>skipped_symlink_names" (project-relative, at most 20) so a
    user with a legitimately linked media folder learns it is not in the
    copy. With always=False the keys appear only when something was
    skipped.
    """
    if not isinstance(result, dict):
        return
    skipped = list((report or {}).get("skipped_symlinks") or [])
    if skipped or always:
        result[f"{prefix}skipped_symlinks"] = len(skipped)
    if skipped:
        result[f"{prefix}skipped_symlink_names"] = skipped[:20]
        result[f"{prefix}skipped_symlinks_note"] = (
            "Symlinks that point outside the project folder, or that "
            "dangle, are not followed into backups or copies; the entries "
            "named here are absent from this copy.")


def _perform_write(op, create_backup: bool = True,
                   backup_fail_detail: str = "nothing was written"):
    """Run a mutation under the full write-safety discipline.

    This is the single, uniform implementation of the write pattern every
    write tool must follow: refuse on pre-v14 schema and while QualCoder
    has the project open (heartbeat lock), upgrade to read-write, hold
    QualCoder's project lock, back up before writing, run the mutation with
    auto_commit deferred, re-check the lock to close the TOCTOU window,
    commit, and downgrade to read-only on EVERY exit path including
    exceptions.

    Args:
        op: Callable op(write_db) that performs the mutation with
            auto_commit=False and returns a JSON-serializable success dict.
        create_backup: Create a timestamped backup before writing.
        backup_fail_detail: Tail of the "nothing was ..." message on backup
            failure (e.g. "nothing was deleted").

    Returns:
        The op's success dict (with backup_path added when a backup was
        made), or an {"error": ...} dict. Callers json.dumps the result.

    Raises:
        DatabaseLockedError: If QualCoder grabs the project mid-write (the
            tool guard converts it to a friendly error).
    """
    gate = _write_gate_error()
    if gate is not None:
        return gate

    project_folder = _current_project_folder()
    write_db = get_db(read_only=False)
    backup_path = None
    committed = False
    try:
        with hold_project_lock(project_folder) as lock_held:
            if create_backup:
                try:
                    backup_path = write_db.backup_before_write()
                except Exception as e:
                    logger.error(f"Failed to create backup: {e}")
                    return {
                        "error": "Failed to create a backup — check disk space "
                                 "and permissions. Nothing was written.",
                        "message": f"Aborting to protect your data — "
                                   f"{backup_fail_detail}.",
                    }
            try:
                result = op(write_db)
                _recheck_lock_before_commit(project_folder, lock_held)
                write_db.conn.commit()
                committed = True
            except DatabaseLockedError:
                raise
            except (ValueError, RuntimeError) as e:
                return {"error": str(e)}
    finally:
        # Unconditional cleanup on EVERY exit path (SEC M-1). The previous
        # shape only rolled back / downgraded for DatabaseLockedError,
        # ValueError and RuntimeError — a commit-time sqlite3.Error (disk
        # I/O, SQLITE_BUSY) escaped past both, leaving the GLOBAL connection
        # read-write with an open transaction; the next write reused it and
        # could silently co-commit the failed operation's changes. A finally
        # block cannot be skipped: roll back anything still in flight, then
        # always return the connection to read-only.
        if not committed:
            try:
                if write_db.conn is not None and write_db.conn.in_transaction:
                    write_db.conn.rollback()
            except Exception:
                pass
        _downgrade_to_readonly()

    if backup_path:
        result["backup_path"] = str(backup_path)
        _attach_skipped_symlinks(
            result, getattr(write_db, "last_backup_report", None),
            prefix="backup_")
    schema_warning = write_db.schema_write_warning()
    if schema_warning and isinstance(result, dict) and "error" not in result:
        result["schema_warning"] = schema_warning
    return result


def _check_session_project(session: AICodingSession) -> Optional[Dict[str, Any]]:
    """Verify a session belongs to the currently open project.

    Session-consuming writes are bound to the project the session was
    created in — applying a session to a different project would silently
    corrupt it (cross-project write trapdoor).

    Returns:
        None if the session matches the current project, otherwise a dict
        suitable for JSON error output.
    """
    if current_project_path is None:
        return {
            "error": "No Qualcoder project selected. Use 'list_available_projects' "
                     "and 'select_project' to open one." + _mru_hint()
        }
    try:
        session_db_path = validate_qda_path(session.project_path)
    except DatabaseLockedError:
        raise
    except Exception:
        return {
            "error": "The project this session was created in could not be found "
                     "(it may have been moved or deleted). Sessions can only be "
                     "used with the project they were created in."
        }
    try:
        current_db_path = validate_qda_path(current_project_path)
    except DatabaseLockedError:
        raise
    except Exception:
        return {"error": "The currently open project could not be resolved. "
                         "Re-open it with select_project."}

    if session_db_path != current_db_path:
        return {
            "error": "This session belongs to a different project than the one "
                     "currently open. Writes are bound to the session's project — "
                     "open it with select_project first.",
            "session_project": session_db_path.parent.name,
            "current_project": current_db_path.parent.name,
        }
    return None


def _find_occurrences(text: str, needle: str, max_hits: int = 11) -> List[int]:
    """Find start offsets of needle in text (including overlapping hits)."""
    hits = []
    pos = text.find(needle)
    while pos != -1 and len(hits) < max_hits:
        hits.append(pos)
        pos = text.find(needle, pos + 1)
    return hits


def _resolve_segment_positions(
    fulltext: str,
    start_pos: Any,
    end_pos: Any,
    segment_text: str,
):
    """Verify (or recover) the positions of a suggested segment.

    The invariant enforced on every write is fulltext[start:end] ==
    segment_text (character/code-point offsets, matching how QualCoder and
    this server store positions). Because language models frequently
    miscount character offsets, a mismatch falls back to locating
    segment_text in the file: exactly one occurrence -> positions are
    corrected; zero or several -> the suggestion is rejected with an
    explanatory error.

    Returns:
        (ok, start, end, corrected, error) where error is a dict with
        'reason' and snippet context when ok is False.
    """
    n = len(fulltext)
    have_positions = (
        isinstance(start_pos, int) and not isinstance(start_pos, bool)
        and isinstance(end_pos, int) and not isinstance(end_pos, bool)
    )

    # Qt's selectedText() stores U+2029 (paragraph separator) where the
    # fulltext has \n, and QualCoder never normalizes (code_text.py:3763) —
    # so text copied from GUI-created codings may carry U+2029. Positions
    # are authoritative; tolerate the substitution when comparing.
    needle = segment_text.replace("\u2029", "\n")

    if have_positions and 0 <= start_pos < end_pos <= n:
        if fulltext[start_pos:end_pos] in (segment_text, needle):
            return True, start_pos, end_pos, False, None

    # Positions missing, out of range, or not matching: locate the text
    hits = _find_occurrences(fulltext, needle)
    if len(hits) == 1:
        start = hits[0]
        # End is computed from the NEEDLE (the string actually located).
        # Today the U+2029 normalization is length-preserving, but any
        # future normalization that is not 1:1 must not corrupt the end
        # offset (text-positions.md RISK-TP3).
        return True, start, start + len(needle), have_positions, None
    if len(hits) == 0:
        error = {
            "reason": "segment_text was not found in the file — it must be an "
                      "exact, verbatim excerpt of the file text",
            "provided_snippet": _snippet(segment_text),
        }
        if have_positions and 0 <= start_pos < min(end_pos, n):
            error["expected_snippet"] = _snippet(fulltext[start_pos:min(end_pos, n)])
        return False, None, None, False, error
    return False, None, None, False, {
        "reason": f"segment_text occurs {'more than 10' if len(hits) > 10 else len(hits)} "
                  f"times in the file and the given positions do not match any of "
                  f"them exactly — provide the correct start_pos/end_pos",
        "provided_snippet": _snippet(segment_text),
    }


# ---------------------------------------------------------------------------
# Span alternatives (v0.8 tester-feedback amendment, design-panel revision):
# whenever a span is verified, precompute up to two ready-made adjustments so
# a researcher can fix span length with one pick instead of describing
# offsets. Stored copies are PRESENTATIONAL — use_alternative recomputes from
# the current fulltext at edit time. Deterministic, code-point-safe, no new
# dependencies. Heuristics (documented):
# - Sentences: ONE global segmentation of the fulltext; sentence ends are
#   [.!?]+ runs, optionally followed by closing quotes/brackets, before
#   whitespace or end-of-text. Abbreviation over-splitting ("Dr.") is
#   tolerated because "shorter" picks the LONGEST wholly-contained sentence
#   (fragments lose).
# - Paragraphs: blank lines in any newline convention (\r\n\r\n, \n\n) or
#   U+2029. If the file has NO blank-line boundary at all (single-newline
#   speaker-turn transcripts), a single newline IS the boundary — the
#   speaker turn is the quotable unit.
# - Speaker labels ("Name:", "**Name:**", "NAME [00:01:23]:") are stripped
#   from the front of "longer" spans so quotes start with speech, and the
#   ±1-sentence fallback never crosses into another speaker's turn.
# - Floors: "shorter" is omitted when degenerate (< 40 code points or < 25%
#   of the span); any alternative is omitted when its boundaries move by
#   < 15 code points or < 10% of the span (no filler alternatives).
# ---------------------------------------------------------------------------

_SENTENCE_END_RE = re.compile(r"[.!?]+[\"'\u201d\u2019)\]]*(?=\s|$)")
_PARAGRAPH_SEP_RE = re.compile(r"(?:\r?\n){2,}|\u2029")
_SINGLE_NEWLINE_RE = re.compile(r"\r?\n")
# Deterministic speaker-label prefix: optional **, a name, optional
# [timestamp], a colon (optionally inside the closing **), trailing blanks
_SPEAKER_LABEL_RE = re.compile(
    r"(?:\*\*)?[A-Za-z][A-Za-z0-9 ._\-]{0,40}?(?:\*\*)?"
    r"(?:\s*\[[^\]\n]{1,40}\])?\s*:(?:\*\*)?[ \t]*")
# How far beyond the span the ±1-sentence fallback may reach
_ALTERNATIVE_SCAN_WINDOW = 2000
# Enclosing-paragraph size cap (code points): beyond max(this, 4x span) the
# paragraph is unhelpfully large -> fall back to ±1 sentence
_PARAGRAPH_CAP = 1500
# Materiality floor: an alternative must move the boundaries by at least
# this many code points AND at least 10% of the span length
_MATERIALITY_CP = 15
# "shorter" degeneracy floor
_SHORTER_MIN_CP = 40


def _trim_span(fulltext: str, start: int, end: int):
    """Shrink [start,end) past leading/trailing whitespace (verbatim)."""
    while start < end and fulltext[start].isspace():
        start += 1
    while end > start and fulltext[end - 1].isspace():
        end -= 1
    return start, end


def _sentence_spans_global(fulltext: str):
    """The single deterministic sentence segmentation of the fulltext."""
    spans = []
    s = 0
    for m in _SENTENCE_END_RE.finditer(fulltext):
        s2, e2 = _trim_span(fulltext, s, m.end())
        if s2 < e2:
            spans.append((s2, e2))
        s = m.end()
    s2, e2 = _trim_span(fulltext, s, len(fulltext))
    if s2 < e2:
        spans.append((s2, e2))
    return spans


def _crosses_speaker_turn(fulltext: str, lo: int, hi: int) -> bool:
    """True if (lo,hi) contains a newline that starts a speaker-label line
    — extending a quote across it would splice another speaker's words."""
    i = fulltext.find("\n", max(0, lo), hi)
    while i != -1:
        if _SPEAKER_LABEL_RE.match(fulltext, i + 1):
            return True
        i = fulltext.find("\n", i + 1, hi)
    return False


def _span_preview(text: str, head: int = 60, tail: int = 60) -> str:
    """Token-frugal preview: newline-flattened, first+last ~60 chars."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= head + tail + 5:
        return text
    return f"{text[:head]} […] {text[-tail:]}"


def _material(alt_start: int, alt_end: int, start: int, end: int) -> bool:
    """Materiality floor: boundaries must move >= 15 cp and >= 10% of span."""
    movement = abs(alt_start - start) + abs(alt_end - end)
    span_len = end - start
    return movement >= _MATERIALITY_CP and movement >= 0.10 * span_len


def _alternative_entry(label: str, unit: str, fulltext: str,
                       start: int, end: int):
    return {
        "label": label,
        "unit": unit,                       # render gloss, e.g. "1 sentence"
        "start_pos": start,
        "end_pos": end,
        "preview": _span_preview(fulltext[start:end]),
        "length": end - start,              # code points
    }


def _compute_span_alternatives(fulltext: str, start: int, end: int):
    """Up to two deterministic span adjustments for [start,end).

    - "shorter": the LONGEST sentence wholly contained in the span (tie ->
      earliest), from the global segmentation; omitted when no complete
      sentence fits, when it equals the trimmed span, or when degenerate.
    - "longer": the enclosing paragraph (capped; speaker label stripped so
      the quote starts with speech), else ± one sentence without crossing
      into another speaker's turn; omitted at document boundaries or when
      it would add only a label. All slices verbatim fulltext.
    """
    alternatives = []
    n = len(fulltext)
    start, end = max(0, start), min(end, n)
    if start >= end:
        return alternatives
    t_start, t_end = _trim_span(fulltext, start, end)
    span_len = end - start
    sentences = _sentence_spans_global(fulltext)

    # ---- shorter ----
    contained = [sp for sp in sentences
                 if sp[0] >= t_start and sp[1] <= t_end]
    if contained:
        # longest wins (abbreviation fragments lose); tie -> earliest
        core = max(contained, key=lambda sp: (sp[1] - sp[0], -sp[0]))
        c_len = core[1] - core[0]
        if (core != (t_start, t_end)
                and c_len >= _SHORTER_MIN_CP
                and c_len >= 0.25 * span_len
                and _material(core[0], core[1], start, end)):
            alternatives.append(_alternative_entry(
                "shorter", "1 sentence", fulltext, core[0], core[1]))

    # ---- longer ----
    sep_re = _PARAGRAPH_SEP_RE
    turn_mode = False
    if not sep_re.search(fulltext) and "\n" in fulltext:
        # No blank-line boundaries anywhere: single newlines delimit
        # speaker turns — the turn is the quotable unit
        sep_re = _SINGLE_NEWLINE_RE
        turn_mode = True
    para_start = 0
    for m in sep_re.finditer(fulltext, 0, start):
        para_start = m.end()
    m = sep_re.search(fulltext, end)
    para_end = m.start() if m else n
    para_start, para_end = _trim_span(fulltext, para_start, para_end)

    longer_span = None
    longer_unit = None
    cap = max(_PARAGRAPH_CAP, 4 * span_len)
    if (para_start <= t_start and t_end <= para_end
            and (para_start, para_end) != (t_start, t_end)
            and (para_end - para_start) <= cap):
        s0 = para_start
        label_stripped = False
        if s0 == 0 or fulltext[s0 - 1] in "\n\u2029":
            lm = _SPEAKER_LABEL_RE.match(fulltext, s0, para_end)
            # strip only a label that lies entirely BEFORE the chosen span
            if lm and lm.end() <= t_start and lm.end() < para_end:
                s0 = lm.end()
                label_stripped = True
        s0, e0 = _trim_span(fulltext, s0, para_end)
        if _material(s0, e0, start, end):
            longer_span = (s0, e0)
            longer_unit = ("full speaker turn"
                           if (turn_mode or label_stripped) else "paragraph")
    if longer_span is None:
        # ± one sentence, never crossing into another speaker's turn
        prev_candidates = [sp for sp in sentences
                           if sp[1] <= t_start
                           and t_start - sp[0] <= _ALTERNATIVE_SCAN_WINDOW]
        next_candidates = [sp for sp in sentences
                           if sp[0] >= t_end
                           and sp[1] - t_end <= _ALTERNATIVE_SCAN_WINDOW]
        cand_start, cand_end = t_start, t_end
        if prev_candidates:
            prev = prev_candidates[-1]
            if not _crosses_speaker_turn(fulltext, prev[0] - 1, t_start):
                cand_start = prev[0]
        if next_candidates:
            nxt = next_candidates[0]
            if not _crosses_speaker_turn(fulltext, t_end, nxt[1]):
                cand_end = nxt[1]
        cand_start, cand_end = _trim_span(fulltext, cand_start, cand_end)
        if ((cand_start, cand_end) != (t_start, t_end)
                and _material(cand_start, cand_end, start, end)):
            longer_span = (cand_start, cand_end)
            longer_unit = "±1 sentence"
    if longer_span is not None:
        existing = {(a["start_pos"], a["end_pos"]) for a in alternatives}
        if longer_span not in existing:
            alternatives.append(_alternative_entry(
                "longer", longer_unit, fulltext,
                longer_span[0], longer_span[1]))

    return alternatives


def _alternative_gloss(alt: Dict[str, Any]) -> str:
    """Render form: 'shorter (1 sentence, 89 chars)' — chars = code points."""
    return f"{alt['label']} ({alt['unit']}, {alt['length']} chars)"


# ============================================================================
# RESOURCES - Read-only data access
# ============================================================================

@mcp.resource("qualcoder://project/info")
def get_project_info() -> str:
    """Get information about the current Qualcoder project.

    Returns project metadata including version, date, coder name, and memo.
    """
    info = get_db().get_project_info()
    return _ai_json(info, indent=2)


@mcp.resource("qualcoder://codes/list")
def list_all_codes() -> str:
    """Get a list of all codes in the project.

    Returns all codes with their names, categories, colors, memos, and metadata.
    Codes are organized hierarchically by category.
    """
    codes = get_db().list_codes()
    return _ai_json(codes, indent=2)


@mcp.resource("qualcoder://categories/list")
def list_all_categories() -> str:
    """Get a list of all code categories.

    Returns all categories with their hierarchical structure (parent-child relationships).
    """
    categories = get_db().list_categories()
    return _ai_json(categories, indent=2)


@mcp.resource("qualcoder://codes/{code_id}")
def get_code_info(code_id: int) -> str:
    """Get detailed information about a specific code.

    Args:
        code_id: The numeric ID of the code (cid)

    Returns detailed code information including statistics on how many
    text, image, and audio/video segments are coded with this code.
    """
    code = get_db().get_code_details(code_id)
    if code is None:
        return json.dumps({"error": f"Code with id {code_id} not found"})
    return _ai_json(code, indent=2)


@mcp.resource("qualcoder://files/list")
def list_all_files() -> str:
    """Get a list of all source files in the project.

    Returns all files (text documents, images, audio, video) with their
    metadata, type, and memo information.
    """
    files = get_db().list_files()
    return _ai_json(files, indent=2)


@mcp.resource("qualcoder://files/{file_id}")
def get_file_content(file_id: int) -> str:
    """Get the content of a specific text file.

    Args:
        file_id: The numeric ID of the file

    Returns the full text content of the file along with metadata.
    For non-text files (media), returns metadata only.
    """
    file_data = get_db().get_file_content(file_id)
    if file_data is None:
        return json.dumps({"error": f"File with id {file_id} not found"})
    return _ai_json(file_data, indent=2)


@mcp.resource("qualcoder://cases/list")
def list_all_cases() -> str:
    """Get a list of all cases in the project.

    Returns all cases (participants, subjects) with their metadata and
    count of associated text segments.
    """
    cases = get_db().list_cases()
    return _ai_json(cases, indent=2)


@mcp.resource("qualcoder://cases/{case_id}")
def get_case_info(case_id: int) -> str:
    """Get detailed information about a specific case.

    Args:
        case_id: The numeric ID of the case

    Returns case details including all associated text segments with excerpts.
    """
    case = get_db().get_case_details(case_id)
    if case is None:
        return json.dumps({"error": f"Case with id {case_id} not found"})
    return _ai_json(case, indent=2)


@mcp.resource("qualcoder://journal")
def get_journal_entries() -> str:
    """Get all journal entries from the project.

    Returns all journal entries ordered by date (most recent first).
    """
    entries = get_db().get_journal_entries()
    # Memo privacy ('#####'): journal text follows the memo convention;
    # the entry body is exposed under 'content' (which elsewhere names
    # file fulltext), so it is stripped here rather than by _ai_json
    for entry in entries:
        if isinstance(entry.get("content"), str):
            entry["content"] = extract_ai_memo(entry["content"])
    return _ai_json(entries, indent=2)


# ============================================================================
# TOOLS - Operations and queries
# ============================================================================

@mcp.tool()
@_tool_guard
def list_available_projects(search_directories: Optional[List[str]] = None) -> str:
    """Discover Qualcoder projects on your system.

    This tool searches common locations for .qda files and returns a list
    of available Qualcoder projects. By default, it searches:
    - ~/Documents/QualCoder_projects
    - ~/Documents/QualCoder
    - ~/QualCoder
    - ~/Documents

    Args:
        search_directories: Optional list of additional directories to search

    Returns:
        JSON array of discovered projects with name, path, size, and last modified date
    """
    try:
        projects = discover_projects(search_directories)

        if not projects:
            return json.dumps({
                "projects": [],
                "message": "No Qualcoder projects found. Make sure you have created "
                          "at least one project in Qualcoder, or specify search_directories.",
                "default_search_paths": [
                    "~/Documents/QualCoder_projects",
                    "~/Documents/QualCoder",
                    "~/QualCoder",
                    "~/Documents"
                ]
            }, indent=2)

        return json.dumps({
            "project_count": len(projects),
            "projects": projects,
            "current_project": current_project_path
        }, indent=2)

    except Exception as e:
        logger.error(f"Error discovering projects: {e}")
        return json.dumps({"error": f"Failed to discover projects: {str(e)}"})


def _project_open_failure_result(project_path: str) -> Dict[str, Any]:
    """Error payload for a well-formed project whose database would not open.

    Used by select_project when validate_qda_path raised
    DatabaseOpenError (SQLite refused data.qda at validation time) or a
    sqlite3.Error surfaced mid-read. The damaged-database advice is
    ALWAYS present. When PROJECT-scoped heuristics suggest a QualCoder
    4.0 window has this project open (a mid-write 4.0 window leaves a
    hot journal that makes even the read-only open fail exactly like
    corruption would), that likelier cause is named first and the
    advice is appended, never replaced. The machine-wide process scan
    is deliberately left out of this decision: a QualCoder window open
    on some OTHER project says nothing about this one (QA round 1,
    F3/F22).
    """
    result = {
        "success": False,
        "error": "The project database appears to be damaged or unreadable. "
                 "Try opening it in QualCoder, or restore a backup."
    }
    try:
        folder = Path(project_path)
        if folder.name == "data.qda":
            folder = folder.parent
        signals = qualcoder_gui_signals(folder, include_process_scan=False)
        result["qualcoder_gui_signals"] = signals
        if signals:
            result["error"] = (
                "The project database could not be opened, and it "
                "APPEARS to be open in QualCoder right now ("
                + "; ".join(signals) + "). Ask the user to close the "
                "project in QualCoder, then retry; only if that does "
                "not help, consider a damaged database or a backup "
                "restore."
            )
    except Exception:
        pass
    return result


@mcp.tool()
@_tool_guard
def select_project(project_path: str) -> str:
    """Switch to a different Qualcoder project.

    Use this tool to change which project you're working with. You can get
    a list of available projects using 'list_available_projects' first.

    The result may include a `warning` — e.g. that QualCoder currently has
    this project open. If so, RELAY it to the user: ask them to close the
    project in QualCoder before any coding they intend to save, because
    all write operations will be refused until it is closed
    (re-check with get_current_project).

    Args:
        project_path: Full path to the .qda file you want to open

    Returns:
        JSON with success status, project information, and possibly a
        `warning` to pass on to the user
    """
    try:
        switch_project(project_path)

        # Get basic info about the newly opened project
        project_info = get_db().get_project_info()

        result = {
            "success": True,
            "message": f"Switched to project: {Path(project_path).stem}",
            "project_path": project_path,
            "project_name": Path(project_path).stem,
            "project_info": project_info
        }

        # P1-6: remember the selection for the MRU recovery hint (the
        # canonical data.qda path, which select_project accepts back)
        _remember_mru_project(str(validate_qda_path(project_path)))

        warnings = []

        # Reads are safe while QualCoder is open, but warn: data may change
        # underneath, and writes will be refused until QualCoder closes it
        state, holder = qualcoder_lock_state(_current_project_folder())
        if state == "active":
            warnings.append(
                f"QualCoder currently has this project open (user "
                f"{holder or 'unknown'}). Ask the user to close the project "
                f"in QualCoder before any coding they intend to save — all "
                f"write operations will be refused until it is closed. Reads "
                f"work but may return changing data."
            )
        else:
            # C5/T17 + P1-5: only released QualCoder (3.x) signals "open"
            # via the lock file; 4.0 removed the protocol, so detection
            # falls back to best-effort heuristics (WARN rung only; the
            # C7 in-transaction fingerprints stay the write-time backstop)
            signals = qualcoder_gui_signals(_current_project_folder())
            result["qualcoder_gui_signals"] = signals
            if signals:
                warnings.append(
                    "This project APPEARS to be open in QualCoder: "
                    + "; ".join(signals) + ". This is a heuristic (4.0 "
                    "writes no lock file), so confirm with the user before "
                    "any write. Also note: an open QualCoder 4.0 window "
                    "will not display external changes until the project "
                    "is reopened there."
                )
            else:
                warnings.append(
                    "Lock-gate limitation: QualCoder 4.0 builds write no "
                    "lock file, so 4.0 detection is best-effort (no "
                    "open-GUI signals right now; an idle 4.0 window with "
                    "no recent AI activity leaves no file trace, so only "
                    "the process scan could see it). Confirm with the "
                    "user that no QualCoder window has this project open "
                    "before writing."
                )

        # QualCoder's own open check requires "QualCoder" in project.about
        # and refuses otherwise ("This is not a QualCoder database") —
        # warn so the user knows QualCoder itself will not open this
        # project (COMPAT V3)
        if not getattr(get_db(), "qualcoder_about_ok", True):
            warnings.append(
                "This database does not identify itself as a QualCoder "
                "project (project.about does not contain 'QualCoder'). "
                "QualCoder itself would refuse to open it with 'This is "
                "not a QualCoder database'."
            )

        if warnings:
            result["warning"] = " | ".join(warnings)

        return _ai_json(result, indent=2)

    except DatabaseLockedError as e:
        logger.error(f"Project locked during select: {e}")
        return json.dumps({
            "success": False,
            "error": str(e)
        })
    except UnsupportedSchemaError as e:
        logger.error(f"Unsupported schema during select: {e}")
        return json.dumps({
            "success": False,
            "error": str(e)
        })
    except (ValueError, FileNotFoundError) as e:
        # Log full error for debugging, but don't expose internal paths to user
        logger.error(f"Failed to select project: {e}")
        if isinstance(e, DatabaseOpenError):
            # The path IS a well-formed project, but SQLite refused its
            # data.qda at validation time: a hot journal left by a 4.0
            # window mid-write, or genuine corruption. Project-scoped
            # heuristics choose the wording; the damaged-database
            # advice is always kept (P1-5; QA round 1, F3/F22).
            return json.dumps(_project_open_failure_result(project_path))
        # A wrong or malformed path: no heuristic can explain it, so the
        # deterministic recovery hint stays exactly as it was
        return json.dumps({
            "success": False,
            "error": "Invalid project path or project not found. "
                     "Use 'list_available_projects' to find valid projects."
        })
    except sqlite3.Error as e:
        # e.g. "database disk image is malformed" surfacing mid-read (F3)
        logger.error(f"SQLite error while opening project: {e}")
        return json.dumps(_project_open_failure_result(project_path))
    except RuntimeError as e:
        logger.error(f"Failed to open project database: {e}")
        return json.dumps({
            "success": False,
            "error": "Failed to open project database"
        })


@mcp.tool()
@_tool_guard
def get_current_project() -> str:
    """Get information about the currently open project.

    Also reports whether QualCoder currently has this project open
    (`qualcoder_open`, from its project_in_use.lock heartbeat). Use this
    to re-check after asking the user to close QualCoder: proceed with
    coding workflows only when `qualcoder_open` is false — all database
    writes are refused while it is true.

    Returns:
        JSON with current project path, basic metadata, and the
        QualCoder-open state (qualcoder_open boolean; qualcoder_lock
        detail when a lock file is present)
    """
    try:
        if current_project_path is None:
            return json.dumps({
                "current_project": None,
                "message": "No project currently open. Use 'list_available_projects' "
                          "and 'select_project' to open one." + _mru_hint()
            }, indent=2)

        project_info = get_db().get_project_info()

        result = {
            "current_project": current_project_path,
            "project_name": Path(current_project_path).stem,
            "project_info": project_info,
            "schema": _schema_block(),
        }

        # QualCoder-open state, cheap to re-check after the user says
        # they have closed it (heartbeat refreshes every 5 s, stale > 30 s)
        state, holder = qualcoder_lock_state(_current_project_folder())
        result["qualcoder_open"] = (state == "active")
        if state == "active":
            result["qualcoder_lock"] = {
                "state": "active",
                "holder": holder or "unknown",
                "note": "QualCoder has this project open — all database "
                        "writes will be refused until it is closed there. "
                        "Ask the user to close it, then re-check."
            }
        elif state == "stale":
            result["qualcoder_lock"] = {
                "state": "stale",
                "holder": holder or "unknown",
                "note": "A leftover lock file from a QualCoder session that "
                        "did not close cleanly — writes proceed normally."
            }

        # P1-5: best-effort 4.0 GUI-open heuristics (4.0 writes no lock
        # file). WARN-level only; writes are still gated by the lock file
        # and the C7 in-transaction text fingerprints.
        signals = qualcoder_gui_signals(_current_project_folder())
        result["qualcoder_gui_signals"] = signals
        if signals and state != "active":
            result["qualcoder_gui_hint"] = (
                "This project APPEARS to be open in QualCoder ("
                + "; ".join(signals) + "). This is a heuristic: confirm "
                "with the user before writing. An open QualCoder 4.0 "
                "window will not display external changes until the "
                "project is reopened there."
            )

        return _ai_json(result, indent=2)

    except (DatabaseOpenError, sqlite3.Error):
        # Let the tool guard return its fixed, path-free text instead of
        # forwarding the sqlite message (S-H4)
        raise
    except Exception as e:
        return json.dumps({"error": f"Failed to get project info: {str(e)}"})


@mcp.tool()
@_tool_guard
def copy_project_to_workspace(
    source_path: str,
    new_name: Optional[str] = None
) -> str:
    """Copy a QualCoder project to the MCP workspace for safe modification.

    This is the recommended first step before any AI coding: work on a copy
    in the workspace folder (~/Documents/Qualcoder MCP Projects/) so your
    original project is never touched. If a project with the same name
    already exists in the workspace, the copy gets a timestamped name.

    The copy carries the whole project tree, ai_data/ included (the AI
    prompt library and chat history are user data), but omits the
    regenerable ai_data/search.sqlite, sqlite sidecar files and lock
    files, the same exclusions backups use. QualCoder rebuilds
    search.sqlite when it opens the copy. Symlinks inside the project
    that point outside the project folder (or dangle) are not followed:
    they are skipped and reported (skipped_symlinks, with names), so a
    shared or untrusted project folder cannot pull outside files into
    the copy; symlinks resolving inside the project are copied as before.

    The copy is NOT opened automatically — use select_project on the
    returned path when you are ready to work on it.

    Args:
        source_path: Path to the source .qda project (folder or data.qda)
        new_name: Optional new name for the workspace copy

    Returns:
        JSON with the workspace copy's path and the count of skipped
        symlinks

    Example:
        "Copy my project 'Interview Study' to the workspace for AI coding"
    """
    from .database import copy_project_to_workspace as copy_to_workspace

    # Validate that the source is a real QualCoder project before copying
    validate_qda_path(source_path)

    report: Dict[str, Any] = {}
    dest = copy_to_workspace(source_path, new_name=new_name, report=report)

    result = {
        "success": True,
        "message": f"Copied project to workspace: {dest.name}",
        "workspace_copy": str(dest),
        "original_untouched": True,
        "hint": f"Use select_project(\"{dest}\") to open the copy and work on it."
    }
    _attach_skipped_symlinks(result, report, always=True)
    return json.dumps(result, indent=2)


@mcp.tool()
@_tool_guard
def search_coded_text(query: str, code_name: Optional[str] = None,
                      limit: int = 50, coder: Optional[str] = None) -> str:
    """Search for text segments that contain specific keywords.

    This tool searches through all coded text segments for matching content.
    Useful for finding specific themes, quotes, or concepts in your data.

    Coder visibility (QualCoder 4.0 projects): when the project hides
    some coders' work, results reflect only visible coders by default
    (what the user sees in QualCoder); the result then carries a
    coder_visibility block. Pass coder to read one specific coder's
    segments from the full data instead.

    Args:
        query: The text to search for (case-insensitive substring match)
        code_name: Optional - filter results to only segments coded with this code
        limit: Maximum number of results to return (default 50)
        coder: Optional coder name; reads that coder's rows from the
               base tables, bypassing the visibility filter

    Returns:
        JSON array of matching segments with their codes, files, and context
    """
    results = get_db().search_coded_text(query, code_name, limit,
                                         coder=coder)
    payload = {
        "query": query,
        "code_filter": code_name,
        "result_count": len(results),
        "results": results
    }
    note = _coder_visibility_note(coder)
    if note:
        payload["coder_visibility"] = note
    return _ai_json(payload, indent=2)


@mcp.tool()
@_tool_guard
def get_coded_segments(code_id: int, limit: int = 100,
                       coder: Optional[str] = None) -> str:
    """Get all text segments that have been coded with a specific code.

    This tool retrieves all the text excerpts that have been assigned
    to a particular code, useful for reviewing themes or categories.

    Coder visibility (QualCoder 4.0 projects): when the project hides
    some coders' work, results reflect only visible coders by default
    (what the user sees in QualCoder); the result then carries a
    coder_visibility block with the suppressed count. Pass coder to
    read one specific coder's segments from the full data instead.

    Args:
        code_id: The numeric ID of the code (cid)
        limit: Maximum number of segments to return (default 100)
        coder: Optional coder name; reads that coder's rows from the
               base tables, bypassing the visibility filter

    Returns:
        JSON array of all text segments coded with this code, including
        the text content, file names, memos, and position information
    """
    db = get_db()
    segments = db.get_coded_text_segments(code_id, limit, coder=coder)
    payload = {
        "code_id": code_id,
        "segment_count": len(segments),
        "segments": segments
    }
    note = _coder_visibility_note(coder)
    if note:
        if coder is None:
            # Count suppressed rows for THIS code (disclosure may count,
            # never name, hidden coders)
            suppressed = (db.count_codings_for_code(code_id,
                                                    honor_visibility=False)
                          - db.count_codings_for_code(code_id))
            note["codings_suppressed"] = max(0, suppressed)
        payload["coder_visibility"] = note
    return _ai_json(payload, indent=2)


@mcp.tool()
@_tool_guard
def search_files(
    pattern: str,
    search_filename: bool = True,
    search_content: bool = False,
    search_memo: bool = False,
    case_sensitive: bool = False,
    limit: int = 50
) -> str:
    """Search for files by name, content, or memo.

    This tool helps you find specific files in the project without searching
    the entire filesystem. Perfect for locating interview transcripts by
    participant name, finding files with specific content, or searching memos.

    PERFORMANCE GUIDE:
    - Filename search: Fast (milliseconds) - searches file names only
    - Content search: Slower (can take seconds for 100+ files) - searches full text
    - Memo search: Fast (milliseconds) - searches file memos

    IMPORTANT - CLARIFICATION WORKFLOW:
    When a user's request is ambiguous (e.g., "search for files containing paul"):

    1. ASK THE USER for clarification:
       "I can search for 'paul' in:
        - File names only (fast)
        - File content (slower, searches full transcript text)
        - File memos
        - All of the above

        Which would you prefer?"

    2. Wait for the user to clarify their preference

    3. Then call this tool with the appropriate search flags

    This ensures you search only what the user intends and provides the best
    performance for their needs.

    Args:
        pattern: Text to search for (case-insensitive by default)
        search_filename: Search in file names (default: True, fast)
        search_content: Search in file content/fulltext (default: False, slower)
        search_memo: Search in file memos (default: False, fast)
        case_sensitive: Use case-sensitive matching (default: False)
        limit: Maximum number of files to return (default: 50)

    Returns:
        JSON object with:
        - search_parameters: Dictionary showing what was searched
        - performance_info: Performance details and warnings
        - total_files_searched: Number of files examined
        - total_matches: Number of files with matches
        - results: Array of matching files with:
            - file_id: ID for use with other tools
            - file_name: Name of the file
            - file_type: Type (text, audio, video, image, pdf)
            - matched_in: {filename: bool, content: bool, memo: bool}
            - match_count: Total number of matches in this file
            - matches: Array of match details with location and preview

    Examples:
        User says: "Find files with 'paul' in the name"
        → search_files("paul", search_filename=True)

        User says: "Search all file content for 'workplace stress'"
        → search_files("workplace stress", search_content=True)

        User says: "Search everywhere for 'motivation'"
        → search_files("motivation", search_filename=True,
                      search_content=True, search_memo=True)

        User says: "Search for files containing paul" (AMBIGUOUS!)
        → Ask user to clarify: filename, content, or both?
        → Then call tool based on their answer

    Tips:
    - For finding a specific interview by participant name, use search_filename
    - For finding specific quotes or themes, use search_content
    - For searching your annotations, use search_memo
    - You can combine multiple search locations
    - Once you have file_id, use analyze_file_with_coding() to get full content
    """
    try:
        result = get_db().search_files(
            pattern=pattern,
            search_filename=search_filename,
            search_content=search_content,
            search_memo=search_memo,
            case_sensitive=case_sensitive,
            limit=limit
        )

        return json.dumps(result, indent=2)

    except Exception as e:
        logger.error(f"Error in search_files: {e}")
        return json.dumps({
            "error": f"Failed to search files: {str(e)}",
            "search_parameters": {
                "pattern": pattern,
                "searched_filename": search_filename,
                "searched_content": search_content,
                "searched_memo": search_memo
            }
        }, indent=2)


@mcp.tool()
@_tool_guard
def get_coding_frequencies(coder: Optional[str] = None) -> str:
    """Get frequency statistics for all codes in the project.

    This tool provides an overview of how often each code has been used,
    helping identify prominent themes and patterns in the data.

    Coder visibility (QualCoder 4.0 projects): when the project hides
    some coders' work, counts reflect only visible coders by default
    (what the user sees in QualCoder); the result then carries a
    coder_visibility block. Pass coder to count one specific coder's
    rows from the full data instead.

    Args:
        coder: Optional coder name; counts that coder's rows from the
               base tables, bypassing the visibility filter

    Returns:
        JSON object with:
        - total_coded_segments: Total count across all codes
        - codes: Array of codes with their frequencies, sorted by frequency
    """
    frequencies = get_db().get_coding_frequencies(coder=coder)
    note = _coder_visibility_note(coder)
    if note:
        frequencies["coder_visibility"] = note
    return json.dumps(frequencies, indent=2)


@mcp.tool()
@_tool_guard
def search_memos(query: str, limit: int = 50) -> str:
    """Search through all memos and annotations in the project.

    This tool searches through code memos, file memos, and annotations
    to find notes and reflections containing specific keywords. To WRITE a
    memo, use set_memo(target_type, target_id, memo); to add a research
    journal entry, use add_journal_entry(name, entry).

    Memo privacy (QualCoder 4.0 convention): memo text from the first
    '#####' marker onward is private to the researcher. The search
    matches and returns only the public part of each memo.

    Coder visibility (QualCoder 4.0 projects): annotation matches
    honor the project's per-coder visibility by default (hidden
    coders' annotations are not returned, matching what the user sees
    in QualCoder), and the result then carries a coder_visibility
    block. Code and file memos have no per-coder visibility in
    QualCoder and are always searched. This tool has no coder
    override.

    Args:
        query: The text to search for in memos
        limit: Maximum number of results to return (default 50)

    Returns:
        JSON array of matching memos with their type, content, and context
    """
    results = get_db().search_memos(query, limit)
    payload = {
        "query": query,
        "result_count": len(results),
        "results": results
    }
    note = _coder_visibility_note()
    if note:
        payload["coder_visibility"] = note
    return json.dumps(payload, indent=2)


@mcp.tool()
@_tool_guard
def export_code_report(code_name: str) -> str:
    """Generate a comprehensive report for a specific code.

    This tool creates a detailed report including code metadata,
    all coded segments, and frequency information.

    Memo privacy (QualCoder 4.0 convention): this report is returned
    into the conversation, not written to a file, so every memo it
    contains is the public part only (text before the first '#####'
    marker). Unlike the file exports (export_refi_qda, export_codebook,
    export_coded_segments_report) it never carries the private zone.

    Coder visibility (QualCoder 4.0 projects): segments reflect visible
    coders by default and the result then carries a coder_visibility
    block. There is no coder override on this tool; use
    get_coded_segments(coder=...) for that.

    Args:
        code_name: The name of the code to generate a report for

    Returns:
        JSON object with complete code information and all coded segments
    """
    # Find the code by name
    codes = get_db().list_codes()
    matching_code = None
    for code in codes:
        if code["name"].lower() == code_name.lower():
            matching_code = code
            break

    if not matching_code:
        return json.dumps({
            "error": f"Code '{code_name}' not found",
            "available_codes": [c["name"] for c in codes]
        })

    # Get detailed information
    code_id = matching_code["id"]
    details = get_db().get_code_details(code_id)
    segments = get_db().get_coded_text_segments(code_id, limit=1000)

    payload = {
        "code": details,
        "segments": segments,
        "report_generated": True
    }
    note = _coder_visibility_note()
    if note:
        payload["coder_visibility"] = note
    return _ai_json(payload, indent=2)


@mcp.tool()
@_tool_guard
def export_refi_qda(
    output_path: str,
    coding_session_id: Optional[str] = None,
    overwrite: bool = False
) -> str:
    """Export codings as a REFI-QDA .qdpx file for other QDA software.

    REFI-QDA is the interchange standard supported by QualCoder, NVivo,
    ATLAS.ti, MAXQDA and others. The export contains the referenced codes,
    the text sources, and the coded selections (with coding memos as
    descriptions).

    Full memos on export (owner-ruled parity with QualCoder's own
    exports): the exported FILE keeps memo text in full, including
    any private '#####' section that read tools never show the AI.
    Mention this to the user if they plan to share the exported
    file.

    Two modes:
    - Default (no coding_session_id): exports ALL text codings of the currently
      open project.
    - With coding_session_id: exports that AI coding session's suggestions
      (all statuses) — useful for reviewing suggestions in another tool
      before applying them.

    Position convention (QualCoder's): selections are character offsets
    into the plain text exactly as exported (verbatim, UTF-8, no BOM,
    newlines as single \\n), 0-based, end-exclusive. Tools that count \\r\\n
    as two characters (e.g. NVivo) may show shifted boundaries.

    Known limitations (documented): cases, annotations and journals are not
    included (code categories ARE preserved as nested codes); all
    selections are attributed to a single export user rather than the
    original coders.

    Args:
        output_path: Where to write the .qdpx file (must end in .qdpx; the
                     directory must already exist)
        coding_session_id: Optional AI coding session to export instead of the
                    project's codings
        overwrite: Allow replacing an existing file (default: False)

    Returns:
        JSON with the output path and export counts

    Example:
        "Export my codings as REFI-QDA to ~/Desktop/study.qdpx"
    """
    # Bridge fix: some MCP middleware strips arguments named
    # 'session_id' (reserved for its own routing); the tool
    # argument is coding_session_id, aliased for the body.
    session_id = coding_session_id
    from .refi_export import RefiQdaExporter

    ro_db = get_db()

    # --- output path validation (consistent with the security posture) ---
    try:
        out_file = Path(output_path).expanduser().resolve()
    except (OSError, RuntimeError):
        return json.dumps({"error": "Invalid output path"})
    if out_file.suffix.lower() != ".qdpx":
        return json.dumps({"error": "output_path must end in .qdpx"})
    if not out_file.parent.is_dir():
        return json.dumps({
            "error": "The output directory does not exist — create it first "
                     "or choose an existing folder (e.g. ~/Documents)"
        })
    if out_file.exists() and not overwrite:
        return json.dumps({
            "error": f"'{out_file.name}' already exists. Pass overwrite=true "
                     f"to replace it."
        })
    project_folder = validate_qda_path(current_project_path).parent
    if project_folder in out_file.parents or out_file.parent == project_folder:
        return json.dumps({
            "error": "Refusing to write the export inside the project folder — "
                     "choose a location outside it."
        })

    # --- collect what to export ---
    skipped_non_text = 0
    if session_id is not None:
        if not session_manager.session_exists(session_id):
            return json.dumps({
                "error": f"Session {session_id} not found",
                "available_sessions": session_manager.list_sessions()
            })
        session = session_manager.load_session(session_id)
        mismatch = _check_session_project(session)
        if mismatch is not None:
            return json.dumps(mismatch, indent=2)
        suggestions = list(session.suggestions)
        project_name = f"AI Coding Suggestions ({Path(current_project_path).stem})"
        if not suggestions:
            return json.dumps({"error": "The session has no suggestions to export"})
    else:
        # Whole-project export: every text coding, built via the same
        # CodingSuggestion structures the exporter understands.
        #
        # Skip-and-disclose (QA2-5): real legacy projects legitimately
        # contain rows this server would never write — GUI-created codings
        # on emoji/CRLF files whose positions overrun the text in
        # code-point space, or damaged rows with NULL positions. Strict
        # all-or-nothing is right for explicit session exports, but "export
        # my project" must export everything valid and report the rest.
        suggestions = []
        skipped_invalid = []
        truncated_codes = []
        file_cache: Dict[int, Optional[Dict[str, Any]]] = {}
        code_frequencies: Optional[Dict[int, int]] = None
        for code in ro_db.list_codes():
            # P1-3: interchange exports read BASE tables (QualCoder's own
            # refi.py does not filter by coder visibility)
            segments = ro_db.get_coded_text_segments(
                code["id"], limit=5000, honor_visibility=False)
            if len(segments) == 5000:
                # The read is capped at 5000 per code — disclose when the
                # project actually holds more (QA2-3)
                if code_frequencies is None:
                    code_frequencies = {
                        c["code_id"]: c["frequency"]
                        for c in ro_db.get_coding_frequencies(
                            honor_visibility=False)["codes"]
                    }
                total = code_frequencies.get(code["id"], len(segments))
                if total > 5000:
                    truncated_codes.append({
                        "code_name": code["name"],
                        "exported": 5000,
                        "total_codings": total,
                    })
            for seg in segments:
                fid = seg["file_id"]
                if fid not in file_cache:
                    file_cache[fid] = ro_db.get_file_content(fid)
                fc = file_cache[fid]
                fulltext = (fc or {}).get("content") or ""
                if fc is None or not fulltext:
                    skipped_non_text += 1
                    continue
                pos0, pos1 = seg["position_start"], seg["position_end"]
                if (not isinstance(pos0, int) or isinstance(pos0, bool)
                        or not isinstance(pos1, int) or isinstance(pos1, bool)):
                    skipped_invalid.append({
                        "coding_id": seg["id"],
                        "code_name": code["name"],
                        "file_name": seg["file_name"],
                        "reason": "missing positions — the coding row may be damaged",
                    })
                    continue
                if pos0 < 0 or pos1 <= pos0 or pos1 > len(fulltext):
                    skipped_invalid.append({
                        "coding_id": seg["id"],
                        "code_name": code["name"],
                        "file_name": seg["file_name"],
                        "reason": f"positions {pos0}-{pos1} invalid for the file "
                                  f"text (length {len(fulltext)}) — likely a "
                                  f"GUI-created coding on a position-unsafe "
                                  f"(emoji/CRLF) file",
                    })
                    continue
                suggestions.append(CodingSuggestion(
                    file_id=fid,
                    file_name=seg["file_name"],
                    code_id=code["id"],
                    code_name=code["name"],
                    start_pos=pos0,
                    end_pos=pos1,
                    segment_text=seg["text"] or "",
                    reasoning=seg["memo"] or "",
                    confidence=0.0,  # human codings carry no AI confidence
                ))
        project_name = Path(current_project_path).stem
        if not suggestions:
            result = {"error": "The project has no text codings to export"}
            if skipped_invalid:
                result["error"] = (
                    "The project has no text codings to export — all its "
                    "codings were skipped as invalid (see skipped_details)"
                )
                result["skipped_invalid_codings"] = len(skipped_invalid)
                result["skipped_details"] = skipped_invalid[:20]
            return json.dumps(result, indent=2)

    exporter = RefiQdaExporter(ro_db, ai_user_name=_default_owner())
    result_path = exporter.export_to_qdpx(
        suggestions, str(out_file), project_name=project_name
    )

    output = {
        "success": True,
        "output_path": result_path,
        "codings_exported": len(suggestions),
        "codes_exported": len({s.code_id for s in suggestions}),
        "files_exported": len({s.file_id for s in suggestions}),
        "note": "Export includes codes, text sources and coded selections. "
                "Categories, cases, annotations and journals are not included."
    }
    if skipped_non_text:
        output["skipped_codings_on_non_text_sources"] = skipped_non_text
    if session_id is None:
        media = ro_db.count_media_codings()
        if media["av"] or media["image"]:
            output["av_image_codings_not_exported"] = media
            output["note"] += (
                f" This project also has {media['av']} audio/video and "
                f"{media['image']} image codings — REFI export covers text "
                f"codings only, so those are NOT included."
            )
        if skipped_invalid:
            output["skipped_invalid_codings"] = len(skipped_invalid)
            output["skipped_details"] = skipped_invalid[:20]
            output["skip_note"] = (
                "Codings with invalid or missing positions were not exported "
                "(their positions cannot be represented against the exported "
                "text). They remain untouched in the project."
            )
        if truncated_codes:
            output["truncated_codes"] = truncated_codes
            output["warning"] = (
                f"Export truncated for {len(truncated_codes)} code(s): only "
                f"the first 5000 codings per code are exported."
            )
    return json.dumps(output, indent=2)


@mcp.tool()
@_tool_guard
def get_project_summary() -> str:
    """Get a comprehensive summary of the entire project.

    This tool provides an overview of the project including counts
    of files, codes, categories, cases, and coding statistics.

    Returns:
        JSON object with project-wide statistics and metadata
    """
    project_info = get_db().get_project_info()
    files = get_db().list_files()
    codes = get_db().list_codes()
    categories = get_db().list_categories()
    cases = get_db().list_cases()
    frequencies = get_db().get_coding_frequencies()

    summary = {
        "project_info": project_info,
        "schema": _schema_block(),
        "statistics": {
            "total_files": len(files),
            "total_codes": len(codes),
            "total_categories": len(categories),
            "total_cases": len(cases),
            "total_coded_segments": frequencies["total_coded_segments"]
        },
        "file_types": {},
        "top_codes": frequencies["codes"][:10]  # Top 10 most used codes
    }

    # Count file types
    for file in files:
        file_type = file["type"]
        summary["file_types"][file_type] = summary["file_types"].get(file_type, 0) + 1

    note = _coder_visibility_note()
    if note:
        summary["coder_visibility"] = note

    return _ai_json(summary, indent=2)


@mcp.tool()
@_tool_guard
def analyze_file_with_coding(file_id: int) -> str:
    """Analyze a text file with all its coded segments for rich context analysis.

    This tool retrieves the complete text of a file along with all coding information,
    enabling deep analysis that considers both coded segments and the full context.
    Perfect for analyzing interview transcripts, documents, or any text where you need
    to see both the structured coding and the complete narrative.

    Use this when you want to:
    - Answer questions that require understanding the full context
    - Find passages that may not be directly coded but are relevant
    - Analyze how a participant discusses multiple themes
    - Understand the relationship between coded and uncoded text

    Args:
        file_id: The numeric ID of the file to analyze

    Returns:
        JSON object with:
        - file_info: File metadata (name, type, date)
        - full_text: Complete text of the file
        - coded_segments: All coded segments with positions, codes, and memos
        - codes_used: Summary of which codes appear in this file
        - annotations: Any annotations on the file
        - statistics: Coding coverage and density metrics

    Example use case:
        "What does Paul say that has relevance to the Wisdom of the Crowds argument?"
        This requires seeing both coded segments AND the full transcript context.

    If the result contains `position_safety_warning`, you MUST relay it to
    the user before coding or applying anything on this file — codings on
    such files can render shifted or unhighlighted in QualCoder's editor.
    """
    result = get_db().get_file_with_coding(file_id)
    if result is None:
        return json.dumps({
            "error": f"File with id {file_id} not found"
        })

    # Non-text sources: say so explicitly — an empty full_text was
    # previously indistinguishable from a genuinely empty text file (track6)
    if not result.get("file_info", {}).get("is_text", True):
        result["note"] = (
            f"This source is {result['file_info'].get('type', 'media')}, not "
            f"text — it has no codable text content, and its image/audio-video "
            f"codings (if any) are not shown by this tool."
        )

    note = _coder_visibility_note()
    if note:
        result["coder_visibility"] = note

    # Read-side position-safety notice (QA2-4): researchers should learn
    # that a file is position-unsafe when EXPLORING it, not only when
    # coding it. On unsafe files QualCoder's GUI uses a divergent position
    # system, so GUI-created codings there may not match code-point slices
    # and MCP codings may render shifted in the GUI editor.
    full_text = result.get("full_text") or ""
    if full_text and not db_position_safe(full_text):
        result["position_safety_warning"] = (
            "This file contains \r\n sequences or characters beyond U+FFFF "
            "(e.g. emoji), so QualCoder's GUI uses a different position "
            "system for it (its documented emoji bug). GUI-created codings "
            "here may not align with the text slices shown by this server, "
            "and codings written here may render shifted or unhighlighted "
            "in the QualCoder editor. Reports and exports are unaffected."
        )
    return _ai_json(result, indent=2)


@mcp.tool()
@_tool_guard
def list_attribute_types() -> str:
    """List all attribute types defined in the project.

    Attributes are used to store demographics, metadata, or other characteristics
    about files or cases (e.g., age, gender, location, interview_type).

    Returns:
        JSON array of attribute types with:
        - name: Attribute name
        - value_type: Data type (character, numeric)
        - applies_to: Whether it's for 'case' or 'file'
        - memo: Description of the attribute
    """
    result = get_db().list_attribute_types()
    return _ai_json({
        "attribute_count": len(result),
        "attributes": result
    }, indent=2)


@mcp.tool()
@_tool_guard
def get_file_attributes(file_id: int) -> str:
    """Get all attribute values for a specific file.

    Retrieves demographics or metadata assigned to a file
    (e.g., document_type, source, date_collected).

    Args:
        file_id: The numeric ID of the file

    Returns:
        JSON array of attributes with their values for this file
    """
    result = get_db().get_file_attributes(file_id)
    return _ai_json({
        "file_id": file_id,
        "attribute_count": len(result),
        "attributes": result
    }, indent=2)


@mcp.tool()
@_tool_guard
def get_case_attributes(case_id: int) -> str:
    """Get all attribute values for a specific case.

    Retrieves demographics or metadata for a case/participant
    (e.g., age, gender, education_level).

    Args:
        case_id: The numeric ID of the case

    Returns:
        JSON array of attributes with their values for this case
    """
    result = get_db().get_case_attributes(case_id)
    return _ai_json({
        "case_id": case_id,
        "attribute_count": len(result),
        "attributes": result
    }, indent=2)


@mcp.tool()
@_tool_guard
def query_by_attribute(
    attr_name: str,
    attr_value: str,
    attr_type: str = "case",
    operator: str = "equals"
) -> str:
    """Find cases or files by attribute value.

    Enables demographic or metadata-based queries like:
    - "Find all participants over age 50"
      -> query_by_attribute("Age", "50", operator="gt")
    - "Get files where interview_type is 'focus_group'"
      -> query_by_attribute("interview_type", "focus_group", "file")
    - "Find cases whose Sector mentions health"
      -> query_by_attribute("Sector", "health", operator="contains")

    Args:
        attr_name: Name of the attribute to query
        attr_value: Value to compare against (a number for gt/gte/lt/lte)
        attr_type: Either 'case' or 'file' (default: 'case')
        operator: 'equals' (exact match, default; numeric attributes
                  compare numerically so "5" finds a stored "5.0", and
                  "" finds cases/files whose attribute is unset),
                  'contains' (case-insensitive substring), or
                  'gt'/'gte'/'lt'/'lte' (numeric comparisons; unset
                  values never match)

    Returns:
        JSON array of matching cases/files, each with id, name, memo and
        the matched attribute value
    """
    result = get_db().query_by_attribute(attr_name, attr_value, attr_type, operator)
    return _ai_json(result, indent=2)


@mcp.tool()
@_tool_guard
def find_cooccurring_codes(code_id: int, window_size: int = 0,
                           coder: Optional[str] = None) -> str:
    """Find codes that appear together with a specific code.

    This tool identifies co-occurrence patterns - which codes tend to appear
    in the same segments or nearby in the text. Essential for discovering
    relationships between themes and concepts.

    NOT QualCoder's co-occurrence matrix: QualCoder's report classifies
    each unordered coding PAIR once (exact/inclusion/overlap) into an
    asymmetric code-by-code matrix, while this tool counts every
    overlapping pair occurrence per target code — the numbers will not
    match QualCoder's Code co-occurrence report. Say so if the user asks
    for a comparison.


    Coder visibility (QualCoder 4.0 projects): when the project hides
    some coders' work, counts reflect only visible coders by
    default (what the user sees in QualCoder). The result is then
    wrapped in an object carrying a coder_visibility block
    (otherwise it stays a plain array). Pass coder to analyze one
    specific coder's rows from the full data instead.

    Args:
        code_id: The numeric ID of the code to analyze
        window_size: How to define "co-occurrence":
                    - 0 (default): Codes that overlap the same text segment
                    - N > 0: Codes within N characters of each other
        coder: Optional coder name; analyzes that coder's rows from the
               base tables, bypassing the visibility filter

    Returns:
        JSON array of co-occurring codes, sorted by frequency; each entry
        has code_id, code_name, color, category, cooccurrence_count

    Example uses:
    - "What themes appear together with 'workplace stress'?"
    - "Find patterns of co-occurring codes"
    - "Which codes never appear with 'job satisfaction'?"
    """
    result = get_db().find_code_cooccurrences(code_id, window_size,
                                              coder=coder)
    payload: Dict[str, Any] = {"cooccurrences": result}
    note = _coder_visibility_note(coder)
    if note:
        payload["coder_visibility"] = note
        return json.dumps(payload, indent=2)
    return json.dumps(result, indent=2)


@mcp.tool()
@_tool_guard
def get_case_code_matrix(coder: Optional[str] = None) -> str:
    """Get a matrix showing which codes appear in which cases.

    This tool creates a cross-tabulation of all cases and codes, showing
    which codes have been applied to text segments from each case. Essential
    for comparative analysis across participants.

    Only codings fully CONTAINED in a case's text interval are counted,
    matching QualCoder's own report semantics.

    Coder visibility (QualCoder 4.0 projects): when the project hides
    some coders' work, counts reflect only visible coders by default
    (what the user sees in QualCoder); the result then carries a
    coder_visibility block. Pass coder to count one specific coder's
    rows from the full data. The CSV export tool is NOT filtered
    (QualCoder report-export parity).

    Returns:
        JSON object with:
        - cases: Array of {id, name}
        - codes: Array of {id, name}
        - matrix: Nested object keyed by case id then code id (keys are
          strings, since this is JSON), value = coding count; absent keys
          mean zero

    Example uses:
    - "Which cases mention 'job satisfaction'?"
    - "Create a comparison table of themes by participant"
    - "Find cases that never mention certain codes"
    """
    result = get_db().get_case_code_matrix(coder=coder)
    note = _coder_visibility_note(coder)
    if note:
        result["coder_visibility"] = note
    return json.dumps(result, indent=2)


@mcp.tool()
@_tool_guard
def get_codes_by_case(case_id: int, coder: Optional[str] = None) -> str:
    """Get all codes that appear in a specific case.

    Shows which themes/codes have been identified in a particular
    case's text segments, with frequency counts.


    Coder visibility (QualCoder 4.0 projects): when the project hides
    some coders' work, counts reflect only visible coders by
    default (what the user sees in QualCoder). The result is then
    wrapped in an object carrying a coder_visibility block
    (otherwise it stays a plain array). Pass coder to analyze one
    specific coder's rows from the full data instead.

    Args:
        case_id: The numeric ID of the case
        coder: Optional coder name; counts that coder's rows from the
               base tables, bypassing the visibility filter

    Only codings fully contained in the case's text intervals are counted
    (QualCoder report semantics).

    Returns:
        JSON array of codes used in this case; each entry has code_id,
        code_name, color, category, occurrence_count
    """
    result = get_db().get_codes_by_case(case_id, coder=coder)
    payload: Dict[str, Any] = {"codes": result}
    note = _coder_visibility_note(coder)
    if note:
        payload["coder_visibility"] = note
        return json.dumps(payload, indent=2)
    return json.dumps(result, indent=2)


@mcp.tool()
@_tool_guard
def get_cases_by_code(code_id: int, coder: Optional[str] = None) -> str:
    """Get all cases that contain a specific code.

    Shows which cases/participants have text segments coded with
    a particular theme or code.


    Coder visibility (QualCoder 4.0 projects): when the project hides
    some coders' work, counts reflect only visible coders by
    default (what the user sees in QualCoder). The result is then
    wrapped in an object carrying a coder_visibility block
    (otherwise it stays a plain array). Pass coder to analyze one
    specific coder's rows from the full data instead.

    Args:
        code_id: The numeric ID of the code
        coder: Optional coder name; counts that coder's rows from the
               base tables, bypassing the visibility filter

    Only codings fully contained in a case's text intervals are counted
    (QualCoder report semantics).

    Returns:
        JSON array of cases containing this code; each entry has case_id,
        case_name, memo, occurrence_count
    """
    result = get_db().get_cases_by_code(code_id, coder=coder)
    payload: Dict[str, Any] = {"cases": result}
    note = _coder_visibility_note(coder)
    if note:
        payload["coder_visibility"] = note
        return _ai_json(payload, indent=2)
    return _ai_json(result, indent=2)


# ============================================================================
# AI-ASSISTED CODING TOOLS (NEW CONVERSATIONAL WORKFLOW)
# ============================================================================

@mcp.tool()
@_tool_guard
def analyze_for_coding(
    file_ids: List[int],
    code_names: Optional[List[str]] = None,
    instruction: str = "Code all relevant segments",
    min_confidence: float = 0.7
) -> str:
    """Analyze files and suggest codings for user review.

    This tool performs AI analysis and returns suggestions in a conversational
    format for the user to review in the chat. NO changes are made to the
    database until the user explicitly approves and uses apply_codings.

    MANDATORY QUALCODER CHECK: if the result contains `qualcoder_open: true`,
    STOP and ask the user to close QualCoder (or close this project inside
    it) before proceeding with ANY part of the coding workflow — do not read
    files for coding, do not record suggestions, do not continue until the
    user confirms it is closed. All database writes are refused while
    QualCoder has the project open, so continuing would waste the whole
    suggest -> review -> approve flow only to fail at apply time. After the
    user confirms, re-check with get_current_project (its `qualcoder_open`
    field) and proceed only when it is false.

    WORKFLOW:
    1. I analyze the files and identify relevant segments
    2. I present suggestions to you in the chat with reasoning
    3. You review and can ask questions about specific suggestions
       (edit_suggestion adjusts a span or code during review)
    4. You approve/reject suggestions using update_suggestion_status
    5. You apply approved suggestions using apply_codings

    SPAN STYLE (learned from real researcher use): prefer
    COMPLETE-THOUGHT spans — a quote that stands alone (a full sentence
    or small paragraph with enough context to be quotable in a paper) —
    over minimal phrases. Researchers overwhelmingly widen short spans
    at review time; err generous.

    CO-CODING: actively consider whether each segment warrants MULTIPLE
    codes. Coding the same span under several codes is normal, expected
    qualitative practice (the schema supports it — record one
    suggestion per code). If the researcher adds a second code to a
    fragment during review, treat that as a calibration signal: look
    for the same code pairing in subsequent segments.

    Args:
        file_ids: List of file IDs to analyze
        code_names: Optional list of specific code names to apply
        instruction: Guidance for what to look for in the analysis.
                     Also the place to set span style once per session —
                     e.g. "code generous spans, full paragraphs" or
                     "keep spans to single sentences" — and honor it in
                     every suggestion you record.
        min_confidence: Minimum confidence for suggestions (0.0-1.0)

    Returns:
        Formatted text presenting all suggestions with:
        - File and segment information
        - Code being applied
        - Text excerpt
        - AI reasoning
        - Confidence score
        - Unique GUID for each suggestion

    Example:
        "Analyze files 1-3 for DATA PRACTICES codes"
    """
    db = get_db()

    # Clamp the confidence threshold to the same [0,1] range suggestion
    # confidences are clamped to (a threshold > 1 would filter everything)
    try:
        min_confidence = max(0.0, min(1.0, float(min_confidence)))
    except (TypeError, ValueError):
        min_confidence = 0.7

    # Get files and codes
    all_files = db.list_files()
    all_codes = db.list_codes()

    # Filter to requested files
    files_to_analyze = [f for f in all_files if f['id'] in file_ids]
    if not files_to_analyze:
        return json.dumps({"error": "No valid files found with those IDs"})

    # Filter codes if specified
    if code_names:
        codes_to_use = [c for c in all_codes if c['name'] in code_names]
        if not codes_to_use:
            return json.dumps({"error": f"No codes found matching: {code_names}"})
    else:
        codes_to_use = all_codes

    # Create analysis session
    session = AICodingSession(
        project_path=str(db.db_path),  # Convert Path to string for JSON serialization
        description=f"Analysis of {len(files_to_analyze)} files with {len(codes_to_use)} codes",
        file_ids=file_ids,
        code_names=[c['name'] for c in codes_to_use],
        instruction=instruction,
        min_confidence=min_confidence
    )

    # Save session (Claude records its suggestions with record_suggestions)
    session_manager.save_session(session)

    # Session-start QualCoder check: reads are safe, so the session is
    # still created — but the whole suggest -> review -> approve flow would
    # dead-end at apply time (writes are refused while QualCoder has the
    # project open). Surface it NOW and instruct the client to check with
    # the user before continuing.
    qualcoder_banner = ""
    action_required = None
    state, holder = qualcoder_lock_state(_current_project_folder())
    if state == "active":
        action_required = (
            f"QualCoder appears to have this project open (user "
            f"{holder or 'unknown'}). Ask the user to close QualCoder (or "
            f"close this project in it) before continuing — all database "
            f"writes will be refused while it is open, so the "
            f"review-and-approve work would be wasted. Once they confirm it "
            f"is closed, re-check via get_current_project (the "
            f"`qualcoder_open` field must be false) and only then proceed "
            f"with the coding workflow."
        )
        qualcoder_banner = f"""
⚠️ **STOP — QUALCODER HAS THIS PROJECT OPEN**

qualcoder_open: true
action_required: {action_required}
"""

    output = f"""{qualcoder_banner}
📊 **ANALYSIS SESSION CREATED**

Session ID: `{session.session_id}`
(pass it to the other coding tools as coding_session_id)

**Analysis Parameters:**
- Files: {len(files_to_analyze)} files ({', '.join(f['name'] for f in files_to_analyze)})
- Codes: {len(codes_to_use)} codes ({', '.join(c['name'] for c in codes_to_use)})
- Instruction: "{instruction}"
- Min confidence: {min_confidence}

**IMPORTANT - NEXT STEPS:**

This session has been created and saved. Now YOU (Claude) need to:

1. **Read each file** (use `analyze_file_with_coding`) and identify segments
   that match the requested codes and instruction
2. **Record your suggestions** with the `record_suggestions` tool, passing this
   session ID and a list of suggestion objects:
   `{{"file_id": ..., "code_name": "...", "start_pos": ..., "end_pos": ...,
   "segment_text": "<exact excerpt>", "reasoning": "...", "confidence": 0.0-1.0}}`
   Each suggestion is verified against the file text before it is stored.
3. **Present the recorded suggestions to the user** in a clear, reviewable format

**FOR THE USER:**
Once Claude records and presents suggestions, you can:
- Review the suggestions in the chat
- Use `review_suggestions` to see more details
- Use `update_suggestion_status` to approve/reject specific suggestions
- Use `apply_codings` to write approved suggestions to the database
"""

    # Structured envelope: session_id and the QualCoder-open signal as REAL
    # fields (track4 #4) so structured-field clients see the "ask" rung the
    # same way get_current_project reports the "re-check" rung. The prose
    # banner is preserved in `instructions` (and still contains the literal
    # `qualcoder_open: true` / `action_required:` markers).
    envelope: Dict[str, Any] = {
        "coding_session_id": session.session_id,
        "session_id": session.session_id,  # deprecated duplicate of coding_session_id (kept one release)
        # Always present (false when clear), matching get_current_project's
        # always-present field so structured consumers get a consistent
        # shape (QA6-1)
        "qualcoder_open": state == "active",
    }
    if state == "active":
        envelope["action_required"] = action_required
    else:
        # P1-5: the ask rung of the ladder also listens to the 4.0
        # GUI-open heuristics (4.0 writes no lock file). WARN-level: ask
        # the user, never refuse on a heuristic.
        signals = qualcoder_gui_signals(_current_project_folder())
        envelope["qualcoder_gui_signals"] = signals
        if signals:
            envelope["qualcoder_gui_hint"] = (
                "This project APPEARS to be open in QualCoder ("
                + "; ".join(signals) + "). That is a heuristic (QualCoder "
                "4.0 writes no lock file), so ASK THE USER whether a "
                "QualCoder window has this project open before "
                "continuing; writes into a live 4.0 session can be lost "
                "or corrupted, and an open 4.0 window will not display "
                "external changes until the project is reopened."
            )
    envelope["instructions"] = output
    return json.dumps(envelope, indent=2)


def _code_name_collisions(name: str) -> Optional[str]:
    """Existing code name(s) a proposal name collides with (QA5-1 style:
    exact match first, else case-insensitive matches), or None."""
    codes = get_db().list_codes()
    exact = [c["name"] for c in codes if c["name"] == name]
    if exact:
        return exact[0]
    ci = [c["name"] for c in codes if c["name"].lower() == name.lower()]
    return ", ".join(ci) if ci else None


def _validate_proposal_evidence(ro_db, items, file_cache):
    """Validate a proposal's evidence spans exactly like record_suggestions
    validates suggestion positions. Returns (kept, rejected, unsafe_files)."""
    kept, rejected = [], []
    unsafe_files: Dict[int, str] = {}
    for idx, item in enumerate(items or []):
        if not isinstance(item, dict):
            rejected.append({"index": idx, "reason": "evidence must be an object"})
            continue
        file_id = item.get("file_id")
        if not isinstance(file_id, int) or isinstance(file_id, bool):
            rejected.append({"index": idx, "reason": "file_id (integer) is required"})
            continue
        if file_id not in file_cache:
            file_cache[file_id] = ro_db.get_file_content(file_id)
        fc = file_cache[file_id]
        fulltext = (fc or {}).get("content") or ""
        if fc is None or not fc.get("is_text") or not fulltext:
            rejected.append({"index": idx,
                             "reason": f"file_id {file_id} is not a text source"})
            continue
        segment_text = item.get("segment_text")
        if not isinstance(segment_text, str) or not segment_text.strip():
            rejected.append({"index": idx,
                             "reason": "segment_text (non-empty string) is required"})
            continue
        ok, start, end, corrected, pos_error = _resolve_segment_positions(
            fulltext, item.get("start_pos"), item.get("end_pos"), segment_text)
        if not ok:
            rejected.append({"index": idx, **pos_error})
            continue
        if file_id not in unsafe_files and not db_position_safe(fulltext):
            unsafe_files[file_id] = fc["name"]
        kept.append({
            "file_id": file_id,
            "file_name": fc["name"],
            "start_pos": start,
            "end_pos": end,
            "segment_text": fulltext[start:end],   # authoritative slice
            "positions_corrected": corrected,
            "span_alternatives": _compute_span_alternatives(
                fulltext, start, end),
        })
    return kept, rejected, unsafe_files


@mcp.tool()
@_tool_guard
def record_suggestions(
    coding_session_id: str,
    suggestions: List[Dict[str, Any]],
    replace: bool = False
) -> str:
    """Record AI coding suggestions into an analysis session for user review.

    This is step 2 of the AI coding workflow: after analyze_for_coding creates
    a session, use this tool to persist the suggestions you (Claude) identified
    by reading the files. Nothing is written to the QualCoder database — the
    suggestions are stored in the session for the user to review, approve, and
    apply.

    Every suggestion is validated against the project before it is stored:
    - the file must exist and be a text source
    - the code must exist (give code_id, or code_name matched case-insensitively)
    - segment_text must be an exact, verbatim excerpt of the file text
    - positions are verified: if fulltext[start_pos:end_pos] != segment_text
      but the text occurs exactly once in the file, positions are corrected
      automatically (flagged as positions_corrected); otherwise the suggestion
      is rejected with an explanation. start_pos/end_pos may be omitted when
      the excerpt is unique in the file.

    SPAN STYLE: prefer COMPLETE-THOUGHT spans — a full sentence or small
    paragraph that stands alone as a quotable extract — over minimal
    phrases. Real researchers consistently widen short spans at review
    time (edit_suggestion exists for that, but getting it right first
    saves them the round-trip). If the session's `instruction` set a span
    style (e.g. "code generous spans"), honor it in every suggestion.

    CO-CODING: for each segment, actively ask whether it warrants MORE
    THAN ONE code — record one suggestion per code on the same span.
    Same-span different-code suggestions are legitimate and expected in
    qualitative work; do not default to one code per fragment. When the
    researcher adds a second code to a fragment during review, treat it
    as a calibration signal for the code pairings in your subsequent
    suggestions.

    Args:
        coding_session_id: The session ID from analyze_for_coding
        suggestions: List of suggestion objects with keys:
            file_id (int, required), code_id (int) or code_name (str),
            start_pos/end_pos (int, optional if the excerpt is unique),
            segment_text (str, required — exact excerpt),
            reasoning (str), confidence (float 0.0-1.0),
            context_before/context_after (str, optional — auto-filled)
        replace: If True, discard previously recorded PENDING suggestions
                 first (approved/rejected/applied are always kept)

    Returns:
        JSON with recorded suggestions (GUIDs for approval), per-item
        rejections with reasons, duplicate count, and session statistics.
        Each recorded item lists its available span alternatives by label
        only ("alternatives": ["shorter","longer"]). Do NOT print the
        alternative texts. End your summary with ONE line, e.g.: "Any
        span can be widened or narrowed — just say e.g. longer on #2."
        When the user asks for longer/shorter, call edit_suggestion with
        use_alternative — never ask them for character positions.
        If it contains `position_safety_warning`, you MUST relay that
        warning to the user before proceeding to approval — codings on the
        named files can render shifted or unhighlighted in QualCoder's
        editor (reports and exports are unaffected).

    Example:
        record_suggestions(coding_session_id="...", suggestions=[
            {"file_id": 4, "code_name": "Burnout", "start_pos": 96,
             "end_pos": 129, "segment_text": "by Thursday I am running on fumes",
             "reasoning": "Explicit exhaustion metaphor", "confidence": 0.9}])
    """
    # Bridge fix: some MCP middleware strips arguments named
    # 'session_id' (reserved for its own routing); the tool
    # argument is coding_session_id, aliased for the body.
    session_id = coding_session_id
    if not session_manager.session_exists(session_id):
        return json.dumps({
            "error": f"Session {session_id} not found",
            "available_sessions": session_manager.list_sessions()
        })

    session = session_manager.load_session(session_id)

    # Suggestions may only be recorded against the project they were
    # analyzed in (same binding as apply_codings)
    mismatch = _check_session_project(session)
    if mismatch is not None:
        return json.dumps(mismatch, indent=2)

    if not isinstance(suggestions, list) or not suggestions:
        return json.dumps({
            "error": "suggestions must be a non-empty list of suggestion objects"
        })

    ro_db = get_db()
    codes = ro_db.list_codes()
    codes_by_id = {c["id"]: c for c in codes}
    codes_by_name = {c["name"].lower(): c for c in codes}

    removed_pending = session.remove_pending_suggestions() if replace else 0

    file_cache: Dict[int, Optional[Dict[str, Any]]] = {}
    recorded = []
    rejected = []
    skipped_duplicates = 0
    unsafe_files: Dict[int, str] = {}

    for idx, item in enumerate(suggestions):
        if not isinstance(item, dict):
            rejected.append({"index": idx, "reason": "each suggestion must be an object"})
            continue

        # --- file ---
        file_id = item.get("file_id")
        if not isinstance(file_id, int) or isinstance(file_id, bool):
            rejected.append({"index": idx, "reason": "file_id (integer) is required"})
            continue
        if file_id not in file_cache:
            file_cache[file_id] = ro_db.get_file_content(file_id)
        file_content = file_cache[file_id]
        if file_content is None:
            rejected.append({"index": idx, "reason": f"file_id {file_id} does not exist"})
            continue
        fulltext = file_content.get("content") or ""
        if not file_content.get("is_text") or not fulltext:
            rejected.append({
                "index": idx,
                "reason": f"file '{file_content['name']}' is not a text source — "
                          f"text codings require a file with text content"
            })
            continue
        if file_id not in unsafe_files and not db_position_safe(fulltext):
            unsafe_files[file_id] = file_content["name"]

        # --- code ---
        code = None
        if item.get("code_id") is not None:
            code_id = item["code_id"]
            if not isinstance(code_id, int) or isinstance(code_id, bool):
                rejected.append({"index": idx, "reason": "code_id must be an integer"})
                continue
            code = codes_by_id.get(code_id)
            if code is None:
                rejected.append({"index": idx, "reason": f"code_id {code_id} does not exist"})
                continue
        elif item.get("code_name"):
            code = codes_by_name.get(str(item["code_name"]).lower())
            if code is None:
                rejected.append({
                    "index": idx,
                    "reason": f"code '{item['code_name']}' not found",
                    "available_codes": sorted(c["name"] for c in codes)[:50]
                })
                continue
        else:
            rejected.append({"index": idx, "reason": "each suggestion needs code_id or code_name"})
            continue

        # --- segment text ---
        segment_text = item.get("segment_text")
        if not isinstance(segment_text, str) or not segment_text.strip():
            rejected.append({"index": idx, "reason": "segment_text (non-empty string) is required"})
            continue

        # --- confidence ---
        try:
            confidence = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            rejected.append({"index": idx, "reason": "confidence must be a number between 0.0 and 1.0"})
            continue

        # --- positions (verified against the file text) ---
        ok, start_pos, end_pos, corrected, pos_error = _resolve_segment_positions(
            fulltext, item.get("start_pos"), item.get("end_pos"), segment_text
        )
        if not ok:
            rejected.append({"index": idx, **pos_error})
            continue

        if session.has_duplicate(file_id, code["id"], start_pos, end_pos):
            skipped_duplicates += 1
            continue

        # Store the authoritative fulltext slice: positions are the record
        # of truth, and the apply-time write requires seltext to equal the
        # slice exactly (provided text may differ by U+2029 vs newline)
        segment_text = fulltext[start_pos:end_pos]

        context_before = item.get("context_before")
        if not isinstance(context_before, str):
            context_before = fulltext[max(0, start_pos - 100):start_pos]
        context_after = item.get("context_after")
        if not isinstance(context_after, str):
            context_after = fulltext[end_pos:end_pos + 100]

        suggestion = CodingSuggestion(
            file_id=file_id,
            file_name=file_content["name"],
            code_id=code["id"],
            code_name=code["name"],
            start_pos=start_pos,
            end_pos=end_pos,
            segment_text=segment_text,
            reasoning=str(item.get("reasoning", "")),
            confidence=confidence,
            status="pending",
            context_before=context_before,
            context_after=context_after,
            span_alternatives=_compute_span_alternatives(
                fulltext, start_pos, end_pos),
        )
        session.add_suggestion(suggestion)
        recorded.append({
            "guid": suggestion.guid,
            "file_id": file_id,
            "file_name": file_content["name"],
            "code_name": code["name"],
            "start_pos": start_pos,
            "end_pos": end_pos,
            "positions_corrected": corrected,
            # labels only — the full alternatives (with previews) live on
            # the suggestion; review_suggestions shows them compactly
            "alternatives": [a["label"]
                             for a in suggestion.span_alternatives],
        })

    session_manager.save_session(session)

    result = {
        "coding_session_id": session_id,
        "session_id": session_id,  # deprecated duplicate of coding_session_id (kept one release)
        "recorded_count": len(recorded),
        "recorded": recorded,
        "rejected_count": len(rejected),
        "rejected": rejected,
        "skipped_duplicates": skipped_duplicates,
        "statistics": session.get_statistics(),
        "next_step": "Present the suggestions to the user; approve/reject with "
                     "update_suggestion_status, then write with apply_codings."
    }
    if replace:
        result["replaced_pending"] = removed_pending
    if unsafe_files:
        result["position_safety_warning"] = (
            f"File(s) {sorted(unsafe_files.values())} contain \r\n sequences "
            f"or characters beyond U+FFFF (e.g. emoji). QualCoder's GUI uses "
            f"a different position system for such files (its documented "
            f"emoji bug), so codings on them may render shifted or "
            f"unhighlighted in the QualCoder editor, and GUI-created codings "
            f"there may not verify. Reports and exports are unaffected."
        )
    return json.dumps(result, indent=2)


@mcp.tool()
@_tool_guard
def review_suggestions(
    coding_session_id: str,
    suggestion_guids: Optional[List[str]] = None,
    show_context: bool = True
) -> str:
    """Review coding suggestions in detail.

    Shows detailed information about specific suggestions from an analysis
    session, WITH the surrounding text by default — researchers judge a
    span by what is around it (is the quote complete? should it be
    wider?). Use this to examine suggestions before approving/rejecting;
    if a span needs adjusting, edit_suggestion changes it in place.

    SPAN ALTERNATIVES: each pending, not-yet-adjusted suggestion may
    carry ready-made shorter/longer spans (core sentence / enclosing
    paragraph or speaker turn). Present them COMPACTLY — a one-line
    "want it shorter (1 sentence, 89 chars) or longer (paragraph,
    412 chars)?" affordance, never full alternative quotes per
    suggestion (decision fatigue). Surface them proactively only when
    the researcher has already adjusted spans this session (the
    calibration signal) or asks about context; otherwise mention once
    that alternatives exist. One pick applies via
    edit_suggestion(use_alternative="shorter"|"longer"). Suggestions
    the researcher already adjusted show "(adjusted)" and get no offers
    — do not offer to undo their decision.

    Args:
        coding_session_id: The session ID from analyze_for_coding
        suggestion_guids: Optional list of specific suggestion GUIDs to review
        show_context: Include surrounding text context (default: True;
                      pass False for a compact listing)

    Returns:
        Detailed formatted information about the requested suggestions

    Example:
        "Show me more details about suggestion abc-123-def"
        "Review all pending suggestions with context"
    """
    # Bridge fix: some MCP middleware strips arguments named
    # 'session_id' (reserved for its own routing); the tool
    # argument is coding_session_id, aliased for the body.
    session_id = coding_session_id
    if not session_manager.session_exists(session_id):
        return json.dumps({"error": f"Session {session_id} not found"})

    session = session_manager.load_session(session_id)

    # Get suggestions to show
    if suggestion_guids:
        suggestions = [session.get_suggestion_by_guid(guid) for guid in suggestion_guids]
        suggestions = [s for s in suggestions if s is not None]
    else:
        suggestions = session.suggestions

    if not suggestions:
        return "No suggestions found."

    output = [f"**Review of {len(suggestions)} Suggestion(s)**\n"]

    small_subset = bool(suggestion_guids) and len(suggestions) <= 5

    for i, sugg in enumerate(suggestions, 1):
        output.append(f"\n{'='*70}")
        output.append(f"**Suggestion {i}** (GUID: `{sugg.guid}`)")
        adjusted = getattr(sugg, "adjusted", False)
        output.append(f"Status: {sugg.status.upper()}"
                      + (" (adjusted)" if adjusted else ""))
        output.append(f"\n📄 **File:** {sugg.file_name} (ID: {sugg.file_id})")
        output.append(f"🏷️  **Code:** {sugg.code_name} (ID: {sugg.code_id})")
        output.append(f"📍 **Position:** {sugg.start_pos}-{sugg.end_pos}")
        output.append(f"💯 **Confidence:** {sugg.confidence:.2f}")
        output.append(f"\n**Segment Text:**")
        output.append(f"```\n{sugg.segment_text}\n```")
        output.append(f"\n**AI Reasoning:**")
        output.append(sugg.reasoning)

        if show_context:
            if sugg.context_before:
                output.append(f"\n**Context Before:**")
                output.append(f"```\n{sugg.context_before}\n```")
            if sugg.context_after:
                output.append(f"\n**Context After:**")
                output.append(f"```\n{sugg.context_after}\n```")

        # Span alternatives: one line each, unit-glossed; previews only in
        # the show_context detail view for small guid subsets (token cost);
        # nothing in the compact listing; no offers on adjusted spans
        alternatives = getattr(sugg, "span_alternatives", None) or []
        if (alternatives and sugg.status == "pending" and not adjusted
                and show_context):
            if small_subset:
                for a in alternatives:
                    output.append(f"↔ {_alternative_gloss(a)}: "
                                  f"“{a['preview']}”")
            else:
                picks = " / ".join(_alternative_gloss(a)
                                   for a in alternatives)
                output.append(f"↔ Span alternatives: {picks} — apply with "
                              f"edit_suggestion(use_alternative=...)")

    return "\n".join(output)


@mcp.tool()
@_tool_guard
def edit_suggestion(
    coding_session_id: str,
    suggestion_guid: str,
    start_pos: Optional[int] = None,
    end_pos: Optional[int] = None,
    segment_text: Optional[str] = None,
    use_alternative: Optional[str] = None,
    code_id: Optional[int] = None,
    code_name: Optional[str] = None,
) -> str:
    """Adjust a PENDING suggestion's span and/or code before approval.

    The review-time refinement tool: when the researcher wants a
    suggestion's span widened to a complete quote (or narrowed, or
    moved), or wants a different code on it, edit it here instead of
    rejecting and re-recording. Session-only — nothing touches the
    project database until apply_codings.

    Span editing accepts one of:
    - use_alternative="shorter"|"longer" — the one-call answer to
      "make it shorter/longer" (details under Args);
    - new start_pos and/or end_pos ("extend it to position 120"; an
      omitted bound keeps its current value) — the stored text becomes
      the exact file slice for the new span;
    - a new segment_text (the exact excerpt; positions optional when it
      occurs exactly once in the file) — verified with the same
      machinery as record_suggestions, positions auto-corrected when
      the excerpt is unique.
    The surrounding context shown by review_suggestions is refreshed,
    and the shorter/longer alternatives are recomputed for the new span.

    Edits are not reversible via the alternatives: they recompute from
    the CURRENT span (shorter after longer is the new paragraph's core
    sentence, not the original span). To undo, use the previous span in
    the result's changes.span.from.

    Only PENDING suggestions are editable: applied ones are immutable
    (the coding is in the database — use delete_coding + record again),
    and approved/rejected ones reflect a decision the user already made
    (change the decision with update_suggestion_status, then edit).

    Args:
        coding_session_id: The session ID from analyze_for_coding
        suggestion_guid: The suggestion to edit
        start_pos: New start position (code-point offset, 0-based)
        end_pos: New end position (end-exclusive)
        segment_text: New exact excerpt (alternative to positions)
        use_alternative: "shorter" | "longer" — apply the
            server-precomputed span alternative (shorter = the span
            trimmed to its core sentence; longer = the enclosing
            paragraph, else ± one sentence). This is the preferred
            response to "make #3 longer" / "widen that one": one call,
            no positions needed. Not every suggestion has both: shorter
            is absent when the span is already one sentence, longer at
            document boundaries — on a miss the error lists which
            labels exist; fall back to explicit start_pos/end_pos or
            segment_text. Mutually exclusive with the manual span
            parameters.
        code_id: Change the code by id (existing codes only)
        code_name: Change the code by name (case-insensitive match
                   against the live codebook)

    Returns:
        JSON with the changes made (old -> new span/code), the new
        segment text, recomputed span_alternatives, and
        positions_corrected if the excerpt was re-located. If it
        contains `position_safety_warning`, relay it to the user.
    """
    # Bridge fix: some MCP middleware strips arguments named
    # 'session_id' (reserved for its own routing); the tool
    # argument is coding_session_id, aliased for the body.
    session_id = coding_session_id
    if not session_manager.session_exists(session_id):
        return json.dumps({
            "error": f"Session {session_id} not found",
            "available_sessions": session_manager.list_sessions()
        })
    session = session_manager.load_session(session_id)
    mismatch = _check_session_project(session)
    if mismatch is not None:
        return json.dumps(mismatch, indent=2)

    sugg = session.get_suggestion_by_guid(suggestion_guid)
    if sugg is None:
        return json.dumps({"error": f"Suggestion {suggestion_guid} not found"})
    if sugg.status != "pending":
        hints = {
            "applied": "the coding is already in the database — use "
                       "delete_coding to remove it, then record a new "
                       "suggestion",
            "approved": "un-approve it first (update_suggestion_status "
                        "reject, then approve after editing) or leave the "
                        "decision as made",
            "rejected": "it was rejected — record a corrected suggestion "
                        "with record_suggestions instead, or approve it "
                        "as-is if the rejection was a mistake",
        }
        return json.dumps({
            "error": f"Only PENDING suggestions can be edited — this one is "
                     f"{sugg.status.upper()}; "
                     f"{hints.get(sugg.status, 'no edit path')}"
        })

    manual_span = (start_pos is not None or end_pos is not None
                   or segment_text is not None)
    if use_alternative is not None:
        if manual_span:
            return json.dumps({
                "error": "use_alternative is mutually exclusive with manual "
                         "start_pos/end_pos/segment_text — pick one"
            })
        # Recompute from the CURRENT fulltext (stored span_alternatives are
        # presentational only — the file may have changed, and pre-v0.8
        # sessions have none stored)
        alt_content = get_db().get_file_content(sugg.file_id)
        alt_fulltext = (alt_content or {}).get("content") or ""
        if alt_content is None or not alt_content.get("is_text") \
                or not alt_fulltext:
            return json.dumps({
                "error": f"file_id {sugg.file_id} no longer exists or is "
                         f"not a text source"
            })
        current_alts = _compute_span_alternatives(
            alt_fulltext, sugg.start_pos, sugg.end_pos)
        alt = next((a for a in current_alts
                    if a.get("label") == use_alternative), None)
        if alt is None:
            return json.dumps({
                "error": f"No '{use_alternative}' span alternative exists "
                         f"for this suggestion",
                "available_alternatives": [a["label"] for a in current_alts],
                "hint": "Fall back to explicit start_pos/end_pos or "
                        "segment_text for an arbitrary adjustment.",
            })
        start_pos, end_pos = alt["start_pos"], alt["end_pos"]
        manual_span = True

    wants_span = manual_span
    wants_code = code_id is not None or code_name is not None
    if not wants_span and not wants_code:
        return json.dumps({
            "error": "Nothing to change — pass start_pos/end_pos/"
                     "segment_text, use_alternative, and/or "
                     "code_id/code_name"
        })

    ro_db = get_db()
    changes: Dict[str, Any] = {}
    result: Dict[str, Any] = {"coding_session_id": session_id,
                              "session_id": session_id,  # deprecated duplicate of coding_session_id (kept one release)
                              "guid": sugg.guid}

    # --- code change (existing codes only, record_suggestions rules) ---
    new_code = None
    if wants_code:
        codes = ro_db.list_codes()
        if code_id is not None:
            if not isinstance(code_id, int) or isinstance(code_id, bool):
                return json.dumps({"error": "code_id must be an integer"})
            new_code = next((c for c in codes if c["id"] == code_id), None)
            if new_code is None:
                return json.dumps({"error": f"code_id {code_id} does not exist"})
        else:
            new_code = next(
                (c for c in codes
                 if c["name"].lower() == str(code_name).lower()), None)
            if new_code is None:
                return json.dumps({
                    "error": f"code '{code_name}' not found",
                    "available_codes": sorted(c["name"] for c in codes)[:50],
                })

    # --- span change (same position machinery as record_suggestions) ---
    new_start, new_end, corrected = sugg.start_pos, sugg.end_pos, False
    fulltext = None
    if wants_span:
        file_content = ro_db.get_file_content(sugg.file_id)
        fulltext = (file_content or {}).get("content") or ""
        if file_content is None or not file_content.get("is_text") or not fulltext:
            return json.dumps({
                "error": f"file_id {sugg.file_id} no longer exists or is "
                         f"not a text source"
            })
        for label, v in (("start_pos", start_pos), ("end_pos", end_pos)):
            if v is not None and (not isinstance(v, int) or isinstance(v, bool)):
                return json.dumps({"error": f"{label} must be an integer"})
        if segment_text is not None:
            if not isinstance(segment_text, str) or not segment_text.strip():
                return json.dumps({
                    "error": "segment_text must be a non-empty string"})
            ok, new_start, new_end, corrected, pos_error = \
                _resolve_segment_positions(fulltext, start_pos, end_pos,
                                           segment_text)
            if not ok:
                details = {k: v for k, v in pos_error.items()
                           if k != "reason"}
                return json.dumps({"error": pos_error["reason"], **details})
        else:
            new_start = start_pos if start_pos is not None else sugg.start_pos
            new_end = end_pos if end_pos is not None else sugg.end_pos
            if not (0 <= new_start < new_end <= len(fulltext)):
                return json.dumps({
                    "error": f"positions must satisfy 0 <= start < end <= "
                             f"{len(fulltext)} (file length), got "
                             f"{new_start}-{new_end}"
                })

    final_code_id = new_code["id"] if new_code else sugg.code_id
    if (new_start, new_end, final_code_id) == (
            sugg.start_pos, sugg.end_pos, sugg.code_id):
        return json.dumps({"error": "No effective change — the span and "
                                    "code are unchanged"})

    # Refuse an edit that lands exactly on another suggestion
    for other in session.suggestions:
        if (other.guid != sugg.guid and other.file_id == sugg.file_id
                and other.code_id == final_code_id
                and other.start_pos == new_start
                and other.end_pos == new_end):
            return json.dumps({
                "error": f"That edit would duplicate suggestion "
                         f"{other.guid} ({other.code_name}, "
                         f"{other.start_pos}-{other.end_pos}, "
                         f"{other.status}) — reject this one instead"
            })

    if wants_span:
        changes["span"] = {"from": f"{sugg.start_pos}-{sugg.end_pos}",
                           "to": f"{new_start}-{new_end}"}
        if use_alternative is not None:
            changes["span"]["via"] = f"use_alternative={use_alternative}"
        sugg.start_pos, sugg.end_pos = new_start, new_end
        # Authoritative slice + refreshed context, as at record time;
        # alternatives recomputed for the new span
        sugg.segment_text = fulltext[new_start:new_end]
        sugg.context_before = fulltext[max(0, new_start - 100):new_start]
        sugg.context_after = fulltext[new_end:new_end + 100]
        sugg.span_alternatives = _compute_span_alternatives(
            fulltext, new_start, new_end)
        if not db_position_safe(fulltext):
            result["position_safety_warning"] = (
                f"File '{sugg.file_name}' contains \r\n or characters "
                f"beyond U+FFFF; codings on it may render shifted in "
                f"QualCoder's editor. Relay this to the user."
            )
    if new_code is not None and new_code["id"] != sugg.code_id:
        changes["code"] = {"from": sugg.code_name, "to": new_code["name"]}
        sugg.code_id = new_code["id"]
        sugg.code_name = new_code["name"]

    sugg.adjusted = True

    # Affordance bookkeeping (server-emitted hints — the pattern that
    # actually steers clients, per the track4 audit): the first MANUAL span
    # edit triggers the shortcut hint once; three same-direction alternative
    # picks trigger the calibration-escalation hint once.
    stats = getattr(session, "span_edit_stats", None)
    if stats is None:
        stats = {"manual_edits": 0, "shorter_picks": 0, "longer_picks": 0}
        session.span_edit_stats = stats
    if wants_span:
        if use_alternative is None:
            stats["manual_edits"] = stats.get("manual_edits", 0) + 1
            if stats["manual_edits"] == 1:
                result["span_shortcut_hint"] = (
                    "The researcher is adjusting spans. From now on, when "
                    "presenting suggestions add one line offering the "
                    "shortcut: every suggestion has precomputed "
                    "shorter/longer spans — they can just say 'longer on "
                    "#N'."
                )
        elif use_alternative in ("shorter", "longer"):
            key = f"{use_alternative}_picks"
            stats[key] = stats.get(key, 0) + 1
            if stats[key] == 3:
                fix = ("re-record the remaining suggestions at paragraph "
                       "level, or set the session instruction to 'code "
                       "paragraph-level spans'"
                       if use_alternative == "longer" else
                       "re-record the remaining suggestions at sentence "
                       "level, or set the session instruction to 'code "
                       "tight single-sentence spans'")
                result["calibration_hint"] = (
                    f"That is the third '{use_alternative}' pick this "
                    f"session — the default span length is miscalibrated. "
                    f"Offer the session-level fix instead of continuing "
                    f"per-item picks: {fix}."
                )

    session.last_modified = datetime.now().isoformat()
    session_manager.save_session(session)

    result.update({
        "success": True,
        "changes": changes,
        "positions_corrected": corrected,
        "segment_text": sugg.segment_text,
        # compact render forms only (label + unit gloss + code points)
        "span_alternatives": [_alternative_gloss(a)
                              for a in sugg.span_alternatives],
        "status": sugg.status,
        "next_step": "Still pending — approve with update_suggestion_status "
                     "when the user is happy with it.",
    })
    return json.dumps(result, indent=2)


@mcp.tool()
@_tool_guard
def update_suggestion_status(
    coding_session_id: str,
    approve: Optional[List[str]] = None,
    reject: Optional[List[str]] = None
) -> str:
    """Approve or reject specific coding suggestions.

    Use this to record the USER'S decisions about which suggestions should
    be applied to the database. Approve only the suggestions the user has
    actually reviewed and confirmed — do not approve on their behalf.

    Suggestions already APPLIED to the database are immutable here and are
    skipped (reported as skipped_applied); to remove an applied coding,
    use delete_coding.

    Args:
        coding_session_id: The session ID from analyze_for_coding
        approve: List of suggestion GUIDs the user approved
        reject: List of suggestion GUIDs the user rejected

    Returns:
        Confirmation of status updates (including any skipped applied
        suggestions)

    Example:
        User says "the first two look right, drop the third" ->
        update_suggestion_status(coding_session_id, approve=[guid1, guid2],
        reject=[guid3])
    """
    # Bridge fix: some MCP middleware strips arguments named
    # 'session_id' (reserved for its own routing); the tool
    # argument is coding_session_id, aliased for the body.
    session_id = coding_session_id
    if not session_manager.session_exists(session_id):
        return json.dumps({"error": f"Session {session_id} not found"})

    session = session_manager.load_session(session_id)

    # Update statuses
    result = session.update_suggestions_by_guid(approve=approve, reject=reject)

    # Save updated session
    session_manager.save_session(session)

    # Get updated stats
    stats = session.get_statistics()

    skipped_note = ""
    if result.get("skipped_applied"):
        skipped_note = (
            f"- Already applied (left unchanged): {result['skipped_applied']} — "
            f"applied suggestions are already in the database; to remove one, "
            f"use delete_coding\n"
        )

    output = f"""
✅ **Updated Suggestion Statuses**

Changed:
- Approved: {result['approved']} suggestions
- Rejected: {result['rejected']} suggestions
{skipped_note}
Current Status:
- Total: {stats['total_suggestions']} suggestions
- Approved: {stats['approved']}
- Rejected: {stats['rejected']}
- Pending: {stats['pending']}
- Applied: {stats.get('applied', 0)}

**Next Step:**
Use `apply_codings` with session ID `{session_id}` to write approved suggestions to the database.
"""

    return output


@mcp.tool()
@_tool_guard
def apply_codings(
    coding_session_id: str,
    create_backup: bool = True,
    owner: Optional[str] = None
) -> str:
    """Apply approved coding suggestions to the project database.

    THIS WRITES TO THE DATABASE. This is the final step that actually modifies
    your project. Only approved suggestions will be applied. A backup is created
    first by default for safety. The lock gate detects released QualCoder
    (3.x) only: QualCoder 4.0 builds no longer use a lock file, so 4.0
    detection is best-effort heuristics (qualcoder_gui_signals in
    get_current_project); never write while any QualCoder window has
    this project open.

    Safety guarantees:
    - Writes are refused while QualCoder has the project open (its
      heartbeat lock). If refused, ask the user to close QualCoder,
      re-check with get_current_project (`qualcoder_open` must be false),
      then retry.
    - The session must belong to the CURRENTLY OPEN project; applying a
      session to a different project is refused.
    - Every approved suggestion is re-validated BEFORE the backup and the
      write: the file must exist and be a text source, the code must exist,
      and the segment text must match the file text at the stored positions.
      If anything fails validation, nothing is written and no backup is made.
    - All codings are written in a single all-or-nothing transaction.
    - Applied suggestions are marked "applied" so the session cannot be
      double-applied by accident.
    - If the success output contains `position_safety_warning`, relay it
      to the user: the written file is position-unsafe (emoji/CRLF) and
      the codings may render shifted in QualCoder's editor.

    Args:
        coding_session_id: The session ID with approved suggestions
        create_backup: Create timestamped backup before writing (default: True)
        owner: Coder name for attribution. Default: the configured AI
               coder name (QUALCODER_MCP_AI_CODER_NAME environment
               variable, falling back to "AI Coding Assistant"). Only
               pass it when the user explicitly asks for a different
               attribution; it is validated like the configured name
               (plain single-line text, at most 80 characters, no
               '#####') and must never be the project's own coder name
               or another human coder's name.

    Returns:
        Detailed confirmation of what was written to the database

    Example:
        "Apply the approved codings to the project"
        "Write these codings to the database"
    """
    # Bridge fix: some MCP middleware strips arguments named
    # 'session_id' (reserved for its own routing); the tool
    # argument is coding_session_id, aliased for the body.
    session_id = coding_session_id
    if not session_manager.session_exists(session_id):
        return json.dumps({"error": f"Session {session_id} not found"})

    session = session_manager.load_session(session_id)

    # Writes are bound to the project the session was created in
    mismatch = _check_session_project(session)
    if mismatch is not None:
        return json.dumps(mismatch, indent=2)

    # Get only approved suggestions (check before upgrading to write mode)
    approved = session.filter_by_status("approved")

    if not approved:
        already_applied = len(session.filter_by_status("applied"))
        message = ("No approved suggestions to apply. Use "
                   "`update_suggestion_status` to approve suggestions first.")
        if already_applied:
            message = (f"No approved suggestions to apply — {already_applied} "
                       f"suggestion(s) in this session were already applied to "
                       f"the database in a previous run.")
        return json.dumps({
            "error": message,
            "statistics": session.get_statistics()
        }, indent=2)

    if owner is None:
        owner = _default_owner()
    # A tool-supplied owner obeys the same rules as the configured name,
    # so a hostile owner string is never stored (S-H3)
    try:
        owner = validate_coder_name(owner, "owner")
    except ValueError as e:
        return json.dumps({"error": str(e)})

    # Pre-validate EVERY approved suggestion on the read-only connection,
    # BEFORE upgrading and BEFORE creating a backup (SEC D-2). This catches
    # missing files/codes, non-text sources (QA F6), and position/text
    # mismatches (QA F7) without leaving backup litter or partial state.
    ro_db = get_db()
    codes_by_id = {c["id"] for c in ro_db.list_codes()}
    file_cache: Dict[int, Optional[Dict[str, Any]]] = {}
    failures = []
    for sugg in approved:
        problem = None
        if sugg.file_id not in file_cache:
            file_cache[sugg.file_id] = ro_db.get_file_content(sugg.file_id)
        file_content = file_cache[sugg.file_id]
        fulltext = (file_content or {}).get("content") or ""
        if file_content is None:
            problem = {"reason": f"file_id {sugg.file_id} does not exist"}
        elif not file_content.get("is_text") or not fulltext:
            problem = {"reason": f"file '{file_content['name']}' is not a text "
                                 f"source — text codings require text content"}
        elif sugg.code_id not in codes_by_id:
            problem = {"reason": f"code_id {sugg.code_id} does not exist"}
        elif not (isinstance(sugg.start_pos, int) and isinstance(sugg.end_pos, int)
                  and 0 <= sugg.start_pos < sugg.end_pos <= len(fulltext)):
            problem = {"reason": f"positions {sugg.start_pos}-{sugg.end_pos} are "
                                 f"out of range for the file (length {len(fulltext)})"}
        elif fulltext[sugg.start_pos:sugg.end_pos] not in (
                sugg.segment_text, sugg.segment_text.replace("\u2029", "\n")):
            problem = {
                "reason": "segment text does not match the file text at the "
                          "stored positions — re-record this suggestion with "
                          "record_suggestions (it verifies and corrects positions)",
                "expected_snippet": _snippet(fulltext[sugg.start_pos:sugg.end_pos]),
                "provided_snippet": _snippet(sugg.segment_text),
            }
        if problem is not None:
            failures.append({
                "guid": sugg.guid,
                "file_id": sugg.file_id,
                "code_name": sugg.code_name,
                **problem
            })

    if failures:
        return json.dumps({
            "error": f"{len(failures)} approved suggestion(s) failed validation — "
                     f"nothing was written and no backup was created. Fix or "
                     f"reject the listed suggestions, then apply again.",
            "failures": failures,
            "total_approved": len(approved)
        }, indent=2)

    # Refuse on pre-v14 schemas and while QualCoder has the project open
    # (heartbeat lock file — SQLite locks say nothing about an idle session)
    lock_error = _write_gate_error()
    if lock_error is not None:
        return json.dumps(lock_error)

    project_folder = _current_project_folder()

    # Upgrade to read-write mode for writing codings
    write_db = get_db(read_only=False)

    # Apply all codings in a single transaction (all-or-nothing), holding
    # QualCoder's project lock so it cannot open the project mid-write
    results = []
    backup_path = None

    try:
        with hold_project_lock(project_folder) as lock_held:
            # Create backup
            if create_backup:
                try:
                    backup_path = write_db.backup_before_write()
                except Exception as e:
                    logger.error(f"Failed to create backup: {e}")
                    _downgrade_to_readonly()
                    return json.dumps({
                        "error": "Failed to create a backup — check disk space "
                                 "and permissions. Nothing was written.",
                        "message": "Aborting to protect your data — nothing was written."
                    })

            try:
                for sugg in approved:
                    # Create memo with reasoning and confidence
                    memo = f"{sugg.reasoning}\n\n[AI Confidence: {sugg.confidence:.2f}]"

                    # Write the authoritative fulltext slice (validated above)
                    # so seltext always equals fulltext[pos0:pos1] on disk
                    slice_text = (
                        (file_cache[sugg.file_id] or {}).get("content") or ""
                    )[sugg.start_pos:sugg.end_pos]

                    ctid = write_db.add_coding(
                        file_id=sugg.file_id,
                        code_id=sugg.code_id,
                        start_pos=sugg.start_pos,
                        end_pos=sugg.end_pos,
                        selected_text=slice_text,
                        owner=owner,
                        memo=memo,
                        auto_commit=False  # Batch: commit after all succeed
                    )

                    results.append({
                        "ctid": ctid,
                        "file": sugg.file_name,
                        "code": sugg.code_name,
                        "guid": sugg.guid
                    })

                # C7: the writes above hold SQLite's reserved lock; verify
                # every touched file's text still matches what positions
                # were validated against (catches a lockless QualCoder 4.0
                # editor and any stale-lock race the gate missed)
                for fid in sorted({s.file_id for s in approved}):
                    validated_text = (file_cache[fid] or {}).get("content") or ""
                    write_db.verify_fulltext_unchanged(
                        fid, write_db.fingerprint_of_text(validated_text))
                # Close the TOCTOU window, then commit all at once
                _recheck_lock_before_commit(project_folder, lock_held)
                write_db.conn.commit()

            except Exception as e:
                # Roll back all changes on any failure
                try:
                    write_db.conn.rollback()
                except Exception:
                    pass
                logger.error(f"Failed to apply codings, rolled back: {e}")
                _downgrade_to_readonly()
                return json.dumps({
                    "error": f"Failed to apply codings (all changes rolled back): {str(e)}",
                    "applied_before_failure": len(results),
                    "total_approved": len(approved)
                })
    except DatabaseLockedError:
        # QualCoder grabbed the project between our check and the write
        _downgrade_to_readonly()
        raise

    # Downgrade back to read-only after successful write
    _downgrade_to_readonly()

    # Mark the written suggestions as applied so a re-run cannot double-apply
    session.mark_applied([r["guid"] for r in results])
    session_manager.save_session(session)

    # Re-signal position safety at the write step (track4 #6): if any file
    # just written to is position-unsafe, say so in the success output too
    unsafe_written = sorted({
        (file_cache[s.file_id] or {}).get("name", str(s.file_id))
        for s in approved
        if not db_position_safe((file_cache[s.file_id] or {}).get("content") or "")
    })

    # Format output
    output = ["\n✅ **CODINGS APPLIED TO DATABASE**\n"]

    if unsafe_written:
        output.append(
            f"position_safety_warning: file(s) {unsafe_written} contain \\r\\n "
            f"or characters beyond U+FFFF, so these codings may render "
            f"shifted or unhighlighted in QualCoder's editor (reports and "
            f"exports are unaffected). Relay this to the user.\n"
        )

    if backup_path:
        output.append(f"🔒 Backup created: `{backup_path}`\n")

    output.append(f"**Successfully Applied: {len(results)} codings**\n")

    # Group by file
    by_file = {}
    for r in results:
        if r['file'] not in by_file:
            by_file[r['file']] = []
        by_file[r['file']].append(r)

    for file_name, file_results in by_file.items():
        output.append(f"\n📄 **{file_name}**: {len(file_results)} codings")
        for r in file_results:
            output.append(f"  - {r['code']} (ctid={r['ctid']})")

    output.append(f"\n\n**You can now open the project in Qualcoder to see the AI-coded segments.**")
    output.append(f"All codings are attributed to '{owner}' with confidence scores in memos.")
    output.append(f"If one of these turns out to be wrong, `delete_coding(ctid)` removes it.")

    return "\n".join(output)


@mcp.tool()
@_tool_guard
def import_text_file(
    filename: str,
    content: str,
    memo: str = "",
    owner: Optional[str] = None,
    create_backup: bool = True,
    case_name: Optional[str] = None
) -> str:
    """Import text content as a new source file in the QualCoder project.

    Creates a new text source file in the project database, similar to
    QualCoder's "Create text file" feature. The file will be visible in
    QualCoder's file manager and available for coding.

    Optionally links the new file to an existing case (participant) in the
    same transaction — without a case link the file is invisible to every
    case-based analysis (matrices, case reports). You can also link later
    with link_file_to_case.

    IMPORTANT: Make sure you're working on a copy of your project in the
    MCP workspace (~/Documents/Qualcoder MCP Projects/)

    Refused while QualCoder has the project open (its heartbeat lock): ask the user to close the project in QualCoder, re-check with get_current_project (qualcoder_open must be false), then retry. The lock gate detects released QualCoder (3.x) only: QualCoder 4.0 builds no longer use a lock file, so 4.0 detection is best-effort heuristics (qualcoder_gui_signals in get_current_project); never write while any QualCoder window has this project open.

    Args:
        filename: Name for the new file (must include extension, e.g., "interview_04.txt")
        content: The full text content of the file
        memo: Optional memo/description for the file
        owner: Creator name for attribution. Default: the configured AI
               coder name (QUALCODER_MCP_AI_CODER_NAME environment
               variable, falling back to "AI Coding Assistant"). Only
               pass it when the user explicitly asks for a different
               attribution; it is validated like the configured name
               (plain single-line text, at most 80 characters, no
               '#####') and must never be the project's own coder name
               or another human coder's name.
        create_backup: Create timestamped backup before writing (default: True)
        case_name: Optional existing case to link the new file to
                   (matched case-insensitively)

    Returns:
        JSON with the new file's ID, name, and confirmation details
    """
    # Early validation before upgrading connection
    if not filename or not filename.strip():
        return json.dumps({"error": "filename must not be empty"})
    if not content or not content.strip():
        return json.dumps({"error": "content must not be empty"})
    if owner is None:
        owner = _default_owner()
    # A tool-supplied owner obeys the same rules as the configured name
    # (S-H3); validate_text_file_import repeats the check as defense in
    # depth
    try:
        owner = validate_coder_name(owner, "owner")
    except ValueError as e:
        return json.dumps({"error": str(e)})

    # Full validation on the read-only connection BEFORE upgrading and
    # before any backup, so rejected imports never copy the whole project
    # (SEC D-2). Also rejects control-char/NUL filenames (SEC D-1).
    try:
        get_db().validate_text_file_import(
            name=filename.strip(), content=content, owner=owner, memo=memo
        )
    except (ValueError, TypeError) as e:
        return json.dumps({"error": str(e)})

    # Resolve the target case (if any) before upgrading — an unknown case
    # must not cost a backup copy
    case = None
    if case_name is not None:
        cases = get_db().list_cases()
        case = next(
            (c for c in cases if c["name"].lower() == str(case_name).lower()),
            None
        )
        if case is None:
            return json.dumps({
                "error": f"Case '{case_name}' not found",
                "available_cases": sorted(c["name"] for c in cases)[:50]
            })

    # Refuse on pre-v14 schemas and while QualCoder has the project open
    lock_error = _write_gate_error()
    if lock_error is not None:
        return json.dumps(lock_error)

    project_folder = _current_project_folder()

    # Upgrade to read-write mode
    write_db = get_db(read_only=False)

    # SEC C-1: this tool keeps its bespoke error contract (TypeError and a
    # "Database error:" prefix on RuntimeError, and a two-write transaction),
    # so it is NOT migrated to _perform_write — but it now carries the
    # IDENTICAL finally-block discipline: on EVERY exit path, roll back an
    # uncommitted transaction and downgrade to read-only. A commit-time
    # sqlite3.Error (disk-full/IO/BUSY) is caught by none of the inner
    # handlers below; previously it skipped the trailing downgrade and left
    # the global connection read-write with a pending transaction (the M-1
    # class). The finally (with the committed-flag guard) closes that.
    backup_path = None
    committed = False
    result = None
    case_link = None
    try:
        with hold_project_lock(project_folder) as lock_held:
            # Create backup
            if create_backup:
                try:
                    backup_path = write_db.backup_before_write()
                except Exception as e:
                    logger.error(f"Failed to create backup: {e}")
                    return json.dumps({
                        "error": "Failed to create a backup — check disk space "
                                 "and permissions. Nothing was written.",
                        "message": "Aborting to protect your data."
                    })

            # Perform the import (the database layer re-validates: defense
            # in depth); commit only after re-checking the QualCoder lock
            try:
                result = write_db.import_text_file(
                    name=filename.strip(),
                    content=content,
                    owner=owner,
                    memo=memo,
                    auto_commit=False
                )
                if case is not None:
                    case_link = write_db.link_file_to_case(
                        case_id=case["id"],
                        file_id=result["id"],
                        owner=owner,
                        auto_commit=False
                    )
                _recheck_lock_before_commit(project_folder, lock_held)
                write_db.conn.commit()
                committed = True
            except DatabaseLockedError:
                raise
            except (ValueError, TypeError) as e:
                return json.dumps({"error": str(e)})
            except RuntimeError as e:
                return json.dumps({"error": f"Database error: {str(e)}"})
    finally:
        # Unconditional cleanup on EVERY exit path (SEC M-1 / C-1): roll back
        # anything still in flight (skipped after a successful commit by the
        # committed guard), then always return the connection to read-only.
        if not committed:
            try:
                if write_db.conn is not None and write_db.conn.in_transaction:
                    write_db.conn.rollback()
            except Exception:
                pass
        _downgrade_to_readonly()

    # Format success response
    output = {
        "success": True,
        "message": f"Successfully imported '{result['name']}' as a new source file",
        "file_id": result["id"],
        "file_name": result["name"],
        "content_length": result["content_length"],
        "owner": result["owner"],
        "date": result["date"],
        "attributes_created": result["attributes_created"]
    }
    if case_link is not None:
        output["linked_to_case"] = case_link
    if backup_path:
        output["backup_path"] = str(backup_path)

    return json.dumps(output, indent=2)


@mcp.tool()
@_tool_guard
def link_file_to_case(
    file_id: int,
    case_id: Optional[int] = None,
    case_name: Optional[str] = None,
    create_backup: bool = True
) -> str:
    """Link a source file to a case so it appears in case-based analyses.

    THIS WRITES TO THE DATABASE. Creates the whole-file case_text link that
    QualCoder's own "Case file manager" would create — without it, a file
    is invisible to get_codes_by_case, get_case_code_matrix, case reports
    and every other case-based analysis. Files imported with
    import_text_file are NOT linked to any case by default.

    Refused while QualCoder has the project open (its heartbeat lock): ask the user to close the project in QualCoder, re-check with get_current_project (qualcoder_open must be false), then retry. The lock gate detects released QualCoder (3.x) only: QualCoder 4.0 builds no longer use a lock file, so 4.0 detection is best-effort heuristics (qualcoder_gui_signals in get_current_project); never write while any QualCoder window has this project open.

    Args:
        file_id: The source file to link
        case_id: The case to link to (or use case_name)
        case_name: Case name, matched case-insensitively (or use case_id)
        create_backup: Create timestamped backup before writing (default: True)

    Returns:
        JSON with the created link (case, file, covered span)

    Example:
        "Link interview_dana.txt to the case Dana"
    """
    ro_db = get_db()

    # Resolve the case
    if case_id is None and case_name is None:
        return json.dumps({"error": "Provide case_id or case_name"})
    cases = ro_db.list_cases()
    if case_id is not None:
        case = next((c for c in cases if c["id"] == case_id), None)
        if case is None:
            return json.dumps({"error": f"Case ID {case_id} does not exist"})
    else:
        case = next(
            (c for c in cases if c["name"].lower() == str(case_name).lower()),
            None
        )
        if case is None:
            return json.dumps({
                "error": f"Case '{case_name}' not found",
                "available_cases": sorted(c["name"] for c in cases)[:50]
            })

    # Validate the file on the read-only connection
    if ro_db.get_file_content(file_id) is None:
        return json.dumps({"error": f"File ID {file_id} does not exist"})

    # SEC C-1: route through _perform_write for the same finally-block
    # rollback+downgrade guarantee as the newer tools (covers a commit-time
    # sqlite3.Error, the M-1 class this tool previously missed). Its inner
    # handler already matched the helper exactly.
    def _op(write_db):
        link = write_db.link_file_to_case(
            case_id=case["id"],
            file_id=file_id,
            owner=_default_owner(),
            auto_commit=False
        )
        return {
            "success": True,
            "message": f"Linked '{link['file_name']}' to case "
                       f"'{link['case_name']}'",
            "link": link,
        }

    result = _perform_write(_op, create_backup=create_backup,
                            backup_fail_detail="nothing was linked")
    return json.dumps(result, indent=2)


# ============================================================================
# ERROR-RECOVERY TOOLS — delete a coding, list and restore backups
# ============================================================================

@mcp.tool()
@_tool_guard
def delete_coding(coding_id: int, create_backup: bool = True,
                  allow_hidden_coder: bool = False,
                  confirm_private_note_deletion: bool = False) -> str:
    """Delete a single coded segment from the project database.

    THIS WRITES TO THE DATABASE. Use it to remove a coding that was applied
    by mistake (e.g. an approved AI suggestion that turned out to be wrong).
    It removes ONE coding — the assignment of a code to a text span — never
    the code itself, the source file, or any other coding.

    A backup is created first by default, so the deletion can be undone with
    restore_backup if needed. Refused while QualCoder has the project open (its heartbeat lock): ask the user to close the project in QualCoder, re-check with get_current_project (qualcoder_open must be false), then retry. The lock gate detects released QualCoder (3.x) only: QualCoder 4.0 builds no longer use a lock file, so 4.0 detection is best-effort heuristics (qualcoder_gui_signals in get_current_project); never write while any QualCoder window has this project open.

    Two guards, each with an explicit override the user must ask for:
    - Hidden coder (QualCoder 4.0 projects that hide coders): a coding
      owned by a hidden coder is REFUSED unless allow_hidden_coder=true.
      The refusal names neither the coder nor how many are hidden. With
      the override the echo carries ids only (coding_id, code_id,
      file_id) plus a coder_visibility note.
    - Private note (any project): a coding whose memo carries a '#####'
      private section the assistant cannot see is REFUSED unless
      confirm_private_note_deletion=true, and a backup is ALWAYS taken
      for such a row even with create_backup=false. Note that this
      refusal, or the forced backup, tells you that a private note
      exists on the row (never its content); the owner accepts that.
    When both apply, both overrides are required and both refusals come
    back in one response.

    Args:
        coding_id: The ctid of the coding to delete. You can find ctids in
                   the output of apply_codings, get_coded_segments, or
                   analyze_file_with_coding (segment_id).
        create_backup: Create timestamped backup before deleting (default:
                   True; ignored, always on, for a row carrying a private note)
        allow_hidden_coder: Override to delete a hidden coder's coding
        confirm_private_note_deletion: Override to delete a coding whose
                   memo carries a private note

    Returns:
        JSON with the deleted coding's details (code, file, positions, text)
        and the backup path, or ids only for a hidden coder's row; hidden
        coders' names and coding decisions never enter the conversation.

    Example:
        "Delete coding 42 — that segment was coded wrongly"
    """
    # Validate on the read-only connection BEFORE upgrading/backup: the
    # row must exist and both guards must pass (or be overridden)
    refusal = _refuse_existing_row_change(
        "coding", coding_id, allow_hidden_coder=allow_hidden_coder,
        deleting=True,
        confirm_private_note_deletion=confirm_private_note_deletion)
    if refusal is not None:
        return json.dumps(refusal, indent=2)
    status = get_db().existing_row_status("coding", coding_id) or {}

    # SEC C-1: route through _perform_write so the unconditional
    # rollback-if-uncommitted + downgrade discipline (its finally block)
    # covers a commit-time sqlite3.Error too — the M-1 class this tool
    # previously missed. Its inner handler was already exactly
    # (ValueError, RuntimeError) -> {"error": str(e)}, matching the helper.
    def _op(write_db):
        deleted = write_db.delete_coding(
            coding_id, auto_commit=False,
            allow_hidden_coder=allow_hidden_coder,
            confirm_private_note_deletion=confirm_private_note_deletion)
        if deleted.get("hidden_coder_row"):
            # Hidden coder's row (QC 4.0 visibility): ids only, never the
            # code or file name (S-MAJ; upstream echoes ids only too)
            return {
                "success": True,
                "message": f"Deleted coding {coding_id}",
                "deleted_coding": deleted,
            }
        return {
            "success": True,
            "message": f"Deleted coding {coding_id} "
                       f"('{deleted['code_name']}' on '{deleted['file_name']}')",
            "deleted_coding": deleted,
        }

    # S-P2 (a): a row carrying a private note is always backed up first
    result = _perform_write(
        _op, create_backup=create_backup or bool(status.get("private_note")),
        backup_fail_detail="nothing was deleted")
    _private_note_backup_note(result, status, create_backup)
    _attach_hidden_target_note(result, "deleted_coding")
    return _ai_json(result, indent=2)


@mcp.tool()
@_tool_guard
def list_backups() -> str:
    """List the automatic backups of the currently open project.

    Every write operation (apply_codings, import_text_file, delete_coding,
    restore_backup) creates a timestamped backup folder next to the project,
    named '<project>_backup_<timestamp>.qda'. This tool lists them, newest
    first, so you can pick one for restore_backup.

    Backups carry the whole project tree, ai_data/ included (QualCoder
    4.0's AI prompt library and chat history are non-regenerable user
    data), but exclude the regenerable vector-search database
    ai_data/search.sqlite (which duplicates every text source in
    plaintext) and sqlite sidecar files, exactly like QualCoder's own
    backups; QualCoder rebuilds search.sqlite on project open. Unlike
    QualCoder's backups, symlinks inside the project that point outside
    the project folder (or dangle) are not followed: they are skipped and
    the write result reports them (backup_skipped_symlinks), so a shared
    or untrusted project folder cannot pull outside files into a backup.

    Returns:
        JSON with the project name and an array of backups
        (name, path, created, size_mb)
    """
    if current_project_path is None:
        return json.dumps({
            "error": "No Qualcoder project selected. Use 'list_available_projects' "
                     "and 'select_project' to open one." + _mru_hint()
        })

    project_folder = validate_qda_path(current_project_path).parent
    backups = _collect_backups(project_folder)

    return json.dumps({
        "project": project_folder.stem,
        "backup_count": len(backups),
        "backups": backups,
        "notes": [
            "kind='qualcoder' backups are made by QualCoder itself on "
            "project open; they may exclude audio/video files and QualCoder "
            "deletes them again when a session made no changes.",
            "QualCoder 3.8.0 through 3.8.2 may also store backups in the "
            "QualCoder settings 'directory' (not listed here); newer "
            "QualCoder builds write _BKUP_ backups next to the project "
            "again, and those ARE listed with kind='qualcoder'.",
            "MCP backups (kind='mcp') accumulate until pruned — use "
            "prune_backups(keep_last=..., older_than_days=...) to reclaim "
            "disk space (retention never touches QualCoder's own backups).",
            "Backups include the whole project tree, ai_data/ included "
            "(QualCoder 4.0's AI prompt library and chat history are "
            "non-regenerable user data), but exclude the regenerable "
            "vector-search database ai_data/search.sqlite and sqlite "
            "sidecar files, exactly like QualCoder's own backups. "
            "QualCoder rebuilds search.sqlite when the project is opened."
        ],
        "hint": "Use restore_backup(backup_path) to roll the project back "
                "to one of these snapshots."
    }, indent=2)


def _collect_backups(project_folder: Path) -> List[Dict[str, Any]]:
    """Collect both backup families next to the project, newest first.

    Families: this server's {name}_backup_{ts}[...].qda (kind 'mcp' —
    including the *_prerestore safety copies) and QualCoder's own
    {name}_BKUP_{...}.qda (kind 'qualcoder'). Each entry carries name,
    path, kind, created, age_days and size_mb.
    """
    backups: List[Dict[str, Any]] = []
    now = datetime.now()
    for prefix, kind in ((f"{project_folder.stem}_backup_", "mcp"),
                         (f"{project_folder.stem}_BKUP_", "qualcoder")):
        for entry in project_folder.parent.glob(f"{prefix}*.qda"):
            if not entry.is_dir():
                continue
            try:
                size_bytes = sum(
                    f.stat().st_size for f in entry.rglob("*") if f.is_file()
                )
                created = datetime.fromtimestamp(entry.stat().st_mtime)
                backups.append({
                    "name": entry.name,
                    "path": str(entry),
                    "kind": kind,
                    "created": created.strftime("%Y-%m-%d %H:%M:%S"),
                    "age_days": round(
                        max(0.0, (now - created).total_seconds()) / 86400, 1),
                    "size_mb": round(size_bytes / (1024 * 1024), 2),
                })
            except OSError as e:
                logger.debug(f"Cannot stat backup {entry}: {e}")
                continue

    backups.sort(key=lambda b: b["created"], reverse=True)
    return backups


@mcp.tool()
@_tool_guard
def prune_backups(keep_last: Optional[int] = None,
                  older_than_days: Optional[float] = None,
                  confirm: bool = False) -> str:
    """Delete this project's own backup snapshots to reclaim disk space.

    DESTRUCTIVE to your recovery points — preview first, then confirm.
    Every write creates a full-project backup copy and they accumulate
    forever; this tool prunes them by a retention policy you choose:

    - keep_last=N: keep only the N newest MCP backups
    - older_than_days=D: remove MCP backups older than D days
    - both: a backup is removed only if it fails BOTH criteria (beyond the
      newest N AND older than D days) — the conservative intersection

    Safety rules:
    - ONLY this server's backups are touched (the {project}_backup_*
      family, including *_prerestore safety copies). QualCoder's own
      _BKUP_ backups are NEVER removed.
    - At least the newest MCP backup is always kept, unless you
      explicitly pass keep_last=0.
    - Call once without confirm to see exactly which folders would be
      removed and how much space is reclaimed; then call again with
      confirm=true.

    This does not touch the live project database, so it works even while
    QualCoder has the project open. Each backup is a whole project tree,
    ai_data/ included (minus the regenerable search.sqlite and sqlite
    sidecars, as in QualCoder's own backups), so pruning also removes
    those recovery points for the AI prompt library and chat history.

    Args:
        keep_last: Keep only this many newest MCP backups (0 allowed, but
                   must be explicit)
        older_than_days: Remove MCP backups older than this many days
        confirm: Must be true to actually delete (default: preview only)

    Returns:
        JSON preview (requires_confirmation) or the removal result
    """
    if current_project_path is None:
        return json.dumps({
            "error": "No Qualcoder project selected. Use 'list_available_projects' "
                     "and 'select_project' to open one." + _mru_hint()
        })

    if keep_last is None and older_than_days is None:
        return json.dumps({
            "error": "Provide a retention policy: keep_last and/or "
                     "older_than_days. Refusing a policy-less prune."
        })
    if keep_last is not None and (
            not isinstance(keep_last, int) or isinstance(keep_last, bool)
            or keep_last < 0):
        return json.dumps({"error": "keep_last must be a non-negative integer"})
    if older_than_days is not None and (
            not isinstance(older_than_days, (int, float))
            or isinstance(older_than_days, bool) or older_than_days < 0):
        return json.dumps({"error": "older_than_days must be a non-negative number"})

    project_folder = validate_qda_path(current_project_path).parent
    mcp_backups = [b for b in _collect_backups(project_folder)
                   if b["kind"] == "mcp"]  # newest first; _BKUP_ never touched

    # Apply the policy. With both criteria, a backup is pruned only if it
    # fails BOTH (conservative intersection).
    to_remove = []
    for index, backup in enumerate(mcp_backups):
        beyond_keep = keep_last is not None and index >= keep_last
        too_old = (older_than_days is not None
                   and backup["age_days"] > older_than_days)
        if keep_last is not None and older_than_days is not None:
            prune = beyond_keep and too_old
        else:
            prune = beyond_keep or too_old
        if prune:
            to_remove.append(backup)

    # Floor: always keep the newest MCP backup unless keep_last=0 explicit
    if (mcp_backups and keep_last != 0
            and any(b["name"] == mcp_backups[0]["name"] for b in to_remove)):
        to_remove = [b for b in to_remove
                     if b["name"] != mcp_backups[0]["name"]]

    kept = [b for b in mcp_backups
            if not any(r["name"] == b["name"] for r in to_remove)]
    reclaimed_mb = round(sum(b["size_mb"] for b in to_remove), 2)

    notes = []
    prerestore_removed = [b for b in to_remove if "_prerestore" in b["name"]]
    if prerestore_removed:
        newest_prerestore = next(
            (b for b in mcp_backups if "_prerestore" in b["name"]), None)
        if newest_prerestore and any(
                b["name"] == newest_prerestore["name"]
                for b in prerestore_removed):
            notes.append(
                "This removes your most recent pre-restore safety snapshot — "
                "the state saved just before the last restore_backup."
            )

    if not to_remove:
        return json.dumps({
            "success": True,
            "message": "Nothing to prune — every MCP backup satisfies the "
                       "retention policy.",
            "kept_count": len(kept),
        }, indent=2)

    if not confirm:
        preview = {
            "requires_confirmation": True,
            "would_remove": [
                {"name": b["name"], "age_days": b["age_days"],
                 "size_mb": b["size_mb"]} for b in to_remove],
            "would_keep": [b["name"] for b in kept],
            "reclaimed_mb": reclaimed_mb,
            "hint": "Call prune_backups again with confirm=true to delete "
                    "these backup folders. QualCoder's own _BKUP_ backups "
                    "are never touched.",
        }
        if notes:
            preview["notes"] = notes
        return json.dumps(preview, indent=2)

    removed, failed = [], []
    for backup in to_remove:
        try:
            shutil.rmtree(backup["path"])
            removed.append(backup["name"])
        except OSError as e:
            logger.error(f"Failed to remove backup {backup['name']}: {e}")
            failed.append(backup["name"])

    result: Dict[str, Any] = {
        "success": not failed,
        "removed": removed,
        "reclaimed_mb": round(sum(b["size_mb"] for b in to_remove
                                  if b["name"] in removed), 2),
        "kept_count": len(kept),
    }
    if failed:
        result["failed_to_remove"] = failed
        result["error"] = ("Some backup folders could not be removed — "
                           "check permissions.")
    if notes:
        result["notes"] = notes
    return json.dumps(result, indent=2)


def _project_is_write_locked(data_qda: Path) -> bool:
    """Probe whether another process holds a write lock on the database."""
    conn = None
    try:
        conn = sqlite3.connect(str(data_qda), timeout=0.5)
        conn.execute("BEGIN IMMEDIATE")
        conn.rollback()
        return False
    except sqlite3.OperationalError:
        return True
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


@mcp.tool()
@_tool_guard
def restore_backup(backup_path: str, confirm: bool = False) -> str:
    """Restore the currently open project from one of its backups.

    THIS REPLACES THE CURRENT PROJECT STATE with the chosen backup snapshot.
    Everything done since that backup is removed from the project — which is
    why this tool:
    1. does nothing until called with confirm=true (the default call returns
       a preview of what would happen),
    2. only accepts backups of the currently open project (created by this
       server, sitting next to the project folder),
    3. creates a safety backup of the CURRENT state first, so even a restore
       can be undone,
    4. refuses to run while QualCoder has the project open (its heartbeat
       lock) or another process holds an SQLite write lock. The lock gate
       detects released QualCoder (3.x) only: QualCoder 4.0 builds no
       longer use a lock file, so 4.0 detection is best-effort heuristics
       (qualcoder_gui_signals, reported in this tool's own preview and in
       get_current_project); never restore while any QualCoder window has
       this project open.

    Note: backups deliberately omit ai_data/search.sqlite (the
    regenerable AI search index, QualCoder-parity exclusion), so a
    restored project not having one is normal, never corruption:
    QualCoder 4.0 rebuilds it on project open. The rest of ai_data/
    (prompt library, chat history) restores with the project. Backups
    made by this server also skip symlinks that point outside the
    project folder (or dangle), so such entries are absent from a
    restored project; the safety backup taken before a restore follows
    the same rule.

    Args:
        backup_path: Path to the backup folder (from list_backups)
        confirm: Must be true to actually restore. When false (default),
                 returns a preview and makes no changes.

    Returns:
        JSON describing the restore (or the preview when confirm is false)

    Example:
        "Restore the project from the backup made this morning"
    """
    if current_project_path is None:
        return json.dumps({
            "error": "No Qualcoder project selected. Use 'list_available_projects' "
                     "and 'select_project' to open one." + _mru_hint()
        })

    project_data = validate_qda_path(current_project_path)
    project_folder = project_data.parent
    # Both families are restorable: ours and QualCoder's own _BKUP_ copies
    prefixes = (f"{project_folder.stem}_backup_", f"{project_folder.stem}_BKUP_")

    # The backup must be a sibling backup of the CURRENT project
    try:
        backup_folder = Path(backup_path).expanduser().resolve(strict=True)
    except OSError:
        return json.dumps({"error": "Backup path not found. Use list_backups "
                                    "to see the available backups."})
    if (not backup_folder.is_dir()
            or backup_folder.parent != project_folder.parent
            or not backup_folder.name.startswith(prefixes)
            or backup_folder.suffix.lower() != ".qda"):
        return json.dumps({
            "error": "Not a backup of the currently open project. Only backups "
                     "created next to this project (see list_backups) can be "
                     "restored."
        })

    # The backup itself must be a valid QualCoder project
    validate_qda_path(str(backup_folder))

    if not confirm:
        preview = {
            "requires_confirmation": True,
            "would_restore_from": backup_folder.name,
            "would_overwrite": project_folder.name,
            "safety": "A safety backup of the current state will be created "
                      "first, so the restore itself can be undone.",
            "hint": "Call restore_backup again with confirm=true to proceed."
        }
        if "_BKUP_" in backup_folder.name:
            preview["note"] = (
                "This is a QualCoder-made backup: depending on QualCoder's "
                "settings it may not contain audio/video media files."
            )
        # P1-5: the preview is this tool's own ask rung. WARN-level only:
        # report the 4.0 GUI-open heuristics and ask, never refuse on
        # them (QA round 1, F18)
        signals = qualcoder_gui_signals(project_folder)
        preview["qualcoder_gui_signals"] = signals
        if signals:
            preview["qualcoder_gui_hint"] = (
                "This project APPEARS to be open in QualCoder ("
                + "; ".join(signals) + "). That is a heuristic (QualCoder "
                "4.0 writes no lock file), so ASK THE USER whether a "
                "QualCoder window has this project open before confirming: "
                "a restore replaces the project folder that window has "
                "open, and the window will not display the restored state "
                "until the project is reopened."
            )
        return json.dumps(preview, indent=2)

    # Refuse while QualCoder has the project open (heartbeat lock file)
    lock_error = _qualcoder_open_error()
    if lock_error is not None:
        return json.dumps(lock_error)

    # Refuse while another process holds an SQLite write lock
    if _project_is_write_locked(project_data):
        return json.dumps({"error": DB_LOCKED_MESSAGE})

    # Safety backup of the current state (rename to mark it as pre-restore)
    safety_backup = backup_project(project_folder)
    marked = safety_backup.with_name(
        safety_backup.name[:-len(".qda")] + "_prerestore.qda"
    )
    try:
        safety_backup.rename(marked)
        safety_backup = marked
    except OSError:
        pass  # keep the unmarked name if rename fails

    # Close the connection, swap the folder, reopen read-only
    global db
    if db is not None:
        try:
            db.close()
        except Exception:
            pass
        db = None

    try:
        with hold_project_lock(project_folder):
            shutil.rmtree(project_folder)
            shutil.copytree(backup_folder, project_folder)
            # Old backups may contain a copied lock file; QualCoder never
            # puts lock files in backups and neither do we (anymore)
            for stray_lock in project_folder.glob("*.lock"):
                try:
                    stray_lock.unlink()
                except OSError:
                    pass
    except DatabaseLockedError:
        # QualCoder opened the project between the check and the swap
        switch_project(current_project_path)
        raise
    except Exception as e:
        # Attempt recovery from the safety backup. A copytree that fails
        # PARTWAY (e.g. disk full) leaves a PARTIAL project folder — the
        # backup's data.qda without the rest — which the previous
        # exists()-guard mistook for "project still there", leaving a live
        # half-replaced project that the next read tool silently reconnects
        # to (fault-injection D1). Remove any partial folder first so the
        # safety-backup recovery always runs on a clean slate.
        logger.error(f"Restore failed mid-swap: {e}")
        try:
            if project_folder.exists():
                # The original folder was already rmtree'd inside the swap;
                # anything here now is a partial copy — never the original
                shutil.rmtree(project_folder)
            shutil.copytree(safety_backup, project_folder)
            switch_project(current_project_path)
            return json.dumps({
                "error": "Restore failed, but the project was recovered "
                         "from the safety backup — nothing was lost.",
                "safety_backup": str(safety_backup)
            })
        except Exception as recovery_error:
            logger.error(f"Recovery also failed: {recovery_error}")
        return json.dumps({
            "error": "Restore failed. The pre-restore state is preserved in "
                     "the safety backup — copy it back over the project folder "
                     "to recover.",
            "safety_backup": str(safety_backup)
        })

    switch_project(current_project_path)

    return json.dumps({
        "success": True,
        "message": f"Project '{project_folder.stem}' restored from "
                   f"'{backup_folder.name}'",
        "restored_from": str(backup_folder),
        "safety_backup": str(safety_backup),
        "hint": "The pre-restore state is kept in the safety backup in case "
                "you change your mind."
    }, indent=2)


@mcp.tool()
@_tool_guard
def get_coding_session_info(coding_session_id: str) -> str:
    """Get detailed information about a coding session.

    Shows all the suggestions, statistics, and metadata for a session.
    Useful for reviewing what was suggested before exporting.

    Args:
        coding_session_id: The session ID to query

    Returns:
        JSON with complete session details including all suggestions

    Example:
        "Show me session abc123"
        "What's in coding session xyz789?"
    """
    # Bridge fix: some MCP middleware strips arguments named
    # 'session_id' (reserved for its own routing); the tool
    # argument is coding_session_id, aliased for the body.
    session_id = coding_session_id
    try:
        # Load session
        if not session_manager.session_exists(session_id):
            return json.dumps({
                "error": f"Session {session_id} not found",
                "available_sessions": session_manager.list_sessions()
            })

        session = session_manager.load_session(session_id)

        # Return full session data. The on-disk format keeps its
        # "session_id" key (internal schema unchanged); the API-facing
        # primary key is coding_session_id.
        payload = {"coding_session_id": session.session_id}
        payload.update(session.to_dict())
        return json.dumps(payload, indent=2)

    except Exception as e:
        logger.error(f"Error in get_coding_session_info: {e}")
        return json.dumps({"error": str(e)})


@mcp.tool()
@_tool_guard
def list_coding_sessions(
    project_path: Optional[str] = None,
    days_old: int = 30
) -> str:
    """List all saved AI coding sessions.

    Shows all coding sessions, optionally filtered by project and age.
    Useful for finding previous coding sessions to review or export.

    Args:
        project_path: Filter by specific project path (optional)
        days_old: Only show sessions from last N days (default: 30)

    Returns:
        JSON with list of sessions and their metadata

    Example:
        "List all my coding sessions"
        "Show coding sessions from the last 7 days"
        "List sessions for this project"
    """
    try:
        sessions = session_manager.list_sessions(project_path, days_old)

        if not sessions:
            return json.dumps({
                "sessions": [],
                "message": "No coding sessions found",
                "filters": {
                    "project_path": project_path,
                    "days_old": days_old
                }
            }, indent=2)

        for entry in sessions:
            if isinstance(entry, dict) and "session_id" in entry:
                entry["coding_session_id"] = entry["session_id"]
        return json.dumps({
            "session_count": len(sessions),
            "sessions": sessions
        }, indent=2)

    except Exception as e:
        logger.error(f"Error in list_coding_sessions: {e}")
        return json.dumps({"error": str(e)})


@mcp.tool()
@_tool_guard
def delete_coding_session(coding_session_id: str) -> str:
    """Delete a saved coding session.

    Permanently removes a session file from disk. Use with caution!

    Args:
        coding_session_id: The session ID to delete

    Returns:
        JSON with success status

    Example:
        "Delete session abc123"
    """
    # Bridge fix: some MCP middleware strips arguments named
    # 'session_id' (reserved for its own routing); the tool
    # argument is coding_session_id, aliased for the body.
    session_id = coding_session_id
    try:
        deleted = session_manager.delete_session(session_id)

        if deleted:
            return json.dumps({
                "success": True,
                "message": f"Session {session_id} deleted",
                "coding_session_id": session_id,
                "session_id": session_id  # deprecated duplicate of coding_session_id (kept one release)
            }, indent=2)
        else:
            return json.dumps({
                "success": False,
                "error": f"Session {session_id} not found"
            }, indent=2)

    except Exception as e:
        logger.error(f"Error in delete_coding_session: {e}")
        return json.dumps({"error": str(e)})


@mcp.tool()
@_tool_guard
def cleanup_old_sessions(days_old: int = 30) -> str:
    """Clean up old coding sessions.

    Deletes sessions older than specified days to free up disk space.

    Args:
        days_old: Delete sessions older than N days (default: 30)

    Returns:
        JSON with count of deleted sessions

    Example:
        "Clean up sessions older than 30 days"
        "Delete coding sessions older than 60 days"
    """
    try:
        if not isinstance(days_old, int) or days_old < 1:
            return json.dumps({
                "error": "days_old must be a positive integer (>= 1) — "
                         "refusing to delete recent or all sessions. To remove "
                         "a specific session use delete_coding_session."
            })

        deleted_count = session_manager.cleanup_old_sessions(days_old)

        return json.dumps({
            "success": True,
            "deleted_count": deleted_count,
            "days_old": days_old,
            "message": f"Deleted {deleted_count} sessions older than {days_old} days"
        }, indent=2)

    except Exception as e:
        logger.error(f"Error in cleanup_old_sessions: {e}")
        return json.dumps({"error": str(e)})



@mcp.tool()
@_tool_guard
def explain_ai_coding_tools(tool_name: Optional[str] = None) -> str:
    """Get help and examples for AI coding tools.

    This tool provides comprehensive documentation and examples for all
    AI-assisted coding features. It's your guide to using Claude to
    help code your qualitative data.

    Args:
        tool_name: Specific tool to explain (optional)
                  If None, returns overview of all tools

    Returns:
        JSON with tool documentation, examples, and tips

    Example usage:
        "Explain the AI coding tools"
        "How do I use analyze_for_coding?"
        "What's the workflow for AI coding?"
    """
    # Comprehensive help documentation
    tool_help = {
        "overview": {
            "title": "AI-Assisted Coding for Qualcoder",
            "description": "Use Claude to help code your qualitative data. Claude can analyze interview transcripts, suggest codes, and create coded segments that you can review and apply directly to your Qualcoder project.",
            "workflow": {
                "step_1": "Create an analysis session (analyze_for_coding)",
                "step_2": "Claude reads the files and records its suggestions "
                          "(record_suggestions - each one is verified against "
                          "the file text)",
                "step_3": "Review suggestions (review_suggestions)",
                "step_4": "Approve or reject suggestions (update_suggestion_status)",
                "step_5": "Apply approved codings to database (apply_codings - "
                          "bound to the session's project, all-or-nothing, "
                          "automatic backup)",
                "step_6": "Recover if needed: delete_coding removes a single "
                          "coding; list_backups + restore_backup roll the "
                          "whole project back"
            },
            "key_features": [
                "Analyze complete transcripts with full context",
                "Suggest coded segments with confidence scores",
                "Every suggestion verified against the file text before storage",
                "Review and approve/reject suggestions before applying",
                "Apply codings directly to Qualcoder database (with automatic backup)",
                "Writes refuse to run while QualCoder has the project open",
                "Session persistence - resume work anytime",
                "Full recovery tools: delete_coding, list_backups, restore_backup"
            ]
        },
        "analyze_for_coding": {
            "purpose": "Main AI coding tool - analyzes files and suggests coded segments",
            "when_to_use": "When you want to automatically code interview transcripts or documents",
            "parameters": {
                "file_ids": "List of file IDs to code (required)",
                "code_names": "Specific codes to apply, or None for all codes",
                "instruction": "Guidance for the AI",
                "min_confidence": "Minimum confidence threshold (0.0-1.0)"
            },
            "examples": [
                {"prompt": "Code files 1, 2, and 3", "explanation": "Codes 3 files with all available codes"},
                {"prompt": "Code interview transcripts with 'workplace stress' codes", "explanation": "Filters to stress-related codes only"},
                {"prompt": "Analyze file 5 for themes about motivation", "explanation": "Focuses AI on specific theme"}
            ],
            "tips": [
                "Be specific in your instruction for better results",
                "Start with one file to test before batch coding",
                "Use min_confidence to filter low-quality suggestions",
                "Save the session id and pass it to every follow-up tool as coding_session_id"
            ]
        },
        "apply_codings": {
            "purpose": "Apply approved coding suggestions directly to the Qualcoder database",
            "when_to_use": "After reviewing suggestions and approving the ones you want",
            "workflow": [
                "1. Run analyze_for_coding on your files",
                "2. Record the suggestions with record_suggestions",
                "3. Review with review_suggestions",
                "4. Adjust spans/codes in place with edit_suggestion",
                "5. Approve/reject with update_suggestion_status",
                "6. Apply approved codings with apply_codings",
                "7. A backup is created automatically before writing; "
                "delete_coding / restore_backup undo mistakes"
            ]
        },
        "edit_suggestion": {
            "purpose": "Adjust a PENDING suggestion's span (extend/shrink/"
                       "move) and/or its code during review, before approval",
            "when_to_use": "When the researcher wants a wider quote, a "
                           "tighter span, or a different code on a "
                           "suggestion — instead of rejecting and "
                           "re-recording",
            "notes": [
                "Pending suggestions only: applied ones are immutable, "
                "approved/rejected ones reflect a decision already made",
                "use_alternative='shorter'|'longer' applies a ready-made "
                "span the server computed (core sentence / enclosing "
                "paragraph) — the one-call answer to 'make it "
                "shorter/longer'; alternatives are recomputed after every "
                "edit",
                "New spans are re-verified against the file text with the "
                "same machinery as record_suggestions",
                "Proposal evidence spans are edited the same way via "
                "update_proposal(example_segments=...)"
            ]
        },
        "coding_style_guidance": {
            "purpose": "How to calibrate span length and multi-coding "
                       "(learned from real researcher use)",
            "span_style": [
                "Prefer complete-thought spans: a full sentence or small "
                "paragraph that stands alone as a quotable extract — not "
                "a minimal phrase",
                "Set the style once per session via analyze_for_coding's "
                "instruction parameter, e.g. instruction='code generous "
                "spans, full paragraphs' — then honor it in every "
                "suggestion",
                "Researchers can widen or narrow any span at review time "
                "with edit_suggestion; every suggestion carries "
                "server-computed shorter/longer alternatives applied in "
                "one call with use_alternative",
                "Present alternatives compactly (one line, lengths only) "
                "and proactively only after the researcher has adjusted "
                "spans in this session — avoid decision fatigue"
            ],
            "co_coding": [
                "Actively consider MULTIPLE codes per segment — record one "
                "suggestion per code on the same span; this is normal "
                "qualitative practice and the schema supports it",
                "When the researcher adds a second code to a fragment "
                "during review, treat it as a calibration signal for "
                "subsequent suggestions"
            ]
        }
    }

    if tool_name is None:
        # Return overview
        return json.dumps(tool_help["overview"], indent=2)

    elif tool_name in tool_help:
        # Return specific tool help
        return json.dumps(tool_help[tool_name], indent=2)

    else:
        # Unknown tool
        return json.dumps({
            "error": f"Unknown tool: {tool_name}",
            "available_tools": [
                "analyze_for_coding",
                "record_suggestions",
                "review_suggestions",
                "edit_suggestion",
                "coding_style_guidance",
                "update_suggestion_status",
                "apply_codings",
                "delete_coding",
                "list_backups",
                "restore_backup",
                "export_refi_qda",
                "copy_project_to_workspace",
                "import_text_file",
                "link_file_to_case",
                "get_coding_session_info",
                "list_coding_sessions",
                "delete_coding_session",
                "cleanup_old_sessions",
                "set_memo",
                "add_journal_entry",
                "create_code",
                "rename_code",
                "recolor_code",
                "move_code_to_category",
                "create_category",
                "rename_category",
                "move_category",
                "merge_codes",
                "delete_code",
                "delete_category"
            ],
            "tip": "Use explain_ai_coding_tools() with no arguments for an overview"
        }, indent=2)


# ============================================================================
# INDUCTIVE / OPEN CODING (v0.8 phase A) — propose new codes from the data
# ============================================================================

@mcp.tool()
@_tool_guard
def propose_codes(coding_session_id: str, proposals: List[Dict[str, Any]],
                  replace: bool = False) -> str:
    """Record BRAND-NEW code proposals discovered in the data (inductive
    coding). Writes NOTHING to the project database — proposals live in
    the session for the user to review, refine and approve; only
    create_proposed_codes (after approval) touches the codebook.

    WORKFLOW: analyze_for_coding creates a session -> you (Claude) read
    the files and record the codes you see emerging with this tool ->
    present them -> the user refines (update_proposal / merge_proposals)
    and decides (update_proposal_status) -> create_proposed_codes writes
    the approved ones.

    Args:
        coding_session_id: The session ID from analyze_for_coding
        proposals: List of proposal objects with keys:
            name (required) — the proposed code name
            memo — the code definition (what belongs under this code)
            rationale — why this code emerges from the data
            color — optional #RRGGBB (default: QualCoder palette pick at
            creation)
            category — optional EXISTING category name to place it in
            example_segments — optional evidence spans
            [{file_id, start_pos, end_pos, segment_text}], each verified
            against the file text like record_suggestions verifies
            positions
        replace: Discard previously recorded PENDING proposals first
                 (approved/rejected/created are always kept)

    Returns:
        JSON with recorded proposals (GUIDs for review/approval),
        per-item rejections, and collides_with flags where a proposal
        name matches an existing code (creation will refuse those unless
        renamed — consider applying the existing code instead). If it
        contains `position_safety_warning`, you MUST relay it to the
        user before proceeding.
    """
    # Bridge fix: some MCP middleware strips arguments named
    # 'session_id' (reserved for its own routing); the tool
    # argument is coding_session_id, aliased for the body.
    session_id = coding_session_id
    if not session_manager.session_exists(session_id):
        return json.dumps({
            "error": f"Session {session_id} not found",
            "available_sessions": session_manager.list_sessions()
        })
    session = session_manager.load_session(session_id)
    mismatch = _check_session_project(session)
    if mismatch is not None:
        return json.dumps(mismatch, indent=2)
    if not isinstance(proposals, list) or not proposals:
        return json.dumps({
            "error": "proposals must be a non-empty list of proposal objects"
        })

    ro_db = get_db()
    cats = ro_db.list_categories()

    removed_pending = 0
    if replace:
        before = len(session.proposed_codes)
        session.proposed_codes = [p for p in session.proposed_codes
                                  if p.status != "pending"]
        removed_pending = before - len(session.proposed_codes)

    recorded, rejected = [], []
    unsafe_files: Dict[int, str] = {}
    file_cache: Dict[int, Optional[Dict[str, Any]]] = {}
    seen_names = {p.name.strip().lower() for p in session.proposed_codes
                  if p.status != "rejected"}

    for idx, item in enumerate(proposals):
        if not isinstance(item, dict):
            rejected.append({"index": idx, "reason": "each proposal must be an object"})
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            rejected.append({"index": idx, "reason": "name (non-empty string) is required"})
            continue
        name = name.strip()
        if name.lower() in seen_names:
            rejected.append({"index": idx,
                             "reason": f"a proposal named '{name}' already "
                                       f"exists in this session"})
            continue
        color = item.get("color")
        if color is not None and (not isinstance(color, str)
                                  or not re.fullmatch(r"#[0-9A-Fa-f]{6}", color)):
            rejected.append({"index": idx,
                             "reason": f"color must be #RRGGBB, got {color!r}"})
            continue
        category = item.get("category")
        if category is not None:
            match = next((c for c in cats
                          if c["name"].lower() == str(category).lower()), None)
            if match is None:
                rejected.append({
                    "index": idx,
                    "reason": f"category '{category}' not found — proposals "
                              f"may only target existing categories "
                              f"(create_category first if needed)",
                    "available_categories": sorted(c["name"] for c in cats)[:50],
                })
                continue
            category = match["name"]  # canonical spelling

        evidence, evidence_rejected, unsafe = _validate_proposal_evidence(
            ro_db, item.get("example_segments"), file_cache)
        unsafe_files.update(unsafe)

        proposal = ProposedCode(
            name=name,
            memo=str(item.get("memo", "") or item.get("definition", "")),
            rationale=str(item.get("rationale", "")),
            color=color,
            category=category,
            example_segments=evidence,
            collides_with=_code_name_collisions(name),
        )
        session.add_proposal(proposal)
        seen_names.add(name.lower())
        entry = {"guid": proposal.guid, "name": name,
                 "category": category, "evidence_count": len(evidence)}
        if proposal.collides_with:
            entry["collides_with"] = proposal.collides_with
        if evidence_rejected:
            entry["evidence_rejected"] = evidence_rejected
        recorded.append(entry)

    session_manager.save_session(session)

    result: Dict[str, Any] = {
        "coding_session_id": session_id,
        "session_id": session_id,  # deprecated duplicate of coding_session_id (kept one release)
        "recorded_count": len(recorded),
        "recorded": recorded,
        "rejected_count": len(rejected),
        "rejected": rejected,
        "proposal_statistics": session.proposal_statistics(),
        "next_step": "Present the proposals to the user; refine with "
                     "update_proposal / merge_proposals, decide with "
                     "update_proposal_status, then write the approved ones "
                     "with create_proposed_codes.",
    }
    if replace:
        result["replaced_pending"] = removed_pending
    if any(e.get("collides_with") for e in recorded):
        result["collision_note"] = (
            "Proposals flagged collides_with match an existing code "
            "(case-insensitively). Creation will refuse them unless renamed "
            "— consider applying the existing code via the normal coding "
            "loop instead of creating a near-duplicate."
        )
    if unsafe_files:
        result["position_safety_warning"] = (
            f"Evidence file(s) {sorted(unsafe_files.values())} contain "
            f"\\r\\n or characters beyond U+FFFF; codings on them may render "
            f"shifted in QualCoder's editor. Relay this to the user."
        )
    return json.dumps(result, indent=2)


@mcp.tool()
@_tool_guard
def review_proposals(coding_session_id: str,
                     proposal_guids: Optional[List[str]] = None,
                     show_examples: bool = False) -> str:
    """Review proposed codes in detail before deciding on them.

    Read-only. Shows each proposal's name, colour, category, definition,
    rationale, status, any collision with an existing code, and (with
    show_examples) the evidence spans.

    Args:
        coding_session_id: The session ID
        proposal_guids: Specific proposals to show (default: all)
        show_examples: Include the evidence segments (default: False)
    """
    # Bridge fix: some MCP middleware strips arguments named
    # 'session_id' (reserved for its own routing); the tool
    # argument is coding_session_id, aliased for the body.
    session_id = coding_session_id
    if not session_manager.session_exists(session_id):
        return json.dumps({"error": f"Session {session_id} not found"})
    session = session_manager.load_session(session_id)

    if proposal_guids:
        proposals = [p for g in proposal_guids
                     if (p := session.get_proposal_by_guid(g)) is not None]
    else:
        proposals = session.proposed_codes
    if not proposals:
        return "No proposals found."

    lines = [f"**Review of {len(proposals)} Code Proposal(s)**\n"]
    for i, p in enumerate(proposals, 1):
        lines.append("=" * 70)
        lines.append(f"**Proposal {i}** (GUID: `{p.guid}`)")
        lines.append(f"Status: {p.status.upper()}")
        lines.append(f"🏷️  **Name:** {p.name}")
        lines.append(f"🎨 Color: {p.color or '(palette pick at creation)'}")
        lines.append(f"📁 Category: {p.category or '(uncategorised)'}")
        if p.memo:
            lines.append(f"**Definition:** {p.memo}")
        if p.rationale:
            lines.append(f"**Rationale:** {p.rationale}")
        if p.collides_with:
            lines.append(f"⚠️  Collides with existing code: {p.collides_with}")
        if p.created_code_id is not None:
            lines.append(f"Created as code id {p.created_code_id}")
        lines.append(f"Evidence segments: {len(p.example_segments)}")
        if show_examples:
            for seg in p.example_segments:
                lines.append(f"  - {seg['file_name']} "
                             f"[{seg['start_pos']}-{seg['end_pos']}]: "
                             f"\u201c{seg['segment_text'][:200]}\u201d")
    return "\n".join(lines)


@mcp.tool()
@_tool_guard
def update_proposal(coding_session_id: str, proposal_guid: str,
                    name: Optional[str] = None,
                    color: Optional[str] = None,
                    category: Optional[str] = None,
                    memo: Optional[str] = None,
                    example_segments: Optional[List[Dict[str, Any]]] = None
                    ) -> str:
    """Refine a proposed code BEFORE it is created — rename, recolour,
    recategorise, rewrite its definition, or replace its evidence spans.

    Session-only (writes nothing to the project). Only the provided
    fields change. Pass category="" to make the proposal uncategorised.
    example_segments REPLACES the proposal's evidence wholesale — pass
    the full corrected list (e.g. with widened spans); each span is
    verified against the file text with the same machinery as
    propose_codes. Proposals already CREATED are immutable here — edit
    the real code with the codebook tools instead.

    Args:
        coding_session_id: The session ID
        proposal_guid: The proposal to refine
        name: New name (collision flag is refreshed)
        color: New #RRGGBB colour
        category: Existing category name, or "" to clear
        memo: New definition text
        example_segments: Replacement evidence spans
            [{file_id, start_pos, end_pos, segment_text}] — positions
            optional when the excerpt is unique in the file
    """
    # Bridge fix: some MCP middleware strips arguments named
    # 'session_id' (reserved for its own routing); the tool
    # argument is coding_session_id, aliased for the body.
    session_id = coding_session_id
    if not session_manager.session_exists(session_id):
        return json.dumps({"error": f"Session {session_id} not found"})
    session = session_manager.load_session(session_id)
    mismatch = _check_session_project(session)
    if mismatch is not None:
        return json.dumps(mismatch, indent=2)
    proposal = session.get_proposal_by_guid(proposal_guid)
    if proposal is None:
        return json.dumps({"error": f"Proposal {proposal_guid} not found"})
    if proposal.status == "created":
        return json.dumps({
            "error": f"Proposal '{proposal.name}' was already created as code "
                     f"id {proposal.created_code_id} — edit the code itself "
                     f"with rename_code / recolor_code / "
                     f"move_code_to_category / set_memo."
        })

    changes = {}
    if name is not None:
        if not isinstance(name, str) or not name.strip():
            return json.dumps({"error": "name must be a non-empty string"})
        new_name = name.strip()
        clash = any(p.guid != proposal.guid
                    and p.status != "rejected"
                    and p.name.strip().lower() == new_name.lower()
                    for p in session.proposed_codes)
        if clash:
            return json.dumps({
                "error": f"Another proposal in this session is already named "
                         f"'{new_name}'"
            })
        changes["name"] = (proposal.name, new_name)
        proposal.name = new_name
        proposal.collides_with = _code_name_collisions(new_name)
    if color is not None:
        if not isinstance(color, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
            return json.dumps({"error": f"color must be #RRGGBB, got {color!r}"})
        changes["color"] = (proposal.color, color)
        proposal.color = color
    if category is not None:
        if category == "":
            changes["category"] = (proposal.category, None)
            proposal.category = None
        else:
            cats = get_db().list_categories()
            match = next((c for c in cats
                          if c["name"].lower() == str(category).lower()), None)
            if match is None:
                return json.dumps({
                    "error": f"Category '{category}' not found",
                    "available_categories": sorted(c["name"] for c in cats)[:50],
                })
            changes["category"] = (proposal.category, match["name"])
            proposal.category = match["name"]
    if memo is not None:
        changes["memo"] = ("(previous definition)", memo)
        proposal.memo = str(memo)
    evidence_rejected = []
    unsafe_files: Dict[int, str] = {}
    if example_segments is not None:
        if not isinstance(example_segments, list):
            return json.dumps({"error": "example_segments must be a list "
                                        "of evidence objects"})
        kept, evidence_rejected, unsafe_files = _validate_proposal_evidence(
            get_db(), example_segments, {})
        if example_segments and not kept and evidence_rejected:
            return json.dumps({
                "error": "None of the replacement evidence spans verified "
                         "against the file text — evidence unchanged",
                "evidence_rejected": evidence_rejected,
            })
        changes["example_segments"] = (
            f"{len(proposal.example_segments)} span(s)",
            f"{len(kept)} span(s)")
        proposal.example_segments = kept

    if not changes:
        return json.dumps({"error": "Nothing to change — pass at least one "
                                    "of name/color/category/memo/"
                                    "example_segments"})
    session.last_modified = datetime.now().isoformat()
    session_manager.save_session(session)

    result = {"success": True, "guid": proposal.guid,
              "changes": {k: {"from": v[0], "to": v[1]}
                          for k, v in changes.items()}}
    if proposal.collides_with:
        result["collides_with"] = proposal.collides_with
    if evidence_rejected:
        result["evidence_rejected"] = evidence_rejected
    if unsafe_files:
        result["position_safety_warning"] = (
            f"Evidence file(s) {sorted(unsafe_files.values())} contain "
            f"\\r\\n or characters beyond U+FFFF; codings on them may "
            f"render shifted in QualCoder's editor. Relay this to the user."
        )
    return json.dumps(result, indent=2)


@mcp.tool()
@_tool_guard
def merge_proposals(coding_session_id: str, from_proposal_guid: str,
                    into_proposal_guid: str) -> str:
    """Combine two code PROPOSALS before creation.

    Session-only (writes nothing to the project; entirely distinct from
    merge_codes, which merges real codes in the codebook). The target
    proposal keeps its name/colour/category/definition and gains the
    source's evidence segments (deduplicated by file and span); the
    source proposal is marked rejected so it is never created.

    Args:
        coding_session_id: The session ID
        from_proposal_guid: The proposal merged away (becomes rejected)
        into_proposal_guid: The proposal that absorbs the evidence
    """
    # Bridge fix: some MCP middleware strips arguments named
    # 'session_id' (reserved for its own routing); the tool
    # argument is coding_session_id, aliased for the body.
    session_id = coding_session_id
    if not session_manager.session_exists(session_id):
        return json.dumps({"error": f"Session {session_id} not found"})
    session = session_manager.load_session(session_id)
    source = session.get_proposal_by_guid(from_proposal_guid)
    target = session.get_proposal_by_guid(into_proposal_guid)
    if source is None or target is None:
        return json.dumps({"error": "Both proposals must exist in this session"})
    if from_proposal_guid == into_proposal_guid:
        return json.dumps({"error": "Cannot merge a proposal into itself"})
    for p in (source, target):
        if p.status == "created":
            return json.dumps({
                "error": f"Proposal '{p.name}' was already created — merge "
                         f"the real codes with merge_codes instead."
            })

    existing_spans = {(s["file_id"], s["start_pos"], s["end_pos"])
                      for s in target.example_segments}
    moved = 0
    for seg in source.example_segments:
        key = (seg["file_id"], seg["start_pos"], seg["end_pos"])
        if key not in existing_spans:
            target.example_segments.append(seg)
            existing_spans.add(key)
            moved += 1
    source.status = "rejected"
    session.last_modified = datetime.now().isoformat()
    session_manager.save_session(session)
    return json.dumps({
        "success": True,
        "message": f"Merged proposal '{source.name}' into '{target.name}'",
        "evidence_moved": moved,
        "target": {"guid": target.guid, "name": target.name,
                   "evidence_count": len(target.example_segments)},
        "source_status": "rejected",
    }, indent=2)


@mcp.tool()
@_tool_guard
def update_proposal_status(coding_session_id: str,
                           approve: Optional[List[str]] = None,
                           reject: Optional[List[str]] = None) -> str:
    """Approve or reject code proposals — record the USER'S decisions.

    Approve only the proposals the user has actually reviewed and
    confirmed — do not approve on their behalf. Proposals already
    CREATED are immutable and skipped (skipped_created). Rejected
    proposals (and their evidence) are simply never created.

    Args:
        coding_session_id: The session ID
        approve: Proposal GUIDs the user approved
        reject: Proposal GUIDs the user rejected
    """
    # Bridge fix: some MCP middleware strips arguments named
    # 'session_id' (reserved for its own routing); the tool
    # argument is coding_session_id, aliased for the body.
    session_id = coding_session_id
    if not session_manager.session_exists(session_id):
        return json.dumps({"error": f"Session {session_id} not found"})
    session = session_manager.load_session(session_id)
    result = session.update_proposals_by_guid(approve=approve, reject=reject)
    session_manager.save_session(session)
    stats = session.proposal_statistics()
    return json.dumps({
        "success": True,
        **result,
        "proposal_statistics": stats,
        "next_step": "Use create_proposed_codes to write the approved "
                     "proposals to the codebook."
    }, indent=2)


@mcp.tool()
@_tool_guard
def create_proposed_codes(coding_session_id: str,
                          apply_coded_segments: bool = False,
                          create_backup: bool = True) -> str:
    """Create the APPROVED code proposals in the project codebook.

    THIS WRITES TO THE DATABASE — the write step of the inductive loop.
    Each approved proposal becomes a real code (palette colour if none
    chosen, placed in its category). With apply_coded_segments=true the
    proposal's evidence spans are ALSO written as codings under the new
    code; the default (false) creates the codes only, so the user can
    review them before any codings land — the normal
    record_suggestions -> apply_codings loop can then apply the
    now-existing codes.

    Every approved proposal is validated BEFORE the backup and the write:
    the name must still be unique against the live codebook (exact AND
    case-variant collisions refuse — rename the proposal first), the
    category must exist, and (when applying) every evidence span must
    still match the file text. Any failure -> nothing is written.
    Rejected proposals and their evidence are never created.

    Refused while QualCoder has the project open (heartbeat lock): ask
    the user to close the project in QualCoder, re-check with
    get_current_project (qualcoder_open must be false), then retry. The lock gate detects released QualCoder (3.x) only: QualCoder 4.0 builds no longer use a lock file, so 4.0 detection is best-effort heuristics (qualcoder_gui_signals in get_current_project); never write while any QualCoder window has this project open.

    Args:
        coding_session_id: The session with approved proposals
        apply_coded_segments: Also write the evidence spans as codings
                              (default: False — codes only)
        create_backup: Create a timestamped backup before writing (default True)

    Returns:
        JSON with the created codes (proposal guid -> real code id),
        codings applied (if any), and per-proposal failures. If it
        contains `position_safety_warning`, relay it to the user.
    """
    # Bridge fix: some MCP middleware strips arguments named
    # 'session_id' (reserved for its own routing); the tool
    # argument is coding_session_id, aliased for the body.
    session_id = coding_session_id
    if not session_manager.session_exists(session_id):
        return json.dumps({"error": f"Session {session_id} not found"})
    session = session_manager.load_session(session_id)
    mismatch = _check_session_project(session)
    if mismatch is not None:
        return json.dumps(mismatch, indent=2)

    approved = [p for p in session.proposed_codes if p.status == "approved"]
    if not approved:
        created_n = len([p for p in session.proposed_codes
                         if p.status == "created"])
        message = ("No approved proposals to create. Use "
                   "update_proposal_status to approve proposals first.")
        if created_n:
            message = (f"No approved proposals to create — {created_n} "
                       f"proposal(s) in this session were already created.")
        return json.dumps({"error": message,
                           "proposal_statistics": session.proposal_statistics()},
                          indent=2)

    # ---- pre-validation on the read-only connection (before backup) ----
    ro_db = get_db()
    cats = ro_db.list_categories()
    failures = []
    batch_names: set = set()
    file_cache: Dict[int, Optional[Dict[str, Any]]] = {}
    unsafe_files: Dict[int, str] = {}
    category_ids: Dict[str, int] = {}

    for p in approved:
        problem = None
        key = p.name.strip().lower()
        collision = _code_name_collisions(p.name)
        if collision:
            problem = (f"name collides with existing code '{collision}' — "
                       f"rename the proposal (update_proposal) or apply the "
                       f"existing code instead")
        elif key in batch_names:
            problem = "another approved proposal in this batch has the same name"
        elif p.category is not None:
            match = next((c for c in cats
                          if c["name"].lower() == p.category.lower()), None)
            if match is None:
                problem = (f"category '{p.category}' does not exist — create "
                           f"it first with create_category")
            else:
                category_ids[p.category] = match["id"]
        if problem is None and apply_coded_segments:
            for seg in p.example_segments:
                fid = seg["file_id"]
                if fid not in file_cache:
                    file_cache[fid] = ro_db.get_file_content(fid)
                fc = file_cache[fid]
                fulltext = (fc or {}).get("content") or ""
                if fc is None or not fulltext:
                    problem = f"evidence file {fid} no longer exists or has no text"
                    break
                if fulltext[seg["start_pos"]:seg["end_pos"]] != seg["segment_text"]:
                    problem = (f"evidence in '{seg['file_name']}' no longer "
                               f"matches the file text — re-propose or drop it")
                    break
                if fid not in unsafe_files and not db_position_safe(fulltext):
                    unsafe_files[fid] = fc["name"]
        if problem is not None:
            failures.append({"guid": p.guid, "name": p.name, "reason": problem})
        else:
            batch_names.add(key)

    if failures:
        return json.dumps({
            "error": f"{len(failures)} approved proposal(s) failed validation "
                     f"— nothing was written and no backup was created.",
            "failures": failures,
        }, indent=2)

    owner = _default_owner()

    def _op(wdb):
        created = []
        codings_applied = 0
        for p in approved:
            cid = wdb.add_code(
                name=p.name.strip(),
                owner=owner,
                memo=p.memo or "",
                category_id=(category_ids.get(p.category)
                             if p.category else None),
                color=p.color,
                auto_commit=False,
            )
            p.created_code_id = cid
            created.append({"proposal_guid": p.guid, "code_id": cid,
                            "name": p.name.strip(),
                            "category": p.category})
            if apply_coded_segments:
                for seg in p.example_segments:
                    memo = (f"{p.rationale}\n\n[AI proposed code]"
                            if p.rationale else "[AI proposed code]")
                    wdb.add_coding(
                        file_id=seg["file_id"],
                        code_id=cid,
                        start_pos=seg["start_pos"],
                        end_pos=seg["end_pos"],
                        selected_text=seg["segment_text"],
                        owner=owner,
                        memo=memo,
                        auto_commit=False,
                    )
                    codings_applied += 1
        if apply_coded_segments:
            # C7: re-verify each evidence file's text inside the write
            # transaction against the pre-validation snapshot
            for fid, fc in sorted(file_cache.items()):
                validated_text = (fc or {}).get("content") or ""
                if validated_text:
                    wdb.verify_fulltext_unchanged(
                        fid, wdb.fingerprint_of_text(validated_text))
        return {"success": True,
                "message": f"Created {len(created)} code(s)"
                           + (f" and applied {codings_applied} coding(s)"
                              if apply_coded_segments else ""),
                "created_codes": created,
                "codings_applied": codings_applied}

    result = _perform_write(_op, create_backup=create_backup,
                            backup_fail_detail="no codes were created")

    if "error" not in result:
        for p in approved:
            p.status = "created"
        session.last_modified = datetime.now().isoformat()
        session_manager.save_session(session)
        result["proposal_statistics"] = session.proposal_statistics()
        if unsafe_files:
            result["position_safety_warning"] = (
                f"File(s) {sorted(unsafe_files.values())} are position-unsafe "
                f"(emoji/CRLF); the applied codings may render shifted in "
                f"QualCoder's editor. Relay this to the user."
            )
    return json.dumps(result, indent=2)


# ============================================================================
# MEMO WRITING & JOURNALS (write tools)
# ============================================================================

@mcp.tool()
@_tool_guard
def set_memo(target_type: str, target_id: int, memo: str,
             create_backup: bool = True,
             allow_hidden_coder: bool = False) -> str:
    """Write (or clear) the memo on a code, category, file, coding, or case.

    THIS WRITES TO THE DATABASE. Memos are the researcher's analytic notes
    attached to an object. This sets the memo, replacing any existing one;
    pass an empty string to clear it.

    Memo privacy (QualCoder 4.0 convention): memo text from the first
    '#####' marker onward is the researcher's private zone. This tool
    replaces only the text before the marker; an existing private
    section always survives the write, and a '#####' in the new text is
    not written. Memos returned by read tools contain the public part
    only.

    Refused while QualCoder has the project open (heartbeat lock): ask
    the user to close the project in QualCoder, re-check with
    get_current_project (qualcoder_open must be false), then retry. The lock gate detects released QualCoder (3.x) only: QualCoder 4.0 builds no longer use a lock file, so 4.0 detection is best-effort heuristics (qualcoder_gui_signals in get_current_project); never write while any QualCoder window has this project open.

    Coder visibility (QualCoder 4.0 projects that hide coders): a memo
    on a CODING owned by a hidden coder is REFUSED unless the user asks
    for allow_hidden_coder=true; the refusal names neither the coder nor
    how many are hidden. Codes, categories, files and cases have no
    per-coder visibility and are unaffected.

    Args:
        target_type: What to attach the memo to — one of 'code', 'category',
                     'file', 'coding', 'case'
        target_id: The object's id (code cid / category catid / file source
                   id / coding ctid / case caseid)
        memo: The memo text ('' clears it)
        create_backup: Create a timestamped backup before writing (default True)
        allow_hidden_coder: Override to write on a hidden coder's coding
                            (target_type 'coding' only)

    Returns:
        JSON confirming the updated object and memo

    Example:
        "Add a memo to code 5: 'participants frame this as institutional'"
        "Note on file 3 that the audio was hard to transcribe"
    """
    # Validate on the read-only connection before upgrading/backup
    valid = {"code", "category", "file", "coding", "case"}
    if target_type not in valid:
        return json.dumps({
            "error": f"target_type must be one of: {', '.join(sorted(valid))}"
        })

    if target_type == "coding":
        refusal = _refuse_existing_row_change(
            "coding", target_id, allow_hidden_coder=allow_hidden_coder,
            deleting=False)
        if refusal is not None:
            return json.dumps(refusal, indent=2)

    result = _perform_write(
        lambda wdb: {
            "success": True,
            **wdb.set_memo(target_type, target_id, memo, auto_commit=False,
                           allow_hidden_coder=allow_hidden_coder),
        },
        create_backup=create_backup,
        backup_fail_detail="the memo was not changed",
    )
    return _ai_json(result, indent=2)


@mcp.tool()
@_tool_guard
def add_journal_entry(name: str, entry: str,
                      create_backup: bool = True) -> str:
    """Add a research journal entry to the project.

    THIS WRITES TO THE DATABASE. Journals are free-form research notes
    (reflexive memos, decisions, an audit trail) kept alongside the coding.

    Refused while QualCoder has the project open (heartbeat lock): ask
    the user to close the project in QualCoder, re-check with
    get_current_project (qualcoder_open must be false), then retry. The lock gate detects released QualCoder (3.x) only: QualCoder 4.0 builds no longer use a lock file, so 4.0 detection is best-effort heuristics (qualcoder_gui_signals in get_current_project); never write while any QualCoder window has this project open.

    Args:
        name: A short unique title for the entry
        entry: The journal text
        create_backup: Create a timestamped backup before writing (default True)

    Returns:
        JSON with the new entry's id, name and date

    Example:
        "Add a journal entry titled 'Week 1 reflections' about the emerging
         boundary-setting theme"
    """
    owner = _default_owner()
    result = _perform_write(
        lambda wdb: {
            "success": True,
            "message": f"Added journal entry '{name}'",
            "journal_entry": wdb.add_journal_entry(
                name, entry, owner, auto_commit=False),
        },
        create_backup=create_backup,
        backup_fail_detail="the journal entry was not added",
    )
    return json.dumps(result, indent=2)


# ============================================================================
# CODEBOOK EDITING (non-destructive write tools)
# ============================================================================

@mcp.tool()
@_tool_guard
def create_code(name: str, category: Optional[str] = None,
                color: Optional[str] = None, memo: Optional[str] = None,
                parent_code_id: Optional[int] = None,
                create_backup: bool = True) -> str:
    """Create a new code in the codebook.

    THIS WRITES TO THE DATABASE. Adds a code that can then be applied to
    segments. Code names are unique. The color defaults to a random pick
    from QualCoder's own palette (like GUI-created codes).

    SUB-CODES (projects with schema v16 or newer only): pass
    parent_code_id to nest the new code under an existing CODE instead of
    a category. A code has either a parent code or a category, never
    both; on projects without sub-code support the parameter is refused.

    Refused while QualCoder has the project open (heartbeat lock): ask
    the user to close the project in QualCoder, re-check with
    get_current_project (qualcoder_open must be false), then retry. The lock gate detects released QualCoder (3.x) only: QualCoder 4.0 builds no longer use a lock file, so 4.0 detection is best-effort heuristics (qualcoder_gui_signals in get_current_project); never write while any QualCoder window has this project open.

    Args:
        name: The code name (must be unique among codes)
        category: Optional category name to place the code in (matched
                  case-insensitively; must already exist)
        color: Optional #RRGGBB hex color (default: random palette color)
        memo: Optional code definition/memo
        parent_code_id: Optional cid of an existing code to nest under
                        (v16+ sub-code; mutually exclusive with category)
        create_backup: Create a timestamped backup before writing (default True)

    Returns:
        JSON with the new code's id, name, category and color

    Example:
        "Create a code 'Institutional distrust' in the Wellbeing category"
    """
    category_id = None
    if category is not None:
        if parent_code_id is not None:
            return json.dumps({
                "error": "Give parent_code_id or category, not both: a "
                         "code has one parent, either a code or a category."
            })
        category_id, err = _resolve_category_by_name(str(category))
        if err is not None:
            return json.dumps(err, indent=2)

    owner = _default_owner()

    def _op(wdb):
        cid = wdb.add_code(name=name, owner=owner, memo=memo,
                           category_id=category_id, color=color,
                           parent_code_id=parent_code_id,
                           auto_commit=False)
        details = wdb.get_code_details(cid)
        return {
            "success": True,
            "message": f"Created code '{name}'",
            "code": {
                "id": cid,
                "name": details["name"],
                "category": details.get("category"),
                "parent_code_id": parent_code_id,
                "color": details.get("color"),
                "memo": details.get("memo", ""),
            },
        }

    result = _perform_write(_op, create_backup=create_backup,
                            backup_fail_detail="the code was not created")
    return _ai_json(result, indent=2)


@mcp.tool()
@_tool_guard
def rename_code(code_id: int, new_name: str,
                create_backup: bool = True) -> str:
    """Rename a code. THIS WRITES TO THE DATABASE. Names are unique.

    Refused while QualCoder has the project open (heartbeat lock): ask
    the user to close the project in QualCoder, re-check with
    get_current_project (qualcoder_open must be false), then retry. The lock gate detects released QualCoder (3.x) only: QualCoder 4.0 builds no longer use a lock file, so 4.0 detection is best-effort heuristics (qualcoder_gui_signals in get_current_project); never write while any QualCoder window has this project open.

    Args:
        code_id: The code's cid
        new_name: The new name (must not collide with another code)
        create_backup: Create a timestamped backup before writing (default True)
    """
    result = _perform_write(
        lambda wdb: {"success": True,
                     "message": "Renamed code",
                     **wdb.rename_code(code_id, new_name, auto_commit=False)},
        create_backup=create_backup,
        backup_fail_detail="the code was not renamed",
    )
    return json.dumps(result, indent=2)


@mcp.tool()
@_tool_guard
def recolor_code(code_id: int, color: str,
                 create_backup: bool = True) -> str:
    """Set a code's color (#RRGGBB). THIS WRITES TO THE DATABASE.

    Refused while QualCoder has the project open (heartbeat lock): ask
    the user to close the project in QualCoder, re-check with
    get_current_project (qualcoder_open must be false), then retry. The lock gate detects released QualCoder (3.x) only: QualCoder 4.0 builds no longer use a lock file, so 4.0 detection is best-effort heuristics (qualcoder_gui_signals in get_current_project); never write while any QualCoder window has this project open.

    Args:
        code_id: The code's cid
        color: Hex color in #RRGGBB format
        create_backup: Create a timestamped backup before writing (default True)
    """
    result = _perform_write(
        lambda wdb: {"success": True,
                     "message": "Recolored code",
                     **wdb.recolor_code(code_id, color, auto_commit=False)},
        create_backup=create_backup,
        backup_fail_detail="the code color was not changed",
    )
    return json.dumps(result, indent=2)


@mcp.tool()
@_tool_guard
def move_code_to_category(code_id: int,
                          category: Optional[str] = None,
                          create_backup: bool = True) -> str:
    """Move a code into a category (or out of any category).

    THIS WRITES TO THE DATABASE.

    Refused while QualCoder has the project open (heartbeat lock): ask
    the user to close the project in QualCoder, re-check with
    get_current_project (qualcoder_open must be false), then retry. The lock gate detects released QualCoder (3.x) only: QualCoder 4.0 builds no longer use a lock file, so 4.0 detection is best-effort heuristics (qualcoder_gui_signals in get_current_project); never write while any QualCoder window has this project open.

    Args:
        code_id: The code's cid
        category: Category name to move the code into (case-insensitive),
                  or null/omitted to make the code uncategorised
        create_backup: Create a timestamped backup before writing (default True)
    """
    category_id = None
    if category is not None:
        category_id, err = _resolve_category_by_name(str(category))
        if err is not None:
            return json.dumps(err, indent=2)

    result = _perform_write(
        lambda wdb: {"success": True,
                     "message": "Moved code",
                     **wdb.move_code_to_category(code_id, category_id,
                                                 auto_commit=False)},
        create_backup=create_backup,
        backup_fail_detail="the code was not moved",
    )
    return json.dumps(result, indent=2)


@mcp.tool()
@_tool_guard
def create_category(name: str, parent_category: Optional[str] = None,
                    memo: Optional[str] = None,
                    create_backup: bool = True) -> str:
    """Create a code category. THIS WRITES TO THE DATABASE.

    Categories group codes (and can nest under a parent category). Names
    are unique among categories.

    Refused while QualCoder has the project open (heartbeat lock): ask
    the user to close the project in QualCoder, re-check with
    get_current_project (qualcoder_open must be false), then retry. The lock gate detects released QualCoder (3.x) only: QualCoder 4.0 builds no longer use a lock file, so 4.0 detection is best-effort heuristics (qualcoder_gui_signals in get_current_project); never write while any QualCoder window has this project open.

    Args:
        name: The category name (unique among categories)
        parent_category: Optional parent category name (case-insensitive) to
                         nest under; omit for a top-level category
        memo: Optional category memo
        create_backup: Create a timestamped backup before writing (default True)
    """
    supercatid = None
    if parent_category is not None:
        supercatid, err = _resolve_category_by_name(str(parent_category))
        if err is not None:
            return json.dumps(err, indent=2)

    owner = _default_owner()
    result = _perform_write(
        lambda wdb: {"success": True,
                     "message": f"Created category '{name}'",
                     "category": wdb.add_category(name, owner,
                                                  supercatid=supercatid,
                                                  memo=memo, auto_commit=False)},
        create_backup=create_backup,
        backup_fail_detail="the category was not created",
    )
    return json.dumps(result, indent=2)


@mcp.tool()
@_tool_guard
def rename_category(category_id: int, new_name: str,
                    create_backup: bool = True) -> str:
    """Rename a category. THIS WRITES TO THE DATABASE. Names are unique.

    Refused while QualCoder has the project open (heartbeat lock): ask
    the user to close the project in QualCoder, re-check with
    get_current_project (qualcoder_open must be false), then retry. The lock gate detects released QualCoder (3.x) only: QualCoder 4.0 builds no longer use a lock file, so 4.0 detection is best-effort heuristics (qualcoder_gui_signals in get_current_project); never write while any QualCoder window has this project open.

    Args:
        category_id: The category's catid
        new_name: The new name (must not collide with another category)
        create_backup: Create a timestamped backup before writing (default True)
    """
    result = _perform_write(
        lambda wdb: {"success": True,
                     "message": "Renamed category",
                     **wdb.rename_category(category_id, new_name,
                                           auto_commit=False)},
        create_backup=create_backup,
        backup_fail_detail="the category was not renamed",
    )
    return json.dumps(result, indent=2)


@mcp.tool()
@_tool_guard
def move_category(category_id: int, parent_category: Optional[str] = None,
                  create_backup: bool = True) -> str:
    """Reparent a category under another category (or to the top level).

    THIS WRITES TO THE DATABASE. Refuses any move that would create a cycle
    (make a category its own ancestor) — such a cycle would silently hide
    the category and all its codes from QualCoder's tree.

    Refused while QualCoder has the project open (heartbeat lock): ask
    the user to close the project in QualCoder, re-check with
    get_current_project (qualcoder_open must be false), then retry. The lock gate detects released QualCoder (3.x) only: QualCoder 4.0 builds no longer use a lock file, so 4.0 detection is best-effort heuristics (qualcoder_gui_signals in get_current_project); never write while any QualCoder window has this project open.

    Args:
        category_id: The category to move (its catid)
        parent_category: Name of the new parent category (case-insensitive),
                         or null/omitted to move to the top level
        create_backup: Create a timestamped backup before writing (default True)
    """
    new_supercatid = None
    if parent_category is not None:
        new_supercatid, err = _resolve_category_by_name(str(parent_category))
        if err is not None:
            return json.dumps(err, indent=2)

    result = _perform_write(
        lambda wdb: {"success": True,
                     "message": "Moved category",
                     **wdb.move_category(category_id, new_supercatid,
                                         auto_commit=False)},
        create_backup=create_backup,
        backup_fail_detail="the category was not moved",
    )
    return json.dumps(result, indent=2)


# ============================================================================
# CODEBOOK EDITING (destructive — preview -> confirm -> safety backup)
# ============================================================================

def _guarded_destructive(preview_fn, op_fn, confirm: bool,
                         backup_fail_detail: str,
                         confirm_hint: str) -> Dict[str, Any]:
    """Preview -> confirm -> safety-backup gate for destructive codebook ops.

    Without confirm this returns a preview (read-only) of exactly what will
    change; with confirm=true it runs the mutation under the full write
    discipline, always creating a backup first (the safety net).
    """
    try:
        preview = preview_fn(get_db())
    except (ValueError, RuntimeError) as e:
        return {"error": str(e)}

    if not confirm:
        return {
            "requires_confirmation": True,
            "preview": preview,
            "hint": confirm_hint,
        }

    # Always back up before a destructive write (no create_backup=False here)
    return _perform_write(op_fn, create_backup=True,
                          backup_fail_detail=backup_fail_detail)


@mcp.tool()
@_tool_guard
def merge_codes(from_code_id: int, into_code_id: int,
                confirm: bool = False) -> str:
    """Merge one code into another. DESTRUCTIVE — preview first, then confirm.

    THIS WRITES TO THE DATABASE. All codings of `from_code_id` are reassigned
    to `into_code_id`, then `from_code_id` is deleted. This matches QualCoder
    exactly and is LOSSY BY DESIGN: where both codes already mark the same
    text span by the same coder, the source coding (with its memo/important
    flag) is DISCARDED — the destination coding wins. Audio/video and image
    codings are reassigned without de-duplication (as QualCoder does), which
    can create visual duplicates.

    Call once without confirm to see how many codings will be reassigned vs
    discarded, then again with confirm=true. A backup is created first.

    Refused while QualCoder has the project open (heartbeat lock): ask
    the user to close the project in QualCoder, re-check with
    get_current_project (qualcoder_open must be false), then retry. The lock gate detects released QualCoder (3.x) only: QualCoder 4.0 builds no longer use a lock file, so 4.0 detection is best-effort heuristics (qualcoder_gui_signals in get_current_project); never write while any QualCoder window has this project open.

    Args:
        from_code_id: The code to merge away (deleted afterwards)
        into_code_id: The code to keep (receives the codings)
        confirm: Must be true to actually merge (default: preview only)
    """
    result = _guarded_destructive(
        preview_fn=lambda ro: ro.preview_merge_codes(from_code_id, into_code_id),
        op_fn=lambda wdb: {"success": True, "message": "Merged codes",
                           **wdb.merge_codes(from_code_id, into_code_id,
                                             auto_commit=False)},
        confirm=confirm,
        backup_fail_detail="no codes were merged",
        confirm_hint="Review the counts, then call merge_codes again with "
                     "confirm=true. The source coding is discarded on any "
                     "duplicate span; a backup is made first.",
    )
    return json.dumps(result, indent=2)


@mcp.tool()
@_tool_guard
def delete_code(code_id: int, confirm: bool = False,
                cascade: bool = False) -> str:
    """Delete a code AND all its codings. DESTRUCTIVE — preview then confirm.

    THIS WRITES TO THE DATABASE. Deleting a code removes the code itself and
    EVERY coded segment made with it (text, audio/video, and image codings).
    Categories, annotations, case links and other codes are not affected.

    SUB-CODES (projects with schema v16 or newer): a code that has
    sub-codes is REFUSED unless cascade=true, which then deletes the
    whole branch (the code, every transitive sub-code, and all their
    codings) in one transaction, exactly as QualCoder's own delete. The
    preview always reports the branch, so review it before confirming.
    Move the sub-codes first if they are needed.

    Call once without confirm to see how many codings will be destroyed, then
    again with confirm=true. A backup is created first, so a mistaken delete
    can be undone with restore_backup.

    Refused while QualCoder has the project open (heartbeat lock): ask
    the user to close the project in QualCoder, re-check with
    get_current_project (qualcoder_open must be false), then retry. The lock gate detects released QualCoder (3.x) only: QualCoder 4.0 builds no longer use a lock file, so 4.0 detection is best-effort heuristics (qualcoder_gui_signals in get_current_project); never write while any QualCoder window has this project open.

    Args:
        code_id: The code's cid
        confirm: Must be true to actually delete (default: preview only)
        cascade: Must be true to delete a code that has sub-codes (the
                 whole branch dies; default false refuses instead)
    """
    result = _guarded_destructive(
        preview_fn=lambda ro: ro.preview_delete_code(code_id),
        op_fn=lambda wdb: {"success": True, "message": "Deleted code",
                           **wdb.delete_code(code_id, cascade=cascade,
                                             auto_commit=False)},
        confirm=confirm,
        backup_fail_detail="the code was not deleted",
        confirm_hint="This will destroy the code and all its coded segments "
                     "(and, with cascade=true, its whole sub-code branch). "
                     "Review total_codings_to_delete, then call delete_code "
                     "again with confirm=true. A backup is made first.",
    )
    return json.dumps(result, indent=2)


@mcp.tool()
@_tool_guard
def delete_category(category_id: int, confirm: bool = False) -> str:
    """Delete a category. DESTRUCTIVE to the category — preview then confirm.

    THIS WRITES TO THE DATABASE. Deleting a category is SHALLOW and safe for
    your coding: its codes and its direct sub-categories are moved to the top
    level (never deleted, never reparented to a grandparent), then the
    category itself is removed. No coded data is lost.

    Version note: this matches QualCoder 3.8.2's category delete.
    QualCoder 4.0's tree UI instead deletes the whole branch INCLUDING
    codes and codings; this tool deliberately does not do that (the safe
    detach is valid on every schema and never destroys coded data).

    Call once without confirm to see how many codes and sub-categories will be
    moved to the top level, then again with confirm=true. A backup is made
    first.

    Refused while QualCoder has the project open (heartbeat lock): ask
    the user to close the project in QualCoder, re-check with
    get_current_project (qualcoder_open must be false), then retry. The lock gate detects released QualCoder (3.x) only: QualCoder 4.0 builds no longer use a lock file, so 4.0 detection is best-effort heuristics (qualcoder_gui_signals in get_current_project); never write while any QualCoder window has this project open.

    Args:
        category_id: The category's catid
        confirm: Must be true to actually delete (default: preview only)
    """
    result = _guarded_destructive(
        preview_fn=lambda ro: ro.preview_delete_category(category_id),
        op_fn=lambda wdb: {"success": True, "message": "Deleted category",
                           **wdb.delete_category(category_id,
                                                 auto_commit=False)},
        confirm=confirm,
        backup_fail_detail="the category was not deleted",
        confirm_hint="Codes and sub-categories will move to the top level "
                     "(coded data is untouched). Call delete_category again "
                     "with confirm=true. A backup is made first.",
    )
    return json.dumps(result, indent=2)


@mcp.tool()
@_tool_guard
def merge_category(from_category_id: int,
                   into_category: Optional[str] = None,
                   confirm: bool = False) -> str:
    """Merge a category into another category (or into the top level).

    DESTRUCTIVE to the category — preview first, then confirm. The source
    category's codes and direct sub-categories are reparented to the
    target (unlike delete_category, which sends them to the top level),
    then the source category is removed. Coded data is never touched —
    codings key on the code, not the category. Merging into a descendant
    of the source is refused (it would orphan the subtree).

    Source category memo: on projects with sub-code support (v16+
    schemas) a merge into a real target carries the source category's
    memo into the target's memo under a "[Merged from category: ...]"
    provenance note, as QualCoder master does; the note lands before any
    '#####' private section on the target, which survives verbatim, and a
    private section the source carries stays private. Merging to the top
    level, or on a pre-sub-code schema (QualCoder 3.8.2 parity), removes
    the source memo with its row; the mandatory backup keeps a copy. The
    preview states which applies (source_memo_carried_to_target) and the
    result reports provenance_memo_added.

    Refused while QualCoder has the project open (heartbeat lock): ask
    the user to close the project in QualCoder, re-check with
    get_current_project (qualcoder_open must be false), then retry. The lock gate detects released QualCoder (3.x) only: QualCoder 4.0 builds no longer use a lock file, so 4.0 detection is best-effort heuristics (qualcoder_gui_signals in get_current_project); never write while any QualCoder window has this project open.

    Args:
        from_category_id: The category to merge away (deleted afterwards)
        into_category: Target category name (case-insensitive, ambiguous
                       case-variants refused), or null/omitted to move
                       everything to the top level
        confirm: Must be true to actually merge (default: preview only)
    """
    into_category_id = None
    if into_category is not None:
        into_category_id, err = _resolve_category_by_name(str(into_category))
        if err is not None:
            return json.dumps(err, indent=2)

    result = _guarded_destructive(
        preview_fn=lambda ro: ro.preview_merge_category(from_category_id,
                                                        into_category_id),
        op_fn=lambda wdb: {"success": True, "message": "Merged category",
                           **wdb.merge_category(from_category_id,
                                                into_category_id,
                                                auto_commit=False)},
        confirm=confirm,
        backup_fail_detail="no categories were merged",
        confirm_hint="Review the reparent counts, then call merge_category "
                     "again with confirm=true. A backup is made first.",
    )
    return json.dumps(result, indent=2)


# ============================================================================
# ANNOTATIONS (v0.8 D1 write tools)
# ============================================================================

@mcp.tool()
@_tool_guard
def add_annotation(file_id: int, start_pos: int, end_pos: int, memo: str,
                   create_backup: bool = True) -> str:
    """Attach a note (annotation) to a text span of a file.

    THIS WRITES TO THE DATABASE. An annotation is a researcher note
    anchored to characters [start_pos, end_pos) of a text file — distinct
    from a coding (no code involved) and from a file memo (span-specific).
    The note must be non-empty: the note IS the annotation, and clearing
    it later (update_annotation with "") deletes it, exactly as QualCoder
    behaves. One annotation per coder per exact span.

    Refused while QualCoder has the project open (heartbeat lock): ask
    the user to close the project in QualCoder, re-check with
    get_current_project (qualcoder_open must be false), then retry. The lock gate detects released QualCoder (3.x) only: QualCoder 4.0 builds no longer use a lock file, so 4.0 detection is best-effort heuristics (qualcoder_gui_signals in get_current_project); never write while any QualCoder window has this project open.

    Args:
        file_id: The text file to annotate
        start_pos: 0-based character offset (inclusive)
        end_pos: End offset (exclusive, > start_pos)
        memo: The note text (must be non-empty)
        create_backup: Create a timestamped backup before writing (default True)

    Returns:
        JSON with the new annotation (anid, span, note). If it contains
        `position_safety_warning`, you MUST relay it to the user — spans
        on such files can render shifted in QualCoder's editor.
    """
    owner = _default_owner()

    def _op(wdb):
        created = wdb.add_annotation(file_id, start_pos, end_pos, memo,
                                     owner, auto_commit=False)
        result = {"success": True,
                  "message": f"Annotated '{created['file_name']}' at "
                             f"{start_pos}-{end_pos}",
                  "annotation": created}
        fulltext = (wdb.get_file_content(file_id) or {}).get("content") or ""
        if fulltext and not db_position_safe(fulltext):
            result["position_safety_warning"] = (
                f"File '{created['file_name']}' contains \\r\\n or characters "
                f"beyond U+FFFF, so this annotation's span may render shifted "
                f"in QualCoder's editor. Relay this to the user."
            )
        return result

    result = _perform_write(_op, create_backup=create_backup,
                            backup_fail_detail="the annotation was not added")
    return _ai_json(result, indent=2)


@mcp.tool()
@_tool_guard
def update_annotation(annotation_id: int, memo: str,
                      create_backup: bool = True,
                      allow_hidden_coder: bool = False) -> str:
    """Edit an annotation's note. AN EMPTY NOTE DELETES THE ANNOTATION.

    THIS WRITES TO THE DATABASE. Matches QualCoder exactly: editing
    updates the note and its date (owner and the anchored span never
    change); clearing the note to "" deletes the annotation row —
    QualCoder never keeps an empty annotation. The response says whether
    it updated or deleted.

    Memo privacy (QualCoder 4.0 convention): only the note text before
    the first '#####' marker is replaced; a private section after the
    marker survives, and clearing the note keeps the row when such a
    section exists (the response then reports cleared, not deleted).

    Coder visibility (QualCoder 4.0 projects that hide coders): an
    annotation belonging to a hidden coder is REFUSED unless the user
    asks for allow_hidden_coder=true; the refusal names neither the
    coder nor how many are hidden. With the override the echo carries
    ids and the new public text only, plus a coder_visibility note; the
    hidden coder's name, span and file never enter the conversation.

    Refused while QualCoder has the project open (heartbeat lock): ask
    the user to close the project in QualCoder, re-check with
    get_current_project (qualcoder_open must be false), then retry. The lock gate detects released QualCoder (3.x) only: QualCoder 4.0 builds no longer use a lock file, so 4.0 detection is best-effort heuristics (qualcoder_gui_signals in get_current_project); never write while any QualCoder window has this project open.

    Args:
        annotation_id: The annotation's anid (from analyze_file_with_coding
                       or search_memos)
        memo: The new note text ('' deletes the annotation)
        create_backup: Create a timestamped backup before writing (default True)
        allow_hidden_coder: Override to edit a hidden coder's annotation
    """
    refusal = _refuse_existing_row_change(
        "annotation", annotation_id, allow_hidden_coder=allow_hidden_coder,
        deleting=False)
    if refusal is not None:
        return json.dumps(refusal, indent=2)
    result = _perform_write(
        lambda wdb: {"success": True,
                     **wdb.update_annotation(
                         annotation_id, memo, auto_commit=False,
                         allow_hidden_coder=allow_hidden_coder)},
        create_backup=create_backup,
        backup_fail_detail="the annotation was not changed",
    )
    _attach_hidden_target_note(result)
    return _ai_json(result, indent=2)


@mcp.tool()
@_tool_guard
def delete_annotation(annotation_id: int, create_backup: bool = True,
                      allow_hidden_coder: bool = False,
                      confirm_private_note_deletion: bool = False) -> str:
    """Delete an annotation by its anid.

    THIS WRITES TO THE DATABASE. Removes one annotation (the note on a
    text span) — never the text, codings, or anything else. A backup is
    created first by default.

    Two guards, each with an explicit override the user must ask for:
    - Hidden coder (QualCoder 4.0 projects that hide coders): an
      annotation owned by a hidden coder is REFUSED unless
      allow_hidden_coder=true. The refusal names neither the coder nor
      how many are hidden. With the override the echo carries ids only
      plus a coder_visibility note.
    - Private note (any project): an annotation whose note carries a
      '#####' private section the assistant cannot see is REFUSED unless
      confirm_private_note_deletion=true, and a backup is ALWAYS taken
      for such a row even with create_backup=false. Note that this
      refusal, or the forced backup, tells you that a private note
      exists on the row (never its content); the owner accepts that.
    When both apply, both overrides are required and both refusals come
    back in one response.

    Refused while QualCoder has the project open (heartbeat lock): ask
    the user to close the project in QualCoder, re-check with
    get_current_project (qualcoder_open must be false), then retry. The lock gate detects released QualCoder (3.x) only: QualCoder 4.0 builds no longer use a lock file, so 4.0 detection is best-effort heuristics (qualcoder_gui_signals in get_current_project); never write while any QualCoder window has this project open.

    Args:
        annotation_id: The annotation's anid
        create_backup: Create a timestamped backup before writing (default
                       True; ignored, always on, for a row carrying a
                       private note)
        allow_hidden_coder: Override to delete a hidden coder's annotation
        confirm_private_note_deletion: Override to delete an annotation
                       whose note carries a private section
    """
    refusal = _refuse_existing_row_change(
        "annotation", annotation_id, allow_hidden_coder=allow_hidden_coder,
        deleting=True,
        confirm_private_note_deletion=confirm_private_note_deletion)
    if refusal is not None:
        return json.dumps(refusal, indent=2)
    status = get_db().existing_row_status("annotation", annotation_id) or {}
    # S-P2 (a): a row carrying a private note is always backed up first
    result = _perform_write(
        lambda wdb: {"success": True,
                     "message": "Deleted annotation",
                     **wdb.delete_annotation(
                         annotation_id, auto_commit=False,
                         allow_hidden_coder=allow_hidden_coder,
                         confirm_private_note_deletion=(
                             confirm_private_note_deletion))},
        create_backup=create_backup or bool(status.get("private_note")),
        backup_fail_detail="the annotation was not deleted",
    )
    _private_note_backup_note(result, status, create_backup)
    _attach_hidden_target_note(result)
    return _ai_json(result, indent=2)


# ============================================================================
# CASES (v0.8 D1 write tool)
# ============================================================================

@mcp.tool()
@_tool_guard
def create_case(name: str, memo: Optional[str] = None,
                create_backup: bool = True) -> str:
    """Create a new case (participant/subject) in the project.

    THIS WRITES TO THE DATABASE. Cases group data by participant; link
    files to the new case with link_file_to_case (or import_text_file's
    case_name parameter) so they appear in case-based analyses. Case
    names are unique. Placeholder rows are created for any existing case
    attributes, exactly as QualCoder does.

    Refused while QualCoder has the project open (heartbeat lock): ask
    the user to close the project in QualCoder, re-check with
    get_current_project (qualcoder_open must be false), then retry. The lock gate detects released QualCoder (3.x) only: QualCoder 4.0 builds no longer use a lock file, so 4.0 detection is best-effort heuristics (qualcoder_gui_signals in get_current_project); never write while any QualCoder window has this project open.

    Args:
        name: The case name (unique among cases)
        memo: Optional case memo
        create_backup: Create a timestamped backup before writing (default True)

    Example:
        "Create a case for participant Dana"
    """
    owner = _default_owner()
    result = _perform_write(
        lambda wdb: {"success": True,
                     "message": f"Created case '{name.strip() if isinstance(name, str) else name}'",
                     "case": wdb.add_case(name, owner, memo=memo,
                                          auto_commit=False)},
        create_backup=create_backup,
        backup_fail_detail="the case was not created",
    )
    return _ai_json(result, indent=2)


@mcp.tool()
@_tool_guard
def create_attribute_type(name: str, applies_to: str,
                          value_type: str = "character",
                          memo: Optional[str] = None,
                          create_backup: bool = True) -> str:
    """Define a new attribute for cases, files or journals.

    THIS WRITES TO THE DATABASE. Attributes are typed variables attached
    to every entity of one domain (e.g. a case attribute "Age" gives
    every case an Age cell). Creating one also back-fills an empty
    placeholder row for every EXISTING entity of that domain, exactly as
    QualCoder does — an unset attribute is the empty string, never a
    missing row.

    Attribute names are GLOBAL across all three domains: a case
    attribute and a file attribute can never share a name. The Ref_*
    names (Ref_Type, Ref_Author, Ref_Authors, Ref_Title, Ref_Year,
    Ref_Journal) are reserved for QualCoder's reference importer.

    Refused while QualCoder has the project open (heartbeat lock): ask
    the user to close the project in QualCoder, re-check with
    get_current_project (qualcoder_open must be false), then retry. The lock gate detects released QualCoder (3.x) only: QualCoder 4.0 builds no longer use a lock file, so 4.0 detection is best-effort heuristics (qualcoder_gui_signals in get_current_project); never write while any QualCoder window has this project open.

    Args:
        name: The attribute name (unique across ALL domains)
        applies_to: 'case', 'file' or 'journal' (QualCoder's real domain
                    set — there is no 'both')
        value_type: 'character' (default) or 'numeric'. Numeric values
                    are stored as text but validated and compared as
                    numbers. There is no path back from numeric data to
                    character-only in this server, so choose carefully.
        memo: Optional description of what the attribute captures
        create_backup: Create a timestamped backup before writing (default True)

    Example:
        "Add a numeric Age attribute for cases"
    """
    owner = _default_owner()
    result = _perform_write(
        lambda wdb: {"success": True,
                     "message": f"Created {applies_to} attribute "
                                f"'{name.strip() if isinstance(name, str) else name}'",
                     "attribute_type": wdb.add_attribute_type(
                         name, owner, applies_to, value_type=value_type,
                         memo=memo, auto_commit=False)},
        create_backup=create_backup,
        backup_fail_detail="the attribute was not created",
    )
    if "error" not in result:
        result["note"] = (
            f"{result['attribute_type']['placeholders_created']} existing "
            f"{applies_to}(s) received an empty placeholder value — set "
            f"real values with set_attribute."
        )
    return json.dumps(result, indent=2)


@mcp.tool()
@_tool_guard
def set_attribute(target_type: str, target_id: int, attribute_name: str,
                  value: str, create_backup: bool = True) -> str:
    """Set (or clear) an attribute value on a case, file or journal.

    THIS WRITES TO THE DATABASE. The attribute must already exist (see
    create_attribute_type and list_attribute_types) and must belong to
    the target's domain — a case attribute cannot be set on a file.
    Pass value="" to unset: QualCoder represents "no value" as an empty
    cell, the row itself always remains.

    Numeric attributes require a number ("30", "4.5", "1e3"): a
    non-numeric value is refused with an error. (QualCoder's own GUI
    silently blanks invalid numeric input — this server refuses instead,
    so nothing is lost without the user knowing.)

    Refused while QualCoder has the project open (heartbeat lock): ask
    the user to close the project in QualCoder, re-check with
    get_current_project (qualcoder_open must be false), then retry. The lock gate detects released QualCoder (3.x) only: QualCoder 4.0 builds no longer use a lock file, so 4.0 detection is best-effort heuristics (qualcoder_gui_signals in get_current_project); never write while any QualCoder window has this project open.

    Args:
        target_type: 'case', 'file' or 'journal'
        target_id: The case ID, file ID or journal ID
        attribute_name: The attribute to set (exact name; see
                        list_attribute_types)
        value: The value as a string ("" clears/unsets)
        create_backup: Create a timestamped backup before writing (default True)

    Example:
        "Set Age to 34 for case 2"
    """
    owner = _default_owner()
    result = _perform_write(
        lambda wdb: {"success": True,
                     "message": f"Set '{attribute_name}' on {target_type} "
                                f"{target_id}",
                     "attribute": wdb.set_attribute_value(
                         target_type, target_id, attribute_name, value,
                         owner, auto_commit=False)},
        create_backup=create_backup,
        backup_fail_detail="the attribute value was not changed",
    )
    return json.dumps(result, indent=2)


# ============================================================================
# REPORT EXPORTS (v0.8 phase B) — file artefacts with QualCoder-parity shapes
# ============================================================================

def _resolve_export_path(output_path: str, suffix: str, default_name: str,
                         overwrite: bool):
    """Resolve and validate an export path (export_refi_qda posture).

    Accepts a full file path (required suffix, parent must exist, refuse
    existing unless overwrite) or an existing DIRECTORY — then QualCoder's
    own convention applies: the report's default filename, with collision
    suffixes _0, _1, … appended before the extension (helpers.py:147-150).
    Always refuses to write inside the project folder.

    Returns (Path, None) on success or (None, error_dict).
    """
    try:
        out_file = Path(output_path).expanduser().resolve()
    except (OSError, RuntimeError):
        return None, {"error": "Invalid output path"}
    if out_file.is_dir():
        candidate = out_file / default_name
        stem, ext = candidate.stem, candidate.suffix
        counter = 0
        while candidate.exists():
            candidate = out_file / f"{stem}_{counter}{ext}"
            counter += 1
            if counter > 999:
                return None, {"error": "Too many existing exports with "
                                       "this name — clean up or give a "
                                       "full file path"}
        # SEC P-1: the join above tacks a fresh final component onto the
        # already-resolved directory, so it is NOT itself resolved — a
        # dangling/traversing symlink named like the export file
        # (candidate.exists() stat-follows and returns False for a dangling
        # link, so the uniquify loop is skipped) would leave the symlink
        # path un-collapsed and slip the containment guard below, which only
        # tests lexical parents; open() would then follow it, e.g. into the
        # project folder. Resolve the final candidate so the guard sees the
        # real target — mirroring the file branch, which resolves at 5623.
        try:
            out_file = candidate.resolve()
        except (OSError, RuntimeError):
            return None, {"error": "Invalid output path"}
    else:
        if out_file.suffix.lower() != suffix:
            return None, {"error": f"output_path must end in {suffix} "
                                   f"(or be an existing directory)"}
        if not out_file.parent.is_dir():
            return None, {
                "error": "The output directory does not exist — create it "
                         "first or choose an existing folder "
                         "(e.g. ~/Documents)"
            }
        if out_file.exists() and not overwrite:
            return None, {
                "error": f"'{out_file.name}' already exists. Pass "
                         f"overwrite=true to replace it."
            }
    project_folder = validate_qda_path(current_project_path).parent
    if project_folder in out_file.parents or out_file.parent == project_folder:
        return None, {
            "error": "Refusing to write the export inside the project "
                     "folder — choose a location outside it."
        }
    return out_file, None


# CSV formula/DDE injection triggers (SEC V8-1, CWE-1236): a cell whose
# text begins with one of these is evaluated as a formula by Excel /
# LibreOffice / Google Sheets — CSV quoting does NOT prevent it.
_FORMULA_TRIGGER_CHARS = ("=", "+", "-", "@", "\t", "\r")


def _defuse_formula_cell(value):
    """OWASP CSV-injection defusal: prefix a single quote so the
    spreadsheet treats the cell as text. Applied to every string cell
    (DB-derived names, memos, seltext, coder names — and headers built
    from them) when sanitize_formulas is on."""
    if isinstance(value, str) and value.startswith(_FORMULA_TRIGGER_CHARS):
        return f"'{value}"
    return value


def _write_csv_file(out_file: Path, rows, quote_all: bool,
                    sanitize: bool = False):
    """QualCoder CSV conventions: utf-8-sig (BOM), CRLF rows; QUOTE_ALL
    for the coded report (report_codes.py:877-881), minimal otherwise.
    sanitize=True applies the V8-1 formula defusal to every cell."""
    import csv
    with open(out_file, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(
            fh, delimiter=",", quotechar='"',
            quoting=csv.QUOTE_ALL if quote_all else csv.QUOTE_MINIMAL)
        for row in rows:
            if sanitize:
                row = [_defuse_formula_cell(cell) for cell in row]
            writer.writerow(row)


def _sanitization_note(sanitize: bool, is_csv: bool = True) -> str:
    """The disclosure every export result carries about V8-1 mode."""
    if not is_csv:
        return ("sanitize_formulas applies to CSV cells only — this "
                "format has no spreadsheet cells")
    if sanitize:
        return ("formulas sanitized for spreadsheet safety: cells starting "
                "with = + - @ tab or CR are prefixed with ' (this "
                "deliberately breaks byte-parity with QualCoder's own "
                "export)")
    return ("verbatim export — cells starting with = are evaluated by "
            "Excel; pass sanitize_formulas=true to neutralize")


def _resolve_names_ci(requested, available, kind: str):
    """Resolve names against a live list: exact match wins, else unique
    case-insensitive match; ambiguous or missing -> (None, error_dict)."""
    by_exact = {a["name"]: a for a in available}
    resolved = []
    for name in requested:
        item = by_exact.get(name)
        if item is None:
            ci = [a for a in available
                  if a["name"].lower() == str(name).lower()]
            if len(ci) == 1:
                item = ci[0]
            elif len(ci) > 1:
                return None, {
                    "error": f"{kind} name '{name}' is ambiguous "
                             f"(case-insensitive matches: "
                             f"{sorted(a['name'] for a in ci)}) — use the "
                             f"exact name"
                }
            else:
                return None, {
                    "error": f"{kind} '{name}' not found",
                    f"available_{kind.lower()}s":
                        sorted(a["name"] for a in available)[:50],
                }
        resolved.append(item)
    return resolved, None


def _codebook_tree(ro_db):
    """Depth-first codebook walk: at each level, sub-categories and codes
    together, sorted case-insensitively by name (the GUI tree's resting
    order). Yields (depth, kind, item) with kind 'category'|'code'.

    Sub-codes (S7, v16+): after a code, its sub-codes are yielded one
    level deeper, exactly as master's codebook re-parents them before
    the walk (codebook.py:54-101). A top-level code has neither a
    category nor a parent code."""
    cats = ro_db.list_categories()
    codes = ro_db.list_codes()
    child_cats: Dict[Any, list] = {}
    for c in cats:
        child_cats.setdefault(c["parent_id"], []).append(c)
    code_children: Dict[Any, list] = {}
    cat_codes: Dict[Any, list] = {}
    for c in codes:
        parent_code = c.get("parent_code_id")
        if parent_code is not None:
            code_children.setdefault(parent_code, []).append(c)
        else:
            cat_codes.setdefault(c["category_id"], []).append(c)

    def walk_code(code, depth):
        yield depth, "code", code
        for sub in sorted(code_children.get(code["id"], []),
                          key=lambda s: s["name"].lower()):
            yield from walk_code(sub, depth + 1)

    def walk(parent_id, depth):
        children = ([("category", c) for c in child_cats.get(parent_id, [])]
                    + [("code", c) for c in cat_codes.get(parent_id, [])])
        for kind, item in sorted(children,
                                 key=lambda kc: kc[1]["name"].lower()):
            if kind == "category":
                yield depth, kind, item
                yield from walk(item["id"], depth + 1)
            else:
                yield from walk_code(item, depth)

    yield from walk(None, 0)


def _category_chain(cat_by_id, category_id):
    """Category names, immediate parent first, up to the root (the
    category leg of the coded-report chain)."""
    chain = []
    seen = set()
    current = category_id
    while current is not None and current not in seen:
        seen.add(current)
        cat = cat_by_id.get(current)
        if cat is None:
            break
        chain.append(cat["name"])
        current = cat["parent_id"]
    return chain


def _code_report_chain(cat_by_id, code_by_id, cid):
    """The coded-report Category-column chain for one code, following
    master's categories_of_code (report_codes.py:1131-1175): parent CODE
    names first (immediate parent upward), then the category lineage of
    the TOP ancestor code, leaf to root. On v14/v15 rows (no parent
    code info) this reduces to the plain category chain."""
    path_codes = []
    seen = set()
    current = cid
    while current in code_by_id and current not in seen:
        seen.add(current)
        parent = code_by_id[current].get("parent_code_id")
        if parent is None or parent not in code_by_id:
            break
        path_codes.append(code_by_id[parent]["name"])
        current = parent
    top_catid = code_by_id.get(current, {}).get("category_id")
    return path_codes + _category_chain(cat_by_id, top_catid)


@mcp.tool()
@_tool_guard
def export_codebook(output_path: str, format: str = "csv",
                    include_memos: bool = True,
                    sanitize_formulas: bool = False,
                    overwrite: bool = False) -> str:
    """Export the full codebook (codes + category tree) to a file.

    Read-only. Content mirrors QualCoder's own Codebook export: the tree
    in depth order, each code with its colour and its coding count —
    counted exactly as QualCoder counts it (text + image + A/V codings,
    all coders, no filters, orphaned codings included).

    Full memos on export (owner-ruled parity with QualCoder's own
    exports): the exported FILE keeps memo text in full, including
    any private '#####' section that read tools never show the AI.
    Mention this to the user if they plan to share the exported
    file.

    Formats:
    - "csv": flat table `Tree, Id, Type, Color, Count[, Memo]` — the
      Tree cell carries the depth prefix (`...` per level, QualCoder's
      codebook convention), Id is `catid:N`/`cid:N`.
    - "txt": QualCoder's Codebook text shape (`...Category: X` /
      `...Code: Y, Count: N`, `MEMO:` lines when include_memos).
    - "md": Markdown — headings per category depth, codes as bullets.
    All files are UTF-8 with BOM (QualCoder's export encoding).

    Args:
        output_path: Target file (matching extension), or an existing
                     directory — then the default name `Codebook.csv`
                     etc. is used, with `_0`, `_1` collision suffixes
        format: "csv" (default), "txt" or "md"
        include_memos: Include code/category memos (default True)
        sanitize_formulas: Neutralize spreadsheet formula injection in
            CSV cells (values starting with = + - @ tab or CR get a '
            prefix). Default False = byte-parity with QualCoder's own
            export; one word turns on safety when the data may contain
            untrusted text.
        overwrite: Allow replacing an existing file (default False)

    Returns:
        JSON with output_path, counts, the counting rule used, and
        which sanitization mode was applied.
    """
    if format not in ("csv", "txt", "md"):
        return json.dumps({"error": "format must be 'csv', 'txt' or 'md'"})
    suffix = f".{format}"
    out_file, err = _resolve_export_path(
        output_path, suffix, f"Codebook{suffix}", overwrite)
    if err:
        return json.dumps(err)

    ro_db = get_db()
    freq = ro_db.get_codebook_frequencies()
    project = Path(current_project_path).stem
    n_codes = n_cats = 0

    if format == "csv":
        header = ["Tree", "Id", "Type", "Color", "Count"]
        if include_memos:
            header.append("Memo")
        rows = [header]
        for depth, kind, item in _codebook_tree(ro_db):
            prefix = "..." * depth
            if kind == "category":
                n_cats += 1
                row = [f"{prefix}{item['name']}", f"catid:{item['id']}",
                       "category", "", ""]
            else:
                n_codes += 1
                row = [f"{prefix}{item['name']}", f"cid:{item['id']}",
                       "code", item["color"] or "",
                       str(freq.get(item["id"], 0))]
            if include_memos:
                row.append(item.get("memo") or "")
            rows.append(row)
        _write_csv_file(out_file, rows, quote_all=False,
                        sanitize=sanitize_formulas)
    else:
        lines = [f"Codebook: {project}", ""]
        for depth, kind, item in _codebook_tree(ro_db):
            memo = item.get("memo") or ""
            if format == "txt":
                prefix = "..." * depth
                if kind == "category":
                    n_cats += 1
                    lines.append(f"{prefix}Category: {item['name']}")
                else:
                    n_codes += 1
                    lines.append(f"{prefix}Code: {item['name']}, "
                                 f"Count: {freq.get(item['id'], 0)}")
                if include_memos and memo:
                    lines.append(f"{prefix}MEMO: {memo}")
            else:  # md
                if kind == "category":
                    n_cats += 1
                    lines.append(f"{'#' * min(depth + 2, 6)} {item['name']}")
                    if include_memos and memo:
                        lines.append(f"> {memo}")
                    lines.append("")
                else:
                    n_codes += 1
                    color = f" `{item['color']}`" if item.get("color") else ""
                    lines.append(f"- **{item['name']}**{color} — "
                                 f"{freq.get(item['id'], 0)} coding(s)")
                    if include_memos and memo:
                        lines.append(f"  > {memo}")
        if format == "md":
            lines.insert(0, f"# Codebook: {project}")
            del lines[1]
        with open(out_file, "w", encoding="utf-8-sig") as fh:
            fh.write("\n".join(lines) + "\n")

    return json.dumps({
        "success": True,
        "output_path": str(out_file),
        "format": format,
        "codes": n_codes,
        "categories": n_cats,
        "counting_rule": "QualCoder Codebook parity: text + image + A/V "
                         "codings, all coders, no filters, orphaned "
                         "codings included",
        "sanitization": _sanitization_note(sanitize_formulas,
                                           is_csv=(format == "csv")),
    }, indent=2)


@mcp.tool()
@_tool_guard
def export_coded_segments_report(
    output_path: str,
    code_names: Optional[List[str]] = None,
    case_names: Optional[List[str]] = None,
    coder: str = "",
    file_ids: Optional[List[int]] = None,
    search_text: Optional[str] = None,
    important: bool = False,
    include_variables: bool = False,
    format: str = "csv",
    sanitize_formulas: bool = False,
    overwrite: bool = False,
) -> str:
    """Export the coded-segments-with-quotes report (QualCoder's Coding
    Report) to a file.

    Read-only. Rows, columns and ordering mirror QualCoder's own
    File > Reports > Coding report export exactly:
    - CSV columns (file mode): `File, Coder, Coded, Id, Codename,
      Coded_Memo` then `Category` × N (the code's category chain,
      immediate parent first, padded to the deepest chain).
      Case mode: `Case, Filename, Coder, Coded, ...`.
      `Id` is `ctid:N`. UTF-8 with BOM, every cell quoted, CRLF rows —
      the exact dialect QualCoder writes.
    - txt: the on-screen report serialization (Search parameters header,
      then `[pos0-pos1] Codename, File: ..., Coder: ...` headings with
      the quoted text).
    - Case mode uses the CONTAINMENT rule (a coding belongs to a case
      iff fully inside one of the case's text spans) — the rule
      QualCoder's coding report uses, stated in the response because the
      GUI ships a second, different rule elsewhere.
    - Text codings only; image/AV codings are not included (disclosed).

    Full memos on export (owner-ruled parity with QualCoder's own
    exports): the exported FILE keeps memo text in full, including
    any private '#####' section that read tools never show the AI.
    Mention this to the user if they plan to share the exported
    file.

    Args:
        output_path: Target file, or an existing directory (default name
                     `Coded_segments.csv`/`.txt`, `_0` collision suffixes)
        code_names: Codes to include (default: all). Exact name wins,
                    else unique case-insensitive match.
        case_names: Switch to CASE mode and filter to these cases
        coder: Exact coder name (default "" = all coders — exact match,
               never a substring, like QualCoder)
        file_ids: Restrict to these files
        search_text: Only segments whose text contains this substring
        important: Only segments flagged important
        include_variables: Append `FileVar_{name}` columns (and, in case
                           mode, `CaseVar_{name}`) with attribute values
                           per row — QualCoder's "variables" checkbox
        format: "csv" (default) or "txt"
        sanitize_formulas: Neutralize spreadsheet formula injection in
            CSV cells (values starting with = + - @ tab or CR get a '
            prefix — coded seltext is untrusted source text and the
            sharpest vector). Default False = byte-parity with
            QualCoder's own export; one word turns on safety.
        overwrite: Allow replacing an existing file (default False)

    Returns:
        JSON with output_path, row count, the filters applied, and the
        counting rule + disclosures.
    """
    if format not in ("csv", "txt"):
        return json.dumps({"error": "format must be 'csv' or 'txt'"})
    ro_db = get_db()

    all_codes = ro_db.list_codes()
    code_ids = None
    if code_names:
        resolved, err = _resolve_names_ci(code_names, all_codes, "Code")
        if err:
            return json.dumps(err)
        code_ids = [c["id"] for c in resolved]
    case_ids = None
    case_mode = bool(case_names)
    if case_mode:
        all_cases = ro_db.list_cases()
        resolved, err = _resolve_names_ci(case_names, all_cases, "Case")
        if err:
            return json.dumps(err)
        case_ids = [c["id"] for c in resolved]

    suffix = f".{format}"
    out_file, err = _resolve_export_path(
        output_path, suffix, f"Coded_segments{suffix}", overwrite)
    if err:
        return json.dumps(err)

    rows = ro_db.get_coding_report_rows(
        code_ids=code_ids, file_ids=file_ids, case_ids=case_ids,
        coder=coder or "", search_text=search_text or "",
        important=important)

    cats = ro_db.list_categories()
    cat_by_id = {c["id"]: c for c in cats}
    code_by_id = {c["id"]: c for c in all_codes}
    result_cids = {r["cid"] for r in rows}
    # S8: chain = parent code names first, then the top ancestor's
    # category lineage (master's categories_of_code)
    chains = {cid: _code_report_chain(cat_by_id, code_by_id, cid)
              for cid in result_cids}
    max_depth = max((len(ch) for ch in chains.values()), default=0)

    if format == "csv":
        if case_mode:
            header = ["Case", "Filename", "Coder", "Coded", "Id",
                      "Codename", "Coded_Memo"]
        else:
            header = ["File", "Coder", "Coded", "Id", "Codename",
                      "Coded_Memo"]
        header += ["Category"] * max_depth
        file_vars = case_vars = []
        file_attr_cache: Dict[int, Dict[str, str]] = {}
        case_attr_cache: Dict[int, Dict[str, str]] = {}
        if include_variables:
            attr_types = ro_db.list_attribute_types()
            file_vars = sorted(a["name"] for a in attr_types
                               if a["applies_to"] == "file")
            header += [f"FileVar_{n}" for n in file_vars]
            if case_mode:
                case_vars = sorted(a["name"] for a in attr_types
                                   if a["applies_to"] == "case")
                header += [f"CaseVar_{n}" for n in case_vars]
        out_rows = [header]
        for r in rows:
            if case_mode:
                row = [r["casename"], r["filename"], r["owner"],
                       r["seltext"] or "", f"ctid:{r['ctid']}",
                       r["codename"], r["coded_memo"]]
            else:
                row = [r["filename"], r["owner"], r["seltext"] or "",
                       f"ctid:{r['ctid']}", r["codename"], r["coded_memo"]]
            chain = chains.get(r["cid"], [])
            row += chain + [""] * (max_depth - len(chain))
            if include_variables:
                fid = r["fid"]
                if fid not in file_attr_cache:
                    file_attr_cache[fid] = {
                        a["name"]: (a["value"] or "")
                        for a in ro_db.get_file_attributes(fid)}
                row += [file_attr_cache[fid].get(n, "") for n in file_vars]
                if case_mode:
                    caseid = r["caseid"]
                    if caseid not in case_attr_cache:
                        case_attr_cache[caseid] = {
                            a["name"]: (a["value"] or "")
                            for a in ro_db.get_case_attributes(caseid)}
                    row += [case_attr_cache[caseid].get(n, "")
                            for n in case_vars]
            out_rows.append(row)
        # NOTE: QUOTE_ALL does NOT defuse formulas — Excel evaluates a
        # quoted "=..." cell all the same (SEC V8-1)
        _write_csv_file(out_file, out_rows, quote_all=True,
                        sanitize=sanitize_formulas)
    else:  # txt — the on-screen report serialization
        total_codes = len(all_codes)
        lines = ["Search parameters", "=" * 10]
        lines.append(f"Coding by: {coder}" if coder else
                     "Coding by: All coders")
        lines.append(f"Codes: "
                     f"{len(code_ids) if code_ids else total_codes} / "
                     f"{total_codes}")
        if case_mode:
            lines.append(f"Cases: {len(case_ids)}")
        if file_ids:
            lines.append(f"Files: {len(file_ids)}")
        if search_text:
            lines.append(f"Search text: {search_text}")
        lines.append("=" * 10)
        for r in rows:
            case_part = f"Case: {r['casename']}, " if case_mode else ""
            lines.append("")
            lines.append(f"[{r['pos0']}-{r['pos1']}] {r['codename']}, "
                         f"File: {r['filename']}, {case_part}"
                         f"Coder: {r['owner']}")
            lines.append(r["seltext"] or "")
        with open(out_file, "w", encoding="utf-8-sig") as fh:
            fh.write("\n".join(lines) + "\n")

    result = {
        "success": True,
        "output_path": str(out_file),
        "format": format,
        "rows": len(rows),
        "mode": "case" if case_mode else "file",
        "filters": {
            "codes": code_names or "all",
            "cases": case_names or None,
            "coder": coder or "all coders (exact-match filter available)",
            "file_ids": file_ids or "all",
            "search_text": search_text,
            "important_only": important,
        },
        "disclosures": [
            "Text codings only — image and A/V codings are not included",
            "Codings on deleted files are excluded (source join), exactly "
            "as QualCoder's report",
        ],
        "sanitization": _sanitization_note(sanitize_formulas,
                                           is_csv=(format == "csv")),
    }
    if case_mode:
        result["counting_rule"] = (
            "CONTAINMENT: a coding belongs to a case iff its span lies "
            "fully inside one of the case's text spans on the same file "
            "(QualCoder coding-report rule). Note QualCoder's comparison "
            "table uses a different rule (file linkage); whole-file links "
            "created by different QualCoder dialogs end at len-1 or len, "
            "which can exclude a coding touching the file end."
        )
    return json.dumps(result, indent=2)


@mcp.tool()
@_tool_guard
def export_frequencies_csv(output_path: str,
                           sanitize_formulas: bool = False,
                           overwrite: bool = False) -> str:
    """Export the code-frequencies table (QualCoder's Code Frequencies
    report) as CSV.

    Read-only. Numbers match QualCoder's report EXACTLY: one count per
    coding row across ALL THREE media tables (text, image, A/V), one
    column per coder plus Total, category rows carrying recursive
    subtree totals, and — like QualCoder — codings whose file was
    deleted still count.

    DIVERGENCE NOTE (disclosed here and in the response): the
    conversational get_coding_frequencies tool counts TEXT codings on
    existing files only, so its numbers can be lower than this export.
    This export is the QualCoder-parity artefact.

    Columns: `Code Tree, Id, {coder…}, Total` (coders alphabetical).
    Tree rows in depth order with `--` per level in the Code Tree cell
    (QualCoder's text-export convention, kept so hierarchy survives a
    flat CSV). UTF-8 with BOM, CRLF rows.

    Args:
        output_path: Target .csv file, or an existing directory (default
                     name `Code_frequencies.csv`, `_0` suffixes)
        sanitize_formulas: Neutralize spreadsheet formula injection
            (cells starting with = + - @ tab or CR get a ' prefix —
            code and coder names are DB-derived text). Default False =
            byte-parity with QualCoder; one word turns on safety.
        overwrite: Allow replacing an existing file (default False)
    """
    out_file, err = _resolve_export_path(
        output_path, ".csv", "Code_frequencies.csv", overwrite)
    if err:
        return json.dumps(err)

    ro_db = get_db()
    raw = ro_db.get_raw_coding_counts()
    coders = sorted({r["owner"] for r in raw if r["owner"] is not None})
    counts: Dict[Any, int] = {}
    for r in raw:
        counts[(r["code_id"], r["owner"])] = r["count"]

    cats = ro_db.list_categories()
    codes = ro_db.list_codes()
    child_cats: Dict[Any, list] = {}
    for c in cats:
        child_cats.setdefault(c["parent_id"], []).append(c)
    # Sub-codes (S6/S7, v16+): nest under their parent CODE in the tree
    # and attribute their counts to the top ancestor's category, as
    # master's frequencies report does (reports.py:287-313, 575-622).
    code_children: Dict[Any, list] = {}
    cat_codes: Dict[Any, list] = {}
    for c in codes:
        parent_code = c.get("parent_code_id")
        if parent_code is not None:
            code_children.setdefault(parent_code, []).append(c)
        else:
            cat_codes.setdefault(c["category_id"], []).append(c)

    def code_row_counts(cid):
        per = [counts.get((cid, coder), 0) for coder in coders]
        return per, sum(per)

    def code_branch_counts(cid):
        """This code plus all its sub-code descendants, per coder."""
        per, _total = code_row_counts(cid)
        for sub in code_children.get(cid, []):
            sper = code_branch_counts(sub["id"])
            per = [a + b for a, b in zip(per, sper)]
        return per

    def subtree_counts(catid):
        per = [0] * len(coders)
        for c in cat_codes.get(catid, []):
            cper = code_branch_counts(c["id"])
            per = [a + b for a, b in zip(per, cper)]
        for sub in child_cats.get(catid, []):
            sper = subtree_counts(sub["id"])
            per = [a + b for a, b in zip(per, sper)]
        return per

    rows = [["Code Tree", "Id"] + coders + ["Total"]]

    def emit_code(item, depth):
        prefix = "--" * depth
        per, total = code_row_counts(item["id"])
        rows.append([f"{prefix}{item['name']}",
                     f"cid:{item['id']}"]
                    + [str(n) for n in per] + [str(total)])
        for sub in sorted(code_children.get(item["id"], []),
                          key=lambda s: s["name"].lower()):
            emit_code(sub, depth + 1)

    def walk(parent_id, depth):
        children = ([("category", c) for c in child_cats.get(parent_id, [])]
                    + [("code", c) for c in cat_codes.get(parent_id, [])])
        for kind, item in sorted(children,
                                 key=lambda kc: kc[1]["name"].lower()):
            prefix = "--" * depth
            if kind == "category":
                per = subtree_counts(item["id"])
                rows.append([f"{prefix}{item['name']}",
                             f"catid:{item['id']}"]
                            + [str(n) for n in per] + [str(sum(per))])
                walk(item["id"], depth + 1)
            else:
                emit_code(item, depth)

    walk(None, 0)
    _write_csv_file(out_file, rows, quote_all=False,
                    sanitize=sanitize_formulas)

    return json.dumps({
        "success": True,
        "output_path": str(out_file),
        "codes": len(codes),
        "categories": len(cats),
        "coders": coders,
        "sanitization": _sanitization_note(sanitize_formulas),
        "counting_rule": "QualCoder Code Frequencies parity: one count "
                         "per coding row over code_text + code_image + "
                         "code_av, per coder; category rows are recursive "
                         "subtree totals; orphaned codings included",
        "divergence_note": "get_coding_frequencies (the conversational "
                           "tool) counts text codings on existing files "
                           "only — its numbers can be lower than this "
                           "QualCoder-parity export.",
    }, indent=2)


@mcp.tool()
@_tool_guard
def export_case_code_matrix_csv(output_path: str,
                                sanitize_formulas: bool = False,
                                overwrite: bool = False) -> str:
    """Export the case × code cross-tab as CSV.

    Read-only. Rows are cases, columns are codes, cells are the number
    of coded text segments of that code contained in that case. Uses the
    CONTAINMENT rule — the same rule as get_case_code_matrix and
    QualCoder's coding report: a coding counts for a case iff its span
    lies fully inside one of the case's text spans on the same file.
    The rule is stated in the response because QualCoder itself ships a
    SECOND, different rule (its comparison table counts by file linkage,
    including codings outside the case's spans) — matrices from the two
    rules are not comparable.

    No totals row/column (parity with QualCoder's matrix exports).
    UTF-8 with BOM, CRLF rows.

    Args:
        output_path: Target .csv file, or an existing directory (default
                     name `Case_code_matrix.csv`, `_0` suffixes)
        sanitize_formulas: Neutralize spreadsheet formula injection
            (cells starting with = + - @ tab or CR get a ' prefix —
            case and code names are DB-derived text). Default False =
            byte-parity with QualCoder; one word turns on safety.
        overwrite: Allow replacing an existing file (default False)
    """
    out_file, err = _resolve_export_path(
        output_path, ".csv", "Case_code_matrix.csv", overwrite)
    if err:
        return json.dumps(err)

    # P1-3: file exports read BASE tables (QualCoder's own reports do
    # not filter by coder visibility)
    data = get_db().get_case_code_matrix(honor_visibility=False)
    codes = data["codes"]
    rows = [["Case"] + [c["name"] for c in codes]]
    for case in data["cases"]:
        cells = data["matrix"].get(case["id"], {})
        rows.append([case["name"]]
                    + [str(cells.get(c["id"], 0)) for c in codes])
    _write_csv_file(out_file, rows, quote_all=False,
                    sanitize=sanitize_formulas)

    return json.dumps({
        "success": True,
        "output_path": str(out_file),
        "cases": len(data["cases"]),
        "codes": len(codes),
        "sanitization": _sanitization_note(sanitize_formulas),
        "counting_rule": "CONTAINMENT: a coding counts for a case iff "
                         "fully inside one of the case's text spans on "
                         "the same file (QualCoder coding-report rule; "
                         "text codings on existing files). QualCoder's "
                         "comparison table uses file-linkage counting "
                         "instead — its numbers will differ.",
    }, indent=2)


# ============================================================================
# PROMPTS - Interaction templates
# ============================================================================

@mcp.prompt()
def analyze_theme(theme_name: str) -> str:
    """Generate a prompt for analyzing a specific theme or code.

    This prompt template helps analyze patterns and insights
    related to a particular code or theme in the data.

    Args:
        theme_name: The name of the code/theme to analyze
    """
    return f"""Please analyze the theme '{theme_name}' in this Qualcoder project.

Use the following tools to gather information:
1. First, use search_coded_text or list_all_codes to find the code
2. Then use get_coded_segments to retrieve all segments for this code
3. Analyze the segments and identify:
   - Key patterns and recurring ideas
   - Variations in how the theme appears
   - Relationships to other themes
   - Notable quotes or examples

Provide a comprehensive thematic analysis with specific examples from the data."""


@mcp.prompt()
def compare_codes(code1: str, code2: str) -> str:
    """Generate a prompt for comparing two codes.

    This prompt template helps analyze similarities and differences
    between two codes or themes.

    Args:
        code1: Name of the first code
        code2: Name of the second code
    """
    return f"""Please compare and contrast the codes '{code1}' and '{code2}' in this Qualcoder project.

Use these tools to gather data:
1. Use get_coded_segments for both codes
2. Use get_coding_frequencies to compare usage patterns
3. Analyze:
   - How frequently each code is used
   - Similarities in the types of segments they code
   - Differences in meaning and application
   - Any overlaps or relationships between them
   - Which files or cases show each code

Provide a detailed comparison with specific examples from the coded segments."""


@mcp.prompt()
def summarize_project() -> str:
    """Generate a prompt for creating a project overview.

    This prompt template helps create a comprehensive summary
    of the entire Qualcoder project.
    """
    return """Please create a comprehensive summary of this Qualcoder project.

Use the following tools:
1. get_project_summary - for overall statistics
2. list_all_codes - to understand the coding scheme
3. list_all_files - to see what data is included
4. get_coding_frequencies - to identify main themes

Create a summary that includes:
- Project metadata and purpose (from project info)
- Description of the data sources (types and number of files)
- Overview of the coding scheme (categories and main codes)
- Key themes (most frequently used codes)
- Any notable patterns or insights

Format the summary as a clear, well-organized report."""


@mcp.prompt()
def explore_case(case_name: str) -> str:
    """Generate a prompt for exploring a specific case.

    This prompt template helps analyze all data related to
    a particular case or participant.

    Args:
        case_name: The name of the case to explore
    """
    return f"""Please explore and analyze the case '{case_name}' in this Qualcoder project.

Use these tools to gather information:
1. list_all_cases to find the case
2. get_case_info to get all text segments for this case
3. Analyze the case data to identify:
   - Key characteristics or themes for this case
   - What makes this case unique
   - Important quotes or segments
   - How this case relates to the overall study

Provide a detailed case profile with specific examples from the data."""


# ============================================================================
# Main entry point
# ============================================================================

# ============================================================================
# Toolset modes (EXPERIMENTAL): QUALCODER_MCP_TOOLSET=core|full
# ============================================================================
# Local models break on large tool surfaces long before frontier models do:
# tool-selection accuracy collapses as the menu grows, and the full 67-tool
# schema payload alone exceeds default local context windows (see the
# multi-host research dossiers). QUALCODER_MCP_TOOLSET=core registers only
# the supervised-coding-loop subset below; the default remains the full
# surface (backward compatible). Required for local models, optional
# elsewhere. Resources and prompts are unaffected.

CORE_TOOLSET = frozenset({
    # project open/select
    "list_available_projects", "select_project", "get_current_project",
    "get_project_summary",
    # file search and read-with-coding
    "search_files", "analyze_file_with_coding",
    # coded-text retrieval and frequencies
    "search_coded_text", "get_coded_segments", "get_coding_frequencies",
    # the supervised suggestion loop
    "analyze_for_coding", "record_suggestions", "review_suggestions",
    "edit_suggestion", "update_suggestion_status", "apply_codings",
    # minimal codebook/memo writes a coding session needs
    "create_code", "set_memo",
    # the safety pair: workspace isolation and undo
    "copy_project_to_workspace", "delete_coding", "list_backups",
})

_VALID_TOOLSET_MODES = ("full", "core")


def _resolve_toolset_mode() -> str:
    """Read QUALCODER_MCP_TOOLSET (default full); unknown values raise."""
    raw = os.environ.get("QUALCODER_MCP_TOOLSET", "full").strip().lower()
    if raw not in _VALID_TOOLSET_MODES:
        raise ValueError(
            f"Unknown QUALCODER_MCP_TOOLSET value {raw!r}: valid values "
            f"are 'full' (default, all tools) and 'core' (the reduced "
            f"supervised-coding set for local models)."
        )
    return raw


def _apply_toolset(mode: str) -> Dict[str, Any]:
    """Restrict the registered tool surface to the requested mode.

    Returns the removed tools keyed by name so tests can restore them.
    Idempotent for mode='full' (removes nothing).
    """
    removed: Dict[str, Any] = {}
    if mode == "core":
        for name in sorted(mcp._tool_manager._tools.keys()):
            if name not in CORE_TOOLSET:
                removed[name] = mcp._tool_manager._tools[name]
                mcp.remove_tool(name)
    active = len(mcp._tool_manager._tools)
    logger.info(f"Toolset mode: {mode} ({active} tools registered)")
    return removed


def main():
    """Main entry point for the MCP server."""
    # Check for optional pre-configured project (Option B: Fixed Project)
    # EXPERIMENTAL: reduced tool surface for local-model hosts. Fail
    # loudly on unknown values; a silent fallback would give a researcher
    # the wrong tool surface without their knowledge.
    try:
        toolset_mode = _resolve_toolset_mode()
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    _apply_toolset(toolset_mode)

    # P1-2: validate the configured AI coder name up front. Refusing to
    # start beats writing rows under a broken or unintended owner string.
    try:
        _ai_coder_name()
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    db_path = os.environ.get("QUALCODER_PROJECT_PATH")

    if db_path:
        # Option B: Fixed project path provided
        if not Path(db_path).exists():
            print(f"Error: Database file not found: {db_path}", file=sys.stderr)
            sys.exit(1)
        logger.info(f"Starting Qualcoder MCP server with pre-configured project: {Path(db_path).name}")
    else:
        # Option A: Dynamic project selection
        logger.info("Starting Qualcoder MCP server in dynamic mode (no project pre-configured)")
        logger.info("Use 'list_available_projects' and 'select_project' to open a project")

    # Run the server using stdio transport
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
