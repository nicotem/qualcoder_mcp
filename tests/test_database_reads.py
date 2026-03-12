"""
Comprehensive tests for QualcoderDatabase read operations.

These tests use a real SQLite database fixture instead of mocks,
ensuring the actual SQL queries work correctly against the real schema.
"""

import pytest
import sqlite3
import tempfile
import shutil
from pathlib import Path
from datetime import datetime

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from qualcoder_mcp.database import (
    QualcoderDatabase,
    validate_qda_path,
    validate_limit,
    validate_id,
    escape_like_pattern,
    DEFAULT_LIMIT,
    MAX_LIMIT,
)


# =============================================================================
# FIXTURE: Create a realistic QualCoder database
# =============================================================================

@pytest.fixture
def qualcoder_db_path(tmp_path):
    """
    Create a complete QualCoder-compatible SQLite database for testing.

    This fixture creates a .qda folder structure with a data.qda SQLite file
    containing all required tables and realistic test data.
    """
    # Create .qda folder structure
    project_folder = tmp_path / "test_project.qda"
    project_folder.mkdir()
    db_file = project_folder / "data.qda"

    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()

    # Create all required tables matching QualCoder schema

    # Project table
    cursor.execute("""
        CREATE TABLE project (
            databaseversion TEXT,
            date TEXT,
            memo TEXT,
            about TEXT,
            codername TEXT
        )
    """)
    cursor.execute("""
        INSERT INTO project VALUES ('v12', '2024-01-15',
            'Test project for MCP integration',
            'Qualitative research on workplace dynamics',
            'TestCoder')
    """)

    # Code categories table
    cursor.execute("""
        CREATE TABLE code_cat (
            catid INTEGER PRIMARY KEY,
            name TEXT UNIQUE,
            memo TEXT,
            owner TEXT,
            date TEXT,
            supercatid INTEGER
        )
    """)
    # Insert categories
    categories = [
        (1, "Emotional States", "Codes related to emotions", "TestCoder", "2024-01-15", None),
        (2, "Work Environment", "Codes about workplace", "TestCoder", "2024-01-15", None),
        (3, "Stress Responses", "Sub-category of emotions", "TestCoder", "2024-01-16", 1),
    ]
    cursor.executemany("INSERT INTO code_cat VALUES (?, ?, ?, ?, ?, ?)", categories)

    # Code names table
    cursor.execute("""
        CREATE TABLE code_name (
            cid INTEGER PRIMARY KEY,
            name TEXT UNIQUE,
            memo TEXT,
            catid INTEGER,
            owner TEXT,
            date TEXT,
            color TEXT,
            FOREIGN KEY (catid) REFERENCES code_cat(catid)
        )
    """)
    # Insert codes
    codes = [
        (1, "Workplace Stress", "Expressions of work-related stress", 3, "TestCoder", "2024-01-15", "#FF5733"),
        (2, "Coping Strategies", "Methods of dealing with challenges", 1, "TestCoder", "2024-01-15", "#33FF57"),
        (3, "Team Dynamics", "Interactions between colleagues", 2, "TestCoder", "2024-01-15", "#3357FF"),
        (4, "Positive Emotions", "Joy, satisfaction, etc.", 1, "TestCoder", "2024-01-16", "#FFD700"),
        (5, "Uncategorized Code", "A code without category", None, "TestCoder", "2024-01-17", "#FFFFFF"),
    ]
    cursor.executemany("INSERT INTO code_name VALUES (?, ?, ?, ?, ?, ?, ?)", codes)

    # Source files table
    cursor.execute("""
        CREATE TABLE source (
            id INTEGER PRIMARY KEY,
            name TEXT,
            fulltext TEXT,
            memo TEXT,
            owner TEXT,
            date TEXT,
            mediapath TEXT
        )
    """)
    # Insert source files with realistic content
    files = [
        (1, "Interview_Participant_A.txt",
         """This is the transcript of Interview A.

         Participant: I often feel overwhelmed with the workload. The deadlines are tight and there's always pressure to perform.

         Interviewer: How do you cope with that stress?

         Participant: I try to take breaks when I can. Sometimes I go for a walk during lunch. My colleagues are supportive, which helps a lot.

         The team dynamics are generally positive despite the stress.""",
         "Interview conducted on Jan 10", "TestCoder", "2024-01-10", None),

        (2, "Interview_Participant_B.txt",
         """Interview B transcript.

         Participant: My experience has been mostly positive. The work is challenging but rewarding.

         I feel satisfied when we complete projects on time. The team works well together.

         There are occasional conflicts but we resolve them through discussion.""",
         "Interview conducted on Jan 12", "TestCoder", "2024-01-12", None),

        (3, "Interview_Participant_C.txt",
         """Participant C discusses their challenges.

         The workload can be overwhelming sometimes. I've developed some coping strategies over time.

         Taking regular breaks helps me stay focused. I also practice mindfulness.

         Team support is crucial for managing stress.""",
         "Focuses on coping mechanisms", "TestCoder", "2024-01-14", None),

        (4, "Audio_Recording_01.mp3", None, "Audio file", "TestCoder", "2024-01-15", "/audio/recording01.mp3"),

        (5, "Image_Chart.png", None, "Data visualization", "TestCoder", "2024-01-16", "/images/chart.png"),
    ]
    cursor.executemany("INSERT INTO source VALUES (?, ?, ?, ?, ?, ?, ?)", files)

    # Code text (coded segments) table
    cursor.execute("""
        CREATE TABLE code_text (
            ctid INTEGER PRIMARY KEY,
            cid INTEGER,
            fid INTEGER,
            seltext TEXT,
            pos0 INTEGER,
            pos1 INTEGER,
            owner TEXT,
            date TEXT,
            memo TEXT,
            important INTEGER DEFAULT 0,
            FOREIGN KEY (cid) REFERENCES code_name(cid),
            FOREIGN KEY (fid) REFERENCES source(id)
        )
    """)
    # Insert coded segments
    coded_segments = [
        # File 1 codings
        (1, 1, 1, "I often feel overwhelmed with the workload", 57, 100, "TestCoder", "2024-01-15", "Clear stress expression", 1),
        (2, 1, 1, "The deadlines are tight and there's always pressure to perform", 102, 164, "TestCoder", "2024-01-15", "", 0),
        (3, 2, 1, "I try to take breaks when I can", 210, 241, "TestCoder", "2024-01-15", "Active coping", 0),
        (4, 2, 1, "Sometimes I go for a walk during lunch", 243, 281, "TestCoder", "2024-01-15", "", 0),
        (5, 3, 1, "My colleagues are supportive, which helps a lot", 283, 329, "TestCoder", "2024-01-16", "", 0),
        (6, 3, 1, "The team dynamics are generally positive", 348, 388, "TestCoder", "2024-01-16", "", 0),

        # File 2 codings
        (7, 4, 2, "My experience has been mostly positive", 26, 64, "TestCoder", "2024-01-16", "", 0),
        (8, 4, 2, "I feel satisfied when we complete projects on time", 102, 152, "TestCoder", "2024-01-16", "Positive outcome", 1),
        (9, 3, 2, "The team works well together", 154, 182, "TestCoder", "2024-01-16", "", 0),

        # File 3 codings
        (10, 1, 3, "The workload can be overwhelming sometimes", 50, 92, "TestCoder", "2024-01-17", "", 0),
        (11, 2, 3, "I've developed some coping strategies", 94, 131, "TestCoder", "2024-01-17", "", 0),
        (12, 2, 3, "Taking regular breaks helps me stay focused", 149, 192, "TestCoder", "2024-01-17", "", 0),
        (13, 3, 3, "Team support is crucial for managing stress", 248, 291, "TestCoder", "2024-01-17", "", 0),
    ]
    cursor.executemany("INSERT INTO code_text VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", coded_segments)

    # Cases table
    cursor.execute("""
        CREATE TABLE cases (
            caseid INTEGER PRIMARY KEY,
            name TEXT,
            memo TEXT,
            owner TEXT,
            date TEXT
        )
    """)
    cases = [
        (1, "Participant A", "Female, 30s, Manager", "TestCoder", "2024-01-10"),
        (2, "Participant B", "Male, 40s, Developer", "TestCoder", "2024-01-12"),
        (3, "Participant C", "Non-binary, 25, Analyst", "TestCoder", "2024-01-14"),
    ]
    cursor.executemany("INSERT INTO cases VALUES (?, ?, ?, ?, ?)", cases)

    # Case text (linking cases to file segments)
    cursor.execute("""
        CREATE TABLE case_text (
            id INTEGER PRIMARY KEY,
            caseid INTEGER,
            fid INTEGER,
            pos0 INTEGER,
            pos1 INTEGER,
            memo TEXT,
            owner TEXT,
            date TEXT,
            FOREIGN KEY (caseid) REFERENCES cases(caseid),
            FOREIGN KEY (fid) REFERENCES source(id)
        )
    """)
    case_links = [
        (1, 1, 1, 0, 500, "", "TestCoder", "2024-01-10"),  # Case 1 linked to file 1
        (2, 2, 2, 0, 300, "", "TestCoder", "2024-01-12"),  # Case 2 linked to file 2
        (3, 3, 3, 0, 300, "", "TestCoder", "2024-01-14"),  # Case 3 linked to file 3
    ]
    cursor.executemany("INSERT INTO case_text VALUES (?, ?, ?, ?, ?, ?, ?, ?)", case_links)

    # Annotation table
    cursor.execute("""
        CREATE TABLE annotation (
            anid INTEGER PRIMARY KEY,
            fid INTEGER,
            pos0 INTEGER,
            pos1 INTEGER,
            memo TEXT,
            owner TEXT,
            date TEXT,
            FOREIGN KEY (fid) REFERENCES source(id)
        )
    """)
    annotations = [
        (1, 1, 50, 100, "Key passage about stress", "TestCoder", "2024-01-15"),
        (2, 1, 200, 250, "Coping strategy mentioned here", "TestCoder", "2024-01-15"),
        (3, 2, 100, 160, "Positive experience note", "TestCoder", "2024-01-16"),
    ]
    cursor.executemany("INSERT INTO annotation VALUES (?, ?, ?, ?, ?, ?, ?)", annotations)

    # Journal table
    cursor.execute("""
        CREATE TABLE journal (
            jid INTEGER PRIMARY KEY,
            name TEXT,
            jentry TEXT,
            date TEXT,
            owner TEXT
        )
    """)
    journal_entries = [
        (1, "Project Start", "Beginning analysis of workplace stress interviews.", "2024-01-15", "TestCoder"),
        (2, "Initial Coding", "Completed first round of coding on 3 interviews.", "2024-01-17", "TestCoder"),
    ]
    cursor.executemany("INSERT INTO journal VALUES (?, ?, ?, ?, ?)", journal_entries)

    # Attribute type table
    cursor.execute("""
        CREATE TABLE attribute_type (
            name TEXT PRIMARY KEY,
            date TEXT,
            owner TEXT,
            memo TEXT,
            caseOrFile TEXT,
            valuetype TEXT
        )
    """)
    attr_types = [
        ("Age", "2024-01-10", "TestCoder", "Participant age", "case", "numeric"),
        ("Gender", "2024-01-10", "TestCoder", "Participant gender", "case", "character"),
        ("Department", "2024-01-10", "TestCoder", "Work department", "case", "character"),
        ("Interview_Length", "2024-01-10", "TestCoder", "Length in minutes", "file", "numeric"),
    ]
    cursor.executemany("INSERT INTO attribute_type VALUES (?, ?, ?, ?, ?, ?)", attr_types)

    # Attribute table
    cursor.execute("""
        CREATE TABLE attribute (
            attrid INTEGER PRIMARY KEY,
            name TEXT,
            attr_type TEXT,
            value TEXT,
            id INTEGER,
            date TEXT,
            owner TEXT,
            FOREIGN KEY (name) REFERENCES attribute_type(name)
        )
    """)
    attributes = [
        (1, "Age", "case", "35", 1, "2024-01-10", "TestCoder"),
        (2, "Gender", "case", "Female", 1, "2024-01-10", "TestCoder"),
        (3, "Department", "case", "Management", 1, "2024-01-10", "TestCoder"),
        (4, "Age", "case", "42", 2, "2024-01-12", "TestCoder"),
        (5, "Gender", "case", "Male", 2, "2024-01-12", "TestCoder"),
        (6, "Department", "case", "Engineering", 2, "2024-01-12", "TestCoder"),
        (7, "Age", "case", "25", 3, "2024-01-14", "TestCoder"),
        (8, "Gender", "case", "Non-binary", 3, "2024-01-14", "TestCoder"),
        (9, "Department", "case", "Analytics", 3, "2024-01-14", "TestCoder"),
        (10, "Interview_Length", "file", "45", 1, "2024-01-10", "TestCoder"),
        (11, "Interview_Length", "file", "30", 2, "2024-01-12", "TestCoder"),
    ]
    cursor.executemany("INSERT INTO attribute VALUES (?, ?, ?, ?, ?, ?, ?)", attributes)

    # Code image table (empty but required for schema validation)
    cursor.execute("""
        CREATE TABLE code_image (
            imid INTEGER PRIMARY KEY,
            cid INTEGER,
            id INTEGER,
            x1 REAL,
            y1 REAL,
            width REAL,
            height REAL,
            memo TEXT,
            owner TEXT,
            date TEXT,
            important INTEGER DEFAULT 0
        )
    """)

    # Code AV table (empty but required for schema validation)
    cursor.execute("""
        CREATE TABLE code_av (
            avid INTEGER PRIMARY KEY,
            cid INTEGER,
            id INTEGER,
            pos0 INTEGER,
            pos1 INTEGER,
            memo TEXT,
            owner TEXT,
            date TEXT,
            important INTEGER DEFAULT 0
        )
    """)

    conn.commit()
    conn.close()

    yield str(project_folder)

    # Cleanup is handled by tmp_path fixture


