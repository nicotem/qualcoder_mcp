"""QA round-2: mechanical verification of COMPAT_REQUIREMENTS.md items.

Each test names the requirement ID it verifies (W* writes, P* positions,
C* concurrency, D* backups, X* REFI export, V* validity). Items already
pinned elsewhere are not duplicated here — the QA report carries the full
requirement -> test mapping.

xfail tests document requirements (or new findings) that are NOT met at
HEAD: they are expected to fail until the developer addresses them, and
will alert us by XPASSing when fixed.
"""

import json
import re
import shutil
import sqlite3
import time
import unicodedata
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server
from qualcoder_mcp.database import (
    QualcoderDatabase,
    position_safe,
)
from qualcoder_mcp.server import _resolve_segment_positions
import qualcoder_mcp.refi_export as refi_export


FULLTEXT = "This is interview text. I feel stressed about deadlines. I cope by exercising."
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
SRC_DIR = Path(__file__).parent.parent / "src" / "qualcoder_mcp"


def _data_qda(project_path) -> Path:
    return Path(project_path) / "data.qda"


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


def _apply_one(file_id=1, code_name="Stress", start=24, end=55,
               segment=None):
    out = server.analyze_for_coding([file_id])
    sid = out.split("Session ID: `")[1].split("`")[0]
    rec = json.loads(server.record_suggestions(sid, [{
        "file_id": file_id, "code_name": code_name,
        "start_pos": start, "end_pos": end,
        "segment_text": segment if segment is not None else FULLTEXT[start:end],
    }]))
    assert rec["recorded_count"] == 1, rec
    server.update_suggestion_status(sid, approve=[rec["recorded"][0]["guid"]])
    result = server.apply_codings(sid, create_backup=False)
    assert "CODINGS APPLIED" in result, result
    return sid


# =============================================================================
# A. Writes — row contracts
# =============================================================================

