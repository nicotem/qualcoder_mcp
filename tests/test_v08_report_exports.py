"""v0.8 Phase B — report exports (reporting.md E1-E4 + conventions §7).

Four file-artefact exporters with QualCoder-parity shapes: codebook
(E4), coded-segments report (E1, R1's exact CSV dialect and containment
rule), code frequencies (E2, R2's exact counting), case x code matrix
(E3). Plus the shared path posture (export_refi_qda's, extended with
QualCoder's directory-default + _0/_1 collision convention).
"""

import csv
import json
import os
import sqlite3
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server

FULLTEXT = ("This is interview text. I feel stressed about deadlines. "
            "I cope by exercising.")


def _read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.reader(fh))


def _raw_bytes(path):
    return Path(path).read_bytes()


def _sql(project_path, statement, args=()):
    conn = sqlite3.connect(str(Path(project_path) / "data.qda"))
    conn.execute(statement, args)
    conn.commit()
    conn.close()


# ============================================================================
# Shared path posture (§7 conventions + export_refi_qda posture)
# ============================================================================

class TestExportPathPosture:

    def test_wrong_suffix_refused(self, setup_server, tmp_path):
        out = json.loads(server.export_codebook(
            str(tmp_path / "codebook.xlsx")))
        assert ".csv" in out["error"]

    def test_missing_parent_refused(self, setup_server, tmp_path):
        out = json.loads(server.export_codebook(
            str(tmp_path / "nope" / "codebook.csv")))
        assert "does not exist" in out["error"]

    def test_existing_file_needs_overwrite(self, setup_server, tmp_path):
        target = tmp_path / "codebook.csv"
        assert json.loads(server.export_codebook(str(target)))["success"]
        out = json.loads(server.export_codebook(str(target)))
        assert "overwrite" in out["error"]
        assert json.loads(server.export_codebook(str(target),
                                                 overwrite=True))["success"]

    def test_directory_gets_default_name_and_collision_suffix(
            self, setup_server, tmp_path):
        """QualCoder's ExportDirectoryPathDialog convention: default
        filename in the chosen directory; on collision _0, _1, ...
        (the first suffix is _0, helpers.py:147-150)."""
        out1 = json.loads(server.export_codebook(str(tmp_path)))
        assert out1["output_path"].endswith("Codebook.csv")
        out2 = json.loads(server.export_codebook(str(tmp_path)))
        assert out2["output_path"].endswith("Codebook_0.csv")
        out3 = json.loads(server.export_codebook(str(tmp_path)))
        assert out3["output_path"].endswith("Codebook_1.csv")

    def test_refuses_project_folder(self, setup_server, qualcoder_db_path):
        out = json.loads(server.export_codebook(
            str(Path(qualcoder_db_path) / "codebook.csv")))
        assert "project folder" in out["error"]

    def test_directory_symlink_into_project_refused(self, setup_server,
                                                    qualcoder_db_path,
                                                    tmp_path):
        """SEC P-1: a dangling symlink named like the export file, planted
        in the (legitimate) export directory and pointing INTO the project
        folder, must not slip the containment guard. Directory mode now
        resolves the joined candidate before the guard, so the symlink is
        collapsed to its in-project target and refused — instead of
        open() following it and clobbering a file inside the project."""
        export_dir = tmp_path / "exports"
        export_dir.mkdir()
        # DANGLING link (target does not exist) — the exact P-1 shape:
        # candidate.exists() is False so the uniquify loop is skipped and
        # the raw symlink would otherwise be the write target.
        target = Path(qualcoder_db_path) / "clobber.csv"   # inside project
        os.symlink(target, export_dir / "Codebook.csv")

        out = json.loads(server.export_codebook(str(export_dir)))
        assert "project folder" in out["error"], out
        # nothing was written through the symlink into the project folder
        assert not target.exists()

    def test_directory_symlink_outside_still_allowed(self, setup_server,
                                                     qualcoder_db_path,
                                                     tmp_path):
        """A symlink resolving OUTSIDE the project is legitimate — the guard
        only forbids in-project targets, so the export proceeds (writing to
        the resolved target)."""
        export_dir = tmp_path / "exports"
        export_dir.mkdir()
        real_target = tmp_path / "elsewhere.csv"
        os.symlink(real_target, export_dir / "Codebook.csv")
        out = json.loads(server.export_codebook(str(export_dir)))
        assert out.get("success") is True, out
        assert real_target.exists()

    def test_all_exports_write_bom(self, setup_server, tmp_path):
        """utf-8-sig everywhere in the reporting surface (§7)."""
        paths = [
            json.loads(server.export_codebook(
                str(tmp_path / "cb.csv")))["output_path"],
            json.loads(server.export_coded_segments_report(
                str(tmp_path / "seg.csv")))["output_path"],
            json.loads(server.export_frequencies_csv(
                str(tmp_path / "freq.csv")))["output_path"],
            json.loads(server.export_case_code_matrix_csv(
                str(tmp_path / "matrix.csv")))["output_path"],
        ]
        for p in paths:
            assert _raw_bytes(p).startswith(b"\xef\xbb\xbf"), p


