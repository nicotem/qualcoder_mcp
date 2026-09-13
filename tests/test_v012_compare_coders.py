"""B3 (v0.12, D2): comparing two coders, with both kappas named honestly.

The parity pins P1 to P8 are the numbers QualCoder's own Coder comparison
report shows for the same data, taken from the dossier's probe against
the pinned master 9bddf17 (`reports.py:1049-1152`, identical arithmetic
at `report_compare_coder_file.py:818-830` and at
`3.8.2:reports.py:705-822`). Where our counting deliberately differs from
the dialog's (a coder's own overlapping segments of one code), the
difference is disclosed, and the disclosure is pinned against a verbatim
port of upstream's loop rather than against a number typed in by hand.
"""

import json
import re
import sqlite3
from pathlib import Path

import pytest
from hypothesis import HealthCheck, assume, given, settings, strategies as st

import qualcoder_mcp.server as server
import track5_helpers as H
from qualcoder_mcp import coder_comparison as cc
from qualcoder_mcp.database import QualcoderDatabase

SECOND = "Second Coder"


def _reopen(project_path):
    if server.db is not None:
        try:
            server.db.close()
        except Exception:
            pass
    server.db = QualcoderDatabase(project_path)


def _code(project_path, ctid, cid, fid, pos0, pos1, owner, text="x"):
    H.execute(project_path,
              "INSERT INTO code_text (ctid, cid, fid, seltext, pos0, pos1, "
              "owner, date, memo, important) VALUES (?,?,?,?,?,?,?,?,'',0)",
              (ctid, cid, fid, text, pos0, pos1, owner, "2024-01-15"))


def _compare(*args, **kwargs):
    return json.loads(server.compare_coders(*args, **kwargs))


def _row(result, code_name):
    return next(r for r in result["per_code"] if r["code_name"] == code_name)


@pytest.fixture
def two_coders(setup_server, qualcoder_db_path):
    """The dossier's probe setup: Second Coder's cid 1 span [31, 60)."""
    _code(qualcoder_db_path, 30, 1, 1, 31, 60, SECOND)
    _reopen(qualcoder_db_path)
    return qualcoder_db_path


def _house_rules(texts, labels=None):
    """No em dashes, British English, no forbidden vocabulary.

    The house rules are pinned on every text this batch adds, in the
    module that owns it, so a reworded string is checked where it is
    written rather than in one distant place (the convention Batch A's D6
    test 8 established).
    """
    forbidden_spellings = ("color", "colors", "behavior", "organize",
                           "recognize", "authorization", "analyze",
                           "labeled", "favor")
    for index, text in enumerate(texts):
        label = (labels[index] if labels else f"text {index}")
        assert "\u2014" not in text, label
        lowered = text.lower()
        for word in forbidden_spellings:
            # Identifiers are exempt by house rule (analyze_file_with_coding,
            # honor_visibility, color): the word counts only when it is
            # prose, that is, not glued to another identifier character.
            pattern = r"(?<![A-Za-z_])" + word + r"(?![A-Za-z_])"
            assert not re.search(pattern, lowered), (label, word)
        for token in re.findall(r"(?<![A-Za-z_])session_id(?![A-Za-z_])",
                                text):
            raise AssertionError((label, "session_id"))

# =============================================================================
# PARITY PINS P1 TO P8
# =============================================================================

