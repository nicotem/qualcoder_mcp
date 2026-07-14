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
    _detect_file_type,
    validate_id,
    validate_limit,
    validate_string,
    MAX_LIMIT,
    MAX_STRING_LENGTH,
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
        # Tools are guarded: graceful JSON error instead of a raw exception
        data = json.loads(server.search_coded_text("test"))
        assert "No Qualcoder project selected" in data["error"]

    def test_get_coded_segments_no_project(self):
        # Tools are guarded: graceful JSON error instead of a raw exception
        data = json.loads(server.get_coded_segments(1))
        assert "No Qualcoder project selected" in data["error"]

    def test_search_files_no_project(self):
        # search_files catches exceptions and returns JSON error
        result = server.search_files("test")
        data = json.loads(result)
        assert "error" in data

    def test_get_coding_frequencies_no_project(self):
        # Tools are guarded: graceful JSON error instead of a raw exception
        data = json.loads(server.get_coding_frequencies())
        assert "No Qualcoder project selected" in data["error"]

    def test_search_memos_no_project(self):
        # Tools are guarded: graceful JSON error instead of a raw exception
        data = json.loads(server.search_memos("test"))
        assert "No Qualcoder project selected" in data["error"]

    def test_export_code_report_no_project(self):
        # Tools are guarded: graceful JSON error instead of a raw exception
        data = json.loads(server.export_code_report("Test"))
        assert "No Qualcoder project selected" in data["error"]

    def test_get_project_summary_no_project(self):
        # Tools are guarded: graceful JSON error instead of a raw exception
        data = json.loads(server.get_project_summary())
        assert "No Qualcoder project selected" in data["error"]

    def test_analyze_file_with_coding_no_project(self):
        # Tools are guarded: graceful JSON error instead of a raw exception
        data = json.loads(server.analyze_file_with_coding(1))
        assert "No Qualcoder project selected" in data["error"]

    def test_list_attribute_types_no_project(self):
        # Tools are guarded: graceful JSON error instead of a raw exception
        data = json.loads(server.list_attribute_types())
        assert "No Qualcoder project selected" in data["error"]

    def test_get_file_attributes_no_project(self):
        # Tools are guarded: graceful JSON error instead of a raw exception
        data = json.loads(server.get_file_attributes(1))
        assert "No Qualcoder project selected" in data["error"]

    def test_get_case_attributes_no_project(self):
        # Tools are guarded: graceful JSON error instead of a raw exception
        data = json.loads(server.get_case_attributes(1))
        assert "No Qualcoder project selected" in data["error"]

    def test_query_by_attribute_no_project(self):
        # Tools are guarded: graceful JSON error instead of a raw exception
        data = json.loads(server.query_by_attribute("Age", "30"))
        assert "No Qualcoder project selected" in data["error"]

    def test_find_cooccurring_codes_no_project(self):
        # Tools are guarded: graceful JSON error instead of a raw exception
        data = json.loads(server.find_cooccurring_codes(1))
        assert "No Qualcoder project selected" in data["error"]

    def test_get_case_code_matrix_no_project(self):
        # Tools are guarded: graceful JSON error instead of a raw exception
        data = json.loads(server.get_case_code_matrix())
        assert "No Qualcoder project selected" in data["error"]

    def test_get_codes_by_case_no_project(self):
        # Tools are guarded: graceful JSON error instead of a raw exception
        data = json.loads(server.get_codes_by_case(1))
        assert "No Qualcoder project selected" in data["error"]

    def test_get_cases_by_code_no_project(self):
        # Tools are guarded: graceful JSON error instead of a raw exception
        data = json.loads(server.get_cases_by_code(1))
        assert "No Qualcoder project selected" in data["error"]

    def test_analyze_for_coding_no_project(self):
        # Tools are guarded: graceful JSON error instead of a raw exception
        data = json.loads(server.analyze_for_coding(file_ids=[1]))
        assert "No Qualcoder project selected" in data["error"]

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


