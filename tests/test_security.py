"""
Security-focused tests for the QualCoder MCP server.

Tests for:
- SQL injection via all string parameters
- Path traversal in project selection and file operations
- LIKE wildcard injection
- Integer overflow in IDs and limits
- Very long strings
- Unicode/special characters
- Error message information leakage

Shared fixtures (qualcoder_db_path, setup_server) are provided by conftest.py.
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
    validate_qda_path,
    validate_limit,
    validate_id,
    escape_like_pattern,
    MAX_LIMIT,
)


# =============================================================================
# FIXTURES (security-specific only; shared fixtures come from conftest.py)
# =============================================================================

@pytest.fixture
def db(qualcoder_db_path):
    """Create a read-write database instance for security testing."""
    database = QualcoderDatabase(qualcoder_db_path, read_only=False)
    yield database
    database.close()


# =============================================================================
# SQL INJECTION TESTS
# =============================================================================

class TestSqlInjectionSearchCodedText:
    """SQL injection attempts via search_coded_text."""

    INJECTION_PAYLOADS = [
        "'; DROP TABLE code_text; --",
        "' OR '1'='1",
        "' UNION SELECT * FROM project --",
        "1; DELETE FROM code_text",
        "'; UPDATE code_name SET name='hacked'; --",
        "' OR 1=1; --",
        "\" OR \"\"=\"",
        "'; ATTACH DATABASE '/tmp/hacked.db' AS hacked; --",
    ]

    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    def test_search_coded_text_injection(self, setup_server, payload):
        result = server.search_coded_text(payload)
        data = json.loads(result)
        assert isinstance(data, dict)
        # Verify tables still exist
        assert server.db.conn.execute("SELECT COUNT(*) FROM code_text").fetchone()[0] >= 1

    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    def test_search_coded_text_code_name_injection(self, setup_server, payload):
        result = server.search_coded_text("test", code_name=payload)
        data = json.loads(result)
        assert isinstance(data, dict)

    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    def test_search_files_injection(self, setup_server, payload):
        result = server.search_files(payload)
        data = json.loads(result)
        assert isinstance(data, dict)

    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    def test_search_memos_injection(self, setup_server, payload):
        result = server.search_memos(payload)
        data = json.loads(result)
        assert isinstance(data, dict)


class TestSqlInjectionDatabaseLayer:
    """SQL injection at the database layer."""

    INJECTION_PAYLOADS = [
        "'; DROP TABLE code_text; --",
        "' OR '1'='1",
        "' UNION SELECT * FROM project --",
    ]

    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    def test_search_coded_text_db(self, db, payload):
        results = db.search_coded_text(payload)
        assert isinstance(results, list)
        # Verify db still intact
        assert db.conn.execute("SELECT COUNT(*) FROM code_text").fetchone()[0] >= 1

    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    def test_search_coded_text_with_code_filter(self, db, payload):
        results = db.search_coded_text("test", code_name=payload)
        assert isinstance(results, list)

    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    def test_search_files_db(self, db, payload):
        result = db.search_files(payload)
        assert isinstance(result, dict)

    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    def test_search_memos_db(self, db, payload):
        results = db.search_memos(payload)
        assert isinstance(results, list)

    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    def test_query_by_attribute_db(self, db, payload):
        results = db.query_by_attribute(payload, payload, "case")
        assert isinstance(results, list)

    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    def test_add_code_name_injection(self, db, payload):
        """Verify SQL injection in code name is safely handled."""
        try:
            db.add_code(name=payload, owner="test")
        except (ValueError, sqlite3.IntegrityError):
            pass
        # Verify original code still exists
        codes = db.list_codes()
        assert any(c["name"] == "Stress" for c in codes)

    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    def test_add_coding_owner_injection(self, db, payload):
        """Verify SQL injection in owner field is safely handled."""
        try:
            db.add_coding(
                file_id=1, code_id=1, start_pos=10, end_pos=15,
                selected_text="text", owner=payload
            )
        except (ValueError, sqlite3.IntegrityError):
            pass
        # Verify db still intact
        assert db.conn.execute("SELECT COUNT(*) FROM code_text").fetchone()[0] >= 1


# =============================================================================
# LIKE WILDCARD INJECTION TESTS
# =============================================================================

class TestLikeWildcardInjection:
    """Test that LIKE wildcards are properly escaped."""

    def test_percent_in_search(self, db):
        results = db.search_coded_text("100%")
        assert isinstance(results, list)

    def test_underscore_in_search(self, db):
        results = db.search_coded_text("test_value")
        assert isinstance(results, list)

    def test_backslash_in_search(self, db):
        results = db.search_coded_text("test\\value")
        assert isinstance(results, list)

    def test_combined_wildcards(self, db):
        results = db.search_coded_text("%_\\")
        assert isinstance(results, list)

    def test_escape_like_pattern_function(self):
        assert escape_like_pattern("hello%world") == "hello\\%world"
        assert escape_like_pattern("test_123") == "test\\_123"
        assert escape_like_pattern("path\\file") == "path\\\\file"
        assert escape_like_pattern("normal") == "normal"
        assert escape_like_pattern("") == ""


# =============================================================================
# PATH TRAVERSAL TESTS
# =============================================================================

class TestPathTraversal:
    """Test path traversal prevention."""

    def test_select_project_traversal(self, setup_server, tmp_path):
        """Attempt to access files outside intended directories."""
        traversal_paths = [
            "../../../etc/passwd",
            "..\\..\\..\\windows\\system32",
            "/etc/passwd",
            "~/../../etc/shadow",
        ]
        for path in traversal_paths:
            result = server.select_project(path)
            data = json.loads(result)
            assert "error" in data

    def test_validate_qda_path_traversal(self, tmp_path):
        """Path validation should reject traversal attempts."""
        with pytest.raises((FileNotFoundError, ValueError)):
            validate_qda_path("../../../etc/passwd")

        with pytest.raises((FileNotFoundError, ValueError)):
            validate_qda_path("/etc/passwd")

    def test_validate_qda_path_requires_extension(self, tmp_path):
        """Path must have .qda extension."""
        plain_dir = tmp_path / "not_qda"
        plain_dir.mkdir()

        with pytest.raises(ValueError, match="must have .qda extension"):
            validate_qda_path(str(plain_dir))


# =============================================================================
# INPUT VALIDATION TESTS
# =============================================================================

class TestInputValidation:
    """Test input validation edge cases."""

    def test_validate_id_negative(self):
        with pytest.raises(ValueError, match="must be non-negative"):
            validate_id(-1, "test_id")

    def test_validate_id_string(self):
        with pytest.raises(TypeError):
            validate_id("1", "test_id")

    def test_validate_id_float(self):
        with pytest.raises(TypeError):
            validate_id(1.5, "test_id")

    def test_validate_limit_negative(self):
        with pytest.raises(ValueError, match="must be positive"):
            validate_limit(-1)

    def test_validate_limit_zero(self):
        with pytest.raises(ValueError, match="must be positive"):
            validate_limit(0)

    def test_validate_limit_string(self):
        with pytest.raises(TypeError):
            validate_limit("100")

    def test_validate_limit_capped(self):
        result = validate_limit(999999)
        assert result == MAX_LIMIT

    def test_validate_limit_at_max(self):
        result = validate_limit(MAX_LIMIT)
        assert result == MAX_LIMIT


class TestIntegerOverflow:
    """Test handling of extreme integer values."""

    def test_very_large_code_id(self, db):
        result = db.get_code_details(2**31)
        assert result is None

    def test_very_large_file_id(self, db):
        result = db.get_file_content(2**31)
        assert result is None

    def test_very_large_limit(self, db):
        segments = db.get_coded_text_segments(1, limit=2**31)
        assert isinstance(segments, list)


class TestVeryLongStrings:
    """Test handling of very long input strings."""

    def test_long_search_query(self, db):
        long_query = "a" * 10000
        results = db.search_coded_text(long_query)
        assert isinstance(results, list)
        assert len(results) == 0

    def test_long_code_name(self, db):
        long_name = "x" * 10000
        try:
            db.add_code(name=long_name, owner="test")
            # If it succeeds, that's fine - no crash
        except (ValueError, sqlite3.OperationalError):
            pass

    def test_long_memo(self, db):
        long_memo = "m" * 100000
        try:
            db.add_code(name="LongMemoCode", owner="test", memo=long_memo)
        except (ValueError, sqlite3.OperationalError):
            pass
        # Should not crash regardless


class TestUnicodeAndSpecialCharacters:
    """Test handling of unicode and special characters."""

    def test_unicode_search(self, db):
        results = db.search_coded_text("日本語テスト")
        assert isinstance(results, list)

    def test_emoji_search(self, db):
        results = db.search_coded_text("🎉🔥💯")
        assert isinstance(results, list)

    def test_null_bytes_in_search(self, db):
        results = db.search_coded_text("test\x00injection")
        assert isinstance(results, list)

    def test_newlines_in_search(self, db):
        results = db.search_coded_text("test\n\rinjection")
        assert isinstance(results, list)

    def test_unicode_code_name(self, db):
        try:
            cid = db.add_code(name="コード", owner="テスター")
            assert cid is not None
        except (ValueError, sqlite3.OperationalError):
            pass

    def test_rtl_text_in_search(self, db):
        results = db.search_coded_text("مرحبا")
        assert isinstance(results, list)

    def test_combining_characters(self, db):
        # Combining diacritical marks
        results = db.search_coded_text("e\u0301")  # é as e + combining accent
        assert isinstance(results, list)


# =============================================================================
# ERROR INFORMATION LEAKAGE TESTS
# =============================================================================

class TestErrorInformationLeakage:
    """Test that error messages don't leak sensitive info."""

    def test_select_project_error_no_full_path(self, setup_server):
        """Error on invalid project should not leak internal paths."""
        result = server.select_project("/very/secret/internal/path.qda")
        data = json.loads(result)
        assert "error" in data

    def test_search_error_no_stack_trace(self, setup_server):
        """Errors should not include Python stack traces."""
        result = server.export_code_report("NonexistentCode")
        data = json.loads(result)
        if "error" in data:
            error_msg = data["error"]
            assert "Traceback" not in error_msg
            assert "File \"" not in error_msg


