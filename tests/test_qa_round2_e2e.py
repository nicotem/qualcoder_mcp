"""QA round-2: end-to-end AI-coding loop through the REGISTERED tools only.

The round-1 campaign found the loop could never be driven end-to-end through
the MCP tool surface (suggestions had to be injected via the Python API).
These tests drive analyze_for_coding -> record_suggestions ->
review_suggestions -> update_suggestion_status -> apply_codings exactly as an
MCP client would — plus every unhappy path: wrong project mid-session, a
QualCoder lock appearing mid-write (TOCTOU), position mismatches, and
double-apply.
"""

import asyncio
import json
import re
import shutil
import sqlite3
import time
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server
from qualcoder_mcp.database import (
    QualcoderDatabase,
    QUALCODER_LOCK_FILENAME,
    validate_qda_path,
)


FULLTEXT = "This is interview text. I feel stressed about deadlines. I cope by exercising."
EXPECTED_TOOLS = {
    # project management (4)
    "list_available_projects", "select_project", "get_current_project",
    "copy_project_to_workspace",
    # read/analysis (15)
    "search_coded_text", "get_coded_segments", "search_files",
    "get_coding_frequencies", "search_memos", "export_code_report",
    "get_project_summary", "analyze_file_with_coding", "list_attribute_types",
    "get_file_attributes", "get_case_attributes", "query_by_attribute",
    "find_cooccurring_codes", "get_case_code_matrix", "get_codes_by_case",
    # (16th read)
    "get_cases_by_code",
    # AI coding loop (5)
    "analyze_for_coding", "record_suggestions", "review_suggestions",
    "update_suggestion_status", "apply_codings",
    # writes & recovery (6)
    "import_text_file", "link_file_to_case", "delete_coding",
    "list_backups", "restore_backup", "export_refi_qda",
    # sessions & help (5)
    "get_coding_session_info", "list_coding_sessions",
    "delete_coding_session", "cleanup_old_sessions", "explain_ai_coding_tools",
    # memo writing (2)
    "set_memo", "add_journal_entry",
    # codebook editing — non-destructive (7)
    "create_code", "rename_code", "recolor_code", "move_code_to_category",
    "create_category", "rename_category", "move_category",
    # codebook editing — destructive, preview->confirm (3)
    "merge_codes", "delete_code", "delete_category",
    # backup retention (v0.8 phase C)
    "prune_backups",
    # annotations, category merge, cases (v0.8 phase D1)
    "add_annotation", "update_annotation", "delete_annotation",
    "merge_category", "create_case",
    # inductive / open coding (v0.8 phase A)
    "propose_codes", "review_proposals", "update_proposal",
    "merge_proposals", "update_proposal_status", "create_proposed_codes",
}


def _data_qda(project_path) -> Path:
    return Path(project_path) / "data.qda"


def _session_id(analyze_output: str) -> str:
    return analyze_output.split("Session ID: `")[1].split("`")[0]


def _rows(project_path, sql, args=()):
    conn = sqlite3.connect(str(_data_qda(project_path)))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return rows


class TestToolSurfaceRegistration:

    def test_all_tools_registered(self):
        tools = asyncio.run(server.mcp.list_tools())
        names = {t.name for t in tools}
        assert names == EXPECTED_TOOLS
        assert len(names) == 60