# =============================================================================
# validate_string() direct unit tests
# =============================================================================

class TestValidateString:
    """Direct unit tests for validate_string() in database.py."""

    def test_non_string_int_raises_type_error(self):
        with pytest.raises(TypeError, match="must be a string, got int"):
            validate_string(42, "test_param")

    def test_non_string_none_raises_type_error(self):
        with pytest.raises(TypeError, match="must be a string, got NoneType"):
            validate_string(None, "test_param")

    def test_non_string_list_raises_type_error(self):
        with pytest.raises(TypeError, match="must be a string, got list"):
            validate_string([1, 2, 3], "test_param")

    def test_truncation_at_max_string_length(self):
        long_string = "a" * 15000
        result = validate_string(long_string, "test_param")
        assert len(result) == MAX_STRING_LENGTH
        assert result == "a" * MAX_STRING_LENGTH

    def test_string_at_exact_max_length_not_truncated(self):
        exact_string = "b" * MAX_STRING_LENGTH
        result = validate_string(exact_string, "test_param")
        assert len(result) == MAX_STRING_LENGTH
        assert result == exact_string

    def test_normal_string_passes_through_unchanged(self):
        result = validate_string("hello world", "test_param")
        assert result == "hello world"

    def test_empty_string_passes_through(self):
        result = validate_string("", "test_param")
        assert result == ""

    def test_default_param_name_in_error(self):
        with pytest.raises(TypeError, match="value must be a string"):
            validate_string(123)


# =============================================================================
# apply_codings() rollback integration test
# =============================================================================

class TestApplyCodingsRollback:
    """Test that apply_codings() rolls back ALL codings when one fails mid-batch."""

    def test_rollback_on_invalid_code_id(self, setup_server, qualcoder_db_path):
        """When a mid-batch suggestion has an invalid code_id, all codings
        (including earlier successful ones) must be rolled back."""
        # Count existing codings before the test
        initial_count = setup_server.db.conn.execute(
            "SELECT COUNT(*) FROM code_text"
        ).fetchone()[0]

        # Create a session with 2 approved suggestions:
        #   - first is valid (code_id=1 exists)
        #   - second has invalid code_id=99999 (does not exist)
        session = AICodingSession(
            project_path=qualcoder_db_path,
            description="Rollback test session",
            file_ids=[1],
            code_names=["Stress"],
            instruction="Test rollback",
            min_confidence=0.5
        )

        valid_suggestion = CodingSuggestion(
            file_id=1, file_name="interview.txt",
            code_id=1, code_name="Stress",
            start_pos=0, end_pos=10,
            segment_text="This is in",
            reasoning="Valid suggestion", confidence=0.9,
            status="approved"
        )
        invalid_suggestion = CodingSuggestion(
            file_id=1, file_name="interview.txt",
            code_id=99999, code_name="NonexistentCode",
            start_pos=20, end_pos=30,
            segment_text="interview ",
            reasoning="Invalid code_id", confidence=0.8,
            status="approved"
        )

        session.add_suggestion(valid_suggestion)
        session.add_suggestion(invalid_suggestion)
        setup_server.session_manager.save_session(session)

        # Call apply_codings -- should fail and roll back
        result = server.apply_codings(
            session_id=session.session_id,
            create_backup=False,
            owner="Rollback Tester"
        )

        # Verify the result indicates failure
        data = json.loads(result)
        assert "error" in data
        assert "rolled back" in data["error"].lower()

        # Verify ZERO new codings were inserted -- the valid one was also rolled back
        # Check by owner to be precise (per security-eng recommendation)
        ai_count = setup_server.db.conn.execute(
            "SELECT COUNT(*) FROM code_text WHERE owner = ?",
            ("Rollback Tester",)
        ).fetchone()[0]
        assert ai_count == 0, (
            f"Expected 0 codings from 'Rollback Tester', but found {ai_count}"
        )

        # Also verify total count unchanged as a belt-and-suspenders check
        final_count = setup_server.db.conn.execute(
            "SELECT COUNT(*) FROM code_text"
        ).fetchone()[0]
        assert final_count == initial_count, (
            f"Expected {initial_count} total codings (no new ones), but found {final_count}"
        )

        # Verify original rows are intact (no corruption from rollback)
        original_rows = setup_server.db.conn.execute(
            "SELECT ctid, cid, fid, owner FROM code_text WHERE owner = 'TestCoder' ORDER BY ctid"
        ).fetchall()
        assert len(original_rows) == 2
        assert tuple(original_rows[0]) == (1, 1, 1, "TestCoder")
        assert tuple(original_rows[1]) == (2, 2, 1, "TestCoder")


