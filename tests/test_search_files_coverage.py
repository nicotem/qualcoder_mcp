"""Targeted coverage for database.search_files (track5 R7 coverage hole).

The search_files body was the largest untested read-side region: this file
exercises the filename / content / memo branches, their combinations, case
sensitivity, limits, media skip counting, and the NULL-name guard.
"""

import json
import sqlite3
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server


def _add_file(project_path, fid, name, fulltext, mediapath=None, memo=""):
    con = sqlite3.connect(str(Path(project_path) / "data.qda"))
    con.execute(
        "INSERT INTO source (id, name, fulltext, mediapath, memo, owner, date) "
        "VALUES (?, ?, ?, ?, ?, 't', '2024')",
        (fid, name, fulltext, mediapath, memo))
    con.commit()
    con.close()


@pytest.fixture
def search_project(setup_server, qualcoder_db_path):
    # fixture already has: interview.txt (stress/cope text, memo 'Test memo'),
    # notes.txt. Add: a /docs/ import, a memo-only match, media, NULL name.
    _add_file(qualcoder_db_path, 10, "report_final.pdf",
              "The keyword magnetita appears here.", "/docs/report_final.pdf")
    _add_file(qualcoder_db_path, 11, "photo.jpg", None, "/images/photo.jpg",
              memo="magnetita in the caption")
    _add_file(qualcoder_db_path, 12, "MAGNETITA_notes.txt",
              "no match inside", None)
    _add_file(qualcoder_db_path, 13, None, "unnamed but magnetita content",
              None)
    server.switch_project(qualcoder_db_path)
    return qualcoder_db_path


class TestSearchFilesBranches:

    def test_filename_only_default(self, search_project):
        out = json.loads(server.search_files("magnetita"))
        assert out["search_parameters"]["searched_filename"] is True
        assert out["search_parameters"]["searched_content"] is False
        names = {r["file_name"] for r in out["results"]}
        assert names == {"MAGNETITA_notes.txt"}  # case-insensitive default
        assert out["results"][0]["matched_in"]["filename"] is True

    def test_content_branch_includes_docs_imports(self, search_project):
        """F10 fix pinned: /docs/ sources with fulltext are content-searched."""
        out = json.loads(server.search_files(
            "magnetita", search_filename=False, search_content=True))
        names = {r["file_name"] for r in out["results"]}
        assert "report_final.pdf" in names          # /docs/ mediapath
        assert None in names                        # NULL-name row searched too
        # media without text is counted, not silently skipped
        assert out["performance_info"]["files_skipped_no_text"] >= 1

    def test_content_match_has_position_and_preview(self, search_project):
        out = json.loads(server.search_files(
            "stressed", search_filename=False, search_content=True))
        match = out["results"][0]["matches"][0]
        assert match["location"] == "content"
        assert isinstance(match["position"], int)
        assert "stressed" in match["preview"]

    def test_memo_branch(self, search_project):
        out = json.loads(server.search_files(
            "magnetita", search_filename=False, search_memo=True))
        names = {r["file_name"] for r in out["results"]}
        assert names == {"photo.jpg"}
        assert out["results"][0]["matched_in"]["memo"] is True

    def test_combined_branches_aggregate_match_count(self, search_project):
        out = json.loads(server.search_files(
            "magnetita", search_filename=True, search_content=True,
            search_memo=True))
        by_name = {r["file_name"]: r for r in out["results"]}
        assert set(by_name) == {"MAGNETITA_notes.txt", "report_final.pdf",
                                "photo.jpg", None}
        assert by_name["photo.jpg"]["matched_in"] == {
            "filename": False, "content": False, "memo": True}

    def test_case_sensitive(self, search_project):
        out = json.loads(server.search_files("magnetita", case_sensitive=True))
        assert out["total_matches"] == 0  # filename is MAGNETITA_
        out = json.loads(server.search_files("MAGNETITA", case_sensitive=True))
        assert out["total_matches"] == 1

    def test_limit_respected(self, search_project):
        out = json.loads(server.search_files(".", limit=2))
        assert len(out["results"]) == 2

    def test_content_matches_capped_per_file(self, search_project,
                                             qualcoder_db_path):
        _add_file(qualcoder_db_path, 14, "dense.txt", "hit " * 50)
        server.switch_project(qualcoder_db_path)
        out = json.loads(server.search_files(
            "hit", search_filename=False, search_content=True))
        dense = next(r for r in out["results"] if r["file_name"] == "dense.txt")
        assert len([m for m in dense["matches"]
                    if m["location"] == "content"]) == 5  # per-file cap

    def test_empty_pattern_returns_empty(self, search_project):
        out = json.loads(server.search_files(""))
        assert out["total_matches"] == 0
        assert out["results"] == []

    def test_null_filename_does_not_abort(self, search_project):
        """F5 fix pinned at the branch level: NULL names searched as ''."""
        out = json.loads(server.search_files("anything at all"))
        assert "error" not in out
