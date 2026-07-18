"""Tests for session management module."""

import pytest
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from qualcoder_mcp.sessions import (
    CodingSuggestion,
    AICodingSession,
    SessionManager
)


class TestCodingSuggestion:
    """Tests for CodingSuggestion class."""

    def test_constructor_with_defaults(self, sample_suggestion_data):
        """Test creating a suggestion with default values."""
        suggestion = CodingSuggestion(
            file_id=sample_suggestion_data["file_id"],
            file_name=sample_suggestion_data["file_name"],
            code_id=sample_suggestion_data["code_id"],
            code_name=sample_suggestion_data["code_name"],
            start_pos=sample_suggestion_data["start_pos"],
            end_pos=sample_suggestion_data["end_pos"],
            segment_text=sample_suggestion_data["segment_text"]
        )

        assert suggestion.file_id == sample_suggestion_data["file_id"]
        assert suggestion.file_name == sample_suggestion_data["file_name"]
        assert suggestion.code_id == sample_suggestion_data["code_id"]
        assert suggestion.code_name == sample_suggestion_data["code_name"]
        assert suggestion.start_pos == sample_suggestion_data["start_pos"]
        assert suggestion.end_pos == sample_suggestion_data["end_pos"]
        assert suggestion.segment_text == sample_suggestion_data["segment_text"]
        assert suggestion.ai_memo == ""
        assert suggestion.confidence == 0.0
        assert suggestion.status == "pending"
        assert suggestion.guid is not None
        assert isinstance(suggestion.guid, str)

    def test_constructor_with_all_parameters(self, sample_suggestion_data):
        """Test creating a suggestion with all parameters."""
        guid = str(uuid.uuid4())
        suggestion = CodingSuggestion(
            file_id=sample_suggestion_data["file_id"],
            file_name=sample_suggestion_data["file_name"],
            code_id=sample_suggestion_data["code_id"],
            code_name=sample_suggestion_data["code_name"],
            start_pos=sample_suggestion_data["start_pos"],
            end_pos=sample_suggestion_data["end_pos"],
            segment_text=sample_suggestion_data["segment_text"],
            reasoning=sample_suggestion_data["reasoning"],
            confidence=sample_suggestion_data["confidence"],
            status=sample_suggestion_data["status"],
            guid=guid
        )

        assert suggestion.reasoning == sample_suggestion_data["reasoning"]
        assert suggestion.ai_memo == sample_suggestion_data["reasoning"]  # Test backwards compatibility
        assert suggestion.confidence == sample_suggestion_data["confidence"]
        assert suggestion.status == sample_suggestion_data["status"]
        assert suggestion.guid == guid

    def test_to_dict(self, sample_suggestion_data):
        """Test serialization to dictionary."""
        suggestion = CodingSuggestion(**sample_suggestion_data)
        data = suggestion.to_dict()

        assert data["file_id"] == sample_suggestion_data["file_id"]
        assert data["file_name"] == sample_suggestion_data["file_name"]
        assert data["code_id"] == sample_suggestion_data["code_id"]
        assert data["code_name"] == sample_suggestion_data["code_name"]
        assert data["start_pos"] == sample_suggestion_data["start_pos"]
        assert data["end_pos"] == sample_suggestion_data["end_pos"]
        assert data["segment_text"] == sample_suggestion_data["segment_text"]
        assert data["reasoning"] == sample_suggestion_data["reasoning"]
        assert data["confidence"] == sample_suggestion_data["confidence"]
        assert data["status"] == sample_suggestion_data["status"]
        assert "guid" in data

    def test_from_dict(self, sample_suggestion_data):
        """Test deserialization from dictionary."""
        guid = str(uuid.uuid4())
        data = {**sample_suggestion_data, "guid": guid}
        suggestion = CodingSuggestion.from_dict(data)

        assert suggestion.file_id == sample_suggestion_data["file_id"]
        assert suggestion.code_name == sample_suggestion_data["code_name"]
        assert suggestion.confidence == sample_suggestion_data["confidence"]
        assert suggestion.guid == guid

    def test_round_trip_serialization(self, sample_suggestion_data):
        """Test that to_dict() and from_dict() preserve all data."""
        original = CodingSuggestion(**sample_suggestion_data)
        data = original.to_dict()
        restored = CodingSuggestion.from_dict(data)

        assert restored.file_id == original.file_id
        assert restored.file_name == original.file_name
        assert restored.code_id == original.code_id
        assert restored.code_name == original.code_name
        assert restored.start_pos == original.start_pos
        assert restored.end_pos == original.end_pos
        assert restored.segment_text == original.segment_text
        assert restored.ai_memo == original.ai_memo
        assert restored.confidence == original.confidence
        assert restored.status == original.status
        assert restored.guid == original.guid

    def test_from_dict_with_missing_optional_fields(self):
        """Test deserialization with missing optional fields."""
        minimal_data = {
            "file_id": 1,
            "file_name": "test.txt",
            "code_id": 5,
            "code_name": "Test Code",
            "start_pos": 0,
            "end_pos": 10,
            "segment_text": "test text"
        }
        suggestion = CodingSuggestion.from_dict(minimal_data)

        assert suggestion.ai_memo == ""
        assert suggestion.confidence == 0.0
        assert suggestion.status == "pending"
        assert suggestion.guid is not None