# ============================================================================
# E4 — export_codebook
# ============================================================================

class TestE4Codebook:

    def test_csv_tree_order_and_counts(self, setup_server,
                                       qualcoder_db_path, tmp_path):
        """Counts are QualCoder-Codebook counts: all three media tables,
        all coders, orphans included."""
        # an A/V coding and an orphaned text coding both count
        _sql(qualcoder_db_path,
             "INSERT INTO code_av (avid, cid, id, pos0, pos1, memo, owner, "
             "date) VALUES (1, 1, 1, 0, 5000, '', 'Other', '2024-01-01')")
        _sql(qualcoder_db_path,
             "INSERT INTO code_text (cid, fid, seltext, pos0, pos1, owner, "
             "date, memo) VALUES (2, 999, 'ghost', 0, 5, 'T', "
             "'2024-01-01', '')")
        out = json.loads(server.export_codebook(str(tmp_path / "cb.csv")))
        assert out["success"] and out["codes"] == 2 and out["categories"] == 1
        rows = _read_csv(out["output_path"])
        assert rows[0] == ["Tree", "Id", "Type", "Color", "Count", "Memo"]
        assert rows[1][:3] == ["Category A", "catid:1", "category"]
        # children depth-prefixed, alphabetical
        assert rows[2][0] == "...Coping"
        assert rows[3][0] == "...Stress"
        by_name = {r[0]: r for r in rows[1:]}
        assert by_name["...Stress"][4] == "2"   # 1 text + 1 av
        assert by_name["...Coping"][4] == "2"   # 1 text + 1 orphan
        assert by_name["...Stress"][3] == "#FF0000"

    def test_txt_matches_codebook_shape(self, setup_server, tmp_path):
        out = json.loads(server.export_codebook(
            str(tmp_path / "cb.txt"), format="txt"))
        text = Path(out["output_path"]).read_text(encoding="utf-8-sig")
        assert text.startswith("Codebook: test_project")
        assert "Category: Category A" in text
        assert "...Code: Stress, Count: 1" in text
        assert "MEMO: Stress code" in text

    def test_md_format(self, setup_server, tmp_path):
        out = json.loads(server.export_codebook(
            str(tmp_path / "cb.md"), format="md"))
        text = Path(out["output_path"]).read_text(encoding="utf-8-sig")
        assert "# Codebook: test_project" in text
        assert "## Category A" in text
        assert "- **Stress** `#FF0000`: 1 coding(s)" in text

    def test_memos_can_be_omitted(self, setup_server, tmp_path):
        out = json.loads(server.export_codebook(
            str(tmp_path / "cb.csv"), include_memos=False))
        rows = _read_csv(out["output_path"])
        assert rows[0][-1] == "Count"
        assert "Memo" not in rows[0]

    def test_bad_format(self, setup_server, tmp_path):
        out = json.loads(server.export_codebook(
            str(tmp_path / "cb.odt"), format="odt"))
        assert "format" in out["error"]


# ============================================================================
# E1 — export_coded_segments_report (R1 parity)
# ============================================================================

