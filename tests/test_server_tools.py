"""
Comprehensive tests for server.py tool functions.

These tests exercise the @mcp.tool() functions directly (not through MCP protocol),
using real SQLite database fixtures.
"""

import pytest
import json
import sqlite3
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server
from qualcoder_mcp.database import QualcoderDatabase
from qualcoder_mcp.sessions import SessionManager, AICodingSession, CodingSuggestion


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def qualcoder_db_path(tmp_path):
    """Create a complete QualCoder-compatible SQLite database."""
    project_folder = tmp_path / "test_project.qda"
    project_folder.mkdir()
    db_file = project_folder / "data.qda"

    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE project (
            databaseversion TEXT, date TEXT, memo TEXT, about TEXT, codername TEXT
        )
    """)
    cursor.execute("INSERT INTO project VALUES ('v12', '2024-01-15', 'Test project', 'About', 'TestCoder')")

    cursor.execute("""
        CREATE TABLE code_cat (
            catid INTEGER PRIMARY KEY, name TEXT UNIQUE, memo TEXT,
            owner TEXT, date TEXT, supercatid INTEGER
        )
    """)
    cursor.execute("INSERT INTO code_cat VALUES (1, 'Category A', '', 'TestCoder', '2024-01-15', NULL)")

    cursor.execute("""
        CREATE TABLE code_name (
            cid INTEGER PRIMARY KEY, name TEXT UNIQUE, memo TEXT,
            catid INTEGER, owner TEXT, date TEXT, color TEXT
        )
    """)
    codes = [
        (1, "Stress", "Stress code", 1, "TestCoder", "2024-01-15", "#FF0000"),
        (2, "Coping", "Coping code", 1, "TestCoder", "2024-01-15", "#00FF00"),
    ]
    cursor.executemany("INSERT INTO code_name VALUES (?, ?, ?, ?, ?, ?, ?)", codes)

    cursor.execute("""
        CREATE TABLE source (
            id INTEGER PRIMARY KEY, name TEXT, fulltext TEXT,
            memo TEXT, owner TEXT, date TEXT, mediapath TEXT
        )
    """)
    cursor.execute("""INSERT INTO source VALUES (1, 'interview.txt',
        'This is interview text. I feel stressed about deadlines. I cope by exercising.',
        'Test memo', 'TestCoder', '2024-01-15', NULL)""")
    cursor.execute("""INSERT INTO source VALUES (2, 'notes.txt',
        'Field notes from observation session.',
        '', 'TestCoder', '2024-01-16', NULL)""")

    cursor.execute("""
        CREATE TABLE code_text (
            ctid INTEGER PRIMARY KEY, cid INTEGER, fid INTEGER,
            seltext TEXT, pos0 INTEGER, pos1 INTEGER,
            owner TEXT, date TEXT, memo TEXT, important INTEGER DEFAULT 0,
            UNIQUE(cid, fid, pos0, pos1, owner)
        )
    """)
    cursor.execute("""INSERT INTO code_text VALUES (1, 1, 1,
        'I feel stressed about deadlines', 24, 55, 'TestCoder', '2024-01-15', 'key passage', 1)""")
    cursor.execute("""INSERT INTO code_text VALUES (2, 2, 1,
        'I cope by exercising', 57, 77, 'TestCoder', '2024-01-15', '', 0)""")

    cursor.execute("""
        CREATE TABLE cases (
            caseid INTEGER PRIMARY KEY, name TEXT, memo TEXT, owner TEXT, date TEXT
        )
    """)
    cursor.execute("INSERT INTO cases VALUES (1, 'Case A', 'First case', 'TestCoder', '2024-01-15')")

    cursor.execute("""
        CREATE TABLE case_text (
            id INTEGER PRIMARY KEY, caseid INTEGER, fid INTEGER,
            pos0 INTEGER, pos1 INTEGER, memo TEXT, owner TEXT, date TEXT
        )
    """)
    cursor.execute("INSERT INTO case_text VALUES (1, 1, 1, 0, 100, '', 'TestCoder', '2024-01-15')")

    cursor.execute("""
        CREATE TABLE annotation (
            anid INTEGER PRIMARY KEY, fid INTEGER, pos0 INTEGER,
            pos1 INTEGER, memo TEXT, owner TEXT, date TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE journal (
            jid INTEGER PRIMARY KEY, name TEXT, jentry TEXT, date TEXT, owner TEXT
        )
    """)
    cursor.execute("INSERT INTO journal VALUES (1, 'Entry 1', 'Some notes', '2024-01-15', 'TestCoder')")

    cursor.execute("""
        CREATE TABLE attribute_type (
            name TEXT PRIMARY KEY, date TEXT, owner TEXT,
            memo TEXT, caseOrFile TEXT, valuetype TEXT
        )
    """)
    cursor.execute("INSERT INTO attribute_type VALUES ('Age', '2024-01-15', 'TestCoder', '', 'case', 'numeric')")

    cursor.execute("""
        CREATE TABLE attribute (
            attrid INTEGER PRIMARY KEY, name TEXT, attr_type TEXT,
            value TEXT, id INTEGER, date TEXT, owner TEXT
        )
    """)
    cursor.execute("INSERT INTO attribute VALUES (1, 'Age', 'case', '30', 1, '2024-01-15', 'TestCoder')")

    cursor.execute("""
        CREATE TABLE code_image (
            imid INTEGER PRIMARY KEY, cid INTEGER, id INTEGER,
            x1 REAL, y1 REAL, width REAL, height REAL,
            memo TEXT, owner TEXT, date TEXT, important INTEGER DEFAULT 0
        )
    """)

    cursor.execute("""
        CREATE TABLE code_av (
            avid INTEGER PRIMARY KEY, cid INTEGER, id INTEGER,
            pos0 INTEGER, pos1 INTEGER,
            memo TEXT, owner TEXT, date TEXT, important INTEGER DEFAULT 0
        )
    """)

    conn.commit()
    conn.close()

    yield str(project_folder)


@pytest.fixture
def setup_server(qualcoder_db_path, tmp_path):
    """Set up server globals for testing."""
    # Save original state
    original_db = server.db
    original_path = server.current_project_path
    original_sm = server.session_manager

    # Set up test state
    server.db = QualcoderDatabase(qualcoder_db_path)
    server.current_project_path = qualcoder_db_path
    server.session_manager = SessionManager(str(tmp_path / "sessions"))

    yield server

    # Restore
    if server.db is not None:
        try:
            server.db.close()
        except Exception:
            pass
    server.db = original_db
    server.current_project_path = original_path
    server.session_manager = original_sm


@pytest.fixture
def session_with_suggestions(setup_server, qualcoder_db_path):
    """Create a session with pre-populated suggestions."""
    session = AICodingSession(
        project_path=qualcoder_db_path,
        description="Test session",
        file_ids=[1],
        code_names=["Stress"],
        instruction="Test",
        min_confidence=0.6
    )

    s1 = CodingSuggestion(
        file_id=1, file_name="interview.txt",
        code_id=1, code_name="Stress",
        start_pos=0, end_pos=10,
        segment_text="This is in",
        reasoning="Test reasoning", confidence=0.85,
        status="pending"
    )
    s2 = CodingSuggestion(
        file_id=1, file_name="interview.txt",
        code_id=2, code_name="Coping",
        start_pos=57, end_pos=77,
        segment_text="I cope by exercising",
        reasoning="Coping behavior", confidence=0.9,
        status="pending"
    )
    session.add_suggestion(s1)
    session.add_suggestion(s2)

    setup_server.session_manager.save_session(session)
    return session


# =============================================================================
# TESTS: Project Management Tools
# =============================================================================

class TestListAvailableProjects:
    """Test list_available_projects tool."""

    def test_returns_valid_json(self, setup_server):
        result = server.list_available_projects()
        data = json.loads(result)
        assert "projects" in data

    def test_custom_search_directories(self, tmp_path):
        # Create a fake .qda project
        qda = tmp_path / "fake_project.qda"
        qda.mkdir()
        (qda / "data.qda").touch()

        result = server.list_available_projects(search_directories=[str(tmp_path)])
        data = json.loads(result)
        assert data["project_count"] >= 1

    def test_empty_search_directory(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        result = server.list_available_projects(search_directories=[str(empty_dir)])
        data = json.loads(result)
        assert data["projects"] == []

    def test_nonexistent_search_directory(self):
        result = server.list_available_projects(search_directories=["/nonexistent/path"])
        data = json.loads(result)
        assert data["projects"] == []


class TestSelectProject:
    """Test select_project tool."""

    def test_select_valid_project(self, qualcoder_db_path):
        # Reset server state first
        original_db = server.db
        original_path = server.current_project_path
        try:
            server.db = None
            server.current_project_path = None

            result = server.select_project(qualcoder_db_path)
            data = json.loads(result)
            assert data["success"] is True
            assert server.current_project_path == qualcoder_db_path
            assert server.db is not None
        finally:
            if server.db is not None:
                server.db.close()
            server.db = original_db
            server.current_project_path = original_path

    def test_select_nonexistent_project(self, setup_server):
        result = server.select_project("/nonexistent/project.qda")
        data = json.loads(result)
        assert "error" in data

    def test_select_invalid_path(self, setup_server, tmp_path):
        invalid = tmp_path / "not_qda_dir"
        invalid.mkdir()
        result = server.select_project(str(invalid))
        data = json.loads(result)
        assert "error" in data


class TestGetCurrentProject:
    """Test get_current_project tool."""

    def test_with_project_loaded(self, setup_server):
        result = server.get_current_project()
        data = json.loads(result)
        assert data["current_project"] is not None
        assert "project_name" in data

    def test_without_project(self):
        original_db = server.db
        original_path = server.current_project_path
        try:
            server.db = None
            server.current_project_path = None
            result = server.get_current_project()
            data = json.loads(result)
            assert data["current_project"] is None
        finally:
            server.db = original_db
            server.current_project_path = original_path


# =============================================================================
# TESTS: Core Data Analysis Tools
# =============================================================================

class TestSearchCodedText:
    """Test search_coded_text tool."""

    def test_basic_search(self, setup_server):
        result = server.search_coded_text("stressed")
        data = json.loads(result)
        assert "results" in data
        assert len(data["results"]) >= 1

    def test_search_with_code_filter(self, setup_server):
        result = server.search_coded_text("stressed", code_name="Stress")
        data = json.loads(result)
        assert all(r["code_name"] == "Stress" for r in data["results"])

    def test_search_no_results(self, setup_server):
        result = server.search_coded_text("xyznonexistent")
        data = json.loads(result)
        assert data["result_count"] == 0

    def test_search_with_limit(self, setup_server):
        result = server.search_coded_text("e", limit=1)
        data = json.loads(result)
        assert len(data["results"]) <= 1

    def test_search_empty_query(self, setup_server):
        result = server.search_coded_text("")
        data = json.loads(result)
        assert isinstance(data, dict)


class TestGetCodedSegments:
    """Test get_coded_segments tool."""

    def test_valid_code_id(self, setup_server):
        result = server.get_coded_segments(1)
        data = json.loads(result)
        assert "segments" in data
        assert len(data["segments"]) >= 1

    def test_invalid_code_id(self, setup_server):
        result = server.get_coded_segments(999)
        data = json.loads(result)
        assert data["segment_count"] == 0

    def test_with_limit(self, setup_server):
        result = server.get_coded_segments(1, limit=1)
        data = json.loads(result)
        assert len(data["segments"]) <= 1


class TestSearchFiles:
    """Test search_files tool."""

    def test_search_by_filename(self, setup_server):
        result = server.search_files("interview")
        data = json.loads(result)
        assert data["total_matches"] >= 1

    def test_search_by_content(self, setup_server):
        result = server.search_files(
            "stressed", search_filename=False, search_content=True
        )
        data = json.loads(result)
        assert data["total_matches"] >= 1

    def test_search_no_results(self, setup_server):
        result = server.search_files("xyznonexistent123")
        data = json.loads(result)
        assert data["total_matches"] == 0

    def test_search_empty_pattern(self, setup_server):
        result = server.search_files("")
        data = json.loads(result)
        assert data["total_matches"] == 0


class TestGetCodingFrequencies:
    """Test get_coding_frequencies tool."""

    def test_returns_frequencies(self, setup_server):
        result = server.get_coding_frequencies()
        data = json.loads(result)
        assert "total_coded_segments" in data
        assert "codes" in data
        assert data["total_coded_segments"] == 2


class TestSearchMemos:
    """Test search_memos tool."""

    def test_basic_search(self, setup_server):
        result = server.search_memos("Stress code")
        data = json.loads(result)
        assert "results" in data
        assert len(data["results"]) >= 1

    def test_search_no_results(self, setup_server):
        result = server.search_memos("nonexistent_memo_text")
        data = json.loads(result)
        assert data["result_count"] == 0


class TestExportCodeReport:
    """Test export_code_report tool."""

    def test_valid_code(self, setup_server):
        result = server.export_code_report("Stress")
        # Returns formatted text, not JSON
        assert "Stress" in result

    def test_nonexistent_code(self, setup_server):
        result = server.export_code_report("NonexistentCode")
        data = json.loads(result)
        assert "error" in data


class TestGetProjectSummary:
    """Test get_project_summary tool."""

    def test_returns_summary(self, setup_server):
        result = server.get_project_summary()
        data = json.loads(result)
        assert "project_info" in data
        assert "statistics" in data
        assert data["statistics"]["total_files"] >= 1
        assert data["statistics"]["total_codes"] >= 1


class TestAnalyzeFileWithCoding:
    """Test analyze_file_with_coding tool."""

    def test_valid_file(self, setup_server):
        result = server.analyze_file_with_coding(1)
        data = json.loads(result)
        assert "file_info" in data
        assert "coded_segments" in data

    def test_nonexistent_file(self, setup_server):
        result = server.analyze_file_with_coding(999)
        data = json.loads(result)
        assert "error" in data


# =============================================================================
# TESTS: Attribute Tools
# =============================================================================

class TestListAttributeTypes:
    """Test list_attribute_types tool."""

    def test_returns_types(self, setup_server):
        result = server.list_attribute_types()
        data = json.loads(result)
        assert "attributes" in data
        assert len(data["attributes"]) >= 1


class TestGetFileAttributes:
    """Test get_file_attributes tool."""

    def test_valid_file_id(self, setup_server):
        result = server.get_file_attributes(1)
        data = json.loads(result)
        assert "file_id" in data

    def test_invalid_file_id(self, setup_server):
        result = server.get_file_attributes(999)
        data = json.loads(result)
        assert "file_id" in data


class TestGetCaseAttributes:
    """Test get_case_attributes tool."""

    def test_valid_case_id(self, setup_server):
        result = server.get_case_attributes(1)
        data = json.loads(result)
        assert "case_id" in data
        assert len(data["attributes"]) >= 1

    def test_invalid_case_id(self, setup_server):
        result = server.get_case_attributes(999)
        data = json.loads(result)
        assert "case_id" in data


class TestQueryByAttribute:
    """Test query_by_attribute tool."""

    def test_matching_query(self, setup_server):
        result = server.query_by_attribute("Age", "30", "case")
        data = json.loads(result)
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_no_match(self, setup_server):
        result = server.query_by_attribute("Age", "99", "case")
        data = json.loads(result)
        assert isinstance(data, list)
        assert len(data) == 0


# =============================================================================
# TESTS: Co-occurrence and Matrix Tools
# =============================================================================

class TestFindCooccurringCodes:
    """Test find_cooccurring_codes tool."""

    def test_valid_code_id(self, setup_server):
        result = server.find_cooccurring_codes(1)
        data = json.loads(result)
        assert isinstance(data, list)

    def test_with_window(self, setup_server):
        result = server.find_cooccurring_codes(1, window_size=100)
        data = json.loads(result)
        assert isinstance(data, list)


class TestGetCaseCodeMatrix:
    """Test get_case_code_matrix tool."""

    def test_returns_matrix(self, setup_server):
        result = server.get_case_code_matrix()
        data = json.loads(result)
        assert "cases" in data
        assert "codes" in data
        assert "matrix" in data


class TestGetCodesByCase:
    """Test get_codes_by_case tool."""

    def test_valid_case(self, setup_server):
        result = server.get_codes_by_case(1)
        data = json.loads(result)
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_invalid_case(self, setup_server):
        result = server.get_codes_by_case(999)
        data = json.loads(result)
        assert isinstance(data, list)
        assert len(data) == 0


class TestGetCasesByCode:
    """Test get_cases_by_code tool."""

    def test_valid_code(self, setup_server):
        result = server.get_cases_by_code(1)
        data = json.loads(result)
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_invalid_code(self, setup_server):
        result = server.get_cases_by_code(999)
        data = json.loads(result)
        assert isinstance(data, list)
        assert len(data) == 0


# =============================================================================
# TESTS: AI Coding Conversational Workflow
# =============================================================================

class TestAnalyzeForCoding:
    """Test analyze_for_coding tool."""

    def test_valid_files_and_codes(self, setup_server):
        result = server.analyze_for_coding(
            file_ids=[1],
            code_names=["Stress"],
            instruction="Find stress indicators"
        )
        # Returns formatted text
        assert "SESSION CREATED" in result
        assert "Session ID" in result

    def test_invalid_file_ids(self, setup_server):
        result = server.analyze_for_coding(file_ids=[999])
        data = json.loads(result)
        assert "error" in data

    def test_invalid_code_names(self, setup_server):
        result = server.analyze_for_coding(
            file_ids=[1],
            code_names=["NonexistentCode"]
        )
        data = json.loads(result)
        assert "error" in data

    def test_all_codes_when_none_specified(self, setup_server):
        result = server.analyze_for_coding(file_ids=[1])
        assert "SESSION CREATED" in result
        # Should use all available codes
        assert "2 codes" in result


class TestReviewSuggestions:
    """Test review_suggestions tool."""

    def test_review_all(self, session_with_suggestions):
        session = session_with_suggestions
        result = server.review_suggestions(session.session_id)
        assert "Suggestion" in result
        assert "2 Suggestion" in result

    def test_review_by_guid(self, session_with_suggestions):
        session = session_with_suggestions
        guid = session.suggestions[0].guid
        result = server.review_suggestions(session.session_id, suggestion_guids=[guid])
        assert "1 Suggestion" in result

    def test_review_nonexistent_session(self, setup_server):
        result = server.review_suggestions("nonexistent-id")
        data = json.loads(result)
        assert "error" in data

    def test_review_with_context(self, session_with_suggestions):
        session = session_with_suggestions
        result = server.review_suggestions(session.session_id, show_context=True)
        assert "Suggestion" in result


class TestUpdateSuggestionStatus:
    """Test update_suggestion_status tool."""

    def test_approve_suggestion(self, session_with_suggestions):
        session = session_with_suggestions
        guid = session.suggestions[0].guid
        result = server.update_suggestion_status(
            session.session_id, approve=[guid]
        )
        assert "Approved: 1" in result

    def test_reject_suggestion(self, session_with_suggestions):
        session = session_with_suggestions
        guid = session.suggestions[0].guid
        result = server.update_suggestion_status(
            session.session_id, reject=[guid]
        )
        assert "Rejected: 1" in result

    def test_approve_and_reject(self, session_with_suggestions):
        session = session_with_suggestions
        g1 = session.suggestions[0].guid
        g2 = session.suggestions[1].guid
        result = server.update_suggestion_status(
            session.session_id, approve=[g1], reject=[g2]
        )
        assert "Approved: 1" in result
        assert "Rejected: 1" in result

    def test_nonexistent_session(self, setup_server):
        result = server.update_suggestion_status("nonexistent", approve=["abc"])
        data = json.loads(result)
        assert "error" in data

    def test_nonexistent_guid(self, session_with_suggestions):
        session = session_with_suggestions
        result = server.update_suggestion_status(
            session.session_id, approve=["nonexistent-guid"]
        )
        assert "Approved: 0" in result


class TestApplyCodings:
    """Test apply_codings tool."""

    def test_apply_approved_codings(self, session_with_suggestions, qualcoder_db_path):
        session = session_with_suggestions

        # Approve first suggestion (pos 0-10, which is within file content)
        guid = session.suggestions[0].guid
        server.update_suggestion_status(session.session_id, approve=[guid])

        result = server.apply_codings(session.session_id, create_backup=False)
        assert "CODINGS APPLIED" in result
        assert "1 codings" in result

    def test_apply_with_no_approved(self, session_with_suggestions):
        session = session_with_suggestions
        result = server.apply_codings(session.session_id, create_backup=False)
        assert "No approved suggestions" in result

    def test_apply_nonexistent_session(self, setup_server):
        result = server.apply_codings("nonexistent")
        data = json.loads(result)
        assert "error" in data

    def test_apply_with_backup(self, session_with_suggestions, qualcoder_db_path):
        session = session_with_suggestions
        guid = session.suggestions[0].guid
        server.update_suggestion_status(session.session_id, approve=[guid])

        result = server.apply_codings(session.session_id, create_backup=True)
        assert "CODINGS APPLIED" in result
        assert "Backup created" in result


# =============================================================================
# TESTS: Session Management Tools
# =============================================================================

class TestGetCodingSessionInfo:
    """Test get_coding_session_info tool."""

    def test_valid_session(self, session_with_suggestions):
        session = session_with_suggestions
        result = server.get_coding_session_info(session.session_id)
        data = json.loads(result)
        assert data["session_id"] == session.session_id
        assert len(data["suggestions"]) == 2

    def test_nonexistent_session(self, setup_server):
        result = server.get_coding_session_info("nonexistent")
        data = json.loads(result)
        assert "error" in data


class TestListCodingSessions:
    """Test list_coding_sessions tool."""

    def test_with_sessions(self, session_with_suggestions):
        result = server.list_coding_sessions()
        data = json.loads(result)
        assert data["session_count"] >= 1

    def test_no_sessions(self, setup_server):
        result = server.list_coding_sessions()
        data = json.loads(result)
        assert data["sessions"] == []

    def test_filter_by_days(self, session_with_suggestions):
        result = server.list_coding_sessions(days_old=1)
        data = json.loads(result)
        assert data["session_count"] >= 1


class TestDeleteCodingSession:
    """Test delete_coding_session tool."""

    def test_delete_existing(self, session_with_suggestions):
        session = session_with_suggestions
        result = server.delete_coding_session(session.session_id)
        data = json.loads(result)
        assert data["success"] is True

    def test_delete_nonexistent(self, setup_server):
        result = server.delete_coding_session("nonexistent")
        data = json.loads(result)
        assert data["success"] is False


class TestCleanupOldSessions:
    """Test cleanup_old_sessions tool."""

    def test_cleanup(self, setup_server):
        result = server.cleanup_old_sessions(days_old=0)
        data = json.loads(result)
        assert data["success"] is True
        assert "deleted_count" in data


# =============================================================================
# TESTS: Help Tool
# =============================================================================

class TestExplainAiCodingTools:
    """Test explain_ai_coding_tools tool."""

    def test_overview(self, setup_server):
        result = server.explain_ai_coding_tools()
        data = json.loads(result)
        assert "title" in data
        assert "workflow" in data

    def test_specific_tool(self, setup_server):
        result = server.explain_ai_coding_tools("analyze_for_coding")
        data = json.loads(result)
        assert "purpose" in data

    def test_unknown_tool(self, setup_server):
        result = server.explain_ai_coding_tools("unknown_tool")
        data = json.loads(result)
        assert "error" in data
        assert "available_tools" in data


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
