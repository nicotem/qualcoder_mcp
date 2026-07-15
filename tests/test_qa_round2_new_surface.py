"""QA round-2: adversarial tests for the NEW tool surface of fix/write-path.

Targets: record_suggestions, delete_coding, list_backups, restore_backup,
export_refi_qda, copy_project_to_workspace, link_file_to_case, and the
QualCoder heartbeat lock protocol.
"""

import json
import os
import shutil
import sqlite3
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server
import qualcoder_mcp.database as database
from qualcoder_mcp.database import (
    QualcoderDatabase,
    QUALCODER_LOCK_FILENAME,
    QUALCODER_LOCK_TIMEOUT,
    qualcoder_lock_state,
    validate_qda_path,
)


FULLTEXT = "This is interview text. I feel stressed about deadlines. I cope by exercising."
NS = "urn:QDA-XML:project:1.0"


def _data_qda(project_path) -> Path:
    return Path(project_path) / "data.qda"


def _sid(*file_ids):
    out = server.analyze_for_coding(list(file_ids) or [1])
    return out.split("Session ID: `")[1].split("`")[0]


def _sql(project_path, query, args=()):
    conn = sqlite3.connect(str(_data_qda(project_path)))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(query, args).fetchall()
    conn.close()
    return rows


def _exec(project_path, query, args=()):
    conn = sqlite3.connect(str(_data_qda(project_path)))
    conn.execute(query, args)
    conn.commit()
    conn.close()


def _backups(project_path):
    folder = Path(project_path)
    return sorted(p for p in folder.parent.glob(f"{folder.stem}_backup_*.qda"))


def _lock(project_path) -> Path:
    return validate_qda_path(project_path).parent / QUALCODER_LOCK_FILENAME


# =============================================================================
# record_suggestions — malformed input gauntlet
# =============================================================================