class TestWriteRowContracts:

    def test_w1_code_text_row_shape(self, setup_server, qualcoder_db_path):
        """W1: GUI columns exactly; ctid auto, avid NULL."""
        _apply_one()
        row = _sql(qualcoder_db_path,
                   "SELECT * FROM code_text WHERE owner='AI Coding Assistant'")[0]
        assert row["ctid"] == max(
            r["ctid"] for r in _sql(qualcoder_db_path, "SELECT ctid FROM code_text"))
        assert row["avid"] is None
        for col in ("cid", "fid", "seltext", "pos0", "pos1", "owner", "date", "memo"):
            assert row[col] is not None, col

    def test_w2_w3_dates_and_empty_memos(self, setup_server, qualcoder_db_path):
        """W2: local wall-clock '%Y-%m-%d %H:%M:%S'; W3: memo '' never NULL."""
        _apply_one()
        json.loads(server.import_text_file("w2file.txt", "content",
                                           create_backup=False))
        json.loads(server.link_file_to_case(2, case_id=1, create_backup=False))

        checks = [
            ("code_text", "owner='AI Coding Assistant'"),
            ("source", "name='w2file.txt'"),
            ("case_text", "fid=2"),
        ]
        for table, where in checks:
            row = _sql(qualcoder_db_path,
                       f"SELECT date, memo FROM {table} WHERE {where}")[0]
            assert DATE_RE.match(row["date"]), (table, row["date"])
            assert row["memo"] is not None, table

        # attribute placeholder rows too
        _exec(qualcoder_db_path,
              "INSERT INTO attribute_type VALUES ('Doc', '2024', 'T', '', 'file', 'character')")
        server.switch_project(qualcoder_db_path)
        out = json.loads(server.import_text_file("w10file.txt", "content",
                                                 create_backup=False))
        attr = _sql(qualcoder_db_path,
                    "SELECT * FROM attribute WHERE id=? AND attr_type='file'",
                    (out["file_id"],))[0]
        assert attr["value"] == ""            # W10 placeholder
        assert DATE_RE.match(attr["date"])

    def test_w4_important_never_zero(self, setup_server, qualcoder_db_path):
        """W4: important domain is {NULL, 1}."""
        _apply_one()
        wdb = QualcoderDatabase(qualcoder_db_path, read_only=False)
        try:
            wdb.add_coding(1, 2, 0, 12, FULLTEXT[0:12], "qa", important=1)
        finally:
            wdb.close()
        # scope to MCP-written rows: the conftest fixture itself carries a
        # legacy important=0 row (which the MCP must not touch — P6)
        mcp_owners = ("AI Coding Assistant", "qa")
        n = _sql(qualcoder_db_path,
                 "SELECT COUNT(*) as n FROM code_text WHERE important = 0 "
                 "AND owner IN (?, ?)", mcp_owners)[0]["n"]
        assert n == 0
        vals = {r["important"] for r in _sql(
            qualcoder_db_path,
            "SELECT important FROM code_text WHERE owner IN (?, ?)",
            mcp_owners)}
        assert vals <= {None, 1}

    def test_w6_span_validation(self, qualcoder_db_path):
        """W6: zero-length and inverted spans rejected."""
        wdb = QualcoderDatabase(qualcoder_db_path, read_only=False)
        try:
            with pytest.raises(ValueError):
                wdb.add_coding(1, 1, 10, 10, "", "qa")     # zero-length
            with pytest.raises(ValueError):
                wdb.add_coding(1, 1, 20, 10, "x", "qa")    # inverted
        finally:
            wdb.close()

    def test_w7_w8_add_code_contract(self, qualcoder_db_path):
        """W7: palette default + strict #RRGGBB; W8: catid check, dup names."""
        from qualcoder_mcp.database import QUALCODER_COLORS
        wdb = QualcoderDatabase(qualcoder_db_path, read_only=False)
        try:
            cid = wdb.add_code("Palette default", "qa")
            color = wdb.conn.execute(
                "SELECT color FROM code_name WHERE cid=?", (cid,)).fetchone()[0]
            assert color in QUALCODER_COLORS

            with pytest.raises(ValueError):
                wdb.add_code("Bad color 1", "qa", color="#zzzzzz")
            with pytest.raises(ValueError):
                wdb.add_code("Bad color 2", "qa", color="#FFF")
            with pytest.raises(ValueError):
                wdb.add_code("Ghost cat", "qa", category_id=424242)
            with pytest.raises(ValueError, match="already exists"):
                wdb.add_code("Palette default", "qa")
        finally:
            wdb.close()

    def test_w9_import_row_matches_qualcoder(self, setup_server,
                                             qualcoder_db_path):
        """W9: mediapath/av_text_id/risid NULL; nothing written to disk."""
        out = json.loads(server.import_text_file("w9.txt", "content",
                                                 create_backup=False))
        row = _sql(qualcoder_db_path,
                   "SELECT mediapath, av_text_id, risid FROM source WHERE id=?",
                   (out["file_id"],))[0]
        assert row["mediapath"] is None
        assert row["av_text_id"] is None
        assert row["risid"] is None
        assert not (Path(qualcoder_db_path) / "documents").exists()

    def test_w14_project_row_and_coder_names_untouched(self, setup_server,
                                                       qualcoder_db_path):
        """W14: the MCP never writes project/coder_names."""
        before = _sql(qualcoder_db_path, "SELECT * FROM project")
        _apply_one()
        json.loads(server.import_text_file("w14.txt", "x", create_backup=False))
        json.loads(server.link_file_to_case(2, case_id=1, create_backup=False))
        json.loads(server.delete_coding(1, create_backup=False))
        after = _sql(qualcoder_db_path, "SELECT * FROM project")
        assert [tuple(r) for r in before] == [tuple(r) for r in after]
        # static: the only UPDATE in the db layer touches code_text memo/date
        src = (SRC_DIR / "database.py").read_text()
        updates = re.findall(r"UPDATE\s+(\w+)\s+SET\s+([^\n]+)", src)
        assert all(t == "code_text" for t, _ in updates)
        assert all("memo" in cols for _, cols in updates)


# =============================================================================
# B. Text positions
# =============================================================================