@pytest.fixture
def db(qualcoder_db_path):
    """Create a QualcoderDatabase instance from the test database."""
    database = QualcoderDatabase(qualcoder_db_path)
    yield database
    database.close()


# =============================================================================
# TESTS: Validation Functions
# =============================================================================

class TestValidationFunctions:
    """Test the standalone validation functions."""

    def test_validate_qda_path_with_folder(self, qualcoder_db_path):
        """Test path validation with .qda folder."""
        path = validate_qda_path(qualcoder_db_path)
        assert path.name == "data.qda"
        assert path.exists()

    def test_validate_qda_path_with_data_file(self, qualcoder_db_path):
        """Test path validation with direct data.qda path."""
        data_file = Path(qualcoder_db_path) / "data.qda"
        path = validate_qda_path(str(data_file))
        assert path.name == "data.qda"

    def test_validate_qda_path_nonexistent(self, tmp_path):
        """Test path validation with non-existent path."""
        with pytest.raises(FileNotFoundError):
            validate_qda_path(str(tmp_path / "nonexistent.qda"))

    def test_validate_qda_path_invalid_directory(self, tmp_path):
        """Test path validation with directory without .qda extension."""
        invalid_dir = tmp_path / "not_qda"
        invalid_dir.mkdir()
        with pytest.raises(ValueError, match="must have .qda extension"):
            validate_qda_path(str(invalid_dir))

    def test_validate_limit_normal(self):
        """Test limit validation with normal value."""
        assert validate_limit(50) == 50
        assert validate_limit(100) == 100

    def test_validate_limit_exceeds_max(self):
        """Test limit validation when exceeding maximum."""
        result = validate_limit(10000)
        assert result == MAX_LIMIT

    def test_validate_limit_invalid_type(self):
        """Test limit validation with wrong type."""
        with pytest.raises(TypeError):
            validate_limit("50")

    def test_validate_limit_negative(self):
        """Test limit validation with negative value."""
        with pytest.raises(ValueError, match="must be positive"):
            validate_limit(-1)

    def test_validate_id_normal(self):
        """Test ID validation with normal value."""
        assert validate_id(1) == 1
        assert validate_id(0) == 0

    def test_validate_id_negative(self):
        """Test ID validation with negative value."""
        with pytest.raises(ValueError, match="must be non-negative"):
            validate_id(-1, "code_id")

    def test_validate_id_wrong_type(self):
        """Test ID validation with wrong type."""
        with pytest.raises(TypeError):
            validate_id("1", "code_id")

    def test_escape_like_pattern(self):
        """Test escaping of SQL LIKE wildcards."""
        assert escape_like_pattern("test") == "test"
        assert escape_like_pattern("test%pattern") == "test\\%pattern"
        assert escape_like_pattern("test_pattern") == "test\\_pattern"
        assert escape_like_pattern("test\\pattern") == "test\\\\pattern"
        assert escape_like_pattern("%_\\") == "\\%\\_\\\\"