class TestEndToEndLoop:

    def test_full_loop_through_registered_tools(self, setup_server,
                                                qualcoder_db_path):
        # 1. create the session
        out = server.analyze_for_coding(
            [1], instruction="find stress and coping segments")
        sid = _session_id(out)
        assert "record_suggestions" in out  # workflow names the real next step

        # 2. record suggestions: one with exact positions, one with WRONG
        #    positions whose text occurs exactly once (auto-corrected)
        rec = json.loads(server.record_suggestions(sid, [
            {"file_id": 1, "code_name": "Stress",
             "start_pos": 24, "end_pos": 55,
             "segment_text": FULLTEXT[24:55],
             "reasoning": "explicit stress statement", "confidence": 0.9},
            {"file_id": 1, "code_name": "coping",   # case-insensitive name
             "start_pos": 3, "end_pos": 9,           # wrong on purpose
             "segment_text": "I cope by exercising",
             "reasoning": "coping behavior", "confidence": 0.8},
        ]))
        assert rec["recorded_count"] == 2, rec
        assert rec["rejected_count"] == 0
        by_code = {r["code_name"]: r for r in rec["recorded"]}
        assert by_code["Stress"]["positions_corrected"] is False
        assert by_code["Coping"]["positions_corrected"] is True
        assert by_code["Coping"]["start_pos"] == FULLTEXT.index("I cope by exercising")
        guids = [r["guid"] for r in rec["recorded"]]

        # 3. review shows both, with context on demand
        review = server.review_suggestions(sid, show_context=True)
        for guid in guids:
            assert guid in review
        assert "PENDING" in review

        # 4. approve both
        upd = server.update_suggestion_status(sid, approve=guids)
        assert "Approved: 2" in upd

        # 5. apply
        result = server.apply_codings(sid)
        assert "CODINGS APPLIED TO DATABASE" in result
        assert "Backup created" in result

        # DB ground truth: rows written exactly as QualCoder would
        rows = _rows(
            qualcoder_db_path,
            "SELECT ct.*, s.fulltext FROM code_text ct JOIN source s "
            "ON ct.fid = s.id WHERE ct.owner = 'AI Coding Assistant' "
            "ORDER BY ct.pos0")
        assert len(rows) == 2
        for row in rows:
            assert row["seltext"] == row["fulltext"][row["pos0"]:row["pos1"]]
            assert row["avid"] is None
            assert row["important"] is None       # never 0
            assert row["memo"]                    # reasoning + confidence
            assert "[AI Confidence:" in row["memo"]
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}",
                                row["date"])

        # session bookkeeping: both suggestions now 'applied'
        info = json.loads(server.get_coding_session_info(sid))
        assert info["statistics"]["applied"] == 2
        assert info["statistics"]["approved"] == 0

        # the new codings are visible through the read tools
        segs = json.loads(server.get_coded_segments(1))
        assert any(s["owner"] == "AI Coding Assistant" for s in segs["segments"])

        # 6. double-apply explains itself instead of failing wholesale
        again = json.loads(server.apply_codings(sid))
        assert "error" in again
        assert "already applied" in again["error"]
        rows2 = _rows(qualcoder_db_path,
                      "SELECT COUNT(*) as n FROM code_text "
                      "WHERE owner = 'AI Coding Assistant'")
        assert rows2[0]["n"] == 2  # nothing was written twice