class TestParityPins:

    def test_p1_pooled_and_per_file(self, two_coders):
        out = _compare("TestCoder", SECOND)
        row = _row(out, "Stress")
        assert row["characters"] == 115
        assert (row["coded_a"], row["coded_b"], row["both"]) == (31, 29, 24)
        assert (row["a_only"], row["b_only"], row["neither"]) == (7, 5, 79)
        assert row["agreement_pct"] == 89.57
        assert row["dual_coded_pct"] == 20.87
        assert row["uncoded_pct"] == 68.7          # not 68.70: a float
        assert row["disagreement_pct"] == 10.43
        assert row["agree_coded_only_pct"] == 66.67
        assert row["kappa_qualcoder"] == 0.6603
        assert row["kappa_cohen"] == 0.7295

        per_file = _row(_compare("TestCoder", SECOND, per_file=True), "Stress")
        file_one = next(f for f in per_file["files"] if f["file_id"] == 1)
        assert file_one["agreement_pct"] == 84.62
        assert file_one["dual_coded_pct"] == 30.77
        assert file_one["uncoded_pct"] == 53.85
        assert file_one["disagreement_pct"] == 15.38
        assert file_one["agree_coded_only_pct"] == 66.67
        assert file_one["kappa_qualcoder"] == 0.6603
        assert file_one["kappa_cohen"] == 0.6752
        assert [f["file_id"] for f in per_file["files"]] == [1]
        assert per_file["files_without_codings"] == 1

    def test_p2_a_code_one_coder_never_applied_is_zeros_not_an_error(
            self, two_coders):
        row = _row(_compare("TestCoder", SECOND), "Coping")
        assert (row["coded_a"], row["coded_b"], row["both"]) == (20, 0, 0)
        assert row["neither"] == 95
        assert row["agreement_pct"] == 82.61
        assert row["uncoded_pct"] == 82.61
        assert row["disagreement_pct"] == 17.39
        assert row["agree_coded_only_pct"] == 0.0
        assert row["kappa_qualcoder"] == 0.0
        assert row["kappa_cohen"] == 0.0
        assert "kappa_note" not in row

    def test_p3_a_code_neither_coder_applied(self, two_coders):
        H.execute(two_coders,
                  "INSERT INTO code_name (cid,name,memo,catid,owner,date,"
                  "color) VALUES (3,'Untouched','',NULL,'TestCoder',"
                  "'2024-01-15','#FF0000')")
        _reopen(two_coders)
        raw = server.compare_coders("TestCoder", SECOND)
        row = _row(json.loads(raw), "Untouched")
        assert row["agreement_pct"] == 100.0
        assert row["agree_coded_only_pct"] is None
        assert row["kappa_qualcoder"] is None
        assert row["kappa_cohen"] is None
        assert row["kappa_note"] == cc.NOTE_NO_VARIANCE
        assert "zerodiv" not in raw and "zero div" not in raw

    def test_p4_both_coders_coded_everything(self, setup_server,
                                             qualcoder_db_path):
        H.execute(qualcoder_db_path, "DELETE FROM code_text")
        _code(qualcoder_db_path, 40, 1, 1, 0, 78, "TestCoder")
        _code(qualcoder_db_path, 41, 1, 1, 0, 78, SECOND)
        _reopen(qualcoder_db_path)
        row = _row(_compare("TestCoder", SECOND, file_ids=[1]), "Stress")
        assert row["kappa_qualcoder"] == 1.0
        assert row["kappa_cohen"] is None
        assert row["kappa_note"] == cc.NOTE_BOTH_CODED_ALL

    def test_p5_disjoint_spans(self, setup_server, qualcoder_db_path):
        H.execute(qualcoder_db_path, "DELETE FROM code_text")
        _code(qualcoder_db_path, 42, 1, 1, 24, 55, "TestCoder")
        _code(qualcoder_db_path, 43, 1, 1, 57, 77, SECOND)
        _reopen(qualcoder_db_path)
        row = _row(_compare("TestCoder", SECOND, file_ids=[1]), "Stress")
        assert row["agreement_pct"] == 34.62
        assert row["kappa_qualcoder"] == -0.0602
        assert row["kappa_cohen"] == -0.4529

    def test_p6_adjacent_spans_do_not_overlap(self, setup_server,
                                              qualcoder_db_path):
        H.execute(qualcoder_db_path, "DELETE FROM code_text")
        _code(qualcoder_db_path, 44, 1, 1, 24, 40, "TestCoder")
        _code(qualcoder_db_path, 45, 1, 1, 40, 55, SECOND)
        _reopen(qualcoder_db_path)
        row = _row(_compare("TestCoder", SECOND, file_ids=[1]), "Stress")
        assert row["agreement_pct"] == 60.26
        assert row["kappa_qualcoder"] == -0.0665
        assert row["kappa_cohen"] == -0.2477
        assert "same_coder_overlap" not in row

    def test_p7_identical_spans(self, setup_server, qualcoder_db_path):
        H.execute(qualcoder_db_path, "DELETE FROM code_text")
        _code(qualcoder_db_path, 46, 1, 1, 24, 55, "TestCoder")
        _code(qualcoder_db_path, 47, 1, 1, 24, 55, SECOND)
        _reopen(qualcoder_db_path)
        row = _row(_compare("TestCoder", SECOND, file_ids=[1]), "Stress")
        assert row["kappa_qualcoder"] == 1.0
        assert row["kappa_cohen"] == 1.0
        assert row["agreement_pct"] == 100.0
        assert row["agree_coded_only_pct"] == 100.0

    def test_p8_on_the_visibility_fixture_with_the_override(
            self, setup_server, qualcoder_db_path):
        from test_qc40_visibility import _apply_visibility_schema, HIDDEN
        H.execute(qualcoder_db_path, "DELETE FROM code_text")
        _code(qualcoder_db_path, 50, 1, 1, 24, 55, "TestCoder")
        _code(qualcoder_db_path, 51, 2, 1, 57, 77, "TestCoder")
        _apply_visibility_schema(qualcoder_db_path)
        # the fixture adds the hidden coder's rows 3, 4 and 5
        _reopen(qualcoder_db_path)
        out = _compare("TestCoder", HIDDEN, allow_hidden_coder=True)
        stress = _row(out, "Stress")
        assert stress["both"] == 31
        assert stress["neither"] == 84
        assert stress["agreement_pct"] == 100.0
        assert stress["kappa_qualcoder"] == 1.0
        assert stress["kappa_cohen"] == 1.0
        coping = _row(out, "Coping")
        assert (coping["coded_a"], coping["coded_b"], coping["both"]) == \
            (20, 30, 20)
        assert coping["b_only"] == 10
        assert coping["agree_coded_only_pct"] == 66.67
        assert coping["kappa_qualcoder"] == 0.6667
        assert coping["kappa_cohen"] == 0.7473
        assert out["coder_visibility"]["hidden_coder_filter"] == "bypassed"