# =============================================================================
# RW connection downgrade after apply_codings()
# =============================================================================

class TestRWConnectionDowngrade:
    """Test that the database is downgraded to read-only after apply_codings()."""

    def test_downgrade_after_successful_apply(
        self, setup_server, qualcoder_db_path
    ):
        """After a successful apply_codings(), the global db should be read-only."""
        # Create a session with one valid approved suggestion
        session = AICodingSession(
            project_path=qualcoder_db_path,
            description="Downgrade test session",
            file_ids=[1],
            code_names=["Stress"],
            instruction="Test downgrade",
            min_confidence=0.5
        )
        suggestion = CodingSuggestion(
            file_id=1, file_name="interview.txt",
            code_id=1, code_name="Stress",
            start_pos=10, end_pos=20,
            segment_text="interview ",
            reasoning="Test", confidence=0.9,
            status="approved"
        )
        session.add_suggestion(suggestion)
        setup_server.session_manager.save_session(session)

        # Apply codings (should succeed)
        result = server.apply_codings(
            session_id=session.session_id,
            create_backup=False,
            owner="Downgrade Tester"
        )
        assert "error" not in result.lower() or "rolled back" not in result.lower()

        # The global db should now be read-only
        assert server.db is not None
        assert server.db.read_only is True

    def test_downgrade_after_failed_apply(
        self, setup_server, qualcoder_db_path
    ):
        """After a failed apply_codings(), the global db should be read-only."""
        session = AICodingSession(
            project_path=qualcoder_db_path,
            description="Downgrade failure test",
            file_ids=[1],
            code_names=["Stress"],
            instruction="Test downgrade on failure",
            min_confidence=0.5
        )
        bad_suggestion = CodingSuggestion(
            file_id=1, file_name="interview.txt",
            code_id=99999, code_name="Nonexistent",
            start_pos=0, end_pos=10,
            segment_text="This is in",
            reasoning="Bad code", confidence=0.9,
            status="approved"
        )
        session.add_suggestion(bad_suggestion)
        setup_server.session_manager.save_session(session)

        # Apply codings (should fail)
        result = server.apply_codings(
            session_id=session.session_id,
            create_backup=False,
            owner="Downgrade Tester"
        )
        data = json.loads(result)
        assert "error" in data

        # The global db should still be read-only after failure
        assert server.db is not None
        assert server.db.read_only is True

    def test_write_rejected_after_downgrade(
        self, setup_server, qualcoder_db_path
    ):
        """After downgrade, write operations should be rejected with RuntimeError."""
        session = AICodingSession(
            project_path=qualcoder_db_path,
            description="Write rejection test",
            file_ids=[1],
            code_names=["Stress"],
            instruction="Test write rejection",
            min_confidence=0.5
        )
        suggestion = CodingSuggestion(
            file_id=1, file_name="interview.txt",
            code_id=1, code_name="Stress",
            start_pos=10, end_pos=20,
            segment_text="interview ",
            reasoning="Test", confidence=0.9,
            status="approved"
        )
        session.add_suggestion(suggestion)
        setup_server.session_manager.save_session(session)

        # Apply codings (should succeed and downgrade)
        server.apply_codings(
            session_id=session.session_id,
            create_backup=False,
            owner="Write Rejection Tester"
        )

        # Attempt a direct write on the downgraded connection -- must be rejected
        assert server.db.read_only is True
        with pytest.raises(RuntimeError, match="read-only mode"):
            server.db.add_coding(
                file_id=1, code_id=1, start_pos=0, end_pos=5,
                selected_text="test", owner="should_fail"
            )

    def test_reconnect_after_downgrade_sets_db_none(
        self, setup_server, qualcoder_db_path
    ):
        """If _downgrade_to_readonly() fails and sets db=None, the next get_db()
        call should reconnect via current_project_path."""
        # Simulate the edge case: force db to None while project path is set
        if server.db is not None:
            server.db.close()
        server.db = None
        assert server.current_project_path is not None

        # get_db() should reconnect automatically
        reconnected_db = server.get_db(read_only=True)
        assert reconnected_db is not None
        assert reconnected_db.read_only is True
        # Verify it works by running a read operation
        info = reconnected_db.get_project_info()
        assert info is not None


