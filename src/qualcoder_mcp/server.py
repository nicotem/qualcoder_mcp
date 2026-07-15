"""Qualcoder MCP Server - Expose Qualcoder data via Model Context Protocol."""

import os
import sys
import json
import shutil
import logging
import sqlite3
import functools
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp import Context

from .database import (
    QualcoderDatabase,
    DatabaseLockedError,
    UnsupportedSchemaError,
    DB_LOCKED_MESSAGE,
    validate_qda_path,
    backup_project,
    qualcoder_lock_state,
    qualcoder_open_message,
    hold_project_lock,
    QUALCODER_LOCK_FILENAME,
    position_safe as db_position_safe,
)
from .sessions import SessionManager, AICodingSession, CodingSuggestion

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize MCP server
mcp = FastMCP("Qualcoder")

# Global database instance and current project path
db: Optional[QualcoderDatabase] = None
current_project_path: Optional[str] = None

# Global session manager for AI coding
session_manager = SessionManager()


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
            return json.dumps({
                "error": "Database error — the project file may be locked or "
                         "corrupted. If QualCoder is open, close it and retry; "
                         "otherwise consider restoring a backup (see list_backups)."
            })
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
            raise ValueError(
                "No Qualcoder project selected. Use 'list_available_projects' "
                "to discover projects, then 'select_project' to choose one. "
                "Or set QUALCODER_PROJECT_PATH environment variable."
            )

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


def _write_gate_error() -> Optional[Dict[str, Any]]:
    """Combined pre-write gate: schema version + QualCoder lock file.

    Returns an error dict when the project's schema is older than v14
    (writes are only supported against the tested schema) or when
    QualCoder currently has the project open. The database layer enforces
    the same schema gate in _require_write_access (defense in depth); the
    early check here produces a clean error before any backup is made.
    """
    version = getattr(get_db(), "db_version", None)
    if version != "v14":
        return {
            "error": f"This project uses database schema "
                     f"{version or 'unknown'}; writes require schema v14. "
                     f"Open and save the project in QualCoder 3.8 to upgrade "
                     f"it, then try again."
        }
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
                     "and 'select_project' to open one."
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


# ============================================================================
# RESOURCES - Read-only data access
# ============================================================================

@mcp.resource("qualcoder://project/info")
def get_project_info() -> str:
    """Get information about the current Qualcoder project.

    Returns project metadata including version, date, coder name, and memo.
    """
    info = get_db().get_project_info()
    return json.dumps(info, indent=2)


@mcp.resource("qualcoder://codes/list")
def list_all_codes() -> str:
    """Get a list of all codes in the project.

    Returns all codes with their names, categories, colors, memos, and metadata.
    Codes are organized hierarchically by category.
    """
    codes = get_db().list_codes()
    return json.dumps(codes, indent=2)


@mcp.resource("qualcoder://categories/list")
def list_all_categories() -> str:
    """Get a list of all code categories.

    Returns all categories with their hierarchical structure (parent-child relationships).
    """
    categories = get_db().list_categories()
    return json.dumps(categories, indent=2)


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
    return json.dumps(code, indent=2)


@mcp.resource("qualcoder://files/list")
def list_all_files() -> str:
    """Get a list of all source files in the project.

    Returns all files (text documents, images, audio, video) with their
    metadata, type, and memo information.
    """
    files = get_db().list_files()
    return json.dumps(files, indent=2)


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
    return json.dumps(file_data, indent=2)


@mcp.resource("qualcoder://cases/list")
def list_all_cases() -> str:
    """Get a list of all cases in the project.

    Returns all cases (participants, subjects) with their metadata and
    count of associated text segments.
    """
    cases = get_db().list_cases()
    return json.dumps(cases, indent=2)


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
    return json.dumps(case, indent=2)


@mcp.resource("qualcoder://journal")
def get_journal_entries() -> str:
    """Get all journal entries from the project.

    Returns all journal entries ordered by date (most recent first).
    """
    entries = get_db().get_journal_entries()
    return json.dumps(entries, indent=2)


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