# =============================================================================
# TESTS: Project and Schema Operations
# =============================================================================

class TestProjectOperations:
    """Test project-level database operations."""

    def test_database_opens_successfully(self, db):
        """Test that the database opens and validates schema."""
        assert db.conn is not None

    def test_get_project_info(self, db):
        """Test retrieving project metadata."""
        info = db.get_project_info()

        assert info["database_version"] == "v12"
        assert "Test project" in info["memo"]
        assert info["coder_name"] == "TestCoder"
        assert info["date"] == "2024-01-15"

    def test_context_manager(self, qualcoder_db_path):
        """Test database works as context manager."""
        with QualcoderDatabase(qualcoder_db_path) as db:
            info = db.get_project_info()
            assert info["database_version"] == "v12"
        # Connection should be closed after context
        assert db.conn is None


# =============================================================================
# TESTS: Code Operations
# =============================================================================

class TestCodeOperations:
    """Test code listing and retrieval."""

    def test_list_codes(self, db):
        """Test listing all codes."""
        codes = db.list_codes()

        assert len(codes) == 5

        # Check code structure
        code_names = [c["name"] for c in codes]
        assert "Workplace Stress" in code_names
        assert "Coping Strategies" in code_names
        assert "Team Dynamics" in code_names
        assert "Positive Emotions" in code_names
        assert "Uncategorized Code" in code_names

    def test_list_codes_has_categories(self, db):
        """Test that codes include category information."""
        codes = db.list_codes()

        stress_code = next(c for c in codes if c["name"] == "Workplace Stress")
        assert stress_code["category"] == "Stress Responses"
        assert stress_code["category_id"] == 3

        # Uncategorized code should have None category
        uncategorized = next(c for c in codes if c["name"] == "Uncategorized Code")
        assert uncategorized["category"] is None
        assert uncategorized["category_id"] is None

    def test_list_codes_includes_metadata(self, db):
        """Test that codes include all metadata fields."""
        codes = db.list_codes()

        stress_code = next(c for c in codes if c["name"] == "Workplace Stress")
        assert stress_code["id"] == 1
        assert "stress" in stress_code["memo"].lower()
        assert stress_code["color"] == "#FF5733"
        assert stress_code["owner"] == "TestCoder"
        assert stress_code["date"] == "2024-01-15"

    def test_list_categories(self, db):
        """Test listing code categories."""
        categories = db.list_categories()

        assert len(categories) == 3

        cat_names = [c["name"] for c in categories]
        assert "Emotional States" in cat_names
        assert "Work Environment" in cat_names
        assert "Stress Responses" in cat_names

    def test_list_categories_includes_hierarchy(self, db):
        """Test that categories include parent information."""
        categories = db.list_categories()

        stress_cat = next(c for c in categories if c["name"] == "Stress Responses")
        assert stress_cat["parent_id"] == 1  # Parent is "Emotional States"

        emotional_cat = next(c for c in categories if c["name"] == "Emotional States")
        assert emotional_cat["parent_id"] is None  # Top-level

    def test_get_code_details_exists(self, db):
        """Test getting details for existing code."""
        details = db.get_code_details(1)

        assert details is not None
        assert details["name"] == "Workplace Stress"
        assert details["category"] == "Stress Responses"
        assert "statistics" in details
        assert details["statistics"]["text_segments"] == 3  # 3 segments coded with this code
        assert details["statistics"]["image_segments"] == 0
        assert details["statistics"]["av_segments"] == 0

    def test_get_code_details_not_exists(self, db):
        """Test getting details for non-existent code."""
        details = db.get_code_details(999)
        assert details is None

    def test_get_code_details_invalid_id(self, db):
        """Test getting code details with invalid ID."""
        with pytest.raises(ValueError, match="must be non-negative"):
            db.get_code_details(-1)

        with pytest.raises(TypeError):
            db.get_code_details("1")

    def test_get_coded_text_segments(self, db):
        """Test retrieving coded segments for a code."""
        segments = db.get_coded_text_segments(1)  # Workplace Stress

        assert len(segments) == 3

        # Check segment structure
        for seg in segments:
            assert "id" in seg
            assert "text" in seg
            assert "position_start" in seg
            assert "position_end" in seg
            assert "file_name" in seg
            assert "file_id" in seg

    def test_get_coded_text_segments_with_limit(self, db):
        """Test that limit parameter works."""
        segments = db.get_coded_text_segments(1, limit=2)
        assert len(segments) == 2

    def test_get_coded_text_segments_no_results(self, db):
        """Test getting segments for code with no codings."""
        segments = db.get_coded_text_segments(5)  # Uncategorized Code has no segments
        assert len(segments) == 0