# =============================================================================
# THE REFERENCE PORT: OURS AGAINST UPSTREAM'S OWN LOOP
# =============================================================================

_SPANS = st.lists(
    st.tuples(st.integers(0, 60), st.integers(1, 40)).map(
        lambda t: (t[0], t[0] + t[1])),
    min_size=0, max_size=5)


def _non_overlapping(spans):
    ordered = sorted(spans)
    for i in range(1, len(ordered)):
        if ordered[i][0] < ordered[i - 1][1]:
            return False
    return True


class TestReferenceEquivalence:
    """D2 6.2: ours equals a verbatim port of upstream's own counting,
    for every count, percentage and kappa, whenever no coder's spans of
    one code overlap in one file (which is when the two definitions
    agree)."""

    @settings(max_examples=60, deadline=None,
              suppress_health_check=[HealthCheck.too_slow,
                                     HealthCheck.data_too_large])
    @given(length=st.integers(0, 120), spans_a=_SPANS, spans_b=_SPANS)
    def test_non_overlapping_spans_agree_exactly(self, length, spans_a,
                                                 spans_b):
        clipped_a = [(a, min(b, length)) for a, b in spans_a
                     if a < length and min(b, length) > a]
        clipped_b = [(a, min(b, length)) for a, b in spans_b
                     if a < length and min(b, length) > a]
        assume(_non_overlapping(clipped_a) and _non_overlapping(clipped_b))

        reference = cc.qualcoder_report_values(length, clipped_a, clipped_b)
        merged_a = QualcoderDatabase._merge_spans(clipped_a)
        merged_b = QualcoderDatabase._merge_spans(clipped_b)
        ours = cc.statistics(
            length,
            QualcoderDatabase._span_length(merged_a),
            QualcoderDatabase._span_length(merged_b),
            QualcoderDatabase._intersection_length(merged_a, merged_b))
        assert ours["coded_a"] == reference["coded0"]
        assert ours["coded_b"] == reference["coded1"]
        assert ours["both"] == reference["dual_coded"]
        assert ours["neither"] == reference["uncoded"]
        for field in ("agreement_pct", "dual_coded_pct", "uncoded_pct",
                      "disagreement_pct", "agree_coded_only_pct"):
            assert ours[field] == reference[field], field
        assert ours["kappa_qualcoder"] == reference["kappa"]

    @settings(max_examples=40, deadline=None,
              suppress_health_check=[HealthCheck.too_slow,
                                     HealthCheck.data_too_large])
    @given(length=st.integers(1, 120), spans_a=_SPANS, spans_b=_SPANS)
    def test_overlapping_spans_agree_on_the_merged_intervals(
            self, length, spans_a, spans_b):
        """With overlaps the two definitions differ, and that is the
        point: ours equals the reference run on the MERGED intervals, and
        the disclosure block equals the reference run on the raw spans."""
        clipped_a = [(a, min(b, length)) for a, b in spans_a
                     if a < length and min(b, length) > a]
        clipped_b = [(a, min(b, length)) for a, b in spans_b
                     if a < length and min(b, length) > a]
        merged_a = QualcoderDatabase._merge_spans(clipped_a)
        merged_b = QualcoderDatabase._merge_spans(clipped_b)
        ours = cc.statistics(
            length,
            QualcoderDatabase._span_length(merged_a),
            QualcoderDatabase._span_length(merged_b),
            QualcoderDatabase._intersection_length(merged_a, merged_b))
        on_merged = cc.qualcoder_report_values(length, merged_a, merged_b)
        assert ours["coded_a"] == on_merged["coded0"]
        assert ours["both"] == on_merged["dual_coded"]
        assert ours["agreement_pct"] == on_merged["agreement_pct"]
        assert ours["kappa_qualcoder"] == on_merged["kappa"]