@mcp.tool()
@_tool_guard
def select_project(project_path: str) -> str:
    """Switch to a different Qualcoder project.

    Use this tool to change which project you're working with. You can get
    a list of available projects using 'list_available_projects' first.

    Args:
        project_path: Full path to the .qda file you want to open

    Returns:
        JSON with success status and project information
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

        warnings = []

        # Reads are safe while QualCoder is open, but warn: data may change
        # underneath, and writes will be refused until QualCoder closes it
        state, holder = qualcoder_lock_state(_current_project_folder())
        if state == "active":
            warnings.append(
                f"QualCoder currently has this project open (user "
                f"{holder or 'unknown'}). Reads may return changing data, and "
                f"all write operations will be refused until the project is "
                f"closed in QualCoder."
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

        return json.dumps(result, indent=2)

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
        return json.dumps({
            "success": False,
            "error": "Invalid project path or project not found. "
                     "Use 'list_available_projects' to find valid projects."
        })
    except sqlite3.Error as e:
        # e.g. "database disk image is malformed" surfacing mid-read (F3)
        logger.error(f"SQLite error while opening project: {e}")
        return json.dumps({
            "success": False,
            "error": "The project database appears to be damaged or unreadable. "
                     "Try opening it in QualCoder, or restore a backup."
        })
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
                          "and 'select_project' to open one."
            }, indent=2)

        project_info = get_db().get_project_info()

        result = {
            "current_project": current_project_path,
            "project_name": Path(current_project_path).stem,
            "project_info": project_info
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

        return json.dumps(result, indent=2)

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

    The copy is NOT opened automatically — use select_project on the
    returned path when you are ready to work on it.

    Args:
        source_path: Path to the source .qda project (folder or data.qda)
        new_name: Optional new name for the workspace copy

    Returns:
        JSON with the workspace copy's path

    Example:
        "Copy my project 'Interview Study' to the workspace for AI coding"
    """
    from .database import copy_project_to_workspace as copy_to_workspace

    # Validate that the source is a real QualCoder project before copying
    validate_qda_path(source_path)

    dest = copy_to_workspace(source_path, new_name=new_name)

    return json.dumps({
        "success": True,
        "message": f"Copied project to workspace: {dest.name}",
        "workspace_copy": str(dest),
        "original_untouched": True,
        "hint": f"Use select_project(\"{dest}\") to open the copy and work on it."
    }, indent=2)


@mcp.tool()
@_tool_guard
def search_coded_text(query: str, code_name: Optional[str] = None, limit: int = 50) -> str:
    """Search for text segments that contain specific keywords.

    This tool searches through all coded text segments for matching content.
    Useful for finding specific themes, quotes, or concepts in your data.

    Args:
        query: The text to search for (case-insensitive substring match)
        code_name: Optional - filter results to only segments coded with this code
        limit: Maximum number of results to return (default 50)

    Returns:
        JSON array of matching segments with their codes, files, and context
    """
    results = get_db().search_coded_text(query, code_name, limit)
    return json.dumps({
        "query": query,
        "code_filter": code_name,
        "result_count": len(results),
        "results": results
    }, indent=2)


@mcp.tool()
@_tool_guard
def get_coded_segments(code_id: int, limit: int = 100) -> str:
    """Get all text segments that have been coded with a specific code.

    This tool retrieves all the text excerpts that have been assigned
    to a particular code, useful for reviewing themes or categories.

    Args:
        code_id: The numeric ID of the code (cid)
        limit: Maximum number of segments to return (default 100)

    Returns:
        JSON array of all text segments coded with this code, including
        the text content, file names, memos, and position information
    """
    segments = get_db().get_coded_text_segments(code_id, limit)
    return json.dumps({
        "code_id": code_id,
        "segment_count": len(segments),
        "segments": segments
    }, indent=2)


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
def get_coding_frequencies() -> str:
    """Get frequency statistics for all codes in the project.

    This tool provides an overview of how often each code has been used,
    helping identify prominent themes and patterns in the data.

    Returns:
        JSON object with:
        - total_coded_segments: Total count across all codes
        - codes: Array of codes with their frequencies, sorted by frequency
    """
    frequencies = get_db().get_coding_frequencies()
    return json.dumps(frequencies, indent=2)