class TestRecordSuggestionsHostileInput:

    def test_non_list_and_empty(self, setup_server):
        sid = _sid()
        assert "error" in json.loads(server.record_suggestions(sid, []))
        assert "error" in json.loads(
            server.record_suggestions(sid, {"file_id": 1}))  # dict, not list

    def test_unknown_session_and_traversal_id(self, setup_server):
        out = json.loads(server.record_suggestions(
            "00000000-0000-0000-0000-000000000000", [{"file_id": 1}]))
        assert "error" in out
        out2 = json.loads(server.record_suggestions(
            "../../../etc/passwd", [{"file_id": 1}]))
        assert "error" in out2

    def test_per_item_garbage_is_rejected_not_fatal(self, setup_server):
        """One good item among garbage: partial success, per-item reasons."""
        sid = _sid()
        rec = json.loads(server.record_suggestions(sid, [
            "not an object",
            {"code_name": "Stress", "segment_text": "x"},          # no file_id
            {"file_id": True, "code_name": "Stress",
             "segment_text": "x"},                                  # bool id
            {"file_id": 424242, "code_name": "Stress",
             "segment_text": "x"},                                  # ghost file
            {"file_id": 1, "segment_text": "x"},                    # no code
            {"file_id": 1, "code_name": "NoSuchCode",
             "segment_text": "x"},                                  # ghost code
            {"file_id": 1, "code_id": 424242, "segment_text": "x"},
            {"file_id": 1, "code_name": "Stress",
             "segment_text": "   "},                                # blank text
            {"file_id": 1, "code_name": "Stress",
             "segment_text": "I feel stressed", "confidence": "hi"},
            # the one good item
            {"file_id": 1, "code_name": "Stress",
             "segment_text": FULLTEXT[24:55]},
        ]))
        assert rec["recorded_count"] == 1
        assert rec["rejected_count"] == 9
        reasons = " | ".join(r["reason"] for r in rec["rejected"])
        assert "file_id" in reasons and "code" in reasons
        # ghost-code rejection helps the model recover
        ghost = next(r for r in rec["rejected"] if "NoSuchCode" in r.get("reason", ""))
        assert "available_codes" in ghost

    def test_bool_positions_fall_back_to_search(self, setup_server):
        sid = _sid()
        rec = json.loads(server.record_suggestions(sid, [{
            "file_id": 1, "code_name": "Stress",
            "start_pos": True, "end_pos": True,
            "segment_text": FULLTEXT[24:55],
        }]))
        assert rec["recorded_count"] == 1
        assert rec["recorded"][0]["start_pos"] == 24

    def test_replace_keeps_non_pending(self, setup_server):
        sid = _sid()
        rec = json.loads(server.record_suggestions(sid, [
            {"file_id": 1, "code_name": "Stress",
             "segment_text": FULLTEXT[24:55]},
            {"file_id": 1, "code_name": "Coping",
             "segment_text": "I cope by exercising"},
        ]))
        keep, drop = rec["recorded"][0]["guid"], rec["recorded"][1]["guid"]
        server.update_suggestion_status(sid, approve=[keep])

        rec2 = json.loads(server.record_suggestions(sid, [{
            "file_id": 1, "code_name": "Stress",
            "segment_text": "This is interview text."}], replace=True))
        assert rec2["replaced_pending"] == 1
        stats = rec2["statistics"]
        assert stats["approved"] == 1     # approved survived the replace
        assert stats["pending"] == 1      # only the fresh recording

    def test_unicode_emoji_file_gets_position_safety_warning(
            self, setup_server, qualcoder_db_path):
        emoji_text = "Intro 😀 emoji. The team was there. I feel very stressed today."
        _exec(qualcoder_db_path,
              "INSERT INTO source (id, name, fulltext) VALUES (70, 'emoji.txt', ?)",
              (emoji_text,))
        server.switch_project(qualcoder_db_path)

        seg = "I feel very stressed"
        sid = _sid(70)
        rec = json.loads(server.record_suggestions(sid, [{
            "file_id": 70, "code_name": "Stress", "segment_text": seg,
        }]))
        assert rec["recorded_count"] == 1
        assert "position_safety_warning" in rec
        assert "emoji.txt" in rec["position_safety_warning"]
        # positions are code-point offsets and the slice matches
        r = rec["recorded"][0]
        assert emoji_text[r["start_pos"]:r["end_pos"]] == seg

    def test_ambiguous_over_ten_occurrences_message(self, setup_server,
                                                    qualcoder_db_path):
        _exec(qualcoder_db_path,
              "INSERT INTO source (id, name, fulltext) VALUES (71, 'rep.txt', ?)",
              ("repeat me. " * 12,))
        server.switch_project(qualcoder_db_path)
        sid = _sid(71)
        rec = json.loads(server.record_suggestions(sid, [{
            "file_id": 71, "code_name": "Stress", "segment_text": "repeat me.",
        }]))
        assert rec["recorded_count"] == 0
        assert "more than 10" in rec["rejected"][0]["reason"]


# =============================================================================
# delete_coding
# =============================================================================

class TestDeleteCoding:

    def test_nonexistent_and_negative_ctid(self, setup_server):
        assert "error" in json.loads(server.delete_coding(424242))
        assert "error" in json.loads(server.delete_coding(-1))

    def test_deletes_exactly_one_code_text_row_nothing_else(
            self, setup_server, qualcoder_db_path):
        """COMPAT W13: snapshot every table; only code_text changes, by 1."""
        tables = [r["name"] for r in _sql(
            qualcoder_db_path,
            "SELECT name FROM sqlite_master WHERE type='table'")]
        before = {t: _sql(qualcoder_db_path, f"SELECT COUNT(*) as n FROM {t}")[0]["n"]
                  for t in tables}

        out = json.loads(server.delete_coding(1))
        assert out["success"] is True
        assert out["deleted_coding"]["coding_id"] == 1
        assert out["deleted_coding"]["code_name"] == "Stress"

        after = {t: _sql(qualcoder_db_path, f"SELECT COUNT(*) as n FROM {t}")[0]["n"]
                 for t in tables}
        for t in tables:
            if t == "code_text":
                assert after[t] == before[t] - 1
            else:
                assert after[t] == before[t], f"table {t} changed"

    def test_orphaned_and_av_linked_rows_deletable(self, setup_server,
                                                   qualcoder_db_path):
        _exec(qualcoder_db_path,
              "INSERT INTO code_text (ctid, cid, fid, seltext, pos0, pos1, owner, avid) "
              "VALUES (90, 1, 999, 'orphan', 0, 6, 'qa', NULL)")
        _exec(qualcoder_db_path,
              "INSERT INTO code_text (ctid, cid, fid, seltext, pos0, pos1, owner, avid) "
              "VALUES (91, 2, 1, 'This is in', 0, 10, 'qa', 7)")
        server.switch_project(qualcoder_db_path)

        assert json.loads(server.delete_coding(90))["success"] is True
        assert json.loads(server.delete_coding(91))["success"] is True
        assert _sql(qualcoder_db_path,
                    "SELECT COUNT(*) as n FROM code_text "
                    "WHERE ctid IN (90, 91)")[0]["n"] == 0


