"""QA v0.8 gate — surfaces 1-2: inductive coding + edit_suggestion/span
alternatives. Independent adversarial verification of the contract (A.1-A.6),
the 16 panel deltas and the 10 demanded fixtures. Driven through registered
tools only.
"""

import json
import sqlite3
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server
from qualcoder_mcp.database import QualcoderDatabase

FULLTEXT = "This is interview text. I feel stressed about deadlines. I cope by exercising."


def _db(p) -> Path:
    return Path(p) / "data.qda"


def _exec(p, sql, args=()):
    conn = sqlite3.connect(str(_db(p)))
    conn.execute(sql, args)
    conn.commit()
    conn.close()


def _one(p, sql, args=()):
    conn = sqlite3.connect(str(_db(p)))
    r = conn.execute(sql, args).fetchone()
    conn.close()
    return r


def _reload():
    server.switch_project(server.current_project_path)


def _sid():
    return json.loads(server.analyze_for_coding([1]))["session_id"]


def _add_file(p, fid, name, text):
    _exec(p, "INSERT INTO source (id, name, fulltext) VALUES (?,?,?)",
          (fid, name, text))
    _reload()


def _record_one(sid, fid, segment, code="Stress", **kw):
    body = {"file_id": fid, "code_name": code, "segment_text": segment}
    body.update(kw)
    rec = json.loads(server.record_suggestions(sid, [body]))
    assert rec["recorded_count"] == 1, rec
    return rec["recorded"][0]


# =============================================================================
# Surface 1 — INDUCTIVE CODING
# =============================================================================