class TestTextPositions:

    def test_p1_invariant_for_all_mcp_rows(self, setup_server,
                                           qualcoder_db_path):
        """P1: seltext == fulltext[pos0:pos1] incl. a multi-paragraph span."""
        multi = "First paragraph line.\nSecond paragraph line.\nThird one."
        out = json.loads(server.import_text_file("multi.txt", multi,
                                                 create_backup=False))
        fid = out["file_id"]
        start = multi.index("paragraph line.\nSecond")
        _apply_one(file_id=fid, start=start,
                   end=start + len("paragraph line.\nSecond"),
                   segment="paragraph line.\nSecond")
        bad = _sql(
            qualcoder_db_path,
            "SELECT COUNT(*) as n FROM code_text ct JOIN source s ON ct.fid=s.id "
            "WHERE ct.owner='AI Coding Assistant' AND "
            "ct.seltext != substr(s.fulltext, ct.pos0+1, ct.pos1-ct.pos0)")
        assert bad[0]["n"] == 0

    def test_p2_u2029_tolerance_is_one_way(self, setup_server,
                                           qualcoder_db_path):
        """P2: U+2029 in PROVIDED text matches \\n in the file; never reverse."""
        text = "para one\npara two ends"
        _exec(qualcoder_db_path,
              "INSERT INTO source (id, name, fulltext) VALUES (75, 'p2a.txt', ?)",
              (text,))
        _exec(qualcoder_db_path,
              "INSERT INTO source (id, name, fulltext) VALUES (76, 'p2b.txt', ?)",
              ("para one para two ends",))
        server.switch_project(qualcoder_db_path)

        # forward direction: provided U+2029, file has \n -> accepted, \n stored
        sid = server.analyze_for_coding([75]).split("Session ID: `")[1].split("`")[0]
        rec = json.loads(server.record_suggestions(sid, [{
            "file_id": 75, "code_name": "Stress",
            "start_pos": 5, "end_pos": 13,
            "segment_text": "one para",
        }]))
        assert rec["recorded_count"] == 1
        server.update_suggestion_status(sid, approve=[rec["recorded"][0]["guid"]])
        assert "CODINGS APPLIED" in server.apply_codings(sid, create_backup=False)
        row = _sql(qualcoder_db_path,
                   "SELECT seltext FROM code_text WHERE fid=75")[0]
        assert row["seltext"] == "one\npara"       # stored slice, not the U+2029 form

        # reverse direction: provided \n, file has U+2029 -> NOT silently matched
        sid2 = server.analyze_for_coding([76]).split("Session ID: `")[1].split("`")[0]
        rec2 = json.loads(server.record_suggestions(sid2, [{
            "file_id": 76, "code_name": "Stress",
            "start_pos": 5, "end_pos": 13,
            "segment_text": "one\npara",
        }]))
        assert rec2["recorded_count"] == 0

    def test_p3_end_beyond_length_rejected_no_backup(self, setup_server,
                                                     qualcoder_db_path):
        """P3: end_pos > len(fulltext) refused on the write path."""
        folder = Path(qualcoder_db_path)
        n_backups = len(list(folder.parent.glob(f"{folder.stem}_backup_*.qda")))
        sid = server.analyze_for_coding([1]).split("Session ID: `")[1].split("`")[0]
        rec = json.loads(server.record_suggestions(sid, [{
            "file_id": 1, "code_name": "Stress",
            "start_pos": 70, "end_pos": len(FULLTEXT) + 5,
            "segment_text": "NOT THE FILE TAIL AT ALL",
        }]))
        assert rec["recorded_count"] == 0    # rejected at record time
        assert len(list(folder.parent.glob(f"{folder.stem}_backup_*.qda"))) \
            == n_backups

    def test_p5_position_safe_truth_table(self):
        """P5: TP §8 vectors — False for 1, 2, 5, 10; True for 3, 4, 6, 7, 9."""
        false_vectors = {
            1: "😀 grinning here",
            2: "👨‍👩‍👧 family here",
            5: "line one\r\nline two\r\nfind here",
            10: "😀 note\r\nsecond\r\nmark here",
        }
        true_vectors = {
            3: "日本語のテキスト、ここです。",
            4: unicodedata.normalize("NFD", "café menu here"),
            6: "Hello world",              # post-BOM-strip form
            7: "para one\npara two",
            9: "line one\rline two",       # lone CR is safe
        }
        for vec, text in false_vectors.items():
            assert position_safe(text) is False, f"vector {vec}"
        for vec, text in true_vectors.items():
            assert position_safe(text) is True, f"vector {vec}"

    def test_p6_no_position_rewrites_static_and_dynamic(self, setup_server,
                                                        qualcoder_db_path):
        """P6 (top-5 risk): the server never auto-shifts or repairs existing
        pos0/pos1/seltext — statically (no such UPDATE exists) and
        dynamically (read tools leave a drifted-row DB byte-identical)."""
        # static: no UPDATE may touch pos0/pos1/seltext
        for fname in ("database.py", "server.py", "sessions.py",
                      "refi_export.py"):
            src = (SRC_DIR / fname).read_text()
            for stmt in re.findall(r"UPDATE\s+code_text\s+SET\s+([^\"]+?)WHERE",
                                   src, flags=re.S):
                assert "pos0" not in stmt and "pos1" not in stmt \
                    and "seltext" not in stmt, (fname, stmt)

        # dynamic: unsafe file + GUI-drifted row + U+2029 GUI-style row
        _exec(qualcoder_db_path,
              "INSERT INTO source (id, name, fulltext) VALUES (77, 'drift.txt', ?)",
              ("😀 grinning here",))
        _exec(qualcoder_db_path,   # GUI would store 12,16 for 'here' (true: 11,15)
              "INSERT INTO code_text (cid, fid, seltext, pos0, pos1, owner) "
              "VALUES (1, 77, 'here', 12, 16, 'gui_user')")
        _exec(qualcoder_db_path,   # GUI-style U+2029 seltext over an \n span
              "INSERT INTO source (id, name, fulltext) VALUES (78, 'para.txt', ?)",
              ("para one\npara two",))
        _exec(qualcoder_db_path,
              "INSERT INTO code_text (cid, fid, seltext, pos0, pos1, owner) "
              "VALUES (2, 78, 'one para', 5, 13, 'gui_user')")
        server.switch_project(qualcoder_db_path)

        digest_before = _data_qda(qualcoder_db_path).read_bytes()
        server.search_coded_text("here")
        server.get_coded_segments(1)
        server.get_coded_segments(2)
        server.analyze_file_with_coding(77)
        server.analyze_file_with_coding(78)
        server.get_coding_frequencies()
        server.get_case_code_matrix()
        server.export_code_report("Stress")
        server.search_files("here", search_content=True)
        server.get_project_summary()
        assert _data_qda(qualcoder_db_path).read_bytes() == digest_before
        # P10: the drifted rows are returned, not "repaired" or dropped
        segs = json.loads(server.get_coded_segments(1))["segments"]
        drifted = next(s for s in segs if s["owner"] == "gui_user")
        assert (drifted["position_start"], drifted["position_end"]) == (12, 16)

    def test_p7_import_normalizes_crlf_and_bom(self, setup_server,
                                               qualcoder_db_path):
        """P7 (top-5 risk): CRLF -> LF and one leading BOM stripped at import.

        ADJUDICATION for the checklist: marked 'unmet' at snapshot 9a2f1dc,
        implemented by e68559d — this test confirms the working tree."""
        raw = "﻿line one\r\nline two\r\nline three"
        out = json.loads(server.import_text_file("p7.txt", raw,
                                                 create_backup=False))
        stored = _sql(qualcoder_db_path,
                      "SELECT fulltext FROM source WHERE id=?",
                      (out["file_id"],))[0]["fulltext"]
        assert stored == "line one\nline two\nline three"
        assert out["content_length"] == len(stored)
        assert position_safe(stored) is True   # position-safe from birth

        # a coding recorded against the NORMALIZED text round-trips
        sid = server.analyze_for_coding([out["file_id"]]).split(
            "Session ID: `")[1].split("`")[0]
        rec = json.loads(server.record_suggestions(sid, [{
            "file_id": out["file_id"], "code_name": "Stress",
            "segment_text": "line two",
        }]))
        assert rec["recorded_count"] == 1
        assert rec["recorded"][0]["start_pos"] == stored.index("line two")

    def test_p8_needle_length_canary(self):
        """P8: the U+2029 needle transform must stay length-preserving."""
        fulltext = "para one\npara two ends here"
        segment = "one para"
        ok, start, end, corrected, err = _resolve_segment_positions(
            fulltext, None, None, segment)
        assert ok, err
        needle = segment.replace(" ", "\n")
        assert fulltext[start:end] == needle
        assert end - start == len(segment)
        # canary: if a non-1:1 transform (e.g. CRLF) is ever added to the
        # needle, this equality breaks and pos1 corruption becomes possible
        assert len(needle) == len(segment)

    def test_p9_content_stored_verbatim_no_unicode_normalization(
            self, setup_server, qualcoder_db_path):
        """P9: no NFC/NFD normalization, no trimming of fulltext."""
        nfd = unicodedata.normalize("NFD", "café menu here") + "  "
        out = json.loads(server.import_text_file("p9.txt", nfd,
                                                 create_backup=False))
        stored = _sql(qualcoder_db_path,
                      "SELECT fulltext FROM source WHERE id=?",
                      (out["file_id"],))[0]["fulltext"]
        assert stored == nfd                     # codepoint-identical
        assert unicodedata.is_normalized("NFC", stored) is False


