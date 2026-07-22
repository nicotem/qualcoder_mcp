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
# Owner amendment (design-panel revision) — server-computed span alternatives
# ============================================================================

PARA2 = (
    "The first paragraph of this transcript describes how the project "
    "started and why the team agreed to take it on that spring.\n\n"
    "The participant explains that the reporting cycle left them without "
    "any uninterrupted analysis time. They also describe how the constant "
    "deadline pressure spilled over into their evenings and weekends at "
    "home.\n\n"
    "The closing paragraph summarises the outcome of the project.")

S1 = ("The participant explains that the reporting cycle left them "
      "without any uninterrupted analysis time.")
S2 = ("They also describe how the constant deadline pressure spilled "
      "over into their evenings and weekends at home.")

TRANSCRIPT = (
    "Interviewer: What made the deadlines feel unmanageable to you at "
    "that point in the project?\n"
    "Dana: Honestly the reporting cycle meant I never had two clear days "
    "in a row to do the actual analysis work. It just never stopped for "
    "long enough to think.\n"
    "Interviewer: Can you say more about that?\n")


def _add_file(project_path, fid, name, text):
    conn = sqlite3.connect(str(Path(project_path) / "data.qda"))
    conn.execute(
        "INSERT INTO source (id, name, fulltext, owner, date) "
        "VALUES (?, ?, ?, 'T', '2024-01-01')", (fid, name, text))
    conn.commit()
    conn.close()


class TestAmendmentSpanAlternatives:

    def test_recorded_with_alternatives_labels_only(self, setup_server):
        """Record output carries labels ONLY (token rule 9); the full
        entries (with unit gloss + preview) live on the suggestion."""
        sid = _make_session(setup_server)
        out = json.loads(server.record_suggestions(sid, [
            {"file_id": 1, "code_name": "Stress",
             "segment_text": "stressed about deadlines"}]))
        entry = out["recorded"][0]
        assert entry["alternatives"] == ["longer"]
        assert "preview" not in json.dumps(out)
        session = server.session_manager.load_session(sid)
        alts = session.get_suggestion_by_guid(entry["guid"]).span_alternatives
        assert [(a["label"], a["unit"]) for a in alts] == [
            ("longer", "paragraph")]
        assert (alts[0]["start_pos"], alts[0]["end_pos"]) == (0, len(FULLTEXT))
        assert alts[0]["length"] == len(FULLTEXT)  # code points

    def test_use_alternative_applies_and_recomputes(self, setup_server,
                                                    qualcoder_db_path):
        _add_file(qualcoder_db_path, 10, "para2.txt", PARA2)
        sid = _make_session(setup_server)
        guid = _record_one(sid, file_id=10, segment_text=S1)
        out = json.loads(server.edit_suggestion(sid, guid,
                                                use_alternative="longer"))
        assert out["success"] is True
        assert out["changes"]["span"]["via"] == "use_alternative=longer"
        session = server.session_manager.load_session(sid)
        s = session.get_suggestion_by_guid(guid)
        assert s.segment_text == f"{S1} {S2}"
        # recomputed for the new span: its core sentence is now offered
        assert any(a.startswith("shorter (1 sentence")
                   for a in out["span_alternatives"])
        out = json.loads(server.edit_suggestion(sid, guid,
                                                use_alternative="shorter"))
        assert out["success"] is True
        session = server.session_manager.load_session(sid)
        # longest wholly-contained sentence wins
        assert session.get_suggestion_by_guid(guid).segment_text == max(
            [S1, S2], key=len)

    def test_shorter_is_longest_contained_sentence(self, setup_server,
                                                   qualcoder_db_path):
        _add_file(qualcoder_db_path, 10, "para2.txt", PARA2)
        sid = _make_session(setup_server)
        guid = _record_one(sid, file_id=10, segment_text=f"{S1} {S2}")
        session = server.session_manager.load_session(sid)
        alts = {a["label"]: a for a in
                session.get_suggestion_by_guid(guid).span_alternatives}
        sh = alts["shorter"]
        assert PARA2[sh["start_pos"]:sh["end_pos"]] == max([S1, S2], key=len)
        assert sh["unit"] == "1 sentence"

    def test_mutually_exclusive_with_manual(self, setup_server):
        sid = _make_session(setup_server)
        guid = _record_one(sid)
        out = json.loads(server.edit_suggestion(
            sid, guid, start_pos=0, use_alternative="longer"))
        assert "mutually exclusive" in out["error"]

    def test_miss_contract_lists_available_and_fallback(self, setup_server):
        """Delta 14: on a miss, the error names the labels that DO exist
        and points at the manual fallback (available_codes pattern)."""
        sid = _make_session(setup_server)
        guid = _record_one(sid)
        out = json.loads(server.edit_suggestion(sid, guid,
                                                use_alternative="wider"))
        assert "No 'wider' span alternative" in out["error"]
        assert out["available_alternatives"] == ["longer"]
        assert "start_pos/end_pos" in out["hint"]

    def test_materiality_and_degeneracy_floors(self, setup_server):
        """Short-sentence fixture: the whole-file span's core sentence is
        32 cp < 40 -> shorter omitted; no expansion exists -> longer
        omitted. No filler alternatives, ever."""
        sid = _make_session(setup_server)
        out = json.loads(server.record_suggestions(sid, [
            {"file_id": 1, "code_name": "Stress", "start_pos": 0,
             "end_pos": len(FULLTEXT), "segment_text": FULLTEXT}]))
        entry = out["recorded"][0]
        assert entry["alternatives"] == []

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

    def test_preview_truncated_and_flattened(self, setup_server,
                                             qualcoder_db_path):
        long_para = ("A sentence about workplace stress and reporting.\n"
                     + "More detail on the stress follows here now. " * 10)
        _add_file(qualcoder_db_path, 11, "long.txt",
                  long_para + "\n\nSecond paragraph.")
        sid = _make_session(setup_server)
        out = json.loads(server.record_suggestions(sid, [
            {"file_id": 11, "code_name": "Stress",
             "segment_text": "A sentence about workplace stress and reporting."}]))
        session = server.session_manager.load_session(sid)
        alts = session.get_suggestion_by_guid(
            out["recorded"][0]["guid"]).span_alternatives
        lg = next(a for a in alts if a["label"] == "longer")
        assert "[…]" in lg["preview"]
        assert len(lg["preview"]) < 140       # token-frugal
        assert "\n" not in lg["preview"]      # flattened