# =============================================================================
# TESTS: File Operations
# =============================================================================

class TestFileOperations:
    """Test file listing and content retrieval."""

    def test_list_files(self, db):
        """Test listing all source files."""
        files = db.list_files()

        assert len(files) == 5

        # Check we have different file types
        types = set(f["type"] for f in files)
        assert "text" in types
        assert "audio" in types
        assert "image" in types

    def test_list_files_text_type_detection(self, db):
        """Test that text files are correctly identified."""
        files = db.list_files()

        text_files = [f for f in files if f["type"] == "text"]
        assert len(text_files) == 3

        for tf in text_files:
            assert tf["media_path"] is None or tf["media_path"] == ""

    def test_list_files_media_type_detection(self, db):
        """Test that media files are correctly typed."""
        files = db.list_files()

        audio = next(f for f in files if f["name"] == "Audio_Recording_01.mp3")
        assert audio["type"] == "audio"

        image = next(f for f in files if f["name"] == "Image_Chart.png")
        assert image["type"] == "image"

    def test_get_file_content_text_file(self, db):
        """Test retrieving text file content."""
        content = db.get_file_content(1)

        assert content is not None
        assert content["name"] == "Interview_Participant_A.txt"
        assert "often feel overwhelmed" in content["content"]
        assert content["is_text"] is True
        assert content["code_count"] >= 0

    def test_get_file_content_media_file(self, db):
        """Test retrieving media file info (no content)."""
        content = db.get_file_content(4)  # Audio file

        assert content is not None
        assert content["name"] == "Audio_Recording_01.mp3"
        assert content["content"] == ""  # No text content
        assert content["is_text"] is False

    def test_get_file_content_not_exists(self, db):
        """Test getting content for non-existent file."""
        content = db.get_file_content(999)
        assert content is None

    def test_get_file_content_invalid_id(self, db):
        """Test getting file content with invalid ID."""
        with pytest.raises(ValueError):
            db.get_file_content(-1)

    def test_get_file_with_coding(self, db):
        """Test getting file with all coding information."""
        result = db.get_file_with_coding(1)

        assert result is not None
        assert "file_info" in result
        assert "full_text" in result
        assert "coded_segments" in result
        assert "codes_used" in result
        assert "annotations" in result
        assert "statistics" in result

        # Check file info
        assert result["file_info"]["name"] == "Interview_Participant_A.txt"

        # Check coded segments
        assert len(result["coded_segments"]) == 6  # 6 segments in file 1

        # Check codes used summary
        assert "Workplace Stress" in result["codes_used"]
        assert "Coping Strategies" in result["codes_used"]
        assert "Team Dynamics" in result["codes_used"]

        # Check statistics
        assert result["statistics"]["total_segments"] == 6
        assert result["statistics"]["unique_codes"] == 3

    def test_get_file_with_coding_includes_annotations(self, db):
        """Test that annotations are included."""
        result = db.get_file_with_coding(1)

        assert len(result["annotations"]) == 2
        assert result["annotations"][0]["memo"] == "Key passage about stress"


