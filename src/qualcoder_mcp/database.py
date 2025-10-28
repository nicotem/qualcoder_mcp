"""Database interface for Qualcoder .qda files."""

import sqlite3
from pathlib import Path
from typing import Optional, List, Dict, Any
import json


class QualcoderDatabase:
    """Interface to read data from a Qualcoder SQLite database."""

    def __init__(self, db_path: str):
        """Initialize connection to Qualcoder database.

        Args:
            db_path: Path to the .qda database file
        """
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(f"Database file not found: {db_path}")

        # Read-only connection to prevent accidental modifications
        self.conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row  # Access columns by name

    def __del__(self):
        """Close database connection on cleanup."""
        if hasattr(self, 'conn'):
            self.conn.close()

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
            Code details including statistics
        """
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

    def get_coded_text_segments(self, code_id: int, limit: int = 100) -> List[Dict[str, Any]]:
        """Get text segments coded with a specific code.

        Args:
            code_id: The code ID (cid)
            limit: Maximum number of segments to return

        Returns:
            List of coded text segments with context
        """
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
                         limit: int = 50) -> List[Dict[str, Any]]:
        """Search for coded text segments.

        Args:
            query: Text to search for
            code_name: Optional code name to filter by
            limit: Maximum results to return

        Returns:
            List of matching coded segments
        """
        if code_name:
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
                WHERE ct.seltext LIKE ? AND c.name = ?
                ORDER BY s.name, ct.pos0
                LIMIT ?
            """, (f"%{query}%", code_name, limit))
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
                WHERE ct.seltext LIKE ?
                ORDER BY s.name, ct.pos0
                LIMIT ?
            """, (f"%{query}%", limit))

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

    def search_memos(self, query: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Search for memos and annotations.

        Args:
            query: Text to search for
            limit: Maximum results

        Returns:
            List of matching memos
        """
        results = []

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
            WHERE memo LIKE ?
            LIMIT ?
        """, (f"%{query}%", limit))

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
                WHERE memo LIKE ?
                LIMIT ?
            """, (f"%{query}%", limit - len(results)))

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
                WHERE a.memo LIKE ?
                LIMIT ?
            """, (f"%{query}%", limit - len(results)))

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
