"""Qualcoder MCP Server - Expose Qualcoder data via Model Context Protocol."""

import os
import sys
import json
import logging
import uuid
from pathlib import Path
from typing import Optional, List, Dict, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp import Context

from .database import QualcoderDatabase
from .sessions import SessionManager, AICodingSession, CodingSuggestion
from .refi_export import RefiQdaExporter

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


def switch_project(project_path: str) -> None:
    """Switch to a different project.

    Args:
        project_path: Path to the .qda file

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

    # Connect to new project
    db = QualcoderDatabase(project_path)
    current_project_path = project_path
    logger.info(f"Switched to project: {Path(project_path).name}")


def get_db() -> QualcoderDatabase:
    """Get or initialize the database connection.

    Raises:
        ValueError: If no project specified or invalid
        FileNotFoundError: If database file doesn't exist
        RuntimeError: If database connection fails
    """
    global db, current_project_path

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
            db = QualcoderDatabase(db_path)
            current_project_path = db_path
            # Log only filename, not full path (security best practice)
            logger.info(f"Connected to Qualcoder database: {Path(db_path).name}")
        except (ValueError, FileNotFoundError, RuntimeError) as e:
            logger.error(f"Failed to connect to database: {e}")
            raise

    return db


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

    except (ValueError, FileNotFoundError) as e:
        return json.dumps({
            "success": False,
            "error": f"Invalid project: {str(e)}"
        })
    except RuntimeError as e:
        return json.dumps({
            "success": False,
            "error": "Failed to open project database"
        })


@mcp.tool()
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
# AI-ASSISTED CODING TOOLS
# ============================================================================

@mcp.tool()
def suggest_coding_for_files(
    file_ids: List[int],
    code_names: Optional[List[str]] = None,
    instruction: str = "Code all relevant segments",
    min_confidence: float = 0.6
) -> str:
    """AI analyzes files and suggests coded segments.

    This is the main AI coding tool. I will read each file and identify
    segments that should be coded with the specified codes. The suggestions
    are stored in a session but NOT written to your database - they remain
    as suggestions until you review and import them.

    Args:
        file_ids: List of file IDs to analyze and code
        code_names: List of code names to apply (if None, I'll use all codes)
        instruction: Specific guidance for the coding task
        min_confidence: Minimum confidence threshold (0.0-1.0, default: 0.6)

    Returns:
        JSON with:
        - session_id: Unique ID for this coding session (save this!)
        - total_suggestions: Count of suggested coded segments
        - by_file: Breakdown by file
        - by_code: Breakdown by code
        - next_steps: What to do next

    Example usage:
        "Code files 1, 2, and 3 with codes related to 'workplace stress'"
        "Apply the 'motivation' and 'team dynamics' codes to interview transcripts"
        "Code all files using all available codes"

    Note: After this completes, use export_coding_suggestions to save
    the results for review, and then import into Qualcoder.
    """
    try:
        # Validate inputs
        if not file_ids:
            return json.dumps({"error": "file_ids cannot be empty"})

        if min_confidence < 0.0 or min_confidence > 1.0:
            return json.dumps({"error": "min_confidence must be between 0.0 and 1.0"})

        # Get database
        database = get_db()

        # Validate files exist
        all_files = database.list_files()
        valid_file_ids = {f["id"] for f in all_files}
        invalid_files = [fid for fid in file_ids if fid not in valid_file_ids]
        if invalid_files:
            return json.dumps({
                "error": f"Invalid file IDs: {invalid_files}",
                "available_files": [{"id": f["id"], "name": f["name"]} for f in all_files]
            })

        # Get codes
        all_codes = database.list_codes()
        if code_names:
            # Filter to specified codes
            codes_to_use = [c for c in all_codes if c["name"] in code_names]
            if not codes_to_use:
                return json.dumps({
                    "error": f"No matching codes found for: {code_names}",
                    "available_codes": [c["name"] for c in all_codes]
                })
        else:
            codes_to_use = all_codes

        # Create session
        session = AICodingSession(
            project_path=current_project_path or "",
            description=f"AI coding of files {file_ids} with instruction: {instruction}",
            file_ids=file_ids,
            code_names=[c["name"] for c in codes_to_use],
            instruction=instruction,
            min_confidence=min_confidence
        )

        # Return instructions for user to provide coding analysis
        # Since we're using native Claude analysis (Option A), we return a structured
        # prompt that guides Claude to analyze and create suggestions
        return json.dumps({
            "status": "ready_for_analysis",
            "session_id": session.session_id,
            "message": "I'm ready to analyze these files. I will now read each file and suggest coded segments.",
            "files_to_analyze": file_ids,
            "codes_to_apply": [c["name"] for c in codes_to_use],
            "instruction": instruction,
            "min_confidence": min_confidence,
            "next_step": "I will now analyze each file and create coding suggestions. Please wait..."
        }, indent=2)

    except Exception as e:
        logger.error(f"Error in suggest_coding_for_files: {e}")
        return json.dumps({"error": str(e)})


@mcp.tool()
def export_coding_suggestions(
    session_id: str,
    output_format: str = "refi-qda",
    output_path: Optional[str] = None,
    include_rejected: bool = False
) -> str:
    """Export AI coding suggestions for review and import.

    Exports the suggestions from a coding session in various formats.
    The primary format is REFI-QDA XML for importing into Qualcoder.

    Args:
        session_id: The session ID from suggest_coding_for_files
        output_format: Format to export ("refi-qda", "json", "csv")
        output_path: Where to save (default: ~/Documents/coding_suggestions_DATE.*)
        include_rejected: If True, includes rejected suggestions (default: False)

    Returns:
        JSON with:
        - output_file: Path to generated file
        - format: Format used
        - segment_count: Number of coded segments included
        - import_instructions: Next steps for importing

    Formats:
        - "refi-qda": Standard .qdpx file for Qualcoder import (recommended)
        - "json": Machine-readable format for editing/review
        - "csv": Spreadsheet format for documentation

    Workflow:
        1. Review suggestions (check the session info)
        2. Optionally update_suggestion_status to approve/reject specific ones
        3. Export with format="refi-qda"
        4. Import the .qdpx file into Qualcoder

    Example:
        "Export session abc123 as REFI-QDA format"
        "Export suggestions to JSON for review"
    """
    try:
        # Load session
        if not session_manager.session_exists(session_id):
            return json.dumps({
                "error": f"Session {session_id} not found",
                "available_sessions": session_manager.list_sessions()
            })

        session = session_manager.load_session(session_id)

        # Filter suggestions by status
        if include_rejected:
            suggestions = session.suggestions
        else:
            suggestions = [s for s in session.suggestions if s.status != "rejected"]

        if not suggestions:
            return json.dumps({
                "error": "No suggestions to export",
                "total_suggestions": len(session.suggestions),
                "rejected_count": len([s for s in session.suggestions if s.status == "rejected"]),
                "note": "All suggestions may have been rejected. Use include_rejected=true to export them anyway."
            })

        # Generate default output path if not specified
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")

        if output_path is None:
            home = Path.home()
            if output_format == "refi-qda":
                output_path = str(home / "Documents" / f"coding_suggestions_{timestamp}.qdpx")
            elif output_format == "json":
                output_path = str(home / "Documents" / f"coding_suggestions_{timestamp}.json")
            elif output_format == "csv":
                output_path = str(home / "Documents" / f"coding_suggestions_{timestamp}.csv")
            else:
                return json.dumps({"error": f"Unknown format: {output_format}"})

        # Export based on format
        if output_format == "refi-qda":
            # Use REFI-QDA exporter
            database = get_db()
            exporter = RefiQdaExporter(database)

            # Validate suggestions
            warnings = exporter.validate_suggestions(suggestions)
            if warnings:
                logger.warning(f"Validation warnings: {warnings}")

            # Export to .qdpx
            output_file = exporter.export_to_qdpx(
                suggestions,
                output_path,
                project_name=f"AI Coding Suggestions - {timestamp}"
            )

            return json.dumps({
                "success": True,
                "output_file": output_file,
                "format": "refi-qda",
                "segment_count": len(suggestions),
                "validation_warnings": warnings if warnings else [],
                "import_instructions": [
                    "1. Open Qualcoder",
                    "2. Go to: File > Import > REFI-QDA Project",
                    f"3. Select: {output_file}",
                    "4. Review the import preview",
                    "5. Confirm the import",
                    "6. Your coded segments will be added to the project!"
                ]
            }, indent=2)

        elif output_format == "json":
            # Export as JSON
            output_file = Path(output_path).expanduser()
            output_file.parent.mkdir(parents=True, exist_ok=True)

            with open(output_file, 'w') as f:
                json.dump(session.to_dict(), f, indent=2)

            return json.dumps({
                "success": True,
                "output_file": str(output_file),
                "format": "json",
                "segment_count": len(suggestions),
                "note": "JSON file can be edited and re-imported as a session"
            }, indent=2)

        elif output_format == "csv":
            # Export as CSV
            import csv as csv_module
            output_file = Path(output_path).expanduser()
            output_file.parent.mkdir(parents=True, exist_ok=True)

            with open(output_file, 'w', newline='') as f:
                writer = csv_module.writer(f)
                writer.writerow([
                    "file_name", "code_name", "start_pos", "end_pos",
                    "segment_text", "ai_memo", "confidence", "status"
                ])
                for s in suggestions:
                    writer.writerow([
                        s.file_name, s.code_name, s.start_pos, s.end_pos,
                        s.segment_text, s.ai_memo, s.confidence, s.status
                    ])

            return json.dumps({
                "success": True,
                "output_file": str(output_file),
                "format": "csv",
                "segment_count": len(suggestions),
                "note": "CSV file is for documentation/review only, not for import"
            }, indent=2)

    except Exception as e:
        logger.error(f"Error in export_coding_suggestions: {e}")
        return json.dumps({"error": str(e)})


@mcp.tool()
def update_suggestion_status(
    session_id: str,
    updates: str
) -> str:
    """Update the status of coding suggestions (approve/reject).

    Allows you to approve or reject specific suggestions before exporting.
    Useful for quality control - you can reject suggestions that don't seem right.

    Args:
        session_id: The session ID from suggest_coding_for_files
        updates: JSON array of updates, format:
                 [{"index": 0, "status": "approved"}, {"index": 1, "status": "rejected"}, ...]

    Returns:
        JSON with updated statistics

    Statuses:
        - "approved": Include in export (default for new suggestions)
        - "rejected": Exclude from export
        - "pending": Not yet reviewed

    Example:
        updates = '[{"index": 0, "status": "approved"}, {"index": 2, "status": "rejected"}]'
    """
    try:
        # Load session
        if not session_manager.session_exists(session_id):
            return json.dumps({"error": f"Session {session_id} not found"})

        session = session_manager.load_session(session_id)

        # Parse updates
        try:
            update_list = json.loads(updates)
        except json.JSONDecodeError:
            return json.dumps({"error": "updates must be valid JSON array"})

        # Apply updates
        updated_count = 0
        errors = []

        for update in update_list:
            index = update.get("index")
            status = update.get("status")

            if index is None or status is None:
                errors.append(f"Update missing index or status: {update}")
                continue

            if status not in ["approved", "rejected", "pending"]:
                errors.append(f"Invalid status '{status}' for index {index}")
                continue

            if session.update_suggestion_status(index, status):
                updated_count += 1
            else:
                errors.append(f"Invalid index: {index} (out of range)")

        # Save updated session
        session_manager.save_session(session)

        # Return updated statistics
        stats = session.get_statistics()

        return json.dumps({
            "success": True,
            "updated_count": updated_count,
            "errors": errors if errors else [],
            "statistics": stats
        }, indent=2)

    except Exception as e:
        logger.error(f"Error in update_suggestion_status: {e}")
        return json.dumps({"error": str(e)})


@mcp.tool()
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
def suggest_new_codes(
    file_ids: List[int],
    instruction: str = "Analyze and suggest relevant codes",
    existing_codes_context: bool = True
) -> str:
    """AI analyzes files and suggests new codes to add to the codebook.

    I will read the specified files and suggest codes based on themes,
    patterns, and concepts found in the text. Useful when starting a
    new project or discovering new themes.

    Args:
        file_ids: List of file IDs to analyze
        instruction: Specific guidance for code suggestion
        existing_codes_context: If True, I'll show existing codes to avoid duplicates

    Returns:
        JSON with suggested codes, each including:
        - code_name: Proposed name for the code
        - description: What this code represents
        - category: Suggested category/hierarchy
        - example_segments: 2-3 text examples
        - rationale: Why this code is suggested

    Example usage:
        "Analyze files 1-5 and suggest codes for themes about workplace stress"
        "Review all files and suggest codes I might be missing"
        "Suggest codes for the interview transcripts"

    Note: These are suggestions only - you'll need to manually add approved
    codes to your Qualcoder project, or use export_new_codes_for_import to
    generate a REFI-QDA file for import.
    """
    try:
        # Validate inputs
        if not file_ids:
            return json.dumps({"error": "file_ids cannot be empty"})

        # Get database
        database = get_db()

        # Validate files exist
        all_files = database.list_files()
        valid_file_ids = {f["id"] for f in all_files}
        invalid_files = [fid for fid in file_ids if fid not in valid_file_ids]
        if invalid_files:
            return json.dumps({
                "error": f"Invalid file IDs: {invalid_files}",
                "available_files": [{"id": f["id"], "name": f["name"]} for f in all_files]
            })

        # Get existing codes for context
        existing_codes = []
        if existing_codes_context:
            existing_codes = database.list_codes()

        # Return structure to guide Claude's analysis
        # Claude will analyze the files and provide code suggestions
        return json.dumps({
            "status": "ready_for_analysis",
            "message": "I'm ready to analyze these files and suggest new codes. I will now read the files and identify themes.",
            "files_to_analyze": file_ids,
            "existing_codes": [c["name"] for c in existing_codes] if existing_codes_context else [],
            "instruction": instruction,
            "next_step": "I will analyze the files and suggest codes based on the themes I find..."
        }, indent=2)

    except Exception as e:
        logger.error(f"Error in suggest_new_codes: {e}")
        return json.dumps({"error": str(e)})


@mcp.tool()
def export_new_codes_for_import(
    codes_json: str,
    output_path: Optional[str] = None
) -> str:
    """Export approved new codes as REFI-QDA Codebook for import.

    Takes suggested codes (from suggest_new_codes) and creates a REFI-QDA
    codebook file that can be imported into Qualcoder.

    Args:
        codes_json: JSON string with approved codes, format:
                   [{"name": "Code Name", "description": "Description", "color": "#FF0000"}, ...]
        output_path: Where to save (default: ~/Documents/new_codes_DATE.qdpx)

    Returns:
        JSON with:
        - output_file: Path to generated .qdpx file
        - code_count: Number of codes exported
        - import_instructions: Step-by-step guide

    Example:
        codes = '[{"name": "Workplace Stress", "description": "Stress factors", "color": "#FF6B6B"}]'

    Import process:
        1. Open Qualcoder
        2. File > Import > REFI-QDA Codebook
        3. Select the generated .qdpx file
        4. Review and confirm import
        5. Codes will be added to your codebook
    """
    try:
        # Parse codes JSON
        try:
            codes = json.loads(codes_json)
        except json.JSONDecodeError:
            return json.dumps({"error": "codes_json must be valid JSON array"})

        if not codes:
            return json.dumps({"error": "codes list cannot be empty"})

        # Generate default output path if not specified
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")

        if output_path is None:
            output_path = str(Path.home() / "Documents" / f"new_codes_{timestamp}.qdpx")

        # Create minimal REFI-QDA XML with just CodeBook
        import xml.etree.ElementTree as ET
        from xml.dom import minidom
        import zipfile

        # Register namespace
        NAMESPACE = "urn:QDA-XML:project:1.0"
        ET.register_namespace('', NAMESPACE)

        # Create root
        root = ET.Element(
            f"{{{NAMESPACE}}}Project",
            attrib={
                "name": "New Codes",
                "origin": "Qualcoder MCP AI Assistant",
                "creatingUserGUID": str(uuid.uuid4()),
                "creationDateTime": datetime.now().isoformat() + "Z"
            }
        )

        # Add CodeBook
        codebook_elem = ET.SubElement(root, f"{{{NAMESPACE}}}CodeBook")
        codes_elem = ET.SubElement(codebook_elem, f"{{{NAMESPACE}}}Codes")

        # Add each code
        for code in codes:
            code_elem = ET.SubElement(
                codes_elem,
                f"{{{NAMESPACE}}}Code",
                attrib={
                    "guid": str(uuid.uuid4()),
                    "name": code.get("name", "Unnamed Code"),
                    "isCodable": "true"
                }
            )

            if code.get("color"):
                code_elem.set("color", code["color"])

            if code.get("description"):
                desc_elem = ET.SubElement(code_elem, f"{{{NAMESPACE}}}Description")
                desc_elem.text = code["description"]

        # Prettify XML
        rough_string = ET.tostring(root, encoding='utf-8')
        reparsed = minidom.parseString(rough_string)
        xml_string = reparsed.toprettyxml(indent="  ", encoding='utf-8').decode('utf-8')

        # Create .qdpx file
        output_file = Path(output_path).expanduser()
        output_file.parent.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(output_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
            zipf.writestr('project.qde', xml_string)

        return json.dumps({
            "success": True,
            "output_file": str(output_file),
            "code_count": len(codes),
            "import_instructions": [
                "1. Open Qualcoder",
                "2. Go to: File > Import > REFI-QDA Codebook",
                f"3. Select: {output_file}",
                "4. Review the codes to import",
                "5. Confirm the import",
                "6. New codes will be added to your codebook!"
            ]
        }, indent=2)

    except Exception as e:
        logger.error(f"Error in export_new_codes_for_import: {e}")
        return json.dumps({"error": str(e)})


@mcp.tool()
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
        "How do I use suggest_coding_for_files?"
        "Show me examples of export_coding_suggestions"
        "What's the workflow for AI coding?"
    """
    # Comprehensive help documentation
    tool_help = {
        "overview": {
            "title": "AI-Assisted Coding for Qualcoder",
            "description": "Use Claude to help code your qualitative data. Claude can analyze interview transcripts, suggest codes, and create coded segments that you can review and import into Qualcoder.",
            "workflow": {
                "phase_1": "Optional: Discover new codes (suggest_new_codes, export_new_codes_for_import)",
                "phase_2": "AI Coding (suggest_coding_for_files)",
                "phase_3": "Review & Export (get_coding_session_info, update_suggestion_status, export_coding_suggestions)",
                "phase_4": "Import into Qualcoder (File > Import > REFI-QDA Project)"
            },
            "key_features": [
                "Analyze complete transcripts with full context",
                "Suggest coded segments with confidence scores",
                "Review and approve/reject suggestions before import",
                "Export to REFI-QDA format for Qualcoder import",
                "Session persistence - resume work anytime"
            ]
        },
        "suggest_coding_for_files": {
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
        "export_coding_suggestions": {
            "purpose": "Export suggestions in various formats for review and import",
            "when_to_use": "After coding session is complete and you want to review/import",
            "formats": {
                "refi-qda": "Standard .qdpx file for Qualcoder import (recommended)",
                "json": "Machine-readable format for editing",
                "csv": "Spreadsheet format for documentation"
            },
            "workflow": [
                "1. Complete suggest_coding_for_files",
                "2. Review session with get_coding_session_info",
                "3. Optionally approve/reject with update_suggestion_status",
                "4. Export with format='refi-qda'",
                "5. Import .qdpx file into Qualcoder"
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
                "suggest_coding_for_files",
                "export_coding_suggestions",
                "update_suggestion_status",
                "get_coding_session_info",
                "list_coding_sessions",
                "delete_coding_session",
                "cleanup_old_sessions",
                "suggest_new_codes",
                "export_new_codes_for_import"
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
    # Check for database path
    if "QUALCODER_PROJECT_PATH" not in os.environ:
        print("Error: QUALCODER_PROJECT_PATH environment variable not set", file=sys.stderr)
        print("Please set it to the path of your .qda file", file=sys.stderr)
        print("Example: export QUALCODER_PROJECT_PATH=/path/to/your/project.qda", file=sys.stderr)
        sys.exit(1)

    db_path = os.environ["QUALCODER_PROJECT_PATH"]
    if not Path(db_path).exists():
        print(f"Error: Database file not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    logger.info(f"Starting Qualcoder MCP server for: {db_path}")

    # Run the server using stdio transport
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
