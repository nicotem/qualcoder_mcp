"""v0.8 tester-feedback fold-in (TESTER_FEEDBACK_01.md, findings F1/F2).

F1a: edit_suggestion — adjust a PENDING suggestion's span and/or code at
review time, re-verified with the record_suggestions position machinery.
F1a-extension: proposal evidence spans editable the same way via
update_proposal(example_segments=...).
F1b/F2: guidance — review context on by default; span-style and
co-coding direction in the docstrings and explain_ai_coding_tools.
"""

import json
import sqlite3
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server

FULLTEXT = ("This is interview text. I feel stressed about deadlines. "
            "I cope by exercising.")


def _make_session(setup_server):
    out = server.analyze_for_coding([1])
    return out.split("Session ID: `")[1].split("`")[0]


def _record_one(sid, **overrides):
    item = {"file_id": 1, "code_name": "Stress",
            "segment_text": "stressed about deadlines",
            "reasoning": "explicit stress talk", "confidence": 0.9}
    item.update(overrides)
    out = json.loads(server.record_suggestions(sid, [item]))
    assert out["recorded_count"] == 1, out
    return out["recorded"][0]["guid"]


# ============================================================================
# F1a — edit_suggestion: span editing
# ============================================================================

class TestF1aEditSpan:

    def test_widen_by_positions(self, setup_server):
        """The tester's #1 friction: AI span too short for a quotable
        extract — widen it at review time by positions alone."""
        sid = _make_session(setup_server)
        guid = _record_one(sid)  # "stressed about deadlines" (31-55)
        out = json.loads(server.edit_suggestion(sid, guid,
                                                start_pos=24, end_pos=55))
        assert out["success"] is True
        assert out["changes"]["span"]["to"] == "24-55"
        assert out["segment_text"] == "I feel stressed about deadlines"
        # authoritative slice re-stored on the session
        session = server.session_manager.load_session(sid)
        s = session.get_suggestion_by_guid(guid)
        assert (s.start_pos, s.end_pos) == (24, 55)
        assert s.segment_text == FULLTEXT[24:55]

    def test_single_bound_keeps_other(self, setup_server):
        sid = _make_session(setup_server)
        guid = _record_one(sid)  # 31-55
        out = json.loads(server.edit_suggestion(sid, guid, start_pos=24))
        assert out["changes"]["span"]["to"] == "24-55"

    def test_new_segment_text_unique_locate(self, setup_server):
        """A replacement excerpt without positions is located exactly
        like record_suggestions does."""
        sid = _make_session(setup_server)
        guid = _record_one(sid)
        out = json.loads(server.edit_suggestion(
            sid, guid, segment_text="I cope by exercising"))
        assert out["success"] is True
        assert out["changes"]["span"]["to"] == "57-77"
        assert out["segment_text"] == "I cope by exercising"

    def test_context_refreshed(self, setup_server):
        sid = _make_session(setup_server)
        guid = _record_one(sid)
        server.edit_suggestion(sid, guid, segment_text="I cope by exercising")
        session = server.session_manager.load_session(sid)
        s = session.get_suggestion_by_guid(guid)
        assert s.context_before.endswith("deadlines. ")
        assert s.context_after == FULLTEXT[77:]

    def test_bad_positions_refused(self, setup_server):
        sid = _make_session(setup_server)
        guid = _record_one(sid)
        out = json.loads(server.edit_suggestion(sid, guid, end_pos=10_000))
        assert "file length" in out["error"]
        out = json.loads(server.edit_suggestion(sid, guid,
                                                start_pos=30, end_pos=20))
        assert "error" in out

    def test_unlocatable_text_refused(self, setup_server):
        sid = _make_session(setup_server)
        guid = _record_one(sid)
        out = json.loads(server.edit_suggestion(
            sid, guid, segment_text="text that is not in the file"))
        assert "error" in out
        # span unchanged
        session = server.session_manager.load_session(sid)
        s = session.get_suggestion_by_guid(guid)
        assert (s.start_pos, s.end_pos) == (31, 55)


# ============================================================================
# F1a — edit_suggestion: code change and gates
# ============================================================================