class TestAmendmentHintsAndRendering:

    def test_shortcut_hint_fires_once_on_first_manual_edit(
            self, setup_server, qualcoder_db_path):
        _add_file(qualcoder_db_path, 10, "para2.txt", PARA2)
        sid = _make_session(setup_server)
        g1 = _record_one(sid, file_id=10, segment_text=S1)
        g2 = _record_one(sid, file_id=10, segment_text=S2)
        out1 = json.loads(server.edit_suggestion(sid, g1, end_pos=PARA2.index(S2) + len(S2)))
        assert "span_shortcut_hint" in out1
        assert "shorter/longer" in out1["span_shortcut_hint"]
        out2 = json.loads(server.edit_suggestion(sid, g2,
                                                 start_pos=PARA2.index(S1)))
        assert "span_shortcut_hint" not in out2

    def test_calibration_hint_at_three_same_direction_picks(
            self, setup_server, qualcoder_db_path):
        _add_file(qualcoder_db_path, 10, "para2.txt", PARA2)
        sid = _make_session(setup_server)
        guids = [
            _record_one(sid, file_id=1,
                        segment_text="stressed about deadlines"),
            _record_one(sid, file_id=10, segment_text=S1),
            _record_one(sid, file_id=10, segment_text=S2,
                        code_name="Coping"),
        ]
        outs = [json.loads(server.edit_suggestion(
            sid, g, use_alternative="longer")) for g in guids]
        assert "calibration_hint" not in outs[0]
        assert "calibration_hint" not in outs[1]
        assert "paragraph-level" in outs[2]["calibration_hint"]
        # alternative picks never trigger the manual-edit hint
        assert all("span_shortcut_hint" not in o for o in outs)

    def test_adjusted_shown_and_offers_withdrawn(self, setup_server):
        """Delta 15: an edited suggestion renders as adjusted and gets no
        further alternative offers."""
        sid = _make_session(setup_server)
        guid = _record_one(sid)
        server.edit_suggestion(sid, guid, start_pos=24)
        out = server.review_suggestions(sid)
        assert "(adjusted)" in out
        assert "Span alternatives:" not in out

    def test_review_compact_listing_has_no_previews(self, setup_server):
        sid = _make_session(setup_server)
        _record_one(sid)
        # full listing with context: one-line gloss, no preview text
        out = server.review_suggestions(sid)
        assert "Span alternatives: longer (paragraph" in out
        assert "…]" not in out or FULLTEXT[:60] not in out
        # compact listing: nothing at all
        out = server.review_suggestions(sid, show_context=False)
        assert "Span alternatives" not in out

    def test_review_small_subset_shows_previews(self, setup_server):
        sid = _make_session(setup_server)
        guid = _record_one(sid)
        out = server.review_suggestions(sid, suggestion_guids=[guid],
                                        show_context=True)
        assert "longer (paragraph" in out
        assert "This is interview text." in out  # preview visible here