# =============================================================================
# F. Validity & read semantics
# =============================================================================

class TestValiditySemantics:

    def test_v4_containment_join_and_len_minus_one_quirk(
            self, setup_server, qualcoder_db_path):
        """V4: case-code joins use FULL containment; a coding ending at
        len(fulltext) is excluded from a whole-file link (the GUI's -1)."""
        # tighten the fixture case span to a mid-file window: 10..30
        _exec(qualcoder_db_path, "UPDATE case_text SET pos0=10, pos1=30 WHERE id=1")
        _exec(qualcoder_db_path,   # fully contained: 12..20
              "INSERT INTO code_text (cid, fid, seltext, pos0, pos1, owner) "
              "VALUES (2, 1, ?, 12, 20, 'qa')", (FULLTEXT[12:20],))
        _exec(qualcoder_db_path,   # straddles the end: 25..40
              "INSERT INTO code_text (cid, fid, seltext, pos0, pos1, owner) "
              "VALUES (2, 1, ?, 25, 40, 'qa')", (FULLTEXT[25:40],))
        server.switch_project(qualcoder_db_path)

        codes = json.loads(server.get_codes_by_case(1))
        coping = next((c for c in codes if c["code_id"] == 2), None)
        assert coping is not None and coping["occurrence_count"] == 1  # only 12..20
        # the fixture's Stress coding (24..55) straddles too -> excluded
        assert not any(c["code_id"] == 1 for c in codes)

        # quirk parity: whole-file link is (0, len-1); a coding ending at len
        # is excluded, exactly as in QualCoder's report engine
        _exec(qualcoder_db_path,
              "INSERT INTO cases VALUES (2, 'Whole file', '', 'qa', '2024')")
        json.loads(server.link_file_to_case(1, case_id=2, create_backup=False))
        _exec(qualcoder_db_path,
              "INSERT INTO code_text (cid, fid, seltext, pos0, pos1, owner) "
              "VALUES (1, 1, ?, 60, ?, 'qa')",
              (FULLTEXT[60:len(FULLTEXT)], len(FULLTEXT)))
        server.switch_project(qualcoder_db_path)
        codes2 = json.loads(server.get_codes_by_case(2))
        stress = next((c for c in codes2 if c["code_id"] == 1), None)
        # 24..55 counts; 60..79 (== len) does NOT (79 > 78 = len-1)
        assert stress is not None and stress["occurrence_count"] == 1

    def test_v3_non_qualcoder_about_flagged(self, setup_server,
                                            qualcoder_db_path, tmp_path):
        dest = tmp_path / "notqc_about.qda"
        shutil.copytree(qualcoder_db_path, dest)
        conn = sqlite3.connect(str(dest / "data.qda"))
        conn.execute("UPDATE project SET about = 'SomethingElse 1.0'")
        conn.commit()
        conn.close()
        out = json.loads(server.select_project(str(dest)))
        assert (out.get("success") is False) or ("warning" in out)