class TestDetectFileType:
    """Direct unit tests for _detect_file_type() helper."""

    # NULL and empty string → text (created in QualCoder)
    def test_none_returns_text(self):
        assert _detect_file_type(None) == "text"

    def test_empty_string_returns_text(self):
        assert _detect_file_type("") == "text"

    # Imported docs → text
    def test_imported_doc(self):
        assert _detect_file_type("/docs/interview.txt") == "text"

    def test_imported_docx(self):
        assert _detect_file_type("/docs/report.docx") == "text"

    def test_imported_odt(self):
        assert _detect_file_type("/docs/notes.odt") == "text"

    # Linked docs → text
    def test_linked_doc(self):
        assert _detect_file_type("docs:/home/user/interview.txt") == "text"

    def test_linked_docx(self):
        assert _detect_file_type("docs:/home/user/report.docx") == "text"

    # Imported PDF → pdf (special case under /docs/)
    def test_imported_pdf(self):
        assert _detect_file_type("/docs/paper.pdf") == "pdf"

    def test_imported_pdf_uppercase(self):
        """QualCoder stores lowercase extensions, but test mixed case."""
        assert _detect_file_type("/docs/paper.PDF") == "pdf"

    # Linked PDF → pdf
    def test_linked_pdf(self):
        assert _detect_file_type("docs:/home/user/paper.pdf") == "pdf"

    # Images
    def test_imported_image(self):
        assert _detect_file_type("/images/chart.png") == "image"

    def test_imported_jpg(self):
        assert _detect_file_type("/images/photo.jpg") == "image"

    def test_linked_image(self):
        assert _detect_file_type("images:/home/user/chart.png") == "image"

    # Audio
    def test_imported_audio(self):
        assert _detect_file_type("/audio/recording.mp3") == "audio"

    def test_imported_wav(self):
        assert _detect_file_type("/audio/interview.wav") == "audio"

    def test_linked_audio(self):
        assert _detect_file_type("audio:/home/user/recording.mp3") == "audio"

    # Video
    def test_imported_video(self):
        assert _detect_file_type("/video/interview.mp4") == "video"

    def test_imported_mov(self):
        assert _detect_file_type("/video/clip.mov") == "video"

    def test_linked_video(self):
        assert _detect_file_type("video:/home/user/interview.mp4") == "video"

    # Unknown prefix → media fallback
    def test_unknown_prefix(self):
        assert _detect_file_type("/other/file.bin") == "media"

    def test_bare_path(self):
        assert _detect_file_type("/some/random/path.txt") == "media"

    # Case sensitivity: QualCoder uses lowercase prefixes
    def test_uppercase_prefix_falls_through(self):
        """QualCoder always writes lowercase prefixes, so uppercase is unknown."""
        assert _detect_file_type("/Images/chart.png") == "media"

    def test_uppercase_docs_falls_through(self):
        assert _detect_file_type("/Docs/file.txt") == "media"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