class TestPanelFixtures:
    """The design panel's demanded edge fixtures, by name."""

    def test_pre_v08_session_json_loads_and_use_alternative_works(
            self, setup_server, qualcoder_db_path):
        """A session saved before span_alternatives/adjusted/
        span_edit_stats existed loads unchanged, and use_alternative
        works on it (recompute-at-use needs nothing stored)."""
        sid = _make_session(setup_server)
        guid = _record_one(sid)
        sm = server.session_manager
        path = Path(sm.storage_dir) / f"session_{sid}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data.pop("span_edit_stats", None)
        for s in data["suggestions"]:
            s.pop("span_alternatives", None)
            s.pop("adjusted", None)
        path.write_text(json.dumps(data), encoding="utf-8")
        out = json.loads(server.edit_suggestion(sid, guid,
                                                use_alternative="longer"))
        assert out["success"] is True
        assert out["changes"]["span"]["to"] == f"0-{len(FULLTEXT)}"

    def test_crlf_file_paragraphs(self, setup_server, qualcoder_db_path):
        """\\r\\n\\r\\n is a paragraph boundary (plain \\n\\n never matches
        on CRLF files)."""
        crlf = PARA2.replace("\n", "\r\n")
        _add_file(qualcoder_db_path, 12, "crlf.txt", crlf)
        sid = _make_session(setup_server)
        out = json.loads(server.record_suggestions(sid, [
            {"file_id": 12, "code_name": "Stress", "segment_text": S1}]))
        session = server.session_manager.load_session(sid)
        alts = session.get_suggestion_by_guid(
            out["recorded"][0]["guid"]).span_alternatives
        lg = next(a for a in alts if a["label"] == "longer")
        text = crlf[lg["start_pos"]:lg["end_pos"]]
        assert text.startswith(S1) and text.endswith(S2)
        assert not text.startswith("\r") and not text.endswith("\n")

    def test_speaker_turn_transcript_label_stripped(self, setup_server,
                                                    qualcoder_db_path):
        """Blank-line-free transcript: single newline = turn boundary;
        the 'longer' span is the FULL SPEAKER TURN with the label
        stripped so the quote starts with speech."""
        _add_file(qualcoder_db_path, 13, "transcript.txt", TRANSCRIPT)
        sid = _make_session(setup_server)
        seg = ("Honestly the reporting cycle meant I never had two clear "
               "days in a row to do the actual analysis work.")
        out = json.loads(server.record_suggestions(sid, [
            {"file_id": 13, "code_name": "Stress", "segment_text": seg}]))
        session = server.session_manager.load_session(sid)
        alts = session.get_suggestion_by_guid(
            out["recorded"][0]["guid"]).span_alternatives
        lg = next(a for a in alts if a["label"] == "longer")
        text = TRANSCRIPT[lg["start_pos"]:lg["end_pos"]]
        assert lg["unit"] == "full speaker turn"
        assert text.startswith("Honestly")          # label stripped
        assert "Dana:" not in text
        assert "Interviewer" not in text            # never crosses turns

    def test_turn_boundary_never_spliced(self, setup_server,
                                         qualcoder_db_path):
        """A span that already covers the whole turn gets NO 'longer' —
        the ±1-sentence fallback must not splice the interviewer's
        question into the participant quote."""
        _add_file(qualcoder_db_path, 13, "transcript.txt", TRANSCRIPT)
        sid = _make_session(setup_server)
        whole_turn = ("Honestly the reporting cycle meant I never had two "
                      "clear days in a row to do the actual analysis work. "
                      "It just never stopped for long enough to think.")
        out = json.loads(server.record_suggestions(sid, [
            {"file_id": 13, "code_name": "Stress",
             "segment_text": whole_turn}]))
        labels = out["recorded"][0]["alternatives"]
        assert "longer" not in labels

    def test_u2029_paragraph_separator(self, setup_server,
                                       qualcoder_db_path):
        text = PARA2.replace("\n\n", " ")
        _add_file(qualcoder_db_path, 14, "u2029.txt", text)
        sid = _make_session(setup_server)
        out = json.loads(server.record_suggestions(sid, [
            {"file_id": 14, "code_name": "Stress", "segment_text": S1}]))
        session = server.session_manager.load_session(sid)
        alts = session.get_suggestion_by_guid(
            out["recorded"][0]["guid"]).span_alternatives
        lg = next(a for a in alts if a["label"] == "longer")
        assert text[lg["start_pos"]:lg["end_pos"]] == f"{S1} {S2}"

    def test_unsafe_file_offers_alternatives_with_warning(
            self, setup_server, qualcoder_db_path):
        """Position-unsafe (emoji) file: alternatives are OFFERED
        (warn-don't-block); the slice still satisfies
        fulltext[start:end] == segment_text in code points; applying
        emits the per-edit position_safety_warning."""
        emoji_text = PARA2.replace("spring.", "spring \U0001F600.")
        _add_file(qualcoder_db_path, 15, "emoji.txt", emoji_text)
        sid = _make_session(setup_server)
        out = json.loads(server.record_suggestions(sid, [
            {"file_id": 15, "code_name": "Stress", "segment_text": S1}]))
        guid = out["recorded"][0]["guid"]
        out = json.loads(server.edit_suggestion(sid, guid,
                                                use_alternative="longer"))
        assert out["success"] is True
        assert "position_safety_warning" in out
        session = server.session_manager.load_session(sid)
        s = session.get_suggestion_by_guid(guid)
        assert emoji_text[s.start_pos:s.end_pos] == s.segment_text

    def test_span_at_document_boundaries_omits_longer(self, setup_server,
                                                      qualcoder_db_path):
        one_para = f"{S1} {S2}"
        _add_file(qualcoder_db_path, 16, "onepara.txt", one_para)
        sid = _make_session(setup_server)
        out = json.loads(server.record_suggestions(sid, [
            {"file_id": 16, "code_name": "Stress", "segment_text": one_para}]))
        assert "longer" not in out["recorded"][0]["alternatives"]

    def test_mid_word_span_omits_shorter(self, setup_server):
        """No complete sentence inside a fragment span -> no 'shorter'
        filler."""
        sid = _make_session(setup_server)
        out = json.loads(server.record_suggestions(sid, [
            {"file_id": 1, "code_name": "Stress", "start_pos": 33,
             "end_pos": 50, "segment_text": FULLTEXT[33:50]}]))
        assert "shorter" not in out["recorded"][0]["alternatives"]

    def test_abbreviation_fragments_lose_longest_wins(self, setup_server,
                                                      qualcoder_db_path):
        """'Dr.' and a numbered list over-split the segmentation; the
        longest-wins rule still surfaces the real sentence."""
        text = ("Notes follow.\n\n"
                "1. Dr. Reyes reviewed the case notes carefully before the "
                "session began that morning. 2. The team met after.\n\n"
                "End of notes.")
        _add_file(qualcoder_db_path, 17, "abbrev.txt", text)
        sid = _make_session(setup_server)
        span_text = ("1. Dr. Reyes reviewed the case notes carefully before "
                     "the session began that morning. 2. The team met after.")
        out = json.loads(server.record_suggestions(sid, [
            {"file_id": 17, "code_name": "Stress", "segment_text": span_text}]))
        session = server.session_manager.load_session(sid)
        alts = session.get_suggestion_by_guid(
            out["recorded"][0]["guid"]).span_alternatives
        sh = next(a for a in alts if a["label"] == "shorter")
        picked = text[sh["start_pos"]:sh["end_pos"]]
        assert "reviewed the case notes" in picked
        assert picked not in ("Dr.", "1.", "2.")

    def test_recompute_after_manual_edit(self, setup_server,
                                         qualcoder_db_path):
        _add_file(qualcoder_db_path, 10, "para2.txt", PARA2)
        sid = _make_session(setup_server)
        guid = _record_one(sid, file_id=10, segment_text=S1)
        server.edit_suggestion(
            sid, guid, start_pos=PARA2.index(S1),
            end_pos=PARA2.index(S2) + len(S2))
        session = server.session_manager.load_session(sid)
        alts = session.get_suggestion_by_guid(guid).span_alternatives
        sh = next(a for a in alts if a["label"] == "shorter")
        assert PARA2[sh["start_pos"]:sh["end_pos"]] == max([S1, S2], key=len)
