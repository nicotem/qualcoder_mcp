"""Tests for database.py GUID generation methods."""

import pytest
import uuid
from pathlib import Path

from qualcoder_mcp.database import QualcoderDatabase


# Path to the test project created earlier
TEST_PROJECT_PATH = Path.home() / "Documents" / "QDA Projects" / "test_project.qda"


@pytest.fixture
def test_db():
    """Create database connection to test project."""
    if not TEST_PROJECT_PATH.exists():
        pytest.skip(f"Test project not found at {TEST_PROJECT_PATH}")

    db = QualcoderDatabase(str(TEST_PROJECT_PATH))
    yield db
    db.close()


class TestDeterministicGuidGeneration:
    """Tests for generate_deterministic_guid method."""

    def test_generate_deterministic_guid_returns_valid_uuid(self, test_db):
        """Test that generated GUIDs are valid UUIDs."""
        guid = test_db.generate_deterministic_guid("code", 1)

        # Should be a valid UUID string
        assert isinstance(guid, str)
        # Should be parseable as UUID
        parsed = uuid.UUID(guid)
        assert isinstance(parsed, uuid.UUID)

    def test_generate_deterministic_guid_is_deterministic(self, test_db):
        """Test that same input always produces same GUID."""
        guid1 = test_db.generate_deterministic_guid("code", 1)
        guid2 = test_db.generate_deterministic_guid("code", 1)
        guid3 = test_db.generate_deterministic_guid("code", 1)

        assert guid1 == guid2
        assert guid2 == guid3

    def test_generate_deterministic_guid_differs_by_entity_type(self, test_db):
        """Test that different entity types produce different GUIDs."""
        code_guid = test_db.generate_deterministic_guid("code", 1)
        file_guid = test_db.generate_deterministic_guid("file", 1)
        case_guid = test_db.generate_deterministic_guid("case", 1)
        user_guid = test_db.generate_deterministic_guid("user", 1)

        # All should be different
        guids = [code_guid, file_guid, case_guid, user_guid]
        assert len(set(guids)) == 4

    def test_generate_deterministic_guid_differs_by_entity_id(self, test_db):
        """Test that different entity IDs produce different GUIDs."""
        guid1 = test_db.generate_deterministic_guid("code", 1)
        guid2 = test_db.generate_deterministic_guid("code", 2)
        guid3 = test_db.generate_deterministic_guid("code", 3)

        assert guid1 != guid2
        assert guid2 != guid3
        assert guid1 != guid3

    def test_generate_deterministic_guid_with_string_id(self, test_db):
        """Test GUID generation with string entity ID."""
        guid1 = test_db.generate_deterministic_guid("user", "alice")
        guid2 = test_db.generate_deterministic_guid("user", "bob")

        # Should be valid UUIDs
        uuid.UUID(guid1)
        uuid.UUID(guid2)

        # Should be different
        assert guid1 != guid2

        # Should be deterministic
        assert guid1 == test_db.generate_deterministic_guid("user", "alice")

    def test_generate_deterministic_guid_with_composite_id(self, test_db):
        """Test GUID generation with composite entity ID."""
        # This is used for coding entities
        guid = test_db.generate_deterministic_guid("coding", "file_1_code_2_pos_100")

        # Should be valid UUID
        uuid.UUID(guid)

        # Should be deterministic
        assert guid == test_db.generate_deterministic_guid("coding", "file_1_code_2_pos_100")

    def test_generate_deterministic_guid_project_specific(self):
        """Test that GUIDs are different for different projects."""
        # Create two database instances with different paths (even if they don't exist)
        # The GUID should be based on the path itself
        db1 = QualcoderDatabase.__new__(QualcoderDatabase)
        db1.db_path = Path("/fake/path1/project.qda")

        db2 = QualcoderDatabase.__new__(QualcoderDatabase)
        db2.db_path = Path("/fake/path2/project.qda")

        guid1 = db1.generate_deterministic_guid("code", 1)
        guid2 = db2.generate_deterministic_guid("code", 1)

        # Same entity type and ID, but different projects -> different GUIDs
        assert guid1 != guid2

    def test_generate_deterministic_guid_uses_uuid_v5(self, test_db):
        """Test that generated GUIDs use UUID v5 (version field = 5)."""
        guid = test_db.generate_deterministic_guid("code", 1)
        parsed = uuid.UUID(guid)

        # UUID v5 has version field = 5
        assert parsed.version == 5

    def test_generate_deterministic_guid_special_characters(self, test_db):
        """Test GUID generation with special characters in ID."""
        guid = test_db.generate_deterministic_guid("user", "user@example.com")

        # Should be valid UUID
        uuid.UUID(guid)

        # Should be deterministic
        assert guid == test_db.generate_deterministic_guid("user", "user@example.com")