class TestTheGuiDivergenceDisclosure:

    def test_an_overlap_is_named_and_the_gui_numbers_reported(
            self, setup_server, qualcoder_db_path):
        """D2 1.7 row 4, the worked example the upstream issue will use:
        identical coding scored below 1.0 by the dialog because one
        coder's two segments overlap."""
        H.execute(qualcoder_db_path, "DELETE FROM code_text")
        _code(qualcoder_db_path, 60, 1, 1, 24, 55, "TestCoder")
        _code(qualcoder_db_path, 61, 1, 1, 30, 40, "TestCoder")   # overlap
        _code(qualcoder_db_path, 62, 1, 1, 24, 55, SECOND)
        _reopen(qualcoder_db_path)
        row = _row(_compare("TestCoder", SECOND, file_ids=[1]), "Stress")
        # Ours: the two coders coded exactly the same characters
        assert row["both"] == 31
        assert row["kappa_qualcoder"] == 1.0
        # QualCoder's dialog would not say so
        assert row["same_coder_overlap"]["coder_a_files"] == [1]
        assert row["same_coder_overlap"]["coder_b_files"] == []
        gui = row["qualcoder_report_values"]
        assert gui["kappa"] is not None and gui["kappa"] < 1.0
        assert gui["note"] == cc.SAME_CODER_OVERLAP_NOTE

    def test_the_disclosure_matches_the_verbatim_port(self, setup_server,
                                                      qualcoder_db_path):
        H.execute(qualcoder_db_path, "DELETE FROM code_text")
        _code(qualcoder_db_path, 63, 1, 1, 24, 55, "TestCoder")
        _code(qualcoder_db_path, 64, 1, 1, 30, 40, "TestCoder")
        _code(qualcoder_db_path, 65, 1, 1, 24, 55, SECOND)
        _reopen(qualcoder_db_path)
        row = _row(_compare("TestCoder", SECOND, file_ids=[1]), "Stress")
        expected = cc.qualcoder_report_values(
            78, [(24, 55), (30, 40)], [(24, 55)])
        assert row["qualcoder_report_values"]["kappa"] == expected["kappa"]
        assert row["qualcoder_report_values"]["agreement_pct"] == \
            expected["agreement_pct"]


# =============================================================================
# ARGUMENTS, SCOPE AND ERROR TEXTS
# =============================================================================

