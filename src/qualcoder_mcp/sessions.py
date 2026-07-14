"""Session management for AI coding suggestions with disk persistence."""

import json
import logging
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Any

logger = logging.getLogger(__name__)


class CodingSuggestion:
    """Data class for AI-suggested coding with conversational review support."""

    def __init__(
        self,
        file_id: int,
        file_name: str,
        code_id: int,
        code_name: str,
        start_pos: int,
        end_pos: int,
        segment_text: str,
        reasoning: str = "",
        confidence: float = 0.0,
        status: str = "pending",
        context_before: str = "",
        context_after: str = "",
        guid: Optional[str] = None
    ):
        self.file_id = file_id
        self.file_name = file_name
        self.code_id = code_id
        self.code_name = code_name
        self.start_pos = start_pos
        self.end_pos = end_pos
        self.segment_text = segment_text
        self.reasoning = reasoning  # Why this segment was coded
        # Clamp confidence to [0.0, 1.0]
        self.confidence = max(0.0, min(1.0, float(confidence)))
        self.status = status  # 'pending', 'approved', 'rejected'
        self.context_before = context_before  # Text before for context
        self.context_after = context_after  # Text after for context
        self.guid = guid or str(uuid.uuid4())

        # For backwards compatibility with old ai_memo field
        self.ai_memo = reasoning

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "file_id": self.file_id,
            "file_name": self.file_name,
            "code_id": self.code_id,
            "code_name": self.code_name,
            "start_pos": self.start_pos,
            "end_pos": self.end_pos,
            "segment_text": self.segment_text,
            "reasoning": self.reasoning,
            "confidence": self.confidence,
            "status": self.status,
            "context_before": self.context_before,
            "context_after": self.context_after,
            "guid": self.guid
        }

    _REQUIRED_FIELDS = {
        "file_id", "file_name", "code_id", "code_name",
        "start_pos", "end_pos", "segment_text"
    }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CodingSuggestion':
        """Create from dictionary.

        Raises:
            ValueError: If required fields are missing
            TypeError: If data is not a dictionary
        """
        if not isinstance(data, dict):
            raise TypeError("CodingSuggestion data must be a dictionary")

        missing = cls._REQUIRED_FIELDS - set(data.keys())
        if missing:
            raise ValueError(f"Missing required fields: {missing}")

        # Handle both old (ai_memo) and new (reasoning) formats
        reasoning = data.get("reasoning") or data.get("ai_memo", "")

        return cls(
            file_id=data["file_id"],
            file_name=data["file_name"],
            code_id=data["code_id"],
            code_name=data["code_name"],
            start_pos=data["start_pos"],
            end_pos=data["end_pos"],
            segment_text=data["segment_text"],
            reasoning=reasoning,
            confidence=data.get("confidence", 0.0),
            status=data.get("status", "pending"),
            context_before=data.get("context_before", ""),
            context_after=data.get("context_after", ""),
            guid=data.get("guid")
        )


