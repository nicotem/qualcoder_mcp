"""Tests for the fix/write-path wave.

Covers: record_suggestions (the loop-completing tool), session-project
binding, text/position integrity, the QualCoder heartbeat lock protocol,
recovery tools (delete_coding, list_backups, restore_backup), case linkage,
REFI-QDA export tool, project-validity tightening, and backup behavior.
"""

import json
import time
import shutil
import sqlite3
import zipfile
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server
import qualcoder_mcp.database as dbmod
from qualcoder_mcp.database import (
    QualcoderDatabase,
    validate_qda_path,
    qualcoder_lock_state,
    copy_project_to_workspace,
    QUALCODER_LOCK_FILENAME,
    backup_project,
)
from qualcoder_mcp.sessions import AICodingSession, CodingSuggestion


FULLTEXT = "This is interview text. I feel stressed about deadlines. I cope by exercising."


def _make_session(setup_server, qualcoder_db_path, **kwargs):
    out = server.analyze_for_coding([1], **kwargs)
    return out.split("Session ID: `")[1].split("`")[0]


def _lock_file(project_path) -> Path:
    return validate_qda_path(project_path).parent / QUALCODER_LOCK_FILENAME


# =============================================================================
# Security fixes (S-1/S-2/S-3)
# =============================================================================

class TestSecurityFixes:

    @pytest.mark.parametrize("evil_name", [
        "../OUTSIDE/exfil",
        "../../tmp/exfil",
        "/tmp/abs_evil",
        "sub/dir/name",
        "back\\slash",
        "dots/../../escape",
        "nul\x00name",
    ])
    def test_s1_copy_rejects_escaping_names(self, qualcoder_db_path, tmp_path,
                                            monkeypatch, evil_name):
        """S-1: new_name must never write outside the workspace."""
        workspace = tmp_path / "workspace"
        monkeypatch.setattr(dbmod, "DEFAULT_WORKSPACE", workspace)
        # Snapshot the whole tmp tree so we can prove nothing escaped
        before = set(tmp_path.rglob("*"))
        with pytest.raises(ValueError):
            copy_project_to_workspace(qualcoder_db_path, new_name=evil_name)
        # Nothing was created anywhere under tmp_path outside the workspace
        after = set(tmp_path.rglob("*"))
        new_paths = after - before
        for p in new_paths:
            assert workspace in p.parents or p == workspace, (
                f"escape: {p} created outside workspace")

    def test_s1_copy_rejects_escape_via_tool(self, setup_server,
                                             qualcoder_db_path, tmp_path,
                                             monkeypatch):
        """S-1 through the registered MCP tool (the reported attack path)."""
        workspace = tmp_path / "ws"
        monkeypatch.setattr(dbmod, "DEFAULT_WORKSPACE", workspace)
        outside = tmp_path / "OUTSIDE"
        # OS-native absolute path (a real drive-qualified path on Windows,
        # a leading-slash path on POSIX) — both must be rejected
        abs_target = tmp_path / "qc_abs_evil_target"
        for evil in ["../OUTSIDE/exfil", str(abs_target)]:
            out = json.loads(server.copy_project_to_workspace(
                qualcoder_db_path, new_name=evil))
            assert "error" in out
        assert not outside.exists()
        assert not abs_target.with_suffix(".qda").exists()

    def test_s1_plain_name_still_works(self, qualcoder_db_path, tmp_path,
                                       monkeypatch):
        workspace = tmp_path / "workspace"
        monkeypatch.setattr(dbmod, "DEFAULT_WORKSPACE", workspace)
        dest = copy_project_to_workspace(qualcoder_db_path, new_name="My Copy")
        assert dest.parent == workspace.resolve() or dest.parent == workspace
        assert dest.name == "My Copy.qda"
        assert (dest / "data.qda").exists()

    def test_s2_backup_failure_message_is_sanitized(self, setup_server,
                                                    qualcoder_db_path,
                                                    monkeypatch):
        """S-2: backup-failure JSON must not leak absolute paths/username."""
        secret = "/Users/realname/secret/path"

        def boom(self):
            raise OSError(f"Backup failed: [Errno 13] Permission denied: "
                          f"'{secret}/proj_backup.qda'")

        monkeypatch.setattr(QualcoderDatabase, "backup_before_write", boom)
        out = server.import_text_file("s2.txt", "content body",
                                      create_backup=True)
        assert secret not in out
        assert "realname" not in out
        data = json.loads(out)
        assert "error" in data

    def test_s3_oversized_lock_file_is_capped(self, qualcoder_db_path):
        """S-3: a huge lock file must not be read whole into memory."""
        lock = _lock_file(qualcoder_db_path)
        # 20 MB of garbage before any parseable content
        lock.write_text("x" * (20 * 1024 * 1024), encoding="utf-8")
        try:
            state, holder = qualcoder_lock_state(lock.parent)
            # unparseable within the capped prefix -> treated as stale
            assert state == "stale"
        finally:
            lock.unlink()

    def test_s3_normal_lock_still_parsed(self, qualcoder_db_path):
        lock = _lock_file(qualcoder_db_path)
        lock.write_text(f"alice\n{time.time()}", encoding="utf-8")
        try:
            state, holder = qualcoder_lock_state(lock.parent)
            assert state == "active" and holder == "alice"
        finally:
            lock.unlink()


