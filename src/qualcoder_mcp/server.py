"""Qualcoder MCP Server - Expose Qualcoder data via Model Context Protocol."""

import os
import sys
import json
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp import Context

from .database import QualcoderDatabase

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