class TestF1aEditCodeAndGates:

    def test_change_code_by_name(self, setup_server):
        sid = _make_session(setup_server)
        guid = _record_one(sid)
        out = json.loads(server.edit_suggestion(sid, guid,
                                                code_name="coping"))
        assert out["changes"]["code"] == {"from": "Stress", "to": "Coping"}
        session = server.session_manager.load_session(sid)
        s = session.get_suggestion_by_guid(guid)
        assert (s.code_id, s.code_name) == (2, "Coping")

    def test_unknown_code_refused(self, setup_server):
        sid = _make_session(setup_server)
        guid = _record_one(sid)
        out = json.loads(server.edit_suggestion(sid, guid,
                                                code_name="Ghost"))
        assert "not found" in out["error"]
        assert "Stress" in out["available_codes"]

    def test_pending_only(self, setup_server):
        """Applied is immutable; approved/rejected reflect decisions —
        each refusal carries its own hint (flagged design call)."""
        sid = _make_session(setup_server)
        guid = _record_one(sid)
        server.update_suggestion_status(sid, approve=[guid])
        out = json.loads(server.edit_suggestion(sid, guid, start_pos=24))
        assert "APPROVED" in out["error"]
        assert "CODINGS APPLIED" in server.apply_codings(
            sid, create_backup=False)
        out = json.loads(server.edit_suggestion(sid, guid, start_pos=24))
        assert "APPLIED" in out["error"]
        assert "delete_coding" in out["error"]

    def test_rejected_refused_with_hint(self, setup_server):
        sid = _make_session(setup_server)
        guid = _record_one(sid)
        server.update_suggestion_status(sid, reject=[guid])
        out = json.loads(server.edit_suggestion(sid, guid, start_pos=24))
        assert "REJECTED" in out["error"]

    def test_no_change_params(self, setup_server):
        sid = _make_session(setup_server)
        guid = _record_one(sid)
        out = json.loads(server.edit_suggestion(sid, guid))
        assert "Nothing to change" in out["error"]

    def test_noop_edit_refused(self, setup_server):
        sid = _make_session(setup_server)
        guid = _record_one(sid)
        out = json.loads(server.edit_suggestion(sid, guid,
                                                start_pos=31, end_pos=55))
        assert "No effective change" in out["error"]

    def test_duplicate_collision_refused(self, setup_server):
        """An edit may not land exactly on another suggestion (would
        double-apply)."""
        sid = _make_session(setup_server)
        g1 = _record_one(sid)                              # Stress 31-55
        g2 = _record_one(sid, segment_text="I cope by exercising")
        out = json.loads(server.edit_suggestion(
            sid, g2, start_pos=31, end_pos=55))
        assert "duplicate" in out["error"]
        assert g1 in out["error"]

    def test_edited_span_applies_cleanly(self, setup_server,
                                         qualcoder_db_path):
        """End-to-end: widen, approve, apply — the coding lands with the
        widened span and the authoritative seltext."""
        sid = _make_session(setup_server)
        guid = _record_one(sid)
        server.edit_suggestion(sid, guid, start_pos=24, end_pos=55)
        server.update_suggestion_status(sid, approve=[guid])
        out = server.apply_codings(sid, create_backup=False)
        assert "Successfully Applied: 1" in out
        conn = sqlite3.connect(str(Path(qualcoder_db_path) / "data.qda"))
        row = conn.execute(
            "SELECT pos0, pos1, seltext FROM code_text "
            "WHERE owner='AI Coding Assistant'").fetchone()
        conn.close()
        assert (row[0], row[1]) == (24, 55)
        assert row[2] == "I feel stressed about deadlines"


# ============================================================================
# F1a extension — proposal evidence spans via update_proposal
# ============================================================================

class TestF1aProposalEvidence:

    def _propose(self, sid):
        out = json.loads(server.propose_codes(sid, [{
            "name": "Deadline pressure",
            "example_segments": [{"file_id": 1,
                                  "segment_text": "stressed about deadlines"}],
        }]))
        return out["recorded"][0]["guid"]

    def test_replace_evidence_with_wider_span(self, setup_server):
        sid = _make_session(setup_server)
        guid = self._propose(sid)
        out = json.loads(server.update_proposal(sid, guid, example_segments=[
            {"file_id": 1, "start_pos": 24, "end_pos": 55,
             "segment_text": "I feel stressed about deadlines"},
        ]))
        assert out["success"] is True
        assert out["changes"]["example_segments"]["to"] == "1 span(s)"
        session = server.session_manager.load_session(sid)
        seg = session.get_proposal_by_guid(guid).example_segments[0]
        assert (seg["start_pos"], seg["end_pos"]) == (24, 55)

    def test_all_invalid_replacement_keeps_old(self, setup_server):
        sid = _make_session(setup_server)
        guid = self._propose(sid)
        out = json.loads(server.update_proposal(sid, guid, example_segments=[
            {"file_id": 1, "segment_text": "not in the file at all"},
        ]))
        assert "evidence unchanged" in out["error"]
        session = server.session_manager.load_session(sid)
        assert len(session.get_proposal_by_guid(guid).example_segments) == 1

    def test_empty_list_clears_evidence(self, setup_server):
        sid = _make_session(setup_server)
        guid = self._propose(sid)
        out = json.loads(server.update_proposal(sid, guid,
                                                example_segments=[]))
        assert out["success"] is True
        session = server.session_manager.load_session(sid)
        assert session.get_proposal_by_guid(guid).example_segments == []


