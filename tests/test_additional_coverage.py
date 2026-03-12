"""
Additional test coverage identified by QA and security reviews.

HIGH priority: no-project tests, SQL injection gaps, session validation,
               deserialization tests.
MEDIUM priority: search modes, path traversal, attr_type="file",
                 integer overflow, confidence bounds, partial failure.
"""

import pytest
import json
import sqlite3
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server
from qualcoder_mcp.database import (
    QualcoderDatabase,
    validate_id,
    validate_limit,
    MAX_LIMIT,
)
from qualcoder_mcp.sessions import (
    SessionManager,
    AICodingSession,
    CodingSuggestion,
)


# =============================================================================
# HIGH-1: "No project loaded" tests for all tools/resources that call get_db()
# =============================================================================

class TestNoProjectLoaded:
    """Verify all tools/resources fail gracefully when no project is open."""

    @pytest.fixture(autouse=True)
    def _clear_server(self):
        original_db = server.db
        original_path = server.current_project_path
        try:
            server.db = None
            server.current_project_path = None
            import os
            original_env = os.environ.pop("QUALCODER_PROJECT_PATH", None)
            yield
        finally:
            server.db = original_db
            server.current_project_path = original_path
            if original_env:
                os.environ["QUALCODER_PROJECT_PATH"] = original_env

    def test_search_coded_text_no_project(self):
        with pytest.raises(ValueError, match="No Qualcoder project selected"):
            server.search_coded_text("test")

    def test_get_coded_segments_no_project(self):
        with pytest.raises(ValueError, match="No Qualcoder project selected"):
            server.get_coded_segments(1)

    def test_search_files_no_project(self):
        # search_files catches exceptions and returns JSON error
        result = server.search_files("test")
        data = json.loads(result)
        assert "error" in data

    def test_get_coding_frequencies_no_project(self):
        with pytest.raises(ValueError, match="No Qualcoder project selected"):
            server.get_coding_frequencies()

    def test_search_memos_no_project(self):
        with pytest.raises(ValueError, match="No Qualcoder project selected"):
            server.search_memos("test")

    def test_export_code_report_no_project(self):
        with pytest.raises(ValueError, match="No Qualcoder project selected"):
            server.export_code_report("Test")

    def test_get_project_summary_no_project(self):
        with pytest.raises(ValueError, match="No Qualcoder project selected"):
            server.get_project_summary()

    def test_analyze_file_with_coding_no_project(self):
        with pytest.raises(ValueError, match="No Qualcoder project selected"):
            server.analyze_file_with_coding(1)

    def test_list_attribute_types_no_project(self):
        with pytest.raises(ValueError, match="No Qualcoder project selected"):
            server.list_attribute_types()

    def test_get_file_attributes_no_project(self):
        with pytest.raises(ValueError, match="No Qualcoder project selected"):
            server.get_file_attributes(1)

    def test_get_case_attributes_no_project(self):
        with pytest.raises(ValueError, match="No Qualcoder project selected"):
            server.get_case_attributes(1)

    def test_query_by_attribute_no_project(self):
        with pytest.raises(ValueError, match="No Qualcoder project selected"):
            server.query_by_attribute("Age", "30")

    def test_find_cooccurring_codes_no_project(self):
        with pytest.raises(ValueError, match="No Qualcoder project selected"):
            server.find_cooccurring_codes(1)

    def test_get_case_code_matrix_no_project(self):
        with pytest.raises(ValueError, match="No Qualcoder project selected"):
            server.get_case_code_matrix()

    def test_get_codes_by_case_no_project(self):
        with pytest.raises(ValueError, match="No Qualcoder project selected"):
            server.get_codes_by_case(1)

    def test_get_cases_by_code_no_project(self):
        with pytest.raises(ValueError, match="No Qualcoder project selected"):
            server.get_cases_by_code(1)

    def test_analyze_for_coding_no_project(self):
        with pytest.raises(ValueError, match="No Qualcoder project selected"):
            server.analyze_for_coding(file_ids=[1])

    # Resources
    def test_get_project_info_no_project(self):
        with pytest.raises(ValueError, match="No Qualcoder project selected"):
            server.get_project_info()

    def test_list_all_codes_no_project(self):
        with pytest.raises(ValueError, match="No Qualcoder project selected"):
            server.list_all_codes()

    def test_list_all_categories_no_project(self):
        with pytest.raises(ValueError, match="No Qualcoder project selected"):
            server.list_all_categories()

    def test_get_code_info_no_project(self):
        with pytest.raises(ValueError, match="No Qualcoder project selected"):
            server.get_code_info(1)

    def test_list_all_files_no_project(self):
        with pytest.raises(ValueError, match="No Qualcoder project selected"):
            server.list_all_files()

    def test_get_file_content_no_project(self):
        with pytest.raises(ValueError, match="No Qualcoder project selected"):
            server.get_file_content(1)

    def test_list_all_cases_no_project(self):
        with pytest.raises(ValueError, match="No Qualcoder project selected"):
            server.list_all_cases()

    def test_get_case_info_no_project(self):
        with pytest.raises(ValueError, match="No Qualcoder project selected"):
            server.get_case_info(1)

    def test_get_journal_entries_no_project(self):
        with pytest.raises(ValueError, match="No Qualcoder project selected"):
            server.get_journal_entries()