class AICodingSession:
    """Manages a session of AI coding suggestions."""

    def __init__(
        self,
        project_path: str,
        session_id: Optional[str] = None,
        description: str = "",
        file_ids: Optional[List[int]] = None,
        code_names: Optional[List[str]] = None,
        instruction: str = "",
        min_confidence: float = 0.6
    ):
        self.session_id = session_id or str(uuid.uuid4())
        # Ensure project_path is always a string for JSON serialization
        self.project_path = str(project_path) if project_path else ""
        self.description = description
        self.file_ids = file_ids or []
        self.code_names = code_names or []
        self.instruction = instruction
        self.min_confidence = min_confidence
        self.suggestions: List[CodingSuggestion] = []
        self.created_at = datetime.now().isoformat()
        self.last_modified = self.created_at

    def add_suggestion(self, suggestion: CodingSuggestion):
        """Add a coding suggestion to the session."""
        self.suggestions.append(suggestion)
        self.last_modified = datetime.now().isoformat()

    def get_suggestions_by_file(self, file_id: int) -> List[CodingSuggestion]:
        """Get all suggestions for a specific file."""
        return [s for s in self.suggestions if s.file_id == file_id]

    def get_suggestions_by_code(self, code_id: int) -> List[CodingSuggestion]:
        """Get all suggestions for a specific code."""
        return [s for s in self.suggestions if s.code_id == code_id]

    def filter_by_status(self, status: str) -> List[CodingSuggestion]:
        """Get suggestions by status (pending/approved/rejected)."""
        return [s for s in self.suggestions if s.status == status]

    def get_statistics(self) -> Dict[str, Any]:
        """Get session statistics."""
        total = len(self.suggestions)
        approved = len([s for s in self.suggestions if s.status == "approved"])
        rejected = len([s for s in self.suggestions if s.status == "rejected"])
        pending = len([s for s in self.suggestions if s.status == "pending"])
        applied = len([s for s in self.suggestions if s.status == "applied"])

        # By file
        by_file = {}
        for s in self.suggestions:
            if s.file_name not in by_file:
                by_file[s.file_name] = 0
            by_file[s.file_name] += 1

        # By code
        by_code = {}
        for s in self.suggestions:
            if s.code_name not in by_code:
                by_code[s.code_name] = 0
            by_code[s.code_name] += 1

        return {
            "total_suggestions": total,
            "approved": approved,
            "rejected": rejected,
            "pending": pending,
            "applied": applied,
            "by_file": by_file,
            "by_code": by_code
        }

    def has_duplicate(self, file_id: int, code_id: int,
                      start_pos: int, end_pos: int) -> bool:
        """Check whether an equivalent suggestion is already in the session."""
        for s in self.suggestions:
            if (s.file_id == file_id and s.code_id == code_id
                    and s.start_pos == start_pos and s.end_pos == end_pos):
                return True
        return False

    def remove_pending_suggestions(self) -> int:
        """Remove all pending suggestions (used by record_suggestions replace).

        Approved, rejected, and applied suggestions are never removed.

        Returns:
            Number of suggestions removed
        """
        before = len(self.suggestions)
        self.suggestions = [s for s in self.suggestions if s.status != "pending"]
        removed = before - len(self.suggestions)
        if removed:
            self.last_modified = datetime.now().isoformat()
        return removed

    def mark_applied(self, guids: List[str]) -> int:
        """Mark suggestions as applied (written to the database).

        Args:
            guids: GUIDs of the suggestions that were written

        Returns:
            Number of suggestions marked
        """
        count = 0
        for guid in guids:
            sugg = self.get_suggestion_by_guid(guid)
            if sugg is not None:
                sugg.status = "applied"
                count += 1
        if count:
            self.last_modified = datetime.now().isoformat()
        return count

    def get_suggestion_by_guid(self, guid: str) -> Optional[CodingSuggestion]:
        """Get a suggestion by its GUID."""
        for s in self.suggestions:
            if s.guid == guid:
                return s
        return None

    def update_suggestion_status(self, index: int, status: str) -> bool:
        """Update the status of a suggestion by index.

        Args:
            index: Index of the suggestion in the list
            status: New status ('approved' or 'rejected')

        Returns:
            True if updated, False if index out of range
        """
        if 0 <= index < len(self.suggestions):
            self.suggestions[index].status = status
            self.last_modified = datetime.now().isoformat()
            return True
        return False

    def update_suggestions_by_guid(
        self,
        approve: Optional[List[str]] = None,
        reject: Optional[List[str]] = None
    ) -> Dict[str, int]:
        """Update multiple suggestions by GUID.

        Args:
            approve: List of GUIDs to approve
            reject: List of GUIDs to reject

        Returns:
            Dictionary with counts of approved and rejected
        """
        approved_count = 0
        rejected_count = 0

        if approve:
            for guid in approve:
                sugg = self.get_suggestion_by_guid(guid)
                if sugg:
                    sugg.status = "approved"
                    approved_count += 1

        if reject:
            for guid in reject:
                sugg = self.get_suggestion_by_guid(guid)
                if sugg:
                    sugg.status = "rejected"
                    rejected_count += 1

        if approved_count > 0 or rejected_count > 0:
            self.last_modified = datetime.now().isoformat()

        return {
            "approved": approved_count,
            "rejected": rejected_count
        }

    def to_dict(self) -> Dict[str, Any]:
        """Export session as dictionary for JSON export."""
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "last_modified": self.last_modified,
            "project_path": self.project_path,
            "description": self.description,
            "file_ids": self.file_ids,
            "code_names": self.code_names,
            "instruction": self.instruction,
            "min_confidence": self.min_confidence,
            "suggestions": [s.to_dict() for s in self.suggestions],
            "statistics": self.get_statistics()
        }

    _REQUIRED_FIELDS = {
        "project_path", "session_id", "created_at", "last_modified"
    }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AICodingSession':
        """Create session from dictionary.

        Raises:
            ValueError: If required fields are missing
            TypeError: If data is not a dictionary
        """
        if not isinstance(data, dict):
            raise TypeError("AICodingSession data must be a dictionary")

        missing = cls._REQUIRED_FIELDS - set(data.keys())
        if missing:
            raise ValueError(f"Missing required session fields: {missing}")

        session = cls(
            project_path=data["project_path"],
            session_id=data["session_id"],
            description=data.get("description", ""),
            file_ids=data.get("file_ids", []),
            code_names=data.get("code_names", []),
            instruction=data.get("instruction", ""),
            min_confidence=data.get("min_confidence", 0.6)
        )
        session.created_at = data["created_at"]
        session.last_modified = data["last_modified"]

        # Load suggestions
        for s_data in data.get("suggestions", []):
            suggestion = CodingSuggestion.from_dict(s_data)
            session.suggestions.append(suggestion)

        return session