class TestGetCodeGuids:
    """Tests for get_code_guids method."""

    def test_get_code_guids_returns_dict(self, test_db):
        """Test that get_code_guids returns a dictionary."""
        guids = test_db.get_code_guids()
        assert isinstance(guids, dict)

    def test_get_code_guids_contains_all_codes(self, test_db):
        """Test that all codes have GUIDs."""
        codes = test_db.list_codes()
        guids = test_db.get_code_guids()

        # Should have GUID for every code
        assert len(guids) == len(codes)

        # Check all code IDs are present
        code_ids = {c["id"] for c in codes}
        guid_ids = set(guids.keys())
        assert code_ids == guid_ids

    def test_get_code_guids_all_valid_uuids(self, test_db):
        """Test that all returned GUIDs are valid UUIDs."""
        guids = test_db.get_code_guids()

        for code_id, guid in guids.items():
            # Should be parseable as UUID
            parsed = uuid.UUID(guid)
            assert isinstance(parsed, uuid.UUID)

    def test_get_code_guids_is_deterministic(self, test_db):
        """Test that get_code_guids returns same GUIDs on multiple calls."""
        guids1 = test_db.get_code_guids()
        guids2 = test_db.get_code_guids()

        assert guids1 == guids2

    def test_get_code_guids_unique_per_code(self, test_db):
        """Test that each code has a unique GUID."""
        guids = test_db.get_code_guids()

        # All GUIDs should be unique
        guid_values = list(guids.values())
        assert len(guid_values) == len(set(guid_values))


class TestGetFileGuids:
    """Tests for get_file_guids method."""

    def test_get_file_guids_returns_dict(self, test_db):
        """Test that get_file_guids returns a dictionary."""
        guids = test_db.get_file_guids()
        assert isinstance(guids, dict)

    def test_get_file_guids_contains_all_files(self, test_db):
        """Test that all files have GUIDs."""
        files = test_db.list_files()
        guids = test_db.get_file_guids()

        # Should have GUID for every file
        assert len(guids) == len(files)

        # Check all file IDs are present
        file_ids = {f["id"] for f in files}
        guid_ids = set(guids.keys())
        assert file_ids == guid_ids

    def test_get_file_guids_all_valid_uuids(self, test_db):
        """Test that all returned GUIDs are valid UUIDs."""
        guids = test_db.get_file_guids()

        for file_id, guid in guids.items():
            parsed = uuid.UUID(guid)
            assert isinstance(parsed, uuid.UUID)

    def test_get_file_guids_is_deterministic(self, test_db):
        """Test that get_file_guids returns same GUIDs on multiple calls."""
        guids1 = test_db.get_file_guids()
        guids2 = test_db.get_file_guids()

        assert guids1 == guids2

    def test_get_file_guids_unique_per_file(self, test_db):
        """Test that each file has a unique GUID."""
        guids = test_db.get_file_guids()

        # All GUIDs should be unique
        guid_values = list(guids.values())
        assert len(guid_values) == len(set(guid_values))

    def test_get_file_guids_different_from_code_guids(self, test_db):
        """Test that file GUIDs are different from code GUIDs."""
        file_guids = test_db.get_file_guids()
        code_guids = test_db.get_code_guids()

        # No overlap between file and code GUIDs
        file_guid_set = set(file_guids.values())
        code_guid_set = set(code_guids.values())
        assert file_guid_set.isdisjoint(code_guid_set)


class TestGetCaseGuids:
    """Tests for get_case_guids method."""

    def test_get_case_guids_returns_dict(self, test_db):
        """Test that get_case_guids returns a dictionary."""
        guids = test_db.get_case_guids()
        assert isinstance(guids, dict)

    def test_get_case_guids_contains_all_cases(self, test_db):
        """Test that all cases have GUIDs."""
        guids = test_db.get_case_guids()

        # Query cases directly from database to avoid list_cases() issues
        cursor = test_db.conn.execute("SELECT caseid FROM cases")
        case_ids = {row["caseid"] for row in cursor.fetchall()}

        # Should have GUID for every case
        assert len(guids) == len(case_ids)

        # Check all case IDs are present
        guid_ids = set(guids.keys())
        assert case_ids == guid_ids

    def test_get_case_guids_all_valid_uuids(self, test_db):
        """Test that all returned GUIDs are valid UUIDs."""
        guids = test_db.get_case_guids()

        for case_id, guid in guids.items():
            parsed = uuid.UUID(guid)
            assert isinstance(parsed, uuid.UUID)

    def test_get_case_guids_is_deterministic(self, test_db):
        """Test that get_case_guids returns same GUIDs on multiple calls."""
        guids1 = test_db.get_case_guids()
        guids2 = test_db.get_case_guids()

        assert guids1 == guids2

    def test_get_case_guids_unique_per_case(self, test_db):
        """Test that each case has a unique GUID."""
        guids = test_db.get_case_guids()

        # All GUIDs should be unique
        guid_values = list(guids.values())
        assert len(guid_values) == len(set(guid_values))


