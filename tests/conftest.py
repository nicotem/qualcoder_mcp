"""Pytest configuration and shared fixtures."""

import pytest
import tempfile
import shutil
from pathlib import Path


@pytest.fixture
def temp_session_dir():
    """Create a temporary directory for session storage."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir)


@pytest.fixture
def sample_suggestion_data():
    """Sample data for creating CodingSuggestion instances."""
    return {
        "file_id": 1,
        "file_name": "interview_01.txt",
        "code_id": 10,
        "code_name": "Workplace Stress",
        "start_pos": 100,
        "end_pos": 250,
        "segment_text": "I often feel overwhelmed with the workload and tight deadlines.",
        "ai_memo": "Clear expression of stress related to workload",
        "confidence": 0.85,
        "status": "pending"
    }


@pytest.fixture
def sample_session_data():
    """Sample data for creating AICodingSession instances."""
    return {
        "project_path": "/home/user/test_project.qda",
        "description": "Test coding session",
        "file_ids": [1, 2, 3],
        "code_names": ["Workplace Stress", "Coping Strategies"],
        "instruction": "Code all relevant segments",
        "min_confidence": 0.6
    }