@mcp.tool()
@_tool_guard
def search_memos(query: str, limit: int = 50) -> str:
    """Search through all memos and annotations in the project.

    This tool searches through code memos, file memos, and annotations
    to find notes and reflections containing specific keywords.

    Args:
        query: The text to search for in memos
        limit: Maximum number of results to return (default 50)

    Returns:
        JSON array of matching memos with their type, content, and context
    """
    results = get_db().search_memos(query, limit)
    return json.dumps({
        "query": query,
        "result_count": len(results),
        "results": results
    }, indent=2)


@mcp.tool()
@_tool_guard
def export_code_report(code_name: str) -> str:
    """Generate a comprehensive report for a specific code.

    This tool creates a detailed report including code metadata,
    all coded segments, and frequency information.

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

    return json.dumps({
        "code": details,
        "segments": segments,
        "report_generated": True
    }, indent=2)


@mcp.tool()
@_tool_guard
def export_refi_qda(
    output_path: str,
    session_id: Optional[str] = None,
    overwrite: bool = False
) -> str:
    """Export codings as a REFI-QDA .qdpx file for other QDA software.

    REFI-QDA is the interchange standard supported by QualCoder, NVivo,
    ATLAS.ti, MAXQDA and others. The export contains the referenced codes,
    the text sources, and the coded selections (with coding memos as
    descriptions).

    Two modes:
    - Default (no session_id): exports ALL text codings of the currently
      open project.
    - With session_id: exports that AI coding session's suggestions
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
        session_id: Optional AI coding session to export instead of the
                    project's codings
        overwrite: Allow replacing an existing file (default: False)

    Returns:
        JSON with the output path and export counts

    Example:
        "Export my codings as REFI-QDA to ~/Desktop/study.qdpx"
    """
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
            segments = ro_db.get_coded_text_segments(code["id"], limit=5000)
            if len(segments) == 5000:
                # The read is capped at 5000 per code — disclose when the
                # project actually holds more (QA2-3)
                if code_frequencies is None:
                    code_frequencies = {
                        c["code_id"]: c["frequency"]
                        for c in ro_db.get_coding_frequencies()["codes"]
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

    exporter = RefiQdaExporter(ro_db)
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

    return json.dumps(summary, indent=2)


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
    """
    result = get_db().get_file_with_coding(file_id)
    if result is None:
        return json.dumps({
            "error": f"File with id {file_id} not found"
        })

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
    return json.dumps(result, indent=2)


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
    return json.dumps({
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
    return json.dumps({
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
    return json.dumps({
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
        operator: 'equals' (exact match, default), 'contains'
                  (case-insensitive substring), or 'gt'/'gte'/'lt'/'lte'
                  (numeric comparisons; non-numeric values never match)

    Returns:
        JSON array of matching cases/files, each with id, name, memo and
        the matched attribute value
    """
    result = get_db().query_by_attribute(attr_name, attr_value, attr_type, operator)
    return json.dumps(result, indent=2)


@mcp.tool()
@_tool_guard
def find_cooccurring_codes(code_id: int, window_size: int = 0) -> str:
    """Find codes that appear together with a specific code.

    This tool identifies co-occurrence patterns - which codes tend to appear
    in the same segments or nearby in the text. Essential for discovering
    relationships between themes and concepts.

    Args:
        code_id: The numeric ID of the code to analyze
        window_size: How to define "co-occurrence":
                    - 0 (default): Codes that overlap the same text segment
                    - N > 0: Codes within N characters of each other

    Returns:
        JSON array of co-occurring codes, sorted by frequency; each entry
        has code_id, code_name, color, category, cooccurrence_count

    Example uses:
    - "What themes appear together with 'workplace stress'?"
    - "Find patterns of co-occurring codes"
    - "Which codes never appear with 'job satisfaction'?"
    """
    result = get_db().find_code_cooccurrences(code_id, window_size)
    return json.dumps(result, indent=2)


@mcp.tool()
@_tool_guard
def get_case_code_matrix() -> str:
    """Get a matrix showing which codes appear in which cases.

    This tool creates a cross-tabulation of all cases and codes, showing
    which codes have been applied to text segments from each case. Essential
    for comparative analysis across participants.

    Only codings fully CONTAINED in a case's text interval are counted,
    matching QualCoder's own report semantics.

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
    result = get_db().get_case_code_matrix()
    return json.dumps(result, indent=2)


@mcp.tool()
@_tool_guard
def get_codes_by_case(case_id: int) -> str:
    """Get all codes that appear in a specific case.

    Shows which themes/codes have been identified in a particular
    case's text segments, with frequency counts.

    Args:
        case_id: The numeric ID of the case

    Only codings fully contained in the case's text intervals are counted
    (QualCoder report semantics).

    Returns:
        JSON array of codes used in this case; each entry has code_id,
        code_name, color, category, occurrence_count
    """
    result = get_db().get_codes_by_case(case_id)
    return json.dumps(result, indent=2)


@mcp.tool()
@_tool_guard
def get_cases_by_code(code_id: int) -> str:
    """Get all cases that contain a specific code.

    Shows which cases/participants have text segments coded with
    a particular theme or code.

    Args:
        code_id: The numeric ID of the code

    Only codings fully contained in a case's text intervals are counted
    (QualCoder report semantics).

    Returns:
        JSON array of cases containing this code; each entry has case_id,
        case_name, memo, occurrence_count
    """
    result = get_db().get_cases_by_code(code_id)
    return json.dumps(result, indent=2)


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
    4. You approve/reject suggestions using update_suggestion_status
    5. You apply approved suggestions using apply_codings

    Args:
        file_ids: List of file IDs to analyze
        code_names: Optional list of specific code names to apply
        instruction: Guidance for what to look for in the analysis
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
    state, holder = qualcoder_lock_state(_current_project_folder())
    if state == "active":
        qualcoder_banner = f"""
⚠️ **STOP — QUALCODER HAS THIS PROJECT OPEN**

qualcoder_open: true
action_required: QualCoder appears to have this project open (user
{holder or 'unknown'}). Ask the user to close QualCoder (or close this
project in it) before continuing — all database writes will be refused
while it is open, so the review-and-approve work would be wasted. Once
they confirm it is closed, re-check via get_current_project (the
`qualcoder_open` field must be false) and only then proceed with the
coding workflow.
"""

    output = f"""{qualcoder_banner}
📊 **ANALYSIS SESSION CREATED**

Session ID: `{session.session_id}`

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

    return output


@mcp.tool()
@_tool_guard
def record_suggestions(
    session_id: str,
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

    Args:
        session_id: The session ID from analyze_for_coding
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
        rejections with reasons, duplicate count, and session statistics

    Example:
        record_suggestions(session_id="...", suggestions=[
            {"file_id": 4, "code_name": "Burnout", "start_pos": 96,
             "end_pos": 129, "segment_text": "by Thursday I am running on fumes",
             "reasoning": "Explicit exhaustion metaphor", "confidence": 0.9}])
    """
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
        })

    session_manager.save_session(session)

    result = {
        "session_id": session_id,
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
    session_id: str,
    suggestion_guids: Optional[List[str]] = None,
    show_context: bool = False
) -> str:
    """Review coding suggestions in detail.

    Shows detailed information about specific suggestions from an analysis session.
    Use this to examine suggestions more closely before approving/rejecting.

    Args:
        session_id: The session ID from analyze_for_coding
        suggestion_guids: Optional list of specific suggestion GUIDs to review
        show_context: Include surrounding text context (default: False)

    Returns:
        Detailed formatted information about the requested suggestions

    Example:
        "Show me more details about suggestion abc-123-def"
        "Review all pending suggestions with context"
    """
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

    for i, sugg in enumerate(suggestions, 1):
        output.append(f"\n{'='*70}")
        output.append(f"**Suggestion {i}** (GUID: `{sugg.guid}`)")
        output.append(f"Status: {sugg.status.upper()}")
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

    return "\n".join(output)


@mcp.tool()
@_tool_guard
def update_suggestion_status(
    session_id: str,
    approve: Optional[List[str]] = None,
    reject: Optional[List[str]] = None
) -> str:
    """Approve or reject specific coding suggestions.

    Use this to mark which suggestions should be applied to the database.
    You can approve some, reject others, or approve/reject all at once.

    Args:
        session_id: The session ID from analyze_for_coding
        approve: List of suggestion GUIDs to approve
        reject: List of suggestion GUIDs to reject

    Returns:
        Confirmation of status updates

    Example:
        "Approve suggestions abc-123 and def-456"
        "Reject suggestion xyz-789"
        "Approve all pending suggestions"
    """
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
    session_id: str,
    create_backup: bool = True,
    owner: str = "AI Coding Assistant"
) -> str:
    """Apply approved coding suggestions to the project database.

    THIS WRITES TO THE DATABASE. This is the final step that actually modifies
    your project. Only approved suggestions will be applied. A backup is created
    first by default for safety.

    Safety guarantees:
    - The session must belong to the CURRENTLY OPEN project; applying a
      session to a different project is refused.
    - Every approved suggestion is re-validated BEFORE the backup and the
      write: the file must exist and be a text source, the code must exist,
      and the segment text must match the file text at the stored positions.
      If anything fails validation, nothing is written and no backup is made.
    - All codings are written in a single all-or-nothing transaction.
    - Applied suggestions are marked "applied" so the session cannot be
      double-applied by accident.

    Args:
        session_id: The session ID with approved suggestions
        create_backup: Create timestamped backup before writing (default: True)
        owner: Coder name for attribution (default: "AI Coding Assistant")

    Returns:
        Detailed confirmation of what was written to the database

    Example:
        "Apply the approved codings to the project"
        "Write these codings to the database"
    """
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

    if not owner or not isinstance(owner, str) or not owner.strip():
        return json.dumps({"error": "owner must be a non-empty string"})

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

    # Format output
    output = ["\n✅ **CODINGS APPLIED TO DATABASE**\n"]

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
    owner: str = "MCP Import",
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

    Args:
        filename: Name for the new file (must include extension, e.g., "interview_04.txt")
        content: The full text content of the file
        memo: Optional memo/description for the file
        owner: Creator name for attribution (default: "MCP Import")
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
                case_link = None
                if case is not None:
                    case_link = write_db.link_file_to_case(
                        case_id=case["id"],
                        file_id=result["id"],
                        owner=owner,
                        auto_commit=False
                    )
                _recheck_lock_before_commit(project_folder, lock_held)
                write_db.conn.commit()
            except DatabaseLockedError:
                try:
                    write_db.conn.rollback()
                except Exception:
                    pass
                raise
            except (ValueError, TypeError) as e:
                try:
                    write_db.conn.rollback()
                except Exception:
                    pass
                _downgrade_to_readonly()
                return json.dumps({"error": str(e)})
            except RuntimeError as e:
                try:
                    write_db.conn.rollback()
                except Exception:
                    pass
                _downgrade_to_readonly()
                return json.dumps({"error": f"Database error: {str(e)}"})
    except DatabaseLockedError:
        _downgrade_to_readonly()
        raise

    # Downgrade back to read-only
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

    # Refuse on pre-v14 schemas and while QualCoder has the project open
    lock_error = _write_gate_error()
    if lock_error is not None:
        return json.dumps(lock_error)

    project_folder = _current_project_folder()

    write_db = get_db(read_only=False)

    backup_path = None
    try:
        with hold_project_lock(project_folder) as lock_held:
            if create_backup:
                try:
                    backup_path = write_db.backup_before_write()
                except Exception as e:
                    logger.error(f"Failed to create backup: {e}")
                    _downgrade_to_readonly()
                    return json.dumps({
                        "error": "Failed to create a backup — check disk space "
                                 "and permissions. Nothing was written.",
                        "message": "Aborting to protect your data — nothing was linked."
                    })

            try:
                link = write_db.link_file_to_case(
                    case_id=case["id"],
                    file_id=file_id,
                    owner="MCP Import",
                    auto_commit=False
                )
                _recheck_lock_before_commit(project_folder, lock_held)
                write_db.conn.commit()
            except DatabaseLockedError:
                try:
                    write_db.conn.rollback()
                except Exception:
                    pass
                raise
            except (ValueError, RuntimeError) as e:
                try:
                    write_db.conn.rollback()
                except Exception:
                    pass
                _downgrade_to_readonly()
                return json.dumps({"error": str(e)})
    except DatabaseLockedError:
        _downgrade_to_readonly()
        raise

    _downgrade_to_readonly()

    output = {
        "success": True,
        "message": f"Linked '{link['file_name']}' to case '{link['case_name']}'",
        "link": link,
    }
    if backup_path:
        output["backup_path"] = str(backup_path)
    return json.dumps(output, indent=2)


# ============================================================================
# ERROR-RECOVERY TOOLS — delete a coding, list and restore backups
# ============================================================================

@mcp.tool()
@_tool_guard
def delete_coding(coding_id: int, create_backup: bool = True) -> str:
    """Delete a single coded segment from the project database.

    THIS WRITES TO THE DATABASE. Use it to remove a coding that was applied
    by mistake (e.g. an approved AI suggestion that turned out to be wrong).
    It removes ONE coding — the assignment of a code to a text span — never
    the code itself, the source file, or any other coding.

    A backup is created first by default, so the deletion can be undone with
    restore_backup if needed.

    Args:
        coding_id: The ctid of the coding to delete. You can find ctids in
                   the output of apply_codings, get_coded_segments, or
                   analyze_file_with_coding (segment_id).
        create_backup: Create timestamped backup before deleting (default: True)

    Returns:
        JSON with the deleted coding's details (code, file, positions, text)
        and the backup path

    Example:
        "Delete coding 42 — that segment was coded wrongly"
    """
    # Validate on the read-only connection BEFORE upgrading/backup
    existing = get_db().get_coding(coding_id)
    if existing is None:
        return json.dumps({"error": f"Coding ID {coding_id} does not exist"})

    # Refuse on pre-v14 schemas and while QualCoder has the project open
    lock_error = _write_gate_error()
    if lock_error is not None:
        return json.dumps(lock_error)

    project_folder = _current_project_folder()

    write_db = get_db(read_only=False)

    backup_path = None
    try:
        with hold_project_lock(project_folder) as lock_held:
            if create_backup:
                try:
                    backup_path = write_db.backup_before_write()
                except Exception as e:
                    logger.error(f"Failed to create backup: {e}")
                    _downgrade_to_readonly()
                    return json.dumps({
                        "error": "Failed to create a backup — check disk space "
                                 "and permissions. Nothing was written.",
                        "message": "Aborting to protect your data — nothing was deleted."
                    })

            try:
                deleted = write_db.delete_coding(coding_id, auto_commit=False)
                _recheck_lock_before_commit(project_folder, lock_held)
                write_db.conn.commit()
            except DatabaseLockedError:
                try:
                    write_db.conn.rollback()
                except Exception:
                    pass
                raise
            except (ValueError, RuntimeError) as e:
                try:
                    write_db.conn.rollback()
                except Exception:
                    pass
                _downgrade_to_readonly()
                return json.dumps({"error": str(e)})
    except DatabaseLockedError:
        _downgrade_to_readonly()
        raise

    _downgrade_to_readonly()

    output = {
        "success": True,
        "message": f"Deleted coding {coding_id} "
                   f"('{deleted['code_name']}' on '{deleted['file_name']}')",
        "deleted_coding": deleted,
    }
    if backup_path:
        output["backup_path"] = str(backup_path)
    return json.dumps(output, indent=2)


@mcp.tool()
@_tool_guard
def list_backups() -> str:
    """List the automatic backups of the currently open project.

    Every write operation (apply_codings, import_text_file, delete_coding,
    restore_backup) creates a timestamped backup folder next to the project,
    named '<project>_backup_<timestamp>.qda'. This tool lists them, newest
    first, so you can pick one for restore_backup.

    Returns:
        JSON with the project name and an array of backups
        (name, path, created, size_mb)
    """
    if current_project_path is None:
        return json.dumps({
            "error": "No Qualcoder project selected. Use 'list_available_projects' "
                     "and 'select_project' to open one."
        })

    project_folder = validate_qda_path(current_project_path).parent

    backups = []
    # Two backup families exist side by side: this server's
    # {name}_backup_{YYYYMMDD_HHMMSS}.qda and QualCoder's own
    # {name}_BKUP_{YYYYMMDD_HH}[suffix].qda open-time backups.
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
                    "size_mb": round(size_bytes / (1024 * 1024), 2),
                })
            except OSError as e:
                logger.debug(f"Cannot stat backup {entry}: {e}")
                continue

    backups.sort(key=lambda b: b["created"], reverse=True)

    return json.dumps({
        "project": project_folder.stem,
        "backup_count": len(backups),
        "backups": backups,
        "notes": [
            "kind='qualcoder' backups are made by QualCoder itself on "
            "project open; they may exclude audio/video files and QualCoder "
            "deletes them again when a session made no changes.",
            "QualCoder may also store its backups in its settings "
            "'directory' — only backups next to the project are listed here."
        ],
        "hint": "Use restore_backup(backup_path) to roll the project back "
                "to one of these snapshots."
    }, indent=2)


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
    4. refuses to run while the project database is locked (QualCoder open).

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
                     "and 'select_project' to open one."
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
        # Attempt recovery from the safety backup
        logger.error(f"Restore failed mid-swap: {e}")
        try:
            if not project_folder.exists():
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
def get_coding_session_info(session_id: str) -> str:
    """Get detailed information about a coding session.

    Shows all the suggestions, statistics, and metadata for a session.
    Useful for reviewing what was suggested before exporting.

    Args:
        session_id: The session ID to query

    Returns:
        JSON with complete session details including all suggestions

    Example:
        "Show me session abc123"
        "What's in coding session xyz789?"
    """
    try:
        # Load session
        if not session_manager.session_exists(session_id):
            return json.dumps({
                "error": f"Session {session_id} not found",
                "available_sessions": session_manager.list_sessions()
            })

        session = session_manager.load_session(session_id)

        # Return full session data
        return json.dumps(session.to_dict(), indent=2)

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

        return json.dumps({
            "session_count": len(sessions),
            "sessions": sessions
        }, indent=2)

    except Exception as e:
        logger.error(f"Error in list_coding_sessions: {e}")
        return json.dumps({"error": str(e)})


@mcp.tool()
@_tool_guard
def delete_coding_session(session_id: str) -> str:
    """Delete a saved coding session.

    Permanently removes a session file from disk. Use with caution!

    Args:
        session_id: The session ID to delete

    Returns:
        JSON with success status

    Example:
        "Delete session abc123"
    """
    try:
        deleted = session_manager.delete_session(session_id)

        if deleted:
            return json.dumps({
                "success": True,
                "message": f"Session {session_id} deleted",
                "session_id": session_id
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
                "Save the session_id - you'll need it for all follow-up actions"
            ]
        },
        "apply_codings": {
            "purpose": "Apply approved coding suggestions directly to the Qualcoder database",
            "when_to_use": "After reviewing suggestions and approving the ones you want",
            "workflow": [
                "1. Run analyze_for_coding on your files",
                "2. Record the suggestions with record_suggestions",
                "3. Review with review_suggestions",
                "4. Approve/reject with update_suggestion_status",
                "5. Apply approved codings with apply_codings",
                "6. A backup is created automatically before writing; "
                "delete_coding / restore_backup undo mistakes"
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
                "cleanup_old_sessions"
            ],
            "tip": "Use explain_ai_coding_tools() with no arguments for an overview"
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

def main():
    """Main entry point for the MCP server."""
    # Check for optional pre-configured project (Option B: Fixed Project)
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
