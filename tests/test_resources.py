"""
Tests for server.py @mcp.resource() functions.

These test the resource functions directly, verifying they return
correct JSON for various states (loaded project, no project, empty data).
"""

import pytest
import json
import sqlite3
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server
from qualcoder_mcp.database import QualcoderDatabase
from qualcoder_mcp.sessions import SessionManager


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def qualcoder_db_path(tmp_path):
    """Create a QualCoder database for testing."""
    project_folder = tmp_path / "test_project.qda"
    project_folder.mkdir()
    db_file = project_folder / "data.qda"

    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()

    cursor.execute("CREATE TABLE project (databaseversion TEXT, date TEXT, memo TEXT, about TEXT, codername TEXT)")
    cursor.execute("INSERT INTO project VALUES ('v12', '2024-01-15', 'Test', 'About', 'TestCoder')")

    cursor.execute("CREATE TABLE code_cat (catid INTEGER PRIMARY KEY, name TEXT UNIQUE, memo TEXT, owner TEXT, date TEXT, supercatid INTEGER)")
    cursor.execute("INSERT INTO code_cat VALUES (1, 'Cat A', 'memo', 'TestCoder', '2024-01-15', NULL)")

    cursor.execute("CREATE TABLE code_name (cid INTEGER PRIMARY KEY, name TEXT UNIQUE, memo TEXT, catid INTEGER, owner TEXT, date TEXT, color TEXT)")
    cursor.execute("INSERT INTO code_name VALUES (1, 'Code1', 'memo1', 1, 'TestCoder', '2024-01-15', '#FF0000')")

    cursor.execute("CREATE TABLE source (id INTEGER PRIMARY KEY, name TEXT, fulltext TEXT, memo TEXT, owner TEXT, date TEXT, mediapath TEXT)")
    cursor.execute("INSERT INTO source VALUES (1, 'file.txt', 'content here', '', 'TestCoder', '2024-01-15', NULL)")

    cursor.execute("CREATE TABLE code_text (ctid INTEGER PRIMARY KEY, cid INTEGER, fid INTEGER, seltext TEXT, pos0 INTEGER, pos1 INTEGER, owner TEXT, date TEXT, memo TEXT, important INTEGER DEFAULT 0)")
    cursor.execute("INSERT INTO code_text VALUES (1, 1, 1, 'content', 0, 7, 'TestCoder', '2024-01-15', '', 0)")

    cursor.execute("CREATE TABLE cases (caseid INTEGER PRIMARY KEY, name TEXT, memo TEXT, owner TEXT, date TEXT)")
    cursor.execute("INSERT INTO cases VALUES (1, 'CaseA', 'memo', 'TestCoder', '2024-01-15')")

    cursor.execute("CREATE TABLE case_text (id INTEGER PRIMARY KEY, caseid INTEGER, fid INTEGER, pos0 INTEGER, pos1 INTEGER, memo TEXT, owner TEXT, date TEXT)")
    cursor.execute("INSERT INTO case_text VALUES (1, 1, 1, 0, 20, '', 'TestCoder', '2024-01-15')")

    cursor.execute("CREATE TABLE annotation (anid INTEGER PRIMARY KEY, fid INTEGER, pos0 INTEGER, pos1 INTEGER, memo TEXT, owner TEXT, date TEXT)")

    cursor.execute("CREATE TABLE journal (jid INTEGER PRIMARY KEY, name TEXT, jentry TEXT, date TEXT, owner TEXT)")
    cursor.execute("INSERT INTO journal VALUES (1, 'Entry', 'Content', '2024-01-15', 'TestCoder')")

    cursor.execute("CREATE TABLE attribute_type (name TEXT PRIMARY KEY, date TEXT, owner TEXT, memo TEXT, caseOrFile TEXT, valuetype TEXT)")
    cursor.execute("CREATE TABLE attribute (attrid INTEGER PRIMARY KEY, name TEXT, attr_type TEXT, value TEXT, id INTEGER, date TEXT, owner TEXT)")

    cursor.execute("CREATE TABLE code_image (imid INTEGER PRIMARY KEY, cid INTEGER, id INTEGER, x1 REAL, y1 REAL, width REAL, height REAL, memo TEXT, owner TEXT, date TEXT, important INTEGER DEFAULT 0)")
    cursor.execute("CREATE TABLE code_av (avid INTEGER PRIMARY KEY, cid INTEGER, id INTEGER, pos0 INTEGER, pos1 INTEGER, memo TEXT, owner TEXT, date TEXT, important INTEGER DEFAULT 0)")

    conn.commit()
    conn.close()

    yield str(project_folder)


