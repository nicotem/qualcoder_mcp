"""Qualcoder MCP Server - Expose Qualcoder data via Model Context Protocol."""

import os
import sys
import json
import logging
import sqlite3
import functools
from pathlib import Path
from typing import Optional, List, Dict, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp import Context

from .database import (
    QualcoderDatabase,
    DatabaseLockedError,
    UnsupportedSchemaError,
    DB_LOCKED_MESSAGE,
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

        return json.dumps({
            "success": True,
            "message": f"Switched to project: {Path(project_path).stem}",
            "project_path": project_path,
            "project_name": Path(project_path).stem,
            "project_info": project_info
        }, indent=2)

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

    Returns:
        JSON with current project path and basic metadata
    """
    try:
        if current_project_path is None:
            return json.dumps({
                "current_project": None,
                "message": "No project currently open. Use 'list_available_projects' "
                          "and 'select_project' to open one."
            }, indent=2)

        project_info = get_db().get_project_info()

        return json.dumps({
            "current_project": current_project_path,
            "project_name": Path(current_project_path).stem,
            "project_info": project_info
        }, indent=2)

    except Exception as e:
        return json.dumps({"error": f"Failed to get project info: {str(e)}"})


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
def query_by_attribute(attr_name: str, attr_value: str, attr_type: str = "case") -> str:
    """Find cases or files that have a specific attribute value.

    This enables demographic or metadata-based queries like:
    - "Find all participants over age 50"
    - "Get files where interview_type is 'focus_group'"
    - "Find cases with education_level 'graduate'"

    Args:
        attr_name: Name of the attribute to query
        attr_value: Value to search for (exact match for numeric, substring for text)
        attr_type: Either 'case' or 'file' (default: 'case')

    Returns:
        JSON object with:
        - matches: Array of cases/files matching the criteria
        - Each match includes the entity details plus all their attributes
    """
    result = get_db().query_by_attribute(attr_name, attr_value, attr_type)
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
        JSON object with:
        - code_info: Details about the primary code
        - cooccurring_codes: Array of codes that appear together, sorted by frequency
        - Each includes overlap count and percentage

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

    Returns:
        JSON object with:
        - matrix: 2D array where matrix[case][code] = segment count
        - case_labels: Array of case names
        - code_labels: Array of code names
        - statistics: Row and column totals

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

    Returns:
        JSON object with:
        - case_info: Case details
        - codes: Array of codes used in this case with segment counts
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

    Returns:
        JSON object with:
        - code_info: Code details
        - cases: Array of cases containing this code with segment counts
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

    # NOTE: This is where YOU (Claude) would do the actual AI analysis
    # For now, return the session info and instructions for the next step

    # Save session
    session_manager.save_session(session)

    output = f"""
📊 **ANALYSIS SESSION CREATED**

Session ID: `{session.session_id}`

**Analysis Parameters:**
- Files: {len(files_to_analyze)} files ({', '.join(f['name'] for f in files_to_analyze)})
- Codes: {len(codes_to_use)} codes ({', '.join(c['name'] for c in codes_to_use)})
- Instruction: "{instruction}"
- Min confidence: {min_confidence}

**IMPORTANT - NEXT STEPS:**

This session has been created and saved. Now YOU (Claude) need to:

1. **Analyze each file** using your AI capabilities
2. **Create CodingSuggestion objects** for each relevant segment you identify
3. **Add them to the session** using session.add_suggestion()
4. **Present them to the user** in a clear, reviewable format

The session is stored at: `{session_manager.storage_dir / f"session_{session.session_id}.json"}`

**FOR THE USER:**
Once Claude completes the analysis and presents suggestions, you can:
- Review the suggestions in the chat
- Use `review_suggestions` to see more details
- Use `update_suggestion_status` to approve/reject specific suggestions
- Use `apply_codings` to write approved suggestions to the database
"""

    return output


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

    output = f"""
✅ **Updated Suggestion Statuses**

Changed:
- Approved: {result['approved']} suggestions
- Rejected: {result['rejected']} suggestions

Current Status:
- Total: {stats['total_suggestions']} suggestions
- Approved: {stats['approved']}
- Rejected: {stats['rejected']}
- Pending: {stats['pending']}

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

    IMPORTANT: Make sure you're working on a copy of your project in the
    MCP workspace (~/Documents/Qualcoder MCP Projects/)

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

    # Get only approved suggestions (check before upgrading to write mode)
    approved = session.filter_by_status("approved")

    if not approved:
        return "❌ No approved suggestions to apply. Use `update_suggestion_status` to approve suggestions first."

    # Upgrade to read-write mode for writing codings
    write_db = get_db(read_only=False)

    # Create backup
    backup_path = None
    if create_backup:
        try:
            backup_path = write_db.backup_before_write()
        except Exception as e:
            _downgrade_to_readonly()
            return f"❌ Failed to create backup: {e}\n\nAborting to protect your data."

    # Apply all codings in a single transaction (all-or-nothing)
    results = []

    try:
        for sugg in approved:
            # Create memo with reasoning and confidence
            memo = f"{sugg.reasoning}\n\n[AI Confidence: {sugg.confidence:.2f}]"

            ctid = write_db.add_coding(
                file_id=sugg.file_id,
                code_id=sugg.code_id,
                start_pos=sugg.start_pos,
                end_pos=sugg.end_pos,
                selected_text=sugg.segment_text,
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

        # Commit all at once
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

    # Downgrade back to read-only after successful write
    _downgrade_to_readonly()
    errors = []

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

    if errors:
        output.append(f"\n\n❌ **Errors: {len(errors)}**")
        for e in errors:
            output.append(f"  - {e['guid']}: {e['error']}")

    output.append(f"\n\n**You can now open the project in Qualcoder to see the AI-coded segments.**")
    output.append(f"All codings are attributed to '{owner}' with confidence scores in memos.")

    return "\n".join(output)


@mcp.tool()
@_tool_guard
def import_text_file(
    filename: str,
    content: str,
    memo: str = "",
    owner: str = "MCP Import",
    create_backup: bool = True
) -> str:
    """Import text content as a new source file in the QualCoder project.

    Creates a new text source file in the project database, similar to
    QualCoder's "Create text file" feature. The file will be visible in
    QualCoder's file manager and available for coding.

    IMPORTANT: Make sure you're working on a copy of your project in the
    MCP workspace (~/Documents/Qualcoder MCP Projects/)

    Args:
        filename: Name for the new file (must include extension, e.g., "interview_04.txt")
        content: The full text content of the file
        memo: Optional memo/description for the file
        owner: Creator name for attribution (default: "MCP Import")
        create_backup: Create timestamped backup before writing (default: True)

    Returns:
        JSON with the new file's ID, name, and confirmation details
    """
    # Early validation before upgrading connection
    if not filename or not filename.strip():
        return json.dumps({"error": "filename must not be empty"})
    if not content or not content.strip():
        return json.dumps({"error": "content must not be empty"})

    # Upgrade to read-write mode
    write_db = get_db(read_only=False)

    # Create backup
    backup_path = None
    if create_backup:
        try:
            backup_path = write_db.backup_before_write()
        except Exception as e:
            _downgrade_to_readonly()
            return json.dumps({
                "error": f"Failed to create backup: {e}",
                "message": "Aborting to protect your data."
            })

    # Perform the import
    try:
        result = write_db.import_text_file(
            name=filename.strip(),
            content=content,
            owner=owner,
            memo=memo,
            auto_commit=True
        )
    except (ValueError, TypeError) as e:
        _downgrade_to_readonly()
        return json.dumps({"error": str(e)})
    except RuntimeError as e:
        try:
            write_db.conn.rollback()
        except Exception:
            pass
        _downgrade_to_readonly()
        return json.dumps({"error": f"Database error: {str(e)}"})

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
    if backup_path:
        output["backup_path"] = str(backup_path)

    return json.dumps(output, indent=2)


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
                "step_1": "Analyze files for coding (analyze_for_coding)",
                "step_2": "Review suggestions (review_suggestions)",
                "step_3": "Approve or reject suggestions (update_suggestion_status)",
                "step_4": "Apply approved codings to database (apply_codings)"
            },
            "key_features": [
                "Analyze complete transcripts with full context",
                "Suggest coded segments with confidence scores",
                "Review and approve/reject suggestions before applying",
                "Apply codings directly to Qualcoder database (with automatic backup)",
                "Session persistence - resume work anytime"
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
                "2. Review with review_suggestions",
                "3. Approve/reject with update_suggestion_status",
                "4. Apply approved codings with apply_codings",
                "5. A backup is created automatically before writing"
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
                "review_suggestions",
                "update_suggestion_status",
                "apply_codings",
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