# =============================================================================
# link_file_to_case
# =============================================================================

class TestLinkFileToCase:

    def test_text_file_link_uses_gui_len_minus_one(self, setup_server,
                                                   qualcoder_db_path):
        # file 2 ('notes.txt') is not linked to any case in the fixture
        out = json.loads(server.link_file_to_case(2, case_name="case a"))
        assert out["success"] is True
        link = out["link"]
        notes_len = len("Field notes from observation session.")
        assert link["position_start"] == 0
        assert link["position_end"] == notes_len - 1  # QualCoder's -1 quirk

    def test_duplicate_link_refused_row_count_stable(self, setup_server,
                                                     qualcoder_db_path):
        json.loads(server.link_file_to_case(2, case_id=1))
        before = _sql(qualcoder_db_path,
                      "SELECT COUNT(*) as n FROM case_text")[0]["n"]
        out = json.loads(server.link_file_to_case(2, case_id=1))
        assert "already linked" in out["error"]
        assert _sql(qualcoder_db_path,
                    "SELECT COUNT(*) as n FROM case_text")[0]["n"] == before

    def test_media_file_links_zero_zero(self, setup_server, qualcoder_db_path):
        _exec(qualcoder_db_path,
              "INSERT INTO source (id, name, fulltext, mediapath) "
              "VALUES (80, 'clip.mp4', NULL, '/video/clip.mp4')")
        server.switch_project(qualcoder_db_path)
        out = json.loads(server.link_file_to_case(80, case_id=1))
        assert out["link"]["position_start"] == 0
        assert out["link"]["position_end"] == 0

    def test_unknown_case_and_file(self, setup_server):
        out = json.loads(server.link_file_to_case(2, case_name="Nobody"))
        assert "not found" in out["error"]
        assert "available_cases" in out
        assert "error" in json.loads(server.link_file_to_case(424242, case_id=1))
        assert "error" in json.loads(server.link_file_to_case(2))  # neither given

    def test_import_with_case_link_is_atomic(self, setup_server,
                                             qualcoder_db_path):
        out = json.loads(server.import_text_file(
            "linked_interview.txt", "Some interview content here.",
            case_name="Case A"))
        assert out["success"] is True
        assert out["linked_to_case"]["case_name"] == "Case A"
        rows = _sql(qualcoder_db_path,
                    "SELECT * FROM case_text WHERE fid = ?", (out["file_id"],))
        assert len(rows) == 1
        assert rows[0]["pos1"] == len("Some interview content here.") - 1


# =============================================================================
# restore_backup — the tool that intentionally destroys state
# =============================================================================