class TestInductiveLoop:

    def test_end_to_end_propose_refine_merge_create(self, setup_server,
                                                    qualcoder_db_path):
        sid = _sid()
        pp = json.loads(server.propose_codes(sid, [
            {"name": "Recovery rituals", "memo": "def A", "rationale": "r A",
             "example_segments": [{"file_id": 1,
                                   "segment_text": "I cope by exercising"}]},
            {"name": "Deadline dread", "memo": "def B", "rationale": "r B",
             "example_segments": [{"file_id": 1,
                                   "segment_text": FULLTEXT[24:55]}]},
        ]))
        assert pp["recorded_count"] == 2, pp
        g1, g2 = [r["guid"] for r in pp["recorded"]]
        # evidence positions verified by the record machinery
        assert pp["recorded"][0]["evidence_count"] == 1

        # refine: rename + recolor + memo
        up = json.loads(server.update_proposal(
            sid, g1, name="Recovery practices", color="#F5F6CE",
            memo="refined def"))
        assert up.get("success") is True, up

        # merge proposals: g2's evidence unions into g1; g2 rejected
        mg = json.loads(server.merge_proposals(sid, g2, g1))
        assert mg.get("success") is True, mg
        info = json.loads(server.get_coding_session_info(sid))
        props = {p["guid"]: p for p in info["proposed_codes"]}
        assert props[g2]["status"] == "rejected"
        assert len(props[g1]["example_segments"]) == 2  # unioned evidence

        # review shows the refined name
        rv = server.review_proposals(sid, show_examples=True)
        assert "Recovery practices" in rv

        # approve + create WITHOUT applying evidence
        server.update_proposal_status(sid, approve=[g1])
        before = _one(qualcoder_db_path, "SELECT COUNT(*) FROM code_text")[0]
        cr = json.loads(server.create_proposed_codes(sid,
                                                     apply_coded_segments=False))
        assert cr["success"] is True, cr
        assert cr["codings_applied"] == 0
        assert _one(qualcoder_db_path,
                    "SELECT COUNT(*) FROM code_text")[0] == before  # A-Q4 honored
        row = _one(qualcoder_db_path,
                   "SELECT name, color, memo FROM code_name WHERE name = ?",
                   ("Recovery practices",))
        assert row is not None and row[1] == "#F5F6CE" and row[2] == "refined def"
        # rejected proposal (g2) never created
        assert _one(qualcoder_db_path,
                    "SELECT COUNT(*) FROM code_name WHERE name='Deadline dread'"
                    )[0] == 0
        # created proposal now immutable
        assert "error" in json.loads(server.update_proposal(sid, g1, name="X"))
        st = json.loads(server.update_proposal_status(sid, approve=[g1]))
        assert st["skipped_created"] == 1
        # then the deductive loop can apply the new code (composition)
        rec = _record_one(sid, 1, "I cope by exercising",
                          code="Recovery practices")
        server.update_suggestion_status(sid, approve=[rec["guid"]])
        assert "CODINGS APPLIED" in server.apply_codings(sid,
                                                         create_backup=False)

    def test_create_with_apply_coded_segments_true(self, setup_server,
                                                   qualcoder_db_path):
        sid = _sid()
        pp = json.loads(server.propose_codes(sid, [
            {"name": "Evidence code", "example_segments": [
                {"file_id": 1, "segment_text": FULLTEXT[24:55]},
                {"file_id": 1, "segment_text": "I cope by exercising"}]}]))
        g = pp["recorded"][0]["guid"]
        server.update_proposal_status(sid, approve=[g])
        cr = json.loads(server.create_proposed_codes(
            sid, apply_coded_segments=True))
        assert cr["success"] is True and cr["codings_applied"] == 2
        cid = cr["created_codes"][0]["code_id"]
        # P1 invariant on the applied evidence
        bad = _one(qualcoder_db_path,
                   "SELECT COUNT(*) FROM code_text ct JOIN source s ON ct.fid=s.id "
                   "WHERE ct.cid=? AND ct.seltext != "
                   "substr(s.fulltext, ct.pos0+1, ct.pos1-ct.pos0)", (cid,))
        assert bad[0] == 0

    def test_all_or_nothing_on_mid_create_fault(self, setup_server,
                                                qualcoder_db_path, monkeypatch):
        sid = _sid()
        pp = json.loads(server.propose_codes(sid, [
            {"name": "First ok"}, {"name": "Second explodes"}]))
        guids = [r["guid"] for r in pp["recorded"]]
        server.update_proposal_status(sid, approve=guids)

        original = QualcoderDatabase.add_code
        calls = {"n": 0}

        def add_code_flaky(self, *a, **k):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("simulated failure on the second create")
            return original(self, *a, **k)

        monkeypatch.setattr(QualcoderDatabase, "add_code", add_code_flaky)
        out = json.loads(server.create_proposed_codes(sid))
        assert "error" in out
        # NOTHING partial: neither code exists, no proposal marked created
        for name in ("First ok", "Second explodes"):
            assert _one(qualcoder_db_path,
                        "SELECT COUNT(*) FROM code_name WHERE name=?",
                        (name,))[0] == 0
        info = json.loads(server.get_coding_session_info(sid))
        assert all(p["status"] != "created" for p in info["proposed_codes"])

    def test_collision_flag_at_propose_hard_block_at_create(
            self, setup_server, qualcoder_db_path):
        sid = _sid()
        # case-variant of the existing 'Stress' code -> flagged, not rejected
        pp = json.loads(server.propose_codes(sid, [{"name": "stress"}]))
        assert pp["recorded_count"] == 1
        assert pp["recorded"][0].get("collides_with") == "Stress"
        g = pp["recorded"][0]["guid"]
        server.update_proposal_status(sid, approve=[g])

        folder = Path(qualcoder_db_path)
        n_backups = len(list(folder.parent.glob(f"{folder.stem}_backup_*.qda")))
        n_codes = _one(qualcoder_db_path, "SELECT COUNT(*) FROM code_name")[0]
        out = json.loads(server.create_proposed_codes(sid))
        assert "error" in out or out.get("failures"), out
        # hard block: nothing written, no backup litter
        assert _one(qualcoder_db_path,
                    "SELECT COUNT(*) FROM code_name")[0] == n_codes
        assert len(list(folder.parent.glob(f"{folder.stem}_backup_*.qda"))) \
            == n_backups

    def test_sibling_batch_collision_rejected_in_batch(self, setup_server):
        sid = _sid()
        pp = json.loads(server.propose_codes(sid, [
            {"name": "Alpha theme"}, {"name": "alpha theme"}]))
        assert pp["recorded_count"] == 1
        assert pp["rejected_count"] == 1

    def test_missing_category_refused_at_create(self, setup_server,
                                                qualcoder_db_path):
        sid = _sid()
        pp = json.loads(server.propose_codes(
            sid, [{"name": "Orphan cat code", "category": "Ghost category"}]))
        # whether flagged at propose or later, creation must refuse (Q-A3)
        if pp["recorded_count"] == 1:
            g = pp["recorded"][0]["guid"]
            server.update_proposal_status(sid, approve=[g])
            out = json.loads(server.create_proposed_codes(sid))
            assert "error" in out or out.get("failures"), out
            assert _one(qualcoder_db_path,
                        "SELECT COUNT(*) FROM code_name "
                        "WHERE name='Orphan cat code'")[0] == 0
        else:
            assert "not found" in json.dumps(pp["rejected"]).lower() \
                or "category" in json.dumps(pp["rejected"]).lower()

    def test_evidence_position_machinery_unicode(self, setup_server,
                                                 qualcoder_db_path):
        emoji = "Intro 😀 emoji here. The pressure was 本当に intense that week. End."
        _add_file(qualcoder_db_path, 60, "emoji.txt", emoji)
        sid = _sid()
        seg = "The pressure was 本当に intense that week."
        pp = json.loads(server.propose_codes(sid, [
            {"name": "Pressure 圧", "example_segments": [
                {"file_id": 60, "start_pos": 3, "end_pos": 9,  # wrong on purpose
                 "segment_text": seg}]}]))
        assert pp["recorded_count"] == 1, pp
        assert "position_safety_warning" in pp
        info = json.loads(server.get_coding_session_info(sid))
        ev = info["proposed_codes"][0]["example_segments"][0]
        assert emoji[ev["start_pos"]:ev["end_pos"]] == seg  # auto-located
        # bogus evidence: proposal recorded, the bad span reported and
        # NOT silently stored (evidence_rejected per-item report)
        pp2 = json.loads(server.propose_codes(sid, [
            {"name": "Bad evidence", "example_segments": [
                {"file_id": 60, "segment_text": "NOT IN FILE"}]}]))
        assert pp2["recorded_count"] == 1
        bad = pp2["recorded"][0]
        assert bad["evidence_count"] == 0
        assert bad["evidence_rejected"][0]["provided_snippet"] == "NOT IN FILE"

    def test_pre_v08_session_json_loads_and_works(self, setup_server,
                                                  qualcoder_db_path):
        """Zero-migration claim: a hand-built v0.7-shaped session file (no
        proposed_codes, suggestions without span_alternatives) loads, lists,
        accepts proposals AND suggestions."""
        import uuid
        sid = str(uuid.uuid4())
        v07 = {
            "session_id": sid,   # on-disk session format: key UNCHANGED
            "created_at": "2026-07-10T10:00:00",
            "last_modified": "2026-07-10T10:00:00",
            "project_path": server.current_project_path,
            "description": "pre-v0.8 session",
            "file_ids": [1], "code_names": ["Stress"],
            "instruction": "", "min_confidence": 0.6,
            "suggestions": [{
                "file_id": 1, "file_name": "interview.txt",
                "code_id": 1, "code_name": "Stress",
                "start_pos": 24, "end_pos": 55,
                "segment_text": FULLTEXT[24:55],
                "reasoning": "old", "confidence": 0.8, "status": "pending",
                "context_before": "", "context_after": "",
                "guid": str(uuid.uuid4()),
            }],
        }
        f = server.session_manager.storage_dir / f"session_{sid}.json"
        f.write_text(json.dumps(v07), encoding="utf-8")

        info = json.loads(server.get_coding_session_info(sid))
        assert "error" not in info
        old_guid = info["suggestions"][0]["guid"]
        # span machinery works on the old suggestion
        ed = json.loads(server.edit_suggestion(sid, old_guid,
                                               segment_text="I feel stressed"))
        assert ed.get("success") is True, ed
        # proposals can ride the old session
        pp = json.loads(server.propose_codes(sid, [{"name": "New in old"}]))
        assert pp["recorded_count"] == 1
        server.update_proposal_status(sid, approve=[pp["recorded"][0]["guid"]])
        assert json.loads(server.create_proposed_codes(sid))["success"] is True


