"""QA round-2 regression tests: one test (or group) per round-1 finding.

Finding IDs (QA campaign 2026-07-02): F1-F15.
Security/developer blocker IDs: D-1 (control-char filenames),
D-2 (validation before backup — no backup litter), D-3 (backup collisions,
same as F2).

Every test name carries the finding ID it pins. These are permanent
regression tests: if any of them fails, a previously fixed defect is back.
"""

import json
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
    DatabaseLockedError,
    UnsupportedSchemaError,
    validate_qda_path,
)
from qualcoder_mcp.sessions import SessionManager


FULLTEXT = "This is interview text. I feel stressed about deadlines. I cope by exercising."


def _data_qda(project_path) -> Path:
    return Path(project_path) / "data.qda"


def _row_count(project_path, table="code_text") -> int:
    conn = sqlite3.connect(str(_data_qda(project_path)))
    n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    conn.close()
    return n


def _backups(project_path):
    folder = Path(project_path)
    return sorted(folder.parent.glob(f"{folder.stem}_backup_*.qda"))


def _make_approved_session(sid_file_id=1, start=24, end=55):
    """Create a session and approve one valid suggestion via tools only."""
    out = server.analyze_for_coding([sid_file_id])
    sid = out.split("Session ID: `")[1].split("`")[0]
    rec = json.loads(server.record_suggestions(sid, [{
        "file_id": sid_file_id, "code_name": "Stress",
        "start_pos": start, "end_pos": end,
        "segment_text": FULLTEXT[start:end],
        "reasoning": "regression fixture", "confidence": 0.9,
    }]))
    assert rec["recorded_count"] == 1, rec
    guid = rec["recorded"][0]["guid"]
    server.update_suggestion_status(sid, approve=[guid])
    return sid, guid


# =============================================================================
# F1 — locked DB during a write must not brick the server session
# =============================================================================

class TestF1LockedWriteDoesNotBrick:

    def test_f1_apply_under_sqlite_lock_returns_json_and_connection_survives(
            self, setup_server, qualcoder_db_path):
        sid, _ = _make_approved_session()
        before = _row_count(qualcoder_db_path)

        other = sqlite3.connect(str(_data_qda(qualcoder_db_path)))
        other.execute("BEGIN EXCLUSIVE")
        try:
            out = server.apply_codings(sid)
            # Must be error JSON, not a raised exception (old behavior:
            # raw ValueError "Invalid or corrupted SQLite database")
            err = json.loads(out)
            assert "error" in err
            assert "corrupt" not in err["error"].lower()
            assert "locked" in err["error"].lower() or "qualcoder" in err["error"].lower()
        finally:
            other.rollback()
            other.close()

        # THE F1 core: without re-selecting the project, reads still work —
        # the global connection must not be a dead object
        res = json.loads(server.search_coded_text("stressed"))
        assert res["result_count"] == 1
        assert _row_count(qualcoder_db_path) == before

    def test_f1_rw_upgrade_is_open_before_close(self, setup_server, qualcoder_db_path):
        """get_db(read_only=False) failure must leave the old connection usable."""
        ro_before = server.db
        other = sqlite3.connect(str(_data_qda(qualcoder_db_path)))
        other.execute("BEGIN EXCLUSIVE")
        try:
            with pytest.raises((DatabaseLockedError, RuntimeError)):
                server.get_db(read_only=False)
        finally:
            other.rollback()
            other.close()
        assert server.db is ro_before
        assert server.db.conn is not None
        # and it still answers queries
        assert server.db.get_project_info()["database_version"] == "v14"


# =============================================================================
# F2 / D-3 — same-second writes must both succeed (backup name uniquified)
# =============================================================================

class TestF2BackupCollision:

    def test_f2_two_imports_within_one_second_both_succeed(
            self, setup_server, qualcoder_db_path):
        n_backups_before = len(_backups(qualcoder_db_path))
        r1 = json.loads(server.import_text_file("qa_f2_one.txt", "first content"))
        r2 = json.loads(server.import_text_file("qa_f2_two.txt", "second content"))
        assert r1.get("success") is True, r1
        assert r2.get("success") is True, r2
        # both writes made their own backup
        assert len(_backups(qualcoder_db_path)) == n_backups_before + 2