class TestRestoreBackupGates:

    def _make_backup(self, qualcoder_db_path) -> Path:
        """Create a genuine MCP backup via a real write."""
        json.loads(server.import_text_file(
            f"marker_{time.time_ns()}.txt", "backup marker"))
        return _backups(qualcoder_db_path)[-1]

    def test_preview_changes_nothing(self, setup_server, qualcoder_db_path):
        backup = self._make_backup(qualcoder_db_path)
        digest_before = _data_qda(qualcoder_db_path).read_bytes()
        out = json.loads(server.restore_backup(str(backup)))
        assert out["requires_confirmation"] is True
        assert _data_qda(qualcoder_db_path).read_bytes() == digest_before

    def test_foreign_and_trick_paths_refused(self, setup_server,
                                             qualcoder_db_path, tmp_path):
        backup = self._make_backup(qualcoder_db_path)

        # a "backup" of a DIFFERENT project in the same directory
        other = tmp_path / "unrelated_backup_20990101_000000.qda"
        shutil.copytree(qualcoder_db_path, other)
        out = json.loads(server.restore_backup(str(other), confirm=True))
        assert "error" in out

        # nested path outside the project parent
        nested_dir = tmp_path / "sub"
        nested_dir.mkdir()
        nested = nested_dir / f"{Path(qualcoder_db_path).stem}_backup_20990101_000000.qda"
        shutil.copytree(backup, nested)
        out = json.loads(server.restore_backup(str(nested), confirm=True))
        assert "error" in out

        # symlink with a correct-looking name pointing elsewhere
        link = tmp_path / f"{Path(qualcoder_db_path).stem}_backup_20990102_000000.qda"
        os.symlink(nested, link)
        out = json.loads(server.restore_backup(str(link), confirm=True))
        assert "error" in out

        # correct-looking name but not a valid project inside
        hollow = tmp_path / f"{Path(qualcoder_db_path).stem}_backup_20990103_000000.qda"
        hollow.mkdir()
        out = json.loads(server.restore_backup(str(hollow), confirm=True))
        assert "error" in out

        # nonexistent path
        out = json.loads(server.restore_backup(
            str(tmp_path / "nope_backup_1.qda"), confirm=True))
        assert "error" in out

    def test_refuses_while_qualcoder_open_or_sqlite_locked(
            self, setup_server, qualcoder_db_path):
        backup = self._make_backup(qualcoder_db_path)
        lock = _lock(qualcoder_db_path)
        lock.write_text(f"livecoder\n{time.time()}")
        try:
            out = json.loads(server.restore_backup(str(backup), confirm=True))
            assert "livecoder" in out["error"]
        finally:
            lock.unlink()

        other = sqlite3.connect(str(_data_qda(qualcoder_db_path)))
        other.execute("BEGIN IMMEDIATE")
        try:
            out = json.loads(server.restore_backup(str(backup), confirm=True))
            assert "error" in out
        finally:
            other.rollback()
            other.close()

    def test_confirmed_restore_roundtrip(self, setup_server, qualcoder_db_path):
        backup = self._make_backup(qualcoder_db_path)
        # damage the project after the backup
        json.loads(server.import_text_file("post_backup.txt", "to be rolled back",
                                           create_backup=False))
        # plant a stray lock file inside the backup (old-format backups had them)
        (backup / QUALCODER_LOCK_FILENAME).write_text("ghost\n1.0")

        out = json.loads(server.restore_backup(str(backup), confirm=True))
        assert out["success"] is True, out
        # the post-backup file is gone
        res = json.loads(server.search_files("post_backup"))
        assert res["total_matches"] == 0
        # safety backup exists and is marked
        safety = Path(out["safety_backup"])
        assert safety.exists() and "_prerestore" in safety.name
        # stray lock stripped from the restored project
        assert not _lock(qualcoder_db_path).exists()
        # server is reconnected and read-only
        assert server.db is not None and server.db.read_only

    def test_qualcoder_bkup_family_recognized(self, setup_server,
                                              qualcoder_db_path, tmp_path):
        stem = Path(qualcoder_db_path).stem
        qc_backup = Path(qualcoder_db_path).parent / f"{stem}_BKUP_20260701_09.qda"
        shutil.copytree(qualcoder_db_path, qc_backup)

        listed = json.loads(server.list_backups())
        kinds = {b["name"]: b["kind"] for b in listed["backups"]}
        assert kinds[qc_backup.name] == "qualcoder"
        assert any("audio/video" in n for n in listed["notes"])

        preview = json.loads(server.restore_backup(str(qc_backup)))
        assert preview["requires_confirmation"] is True
        assert "note" in preview  # A/V caveat for QualCoder-made backups