# =============================================================================
# Surface 2 — EDIT_SUGGESTION + SPAN ALTERNATIVES (16 deltas / 10 fixtures)
# =============================================================================

TURNS = ("Anna: I felt completely fine that day and everything seemed calm. "
         "It was honestly good. It really was a nice day overall.\n"
         "Ben: But then things changed quite a lot for everyone involved.\n"
         "Anna: Yes indeed, that is very true.")

DR_TEXT = ("Dr. Smith spoke first about the design. The methods included "
           "1. sampling and 2. coding, which took a long time to complete. "
           "End of the section here.")


class TestEditSuggestionContract:

    def test_pending_only_with_per_status_hints(self, setup_server):
        sid = _sid()
        r1 = _record_one(sid, 1, FULLTEXT[24:55])
        server.update_suggestion_status(sid, approve=[r1["guid"]])
        out = json.loads(server.edit_suggestion(sid, r1["guid"],
                                                segment_text="I feel"))
        assert "error" in out
        assert "update_suggestion_status" in out["error"]  # decision hint

        r2 = _record_one(sid, 1, "I cope by exercising")
        server.update_suggestion_status(sid, reject=[r2["guid"]])
        out = json.loads(server.edit_suggestion(sid, r2["guid"],
                                                segment_text="I cope"))
        assert "error" in out  # rejected: refused, not silently re-opened

        r3 = _record_one(sid, 1, "This is interview text.")
        server.update_suggestion_status(sid, approve=[r3["guid"]])
        server.apply_codings(sid, create_backup=False)
        out = json.loads(server.edit_suggestion(sid, r3["guid"],
                                                segment_text="This is"))
        assert "error" in out
        assert "APPLIED" in out["error"] and "delete_coding" in out["error"]

    def test_mutual_exclusion_and_miss_contract(self, setup_server):
        sid = _sid()
        r = _record_one(sid, 1, FULLTEXT[24:55])
        out = json.loads(server.edit_suggestion(
            sid, r["guid"], use_alternative="longer", start_pos=0, end_pos=5))
        assert "error" in out  # exclusive
        out = json.loads(server.edit_suggestion(sid, r["guid"],
                                                use_alternative="banana"))
        assert "error" in out
        out = json.loads(server.edit_suggestion(sid, r["guid"]))
        assert "error" in out  # nothing to change

    def test_duplicate_landing_refused(self, setup_server):
        sid = _sid()
        a = _record_one(sid, 1, FULLTEXT[24:55])
        b = _record_one(sid, 1, "I cope by exercising")
        out = json.loads(server.edit_suggestion(
            sid, b["guid"], start_pos=24, end_pos=55,
            segment_text=FULLTEXT[24:55], new_code="Stress"))
        # wait: b is Stress already? b was recorded with code Stress; landing
        # on a's exact (file, code, span) must refuse
        assert "error" in out, out

    def test_changes_span_from_is_the_undo_path(self, setup_server):
        sid = _sid()
        r = _record_one(sid, 1, FULLTEXT[24:55])
        ed = json.loads(server.edit_suggestion(sid, r["guid"],
                                               segment_text="I feel stressed"))
        assert ed["changes"]["span"]["from"] == "24-55"
        f0, f1 = (int(x) for x in ed["changes"]["span"]["from"].split("-"))
        undo = json.loads(server.edit_suggestion(
            sid, r["guid"], start_pos=f0, end_pos=f1,
            segment_text=FULLTEXT[f0:f1]))
        assert undo.get("success") is True