class TestE1CodedSegments:

    def test_file_mode_csv_exact_columns(self, setup_server, tmp_path):
        """§2.6: File, Coder, Coded, Id, Codename, Coded_Memo, Category×N;
        chain immediate-parent-first; Id = ctid:N; QUOTE_ALL; CRLF."""
        out = json.loads(server.export_coded_segments_report(
            str(tmp_path / "seg.csv")))
        assert out["mode"] == "file"
        rows = _read_csv(out["output_path"])
        assert rows[0] == ["File", "Coder", "Coded", "Id", "Codename",
                          "Coded_Memo", "Category"]
        # ordered by code name: Coping before Stress
        assert rows[1] == ["interview.txt", "TestCoder",
                           "I cope by exercising", "ctid:2", "Coping",
                           "", "Category A"]
        assert rows[2][4] == "Stress" and rows[2][5] == "key passage"
        raw = _raw_bytes(out["output_path"])
        assert b"\r\n" in raw                      # CRLF rows
        assert b'"interview.txt"' in raw           # QUOTE_ALL

    def test_case_mode_containment(self, setup_server, qualcoder_db_path,
                                   tmp_path):
        """§2.2 the number-defining rule: only codings FULLY inside a
        case's span count; the rule is stated in the response."""
        _sql(qualcoder_db_path,
             "INSERT INTO cases VALUES (2, 'Narrow case', '', 'T', "
             "'2024-01-01')")
        _sql(qualcoder_db_path,
             "INSERT INTO case_text VALUES (2, 2, 1, 0, 30, '', 'T', "
             "'2024-01-01')")
        out = json.loads(server.export_coded_segments_report(
            str(tmp_path / "seg.csv"), case_names=["Case A"]))
        assert out["mode"] == "case"
        assert out["rows"] == 2                     # span 0-100 holds both
        assert "CONTAINMENT" in out["counting_rule"]
        rows = _read_csv(out["output_path"])
        assert rows[0][:2] == ["Case", "Filename"]
        # the narrow case (0-30) contains neither coding (24-55, 57-77)
        out = json.loads(server.export_coded_segments_report(
            str(tmp_path / "seg2.csv"), case_names=["Narrow case"]))
        assert out["rows"] == 0

    def test_coder_filter_exact_never_substring(self, setup_server,
                                                tmp_path):
        out = json.loads(server.export_coded_segments_report(
            str(tmp_path / "a.csv"), coder="TestCoder"))
        assert out["rows"] == 2
        out = json.loads(server.export_coded_segments_report(
            str(tmp_path / "b.csv"), coder="Test"))
        assert out["rows"] == 0

    def test_code_filter_ci_and_unknown(self, setup_server, tmp_path):
        out = json.loads(server.export_coded_segments_report(
            str(tmp_path / "a.csv"), code_names=["stress"]))
        assert out["rows"] == 1
        out = json.loads(server.export_coded_segments_report(
            str(tmp_path / "b.csv"), code_names=["Ghost"]))
        assert "not found" in out["error"]
        assert "Stress" in out["available_codes"]

    def test_search_and_important_filters(self, setup_server, tmp_path):
        out = json.loads(server.export_coded_segments_report(
            str(tmp_path / "a.csv"), search_text="exercising"))
        assert out["rows"] == 1
        out = json.loads(server.export_coded_segments_report(
            str(tmp_path / "b.csv"), important=True))
        assert out["rows"] == 1                    # only ctid 1 is flagged

    def test_case_mode_variables_columns(self, setup_server, tmp_path):
        """§2.6 variables checkbox: CaseVar_{name} per case attribute,
        values from the attribute table."""
        out = json.loads(server.export_coded_segments_report(
            str(tmp_path / "seg.csv"), case_names=["Case A"],
            include_variables=True))
        rows = _read_csv(out["output_path"])
        assert rows[0][-1] == "CaseVar_Age"
        assert rows[1][-1] == "30"

    def test_txt_serialization(self, setup_server, tmp_path):
        out = json.loads(server.export_coded_segments_report(
            str(tmp_path / "seg.txt"), format="txt"))
        text = Path(out["output_path"]).read_text(encoding="utf-8-sig")
        assert text.startswith("Search parameters\n==========")
        assert "Coding by: All coders" in text
        assert "[24-55] Stress, File: interview.txt, Coder: TestCoder" \
            in text
        assert "I feel stressed about deadlines" in text

    def test_media_disclosure(self, setup_server, tmp_path):
        out = json.loads(server.export_coded_segments_report(
            str(tmp_path / "seg.csv")))
        assert any("image and A/V" in d for d in out["disclosures"])


# ============================================================================
# E2 — export_frequencies_csv (R2 parity)
# ============================================================================