# =============================================================================
# F3 — corrupted database file: friendly error, not a traceback
# =============================================================================

class TestF3CorruptedDatabase:

    def _corrupted_project(self, tmp_path) -> str:
        folder = tmp_path / "corrupt_pages.qda"
        folder.mkdir()
        dbf = folder / "data.qda"
        conn = sqlite3.connect(str(dbf))
        conn.execute("CREATE TABLE project (databaseversion TEXT, date TEXT, memo TEXT, about TEXT, codername TEXT)")
        conn.execute("INSERT INTO project (databaseversion) VALUES ('v14')")
        conn.execute("CREATE TABLE filler (t TEXT)")
        conn.execute("INSERT INTO filler VALUES (?)", ("x" * 8000,))
        conn.commit()
        conn.close()
        data = bytearray(dbf.read_bytes())
        for i in range(4096, min(len(data), 12288)):
            data[i] = 0xAB
        dbf.write_bytes(bytes(data))
        return str(folder)

    def test_f3_select_project_corrupted_pages_returns_error_json(
            self, setup_server, tmp_path):
        out = server.select_project(self._corrupted_project(tmp_path))
        result = json.loads(out)  # must not raise out of the tool
        assert result.get("success") is False
        assert "error" in result


# =============================================================================
# F4 — old schemas: informative refusal instead of mid-read crashes
# =============================================================================

class TestF4OldSchema:

    def _v13_missing_column(self, qualcoder_db_path, tmp_path) -> str:
        dest = tmp_path / "v13_missing.qda"
        shutil.copytree(qualcoder_db_path, dest)
        conn = sqlite3.connect(str(dest / "data.qda"))
        conn.execute("ALTER TABLE code_text DROP COLUMN important")
        conn.execute("UPDATE project SET databaseversion = 'v13'")
        conn.commit()
        conn.close()
        return str(dest)

    def _v13_version_string_only(self, qualcoder_db_path, tmp_path) -> str:
        dest = tmp_path / "v13_verstring.qda"
        shutil.copytree(qualcoder_db_path, dest)
        conn = sqlite3.connect(str(dest / "data.qda"))
        conn.execute("UPDATE project SET databaseversion = 'v13'")
        conn.commit()
        conn.close()
        return str(dest)

    def test_f4_missing_column_refused_at_connect_with_upgrade_hint(
            self, setup_server, qualcoder_db_path, tmp_path):
        path = self._v13_missing_column(qualcoder_db_path, tmp_path)
        with pytest.raises(UnsupportedSchemaError, match="QualCoder 3.8"):
            QualcoderDatabase(path)
        # and through the tool surface: error JSON, not a crash
        result = json.loads(server.select_project(path))
        assert result.get("success") is False
        assert "QualCoder 3.8" in result["error"]

    def test_f4_v13_version_reads_ok_writes_refused_without_backup(
            self, setup_server, qualcoder_db_path, tmp_path):
        path = self._v13_version_string_only(qualcoder_db_path, tmp_path)
        result = json.loads(server.select_project(path))
        assert result.get("success") is True  # reads stay permissive
        assert json.loads(server.search_coded_text("stressed"))["result_count"] == 1

        n_backups = len(_backups(path))
        out = json.loads(server.import_text_file("nope.txt", "content"))
        assert "error" in out
        assert "v14" in out["error"]
        assert len(_backups(path)) == n_backups  # refused before any backup


# =============================================================================
# F5 — NULL filename must not abort search_files
# =============================================================================