# =============================================================================
# Session-start QualCoder check ("ask the user to close it")
# =============================================================================

class TestSessionStartQualcoderCheck:

    def test_fresh_lock_flags_session_start(self, setup_server, qualcoder_db_path):
        """Active heartbeat: session still created (reads are safe) but the
        response carries the flag and the explicit ask-the-user instruction."""
        lock = _lock_file(qualcoder_db_path)
        lock.write_text(f"gemma\n{time.time()}", encoding="utf-8")
        try:
            out = server.analyze_for_coding([1])
            assert "qualcoder_open: true" in out
            assert "action_required" in out
            assert "close QualCoder" in out
            assert "get_current_project" in out
            assert "gemma" in out
            # the session was still created
            sid = out.split("Session ID: `")[1].split("`")[0]
            assert server.session_manager.session_exists(sid)
        finally:
            lock.unlink()

    def test_absent_lock_consistent_shape(self, setup_server, qualcoder_db_path):
        out = server.analyze_for_coding([1])
        # QA6-1: qualcoder_open is always present, false when clear; no
        # directive noise beyond that
        env = json.loads(out)
        assert env["qualcoder_open"] is False
        assert "action_required" not in env
        assert "STOP" not in out
        assert "ANALYSIS SESSION CREATED" in out

    def test_stale_lock_consistent_shape(self, setup_server, qualcoder_db_path):
        lock = _lock_file(qualcoder_db_path)
        lock.write_text(f"gemma\n{time.time() - 60}", encoding="utf-8")
        try:
            out = server.analyze_for_coding([1])
            env = json.loads(out)
            assert env["qualcoder_open"] is False
            assert "action_required" not in env
            assert "ANALYSIS SESSION CREATED" in out
        finally:
            lock.unlink()

    def test_get_current_project_reflects_open_state(self, setup_server,
                                                     qualcoder_db_path):
        lock = _lock_file(qualcoder_db_path)
        lock.write_text(f"gemma\n{time.time()}", encoding="utf-8")
        try:
            out = json.loads(server.get_current_project())
            assert out["qualcoder_open"] is True
            assert out["qualcoder_lock"]["holder"] == "gemma"
            assert "writes will be refused" in out["qualcoder_lock"]["note"]
        finally:
            lock.unlink()

    def test_get_current_project_reflects_closed_state(self, setup_server,
                                                       qualcoder_db_path):
        out = json.loads(server.get_current_project())
        assert out["qualcoder_open"] is False
        assert "qualcoder_lock" not in out

    def test_get_current_project_stale_lock_detail(self, setup_server,
                                                   qualcoder_db_path):
        lock = _lock_file(qualcoder_db_path)
        lock.write_text(f"gemma\n{time.time() - 60}", encoding="utf-8")
        try:
            out = json.loads(server.get_current_project())
            assert out["qualcoder_open"] is False
            assert out["qualcoder_lock"]["state"] == "stale"
        finally:
            lock.unlink()

    def test_docstring_mandates_the_stop(self):
        doc = server.analyze_for_coding.__doc__ or ""
        assert "qualcoder_open" in doc
        assert "STOP" in doc
        assert "get_current_project" in doc