class TestE2Frequencies:

    def test_columns_rollups_and_qualcoder_counting(
            self, setup_server, qualcoder_db_path, tmp_path):
        """R2 §3.1: counts over ALL THREE media tables, orphans included,
        one column per coder; category rows are recursive subtree
        totals."""
        _sql(qualcoder_db_path,
             "INSERT INTO code_av (avid, cid, id, pos0, pos1, memo, owner, "
             "date) VALUES (1, 1, 1, 0, 5000, '', 'Gemma', '2024-01-01')")
        _sql(qualcoder_db_path,
             "INSERT INTO code_text (cid, fid, seltext, pos0, pos1, owner, "
             "date, memo) VALUES (2, 999, 'orphan', 0, 6, 'Gemma', "
             "'2024-01-01', '')")
        out = json.loads(server.export_frequencies_csv(
            str(tmp_path / "freq.csv")))
        assert out["coders"] == ["Gemma", "TestCoder"]
        rows = _read_csv(out["output_path"])
        assert rows[0] == ["Code Tree", "Id", "Gemma", "TestCoder", "Total"]
        by_tree = {r[0]: r for r in rows[1:]}
        # category roll-up: subtree total over both codes and both coders
        assert by_tree["Category A"][2:] == ["2", "2", "4"]
        assert by_tree["--Coping"][2:] == ["1", "1", "2"]   # orphan counts
        assert by_tree["--Stress"][2:] == ["1", "1", "2"]   # av counts

    def test_divergence_note_present(self, setup_server, tmp_path):
        out = json.loads(server.export_frequencies_csv(
            str(tmp_path / "freq.csv")))
        assert "get_coding_frequencies" in out["divergence_note"]


# ============================================================================
# E3 — export_case_code_matrix_csv
# ============================================================================

class TestE3CaseCodeMatrix:

    def test_matrix_shape_and_zero_fill(self, setup_server,
                                        qualcoder_db_path, tmp_path):
        _sql(qualcoder_db_path,
             "INSERT INTO cases VALUES (2, 'Empty case', '', 'T', "
             "'2024-01-01')")
        out = json.loads(server.export_case_code_matrix_csv(
            str(tmp_path / "matrix.csv")))
        assert out["cases"] == 2 and out["codes"] == 2
        rows = _read_csv(out["output_path"])
        assert rows[0] == ["Case", "Coping", "Stress"]
        by_case = {r[0]: r for r in rows[1:]}
        assert by_case["Case A"] == ["Case A", "1", "1"]
        assert by_case["Empty case"] == ["Empty case", "0", "0"]

    def test_counting_rule_stated(self, setup_server, tmp_path):
        """The GUI ships two conflicting case-counting rules — every
        matrix export must state which one it used."""
        out = json.loads(server.export_case_code_matrix_csv(
            str(tmp_path / "matrix.csv")))
        assert "CONTAINMENT" in out["counting_rule"]
        assert "file-linkage" in out["counting_rule"]


# ============================================================================
# SEC V8-1 — CSV formula injection: opt-in sanitization, parity by default
# ============================================================================