class TestF5NullFilename:

    def test_f5_search_survives_null_name_row(self, setup_server, qualcoder_db_path):
        conn = sqlite3.connect(str(_data_qda(qualcoder_db_path)))
        conn.execute(
            "INSERT INTO source (id, name, fulltext) VALUES (99, NULL, 'null-named text here')")
        conn.commit()
        conn.close()
        server.switch_project(qualcoder_db_path)  # reopen to see the change

        res = json.loads(server.search_files("interview"))
        assert "error" not in res
        assert res["total_matches"] >= 1
        # content search also reaches the NULL-named row without crashing
        res2 = json.loads(server.search_files(
            "null-named", search_filename=False, search_content=True))
        assert res2["total_matches"] == 1
        assert res2["results"][0]["file_name"] is None


# =============================================================================
# F6 — text codings on media sources must be refused (no junk rows)
# =============================================================================

class TestF6MediaCodingRefused:

    def test_f6_apply_to_image_source_refused_pre_backup(
            self, setup_server, qualcoder_db_path):
        conn = sqlite3.connect(str(_data_qda(qualcoder_db_path)))
        conn.execute(
            "INSERT INTO source (id, name, fulltext, mediapath) "
            "VALUES (50, 'photo.jpg', NULL, '/images/photo.jpg')")
        conn.commit()
        conn.close()
        server.switch_project(qualcoder_db_path)

        out = server.analyze_for_coding([1])
        sid = out.split("Session ID: `")[1].split("`")[0]
        # record_suggestions already refuses media targets
        rec = json.loads(server.record_suggestions(sid, [{
            "file_id": 50, "code_name": "Stress",
            "start_pos": 0, "end_pos": 10, "segment_text": "irrelevant",
        }]))
        assert rec["recorded_count"] == 0
        assert rec["rejected_count"] == 1
        assert "not a text source" in rec["rejected"][0]["reason"]

        # defense in depth: db layer refuses too
        wdb = QualcoderDatabase(qualcoder_db_path, read_only=False)
        try:
            with pytest.raises(ValueError, match="not a text source"):
                wdb.add_coding(50, 1, 0, 10, "irrelevant", "qa")
        finally:
            wdb.close()
        assert _row_count(qualcoder_db_path) == 2  # fixture's two codings only


# =============================================================================
# F7 — seltext/position mismatch must be refused, never silently stored
# =============================================================================

class TestF7TextPositionIntegrity:

    def test_f7_mismatched_text_refused_no_backup_no_write(
            self, setup_server, qualcoder_db_path):
        sid, guid = _make_approved_session()
        # corrupt the suggestion's stored text behind the tools' back
        sm = server.session_manager
        session = sm.load_session(sid)
        session.get_suggestion_by_guid(guid).segment_text = "TOTALLY WRONG TEXT"
        sm.save_session(session)

        n_backups = len(_backups(qualcoder_db_path))
        before = _row_count(qualcoder_db_path)
        out = json.loads(server.apply_codings(sid))
        assert "error" in out
        assert out["failures"][0]["guid"] == guid
        assert "expected_snippet" in out["failures"][0]
        assert _row_count(qualcoder_db_path) == before
        assert len(_backups(qualcoder_db_path)) == n_backups  # no backup litter

    def test_f7_db_layer_verifies_slice(self, qualcoder_db_path):
        wdb = QualcoderDatabase(qualcoder_db_path, read_only=False)
        try:
            with pytest.raises(ValueError, match="does not match the file text"):
                wdb.add_coding(1, 1, 0, 12, "WRONG WRONG!", "qa")
        finally:
            wdb.close()


# =============================================================================
# F8 — orphaned codings: all statistics tools agree
# =============================================================================

class TestF8OrphanConsistency:

    def test_f8_counts_agree_with_orphaned_fid(self, setup_server, qualcoder_db_path):
        conn = sqlite3.connect(str(_data_qda(qualcoder_db_path)))
        conn.execute(
            "INSERT INTO code_text (cid, fid, seltext, pos0, pos1, owner, date, memo) "
            "VALUES (1, 999, 'ghost segment', 0, 13, 'qa', '2024-01-01', '')")
        conn.commit()
        conn.close()
        server.switch_project(qualcoder_db_path)

        freq = json.loads(server.get_coding_frequencies())
        code1 = next(c for c in freq["codes"] if c["code_id"] == 1)
        segs = json.loads(server.get_coded_segments(1))
        details = json.loads(server.get_code_info(1))
        assert code1["frequency"] == segs["segment_count"] == \
            details["statistics"]["text_segments"] == 1