# =============================================================================
# export_refi_qda — conformance and hostile data
# =============================================================================

class TestExportRefiQda:

    def _poison_project(self, qualcoder_db_path):
        _exec(qualcoder_db_path,
              "UPDATE code_name SET name = ?, memo = ? WHERE cid = 1",
              ("bad\x02code\x0c", "memo\x00with\x08junk"))
        _exec(qualcoder_db_path,
              "UPDATE source SET memo = ? WHERE id = 1", ("file\x0cmemo",))
        server.switch_project(qualcoder_db_path)

    def test_output_path_attacks(self, setup_server, qualcoder_db_path, tmp_path):
        inside = Path(qualcoder_db_path) / "export.qdpx"
        assert "Refusing" in json.loads(
            server.export_refi_qda(str(inside)))["error"]
        assert "error" in json.loads(
            server.export_refi_qda(str(tmp_path / "no_dir" / "x.qdpx")))
        assert "error" in json.loads(
            server.export_refi_qda(str(tmp_path / "wrong.zip")))
        target = tmp_path / "dup.qdpx"
        target.write_bytes(b"existing")
        assert "already exists" in json.loads(
            server.export_refi_qda(str(target)))["error"]
        assert json.loads(server.export_refi_qda(
            str(target), overwrite=True)).get("success") is True

    def test_poison_export_full_conformance(self, setup_server,
                                            qualcoder_db_path, tmp_path):
        """Control chars everywhere + the QualCoder importer's hard
        requirements replayed on the archive (X1/X3/X5/X6/X7/X8)."""
        self._poison_project(qualcoder_db_path)
        out_path = tmp_path / "poison.qdpx"
        result = json.loads(server.export_refi_qda(str(out_path)))
        assert result.get("success") is True, result

        with zipfile.ZipFile(out_path) as z:
            names = z.namelist()
            qde_bytes = z.read("project.qde")
            members = {n: z.read(n) for n in names}

        # X6: no BOM anywhere
        for name, data in members.items():
            assert not data.startswith(b"\xef\xbb\xbf"), name

        root = ET.fromstring(qde_bytes)
        assert root.tag == f"{{{NS}}}Project"
        # X3: unqualified attributes (esp. Project name)
        assert "name" in root.attrib
        for elem in root.iter():
            for key in elem.attrib:
                assert not key.startswith(f"{{{NS}}}"), (elem.tag, key)

        # X1: QualCoder's importer does path.split('internal:/')[1]
        sources = root.findall(f".//{{{NS}}}TextSource")
        assert sources
        for src in sources:
            ptp = src.get("plainTextPath")
            assert ptp.startswith("internal://"), ptp
            member = "sources/" + ptp.split("internal:/")[1].lstrip("/")
            assert member in members, (ptp, names)

        # X8: document-wide GUID uniqueness; targetGUIDs resolve
        guids = []
        declared = set()
        for elem in root.iter():
            if "guid" in elem.attrib:
                guids.append(elem.attrib["guid"])
                declared.add(elem.attrib["guid"])
        assert len(guids) == len(set(guids)), "duplicate guid in document"
        for ref in root.iter(f"{{{NS}}}CodeRef"):
            assert ref.get("targetGUID") in declared

        # X5: selection positions bounds-checked against the actual member
        for src in sources:
            member = "sources/" + src.get("plainTextPath").split("internal:/")[1].lstrip("/")
            text = members[member].decode("utf-8")
            for sel in src.findall(f"{{{NS}}}PlainTextSelection"):
                s, e = int(sel.get("startPosition")), int(sel.get("endPosition"))
                assert 0 <= s < e <= len(text)

        # X7: real UTC timestamps
        stamp = root.get("creationDateTime")
        parsed = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
        assert abs((datetime.now(timezone.utc) - parsed).total_seconds()) < 300

    def test_guid_uniqueness_same_start_different_end(
            self, setup_server, qualcoder_db_path, tmp_path):
        """Two codings, same file/code/start, different ends (round-1 GUID
        seed ignored end_pos -> collision)."""
        _exec(qualcoder_db_path,
              "INSERT INTO code_text (cid, fid, seltext, pos0, pos1, owner) "
              "VALUES (1, 1, ?, 24, 39, 'qa2')", (FULLTEXT[24:39],))
        server.switch_project(qualcoder_db_path)
        out_path = tmp_path / "same_start.qdpx"
        assert json.loads(server.export_refi_qda(str(out_path)))["success"]
        with zipfile.ZipFile(out_path) as z:
            root = ET.fromstring(z.read("project.qde"))
        coding_guids = [c.get("guid") for c in root.iter(f"{{{NS}}}Coding")]
        assert len(coding_guids) == len(set(coding_guids))

    def test_category_hierarchy_nested_not_codable(self, setup_server,
                                                   qualcoder_db_path, tmp_path):
        _exec(qualcoder_db_path,
              "INSERT INTO code_cat (catid, name, supercatid) VALUES (2, 'Child cat', 1)")
        _exec(qualcoder_db_path,
              "UPDATE code_name SET catid = 2 WHERE cid = 1")
        server.switch_project(qualcoder_db_path)
        out_path = tmp_path / "cats.qdpx"
        assert json.loads(server.export_refi_qda(str(out_path)))["success"]
        with zipfile.ZipFile(out_path) as z:
            root = ET.fromstring(z.read("project.qde"))
        codes_root = root.find(f"{{{NS}}}CodeBook/{{{NS}}}Codes")
        top = {c.get("name"): c for c in codes_root.findall(f"{{{NS}}}Code")}
        assert top["Category A"].get("isCodable") == "false"
        child = top["Category A"].find(f"{{{NS}}}Code")
        assert child.get("name") == "Child cat"
        assert child.get("isCodable") == "false"
        leaf = child.find(f"{{{NS}}}Code")
        assert leaf.get("name") == "Stress"
        assert leaf.get("isCodable") == "true"

    def test_stale_reference_fails_loudly_no_partial_file(
            self, setup_server, qualcoder_db_path, tmp_path):
        sid = _sid()
        rec = json.loads(server.record_suggestions(sid, [{
            "file_id": 1, "code_name": "Stress",
            "segment_text": FULLTEXT[24:55]}]))
        assert rec["recorded_count"] == 1
        # delete the code AFTER recording
        _exec(qualcoder_db_path, "DELETE FROM code_name WHERE cid = 1")
        server.switch_project(qualcoder_db_path)

        out_path = tmp_path / "stale.qdpx"
        out = json.loads(server.export_refi_qda(str(out_path), session_id=sid))
        assert "Export validation failed" in out["error"]
        assert not out_path.exists()   # never a partial archive

    def test_empty_project_and_empty_session(self, setup_server,
                                             qualcoder_db_path, tmp_path):
        sid = _sid()
        out = json.loads(server.export_refi_qda(
            str(tmp_path / "empty_session.qdpx"), session_id=sid))
        assert "no suggestions" in out["error"]

        _exec(qualcoder_db_path, "DELETE FROM code_text")
        server.switch_project(qualcoder_db_path)
        out = json.loads(server.export_refi_qda(str(tmp_path / "empty.qdpx")))
        assert "no text codings" in out["error"]