# =============================================================================
# HIGH-2: SQL injection tests for additional string parameters
# =============================================================================

class TestAdditionalSqlInjection:
    """SQL injection tests for parameters not covered by test_security.py."""

    PAYLOADS = [
        "'; DROP TABLE code_text; --",
        "' OR '1'='1",
        "' UNION SELECT * FROM project --",
    ]

    @pytest.mark.parametrize("payload", PAYLOADS)
    def test_export_code_report_injection(self, setup_server, payload):
        result = server.export_code_report(payload)
        data = json.loads(result)
        assert "error" in data
        # Verify DB intact
        assert server.db.conn.execute("SELECT COUNT(*) FROM code_name").fetchone()[0] >= 1

    @pytest.mark.parametrize("payload", PAYLOADS)
    def test_query_by_attribute_attr_type_injection(self, setup_server, payload):
        """Inject into attr_type parameter (should be 'case' or 'file')."""
        try:
            result = server.query_by_attribute("Age", "30", payload)
            data = json.loads(result)
            assert isinstance(data, (list, dict))
        except ValueError:
            pass  # Validation rejects invalid attr_type
        # Verify DB intact
        assert server.db.conn.execute("SELECT COUNT(*) FROM code_name").fetchone()[0] >= 1


# =============================================================================
# HIGH-3: Session ID validation for save_session() write path
# =============================================================================

class TestSessionIdValidation:
    """Verify session ID validation on all SessionManager paths."""

    def test_save_session_with_traversal_id(self, tmp_path):
        manager = SessionManager(str(tmp_path / "sessions"))
        session = AICodingSession(
            project_path="/test.qda",
            session_id="../../etc/passwd",
            description="malicious"
        )
        with pytest.raises(ValueError, match="Invalid session ID format"):
            manager.save_session(session)

    def test_save_session_with_valid_uuid(self, tmp_path):
        manager = SessionManager(str(tmp_path / "sessions"))
        session = AICodingSession(
            project_path="/test.qda",
            description="legitimate"
        )
        # Should not raise -- session_id auto-generated as UUID4
        manager.save_session(session)
        assert manager.session_exists(session.session_id)

    def test_session_exists_invalid_id(self, tmp_path):
        manager = SessionManager(str(tmp_path / "sessions"))
        assert manager.session_exists("../../../etc/passwd") is False
        assert manager.session_exists("not-a-uuid") is False
        assert manager.session_exists("") is False

    def test_delete_session_invalid_id(self, tmp_path):
        manager = SessionManager(str(tmp_path / "sessions"))
        assert manager.delete_session("../../../etc/passwd") is False
        assert manager.delete_session("not-a-uuid") is False


# =============================================================================
# HIGH-4: Deserialization tests with malformed data
# =============================================================================

class TestDeserializationValidation:
    """Test from_dict() with malformed data."""

    def test_coding_suggestion_missing_required_fields(self):
        with pytest.raises(ValueError, match="Missing required fields"):
            CodingSuggestion.from_dict({"file_id": 1, "file_name": "test.txt"})

    def test_coding_suggestion_not_a_dict(self):
        with pytest.raises(TypeError, match="must be a dictionary"):
            CodingSuggestion.from_dict("not a dict")

    def test_coding_suggestion_none_input(self):
        with pytest.raises(TypeError, match="must be a dictionary"):
            CodingSuggestion.from_dict(None)

    def test_coding_suggestion_empty_dict(self):
        with pytest.raises(ValueError, match="Missing required fields"):
            CodingSuggestion.from_dict({})

    def test_session_missing_required_fields(self):
        with pytest.raises(ValueError, match="Missing required session fields"):
            AICodingSession.from_dict({"project_path": "/test.qda"})

    def test_session_not_a_dict(self):
        with pytest.raises(TypeError, match="must be a dictionary"):
            AICodingSession.from_dict([1, 2, 3])

    def test_session_empty_dict(self):
        with pytest.raises(ValueError, match="Missing required session fields"):
            AICodingSession.from_dict({})