# ============================================================================
# F1b/F2 — guidance surfaced where the model reads it
# ============================================================================

class TestF1bF2Guidance:

    def test_review_context_default_on(self, setup_server):
        sid = _make_session(setup_server)
        _record_one(sid)
        out = server.review_suggestions(sid)
        assert "Context Before" in out or "Context After" in out

    def test_docstrings_carry_guidance(self):
        analyze_doc = server.analyze_for_coding.__doc__ or ""
        record_doc = server.record_suggestions.__doc__ or ""
        for doc in (analyze_doc, record_doc):
            assert "complete-thought" in doc.lower()
        assert "CO-CODING" in record_doc and "CO-CODING" in analyze_doc
        assert "calibration signal" in record_doc
        assert "instruction" in analyze_doc          # span-style pattern

    def test_explain_covers_edit_and_style(self):
        out = json.loads(server.explain_ai_coding_tools("edit_suggestion"))
        assert "PENDING" in json.dumps(out)
        out = json.loads(
            server.explain_ai_coding_tools("coding_style_guidance"))
        blob = json.dumps(out)
        assert "complete-thought" in blob
        assert "instruction" in blob


# ============================================================================
# Owner amendment — server-computed span alternatives (shorter/longer)
# ============================================================================

PARA_TEXT = ("Para one sentence A. Para one sentence B.\n\n"
             "Para two sentence C. Para two sentence D.\n\n"
             "Para three sentence E.")


def _add_para_file(project_path, fid=7):
    conn = sqlite3.connect(str(Path(project_path) / "data.qda"))
    conn.execute(
        "INSERT INTO source (id, name, fulltext, owner, date) "
        "VALUES (?, 'paras.txt', ?, 'T', '2024-01-01')", (fid, PARA_TEXT))
    conn.commit()
    conn.close()


