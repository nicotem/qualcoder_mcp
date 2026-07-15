"""
Comprehensive tests for QualcoderDatabase write operations.

These tests verify the database modification operations work correctly,
including proper validation, transaction handling, and error recovery.
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
    backup_project,
    copy_project_to_workspace,
    DEFAULT_WORKSPACE,
)


# =============================================================================
# FIXTURE: Create a test database for write operations
# =============================================================================

@pytest.fixture
def write_test_db_path(tmp_path):
    """
    Create a QualCoder database specifically for testing write operations.
    """
    # Create .qda folder structure
    project_folder = tmp_path / "write_test.qda"
    project_folder.mkdir()
    db_file = project_folder / "data.qda"

    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()

    # Create required tables

    cursor.execute("""
        CREATE TABLE project (
            databaseversion TEXT,
            date TEXT,
            memo TEXT,
            about TEXT,
            bookmarkfile INTEGER,
            bookmarkpos INTEGER,
            codername TEXT,
            recently_used_codes TEXT
        )
    """)
    cursor.execute("INSERT INTO project (databaseversion, date, memo, about, codername) VALUES ('v14', '2024-01-15', 'Test', 'About', 'TestCoder')")

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
    cursor.execute("INSERT INTO code_cat VALUES (1, 'Test Category', '', 'TestCoder', '2024-01-15', NULL)")

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
    cursor.execute("INSERT INTO code_name VALUES (1, 'Existing Code', 'Test memo', 1, 'TestCoder', '2024-01-15', '#FF0000')")

    cursor.execute("""
        CREATE TABLE source (
            id INTEGER PRIMARY KEY,
            name TEXT,
            fulltext TEXT,
            mediapath TEXT,
            memo TEXT,
            owner TEXT,
            date TEXT,
            av_text_id INTEGER,
            risid INTEGER,
            UNIQUE(name)
        )
    """)
    # Insert a file with known content for position testing
    test_content = "This is a test file with some content. The content is used for testing coding positions."
    cursor.execute(f"INSERT INTO source (id, name, fulltext, mediapath, memo, owner, date) VALUES (1, 'test_file.txt', '{test_content}', NULL, '', 'TestCoder', '2024-01-15')")

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
            avid INTEGER,
            important INTEGER,
            UNIQUE(cid, fid, pos0, pos1, owner),
            FOREIGN KEY (cid) REFERENCES code_name(cid),
            FOREIGN KEY (fid) REFERENCES source(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE cases (
            caseid INTEGER PRIMARY KEY,
            name TEXT,
            memo TEXT,
            owner TEXT,
            date TEXT,
            CONSTRAINT ucm UNIQUE(name)
        )
    """)

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

    cursor.execute("""
        CREATE TABLE journal (
            jid INTEGER PRIMARY KEY,
            name TEXT,
            jentry TEXT,
            date TEXT,
            owner TEXT
        )
    """)

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

    cursor.execute("""
        CREATE TABLE code_image (
            imid INTEGER PRIMARY KEY,
            id INTEGER,
            x1 INTEGER, y1 INTEGER, width INTEGER, height INTEGER,
            cid INTEGER, memo TEXT, date TEXT, owner TEXT,
            important INTEGER, pdf_page INTEGER
        )
    """)

    cursor.execute("""
        CREATE TABLE code_av (
            avid INTEGER PRIMARY KEY,
            cid INTEGER,
            id INTEGER,
            pos0 INTEGER, pos1 INTEGER,
            memo TEXT, owner TEXT, date TEXT, important INTEGER DEFAULT 0
        )
    """)

    conn.commit()
    conn.close()

    yield str(project_folder)


@pytest.fixture
def write_db(write_test_db_path):
    """Create a QualcoderDatabase instance for write testing."""
    database = QualcoderDatabase(write_test_db_path, read_only=False)
    yield database
    database.close()


# =============================================================================
# TESTS: add_coding() - Adding coded segments
# =============================================================================