class TestAICodingSession:
    """Tests for AICodingSession class."""

    def test_constructor_with_defaults(self):
        """Test creating a session with minimal parameters."""
        project_path = "/test/project.qda"
        session = AICodingSession(project_path=project_path)

        assert session.project_path == project_path
        assert session.session_id is not None
        assert isinstance(session.session_id, str)
        assert session.description == ""
        assert session.file_ids == []
        assert session.code_names == []
        assert session.instruction == ""
        assert session.min_confidence == 0.6
        assert session.suggestions == []
        assert session.created_at is not None
        assert session.last_modified == session.created_at

    def test_constructor_with_all_parameters(self, sample_session_data):
        """Test creating a session with all parameters."""
        session_id = str(uuid.uuid4())
        session = AICodingSession(
            **sample_session_data,
            session_id=session_id
        )

        assert session.session_id == session_id
        assert session.project_path == sample_session_data["project_path"]
        assert session.description == sample_session_data["description"]
        assert session.file_ids == sample_session_data["file_ids"]
        assert session.code_names == sample_session_data["code_names"]
        assert session.instruction == sample_session_data["instruction"]
        assert session.min_confidence == sample_session_data["min_confidence"]

    def test_add_suggestion(self, sample_session_data, sample_suggestion_data):
        """Test adding suggestions to a session."""
        session = AICodingSession(**sample_session_data)
        original_modified = session.last_modified

        suggestion = CodingSuggestion(**sample_suggestion_data)
        session.add_suggestion(suggestion)

        assert len(session.suggestions) == 1
        assert session.suggestions[0] == suggestion
        # last_modified is refreshed on mutation. Assert monotonicity, not
        # strict inequality: Windows' coarse clock (~1-16 ms) can produce an
        # identical microsecond timestamp for two rapid ops.
        assert session.last_modified >= original_modified

    def test_get_suggestions_by_file(self, sample_session_data, sample_suggestion_data):
        """Test filtering suggestions by file ID."""
        session = AICodingSession(**sample_session_data)

        # Add suggestions for different files
        suggestion1 = CodingSuggestion(**sample_suggestion_data)
        suggestion2 = CodingSuggestion(
            **{**sample_suggestion_data, "file_id": 2, "file_name": "interview_02.txt"}
        )
        suggestion3 = CodingSuggestion(**sample_suggestion_data)  # Same file as suggestion1

        session.add_suggestion(suggestion1)
        session.add_suggestion(suggestion2)
        session.add_suggestion(suggestion3)

        file1_suggestions = session.get_suggestions_by_file(1)
        file2_suggestions = session.get_suggestions_by_file(2)

        assert len(file1_suggestions) == 2
        assert len(file2_suggestions) == 1
        assert file2_suggestions[0] == suggestion2

    def test_get_suggestions_by_code(self, sample_session_data, sample_suggestion_data):
        """Test filtering suggestions by code ID."""
        session = AICodingSession(**sample_session_data)

        # Add suggestions for different codes
        suggestion1 = CodingSuggestion(**sample_suggestion_data)
        suggestion2 = CodingSuggestion(
            **{**sample_suggestion_data, "code_id": 20, "code_name": "Different Code"}
        )
        suggestion3 = CodingSuggestion(**sample_suggestion_data)

        session.add_suggestion(suggestion1)
        session.add_suggestion(suggestion2)
        session.add_suggestion(suggestion3)

        code10_suggestions = session.get_suggestions_by_code(10)
        code20_suggestions = session.get_suggestions_by_code(20)

        assert len(code10_suggestions) == 2
        assert len(code20_suggestions) == 1

    def test_filter_by_status(self, sample_session_data, sample_suggestion_data):
        """Test filtering suggestions by status."""
        session = AICodingSession(**sample_session_data)

        # Add suggestions with different statuses
        pending = CodingSuggestion(**{**sample_suggestion_data, "status": "pending"})
        approved = CodingSuggestion(**{**sample_suggestion_data, "status": "approved"})
        rejected = CodingSuggestion(**{**sample_suggestion_data, "status": "rejected"})

        session.add_suggestion(pending)
        session.add_suggestion(approved)
        session.add_suggestion(rejected)

        assert len(session.filter_by_status("pending")) == 1
        assert len(session.filter_by_status("approved")) == 1
        assert len(session.filter_by_status("rejected")) == 1

    def test_get_statistics(self, sample_session_data, sample_suggestion_data):
        """Test session statistics calculation."""
        session = AICodingSession(**sample_session_data)

        # Add various suggestions
        session.add_suggestion(CodingSuggestion(**{**sample_suggestion_data, "status": "pending"}))
        session.add_suggestion(CodingSuggestion(**{**sample_suggestion_data, "status": "approved"}))
        session.add_suggestion(CodingSuggestion(**{**sample_suggestion_data, "status": "approved"}))
        session.add_suggestion(CodingSuggestion(**{**sample_suggestion_data, "status": "rejected"}))
        session.add_suggestion(CodingSuggestion(
            **{**sample_suggestion_data, "file_id": 2, "file_name": "interview_02.txt", "status": "pending"}
        ))
        session.add_suggestion(CodingSuggestion(
            **{**sample_suggestion_data, "code_id": 20, "code_name": "Different Code"}
        ))

        stats = session.get_statistics()

        assert stats["total_suggestions"] == 6
        assert stats["approved"] == 2
        assert stats["rejected"] == 1
        assert stats["pending"] == 3

        # Check by_file counts
        assert stats["by_file"]["interview_01.txt"] == 5
        assert stats["by_file"]["interview_02.txt"] == 1

        # Check by_code counts
        assert stats["by_code"]["Workplace Stress"] == 5
        assert stats["by_code"]["Different Code"] == 1

    def test_update_suggestion_status(self, sample_session_data, sample_suggestion_data):
        """Test updating suggestion status by index."""
        session = AICodingSession(**sample_session_data)
        suggestion = CodingSuggestion(**sample_suggestion_data)
        session.add_suggestion(suggestion)

        original_modified = session.last_modified

        # Valid update
        result = session.update_suggestion_status(0, "approved")
        assert result is True
        # the meaningful effect (status change) is the real assertion;
        # last_modified is monotonic, not strictly greater (coarse clock)
        assert session.suggestions[0].status == "approved"
        assert session.last_modified >= original_modified

        # Invalid index (out of range)
        result = session.update_suggestion_status(10, "rejected")
        assert result is False

        # Negative index not supported (by design for safety)
        result = session.update_suggestion_status(-1, "rejected")
        assert result is False

        # But the status should still be approved from earlier
        assert session.suggestions[0].status == "approved"

    def test_to_dict(self, sample_session_data, sample_suggestion_data):
        """Test session serialization to dictionary."""
        session = AICodingSession(**sample_session_data)
        session.add_suggestion(CodingSuggestion(**sample_suggestion_data))

        data = session.to_dict()

        assert data["session_id"] == session.session_id
        assert data["project_path"] == sample_session_data["project_path"]
        assert data["description"] == sample_session_data["description"]
        assert data["file_ids"] == sample_session_data["file_ids"]
        assert data["code_names"] == sample_session_data["code_names"]
        assert data["instruction"] == sample_session_data["instruction"]
        assert data["min_confidence"] == sample_session_data["min_confidence"]
        assert len(data["suggestions"]) == 1
        assert "statistics" in data

    def test_from_dict(self, sample_session_data, sample_suggestion_data):
        """Test session deserialization from dictionary."""
        session = AICodingSession(**sample_session_data)
        session.add_suggestion(CodingSuggestion(**sample_suggestion_data))

        data = session.to_dict()
        restored = AICodingSession.from_dict(data)

        assert restored.session_id == session.session_id
        assert restored.project_path == session.project_path
        assert restored.created_at == session.created_at
        assert restored.last_modified == session.last_modified
        assert len(restored.suggestions) == 1

    def test_round_trip_serialization(self, sample_session_data, sample_suggestion_data):
        """Test that to_dict() and from_dict() preserve all session data."""
        session = AICodingSession(**sample_session_data)
        session.add_suggestion(CodingSuggestion(**sample_suggestion_data))
        session.add_suggestion(CodingSuggestion(
            **{**sample_suggestion_data, "status": "approved"}
        ))

        data = session.to_dict()
        restored = AICodingSession.from_dict(data)

        assert restored.session_id == session.session_id
        assert restored.project_path == session.project_path
        assert restored.description == session.description
        assert restored.file_ids == session.file_ids
        assert restored.code_names == session.code_names
        assert restored.instruction == session.instruction
        assert restored.min_confidence == session.min_confidence
        assert len(restored.suggestions) == len(session.suggestions)

        # Check suggestions are preserved
        for orig, rest in zip(session.suggestions, restored.suggestions):
            assert rest.file_id == orig.file_id
            assert rest.code_id == orig.code_id
            assert rest.status == orig.status
            assert rest.guid == orig.guid