# =============================================================================
# F9 — cleanup_old_sessions must refuse to wipe everything
# =============================================================================

class TestF9SessionCleanupGuard:

    @pytest.mark.parametrize("days", [0, -5])
    def test_f9_nonpositive_days_refused(self, setup_server, days):
        out = server.analyze_for_coding([1])
        sid = out.split("Session ID: `")[1].split("`")[0]

        result = json.loads(server.cleanup_old_sessions(days))
        assert "error" in result
        assert server.session_manager.session_exists(sid)  # nothing deleted


# =============================================================================
# F10 — content search must include /docs/ imported documents and PDFs
# =============================================================================

class TestF10DocsContentSearch:

    def test_f10_docs_sources_are_content_searched(self, setup_server, qualcoder_db_path):
        conn = sqlite3.connect(str(_data_qda(qualcoder_db_path)))
        conn.execute(
            "INSERT INTO source (id, name, fulltext, mediapath) VALUES "
            "(60, 'report.pdf', 'pdf mentions uniqueword here', '/docs/report.pdf')")
        conn.execute(
            "INSERT INTO source (id, name, fulltext, mediapath) VALUES "
            "(61, 'imported.docx', 'docx mentions uniqueword too', '/docs/imported.docx')")
        conn.execute(
            "INSERT INTO source (id, name, fulltext, mediapath) VALUES "
            "(62, 'clip.mp4', NULL, '/video/clip.mp4')")
        conn.commit()
        conn.close()
        server.switch_project(qualcoder_db_path)

        res = json.loads(server.search_files(
            "uniqueword", search_filename=False, search_content=True))
        hit_names = {r["file_name"] for r in res["results"]}
        assert {"report.pdf", "imported.docx"} <= hit_names
        # media skips are visible, never silent
        assert res["performance_info"]["files_skipped_no_text"] >= 1


# =============================================================================
# F11 — control characters must not crash the REFI-QDA export
# =============================================================================

class TestF11RefiControlChars:

    def test_f11_export_with_control_chars_succeeds_and_parses(
            self, setup_server, qualcoder_db_path, tmp_path):
        conn = sqlite3.connect(str(_data_qda(qualcoder_db_path)))
        conn.execute("UPDATE code_name SET name = ?, memo = ? WHERE cid = 1",
                     ("ctrl\x0bchar", "m\x0c\x00emo"))
        conn.commit()
        conn.close()
        server.switch_project(qualcoder_db_path)

        out_path = tmp_path / "exports" / "poison.qdpx"
        out_path.parent.mkdir()
        result = json.loads(server.export_refi_qda(str(out_path)))
        assert result.get("success") is True, result

        import zipfile
        import xml.etree.ElementTree as ET
        from xml.dom import minidom
        with zipfile.ZipFile(out_path) as z:
            qde = z.read("project.qde")
        ET.fromstring(qde)          # both parsers must accept the XML
        minidom.parseString(qde)


# =============================================================================
# F12 — locked-DB reads: friendly JSON through the tool surface
# =============================================================================

class TestF12LockedReads:

    def test_f12_search_tool_under_exclusive_lock_returns_locked_json(
            self, setup_server, qualcoder_db_path):
        other = sqlite3.connect(str(_data_qda(qualcoder_db_path)))
        other.execute("BEGIN EXCLUSIVE")
        try:
            out = json.loads(server.search_coded_text("stressed"))
            assert "error" in out
            assert "locked" in out["error"].lower() or "qualcoder" in out["error"].lower()
        finally:
            other.rollback()
            other.close()

    def test_f12_select_project_under_lock_says_locked_not_invalid(
            self, setup_server, qualcoder_db_path):
        other = sqlite3.connect(str(_data_qda(qualcoder_db_path)))
        other.execute("BEGIN EXCLUSIVE")
        try:
            out = json.loads(server.select_project(qualcoder_db_path))
            assert out.get("success") is False
            assert "Invalid project path" not in out["error"]
            assert "locked" in out["error"].lower() or "qualcoder" in out["error"].lower()
        finally:
            other.rollback()
            other.close()