class TestAddCoding:
    """Test adding new coded segments to the database."""

    def test_add_coding_success(self, write_db):
        """Test successfully adding a coding."""
        ctid = write_db.add_coding(
            file_id=1,
            code_id=1,
            start_pos=0,
            end_pos=19,
            selected_text="This is a test file",
            owner="AI Coder",
            memo="Test coding"
        )

        assert ctid is not None
        assert ctid > 0

        # Verify the coding was added
        segments = write_db.get_coded_text_segments(1)
        assert len(segments) == 1
        assert segments[0]["text"] == "This is a test file"
        assert segments[0]["memo"] == "Test coding"

    def test_add_coding_without_memo(self, write_db):
        """Test adding a coding without memo."""
        ctid = write_db.add_coding(
            file_id=1,
            code_id=1,
            start_pos=0,
            end_pos=9,
            selected_text="This is a",
            owner="AI Coder"
        )

        assert ctid is not None

        segments = write_db.get_coded_text_segments(1)
        assert segments[0]["memo"] == ""

    def test_add_coding_with_important_flag(self, write_db):
        """Test adding a coding marked as important."""
        ctid = write_db.add_coding(
            file_id=1,
            code_id=1,
            start_pos=0,
            end_pos=9,
            selected_text="This is a",
            owner="AI Coder",
            important=1
        )

        segments = write_db.get_coded_text_segments(1)
        assert segments[0]["important"] is True

    def test_add_coding_invalid_file_id(self, write_db):
        """Test adding coding with non-existent file ID."""
        with pytest.raises(ValueError, match="File ID 999 does not exist"):
            write_db.add_coding(
                file_id=999,
                code_id=1,
                start_pos=0,
                end_pos=10,
                selected_text="test",
                owner="AI Coder"
            )

    def test_add_coding_invalid_code_id(self, write_db):
        """Test adding coding with non-existent code ID."""
        with pytest.raises(ValueError, match="Code ID 999 does not exist"):
            write_db.add_coding(
                file_id=1,
                code_id=999,
                start_pos=0,
                end_pos=10,
                selected_text="test",
                owner="AI Coder"
            )

    def test_add_coding_negative_file_id(self, write_db):
        """Test adding coding with negative file ID."""
        with pytest.raises(ValueError, match="must be non-negative"):
            write_db.add_coding(
                file_id=-1,
                code_id=1,
                start_pos=0,
                end_pos=10,
                selected_text="test",
                owner="AI Coder"
            )

    def test_add_coding_invalid_positions(self, write_db):
        """Test adding coding with invalid position values."""
        # start_pos negative
        with pytest.raises(ValueError, match="start_pos must be non-negative"):
            write_db.add_coding(
                file_id=1,
                code_id=1,
                start_pos=-1,
                end_pos=10,
                selected_text="test",
                owner="AI Coder"
            )

        # end_pos <= start_pos
        with pytest.raises(ValueError, match="end_pos must be greater than start_pos"):
            write_db.add_coding(
                file_id=1,
                code_id=1,
                start_pos=10,
                end_pos=5,
                selected_text="test",
                owner="AI Coder"
            )

        # end_pos == start_pos
        with pytest.raises(ValueError, match="end_pos must be greater than start_pos"):
            write_db.add_coding(
                file_id=1,
                code_id=1,
                start_pos=10,
                end_pos=10,
                selected_text="test",
                owner="AI Coder"
            )

    def test_add_coding_position_exceeds_file_length(self, write_db):
        """Test adding coding with position beyond file length."""
        # File content is ~90 chars
        with pytest.raises(ValueError, match="exceeds file length"):
            write_db.add_coding(
                file_id=1,
                code_id=1,
                start_pos=0,
                end_pos=10000,
                selected_text="test",
                owner="AI Coder"
            )

    def test_add_coding_empty_owner(self, write_db):
        """Test adding coding with empty owner."""
        with pytest.raises(ValueError, match="owner must be a non-empty string"):
            write_db.add_coding(
                file_id=1,
                code_id=1,
                start_pos=0,
                end_pos=10,
                selected_text="test",
                owner=""
            )

    def test_add_coding_duplicate_rejected(self, write_db):
        """Test that duplicate codings are rejected."""
        # Add first coding
        write_db.add_coding(
            file_id=1,
            code_id=1,
            start_pos=0,
            end_pos=9,
            selected_text="This is a",
            owner="AI Coder"
        )

        # Try to add identical coding
        with pytest.raises(ValueError, match="already exists"):
            write_db.add_coding(
                file_id=1,
                code_id=1,
                start_pos=0,
                end_pos=9,
                selected_text="This is a",
                owner="AI Coder"
            )

    def test_add_multiple_codings_same_file(self, write_db):
        """Test adding multiple non-overlapping codings to same file."""
        ctid1 = write_db.add_coding(
            file_id=1,
            code_id=1,
            start_pos=0,
            end_pos=9,
            selected_text="This is a",
            owner="AI Coder"
        )

        ctid2 = write_db.add_coding(
            file_id=1,
            code_id=1,
            start_pos=15,
            end_pos=24,
            selected_text="file with",
            owner="AI Coder"
        )

        assert ctid1 != ctid2

        segments = write_db.get_coded_text_segments(1)
        assert len(segments) == 2

    def test_add_coding_different_owners(self, write_db):
        """Test that same position can be coded by different owners."""
        ctid1 = write_db.add_coding(
            file_id=1,
            code_id=1,
            start_pos=0,
            end_pos=9,
            selected_text="This is a",
            owner="Coder1"
        )

        ctid2 = write_db.add_coding(
            file_id=1,
            code_id=1,
            start_pos=0,
            end_pos=9,
            selected_text="This is a",
            owner="Coder2"
        )

        assert ctid1 != ctid2