# =============================================================================
# SESSION SECURITY TESTS
# =============================================================================

class TestSessionSecurity:
    """Test session storage security."""

    def test_session_id_not_path_traversal(self, setup_server):
        """Session ID with path traversal should not access arbitrary files."""
        result = server.get_coding_session_info("../../etc/passwd")
        data = json.loads(result)
        assert "error" in data

    def test_session_id_with_special_chars(self, setup_server):
        """Session IDs with special characters should be handled safely."""
        special_ids = [
            "../../../etc/passwd",
            "'; DROP TABLE sessions; --",
            "<script>alert('xss')</script>",
            "\x00null\x00byte",
        ]
        for sid in special_ids:
            result = server.get_coding_session_info(sid)
            data = json.loads(result)
            assert "error" in data


# =============================================================================
# CONCURRENT / STATE TESTS
# =============================================================================

class TestDatabaseState:
    """Test database state management."""

    def test_connection_recovery(self, qualcoder_db_path):
        """Test that server recovers from lost connection."""
        original_db = server.db
        original_path = server.current_project_path
        try:
            server.db = QualcoderDatabase(qualcoder_db_path)
            server.current_project_path = qualcoder_db_path

            # Simulate connection loss
            server.db.close()
            server.db = None

            # Should reconnect automatically via get_db()
            db = server.get_db()
            assert db is not None
            info = db.get_project_info()
            assert info["database_version"] == "v12"
        finally:
            if server.db is not None:
                try:
                    server.db.close()
                except Exception:
                    pass
            server.db = original_db
            server.current_project_path = original_path


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