# =============================================================================
# record_suggestions
# =============================================================================

class TestRecordSuggestions:

    def test_happy_path_and_stats(self, setup_server, qualcoder_db_path):
        sid = _make_session(setup_server, qualcoder_db_path)
        result = json.loads(server.record_suggestions(sid, [{
            "file_id": 1, "code_name": "Stress",
            "start_pos": 24, "end_pos": 55,
            "segment_text": FULLTEXT[24:55],
            "reasoning": "clear stress", "confidence": 0.9,
        }]))
        assert result["recorded_count"] == 1
        assert result["rejected_count"] == 0
        assert result["statistics"]["pending"] == 1
        assert result["recorded"][0]["positions_corrected"] is False

    def test_locates_unique_text_without_positions(self, setup_server, qualcoder_db_path):
        sid = _make_session(setup_server, qualcoder_db_path)
        result = json.loads(server.record_suggestions(sid, [{
            "file_id": 1, "code_name": "stress",  # case-insensitive
            "segment_text": "I cope by exercising",
        }]))
        assert result["recorded_count"] == 1
        rec = result["recorded"][0]
        assert (rec["start_pos"], rec["end_pos"]) == (57, 77)

    def test_corrects_wrong_positions(self, setup_server, qualcoder_db_path):
        sid = _make_session(setup_server, qualcoder_db_path)
        result = json.loads(server.record_suggestions(sid, [{
            "file_id": 1, "code_id": 1,
            "start_pos": 3, "end_pos": 22,  # wrong on purpose
            "segment_text": "I cope by exercising",
        }]))
        rec = result["recorded"][0]
        assert rec["positions_corrected"] is True
        assert (rec["start_pos"], rec["end_pos"]) == (57, 77)

    def test_rejects_text_not_in_file(self, setup_server, qualcoder_db_path):
        sid = _make_session(setup_server, qualcoder_db_path)
        result = json.loads(server.record_suggestions(sid, [{
            "file_id": 1, "code_id": 1, "start_pos": 0, "end_pos": 10,
            "segment_text": "NOT IN THE FILE AT ALL",
        }]))
        assert result["recorded_count"] == 0
        assert "not found in the file" in result["rejected"][0]["reason"]

    def test_rejects_unknown_code_and_file(self, setup_server, qualcoder_db_path):
        sid = _make_session(setup_server, qualcoder_db_path)
        result = json.loads(server.record_suggestions(sid, [
            {"file_id": 99, "code_id": 1, "segment_text": "x"},
            {"file_id": 1, "code_name": "Nope", "segment_text": "stressed"},
        ]))
        assert result["recorded_count"] == 0
        reasons = " | ".join(r["reason"] for r in result["rejected"])
        assert "file_id 99" in reasons
        assert "'Nope' not found" in reasons

    def test_duplicates_skipped(self, setup_server, qualcoder_db_path):
        sid = _make_session(setup_server, qualcoder_db_path)
        item = {"file_id": 1, "code_id": 1, "segment_text": "I cope by exercising"}
        json.loads(server.record_suggestions(sid, [item]))
        result = json.loads(server.record_suggestions(sid, [item]))
        assert result["skipped_duplicates"] == 1

    def test_project_binding(self, setup_server, qualcoder_db_path, tmp_path):
        """Recording against a session from another project is refused."""
        other = AICodingSession(project_path="/nonexistent/other.qda")
        setup_server.session_manager.save_session(other)
        result = json.loads(server.record_suggestions(other.session_id, [
            {"file_id": 1, "code_id": 1, "segment_text": "stressed"}]))
        assert "error" in result