# =============================================================================
# copy_project_to_workspace
# =============================================================================

class TestCopyProjectToWorkspace:

    @pytest.fixture(autouse=True)
    def _sandbox_workspace(self, tmp_path, monkeypatch):
        """Never write into the real ~/Documents workspace from tests."""
        monkeypatch.setattr(database, "DEFAULT_WORKSPACE",
                            tmp_path / "workspace")

    def test_copy_and_uniquify(self, setup_server, qualcoder_db_path):
        out = json.loads(server.copy_project_to_workspace(qualcoder_db_path))
        assert out["success"] is True
        copy1 = Path(out["workspace_copy"])
        assert copy1.exists() and (copy1 / "data.qda").exists()

        out2 = json.loads(server.copy_project_to_workspace(qualcoder_db_path))
        copy2 = Path(out2["workspace_copy"])
        assert copy2 != copy1 and copy2.exists()  # name clash uniquified

        # the copy is selectable and readable
        sel = json.loads(server.select_project(str(copy1)))
        assert sel["success"] is True

    def test_invalid_source_refused(self, setup_server, tmp_path):
        out = json.loads(server.copy_project_to_workspace(str(tmp_path)))
        assert "error" in out
        out2 = json.loads(server.copy_project_to_workspace(
            str(tmp_path / "ghost.qda")))
        assert "error" in out2


