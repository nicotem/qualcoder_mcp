"""
Tests for server.py @mcp.resource() functions.

These test the resource functions directly, verifying they return
correct JSON for various states (loaded project, no project, empty data).
Fixtures are provided by conftest.py.
"""

import pytest
import json
import os
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server


# =============================================================================
# TESTS: Resources with data
# =============================================================================

class TestProjectInfoResource:
    def test_returns_valid_json(self, setup_server):
        result = server.get_project_info()
        data = json.loads(result)
        assert data["database_version"] == "v14"
        assert data["coder_name"] == "TestCoder"


class TestCodesListResource:
    def test_returns_codes(self, setup_server):
        result = server.list_all_codes()
        data = json.loads(result)
        assert len(data) == 2
        names = [c["name"] for c in data]
        assert "Stress" in names

    def test_empty_db(self, setup_empty_server):
        result = server.list_all_codes()
        data = json.loads(result)
        assert data == []


class TestCategoriesListResource:
    def test_returns_categories(self, setup_server):
        result = server.list_all_categories()
        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["name"] == "Category A"

    def test_empty_db(self, setup_empty_server):
        result = server.list_all_categories()
        data = json.loads(result)
        assert data == []


class TestCodeInfoResource:
    def test_existing_code(self, setup_server):
        result = server.get_code_info(1)
        data = json.loads(result)
        assert data["name"] == "Stress"
        assert "statistics" in data

    def test_nonexistent_code(self, setup_server):
        result = server.get_code_info(999)
        data = json.loads(result)
        assert "error" in data


class TestFilesListResource:
    def test_returns_files(self, setup_server):
        result = server.list_all_files()
        data = json.loads(result)
        assert len(data) == 2
        names = [f["name"] for f in data]
        assert "interview.txt" in names

    def test_empty_db(self, setup_empty_server):
        result = server.list_all_files()
        data = json.loads(result)
        assert data == []


class TestFileContentResource:
    def test_existing_file(self, setup_server):
        result = server.get_file_content(1)
        data = json.loads(result)
        assert data["name"] == "interview.txt"
        assert "interview text" in data["content"]

    def test_nonexistent_file(self, setup_server):
        result = server.get_file_content(999)
        data = json.loads(result)
        assert "error" in data


class TestCasesListResource:
    def test_returns_cases(self, setup_server):
        result = server.list_all_cases()
        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["name"] == "Case A"

    def test_empty_db(self, setup_empty_server):
        result = server.list_all_cases()
        data = json.loads(result)
        assert data == []


class TestCaseInfoResource:
    def test_existing_case(self, setup_server):
        result = server.get_case_info(1)
        data = json.loads(result)
        assert data["name"] == "Case A"

    def test_nonexistent_case(self, setup_server):
        result = server.get_case_info(999)
        data = json.loads(result)
        assert "error" in data


class TestJournalResource:
    def test_returns_entries(self, setup_server):
        result = server.get_journal_entries()
        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["name"] == "Entry 1"

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