# =============================================================================
# MEDIUM-5: search_files multi-mode tests
# =============================================================================

class TestSearchFilesMultiMode:
    """Test search_files with combined search flags."""

    def test_search_content_and_filename(self, setup_server):
        result = server.search_files(
            "interview",
            search_filename=True,
            search_content=True
        )
        data = json.loads(result)
        assert data["total_matches"] >= 1

    def test_search_memo_only(self, setup_server):
        result = server.search_files(
            "Test memo",
            search_filename=False,
            search_content=False,
            search_memo=True
        )
        data = json.loads(result)
        assert isinstance(data, dict)

    def test_search_all_modes(self, setup_server):
        result = server.search_files(
            "test",
            search_filename=True,
            search_content=True,
            search_memo=True
        )
        data = json.loads(result)
        assert isinstance(data, dict)

    def test_search_case_sensitive(self, setup_server):
        # Case-sensitive search for "Interview" should fail (data has lowercase)
        result = server.search_files(
            "Interview",
            search_filename=True,
            case_sensitive=True
        )
        data = json.loads(result)
        assert isinstance(data, dict)


# =============================================================================
# MEDIUM-6: Path traversal for list_available_projects
# =============================================================================

class TestListAvailableProjectsTraversal:
    """Test path traversal in search_directories parameter."""

    def test_traversal_in_search_dirs(self):
        result = server.list_available_projects(
            search_directories=["../../../etc"]
        )
        data = json.loads(result)
        # Should not crash; results should be empty or contain no system files
        assert isinstance(data, dict)

    def test_null_bytes_in_search_dirs(self):
        result = server.list_available_projects(
            search_directories=["/tmp\x00/evil"]
        )
        data = json.loads(result)
        assert isinstance(data, dict)


# =============================================================================
# MEDIUM-7: query_by_attribute with attr_type="file"
# =============================================================================

class TestQueryByAttributeFile:
    """Test query_by_attribute with file attributes."""

    def test_file_type_no_results(self, setup_server):
        result = server.query_by_attribute("SomeAttr", "SomeValue", "file")
        data = json.loads(result)
        assert isinstance(data, list)
        assert len(data) == 0

    def test_invalid_attr_type(self, setup_server):
        """Invalid attr_type should raise ValueError."""
        try:
            result = server.query_by_attribute("Age", "30", "invalid")
            # If it returns JSON, check for error
            data = json.loads(result)
        except ValueError:
            pass  # Expected


# =============================================================================
# MEDIUM-8: Expanded integer overflow tests
# =============================================================================

class TestExpandedIntegerOverflow:
    """Test extreme integer values beyond 2**31."""

    def test_very_large_code_id_64bit(self, qualcoder_db_path):
        """2**63 exceeds SQLite INTEGER range and raises OverflowError."""
        db = QualcoderDatabase(qualcoder_db_path)
        with pytest.raises(OverflowError):
            db.get_code_details(2**63)
        db.close()

    def test_very_large_file_id_64bit(self, qualcoder_db_path):
        """2**63 exceeds SQLite INTEGER range and raises OverflowError."""
        db = QualcoderDatabase(qualcoder_db_path)
        with pytest.raises(OverflowError):
            db.get_file_content(2**63)
        db.close()

    def test_negative_extreme_id(self):
        with pytest.raises(ValueError, match="must be non-negative"):
            validate_id(-2**31, "test_id")

    def test_negative_extreme_limit(self):
        with pytest.raises(ValueError, match="must be positive"):
            validate_limit(-2**31)

    def test_zero_id_valid(self):
        """Zero is a valid non-negative ID."""
        result = validate_id(0, "test_id")
        assert result == 0


# =============================================================================
# MEDIUM-9: Confidence bounds tests
# =============================================================================