# =============================================================================
# apply_codings: binding, integrity, applied status
# =============================================================================

class TestApplyCodingsSafety:

    def _approved_session(self, setup_server, qualcoder_db_path, **sugg_kwargs):
        session = AICodingSession(project_path=qualcoder_db_path)
        defaults = dict(
            file_id=1, file_name="interview.txt", code_id=1, code_name="Stress",
            start_pos=24, end_pos=55, segment_text=FULLTEXT[24:55],
            reasoning="r", confidence=0.9, status="approved",
        )
        defaults.update(sugg_kwargs)
        session.add_suggestion(CodingSuggestion(**defaults))
        setup_server.session_manager.save_session(session)
        return session

    def test_cross_project_apply_refused(self, setup_server, qualcoder_db_path, tmp_path):
        # Build a second, distinct project and open it
        other = tmp_path / "other_project.qda"
        shutil.copytree(qualcoder_db_path, other)
        session = self._approved_session(setup_server, qualcoder_db_path)
        json.loads(server.select_project(str(other)))

        result = json.loads(server.apply_codings(session.session_id, create_backup=False))
        assert "different project" in result["error"]
        # nothing written into the other project
        con = sqlite3.connect(str(other / "data.qda"))
        count = con.execute("SELECT COUNT(*) FROM code_text").fetchone()[0]
        con.close()
        assert count == 2  # only the fixture rows

    def test_mismatched_text_fails_before_backup(self, setup_server, qualcoder_db_path):
        session = self._approved_session(
            setup_server, qualcoder_db_path,
            segment_text="TOTALLY WRONG", start_pos=0, end_pos=13,
        )
        parent = Path(qualcoder_db_path).parent
        before = len(list(parent.glob("*_backup_*")))
        result = json.loads(server.apply_codings(session.session_id, create_backup=True))
        after = len(list(parent.glob("*_backup_*")))
        assert "failed validation" in result["error"]
        assert before == after  # no backup litter
        assert result["failures"][0]["expected_snippet"]

    def test_media_source_coding_refused(self, setup_server, qualcoder_db_path):
        con = sqlite3.connect(str(Path(qualcoder_db_path) / "data.qda"))
        con.execute(
            "INSERT INTO source (id, name, fulltext, mediapath, memo, owner, date) "
            "VALUES (7, 'pic.jpg', NULL, '/images/pic.jpg', '', 't', '2024')"
        )
        con.commit()
        con.close()
        server.select_project(qualcoder_db_path)
        session = self._approved_session(
            setup_server, qualcoder_db_path,
            file_id=7, file_name="pic.jpg", start_pos=0, end_pos=10,
            segment_text="junk here!",
        )
        result = json.loads(server.apply_codings(session.session_id, create_backup=False))
        assert "not a text source" in json.dumps(result["failures"])

    def test_applied_status_prevents_double_apply(self, setup_server, qualcoder_db_path):
        session = self._approved_session(setup_server, qualcoder_db_path)
        out = server.apply_codings(session.session_id, create_backup=False)
        assert "CODINGS APPLIED" in out
        # second run: clean explanation, not a unique-constraint blowup
        result = json.loads(server.apply_codings(session.session_id, create_backup=False))
        assert "already applied" in result["error"]
        assert result["statistics"]["applied"] == 1

    def test_important_written_as_null(self, setup_server, qualcoder_db_path):
        session = self._approved_session(setup_server, qualcoder_db_path)
        server.apply_codings(session.session_id, create_backup=False)
        con = sqlite3.connect(str(Path(qualcoder_db_path) / "data.qda"))
        row = con.execute(
            "SELECT important FROM code_text ORDER BY ctid DESC LIMIT 1"
        ).fetchone()
        con.close()
        assert row[0] is None  # QualCoder domain is {NULL, 1}, never 0