class TestGetOrCreateUserGuid:
    """Tests for get_or_create_user_guid method."""

    def test_get_or_create_user_guid_returns_valid_uuid(self, test_db):
        """Test that user GUID is a valid UUID."""
        guid = test_db.get_or_create_user_guid("test_user")

        # Should be valid UUID
        parsed = uuid.UUID(guid)
        assert isinstance(parsed, uuid.UUID)

    def test_get_or_create_user_guid_is_deterministic(self, test_db):
        """Test that same username always produces same GUID."""
        guid1 = test_db.get_or_create_user_guid("alice")
        guid2 = test_db.get_or_create_user_guid("alice")
        guid3 = test_db.get_or_create_user_guid("alice")

        assert guid1 == guid2
        assert guid2 == guid3

    def test_get_or_create_user_guid_differs_by_username(self, test_db):
        """Test that different usernames produce different GUIDs."""
        guid1 = test_db.get_or_create_user_guid("alice")
        guid2 = test_db.get_or_create_user_guid("bob")
        guid3 = test_db.get_or_create_user_guid("charlie")

        assert guid1 != guid2
        assert guid2 != guid3
        assert guid1 != guid3

    def test_get_or_create_user_guid_special_username(self, test_db):
        """Test with special username (like ai_coder)."""
        guid = test_db.get_or_create_user_guid("ai_coder")

        # Should be valid UUID
        uuid.UUID(guid)

        # Should be deterministic
        assert guid == test_db.get_or_create_user_guid("ai_coder")


class TestGuidConsistencyAcrossMethods:
    """Tests for consistency of GUID generation across different methods."""

    def test_code_guid_consistency(self, test_db):
        """Test that get_code_guids uses same generation as generate_deterministic_guid."""
        code_guids = test_db.get_code_guids()

        for code_id, guid in code_guids.items():
            # Direct generation should match
            expected = test_db.generate_deterministic_guid("code", code_id)
            assert guid == expected

    def test_file_guid_consistency(self, test_db):
        """Test that get_file_guids uses same generation as generate_deterministic_guid."""
        file_guids = test_db.get_file_guids()

        for file_id, guid in file_guids.items():
            # Direct generation should match
            expected = test_db.generate_deterministic_guid("file", file_id)
            assert guid == expected

    def test_case_guid_consistency(self, test_db):
        """Test that get_case_guids uses same generation as generate_deterministic_guid."""
        case_guids = test_db.get_case_guids()

        for case_id, guid in case_guids.items():
            # Direct generation should match
            expected = test_db.generate_deterministic_guid("case", case_id)
            assert guid == expected

    def test_user_guid_consistency(self, test_db):
        """Test that get_or_create_user_guid uses same generation."""
        username = "test_user"

        guid1 = test_db.get_or_create_user_guid(username)
        guid2 = test_db.generate_deterministic_guid("user", username)

        assert guid1 == guid2

    def test_all_entity_guids_unique(self, test_db):
        """Test that GUIDs across all entity types are unique."""
        all_guids = []

        # Collect all GUIDs
        all_guids.extend(test_db.get_code_guids().values())
        all_guids.extend(test_db.get_file_guids().values())
        all_guids.extend(test_db.get_case_guids().values())
        all_guids.append(test_db.get_or_create_user_guid("ai_coder"))

        # All should be unique
        assert len(all_guids) == len(set(all_guids))


class TestGuidPersistenceAcrossConnections:
    """Tests that GUIDs remain consistent across database connections."""

    def test_guid_consistency_across_connections(self):
        """Test that same entity has same GUID with different connections."""
        if not TEST_PROJECT_PATH.exists():
            pytest.skip(f"Test project not found at {TEST_PROJECT_PATH}")

        # First connection
        db1 = QualcoderDatabase(str(TEST_PROJECT_PATH))
        guid1 = db1.generate_deterministic_guid("code", 1)
        code_guids1 = db1.get_code_guids()
        db1.close()

        # Second connection
        db2 = QualcoderDatabase(str(TEST_PROJECT_PATH))
        guid2 = db2.generate_deterministic_guid("code", 1)
        code_guids2 = db2.get_code_guids()
        db2.close()

        # Should be identical
        assert guid1 == guid2
        assert code_guids1 == code_guids2