# =============================================================================
# TESTS: Search Operations
# =============================================================================

class TestSearchOperations:
    """Test search functionality."""

    def test_search_coded_text_basic(self, db):
        """Test basic coded text search."""
        results = db.search_coded_text("overwhelmed")

        assert len(results) >= 1
        assert any("overwhelmed" in r["text"].lower() for r in results)

    def test_search_coded_text_with_code_filter(self, db):
        """Test searching within specific code."""
        results = db.search_coded_text("feel", code_name="Workplace Stress")

        assert len(results) >= 1
        for r in results:
            assert r["code_name"] == "Workplace Stress"

    def test_search_coded_text_no_results(self, db):
        """Test search with no matching results."""
        results = db.search_coded_text("xyznonexistent123")
        assert len(results) == 0

    def test_search_coded_text_with_limit(self, db):
        """Test search result limiting."""
        results = db.search_coded_text("the", limit=2)
        assert len(results) <= 2

    def test_search_coded_text_sql_injection_safe(self, db):
        """Test that SQL special characters are safely handled."""
        # This should not cause SQL errors
        results = db.search_coded_text("'; DROP TABLE code_text; --")
        assert isinstance(results, list)

        results = db.search_coded_text("100% complete")
        assert isinstance(results, list)

    def test_search_files_by_filename(self, db):
        """Test searching files by filename."""
        result = db.search_files("Participant_A", search_filename=True, search_content=False)

        assert result["total_matches"] >= 1
        assert any("Participant_A" in r["file_name"] for r in result["results"])

    def test_search_files_by_content(self, db):
        """Test searching files by content."""
        result = db.search_files("overwhelmed", search_filename=False, search_content=True)

        assert result["total_matches"] >= 1

        # Check that match location is correctly identified
        for r in result["results"]:
            if r["matched_in"]["content"]:
                assert any(m["location"] == "content" for m in r["matches"])

    def test_search_files_by_memo(self, db):
        """Test searching files by memo."""
        result = db.search_files("coping", search_filename=False, search_content=False, search_memo=True)

        assert result["total_matches"] >= 1

    def test_search_files_case_insensitive(self, db):
        """Test case-insensitive search (default)."""
        result1 = db.search_files("OVERWHELMED", search_content=True)
        result2 = db.search_files("overwhelmed", search_content=True)

        assert result1["total_matches"] == result2["total_matches"]

    def test_search_files_case_sensitive(self, db):
        """Test case-sensitive search."""
        result = db.search_files("OVERWHELMED", search_content=True, case_sensitive=True)
        # Should find fewer or no results since text is lowercase
        assert result["total_matches"] == 0

    def test_search_files_empty_pattern(self, db):
        """Test search with empty pattern."""
        result = db.search_files("")
        assert result["total_matches"] == 0

    def test_search_memos(self, db):
        """Test searching through memos and annotations."""
        results = db.search_memos("stress")

        assert len(results) >= 1
        # Results can come from codes, files, or annotations
        types = set(r["type"] for r in results)
        assert len(types) >= 1


