"""Database interface for Qualcoder .qda files."""

import sqlite3
from pathlib import Path
from typing import Optional, List, Dict, Any
import json
import logging

logger = logging.getLogger(__name__)

# Configuration constants
DEFAULT_LIMIT = 50
MAX_LIMIT = 5000
SUPPORTED_DB_VERSIONS = ['v6', 'v7', 'v8', 'v9', 'v10', 'v11', 'v12', 'v13']


def validate_qda_path(db_path: str) -> Path:
    """Validate that the path is a legitimate .qda file.

    Args:
        db_path: Path to validate

    Returns:
        Resolved Path object

    Raises:
        ValueError: If path is invalid
        FileNotFoundError: If file doesn't exist
    """
    try:
        # Resolve to absolute path, following symlinks
        path = Path(db_path).resolve(strict=False)
    except (OSError, RuntimeError) as e:
        raise ValueError(f"Invalid path: {e}")

    # Check file extension
    if path.suffix.lower() != '.qda':
        raise ValueError(f"Invalid file extension: must be .qda, got {path.suffix}")

    # Check file exists
    if not path.exists():
        raise FileNotFoundError(f"Database file not found: {path}")

    # Check it's a regular file (not a directory or device)
    if not path.is_file():
        raise ValueError(f"Path is not a regular file: {path}")

    # Basic SQLite validation
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 1")
        cursor.fetchone()
        conn.close()
    except sqlite3.DatabaseError as e:
        raise ValueError(f"Invalid or corrupted SQLite database: {e}")

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

    def __init__(self, db_path: str):
        """Initialize connection to Qualcoder database.

        Args:
            db_path: Path to the .qda database file

        Raises:
            ValueError: If path validation fails
            FileNotFoundError: If database file doesn't exist
        """
        # Validate path before opening
        self.db_path = validate_qda_path(db_path)

        # Read-only connection to prevent accidental modifications
        try:
            self.conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            self.conn.row_factory = sqlite3.Row  # Access columns by name
        except sqlite3.Error as e:
            raise RuntimeError(f"Failed to open database: {e}") from e

        # Validate this is a Qualcoder database
        self._validate_schema()

        # Check database version
        self._check_version()

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
        except sqlite3.Error as e:
            raise RuntimeError(f"Failed to validate database schema: {e}") from e

    def _check_version(self):
        """Check database version and log warnings if unsupported."""
        try:
            cursor = self.conn.execute("SELECT databaseversion FROM project")
            row = cursor.fetchone()
            if row:
                version = row[0]
                if version not in SUPPORTED_DB_VERSIONS:
                    logger.warning(
                        f"Untested database version: {version}. "
                        f"Supported versions: {SUPPORTED_DB_VERSIONS}"
                    )
                else:
                    logger.info(f"Connected to Qualcoder database version {version}")
        except sqlite3.Error as e:
            logger.warning(f"Could not determine database version: {e}")

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
            logger.error(f"Database error in get_code_details: {e}")
            raise RuntimeError("Failed to retrieve code details") from None

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
            logger.error(f"Database error in get_coded_text_segments: {e}")
            raise RuntimeError("Failed to retrieve coded text segments") from None

    def list_files(self) -> List[Dict[str, Any]]:
        """Get all source files in the project.

        Returns:
            List of files with metadata
        """
        cursor = self.conn.execute("""
            SELECT
                id,
                name,
                memo,
                owner,
                date,
                mediapath,
                CASE
                    WHEN mediapath IS NULL OR mediapath = '' THEN 'text'
                    WHEN mediapath LIKE '%.mp3' OR mediapath LIKE '%.wav'
                         OR mediapath LIKE '%.m4a' THEN 'audio'
                    WHEN mediapath LIKE '%.mp4' OR mediapath LIKE '%.avi'
                         OR mediapath LIKE '%.mov' THEN 'video'
                    WHEN mediapath LIKE '%.jpg' OR mediapath LIKE '%.png'
                         OR mediapath LIKE '%.gif' THEN 'image'
                    WHEN mediapath LIKE '%.pdf' THEN 'pdf'
                    ELSE 'media'
                END as file_type
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
                "type": row["file_type"],
                "media_path": row["mediapath"]
            })
        return files

    def get_file_content(self, file_id: int) -> Optional[Dict[str, Any]]:
        """Get the content of a text file.

        Args:
            file_id: The file ID

        Returns:
            File content and metadata
        """
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
            "is_text": not row["mediapath"] or row["mediapath"] == "",
            "code_count": code_count
        }

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
        escaped_query = escape_like_pattern(query)
        limit = validate_limit(limit)

        try:
            if code_name:
                if not isinstance(code_name, str):
                    raise TypeError(f"code_name must be a string, got {type(code_name).__name__}")

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
            logger.error(f"Database error in search_coded_text: {e}")
            raise RuntimeError("Failed to search coded text") from None

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
            logger.error(f"Database error in search_memos: {e}")
            raise RuntimeError("Failed to search memos") from None

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