class TestSessionManager:
    """Tests for SessionManager class."""

    def test_constructor_creates_directory(self, temp_session_dir):
        """Test that SessionManager creates storage directory."""
        storage_path = Path(temp_session_dir) / "new_dir"
        assert not storage_path.exists()

        manager = SessionManager(str(storage_path))

        assert storage_path.exists()
        assert storage_path.is_dir()
        assert manager.storage_dir == storage_path

    def test_save_session(self, temp_session_dir, sample_session_data):
        """Test saving a session to disk."""
        manager = SessionManager(temp_session_dir)
        session = AICodingSession(**sample_session_data)

        manager.save_session(session)

        # Check file exists
        filepath = Path(temp_session_dir) / f"session_{session.session_id}.json"
        assert filepath.exists()

        # Check file content
        with open(filepath, 'r', encoding="utf-8") as f:
            data = json.load(f)
        assert data["session_id"] == session.session_id
        assert data["project_path"] == sample_session_data["project_path"]

    def test_load_session(self, temp_session_dir, sample_session_data, sample_suggestion_data):
        """Test loading a session from disk."""
        manager = SessionManager(temp_session_dir)
        session = AICodingSession(**sample_session_data)
        session.add_suggestion(CodingSuggestion(**sample_suggestion_data))

        manager.save_session(session)
        loaded = manager.load_session(session.session_id)

        assert loaded.session_id == session.session_id
        assert loaded.project_path == session.project_path
        assert len(loaded.suggestions) == 1
        assert loaded.suggestions[0].file_id == sample_suggestion_data["file_id"]

    def test_load_nonexistent_session(self, temp_session_dir):
        """Test loading a session that doesn't exist."""
        manager = SessionManager(temp_session_dir)

        with pytest.raises(FileNotFoundError):
            # Use valid UUID4 format that doesn't correspond to any saved session
            manager.load_session("00000000-0000-0000-0000-000000000000")

    def test_load_session_invalid_id_format(self, temp_session_dir):
        """Test loading with invalid session ID format raises ValueError."""
        manager = SessionManager(temp_session_dir)

        with pytest.raises(ValueError, match="Invalid session ID format"):
            manager.load_session("nonexistent_id")

    def test_session_exists(self, temp_session_dir, sample_session_data):
        """Test checking if a session exists."""
        manager = SessionManager(temp_session_dir)
        session = AICodingSession(**sample_session_data)

        assert manager.session_exists(session.session_id) is False

        manager.save_session(session)

        assert manager.session_exists(session.session_id) is True

    def test_list_sessions_empty(self, temp_session_dir):
        """Test listing sessions when none exist."""
        manager = SessionManager(temp_session_dir)
        sessions = manager.list_sessions()

        assert sessions == []

    def test_list_sessions(self, temp_session_dir, sample_session_data):
        """Test listing all sessions."""
        manager = SessionManager(temp_session_dir)

        # Create multiple sessions
        session1 = AICodingSession(**sample_session_data)
        session2 = AICodingSession(**{**sample_session_data, "description": "Second session"})

        manager.save_session(session1)
        manager.save_session(session2)

        sessions = manager.list_sessions()

        assert len(sessions) == 2
        assert any(s["session_id"] == session1.session_id for s in sessions)
        assert any(s["session_id"] == session2.session_id for s in sessions)

    def test_list_sessions_filtered_by_project(self, temp_session_dir):
        """Test listing sessions filtered by project path."""
        manager = SessionManager(temp_session_dir)

        session1 = AICodingSession(project_path="/project1.qda")
        session2 = AICodingSession(project_path="/project2.qda")

        manager.save_session(session1)
        manager.save_session(session2)

        filtered = manager.list_sessions(project_path="/project1.qda")

        assert len(filtered) == 1
        assert filtered[0]["session_id"] == session1.session_id

    def test_list_sessions_filtered_by_age(self, temp_session_dir, sample_session_data):
        """Test listing sessions filtered by age."""
        manager = SessionManager(temp_session_dir)

        # Create old session
        old_session = AICodingSession(**sample_session_data)
        old_date = (datetime.now() - timedelta(days=40)).isoformat()
        old_session.last_modified = old_date
        manager.save_session(old_session)

        # Create recent session
        recent_session = AICodingSession(**{**sample_session_data, "description": "Recent"})
        manager.save_session(recent_session)

        # List sessions from last 30 days
        sessions = manager.list_sessions(days_old=30)

        assert len(sessions) == 1
        assert sessions[0]["session_id"] == recent_session.session_id

    def test_list_sessions_includes_statistics(self, temp_session_dir, sample_session_data, sample_suggestion_data):
        """Test that list_sessions includes suggestion counts."""
        manager = SessionManager(temp_session_dir)
        session = AICodingSession(**sample_session_data)
        session.add_suggestion(CodingSuggestion(**{**sample_suggestion_data, "status": "pending"}))
        session.add_suggestion(CodingSuggestion(**{**sample_suggestion_data, "status": "approved"}))
        session.add_suggestion(CodingSuggestion(**{**sample_suggestion_data, "status": "rejected"}))

        manager.save_session(session)
        sessions = manager.list_sessions()

        assert len(sessions) == 1
        assert sessions[0]["suggestion_count"] == 3
        assert sessions[0]["approved_count"] == 1
        assert sessions[0]["rejected_count"] == 1
        assert sessions[0]["pending_count"] == 1

    def test_list_sessions_sorted_by_modified(self, temp_session_dir, sample_session_data):
        """Test that sessions are sorted by last modified (most recent first)."""
        manager = SessionManager(temp_session_dir)

        # Create sessions with different modification times
        old_session = AICodingSession(**sample_session_data)
        old_session.last_modified = (datetime.now() - timedelta(days=5)).isoformat()
        manager.save_session(old_session)

        recent_session = AICodingSession(**{**sample_session_data, "description": "Recent"})
        manager.save_session(recent_session)

        sessions = manager.list_sessions()

        assert len(sessions) == 2
        assert sessions[0]["session_id"] == recent_session.session_id  # Most recent first
        assert sessions[1]["session_id"] == old_session.session_id

    def test_delete_session(self, temp_session_dir, sample_session_data):
        """Test deleting a session."""
        manager = SessionManager(temp_session_dir)
        session = AICodingSession(**sample_session_data)

        manager.save_session(session)
        assert manager.session_exists(session.session_id)

        result = manager.delete_session(session.session_id)

        assert result is True
        assert not manager.session_exists(session.session_id)

    def test_delete_nonexistent_session(self, temp_session_dir):
        """Test deleting a session that doesn't exist."""
        manager = SessionManager(temp_session_dir)

        result = manager.delete_session("nonexistent_id")

        assert result is False

    def test_cleanup_old_sessions(self, temp_session_dir, sample_session_data):
        """Test cleaning up old sessions."""
        manager = SessionManager(temp_session_dir)

        # Create old sessions
        for i in range(3):
            old_session = AICodingSession(**{**sample_session_data, "description": f"Old {i}"})
            old_date = (datetime.now() - timedelta(days=40)).isoformat()
            old_session.last_modified = old_date
            manager.save_session(old_session)

        # Create recent sessions
        for i in range(2):
            recent_session = AICodingSession(**{**sample_session_data, "description": f"Recent {i}"})
            manager.save_session(recent_session)

        # Cleanup sessions older than 30 days
        deleted = manager.cleanup_old_sessions(days_old=30)

        assert deleted == 3
        sessions = manager.list_sessions(days_old=365)  # Get all sessions
        assert len(sessions) == 2

    def test_save_load_round_trip(self, temp_session_dir, sample_session_data, sample_suggestion_data):
        """Test complete save/load cycle preserves all data."""
        manager = SessionManager(temp_session_dir)

        # Create complex session
        session = AICodingSession(**sample_session_data)
        session.add_suggestion(CodingSuggestion(**sample_suggestion_data))
        session.add_suggestion(CodingSuggestion(**{**sample_suggestion_data, "status": "approved"}))
        session.update_suggestion_status(0, "rejected")

        manager.save_session(session)
        loaded = manager.load_session(session.session_id)

        # Verify everything is preserved
        assert loaded.session_id == session.session_id
        assert loaded.project_path == session.project_path
        assert loaded.description == session.description
        assert loaded.file_ids == session.file_ids
        assert loaded.code_names == session.code_names
        assert loaded.instruction == session.instruction
        assert loaded.min_confidence == session.min_confidence
        assert loaded.created_at == session.created_at
        assert loaded.last_modified == session.last_modified
        assert len(loaded.suggestions) == 2
        assert loaded.suggestions[0].status == "rejected"
        assert loaded.suggestions[1].status == "approved"

    def test_invalid_json_handling(self, temp_session_dir):
        """Test handling of corrupted session files."""
        manager = SessionManager(temp_session_dir)

        # Create invalid JSON file
        invalid_file = Path(temp_session_dir) / "session_invalid.json"
        with open(invalid_file, 'w', encoding="utf-8") as f:
            f.write("{ invalid json ]")

        # list_sessions should skip invalid files without crashing
        sessions = manager.list_sessions()
        assert sessions == []

        # cleanup should skip invalid files
        deleted = manager.cleanup_old_sessions(days_old=0)
        assert deleted == 0