# =============================================================================
# TESTS: Case Operations
# =============================================================================

class TestCaseOperations:
    """Test case listing and retrieval."""

    def test_list_cases(self, db):
        """Test listing all cases."""
        cases = db.list_cases()

        assert len(cases) == 3

        case_names = [c["name"] for c in cases]
        assert "Participant A" in case_names
        assert "Participant B" in case_names
        assert "Participant C" in case_names

    def test_list_cases_includes_counts(self, db):
        """Test that cases include text segment counts."""
        cases = db.list_cases()

        for case in cases:
            assert "text_segment_count" in case
            assert case["text_segment_count"] >= 0

    def test_get_case_details_exists(self, db):
        """Test getting details for existing case."""
        details = db.get_case_details(1)

        assert details is not None
        assert details["name"] == "Participant A"
        assert "Female" in details["memo"]
        assert "text_segments" in details

    def test_get_case_details_not_exists(self, db):
        """Test getting details for non-existent case."""
        details = db.get_case_details(999)
        assert details is None


# =============================================================================
# TESTS: Attribute Operations
# =============================================================================

class TestAttributeOperations:
    """Test attribute (demographic) operations."""

    def test_list_attribute_types(self, db):
        """Test listing attribute type definitions."""
        types = db.list_attribute_types()

        assert len(types) == 4

        type_names = [t["name"] for t in types]
        assert "Age" in type_names
        assert "Gender" in type_names
        assert "Department" in type_names
        assert "Interview_Length" in type_names

    def test_list_attribute_types_includes_metadata(self, db):
        """Test that attribute types include full metadata."""
        types = db.list_attribute_types()

        age_type = next(t for t in types if t["name"] == "Age")
        assert age_type["applies_to"] == "case"
        assert age_type["value_type"] == "numeric"

    def test_get_case_attributes(self, db):
        """Test getting attributes for a case."""
        attrs = db.get_case_attributes(1)

        assert len(attrs) == 3  # Age, Gender, Department

        attr_names = [a["name"] for a in attrs]
        assert "Age" in attr_names
        assert "Gender" in attr_names

        age_attr = next(a for a in attrs if a["name"] == "Age")
        assert age_attr["value"] == "35"

    def test_get_file_attributes(self, db):
        """Test getting attributes for a file."""
        attrs = db.get_file_attributes(1)

        assert len(attrs) == 1  # Interview_Length
        assert attrs[0]["name"] == "Interview_Length"
        assert attrs[0]["value"] == "45"

    def test_query_by_attribute(self, db):
        """Test querying cases by attribute value."""
        results = db.query_by_attribute("Gender", "Female", "case")

        assert len(results) == 1
        assert results[0]["name"] == "Participant A"

    def test_query_by_attribute_no_results(self, db):
        """Test querying with no matching results."""
        results = db.query_by_attribute("Gender", "Unknown", "case")
        assert len(results) == 0