class SessionManager:
    """Manage AI coding sessions with disk persistence."""

    # Session IDs must be valid UUID4 strings (hex + hyphens only)
    _SESSION_ID_PATTERN = re.compile(
        r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    )

    def __init__(self, storage_dir: str = "~/.qualcoder_mcp/sessions"):
        self.storage_dir = Path(storage_dir).expanduser()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"SessionManager initialized with storage: {self.storage_dir}")

    @classmethod
    def _validate_session_id(cls, session_id: str) -> str:
        """Validate session ID to prevent path traversal.

        Session IDs must be valid UUID4 format (lowercase hex with hyphens).

        Args:
            session_id: The session ID to validate

        Returns:
            The validated session ID

        Raises:
            ValueError: If session ID is not valid UUID4 format
        """
        if not isinstance(session_id, str):
            raise ValueError("session_id must be a string")
        if not cls._SESSION_ID_PATTERN.match(session_id):
            raise ValueError(
                f"Invalid session ID format: must be UUID4 "
                f"(e.g., '550e8400-e29b-41d4-a716-446655440000')"
            )
        return session_id

    def save_session(self, session: AICodingSession) -> None:
        """Save session to disk as JSON.

        Args:
            session: The AICodingSession to save

        Raises:
            ValueError: If session ID is not valid UUID4 format
        """
        self._validate_session_id(session.session_id)
        filepath = self.storage_dir / f"session_{session.session_id}.json"
        try:
            with open(filepath, 'w') as f:
                json.dump(session.to_dict(), f, indent=2)
            logger.info(f"Saved session {session.session_id} to {filepath}")
        except Exception as e:
            logger.error(f"Failed to save session {session.session_id}: {e}")
            raise

    def load_session(self, session_id: str) -> AICodingSession:
        """Load session from disk.

        Args:
            session_id: The session ID to load

        Returns:
            The loaded AICodingSession

        Raises:
            ValueError: If session ID is not valid UUID4 format
            FileNotFoundError: If session file doesn't exist
        """
        self._validate_session_id(session_id)
        filepath = self.storage_dir / f"session_{session_id}.json"
        if not filepath.exists():
            raise FileNotFoundError(f"Session {session_id} not found at {filepath}")

        try:
            with open(filepath, 'r') as f:
                data = json.load(f)
            session = AICodingSession.from_dict(data)
            logger.info(f"Loaded session {session_id} from {filepath}")
            return session
        except Exception as e:
            logger.error(f"Failed to load session {session_id}: {e}")
            raise

    def session_exists(self, session_id: str) -> bool:
        """Check if a session file exists.

        Args:
            session_id: The session ID to check

        Returns:
            True if session file exists, False otherwise.
            Returns False for invalid session ID formats.
        """
        try:
            self._validate_session_id(session_id)
        except ValueError:
            return False
        filepath = self.storage_dir / f"session_{session_id}.json"
        return filepath.exists()

    def list_sessions(
        self,
        project_path: Optional[str] = None,
        days_old: int = 30
    ) -> List[Dict[str, Any]]:
        """List all sessions, optionally filtered by project and age.

        Args:
            project_path: Filter by specific project path (optional)
            days_old: Only show sessions from last N days (default: 30)

        Returns:
            List of session metadata dictionaries
        """
        sessions = []
        cutoff_date = datetime.now() - timedelta(days=days_old)

        try:
            for filepath in self.storage_dir.glob("session_*.json"):
                try:
                    with open(filepath, 'r') as f:
                        data = json.load(f)

                    # Filter by project if specified
                    if project_path and data['project_path'] != project_path:
                        continue

                    # Filter by age
                    last_modified = datetime.fromisoformat(data['last_modified'])
                    if last_modified < cutoff_date:
                        continue

                    # Get project name
                    project_name = Path(data['project_path']).stem

                    sessions.append({
                        'session_id': data['session_id'],
                        'created_at': data['created_at'],
                        'last_modified': data['last_modified'],
                        'description': data.get('description', ''),
                        'suggestion_count': data['statistics']['total_suggestions'],
                        'approved_count': data['statistics']['approved'],
                        'rejected_count': data['statistics']['rejected'],
                        'pending_count': data['statistics']['pending'],
                        'project_name': project_name,
                        'project_path': data['project_path']
                    })
                except Exception as e:
                    logger.warning(f"Skipping invalid session file {filepath}: {e}")
                    continue

            # Sort by last modified (most recent first)
            sessions.sort(key=lambda x: x['last_modified'], reverse=True)

        except Exception as e:
            logger.error(f"Error listing sessions: {e}")
            raise

        return sessions

    def delete_session(self, session_id: str) -> bool:
        """Delete a session file.

        Args:
            session_id: The session ID to delete

        Returns:
            True if deleted, False if not found.
            Returns False for invalid session ID formats.
        """
        try:
            self._validate_session_id(session_id)
        except ValueError:
            return False
        filepath = self.storage_dir / f"session_{session_id}.json"
        if filepath.exists():
            try:
                filepath.unlink()
                logger.info(f"Deleted session {session_id}")
                return True
            except Exception as e:
                logger.error(f"Failed to delete session {session_id}: {e}")
                raise
        return False

    def cleanup_old_sessions(self, days_old: int = 30) -> int:
        """Delete sessions older than specified days.

        Args:
            days_old: Delete sessions older than N days

        Returns:
            Count of deleted sessions
        """
        deleted = 0
        cutoff_date = datetime.now() - timedelta(days=days_old)

        try:
            for filepath in self.storage_dir.glob("session_*.json"):
                try:
                    with open(filepath, 'r') as f:
                        data = json.load(f)
                    last_modified = datetime.fromisoformat(data['last_modified'])
                    if last_modified < cutoff_date:
                        filepath.unlink()
                        deleted += 1
                        logger.info(f"Deleted old session {data['session_id']}")
                except Exception as e:
                    logger.warning(f"Error processing {filepath} during cleanup: {e}")
                    continue
        except Exception as e:
            logger.error(f"Error during session cleanup: {e}")
            raise

        logger.info(f"Cleaned up {deleted} old sessions")
        return deleted
