"""Database interface for Qualcoder .qda files."""

import sqlite3
from pathlib import Path
from typing import Optional, List, Dict, Any, Union
import json
import logging
import hashlib
import uuid
import shutil
from datetime import datetime

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
REQUIRED_COLUMNS = {
    "code_text": ["important"],
}

DB_LOCKED_MESSAGE = (
    "The project database is locked — QualCoder may have it open. "
    "Close the project in QualCoder (or wait a moment) and try again."
)


class DatabaseLockedError(RuntimeError):
    """Raised when the SQLite database is locked by another process."""


class UnsupportedSchemaError(RuntimeError):
    """Raised when the project database schema is too old for this server."""


def _is_locked_error(e: sqlite3.Error) -> bool:
    """Check whether a sqlite3 error indicates a locked/busy database."""
    msg = str(e).lower()
    return "locked" in msg or "busy" in msg


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

    # Handle different path formats
    if path.is_dir():
        # Path is a directory - look for data.qda inside
        if path.suffix.lower() == '.qda':
            # This is a .qda project folder
            data_file = path / "data.qda"
            if not data_file.exists():
                raise FileNotFoundError(f"No data.qda file found in project folder: {path}")
            if not data_file.is_file():
                raise ValueError(f"data.qda exists but is not a file: {data_file}")
            path = data_file
        else:
            raise ValueError(f"Directory must have .qda extension: {path}")
    elif path.is_file():
        # Path is a file - verify it's a .qda file
        if path.name != "data.qda" and path.suffix.lower() != '.qda':
            raise ValueError(f"Invalid file: must be data.qda or *.qda, got {path.name}")
    else:
        raise ValueError(f"Path is neither a file nor a directory: {path}")

    # Basic SQLite validation (read-only check)
    conn = None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
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

    # Create backup with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"{project_path.stem}_backup_{timestamp}.qda"
    backup_path = project_path.parent / backup_name

    logger.info(f"Creating backup: {backup_path}")

    try:
        shutil.copytree(project_path, backup_path)
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

    # Determine destination name
    if new_name:
        dest_name = new_name if new_name.endswith('.qda') else f"{new_name}.qda"
    else:
        dest_name = source_path.name

    dest_path = workspace / dest_name

    # Check if destination already exists
    if dest_path.exists():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest_name = f"{dest_path.stem}_{timestamp}.qda"
        dest_path = workspace / dest_name

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
                uri = f"file:{self.db_path}?mode=ro"
                self.conn = sqlite3.connect(uri, uri=True)
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
        """Check database version and log warnings if unsupported."""
        self.db_version = None
        try:
            cursor = self.conn.execute("SELECT databaseversion FROM project")
            row = cursor.fetchone()
            if row:
                version = row[0]
                self.db_version = version
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

            # Count coded segments
            text_count = self.conn.execute(
                "SELECT COUNT(*) as cnt FROM code_text WHERE cid = ?",
                (code_id,)
            ).fetchone()["cnt"]

            image_count = self.conn.execute(
                "SELECT COUNT(*) as cnt FROM code_image WHERE cid = ?",
                (code_id,)
            ).fetchone()["cnt"]

            av_count = self.conn.execute(
                "SELECT COUNT(*) as cnt FROM code_av WHERE cid = ?",
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
                    memo,
                    owner,
                    date
                FROM source
                WHERE id = ?
            """, (file_id,))

            file_row = file_cursor.fetchone()
            if not file_row:
                return None

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
                    "memo": ann_row["memo"],
                    "owner": ann_row["owner"],
                    "date": ann_row["date"]
                })

            return {
                "file_info": {
                    "id": file_row["id"],
                    "name": file_row["name"],
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
        cursor = self.conn.execute("""
            SELECT
                c.cid,
                c.name,
                c.color,
                cat.name as category,
                COUNT(ct.ctid) as text_count
            FROM code_name c
            LEFT JOIN code_cat cat ON c.catid = cat.catid
            LEFT JOIN code_text ct ON c.cid = ct.cid
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

                # Search filename
                if search_filename:
                    file_name = row["name"] if case_sensitive else row["name"].lower()
                    if search_pattern in file_name:
                        matched_in["filename"] = True
                        matches.append({
                            "location": "filename",
                            "preview": row["name"]
                        })
                        match_count += 1

                # Search content
                if search_content and (row["mediapath"] is None or row["mediapath"] == ""):
                    file_text = row["fulltext"] or ""
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
                    "applies_to": row["caseOrFile"],  # 'case', 'file', or 'both'
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

    def query_by_attribute(self, attr_name: str, attr_value: str,
                           attr_type: str = "case") -> List[Dict[str, Any]]:
        """Query cases or files by attribute value.

        Args:
            attr_name: The attribute name to filter by
            attr_value: The attribute value to match
            attr_type: 'case' or 'file'

        Returns:
            List of cases or files matching the attribute criteria
        """
        if not isinstance(attr_name, str) or not isinstance(attr_value, str):
            raise TypeError("attr_name and attr_value must be strings")

        if attr_type not in ['case', 'file']:
            raise ValueError("attr_type must be 'case' or 'file'")

        try:
            if attr_type == 'case':
                cursor = self.conn.execute("""
                    SELECT
                        c.caseid,
                        c.name,
                        c.memo,
                        a.value as attr_value
                    FROM cases c
                    JOIN attribute a ON c.caseid = a.id AND a.attr_type = 'case'
                    WHERE a.name = ? AND a.value = ?
                    ORDER BY c.name
                """, (attr_name, attr_value))

                results = []
                for row in cursor.fetchall():
                    results.append({
                        "case_id": row["caseid"],
                        "name": row["name"],
                        "memo": row["memo"] or "",
                        "attribute_value": row["attr_value"]
                    })
            else:  # file
                cursor = self.conn.execute("""
                    SELECT
                        s.id,
                        s.name,
                        s.memo,
                        a.value as attr_value
                    FROM source s
                    JOIN attribute a ON s.id = a.id AND a.attr_type = 'file'
                    WHERE a.name = ? AND a.value = ?
                    ORDER BY s.name
                """, (attr_name, attr_value))

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

        try:
            if window_size == 0:
                # Find overlapping segments
                cursor = self.conn.execute("""
                    SELECT
                        c.cid,
                        c.name as code_name,
                        c.color,
                        cat.name as category_name,
                        COUNT(*) as cooccurrence_count
                    FROM code_text ct1
                    JOIN code_text ct2 ON ct1.fid = ct2.fid
                        AND ct1.cid != ct2.cid
                        AND (
                            (ct2.pos0 >= ct1.pos0 AND ct2.pos0 <= ct1.pos1)
                            OR (ct2.pos1 >= ct1.pos0 AND ct2.pos1 <= ct1.pos1)
                            OR (ct2.pos0 <= ct1.pos0 AND ct2.pos1 >= ct1.pos1)
                        )
                    JOIN code_name c ON ct2.cid = c.cid
                    LEFT JOIN code_cat cat ON c.catid = cat.catid
                    WHERE ct1.cid = ?
                    GROUP BY c.cid, c.name, c.color, cat.name
                    ORDER BY cooccurrence_count DESC
                """, (code_id,))
            else:
                # Find codes within window
                cursor = self.conn.execute("""
                    SELECT
                        c.cid,
                        c.name as code_name,
                        c.color,
                        cat.name as category_name,
                        COUNT(*) as cooccurrence_count
                    FROM code_text ct1
                    JOIN code_text ct2 ON ct1.fid = ct2.fid
                        AND ct1.cid != ct2.cid
                        AND ABS(ct2.pos0 - ct1.pos0) <= ?
                    JOIN code_name c ON ct2.cid = c.cid
                    LEFT JOIN code_cat cat ON c.catid = cat.catid
                    WHERE ct1.cid = ?
                    GROUP BY c.cid, c.name, c.color, cat.name
                    ORDER BY cooccurrence_count DESC
                """, (window_size, code_id))

            cooccurrences = []
            for row in cursor.fetchall():
                cooccurrences.append({
                    "code_id": row["cid"],
                    "code_name": row["code_name"],
                    "color": row["color"],
                    "category": row["category_name"],
                    "cooccurrence_count": row["cooccurrence_count"]
                })

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
                    AND ((ct.pos0 >= cs.pos0 AND ct.pos0 <= cs.pos1)
                         OR (ct.pos1 >= cs.pos0 AND ct.pos1 <= cs.pos1)
                         OR (ct.pos0 <= cs.pos0 AND ct.pos1 >= cs.pos1))
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
                    AND ((ct.pos0 >= cs.pos0 AND ct.pos0 <= cs.pos1)
                         OR (ct.pos1 >= cs.pos0 AND ct.pos1 <= cs.pos1)
                         OR (ct.pos0 <= cs.pos0 AND ct.pos1 >= cs.pos1))
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
                    AND ((ct.pos0 >= cs.pos0 AND ct.pos0 <= cs.pos1)
                         OR (ct.pos1 >= cs.pos0 AND ct.pos1 <= cs.pos1)
                         OR (ct.pos0 <= cs.pos0 AND ct.pos1 >= cs.pos1))
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
        """Check that database was opened with write access.

        Raises:
            RuntimeError: If database is in read-only mode
        """
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
        important: int = 0,
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
            important: Importance flag (0 or 1)
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

        # Get file content and validate positions
        file_content = self.get_file_content(file_id)
        if file_content and file_content.get("content"):
            content_length = len(file_content["content"])
            if end_pos > content_length:
                raise ValueError(f"end_pos ({end_pos}) exceeds file length ({content_length})")

        # Current timestamp
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            cursor = self.conn.execute("""
                INSERT INTO code_text (cid, fid, seltext, pos0, pos1, owner, date, memo, important)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (code_id, file_id, selected_text, start_pos, end_pos, owner, date_str, memo or "", important))

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
        color: str = "#FFFFFF"
    ) -> int:
        """Add a new code to the project.

        Args:
            name: Code name (must be unique)
            owner: Name of the person creating the code
            memo: Optional description/definition of the code
            category_id: Optional category ID to place code in
            color: Hex color code (default white)

        Returns:
            The cid (code ID) of the newly created code

        Raises:
            ValueError: If validation fails
            RuntimeError: If database is read-only or operation fails
        """
        self._require_write_access()
        if not name or not isinstance(name, str):
            raise ValueError("name must be a non-empty string")

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

        # Validate color format
        if not color.startswith("#") or len(color) != 7:
            raise ValueError(f"color must be hex format #RRGGBB, got {color}")

        # Current timestamp
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            cursor = self.conn.execute("""
                INSERT INTO code_name (name, memo, catid, owner, date, color)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (name, memo or "", category_id, owner, date_str, color))

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

        # Update timestamp
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            self.conn.execute("""
                UPDATE code_text
                SET memo = ?, date = ?
                WHERE ctid = ?
            """, (memo, date_str, coding_id))

            self.conn.commit()
            logger.info(f"Updated memo for coding {coding_id}")

        except sqlite3.Error as e:
            self.conn.rollback()
            logger.error(f"Database error in add_memo_to_coding: {e}")
            raise RuntimeError(f"Failed to update memo: {e}") from None

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

        # Validate name
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty string")
        name = name.strip()
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
        existing = self.conn.execute(
            "SELECT id FROM source WHERE name = ?", (name,)
        ).fetchone()
        if existing:
            raise ValueError(
                f"A file named '{name}' already exists (id={existing['id']})"
            )

        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            cursor = self.conn.execute("""
                INSERT INTO source (name, fulltext, mediapath, memo, owner, date)
                VALUES (?, ?, NULL, ?, ?, ?)
            """, (name, content, memo, owner, date_str))
            file_id = cursor.lastrowid

            # Create attribute placeholders for file-type attribute types
            attr_types = self.conn.execute(
                "SELECT name FROM attribute_type WHERE caseOrFile IN ('file', 'both')"
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

    def backup_before_write(self) -> Path:
        """Create a backup of the current project before making changes.

        Returns:
            Path to the backup folder

        Raises:
            OSError: If backup fails
        """
        return backup_project(self.db_path)