@pytest.fixture
def empty_db_path(tmp_path):
    """Create an empty QualCoder database (schema only, no data rows except project)."""
    project_folder = tmp_path / "empty_project.qda"
    project_folder.mkdir()
    db_file = project_folder / "data.qda"

    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()

    cursor.execute("CREATE TABLE project (databaseversion TEXT, date TEXT, memo TEXT, about TEXT, codername TEXT)")
    cursor.execute("INSERT INTO project VALUES ('v12', '2024-01-15', '', '', 'TestCoder')")
    cursor.execute("CREATE TABLE code_cat (catid INTEGER PRIMARY KEY, name TEXT UNIQUE, memo TEXT, owner TEXT, date TEXT, supercatid INTEGER)")
    cursor.execute("CREATE TABLE code_name (cid INTEGER PRIMARY KEY, name TEXT UNIQUE, memo TEXT, catid INTEGER, owner TEXT, date TEXT, color TEXT)")
    cursor.execute("CREATE TABLE source (id INTEGER PRIMARY KEY, name TEXT, fulltext TEXT, memo TEXT, owner TEXT, date TEXT, mediapath TEXT)")
    cursor.execute("CREATE TABLE code_text (ctid INTEGER PRIMARY KEY, cid INTEGER, fid INTEGER, seltext TEXT, pos0 INTEGER, pos1 INTEGER, owner TEXT, date TEXT, memo TEXT, important INTEGER DEFAULT 0)")
    cursor.execute("CREATE TABLE cases (caseid INTEGER PRIMARY KEY, name TEXT, memo TEXT, owner TEXT, date TEXT)")
    cursor.execute("CREATE TABLE case_text (id INTEGER PRIMARY KEY, caseid INTEGER, fid INTEGER, pos0 INTEGER, pos1 INTEGER, memo TEXT, owner TEXT, date TEXT)")
    cursor.execute("CREATE TABLE annotation (anid INTEGER PRIMARY KEY, fid INTEGER, pos0 INTEGER, pos1 INTEGER, memo TEXT, owner TEXT, date TEXT)")
    cursor.execute("CREATE TABLE journal (jid INTEGER PRIMARY KEY, name TEXT, jentry TEXT, date TEXT, owner TEXT)")
    cursor.execute("CREATE TABLE attribute_type (name TEXT PRIMARY KEY, date TEXT, owner TEXT, memo TEXT, caseOrFile TEXT, valuetype TEXT)")
    cursor.execute("CREATE TABLE attribute (attrid INTEGER PRIMARY KEY, name TEXT, attr_type TEXT, value TEXT, id INTEGER, date TEXT, owner TEXT)")
    cursor.execute("CREATE TABLE code_image (imid INTEGER PRIMARY KEY, cid INTEGER, id INTEGER, x1 REAL, y1 REAL, width REAL, height REAL, memo TEXT, owner TEXT, date TEXT, important INTEGER DEFAULT 0)")
    cursor.execute("CREATE TABLE code_av (avid INTEGER PRIMARY KEY, cid INTEGER, id INTEGER, pos0 INTEGER, pos1 INTEGER, memo TEXT, owner TEXT, date TEXT, important INTEGER DEFAULT 0)")

    conn.commit()
    conn.close()

    yield str(project_folder)


@pytest.fixture
def setup_server(qualcoder_db_path, tmp_path):
    """Set up server with test database."""
    original_db = server.db
    original_path = server.current_project_path
    original_sm = server.session_manager

    server.db = QualcoderDatabase(qualcoder_db_path)
    server.current_project_path = qualcoder_db_path
    server.session_manager = SessionManager(str(tmp_path / "sessions"))

    yield server

    if server.db is not None:
        try:
            server.db.close()
        except Exception:
            pass
    server.db = original_db
    server.current_project_path = original_path
    server.session_manager = original_sm