# =============================================================================
# TESTS: add_code() - Creating new codes
# =============================================================================

class TestAddCode:
    """Test creating new codes in the database."""

    def test_add_code_success(self, write_db):
        """Test successfully adding a new code."""
        cid = write_db.add_code(
            name="New Test Code",
            owner="AI Coder",
            memo="Code description",
            color="#00FF00"
        )

        assert cid is not None
        assert cid > 1  # 1 is the existing code

        # Verify the code was added
        details = write_db.get_code_details(cid)
        assert details is not None
        assert details["name"] == "New Test Code"
        assert details["memo"] == "Code description"

    def test_add_code_with_category(self, write_db):
        """Test adding a code to a category."""
        cid = write_db.add_code(
            name="Categorized Code",
            owner="AI Coder",
            category_id=1
        )

        details = write_db.get_code_details(cid)
        assert details["category"] == "Test Category"

    def test_add_code_without_category(self, write_db):
        """Test adding a code without category."""
        cid = write_db.add_code(
            name="Uncategorized Code",
            owner="AI Coder"
        )

        details = write_db.get_code_details(cid)
        assert details["category"] is None

    def test_add_code_duplicate_name(self, write_db):
        """Test that duplicate code names are rejected."""
        with pytest.raises(ValueError, match="already exists"):
            write_db.add_code(
                name="Existing Code",  # Same as fixture
                owner="AI Coder"
            )

    def test_add_code_empty_name(self, write_db):
        """Test that empty code names are rejected."""
        with pytest.raises(ValueError, match="name must be a non-empty string"):
            write_db.add_code(
                name="",
                owner="AI Coder"
            )

    def test_add_code_empty_owner(self, write_db):
        """Test that empty owner is rejected."""
        with pytest.raises(ValueError, match="owner must be a non-empty string"):
            write_db.add_code(
                name="Test Code",
                owner=""
            )

    def test_add_code_invalid_category(self, write_db):
        """Test adding code with non-existent category."""
        with pytest.raises(ValueError, match="Category ID 999 does not exist"):
            write_db.add_code(
                name="Test Code",
                owner="AI Coder",
                category_id=999
            )

    def test_add_code_invalid_color_format(self, write_db):
        """Test that invalid color formats are rejected."""
        with pytest.raises(ValueError, match="color must be hex format"):
            write_db.add_code(
                name="Test Code",
                owner="AI Coder",
                color="red"  # Not hex format
            )

        with pytest.raises(ValueError, match="color must be hex format"):
            write_db.add_code(
                name="Test Code",
                owner="AI Coder",
                color="#FFF"  # Too short
            )

    def test_add_code_default_color(self, write_db):
        """Default color is a random pick from QualCoder's own palette."""
        from qualcoder_mcp.database import QUALCODER_COLORS

        cid = write_db.add_code(
            name="Default Color Code",
            owner="AI Coder"
        )

        codes = write_db.list_codes()
        new_code = next(c for c in codes if c["id"] == cid)
        assert new_code["color"] in QUALCODER_COLORS


# =============================================================================
# TESTS: add_memo_to_coding() - Updating memos
# =============================================================================

