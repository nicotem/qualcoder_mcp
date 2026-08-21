"""v17 WS4 parity hardening (behavior-delta.md): T12-T16.

T12 dual-convention link dedupe lives with the existing link tests
(test_v08_attributes / test_qa_round2_*, updated for the unified len
convention); this file covers the CR normalization (T13), backup sidecar
ignores (T14), backup-notes scoping (T15), and journal-domain plus
system-owner tolerance (T16 / W15 / W16).
"""

import json
import sqlite3
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server


def _sql(project_path, sql, args=()):
    conn = sqlite3.connect(str(Path(project_path) / "data.qda"))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return rows


def _exec(project_path, sql, args=()):
    conn = sqlite3.connect(str(Path(project_path) / "data.qda"))
    conn.execute(sql, args)
    conn.commit()
    conn.close()


# ===========================================================================
# T13 / P7: import normalization now includes lone \r (master parity)
# ===========================================================================

class TestT13ImportNormalization:

    def test_crlf_cr_and_bom_normalized(self, setup_server,
                                        qualcoder_db_path):
        out = json.loads(server.import_text_file(
            "norm.txt", "﻿line one\r\ntwo\rthree",
            create_backup=False))
        assert out["success"] is True
        row = _sql(qualcoder_db_path,
                   "SELECT fulltext FROM source WHERE name='norm.txt'")[0]
        assert row["fulltext"] == "line one\ntwo\nthree"
        assert out["content_length"] == len("line one\ntwo\nthree")

    def test_positions_validate_against_normalized_text(self, setup_server,
                                                        qualcoder_db_path):
        json.loads(server.import_text_file(
            "norm2.txt", "alpha\rbeta gamma\r\ndelta", create_backup=False))
        analyze = server.analyze_for_coding([1])
        sid = analyze.split("Session ID: `")[1].split("`")[0]
        fid = _sql(qualcoder_db_path,
                   "SELECT id FROM source WHERE name='norm2.txt'")[0]["id"]
        out = json.loads(server.record_suggestions(sid, [{
            "file_id": fid, "code_name": "Stress",
            "segment_text": "beta gamma",
        }]))
        assert out["recorded_count"] == 1
        rec = out["recorded"][0]
        # positions live in the NORMALIZED text ("alpha\nbeta gamma\n...")
        assert rec["start_pos"] == len("alpha\n")


# ===========================================================================
# T14 / D3: backup ignores sqlite sidecars (master app.py:1619-1625)
# ===========================================================================

class TestT14BackupSidecars:

    def test_sidecars_and_lock_excluded_from_backup(self, setup_server,
                                                    qualcoder_db_path):
        proj = Path(qualcoder_db_path)
        (proj / "search.sqlite").write_bytes(b"vectorstore")
        (proj / "search.sqlite-wal").write_bytes(b"wal")
        (proj / "search.sqlite-journal").write_bytes(b"j")
        (proj / "extra.sqlite-shm").write_bytes(b"shm")
        (proj / "project_in_use.lock").write_text("x\n0", encoding="utf-8")
        (proj / "keepme.txt").write_text("keep", encoding="utf-8")

        out = json.loads(server.set_memo("code", 1, "trigger backup",
                                         create_backup=True))
        assert out.get("success") is True
        backup = Path(out["backup_path"])
        names = {p.name for p in backup.rglob("*")}
        assert "keepme.txt" in names
        assert "data.qda" in names
        for excluded in ("search.sqlite", "search.sqlite-wal",
                         "search.sqlite-journal", "extra.sqlite-shm",
                         "project_in_use.lock"):
            assert excluded not in names, excluded


# ===========================================================================
# T15 / D4: list_backups notes are version-scoped
# ===========================================================================