class TestArgumentsAndErrors:

    def test_one_coder_only(self, two_coders):
        out = _compare("TestCoder")
        assert out["error"] == (
            "coder_a and coder_b must both be given, or both omitted to "
            "compare the project's two coders automatically.")

    def test_the_same_coder_twice(self, two_coders):
        out = _compare("TestCoder", "TestCoder")
        assert out["error"] == (
            "coder_a and coder_b must be two different coder names.")

    def test_a_blank_name(self, two_coders):
        assert _compare("   ", "TestCoder")["error"] == \
            "coder_a must be a non-empty coder name."
        assert _compare("TestCoder", "  ")["error"] == \
            "coder_b must be a non-empty coder name."

    def test_names_are_compared_exactly_not_casefolded(self, two_coders):
        """X2: coder names are TEXT UNIQUE under a binary collation
        upstream, so "second coder" is not "Second Coder"."""
        out = _compare("TestCoder", "second coder")
        assert "has no text codings in this project" in out["error"]

    def test_an_unknown_coder_lists_the_eligible_ones(self, two_coders):
        out = _compare("TestCoder", "Nobody")
        assert out["error"] == (
            "coder_b 'Nobody' has no text codings in this project. Coders "
            "with text codings visible in QualCoder: ['Second Coder', "
            "'TestCoder'].")

    def test_auto_selection_with_exactly_two_coders(self, two_coders):
        out = _compare()
        assert {out["coder_a"], out["coder_b"]} == {"TestCoder", SECOND}

    def test_auto_selection_refuses_with_three(self, two_coders):
        _code(two_coders, 70, 1, 2, 0, 5, "Third Coder")
        _reopen(two_coders)
        out = _compare()
        assert out["error"] == (
            "coder_a and coder_b are required: this project has 3 coders "
            "with text codings visible in QualCoder: ['Second Coder', "
            "'TestCoder', 'Third Coder']. Name two of them.")

    def test_auto_selection_refuses_with_one(self, setup_server,
                                             qualcoder_db_path):
        out = _compare()
        assert "1 coder with text codings" in out["error"]
        assert "a comparison needs two." in out["error"]

    def test_the_speaker_coder_is_refused_by_name(self, two_coders):
        speaker = "\U0001F4CC Speaker coding"
        _code(two_coders, 71, 1, 2, 0, 5, speaker)
        _reopen(two_coders)
        out = _compare("TestCoder", speaker)
        assert out["error"] == (
            f"'{speaker}' is QualCoder's speaker segmentation coder, not an "
            f"analyst; its rows are speaker turns and cannot be compared as "
            f"codings.")

    def test_the_speaker_coder_is_never_auto_selected(self, setup_server,
                                                      qualcoder_db_path):
        speaker = "\U0001F4CC Speaker coding"
        _code(qualcoder_db_path, 72, 1, 2, 0, 5, speaker)
        _code(qualcoder_db_path, 73, 1, 2, 6, 9, SECOND)
        _reopen(qualcoder_db_path)
        out = _compare()
        assert {out["coder_a"], out["coder_b"]} == {"TestCoder", SECOND}

    def test_unknown_ids(self, two_coders):
        assert _compare("TestCoder", SECOND, code_ids=[9])["error"] == \
            "Code ID 9 does not exist"
        assert _compare("TestCoder", SECOND, file_ids=[9])["error"] == \
            "File ID 9 does not exist"
        assert _compare("TestCoder", SECOND, case_ids=[9])["error"] == \
            "Case ID 9 does not exist"

    def test_a_non_text_file(self, two_coders):
        H.execute(two_coders,
                  "INSERT INTO source (id,name,fulltext,mediapath,memo,"
                  "owner,date) VALUES (5,'clip.mp3',NULL,'/audio/clip.mp3',"
                  "'','TestCoder','2024-01-15')")
        _reopen(two_coders)
        out = _compare("TestCoder", SECOND, file_ids=[5])
        assert out["error"] == (
            "File ID 5 is not a text file; compare_coders covers text "
            "codings only. QualCoder's Coder comparison by file report "
            "compares image and audio/video codings; this tool does not "
            "reproduce those.")

    def test_lists_too_long(self, two_coders):
        out = _compare("TestCoder", SECOND, code_ids=list(range(1, 250)))
        assert out["error"] == "code_ids has 249 entries; the maximum is 200."
        out = _compare("TestCoder", SECOND, file_ids=list(range(1, 600)))
        assert out["error"] == "file_ids has 599 entries; the maximum is 500."

    def test_case_scope(self, two_coders):
        out = _compare("TestCoder", SECOND, case_ids=[1])
        assert out["scope"]["case_ids"] == [1]
        assert out["scope"]["files"] >= 0

    def test_an_empty_scope_is_a_valid_result(self, two_coders):
        H.execute(two_coders, "UPDATE source SET fulltext = '' WHERE id != 0")
        _reopen(two_coders)
        out = _compare("TestCoder", SECOND)
        assert out["scope"]["characters"] == 0
        row = _row(out, "Stress")
        assert row["kappa_note"] == cc.NOTE_NO_CHARACTERS
        assert row["agreement_pct"] is None