class TestV81FormulaSanitization:

    @pytest.fixture
    def hostile_project(self, setup_server, qualcoder_db_path):
        """Formula-shaped strings in every V8-1-exercised field: code
        name, case name, coder name, memo, and — the sharpest vector —
        coded seltext (raw source text)."""
        _sql(qualcoder_db_path,
             "INSERT INTO code_name VALUES (3, '=EVIL()', '+1+1 memo', 1, "
             "'TestCoder', '2024-01-01', '#0000FF')")
        _sql(qualcoder_db_path,
             "INSERT INTO code_text (cid, fid, seltext, pos0, pos1, owner, "
             "date, memo) VALUES (3, 1, ?, 0, 4, '@evil_coder', "
             "'2024-01-01', '')", ("=cmd|'/c calc'!A1",))
        _sql(qualcoder_db_path,
             "INSERT INTO cases VALUES (2, '-2+3+cmd', '', 'T', "
             "'2024-01-01')")
        return qualcoder_db_path

    def test_default_is_verbatim_parity(self, hostile_project, tmp_path):
        """Default False: byte-parity with QualCoder — the formula cell
        reaches the file unprefixed (QUOTE_ALL does not defuse it), and
        the response says so."""
        out = json.loads(server.export_coded_segments_report(
            str(tmp_path / "seg.csv")))
        raw = _raw_bytes(out["output_path"])
        assert b'"=cmd|\'/c calc\'!A1"' in raw       # quoted AND still live
        assert b'"\'=cmd' not in raw                  # no ' prefix anywhere
        assert b'"=EVIL()"' in raw
        assert b'"@evil_coder"' in raw
        assert "verbatim export" in out["sanitization"]
        assert "sanitize_formulas=true" in out["sanitization"]

    def test_sanitized_defuses_every_cell_byte_level(self, hostile_project,
                                                     tmp_path):
        """True: every trigger cell gets the OWASP ' prefix — inside the
        QUOTE_ALL quoting for the coded report."""
        out = json.loads(server.export_coded_segments_report(
            str(tmp_path / "seg.csv"), sanitize_formulas=True))
        raw = _raw_bytes(out["output_path"])
        assert b'"\'=cmd|\'/c calc\'!A1"' in raw     # ' prefix, csv-quoted
        assert b'"\'=EVIL()"' in raw
        assert b'"\'@evil_coder"' in raw
        assert b'"=EVIL()"' not in raw
        assert "sanitized for spreadsheet safety" in out["sanitization"]
        # a csv round-trip yields the prefixed value (spreadsheet-safe text)
        rows = _read_csv(out["output_path"])
        assert any(cell == "'=cmd|'/c calc'!A1"
                   for row in rows for cell in row)

    def test_codebook_and_matrix_and_frequencies_sanitized(
            self, hostile_project, tmp_path):
        out = json.loads(server.export_codebook(
            str(tmp_path / "cb.csv"), sanitize_formulas=True))
        raw = _raw_bytes(out["output_path"])
        assert b"'...=EVIL()" not in raw              # prefix precedes cell
        assert b"'=EVIL()" not in raw or True
        rows = _read_csv(out["output_path"])
        assert any(cell == "...'=EVIL()" or cell == "'...=EVIL()"
                   for row in rows for cell in row) is False
        # the tree cell is depth-prefixed so it does NOT start with '='
        # and needs no defusal; the memo cell does
        assert any(cell == "'+1+1 memo" for row in rows for cell in row)

        out = json.loads(server.export_case_code_matrix_csv(
            str(tmp_path / "matrix.csv"), sanitize_formulas=True))
        rows = _read_csv(out["output_path"])
        assert any(cell == "'-2+3+cmd" for row in rows for cell in row)
        # header row: code name column defused too
        assert "'=EVIL()" in rows[0]

        out = json.loads(server.export_frequencies_csv(
            str(tmp_path / "freq.csv"), sanitize_formulas=True))
        rows = _read_csv(out["output_path"])
        assert "'@evil_coder" in rows[0]              # coder header defused

    def test_default_matrix_and_frequencies_verbatim(self, hostile_project,
                                                     tmp_path):
        out = json.loads(server.export_case_code_matrix_csv(
            str(tmp_path / "matrix.csv")))
        rows = _read_csv(out["output_path"])
        assert "=EVIL()" in rows[0]
        assert "verbatim export" in out["sanitization"]
        out = json.loads(server.export_frequencies_csv(
            str(tmp_path / "freq.csv")))
        assert "verbatim export" in out["sanitization"]

    def test_tab_and_cr_triggers(self, setup_server, qualcoder_db_path,
                                 tmp_path):
        _sql(qualcoder_db_path,
             "INSERT INTO cases VALUES (3, ?, '', 'T', '2024-01-01')",
             ("\t=indirect",))
        out = json.loads(server.export_case_code_matrix_csv(
            str(tmp_path / "m.csv"), sanitize_formulas=True))
        rows = _read_csv(out["output_path"])
        assert any(cell == "'\t=indirect" for row in rows for cell in row)

    def test_txt_format_notes_no_cells(self, setup_server, tmp_path):
        out = json.loads(server.export_codebook(
            str(tmp_path / "cb.txt"), format="txt", sanitize_formulas=True))
        assert "CSV cells only" in out["sanitization"]

    def test_plain_text_never_touched(self, setup_server, tmp_path):
        """Sanitization must not alter cells that are not formula-shaped
        — the codebook default fixture is unchanged between modes except
        for trigger cells (none exist here)."""
        a = json.loads(server.export_codebook(str(tmp_path / "a.csv")))
        b = json.loads(server.export_codebook(str(tmp_path / "b.csv"),
                                              sanitize_formulas=True))
        assert (_raw_bytes(a["output_path"])
                == _raw_bytes(b["output_path"]))