# =============================================================================
# TESTS: Analysis Operations
# =============================================================================

class TestAnalysisOperations:
    """Test analysis and aggregation operations."""

    def test_get_coding_frequencies(self, db):
        """Test getting code frequency counts."""
        freq = db.get_coding_frequencies()

        assert "total_coded_segments" in freq
        assert "codes" in freq
        assert freq["total_coded_segments"] == 13  # Total segments in fixture

        # Check that frequencies are sorted descending
        codes = freq["codes"]
        assert len(codes) == 5

        # Verify frequency counts
        coping = next(c for c in codes if c["code_name"] == "Coping Strategies")
        assert coping["frequency"] == 4  # 4 segments coded with Coping Strategies

    def test_find_code_cooccurrences_overlap(self, db):
        """Test finding overlapping code co-occurrences."""
        # This tests codes that appear in the same or overlapping segments
        cooccurrences = db.find_code_cooccurrences(1, window_size=0)

        # Result depends on actual overlapping segments in fixture
        assert isinstance(cooccurrences, list)
        for cooc in cooccurrences:
            assert "code_id" in cooc
            assert "code_name" in cooc
            assert "cooccurrence_count" in cooc

    def test_find_code_cooccurrences_with_window(self, db):
        """Test finding co-occurrences within character window."""
        cooccurrences = db.find_code_cooccurrences(1, window_size=100)

        assert isinstance(cooccurrences, list)

    def test_get_case_code_matrix(self, db):
        """Test getting case-code cross-tabulation."""
        matrix = db.get_case_code_matrix()

        assert "cases" in matrix
        assert "codes" in matrix
        assert "matrix" in matrix

        assert len(matrix["cases"]) == 3
        assert len(matrix["codes"]) == 5

    def test_get_codes_by_case(self, db):
        """Test getting codes used in a specific case."""
        codes = db.get_codes_by_case(1)

        assert isinstance(codes, list)
        for code in codes:
            assert "code_id" in code
            assert "code_name" in code
            assert "occurrence_count" in code

    def test_get_cases_by_code(self, db):
        """Test getting cases containing a specific code."""
        cases = db.get_cases_by_code(1)  # Workplace Stress

        assert isinstance(cases, list)
        # Should include cases where this code appears
        for case in cases:
            assert "case_id" in case
            assert "case_name" in case
            assert "occurrence_count" in case