# =============================================================================
# F13 — validation errors surface as error JSON, not raw exceptions
# =============================================================================

class TestF13ValidationErrorShape:

    @pytest.mark.parametrize("call", [
        lambda: server.get_coded_segments(1, limit=0),
        lambda: server.search_coded_text("x", limit=-5),
        lambda: server.find_cooccurring_codes(1, window_size=-1),
        lambda: server.query_by_attribute("Age", "30", "bogus_type"),
        lambda: server.get_file_attributes(-1),
        lambda: server.query_by_attribute("Age", "30", operator="between"),
    ])
    def test_f13_error_json_not_exception(self, setup_server, call):
        out = call()          # must not raise
        assert "error" in json.loads(out)


# =============================================================================
# F14 — min_confidence clamped to [0, 1]
# =============================================================================

class TestF14ConfidenceClamp:

    def test_f14_out_of_range_confidence_clamped(self, setup_server):
        out = server.analyze_for_coding([1], min_confidence=7.0)
        sid = out.split("Session ID: `")[1].split("`")[0]
        session = server.session_manager.load_session(sid)
        assert session.min_confidence == 1.0


# =============================================================================
# F15 — every write-tool failure is JSON (no plain-string errors)
# =============================================================================

class TestF15UniformErrorShape:

    def test_f15_backup_failure_is_json(self, setup_server, qualcoder_db_path,
                                        monkeypatch):
        sid, _ = _make_approved_session()
        monkeypatch.setattr(
            QualcoderDatabase, "backup_before_write",
            lambda self: (_ for _ in ()).throw(OSError("disk full (simulated)")))
        out = server.apply_codings(sid)
        result = json.loads(out)   # the old code returned a "❌ ..." plain string
        assert "error" in result
        assert _row_count(qualcoder_db_path) == 2  # nothing written


# =============================================================================
# D-1 — control characters in imported filenames are rejected
# =============================================================================

class TestD1ControlCharFilenames:

    @pytest.mark.parametrize("name", [
        "evil\x00name.txt", "line\nbreak.txt", "bell\x07.txt", "del\x7f.txt",
    ])
    def test_d1_control_char_filename_rejected(self, setup_server,
                                               qualcoder_db_path, name):
        before = _row_count(qualcoder_db_path, "source")
        out = json.loads(server.import_text_file(name, "content"))
        assert "error" in out
        assert _row_count(qualcoder_db_path, "source") == before

    def test_d1_nfc_duplicate_detected(self, setup_server, qualcoder_db_path):
        nfd = "café.txt"      # NFD: e + combining acute
        nfc = "café.txt"       # NFC: precomposed é
        r1 = json.loads(server.import_text_file(nfd, "one"))
        assert r1.get("success") is True
        r2 = json.loads(server.import_text_file(nfc, "two"))
        assert "error" in r2 and "already exists" in r2["error"]


# =============================================================================
# D-2 — ALL validation precedes the backup: rejected writes leave no litter
# =============================================================================

class TestD2NoBackupLitter:

    def test_d2_five_invalid_writes_zero_new_backups(self, setup_server,
                                                     qualcoder_db_path):
        n = len(_backups(qualcoder_db_path))
        # 1. duplicate filename
        server.import_text_file("interview.txt", "dup")
        # 2. traversal filename
        server.import_text_file("../evil.txt", "x")
        # 3. unknown case link
        server.import_text_file("okname.txt", "x", case_name="No Such Case")
        # 4. apply with mismatched text
        sid, guid = _make_approved_session()
        session = server.session_manager.load_session(sid)
        session.get_suggestion_by_guid(guid).segment_text = "WRONG"
        server.session_manager.save_session(session)
        server.apply_codings(sid)
        # 5. delete a nonexistent coding
        server.delete_coding(424242)
        assert len(_backups(qualcoder_db_path)) == n