# =============================================================================
# QualCoder heartbeat lock protocol
# =============================================================================

class TestQualcoderLockProtocol:

    def test_active_lock_refuses_write(self, setup_server, qualcoder_db_path):
        lock = _lock_file(qualcoder_db_path)
        lock.write_text(f"someone\n{time.time()}", encoding="utf-8")
        try:
            result = json.loads(server.import_text_file(
                "new.txt", "some new content", create_backup=False))
            assert "open in QualCoder" in result["error"]
            assert "someone" in result["error"]
        finally:
            lock.unlink()

    def test_stale_lock_allows_write_and_is_left_alone(self, setup_server, qualcoder_db_path):
        lock = _lock_file(qualcoder_db_path)
        lock.write_text(f"someone\n{time.time() - 60}", encoding="utf-8")
        try:
            result = json.loads(server.import_text_file(
                "stale_ok.txt", "content body", create_backup=False))
            assert result["success"] is True
            assert lock.exists()  # stale foreign lock untouched
            assert "someone" in lock.read_text(encoding="utf-8")
        finally:
            lock.unlink()

    def test_our_lock_removed_after_write(self, setup_server, qualcoder_db_path):
        result = json.loads(server.import_text_file(
            "clean.txt", "content body", create_backup=False))
        assert result["success"] is True
        assert not _lock_file(qualcoder_db_path).exists()

    def test_lock_state_helper(self, setup_server, qualcoder_db_path):
        folder = validate_qda_path(qualcoder_db_path).parent
        assert qualcoder_lock_state(folder)[0] == "absent"
        lock = _lock_file(qualcoder_db_path)
        lock.write_text(f"u\n{time.time()}", encoding="utf-8")
        assert qualcoder_lock_state(folder) == ("active", "u")
        lock.write_text(f"u\n{time.time() - 31}", encoding="utf-8")
        assert qualcoder_lock_state(folder)[0] == "stale"
        lock.unlink()

    def test_backup_excludes_lock_files(self, setup_server, qualcoder_db_path):
        lock = _lock_file(qualcoder_db_path)
        lock.write_text(f"u\n{time.time() - 60}", encoding="utf-8")  # stale so writes proceed
        try:
            backup = backup_project(qualcoder_db_path)
            assert not (backup / QUALCODER_LOCK_FILENAME).exists()
            shutil.rmtree(backup)
        finally:
            lock.unlink()

    def test_select_project_warns_when_open(self, setup_server, qualcoder_db_path):
        lock = _lock_file(qualcoder_db_path)
        lock.write_text(f"gemma\n{time.time()}", encoding="utf-8")
        try:
            result = json.loads(server.select_project(qualcoder_db_path))
            assert result["success"] is True
            assert "gemma" in result["warning"]
        finally:
            lock.unlink()


# =============================================================================
# Recovery tools
# =============================================================================