# =============================================================================
# TESTS: Journal Operations
# =============================================================================

class TestJournalOperations:
    """Test journal entry retrieval."""

    def test_get_journal_entries(self, db):
        """Test getting all journal entries."""
        entries = db.get_journal_entries()

        assert len(entries) == 2

        # Check entries are sorted by date descending
        assert entries[0]["name"] == "Initial Coding"  # Most recent
        assert entries[1]["name"] == "Project Start"

    def test_journal_entry_structure(self, db):
        """Test journal entry structure."""
        entries = db.get_journal_entries()

        entry = entries[0]
        assert "id" in entry
        assert "name" in entry
        assert "content" in entry
        assert "date" in entry
        assert "owner" in entry


# =============================================================================
# TESTS: GUID Operations
# =============================================================================

class TestGuidOperations:
    """Test GUID generation for REFI-QDA export."""

    def test_generate_deterministic_guid(self, db):
        """Test that GUIDs are deterministic."""
        guid1 = db.generate_deterministic_guid("code", 1)
        guid2 = db.generate_deterministic_guid("code", 1)

        assert guid1 == guid2

    def test_different_entities_different_guids(self, db):
        """Test that different entities get different GUIDs."""
        code_guid = db.generate_deterministic_guid("code", 1)
        file_guid = db.generate_deterministic_guid("file", 1)

        assert code_guid != file_guid

    def test_guid_is_valid_uuid(self, db):
        """Test that generated GUIDs are valid UUIDs."""
        import uuid
        guid = db.generate_deterministic_guid("code", 1)

        # Should not raise
        parsed = uuid.UUID(guid)
        assert parsed.version == 5  # UUID v5

    def test_get_code_guids(self, db):
        """Test getting GUIDs for all codes."""
        guids = db.get_code_guids()

        assert len(guids) == 5
        assert 1 in guids
        assert all(isinstance(g, str) for g in guids.values())

    def test_get_file_guids(self, db):
        """Test getting GUIDs for all files."""
        guids = db.get_file_guids()

        assert len(guids) == 5
        assert 1 in guids

    def test_get_case_guids(self, db):
        """Test getting GUIDs for all cases."""
        guids = db.get_case_guids()

        assert len(guids) == 3
        assert 1 in guids


# =============================================================================
# TESTS: Edge Cases and Error Handling
# =============================================================================

class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_search_results(self, db):
        """Test handling of searches with no results."""
        results = db.search_coded_text("xyznonexistent123456789")
        assert results == []

    def test_special_characters_in_search(self, db):
        """Test handling of special characters in search."""
        # These should not cause SQL errors
        results = db.search_coded_text("test%pattern")
        assert isinstance(results, list)

        results = db.search_coded_text("test_pattern")
        assert isinstance(results, list)

        results = db.search_coded_text("test'quote")
        assert isinstance(results, list)

    def test_limit_zero_raises_error(self, db):
        """Test that limit of 0 raises error."""
        with pytest.raises(ValueError, match="must be positive"):
            db.get_coded_text_segments(1, limit=0)

    def test_large_limit_capped(self, db):
        """Test that very large limits are capped."""
        # This should not crash or return all data
        segments = db.get_coded_text_segments(1, limit=100000)
        assert len(segments) <= MAX_LIMIT

    def test_unicode_content(self, qualcoder_db_path):
        """Test handling of unicode content."""
        # Add unicode content to database
        db_file = Path(qualcoder_db_path) / "data.qda"
        conn = sqlite3.connect(str(db_file))
        conn.execute("""
            INSERT INTO source (id, name, fulltext, memo, owner, date, mediapath)
            VALUES (100, 'Unicode_Test.txt', 'Unicode: 日本語 中文 한국어 émojis: 🎉🔥',
                    'Test memo', 'TestCoder', '2024-01-20', NULL)
        """)
        conn.commit()
        conn.close()

        db = QualcoderDatabase(qualcoder_db_path)
        content = db.get_file_content(100)

        assert "日本語" in content["content"]
        assert "🎉" in content["content"]
        db.close()

    def test_null_fields_handled(self, db):
        """Test that NULL fields are handled correctly."""
        codes = db.list_codes()

        # All codes should have memo as string (empty if NULL)
        for code in codes:
            assert isinstance(code["memo"], str)

        # Uncategorized code has NULL category
        uncategorized = next(c for c in codes if c["name"] == "Uncategorized Code")
        assert uncategorized["category"] is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