class TestUnhappyPaths:

    def test_wrong_project_selected_mid_session(self, setup_server,
                                                qualcoder_db_path, tmp_path):
        """Session created in A, project B selected: every session-consuming
        write refuses and B is untouched."""
        sid = _session_id(server.analyze_for_coding([1]))
        rec = json.loads(server.record_suggestions(sid, [{
            "file_id": 1, "code_name": "Stress",
            "start_pos": 24, "end_pos": 55, "segment_text": FULLTEXT[24:55],
        }]))
        guid = rec["recorded"][0]["guid"]
        server.update_suggestion_status(sid, approve=[guid])

        # a second project = byte copy of the first
        project_b = tmp_path / "other_project.qda"
        shutil.copytree(qualcoder_db_path, project_b)
        assert json.loads(server.select_project(str(project_b)))["success"]

        # record refuses
        rec2 = json.loads(server.record_suggestions(sid, [{
            "file_id": 1, "code_name": "Stress",
            "start_pos": 0, "end_pos": 4, "segment_text": FULLTEXT[0:4],
        }]))
        assert "different project" in rec2["error"]

        # apply refuses, names both projects, writes nothing to B
        before_b = _rows(project_b, "SELECT COUNT(*) as n FROM code_text")[0]["n"]
        out = json.loads(server.apply_codings(sid))
        assert "different project" in out["error"]
        assert out["session_project"] == Path(qualcoder_db_path).name
        assert out["current_project"] == project_b.name
        assert _rows(project_b,
                     "SELECT COUNT(*) as n FROM code_text")[0]["n"] == before_b

        # session-mode REFI export refuses too
        exp = json.loads(server.export_refi_qda(
            str(tmp_path / "x.qdpx"), session_id=sid))
        assert "different project" in exp["error"]

    def test_toctou_qualcoder_opens_mid_write(self, setup_server,
                                              qualcoder_db_path, monkeypatch):
        """A stale foreign lock lets the write proceed unheld; QualCoder
        'opening' between validation and commit must abort with rollback."""
        sid = _session_id(server.analyze_for_coding([1]))
        rec = json.loads(server.record_suggestions(sid, [{
            "file_id": 1, "code_name": "Stress",
            "start_pos": 24, "end_pos": 55, "segment_text": FULLTEXT[24:55],
        }]))
        server.update_suggestion_status(sid, approve=[rec["recorded"][0]["guid"]])

        project_folder = validate_qda_path(qualcoder_db_path).parent
        lock = project_folder / QUALCODER_LOCK_FILENAME
        # stale foreign lock: 31 s old -> write proceeds WITHOUT holding
        lock.write_text(f"crashed_user\n{time.time() - 31}", encoding="utf-8")

        original = QualcoderDatabase.add_coding

        def add_and_refresh_lock(self, *args, **kwargs):
            ctid = original(self, *args, **kwargs)
            # QualCoder starts up right after our insert, before our commit
            lock.write_text(f"qc_user\n{time.time()}", encoding="utf-8")
            return ctid

        monkeypatch.setattr(QualcoderDatabase, "add_coding", add_and_refresh_lock)

        before = _rows(qualcoder_db_path,
                       "SELECT COUNT(*) as n FROM code_text")[0]["n"]
        out = json.loads(server.apply_codings(sid))
        assert "error" in out
        assert "QualCoder" in out["error"]
        # rolled back: nothing committed
        assert _rows(qualcoder_db_path,
                     "SELECT COUNT(*) as n FROM code_text")[0]["n"] == before
        # the foreign lock was NOT deleted (it is not ours)
        assert lock.exists()
        assert lock.read_text(encoding="utf-8").startswith("qc_user")
        # server still usable afterwards
        lock.unlink()
        assert json.loads(server.search_coded_text("stressed"))["result_count"] == 1

    def test_fresh_lock_blocks_the_whole_flow_but_not_reads(
            self, setup_server, qualcoder_db_path):
        sid = _session_id(server.analyze_for_coding([1]))
        rec = json.loads(server.record_suggestions(sid, [{
            "file_id": 1, "code_name": "Stress",
            "start_pos": 24, "end_pos": 55, "segment_text": FULLTEXT[24:55],
        }]))
        server.update_suggestion_status(sid, approve=[rec["recorded"][0]["guid"]])

        project_folder = validate_qda_path(qualcoder_db_path).parent
        lock = project_folder / QUALCODER_LOCK_FILENAME
        lock.write_text(f"livecoder\n{time.time()}", encoding="utf-8")
        try:
            out = json.loads(server.apply_codings(sid))
            assert "livecoder" in out["error"]
            # reads keep working while QualCoder is open
            assert json.loads(server.search_coded_text("stressed"))["result_count"] == 1
            # stale after 31s: heartbeat aging is what unblocks writes
            lock.write_text(f"livecoder\n{time.time() - 31}", encoding="utf-8")
            result = server.apply_codings(sid)
            assert "CODINGS APPLIED" in result
            # the stale foreign lock file was left alone
            assert lock.exists()
            assert lock.read_text(encoding="utf-8").startswith("livecoder")
        finally:
            if lock.exists():
                lock.unlink()

    def test_position_mismatch_rejected_at_record_time(self, setup_server):
        sid = _session_id(server.analyze_for_coding([1]))
        # "I " occurs twice in the fixture text; wrong positions + ambiguous
        # text must be rejected, not guessed
        rec = json.loads(server.record_suggestions(sid, [{
            "file_id": 1, "code_name": "Stress",
            "start_pos": 0, "end_pos": 2, "segment_text": "I ",
        }]))
        assert rec["recorded_count"] == 0
        assert "times in the file" in rec["rejected"][0]["reason"]

        # text not in the file at all -> rejected with both snippets
        rec2 = json.loads(server.record_suggestions(sid, [{
            "file_id": 1, "code_name": "Stress",
            "start_pos": 0, "end_pos": 9, "segment_text": "NOT PRESENT",
        }]))
        assert rec2["recorded_count"] == 0
        assert rec2["rejected"][0]["provided_snippet"] == "NOT PRESENT"
        assert "expected_snippet" in rec2["rejected"][0]

    def test_file_changed_between_record_and_apply(self, setup_server,
                                                   qualcoder_db_path):
        """Simulates QualCoder (or anything) editing the text after the
        suggestion was recorded: apply must re-validate and refuse."""
        sid = _session_id(server.analyze_for_coding([1]))
        rec = json.loads(server.record_suggestions(sid, [{
            "file_id": 1, "code_name": "Stress",
            "start_pos": 24, "end_pos": 55, "segment_text": FULLTEXT[24:55],
        }]))
        guid = rec["recorded"][0]["guid"]
        server.update_suggestion_status(sid, approve=[guid])

        conn = sqlite3.connect(str(_data_qda(qualcoder_db_path)))
        conn.execute("UPDATE source SET fulltext = ? WHERE id = 1",
                     ("Completely different text now, shorter.",))
        conn.commit()
        conn.close()
        server.switch_project(qualcoder_db_path)

        out = json.loads(server.apply_codings(sid))
        assert "error" in out
        assert out["failures"][0]["guid"] == guid
        rows = _rows(qualcoder_db_path,
                     "SELECT COUNT(*) as n FROM code_text "
                     "WHERE owner='AI Coding Assistant'")
        assert rows[0]["n"] == 0

    def test_large_batch_loop(self, setup_server, qualcoder_db_path):
        """A big batch through the whole loop: everything recorded, applied
        once, and the P1 invariant holds for every written row."""
        big = ("Paragraph %d discusses workload pressure in detail.\n" * 1
               ) # placeholder replaced below
        content = "".join(f"Paragraph {i:03d} about workload and recovery. "
                          for i in range(400))
        imp = json.loads(server.import_text_file(
            "big_interview.txt", content, create_backup=False))
        fid = imp["file_id"]

        sid = _session_id(server.analyze_for_coding([fid]))
        suggestions = []
        for i in range(200):
            snippet = f"Paragraph {i:03d} about workload"
            start = content.index(snippet)
            suggestions.append({
                "file_id": fid,
                "code_name": "Stress" if i % 2 else "Coping",
                "start_pos": start, "end_pos": start + len(snippet),
                "segment_text": snippet, "confidence": 0.8,
            })
        t0 = time.perf_counter()
        rec = json.loads(server.record_suggestions(sid, suggestions))
        record_ms = (time.perf_counter() - t0) * 1000
        assert rec["recorded_count"] == 200, rec["rejected"][:3]

        guids = [r["guid"] for r in rec["recorded"]]
        server.update_suggestion_status(sid, approve=guids)
        t0 = time.perf_counter()
        result = server.apply_codings(sid, create_backup=False)
        apply_ms = (time.perf_counter() - t0) * 1000
        assert "CODINGS APPLIED" in result

        bad = _rows(
            qualcoder_db_path,
            "SELECT COUNT(*) as n FROM code_text ct JOIN source s ON ct.fid = s.id "
            "WHERE ct.owner = 'AI Coding Assistant' "
            "AND ct.seltext != substr(s.fulltext, ct.pos0 + 1, ct.pos1 - ct.pos0)")
        assert bad[0]["n"] == 0
        assert record_ms < 10_000 and apply_ms < 10_000, (record_ms, apply_ms)