# =============================================================================
# D/X leftovers
# =============================================================================

class TestBackupAndExportPolicy:

    @pytest.mark.xfail(reason="COMPAT D7 unmet: no retention/prune policy "
                       "for MCP *_backup_* folders — they accumulate full "
                       "project copies forever (QualCoder prunes its own "
                       "family to 5).", strict=True)
    def test_d7_backup_retention(self, setup_server, qualcoder_db_path):
        for i in range(7):
            json.loads(server.import_text_file(f"d7_{i}.txt", "x"))
        folder = Path(qualcoder_db_path)
        backups = list(folder.parent.glob(f"{folder.stem}_backup_*.qda"))
        listed = json.loads(server.list_backups())
        retention_note = any("retention" in n.lower() or "prune" in n.lower()
                             for n in listed.get("notes", []))
        assert len(backups) <= 5 or retention_note

    def test_x10_namespace_constant(self):
        assert refi_export.NAMESPACE == "urn:QDA-XML:project:1.0"

    def test_x13_line_ending_consequence_documented(self):
        """X13: the LF-only choice and its cross-tool consequence must be
        documented (the dialog-parameter alternative was not chosen)."""
        doc = server.export_refi_qda.__doc__ or ""
        assert "\\r\\n" in doc or "\\n" in doc
        assert "NVivo" in doc

    def test_x11_xsd_validation(self, setup_server, qualcoder_db_path,
                                tmp_path):
        """X11: now verifiable — the official Project.xsd is vendored
        (tests/fixtures/refi_qda/, provenance in its README) and xmlschema
        is a dev dependency. The full fixture matrix lives in
        test_refi_xsd_validation.py; this is the compat-checklist pin."""
        xmlschema = pytest.importorskip("xmlschema")
        schema = xmlschema.XMLSchema(
            str(Path(__file__).parent / "fixtures" / "refi_qda" / "Project.xsd"))
        out_file = tmp_path / "x11.qdpx"
        result = json.loads(server.export_refi_qda(str(out_file)))
        assert result.get("success") is True
        import zipfile
        xml_text = zipfile.ZipFile(out_file).read("project.qde").decode("utf-8")
        schema.validate(xml_text)  # raises on any schema violation