class TestAddMemoToCoding:
    """Test updating memos on existing codings."""

    def test_add_memo_success(self, write_db):
        """Test successfully adding a memo to coding."""
        # First create a coding
        ctid = write_db.add_coding(
            file_id=1,
            code_id=1,
            start_pos=0,
            end_pos=9,
            selected_text="This is a",
            owner="AI Coder"
        )

        # Add memo
        write_db.add_memo_to_coding(ctid, "Updated memo", "AI Coder")

        # Verify memo was added
        segments = write_db.get_coded_text_segments(1)
        assert segments[0]["memo"] == "Updated memo"

    def test_add_memo_replaces_existing(self, write_db):
        """Test that memo replaces any existing memo."""
        ctid = write_db.add_coding(
            file_id=1,
            code_id=1,
            start_pos=0,
            end_pos=9,
            selected_text="This is a",
            owner="AI Coder",
            memo="Original memo"
        )

        write_db.add_memo_to_coding(ctid, "New memo", "AI Coder")

        segments = write_db.get_coded_text_segments(1)
        assert segments[0]["memo"] == "New memo"

    def test_add_memo_nonexistent_coding(self, write_db):
        """Test adding memo to non-existent coding."""
        with pytest.raises(ValueError, match="Coding ID 999 does not exist"):
            write_db.add_memo_to_coding(999, "Test memo", "AI Coder")

    def test_add_memo_invalid_id(self, write_db):
        """Test adding memo with invalid ID."""
        with pytest.raises(ValueError, match="must be non-negative"):
            write_db.add_memo_to_coding(-1, "Test memo", "AI Coder")


# =============================================================================
# TESTS: backup_project() and copy_project_to_workspace()
# =============================================================================