class TestResultShape:

    def test_the_contract_texts_are_verbatim(self, two_coders):
        out = _compare("TestCoder", SECOND)
        assert out["unit_of_analysis"] == server.UNIT_OF_ANALYSIS
        assert out["method"] == server.COMPARISON_METHOD
        assert "reports.py:1140-1151 at QualCoder master 9bddf17" in \
            out["method"]["kappa_qualcoder"]
        assert "It is not Cohen's kappa" in out["method"]["kappa_qualcoder"]

    def test_the_four_coder_roles(self, setup_server, qualcoder_db_path):
        from qualcoder_mcp.project_settings import (DEFAULT_AI_CODER_NAME,
                                                    KNOWN_AI_ASSISTANT_OWNER)
        speaker = "\U0001F4CC Speaker coding"
        for ctid, owner in ((80, DEFAULT_AI_CODER_NAME),
                            (81, KNOWN_AI_ASSISTANT_OWNER),
                            (82, speaker)):
            _code(qualcoder_db_path, ctid, 1, 2, ctid - 80, ctid - 79, owner)
        _reopen(qualcoder_db_path)
        out = _compare("TestCoder", DEFAULT_AI_CODER_NAME)
        assert out["coder_roles"]["TestCoder"] == "human_or_unknown"
        assert out["coder_roles"][DEFAULT_AI_CODER_NAME] == "ai_this_server"
        out = _compare("TestCoder", KNOWN_AI_ASSISTANT_OWNER)
        assert out["coder_roles"][KNOWN_AI_ASSISTANT_OWNER] == \
            "known_ai_assistant"
        assert "labels, not facts" in out["coder_roles_note"].lower()

    def test_the_ai_coder_name_is_reported_and_never_asked_for(
            self, setup_server_unset, qualcoder_db_path):
        _code(qualcoder_db_path, 83, 1, 1, 31, 60, SECOND)
        _reopen(qualcoder_db_path)
        out = _compare("TestCoder", SECOND)
        assert out["ai_coder_name"] is None
        assert out["ai_coder_name_source"] == "unset"
        assert "error" not in out

    def test_no_text_memo_or_position_reaches_the_result(self, two_coders):
        H.execute(two_coders,
                  "UPDATE code_text SET memo = ? WHERE ctid = 1",
                  ("public #####secret private note",))
        _reopen(two_coders)
        raw = server.compare_coders("TestCoder", SECOND, per_file=True)
        assert "#####secret" not in raw
        assert "private note" not in raw
        assert "seltext" not in raw
        assert "position_start" not in raw
        assert "I feel stressed" not in raw

    def test_include_subcodes_adds_rows_and_never_merges(self, two_coders):
        out = _compare("TestCoder", SECOND, code_ids=[1],
                       include_subcodes=True)
        assert [r["code_id"] for r in out["per_code"]] == [1]
        assert out["scope"]["include_subcodes"] is True

    def test_per_file_row_cap(self, two_coders, monkeypatch):
        monkeypatch.setattr(server, "MAX_LIMIT", 1)
        out = _compare("TestCoder", SECOND, per_file=True)
        assert "the maximum is 1." in out["error"]
        assert "Narrow the request" in out["error"]

    def test_it_is_read_only_and_takes_no_backup(self, two_coders):
        parent = Path(two_coders).parent
        before = sorted(p.name for p in parent.glob("*_backup_*"))
        _compare("TestCoder", SECOND)
        assert sorted(p.name for p in parent.glob("*_backup_*")) == before
        assert server.db.read_only is True

    def test_determinism_and_the_recycle(self, two_coders):
        """The ground rule for every item: nothing is cached between
        calls, so a recycled process gives byte-identical output."""
        first = server.compare_coders("TestCoder", SECOND, per_file=True)
        server.db = None
        server.current_project_path = None
        json.loads(server.select_project(two_coders))
        second = server.compare_coders("TestCoder", SECOND, per_file=True)
        assert first == second