class TestRecoveryTools:

    def test_delete_coding_roundtrip(self, setup_server, qualcoder_db_path):
        result = json.loads(server.delete_coding(1, create_backup=False))
        assert result["success"] is True
        assert result["deleted_coding"]["code_name"] == "Stress"
        con = sqlite3.connect(str(Path(qualcoder_db_path) / "data.qda"))
        assert con.execute(
            "SELECT COUNT(*) FROM code_text WHERE ctid = 1").fetchone()[0] == 0
        con.close()
        assert server.db.read_only is True

    def test_delete_coding_unknown_id(self, setup_server):
        result = json.loads(server.delete_coding(9999, create_backup=False))
        assert "does not exist" in result["error"]

    def test_list_backups_both_families(self, setup_server, qualcoder_db_path):
        parent = Path(qualcoder_db_path).parent
        stem = Path(qualcoder_db_path).stem
        mcp_b = parent / f"{stem}_backup_20260101_000000.qda"
        qc_b = parent / f"{stem}_BKUP_20260101_00.qda"
        shutil.copytree(qualcoder_db_path, mcp_b)
        shutil.copytree(qualcoder_db_path, qc_b)
        result = json.loads(server.list_backups())
        kinds = {b["kind"] for b in result["backups"]}
        assert kinds == {"mcp", "qualcoder"}

    def test_restore_requires_confirmation(self, setup_server, qualcoder_db_path):
        parent = Path(qualcoder_db_path).parent
        stem = Path(qualcoder_db_path).stem
        backup = parent / f"{stem}_backup_20260101_000001.qda"
        shutil.copytree(qualcoder_db_path, backup)
        result = json.loads(server.restore_backup(str(backup)))
        assert result["requires_confirmation"] is True

    def test_restore_foreign_path_refused(self, setup_server, tmp_path, qualcoder_db_path):
        foreign = tmp_path / "unrelated.qda"
        shutil.copytree(qualcoder_db_path, foreign)
        result = json.loads(server.restore_backup(str(foreign), confirm=True))
        assert "Not a backup of the currently open project" in result["error"]

    def test_restore_roundtrip_with_safety_backup(self, setup_server, qualcoder_db_path):
        parent = Path(qualcoder_db_path).parent
        stem = Path(qualcoder_db_path).stem
        backup = parent / f"{stem}_backup_20260101_000002.qda"
        shutil.copytree(qualcoder_db_path, backup)
        # mutate current state
        json.loads(server.import_text_file("extra.txt", "extra content",
                                           create_backup=False))
        result = json.loads(server.restore_backup(str(backup), confirm=True))
        assert result["success"] is True
        assert "_prerestore" in result["safety_backup"]
        # the imported file is gone after the restore
        con = sqlite3.connect(str(Path(qualcoder_db_path) / "data.qda"))
        count = con.execute(
            "SELECT COUNT(*) FROM source WHERE name='extra.txt'").fetchone()[0]
        con.close()
        assert count == 0


# =============================================================================
# Case linkage
# =============================================================================

class TestCaseLinkage:

    def test_import_with_case_link(self, setup_server, qualcoder_db_path):
        result = json.loads(server.import_text_file(
            "dana.txt", "Dana talks at length here.",
            case_name="case a", create_backup=False))
        assert result["success"] is True
        link = result["linked_to_case"]
        assert link["case_name"] == "Case A"
        # QualCoder GUI convention: pos1 = len(fulltext) - 1
        assert link["position_end"] == len("Dana talks at length here.") - 1

    def test_import_unknown_case_no_write(self, setup_server, qualcoder_db_path):
        result = json.loads(server.import_text_file(
            "x.txt", "body", case_name="Nobody", create_backup=False))
        assert "not found" in result["error"]
        con = sqlite3.connect(str(Path(qualcoder_db_path) / "data.qda"))
        count = con.execute(
            "SELECT COUNT(*) FROM source WHERE name='x.txt'").fetchone()[0]
        con.close()
        assert count == 0

    def test_link_tool_and_duplicate(self, setup_server, qualcoder_db_path):
        result = json.loads(server.link_file_to_case(2, case_name="Case A",
                                                     create_backup=False))
        assert result["success"] is True
        result = json.loads(server.link_file_to_case(2, case_name="Case A",
                                                     create_backup=False))
        assert "already linked" in result["error"]


# =============================================================================
# REFI-QDA export tool
# =============================================================================