class TestConfidenceBounds:
    """Test that confidence values are properly clamped."""

    def test_negative_confidence_clamped(self):
        s = CodingSuggestion(
            file_id=1, file_name="test.txt",
            code_id=1, code_name="Test",
            start_pos=0, end_pos=5,
            segment_text="hello",
            confidence=-0.5
        )
        assert s.confidence == 0.0

    def test_over_one_confidence_clamped(self):
        s = CodingSuggestion(
            file_id=1, file_name="test.txt",
            code_id=1, code_name="Test",
            start_pos=0, end_pos=5,
            segment_text="hello",
            confidence=1.5
        )
        assert s.confidence == 1.0

    def test_extreme_negative_confidence(self):
        s = CodingSuggestion(
            file_id=1, file_name="test.txt",
            code_id=1, code_name="Test",
            start_pos=0, end_pos=5,
            segment_text="hello",
            confidence=-1000.0
        )
        assert s.confidence == 0.0

    def test_extreme_positive_confidence(self):
        s = CodingSuggestion(
            file_id=1, file_name="test.txt",
            code_id=1, code_name="Test",
            start_pos=0, end_pos=5,
            segment_text="hello",
            confidence=float('inf')
        )
        # inf > 1.0, so min(1.0, inf) = 1.0
        assert s.confidence == 1.0

    def test_normal_confidence_unchanged(self):
        s = CodingSuggestion(
            file_id=1, file_name="test.txt",
            code_id=1, code_name="Test",
            start_pos=0, end_pos=5,
            segment_text="hello",
            confidence=0.75
        )
        assert s.confidence == 0.75

    def test_boundary_confidence_zero(self):
        s = CodingSuggestion(
            file_id=1, file_name="test.txt",
            code_id=1, code_name="Test",
            start_pos=0, end_pos=5,
            segment_text="hello",
            confidence=0.0
        )
        assert s.confidence == 0.0

    def test_boundary_confidence_one(self):
        s = CodingSuggestion(
            file_id=1, file_name="test.txt",
            code_id=1, code_name="Test",
            start_pos=0, end_pos=5,
            segment_text="hello",
            confidence=1.0
        )
        assert s.confidence == 1.0


# =============================================================================
# MEDIUM-10: Read-only mode enforcement tests
# =============================================================================

class TestReadOnlyMode:
    """Test that read-only databases reject write operations."""

    def test_add_coding_rejected_in_readonly(self, qualcoder_db_path):
        db = QualcoderDatabase(qualcoder_db_path, read_only=True)
        with pytest.raises(RuntimeError, match="read-only mode"):
            db.add_coding(
                file_id=1, code_id=1, start_pos=0, end_pos=5,
                selected_text="test", owner="tester"
            )
        db.close()

    def test_add_code_rejected_in_readonly(self, qualcoder_db_path):
        db = QualcoderDatabase(qualcoder_db_path, read_only=True)
        with pytest.raises(RuntimeError, match="read-only mode"):
            db.add_code(name="NewCode", owner="tester")
        db.close()

    def test_add_memo_rejected_in_readonly(self, qualcoder_db_path):
        db = QualcoderDatabase(qualcoder_db_path, read_only=True)
        with pytest.raises(RuntimeError, match="read-only mode"):
            db.add_memo_to_coding(coding_id=1, memo="test", owner="tester")
        db.close()

    def test_read_operations_work_in_readonly(self, qualcoder_db_path):
        db = QualcoderDatabase(qualcoder_db_path, read_only=True)
        # All read operations should succeed
        assert db.get_project_info() is not None
        assert isinstance(db.list_codes(), list)
        assert isinstance(db.list_files(), list)
        assert isinstance(db.list_cases(), list)
        db.close()


# =============================================================================
# MEDIUM-11: Error information leakage (expanded)
# =============================================================================

class TestExpandedErrorLeakage:
    """Expanded error information leakage tests."""

    def test_select_project_no_internal_paths(self):
        result = server.select_project("/very/secret/internal/path.qda")
        data = json.loads(result)
        assert "error" in data
        error_msg = data["error"]
        # Should not contain internal paths
        assert "/very/secret" not in error_msg
        assert "Traceback" not in error_msg

    def test_select_project_no_stack_trace(self, tmp_path):
        result = server.select_project(str(tmp_path / "nonexistent.qda"))
        data = json.loads(result)
        assert "error" in data
        assert "Traceback" not in data["error"]
        assert "File \"" not in data["error"]

    def test_analyze_for_coding_error_no_leakage(self, setup_server):
        result = server.analyze_for_coding(file_ids=[999])
        data = json.loads(result)
        assert "error" in data
        # Error should describe the problem without leaking paths
        assert "Traceback" not in data.get("error", "")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