# =============================================================================
# Heartbeat lock protocol details
# =============================================================================

class TestHeartbeatProtocol:

    def test_constant_matches_qualcoder(self):
        assert QUALCODER_LOCK_TIMEOUT == 30.0

    def test_all_five_write_tools_refuse_fresh_lock_without_backups(
            self, setup_server, qualcoder_db_path):
        """COMPAT C1: the write gate covers every write tool and no backup
        is created for a refused write."""
        # prepare an approved session and a real backup for restore_backup
        sid = _sid()
        rec = json.loads(server.record_suggestions(sid, [{
            "file_id": 1, "code_name": "Stress",
            "segment_text": FULLTEXT[24:55]}]))
        server.update_suggestion_status(
            sid, approve=[rec["recorded"][0]["guid"]])
        json.loads(server.import_text_file("prelock.txt", "content"))
        backup = _backups(qualcoder_db_path)[-1]

        lock = _lock(qualcoder_db_path)
        lock.write_text(f"activeuser\n{time.time()}")
        n_backups = len(_backups(qualcoder_db_path))
        try:
            attempts = [
                server.apply_codings(sid),
                server.import_text_file("locked_out.txt", "nope"),
                server.link_file_to_case(2, case_id=1),
                server.delete_coding(1),
                server.restore_backup(str(backup), confirm=True),
            ]
            for out in attempts:
                parsed = json.loads(out)
                assert "activeuser" in parsed["error"], parsed
            assert len(_backups(qualcoder_db_path)) == n_backups
        finally:
            lock.unlink()

    def test_garbled_lock_treated_stale_after_retry(self, setup_server,
                                                    qualcoder_db_path):
        lock = _lock(qualcoder_db_path)
        lock.write_text("just one garbled line")
        try:
            state, holder = qualcoder_lock_state(lock.parent)
            assert state == "stale"
            assert holder == "unknown"
        finally:
            lock.unlink()

    def test_lock_file_format_matches_qualcoder(self, setup_server,
                                                qualcoder_db_path, monkeypatch):
        """During our write window the lock is QualCoder's two-line format."""
        from qualcoder_mcp.database import hold_project_lock
        import getpass
        folder = _lock(qualcoder_db_path).parent
        with hold_project_lock(folder) as held:
            assert held is True
            lines = _lock(qualcoder_db_path).read_text().splitlines()
            assert len(lines) == 2
            assert lines[0] == getpass.getuser()
            age = time.time() - float(lines[1])   # parses as epoch float
            assert 0 <= age < 5
        assert not _lock(qualcoder_db_path).exists()  # removed on exit

    def test_own_lock_held_during_write_window(self, setup_server,
                                               qualcoder_db_path, monkeypatch):
        """COMPAT C3: while a write runs, the lock file exists with our user."""
        import getpass
        seen = {}
        original = QualcoderDatabase.add_coding

        def spy(self, *args, **kwargs):
            lock = _lock(qualcoder_db_path)
            seen["exists"] = lock.exists()
            if lock.exists():
                seen["holder"] = lock.read_text().splitlines()[0]
            return original(self, *args, **kwargs)

        monkeypatch.setattr(QualcoderDatabase, "add_coding", spy)

        sid = _sid()
        rec = json.loads(server.record_suggestions(sid, [{
            "file_id": 1, "code_name": "Stress",
            "segment_text": FULLTEXT[24:55]}]))
        server.update_suggestion_status(sid, approve=[rec["recorded"][0]["guid"]])
        result = server.apply_codings(sid)
        assert "CODINGS APPLIED" in result
        assert seen == {"exists": True, "holder": getpass.getuser()}
        assert not _lock(qualcoder_db_path).exists()