class TestExportRefiQda:

    def test_project_export_conformance_essentials(self, setup_server, tmp_path):
        out = tmp_path / "export.qdpx"
        result = json.loads(server.export_refi_qda(str(out)))
        assert result["success"] is True
        z = zipfile.ZipFile(out)
        names = z.namelist()
        assert "project.qde" in names
        raw = z.read("project.qde")
        assert not raw.startswith(b"\xef\xbb\xbf")  # no BOM
        import xml.etree.ElementTree as ET
        root = ET.fromstring(raw)
        assert "name" in root.attrib  # unqualified per schema
        NS = "{urn:QDA-XML:project:1.0}"
        for src_el in root.findall(f".//{NS}TextSource"):
            path = src_el.get("plainTextPath")
            assert path.startswith("internal://") and path.endswith(".txt")
            member = f"sources/{path.split('internal://')[1]}"
            assert member in names
        # GUID uniqueness
        guids = [e.get("guid") for e in root.iter() if e.get("guid")]
        assert len(guids) == len(set(guids))

    def test_output_path_validation(self, setup_server, tmp_path):
        assert "must end in .qdpx" in server.export_refi_qda(str(tmp_path / "x.zip"))
        assert "does not exist" in server.export_refi_qda(
            str(tmp_path / "nodir" / "x.qdpx"))
        out = tmp_path / "dup.qdpx"
        json.loads(server.export_refi_qda(str(out)))
        assert "already exists" in server.export_refi_qda(str(out))
        assert json.loads(server.export_refi_qda(str(out), overwrite=True))["success"]


# =============================================================================
# Project validity and discovery
# =============================================================================

class TestValidityAndDiscovery:

    def test_rejects_uppercase_and_bare_files(self, tmp_path, qualcoder_db_path):
        upper = tmp_path / "SHOUT.QDA"
        shutil.copytree(qualcoder_db_path, upper)
        with pytest.raises(ValueError, match="lowercase"):
            validate_qda_path(str(upper))
        bare = tmp_path / "loose.qda"
        shutil.copyfile(Path(qualcoder_db_path) / "data.qda", bare)
        with pytest.raises(ValueError, match="data.qda"):
            validate_qda_path(str(bare))

    def test_discovery_skips_backups_and_inner_data(self, tmp_path, qualcoder_db_path):
        base = tmp_path / "discovery"
        base.mkdir()
        shutil.copytree(qualcoder_db_path, base / "real_project.qda")
        shutil.copytree(qualcoder_db_path, base / "real_project_backup_20260101_000000.qda")
        shutil.copytree(qualcoder_db_path, base / "real_project_BKUP_2026010100.qda")
        projects = server.discover_projects([str(base)])
        names = [p["name"] for p in projects]
        assert names == ["real_project"]

    def test_same_second_backups_unique(self, qualcoder_db_path):
        b1 = backup_project(qualcoder_db_path)
        b2 = backup_project(qualcoder_db_path)
        try:
            assert b1 != b2
            assert b1.exists() and b2.exists()
        finally:
            shutil.rmtree(b1)
            shutil.rmtree(b2)


# =============================================================================
# v14 write gate
# =============================================================================

class TestWriteVersionGate:

    def test_pre_v14_write_refused(self, setup_server, qualcoder_db_path, tmp_path):
        """A REAL pre-v14 project (no coder_names table, upstream's own v14
        marker) refuses writes. The version string alone no longer gates:
        support is decided by capability probes (S1)."""
        old = tmp_path / "old_v13.qda"
        shutil.copytree(qualcoder_db_path, old)
        con = sqlite3.connect(str(old / "data.qda"))
        con.execute("DROP TABLE coder_names")
        con.execute("UPDATE project SET databaseversion = 'v13'")
        con.commit()
        con.close()
        json.loads(server.select_project(str(old)))
        result = json.loads(server.import_text_file(
            "y.txt", "content", create_backup=False))
        assert "pre-v14 schema" in result["error"]
        assert "QualCoder 3.8 or newer" in result["error"]

    def test_restamped_version_string_does_not_gate(self, setup_server,
                                                    qualcoder_db_path,
                                                    tmp_path):
        """A capability-v14 project mis-stamped 'v13' stays writable: the
        probes, not the string, decide (S1)."""
        old = tmp_path / "stamped_v13.qda"
        shutil.copytree(qualcoder_db_path, old)
        con = sqlite3.connect(str(old / "data.qda"))
        con.execute("UPDATE project SET databaseversion = 'v13'")
        con.commit()
        con.close()
        json.loads(server.select_project(str(old)))
        result = json.loads(server.import_text_file(
            "y.txt", "content", create_backup=False))
        assert result.get("success") is True