@pytest.fixture
def setup_empty_server(empty_db_path, tmp_path):
    """Set up server with empty database."""
    original_db = server.db
    original_path = server.current_project_path
    original_sm = server.session_manager

    server.db = QualcoderDatabase(empty_db_path)
    server.current_project_path = empty_db_path
    server.session_manager = SessionManager(str(tmp_path / "sessions"))

    yield server

    if server.db is not None:
        try:
            server.db.close()
        except Exception:
            pass
    server.db = original_db
    server.current_project_path = original_path
    server.session_manager = original_sm


# =============================================================================
# TESTS: Resources with data
# =============================================================================

class TestProjectInfoResource:
    def test_returns_valid_json(self, setup_server):
        result = server.get_project_info()
        data = json.loads(result)
        assert data["database_version"] == "v12"
        assert data["coder_name"] == "TestCoder"


class TestCodesListResource:
    def test_returns_codes(self, setup_server):
        result = server.list_all_codes()
        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["name"] == "Code1"

    def test_empty_db(self, setup_empty_server):
        result = server.list_all_codes()
        data = json.loads(result)
        assert data == []


class TestCategoriesListResource:
    def test_returns_categories(self, setup_server):
        result = server.list_all_categories()
        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["name"] == "Cat A"

    def test_empty_db(self, setup_empty_server):
        result = server.list_all_categories()
        data = json.loads(result)
        assert data == []


class TestCodeInfoResource:
    def test_existing_code(self, setup_server):
        result = server.get_code_info(1)
        data = json.loads(result)
        assert data["name"] == "Code1"
        assert "statistics" in data

    def test_nonexistent_code(self, setup_server):
        result = server.get_code_info(999)
        data = json.loads(result)
        assert "error" in data


class TestFilesListResource:
    def test_returns_files(self, setup_server):
        result = server.list_all_files()
        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["name"] == "file.txt"

    def test_empty_db(self, setup_empty_server):
        result = server.list_all_files()
        data = json.loads(result)
        assert data == []


class TestFileContentResource:
    def test_existing_file(self, setup_server):
        result = server.get_file_content(1)
        data = json.loads(result)
        assert data["name"] == "file.txt"
        assert "content here" in data["content"]

    def test_nonexistent_file(self, setup_server):
        result = server.get_file_content(999)
        data = json.loads(result)
        assert "error" in data


class TestCasesListResource:
    def test_returns_cases(self, setup_server):
        result = server.list_all_cases()
        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["name"] == "CaseA"

    def test_empty_db(self, setup_empty_server):
        result = server.list_all_cases()
        data = json.loads(result)
        assert data == []


class TestCaseInfoResource:
    def test_existing_case(self, setup_server):
        result = server.get_case_info(1)
        data = json.loads(result)
        assert data["name"] == "CaseA"

    def test_nonexistent_case(self, setup_server):
        result = server.get_case_info(999)
        data = json.loads(result)
        assert "error" in data


class TestJournalResource:
    def test_returns_entries(self, setup_server):
        result = server.get_journal_entries()
        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["name"] == "Entry"

    def test_empty_db(self, setup_empty_server):
        result = server.get_journal_entries()
        data = json.loads(result)
        assert data == []


# =============================================================================
# TESTS: Resources without project loaded
# =============================================================================

class TestResourcesNoProject:
    """Test that resources fail gracefully when no project is loaded."""

    def test_project_info_no_project(self):
        original_db = server.db
        original_path = server.current_project_path
        try:
            server.db = None
            server.current_project_path = None
            # Remove env var if set
            import os
            original_env = os.environ.pop("QUALCODER_PROJECT_PATH", None)

            with pytest.raises(ValueError, match="No Qualcoder project selected"):
                server.get_project_info()
        finally:
            server.db = original_db
            server.current_project_path = original_path
            if original_env:
                os.environ["QUALCODER_PROJECT_PATH"] = original_env


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