class TestT15BackupNotes:

    def test_notes_scope_settings_directory_to_382(self, setup_server,
                                                   qualcoder_db_path):
        out = json.loads(server.list_backups())
        notes = " ".join(out["notes"])
        assert "3.8.0 through 3.8.2" in notes
        assert "next to the project again" in notes

    def test_master_style_sibling_bkup_listed(self, setup_server,
                                              qualcoder_db_path):
        proj = Path(qualcoder_db_path)
        sibling = proj.parent / f"{proj.stem}_BKUP_20260821_1200.qda"
        sibling.mkdir()
        (sibling / "data.qda").write_bytes(b"SQLite format 3\x00")
        out = json.loads(server.list_backups())
        kinds = {b["name"]: b["kind"] for b in out["backups"]}
        assert kinds.get(sibling.name) == "qualcoder"


# ===========================================================================
# T16 / W15 / W16: journal attribute domain + system owner tolerance
# ===========================================================================

@pytest.fixture
def master_touched_project(setup_server, qualcoder_db_path):
    """A project a master build has touched: journal-domain attributes and
    the two new system owners ('AI Agent', emoji speaker coder)."""
    _exec(qualcoder_db_path,
          "INSERT INTO attribute_type VALUES "
          "('Phase', '2026-08-21', 'Owner', '', 'journal', 'character')")
    _exec(qualcoder_db_path,
          "INSERT INTO attribute VALUES "
          "(50, 'Phase', 'journal', 'pilot', 1, '2026-08-21', 'Owner')")
    _exec(qualcoder_db_path,
          "INSERT INTO code_text (cid, fid, seltext, pos0, pos1, owner, date, memo) "
          "VALUES (2, 1, 'I cope by exercising', 57, 77, 'AI Agent', '2026-08-21', '')")
    _exec(qualcoder_db_path,
          "INSERT INTO code_text (cid, fid, seltext, pos0, pos1, owner, date, memo) "
          "VALUES (1, 1, 'This is interview text', 0, 22, "
          "'\U0001F4CC Speaker coding', '2026-08-21', '')")
    server.switch_project(qualcoder_db_path)
    return qualcoder_db_path


class TestT16JournalAndOwnerTolerance:

    def test_attribute_reads_tolerate_journal_domain(self,
                                                     master_touched_project):
        out = json.loads(server.list_attribute_types())["attributes"]
        journal_rows = [a for a in out if a["applies_to"] == "journal"]
        assert len(journal_rows) == 1          # listed, not erroring
        # file/case attribute surfaces unpolluted by the journal row
        case_attrs = json.loads(server.get_case_attributes(1))["attributes"]
        assert all(a["name"] != "Phase" for a in case_attrs)
        file_attrs = json.loads(server.get_file_attributes(1))["attributes"]
        assert all(a["name"] != "Phase" for a in file_attrs)

    def test_query_by_attribute_unaffected_by_journal_rows(
            self, master_touched_project):
        out = json.loads(server.query_by_attribute("Age", "30"))
        assert len(out) == 1                   # fixture case still found

    def test_import_placeholders_exclude_journal_domain(
            self, master_touched_project):
        out = json.loads(server.import_text_file("t16.txt", "body",
                                                 create_backup=False))
        assert out["success"] is True
        # no placeholder for the journal attribute on a FILE
        rows = _sql(master_touched_project,
                    "SELECT * FROM attribute WHERE name='Phase' "
                    "AND attr_type != 'journal'")
        assert rows == []

    def test_read_tools_tolerate_system_owners(self, master_touched_project):
        """W16: owner names are opaque strings, emoji included."""
        out = json.loads(server.get_coding_frequencies())
        assert out["total_coded_segments"] >= 4
        analysis = json.loads(server.analyze_file_with_coding(1))
        owners = {s["coder"] if "coder" in s else s.get("owner")
                  for s in analysis["coded_segments"]}
        assert "AI Agent" in owners
        assert "\U0001F4CC Speaker coding" in owners
        segs = json.loads(server.get_coded_segments(1))
        assert "error" not in segs

    def test_frequencies_export_includes_system_owner_columns(
            self, master_touched_project, tmp_path):
        out = json.loads(server.export_frequencies_csv(
            str(tmp_path / "freq.csv")))
        assert "AI Agent" in out["coders"]
        assert "\U0001F4CC Speaker coding" in out["coders"]