# =============================================================================
# NEW findings from this round (xfail = open until fixed)
# =============================================================================

class TestRound2NewFindings:

    def test_qa2_1_bom_only_content_rejected(self, setup_server,
                                             qualcoder_db_path):
        out = json.loads(server.import_text_file("bomonly.txt", "﻿",
                                                 create_backup=False))
        assert "error" in out

    def test_qa2_2_applied_suggestions_not_reapprovable(self, setup_server):
        sid = _apply_one()
        session = server.session_manager.load_session(sid)
        guid = session.suggestions[0].guid
        server.update_suggestion_status(sid, approve=[guid])
        session = server.session_manager.load_session(sid)
        assert session.suggestions[0].status == "applied"  # must stay applied

    def test_qa2_3_export_truncation_is_disclosed(self, setup_server,
                                                  qualcoder_db_path, tmp_path):
        conn = sqlite3.connect(str(_data_qda(qualcoder_db_path)))
        conn.execute("INSERT INTO source (id, name, fulltext) VALUES (85, 'big.txt', ?)",
                     ("word " * 6000,))
        conn.executemany(
            "INSERT INTO code_text (cid, fid, seltext, pos0, pos1, owner) "
            "VALUES (1, 85, 'word', ?, ?, 'qa')",
            [(i * 5, i * 5 + 4) for i in range(5010)])
        conn.commit()
        conn.close()
        server.switch_project(qualcoder_db_path)
        out = json.loads(server.export_refi_qda(str(tmp_path / "big.qdpx")))
        total = 5010 + 2  # + the two fixture codings
        assert out["codings_exported"] == total or "truncated" in json.dumps(out)

    def test_qa2_4_read_output_warns_on_unsafe_files(self, setup_server,
                                                     qualcoder_db_path):
        _exec(qualcoder_db_path,
              "INSERT INTO source (id, name, fulltext) VALUES (86, 'emoji.txt', ?)",
              ("😀 grinning here",))
        server.switch_project(qualcoder_db_path)
        out = json.loads(server.analyze_file_with_coding(86))
        assert "position_safety_warning" in json.dumps(out)

    def test_qa2_5_whole_project_export_skips_invalid_legacy_rows(
            self, setup_server, qualcoder_db_path, tmp_path):
        # a GUI-drifted legacy row: pos1 beyond the file text
        _exec(qualcoder_db_path,
              "INSERT INTO code_text (cid, fid, seltext, pos0, pos1, owner) "
              "VALUES (2, 1, 'exercising.', 70, ?, 'gui_user')",
              (len(FULLTEXT) + 3,))
        server.switch_project(qualcoder_db_path)
        out = json.loads(server.export_refi_qda(str(tmp_path / "legacy.qdpx")))
        assert out.get("success") is True          # valid codings still export
        assert out.get("skipped_invalid_codings", 0) >= 1

    def test_qa2_6_null_position_rows_get_actionable_export_error(
            self, setup_server, qualcoder_db_path, tmp_path):
        _exec(qualcoder_db_path,
              "INSERT INTO code_text (cid, fid, seltext, pos0, pos1, owner) "
              "VALUES (2, 1, NULL, NULL, NULL, 'gui_user')")
        server.switch_project(qualcoder_db_path)
        out = json.loads(server.export_refi_qda(str(tmp_path / "nullpos.qdpx")))
        # error (or skip-and-succeed) is fine — but never a bare TypeError text
        message = json.dumps(out)
        assert "NoneType" not in message