class TestAmendmentSpanAlternatives:

    def test_recorded_with_alternatives(self, setup_server):
        """A single-sentence-fragment span gets 'longer' (enclosing
        paragraph) and no meaningless 'shorter'; labels surface in the
        record response."""
        sid = _make_session(setup_server)
        item = {"file_id": 1, "code_name": "Stress",
                "segment_text": "stressed about deadlines"}
        out = json.loads(server.record_suggestions(sid, [item]))
        entry = out["recorded"][0]
        assert entry["alternatives"] == ["longer"]
        session = server.session_manager.load_session(sid)
        alts = session.get_suggestion_by_guid(entry["guid"]).span_alternatives
        assert len(alts) == 1
        a = alts[0]
        assert a["label"] == "longer"
        assert (a["start_pos"], a["end_pos"]) == (0, len(FULLTEXT))
        assert a["length"] == len(FULLTEXT)
        assert a["preview"]  # verbatim slice preview

    def test_use_alternative_applies_and_recomputes(self, setup_server):
        sid = _make_session(setup_server)
        guid = _record_one(sid)
        out = json.loads(server.edit_suggestion(sid, guid,
                                                use_alternative="longer"))
        assert out["success"] is True
        assert out["changes"]["span"]["to"] == f"0-{len(FULLTEXT)}"
        assert out["changes"]["span"]["via"] == "use_alternative=longer"
        # recomputed: whole-file span now offers its central sentence
        labels = [a["label"] for a in out["span_alternatives"]]
        assert labels == ["shorter"]
        out = json.loads(server.edit_suggestion(sid, guid,
                                                use_alternative="shorter"))
        assert out["success"] is True
        session = server.session_manager.load_session(sid)
        s = session.get_suggestion_by_guid(guid)
        assert s.segment_text == "I feel stressed about deadlines."

    def test_mutually_exclusive_with_manual(self, setup_server):
        sid = _make_session(setup_server)
        guid = _record_one(sid)
        out = json.loads(server.edit_suggestion(
            sid, guid, start_pos=0, use_alternative="longer"))
        assert "mutually exclusive" in out["error"]

    def test_unknown_alternative_lists_available(self, setup_server):
        sid = _make_session(setup_server)
        guid = _record_one(sid)
        out = json.loads(server.edit_suggestion(sid, guid,
                                                use_alternative="wider"))
        assert "No 'wider' alternative" in out["error"]
        assert out["available_alternatives"] == ["longer"]

    def test_multi_sentence_span_offers_core_sentence(self, setup_server,
                                                      qualcoder_db_path):
        _add_para_file(qualcoder_db_path)
        sid = _make_session(setup_server)
        seg = "Para two sentence C. Para two sentence D."
        out = json.loads(server.record_suggestions(sid, [
            {"file_id": 7, "code_name": "Stress", "segment_text": seg}]))
        guid = out["recorded"][0]["guid"]
        session = server.session_manager.load_session(sid)
        alts = {a["label"]: a
                for a in session.get_suggestion_by_guid(guid).span_alternatives}
        # shorter: the sentence containing the span midpoint
        sh = alts["shorter"]
        assert PARA_TEXT[sh["start_pos"]:sh["end_pos"]] in (
            "Para two sentence C.", "Para two sentence D.")
        # span == its paragraph, so longer = +/- one sentence into neighbors
        lg = alts["longer"]
        assert PARA_TEXT[lg["start_pos"]:lg["end_pos"]].startswith(
            "Para one sentence B.")
        assert PARA_TEXT[lg["start_pos"]:lg["end_pos"]].endswith(
            "Para three sentence E.")

    def test_alternatives_differ_from_span_and_each_other(self, setup_server,
                                                          qualcoder_db_path):
        _add_para_file(qualcoder_db_path)
        sid = _make_session(setup_server)
        out = json.loads(server.record_suggestions(sid, [
            {"file_id": 7, "code_name": "Stress",
             "segment_text": "Para two sentence C."}]))
        guid = out["recorded"][0]["guid"]
        session = server.session_manager.load_session(sid)
        s = session.get_suggestion_by_guid(guid)
        spans = {(a["start_pos"], a["end_pos"]) for a in s.span_alternatives}
        assert (s.start_pos, s.end_pos) not in spans
        assert len(spans) == len(s.span_alternatives)

    def test_preview_truncated_for_long_spans(self, setup_server,
                                              qualcoder_db_path):
        long_para = ("Sentence one about work stress. " * 12).strip()
        conn = sqlite3.connect(str(Path(qualcoder_db_path) / "data.qda"))
        conn.execute(
            "INSERT INTO source (id, name, fulltext, owner, date) "
            "VALUES (8, 'long.txt', ?, 'T', '2024-01-01')", (long_para,))
        conn.commit()
        conn.close()
        sid = _make_session(setup_server)
        out = json.loads(server.record_suggestions(sid, [
            {"file_id": 8, "code_name": "Stress", "start_pos": 0,
             "end_pos": 31, "segment_text": long_para[:31]}]))
        guid = out["recorded"][0]["guid"]
        session = server.session_manager.load_session(sid)
        lg = session.get_suggestion_by_guid(guid).span_alternatives[-1]
        assert lg["label"] == "longer"
        assert "[…]" in lg["preview"]
        assert len(lg["preview"]) < 140  # token-frugal, not the full quote

    def test_proposal_evidence_carries_alternatives(self, setup_server):
        sid = _make_session(setup_server)
        out = json.loads(server.propose_codes(sid, [{
            "name": "Deadline pressure",
            "example_segments": [{"file_id": 1,
                                  "segment_text": "stressed about deadlines"}],
        }]))
        guid = out["recorded"][0]["guid"]
        session = server.session_manager.load_session(sid)
        seg = session.get_proposal_by_guid(guid).example_segments[0]
        assert [a["label"] for a in seg["span_alternatives"]] == ["longer"]

    def test_review_shows_compact_affordance(self, setup_server):
        sid = _make_session(setup_server)
        _record_one(sid)
        out = server.review_suggestions(sid)
        assert "Span alternatives:" in out
        assert "use_alternative" in out
        # compact: lengths, not the full alternative quote
        assert FULLTEXT not in out