class TestBackupOperations:
    """Test backup and copy operations."""

    def test_backup_project(self, write_test_db_path):
        """Test creating a project backup."""
        backup_path = backup_project(write_test_db_path)

        assert backup_path.exists()
        assert backup_path.is_dir()
        assert backup_path.suffix == ".qda"
        assert "_backup_" in backup_path.name

        # Verify backup contains data.qda
        assert (backup_path / "data.qda").exists()

        # Cleanup
        shutil.rmtree(backup_path)

    def test_backup_project_from_data_file(self, write_test_db_path):
        """Test backup when given path to data.qda file."""
        data_file = Path(write_test_db_path) / "data.qda"
        backup_path = backup_project(str(data_file))

        assert backup_path.exists()
        shutil.rmtree(backup_path)

    def test_backup_project_nonexistent(self, tmp_path):
        """Test backup of non-existent project."""
        with pytest.raises(FileNotFoundError):
            backup_project(str(tmp_path / "nonexistent.qda"))

    def test_copy_project_to_workspace(self, write_test_db_path, tmp_path):
        """Test copying project to workspace."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        copied_path = copy_project_to_workspace(
            write_test_db_path,
            workspace=workspace
        )

        assert copied_path.exists()
        assert copied_path.is_dir()
        assert (copied_path / "data.qda").exists()
        assert str(copied_path).startswith(str(workspace))

    def test_copy_project_with_new_name(self, write_test_db_path, tmp_path):
        """Test copying project with a new name."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        copied_path = copy_project_to_workspace(
            write_test_db_path,
            workspace=workspace,
            new_name="renamed_project"
        )

        assert copied_path.name == "renamed_project.qda"

    def test_copy_project_existing_destination(self, write_test_db_path, tmp_path):
        """Test that copy adds timestamp if destination exists."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        # First copy
        copied1 = copy_project_to_workspace(
            write_test_db_path,
            workspace=workspace
        )

        # Second copy (should get timestamp)
        copied2 = copy_project_to_workspace(
            write_test_db_path,
            workspace=workspace
        )

        assert copied1 != copied2
        assert copied2.exists()


# =============================================================================
# TESTS: Transaction Handling
# =============================================================================

class TestTransactionHandling:
    """Test transaction handling and rollback behavior."""

    def test_failed_insert_does_not_persist(self, write_db):
        """Test that failed inserts are rolled back."""
        initial_count = len(write_db.list_codes())

        # Try to add code with duplicate name - should fail
        try:
            write_db.add_code(name="Existing Code", owner="AI")
        except ValueError:
            pass

        # Count should be unchanged
        final_count = len(write_db.list_codes())
        assert final_count == initial_count

    def test_coding_persists_after_commit(self, write_test_db_path):
        """Test that successful writes persist after closing connection."""
        # Add coding in first connection
        db1 = QualcoderDatabase(write_test_db_path, read_only=False)
        ctid = db1.add_coding(
            file_id=1,
            code_id=1,
            start_pos=0,
            end_pos=9,
            selected_text="This is a",
            owner="AI Coder"
        )
        db1.close()

        # Verify in new connection (read-only is fine for verification)
        db2 = QualcoderDatabase(write_test_db_path)
        segments = db2.get_coded_text_segments(1)
        db2.close()

        assert len(segments) == 1
        assert segments[0]["text"] == "This is a"


# =============================================================================
# TESTS: Backup Before Write
# =============================================================================

class TestBackupBeforeWrite:
    """Test the backup_before_write functionality."""

    def test_backup_before_write(self, write_db, write_test_db_path):
        """Test that backup_before_write creates a backup."""
        backup_path = write_db.backup_before_write()

        assert backup_path.exists()
        assert "_backup_" in backup_path.name

        # Cleanup
        shutil.rmtree(backup_path)


# =============================================================================
# TESTS: Data Integrity
# =============================================================================

class TestDataIntegrity:
    """Test data integrity during write operations."""

    def test_coding_references_valid(self, write_db):
        """Test that codings maintain referential integrity."""
        # Add a coding
        ctid = write_db.add_coding(
            file_id=1,
            code_id=1,
            start_pos=0,
            end_pos=9,
            selected_text="This is a",
            owner="AI Coder"
        )

        # Verify foreign key relationships
        cursor = write_db.conn.execute("""
            SELECT ct.ctid, c.name as code_name, s.name as file_name
            FROM code_text ct
            JOIN code_name c ON ct.cid = c.cid
            JOIN source s ON ct.fid = s.id
            WHERE ct.ctid = ?
        """, (ctid,))

        row = cursor.fetchone()
        assert row is not None
        assert row["code_name"] == "Existing Code"
        assert row["file_name"] == "test_file.txt"

    def test_date_field_format(self, write_db):
        """Test that date fields are in correct format."""
        ctid = write_db.add_coding(
            file_id=1,
            code_id=1,
            start_pos=0,
            end_pos=9,
            selected_text="This is a",
            owner="AI Coder"
        )

        cursor = write_db.conn.execute(
            "SELECT date FROM code_text WHERE ctid = ?", (ctid,)
        )
        date_str = cursor.fetchone()["date"]

        # Verify date format is parseable
        datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")


class TestImportTextFile:
    """Test importing text content as new source files."""

    def test_import_success(self, write_db):
        result = write_db.import_text_file(
            name="new_interview.txt",
            content="This is the transcript content.",
            owner="MCP Import"
        )
        assert result["id"] > 0
        assert result["name"] == "new_interview.txt"
        assert result["content_length"] == len("This is the transcript content.")
        assert result["owner"] == "MCP Import"
        # Verify via read
        file_data = write_db.get_file_content(result["id"])
        assert file_data["content"] == "This is the transcript content."
        assert file_data["name"] == "new_interview.txt"

    def test_import_with_memo(self, write_db):
        result = write_db.import_text_file(
            name="noted_file.txt",
            content="Content here.",
            owner="MCP Import",
            memo="Transcribed from audio recording"
        )
        file_data = write_db.get_file_content(result["id"])
        assert file_data["memo"] == "Transcribed from audio recording"

    def test_import_custom_owner(self, write_db):
        result = write_db.import_text_file(
            name="owned_file.txt",
            content="Content.",
            owner="Researcher A"
        )
        assert result["owner"] == "Researcher A"

    def test_import_mediapath_is_null(self, write_db):
        result = write_db.import_text_file(
            name="inline_file.txt",
            content="Text content.",
            owner="MCP Import"
        )
        file_data = write_db.get_file_content(result["id"])
        assert file_data["media_path"] is None

    def test_import_appears_in_list_files(self, write_db):
        initial_count = len(write_db.list_files())
        write_db.import_text_file(
            name="listed.txt",
            content="Content.",
            owner="MCP Import"
        )
        assert len(write_db.list_files()) == initial_count + 1

    def test_import_date_format(self, write_db):
        result = write_db.import_text_file(
            name="dated.txt",
            content="Content.",
            owner="MCP Import"
        )
        datetime.strptime(result["date"], "%Y-%m-%d %H:%M:%S")

    def test_import_duplicate_name_rejected(self, write_db):
        # 'test_file.txt' already exists in the fixture
        with pytest.raises(ValueError, match="already exists"):
            write_db.import_text_file(
                name="test_file.txt",
                content="Different content.",
                owner="MCP Import"
            )

    def test_import_empty_filename_rejected(self, write_db):
        with pytest.raises(ValueError, match="non-empty"):
            write_db.import_text_file(
                name="",
                content="Content.",
                owner="MCP Import"
            )

    def test_import_filename_no_extension_rejected(self, write_db):
        with pytest.raises(ValueError, match="extension"):
            write_db.import_text_file(
                name="no_extension",
                content="Content.",
                owner="MCP Import"
            )

    def test_import_filename_with_slash_rejected(self, write_db):
        with pytest.raises(ValueError, match="path separators"):
            write_db.import_text_file(
                name="sub/file.txt",
                content="Content.",
                owner="MCP Import"
            )

    def test_import_filename_with_backslash_rejected(self, write_db):
        with pytest.raises(ValueError, match="path separators"):
            write_db.import_text_file(
                name="sub\\file.txt",
                content="Content.",
                owner="MCP Import"
            )

    def test_import_filename_with_dotdot_rejected(self, write_db):
        with pytest.raises(ValueError, match="path separators"):
            write_db.import_text_file(
                name="../../evil.txt",
                content="Content.",
                owner="MCP Import"
            )

    def test_import_empty_content_rejected(self, write_db):
        with pytest.raises(ValueError, match="content must not be empty"):
            write_db.import_text_file(
                name="empty.txt",
                content="",
                owner="MCP Import"
            )

    def test_import_whitespace_only_content_rejected(self, write_db):
        with pytest.raises(ValueError, match="content must not be empty"):
            write_db.import_text_file(
                name="whitespace.txt",
                content="   \n\t  ",
                owner="MCP Import"
            )

    def test_import_empty_owner_rejected(self, write_db):
        with pytest.raises(ValueError, match="owner must be a non-empty"):
            write_db.import_text_file(
                name="file.txt",
                content="Content.",
                owner=""
            )

    def test_import_creates_attribute_placeholders(self, write_db):
        # Add a file-type attribute type
        write_db.conn.execute(
            "INSERT INTO attribute_type (name, date, owner, memo, caseOrFile, valuetype) "
            "VALUES ('Source', '2024-01-15', 'TestCoder', '', 'file', 'character')"
        )
        write_db.conn.commit()
        result = write_db.import_text_file(
            name="attributed.txt",
            content="Content.",
            owner="MCP Import"
        )
        assert result["attributes_created"] == 1

    def test_import_creates_both_type_attribute_placeholders(self, write_db):
        # Add a 'both'-type attribute (applies to files AND cases)
        write_db.conn.execute(
            "INSERT INTO attribute_type (name, date, owner, memo, caseOrFile, valuetype) "
            "VALUES ('SharedAttr', '2024-01-15', 'TestCoder', '', 'both', 'character')"
        )
        write_db.conn.commit()
        result = write_db.import_text_file(
            name="both_attr.txt",
            content="Content.",
            owner="MCP Import"
        )
        assert result["attributes_created"] == 1

    def test_import_no_attribute_placeholders_when_none_exist(self, write_db):
        result = write_db.import_text_file(
            name="no_attrs.txt",
            content="Content.",
            owner="MCP Import"
        )
        assert result["attributes_created"] == 0

    def test_import_persists_after_close(self, write_test_db_path):
        db1 = QualcoderDatabase(write_test_db_path, read_only=False)
        result = db1.import_text_file(
            name="persistent.txt",
            content="This should persist.",
            owner="MCP Import"
        )
        db1.close()
        db2 = QualcoderDatabase(write_test_db_path)
        file_data = db2.get_file_content(result["id"])
        db2.close()
        assert file_data is not None
        assert file_data["content"] == "This should persist."

    def test_import_read_only_rejected(self, write_test_db_path):
        ro_db = QualcoderDatabase(write_test_db_path, read_only=True)
        with pytest.raises(RuntimeError, match="read-only"):
            ro_db.import_text_file(
                name="file.txt",
                content="Content.",
                owner="MCP Import"
            )
        ro_db.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