class TestSpanAlternativeDeltas:

    def test_delta1_recompute_at_use_not_stale(self, setup_server,
                                               qualcoder_db_path):
        multi = ("Alpha sentence begins the text right here. Beta sentence is "
                 "clearly much longer than all others around. Gamma closes it.")
        _add_file(qualcoder_db_path, 70, "re.txt", multi)
        sid = _sid()
        r = _record_one(sid, 70, "Beta sentence is clearly much longer than "
                                 "all others around.")
        assert "longer" in r["alternatives"]
        # the file changes UNDER the session (prefix grows by 12 chars)
        _exec(qualcoder_db_path,
              "UPDATE source SET fulltext = ? WHERE id = 70",
              ("PREPENDED!! " + multi,))
        _reload()
        # stored alternatives are presentational; use must recompute and
        # refuse/relocate against the CURRENT text, never write a stale slice
        out = json.loads(server.edit_suggestion(sid, r["guid"],
                                                use_alternative="longer"))
        if out.get("success"):
            info = json.loads(server.get_coding_session_info(sid))
            sugg = next(s for s in info["suggestions"]
                        if s["guid"] == r["guid"])
            cur = _one(qualcoder_db_path,
                       "SELECT fulltext FROM source WHERE id = 70")[0]
            assert cur[sugg["start_pos"]:sugg["end_pos"]] \
                == sugg["segment_text"]
        else:
            assert "error" in out  # honest refusal also acceptable

    def test_delta3_shorter_is_longest_contained_sentence(
            self, setup_server, qualcoder_db_path):
        _add_file(qualcoder_db_path, 71, "sh.txt",
                  "Tiny one. This middle sentence is by far the longest of the "
                  "three here. Small end.")
        sid = _sid()
        r = _record_one(sid, 71, "Tiny one. This middle sentence is by far the "
                                 "longest of the three here. Small end.")
        assert "shorter" in r["alternatives"]
        ed = json.loads(server.edit_suggestion(sid, r["guid"],
                                               use_alternative="shorter"))
        assert ed["segment_text"].startswith("This middle sentence")

    def test_delta4_degeneracy_floor_no_shorter_on_single_sentence(
            self, setup_server, qualcoder_db_path):
        _add_file(qualcoder_db_path, 72, "single.txt",
                  "Only a short line. And more text follows here afterwards.")
        sid = _sid()
        r = _record_one(sid, 72, "Only a short line.")
        assert "shorter" not in r["alternatives"]

    def test_delta5_paragraph_boundaries_uniform(self, setup_server,
                                                 qualcoder_db_path):
        for fid, name, sep in ((73, "lf.txt", "\n\n"),
                               (74, "crlf.txt", "\r\n\r\n"),
                               (75, "u2029.txt", " ")):
            text = (f"Lead sentence of paragraph one. Second sentence of it."
                    f"{sep}Target sentence sits in paragraph two here. "
                    f"Companion sentence follows it closely.{sep}Third para.")
            _add_file(qualcoder_db_path, fid, name, text)
            sid = _sid()
            r = _record_one(sid, fid,
                            "Target sentence sits in paragraph two here.")
            assert "longer" in r["alternatives"], name
            ed = json.loads(server.edit_suggestion(sid, r["guid"],
                                                   use_alternative="longer"))
            assert ed.get("success") is True, (name, ed)
            # paragraph-two content only — never crosses the separator
            assert "paragraph one" not in ed["segment_text"], name
            assert "Third para" not in ed["segment_text"], name
            assert "Companion sentence" in ed["segment_text"], name

    def test_delta6_cap_falls_back_to_pm1_sentence(self, setup_server,
                                                   qualcoder_db_path):
        giant = " ".join(f"Filler sentence number {i} keeps flowing onward."
                         for i in range(200))  # one huge paragraph ~9000 cp
        giant = giant[:4000] + " The tiny target lives here. " + giant[4000:]
        _add_file(qualcoder_db_path, 76, "giant.txt", giant)
        sid = _sid()
        r = _record_one(sid, 76, "The tiny target lives here.")
        assert "longer" in r["alternatives"]
        ed = json.loads(server.edit_suggestion(sid, r["guid"],
                                               use_alternative="longer"))
        # capped: NOT the whole 9000-cp paragraph
        assert len(ed["segment_text"]) < 1500
        assert "The tiny target lives here." in ed["segment_text"]

    def test_delta7_8_speaker_turns_label_strip_and_splice_guard(
            self, setup_server, qualcoder_db_path):
        _add_file(qualcoder_db_path, 77, "turns.txt", TURNS)
        sid = _sid()
        r = _record_one(sid, 77, "It was honestly good.")
        assert "longer" in r["alternatives"]
        ed = json.loads(server.edit_suggestion(sid, r["guid"],
                                               use_alternative="longer"))
        seg = ed["segment_text"]
        # full speaker turn, label stripped from the quote start
        assert not seg.startswith("Anna:")
        assert seg.startswith("I felt completely fine")
        # the splice guard: NEVER crosses into Ben's turn
        assert "Ben:" not in seg and "things changed" not in seg
        # slice fidelity retained (positions match the real fulltext)
        info = json.loads(server.get_coding_session_info(sid))
        sugg = next(s for s in info["suggestions"] if s["guid"] == r["guid"])
        assert TURNS[sugg["start_pos"]:sugg["end_pos"]] == seg

    def test_delta9_fixture_dr_and_numbered_list_robust(self, setup_server,
                                                        qualcoder_db_path):
        _add_file(qualcoder_db_path, 78, "dr.txt", DR_TEXT)
        sid = _sid()
        span = ("Dr. Smith spoke first about the design. The methods included "
                "1. sampling and 2. coding, which took a long time to complete.")
        r = _record_one(sid, 78, span)
        if "shorter" in r["alternatives"]:
            ed = json.loads(server.edit_suggestion(sid, r["guid"],
                                                   use_alternative="shorter"))
            # QA7-2 (LOW, reported): the 'fragments lose' claim is heuristic —
            # here the list fragment 'coding, which took…' IS the longest
            # segmentation piece and wins. Deterministic and slice-valid, but
            # a mid-sentence fragment. Pinned so a heuristic change is seen.
            assert ed["segment_text"] == (
                "coding, which took a long time to complete.")
            f0 = DR_TEXT.index(ed["segment_text"])
            assert DR_TEXT[f0:f0 + len(ed["segment_text"])] == ed["segment_text"]

    def test_delta10_no_dead_affordances(self, setup_server,
                                         qualcoder_db_path):
        """Every offered alternative must be usable — none may die on the
        materiality/no-effective-change guard."""
        _add_file(qualcoder_db_path, 79, "aff.txt",
                  "One clear sentence sits here. A second follows with much "
                  "more words than before. Third ends the paragraph.\n\n"
                  "Second paragraph exists too.")
        json.loads(server.create_code("AffB", create_backup=False))
        sid = _sid()
        offered = []
        for seg, code in (
                ("A second follows with much more words than before.",
                 "Stress"),
                ("One clear sentence sits here. A second follows with "
                 "much more words than before.", "AffB")):
            r = _record_one(sid, 79, seg, code=code)
            for label in r["alternatives"]:
                offered.append(label)
                out = json.loads(server.edit_suggestion(
                    sid, r["guid"], use_alternative=label))
                assert out.get("success") is True, (seg, label, out)
                # move back for the next label using the undo path
                f0, f1 = (int(x) for x in
                          out["changes"]["span"]["from"].split("-"))
                cur = _one(qualcoder_db_path,
                           "SELECT fulltext FROM source WHERE id=79")[0]
                server.edit_suggestion(sid, r["guid"], start_pos=f0,
                                       end_pos=f1, segment_text=cur[f0:f1])
        assert offered  # the test must have exercised something

    def test_delta11_12_token_rules_on_30_batch(self, setup_server,
                                                qualcoder_db_path):
        para = " ".join(
            f"Statement {i:02d} about workload appears in this batch here."
            for i in range(35))
        _add_file(qualcoder_db_path, 80, "batch.txt", para)
        sid = _sid()
        batch = []
        for i in range(30):
            seg = f"Statement {i:02d} about workload appears in this batch here."
            batch.append({"file_id": 80, "code_name": "Stress",
                          "segment_text": seg})
        raw = server.record_suggestions(sid, batch)
        rec = json.loads(raw)
        assert rec["recorded_count"] == 30
        # labels-only: no gloss/preview strings in the record output
        assert "chars)" not in raw and "↔" not in raw and "“" not in raw
        # compact review listing: affordance labels allowed, but NO quoted
        # preview text (that is the token rule)
        compact = server.review_suggestions(sid)
        assert "“" not in compact and "”" not in compact
        # a <=5-guid show_context subset DOES carry the alternatives detail
        subset = [r["guid"] for r in rec["recorded"][:2]]
        detail = server.review_suggestions(sid, subset, show_context=True)
        assert "↔" in detail or "longer" in detail or "shorter" in detail

    def test_delta13_hints_shortcut_once_and_escalation_at_3(
            self, setup_server, qualcoder_db_path):
        _add_file(qualcoder_db_path, 81, "hints.txt",
                  "First target sentence stands alone here. Filler one ends."
                  "\n\nSecond target sentence stands alone too. Filler two "
                  "ends.\n\nThird target sentence also stands alone. Filler "
                  "three ends.")
        sid = _sid()
        guids = []
        for seg in ("First target sentence stands alone here.",
                    "Second target sentence stands alone too.",
                    "Third target sentence also stands alone."):
            guids.append(_record_one(sid, 81, seg)["guid"])

        # first MANUAL span edit -> one-time span_shortcut_hint
        e1 = json.loads(server.edit_suggestion(
            sid, guids[0], segment_text="First target sentence stands alone "
                                        "here. Filler one ends."))
        assert "span_shortcut_hint" in e1, e1
        e1b = json.loads(server.edit_suggestion(
            sid, guids[0], segment_text="First target sentence stands alone here."))
        assert "span_shortcut_hint" not in e1b  # once only

        # three same-direction picks -> calibration hint on the third
        picks = []
        for g in guids:
            r = json.loads(server.edit_suggestion(sid, g,
                                                  use_alternative="longer"))
            picks.append(r)
        assert any("calibration_hint" in p for p in picks[2:]), picks[2]
        hint_msg = json.dumps(picks)
        assert "instruction" in hint_msg or "session" in hint_msg  # session-level fix

    def test_delta14_adjusted_rendering_no_further_offers(self, setup_server):
        sid = _sid()
        r = _record_one(sid, 1, FULLTEXT[24:55])
        json.loads(server.edit_suggestion(sid, r["guid"],
                                          segment_text="I feel stressed"))
        listing = server.review_suggestions(sid)
        assert "(adjusted)" in listing

    def test_delta15_verbatim_docstring_scripts(self):
        rec_doc = server.record_suggestions.__doc__ or ""
        edit_doc = server.edit_suggestion.__doc__ or ""
        assert "alternatives" in rec_doc
        assert "use_alternative" in edit_doc
        # the scripted affordance line mentions offering the labels to the user
        assert "shorter" in (rec_doc + edit_doc) and "longer" in (rec_doc + edit_doc)

    def test_fixture_emoji_unsafe_slice_integrity(self, setup_server,
                                                  qualcoder_db_path):
        emoji = ("Intro 😀 line to unsettle offsets. The key claim sentence "
                 "sits right here in the middle. Trailing sentence closes 🎯 "
                 "the paragraph.")
        _add_file(qualcoder_db_path, 82, "emoji2.txt", emoji)
        sid = _sid()
        r = _record_one(sid, 82,
                        "The key claim sentence sits right here in the middle.")
        for label in r["alternatives"]:
            ed = json.loads(server.edit_suggestion(sid, r["guid"],
                                                   use_alternative=label))
            assert ed.get("success") is True, (label, ed)
            info = json.loads(server.get_coding_session_info(sid))
            sugg = next(s for s in info["suggestions"]
                        if s["guid"] == r["guid"])
            assert emoji[sugg["start_pos"]:sugg["end_pos"]] \
                == sugg["segment_text"]  # code-point slice fidelity

    def test_fixture_file_edge_omissions(self, setup_server,
                                         qualcoder_db_path):
        text = "Edge sentence opens the file. Second one follows here."
        _add_file(qualcoder_db_path, 83, "edge.txt", text)
        sid = _sid()
        # whole file as span: nothing longer exists
        r = _record_one(sid, 83, text)
        assert "longer" not in r["alternatives"]

    def test_fixture_mid_word_span(self, setup_server, qualcoder_db_path):
        text = ("Complete sentences live in this file. The interesting "
                "fragment appears midway through it. Final sentence here.")
        _add_file(qualcoder_db_path, 84, "midword.txt", text)
        sid = _sid()
        # span starting mid-word ('teresting fragment ... midway')
        start = text.index("teresting")
        end = text.index("midway") + len("midway")
        r = _record_one(sid, 84, text[start:end], start_pos=start, end_pos=end)
        for label in r["alternatives"]:
            ed = json.loads(server.edit_suggestion(sid, r["guid"],
                                                   use_alternative=label))
            assert ed.get("success") is True
            # result is a trimmed, whole-word span (no leading/trailing space)
            assert ed["segment_text"] == ed["segment_text"].strip()
            f0, f1 = (int(x) for x in ed["changes"]["span"]["from"].split("-"))
            server.edit_suggestion(sid, r["guid"], start_pos=f0, end_pos=f1,
                                   segment_text=text[f0:f1])