class TestVisibility:

    @pytest.fixture
    def hidden(self, setup_server, qualcoder_db_path):
        from test_qc40_visibility import _apply_visibility_schema, HIDDEN
        _apply_visibility_schema(qualcoder_db_path)
        _reopen(qualcoder_db_path)
        return qualcoder_db_path, HIDDEN

    def test_naming_a_hidden_coder_is_refused_name_free(self, hidden):
        project, name = hidden
        raw = server.compare_coders("TestCoder", name)
        out = json.loads(raw)
        assert out["error"] == server.HIDDEN_COMPARISON_REFUSAL
        assert name not in raw
        assert not re.search(r"\b\d+\b", out["error"])

    def test_the_override_compares_and_discloses(self, hidden):
        project, name = hidden
        out = _compare("TestCoder", name, allow_hidden_coder=True)
        assert out["coder_visibility"]["hidden_coder_filter"] == "bypassed"
        assert out["coder_visibility"]["hidden_coders"] >= 1

    def test_hidden_coders_are_not_listed_in_error_texts(self, hidden):
        project, name = hidden
        out = _compare("TestCoder", "Nobody")
        assert name not in out["error"]
        assert "hidden in QualCoder" in out["error"]

    def test_visible_pair_on_a_project_that_hides_others(self, hidden):
        project, name = hidden
        _code(project, 90, 1, 1, 31, 60, SECOND)
        _reopen(project)
        out = _compare("TestCoder", SECOND)
        assert out["coder_visibility"]["hidden_coder_filter"] == \
            "not_applicable"
        assert out["coder_visibility"]["hidden_coders"] >= 1

    def test_without_the_capability_the_boolean_is_accepted_and_ignored(
            self, two_coders):
        out = _compare("TestCoder", SECOND, allow_hidden_coder=True)
        assert "coder_visibility" not in out
        assert out["per_code"]

    def test_logs_carry_counts_not_coder_names(self, two_coders, caplog):
        import logging
        with caplog.at_level(logging.INFO):
            _compare("TestCoder", SECOND)
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "TestCoder" not in text
        assert SECOND not in text


class TestFrequenciesExportRider:
    """B3.7 (ruling Q11): the JSON result of export_frequencies_csv named
    hidden coders in the conversation. The FILE is unchanged, for parity
    with QualCoder's own report."""

    @pytest.fixture
    def hidden(self, setup_server, qualcoder_db_path):
        from test_qc40_visibility import _apply_visibility_schema, HIDDEN
        _apply_visibility_schema(qualcoder_db_path)
        _reopen(qualcoder_db_path)
        return qualcoder_db_path, HIDDEN

    def test_the_result_names_visible_coders_only(self, hidden, tmp_path):
        project, name = hidden
        raw = server.export_frequencies_csv(str(tmp_path / "freq.csv"))
        out = json.loads(raw)
        assert out["success"] is True
        assert name not in out["coders"]
        assert "TestCoder" in out["coders"]
        assert out["coder_visibility"]["hidden_coders"] == 1
        assert name not in raw

    def test_the_exported_file_still_carries_every_coder(self, hidden,
                                                         tmp_path):
        project, name = hidden
        target = tmp_path / "freq.csv"
        server.export_frequencies_csv(str(target))
        text = target.read_text(encoding="utf-8")
        assert name in text, "the FILE keeps QualCoder parity"

    def test_a_project_with_nothing_hidden_carries_no_block(
            self, setup_server, qualcoder_db_path, tmp_path):
        out = json.loads(server.export_frequencies_csv(
            str(tmp_path / "freq.csv")))
        assert "coder_visibility" not in out


class TestSurface:

    def test_absent_from_the_core_toolset(self):
        """Ruling Q10: a reduced surface for local models does not need a
        statistics tool."""
        assert "compare_coders" not in server.CORE_TOOLSET
        assert "compare_coders" in server.mcp._tool_manager._tools

    def test_no_row_level_field_is_called_plain_kappa(self, two_coders):
        """X3: the only `kappa` key is QualCoder's own column inside the
        disclosure block, under the GUI's own name."""
        out = _compare("TestCoder", SECOND, per_file=True)
        for row in out["per_code"]:
            assert "kappa" not in row
            assert "kappa_qualcoder" in row and "kappa_cohen" in row
            for file_row in row.get("files", []):
                assert "kappa" not in file_row


class TestHouseRulesOnTheNewTexts:

    def test_every_new_text_of_this_item(self):
        texts = [
            server.UNIT_OF_ANALYSIS,
            server.HIDDEN_COMPARISON_REFUSAL,
            cc.NOTE_NO_CHARACTERS,
            cc.NOTE_NO_VARIANCE,
            cc.NOTE_BOTH_CODED_ALL,
            cc.SAME_CODER_OVERLAP_NOTE,
            server.compare_coders.__doc__ or "",
        ] + list(server.COMPARISON_METHOD.values())
        _house_rules(texts)
