"""v0.8 Phase A — inductive / open coding (contract A.1-A.6).

The six-tool loop: propose_codes records brand-new code proposals on the
session (writing nothing to the project), review_proposals shows them,
update_proposal / merge_proposals refine them, update_proposal_status
records the user's decisions, and create_proposed_codes performs the one
atomic backed-up write. Covers: session-only guarantees, collision
flag-then-block, refuse-on-missing-category, evidence validation with
authoritative slices, apply_coded_segments default-off, created
immutability, and the lock gate.
"""

import json
import sqlite3
import time
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


def _propose_one(session_id, name="Deadline pressure", **overrides):
    proposal = {
        "name": name,
        "memo": "Talk about time pressure from deadlines",
        "rationale": "Recurs across the interview",
        "example_segments": [{
            "file_id": 1,
            "segment_text": "I feel stressed about deadlines",
        }],
    }
    proposal.update(overrides)
    out = json.loads(server.propose_codes(session_id, [proposal]))
    assert out["recorded_count"] == 1, out
    return out["recorded"][0]["guid"]


def _code_names(project_path):
    conn = sqlite3.connect(str(Path(project_path) / "data.qda"))
    try:
        return {r[0] for r in conn.execute("SELECT name FROM code_name")}
    finally:
        conn.close()


def _coding_count(project_path):
    conn = sqlite3.connect(str(Path(project_path) / "data.qda"))
    try:
        return conn.execute("SELECT COUNT(*) FROM code_text").fetchone()[0]
    finally:
        conn.close()


# ============================================================================
# A.1 propose_codes
# ============================================================================

class TestA1ProposeCodes:

    def test_session_required(self, setup_server):
        out = json.loads(server.propose_codes("nope", [{"name": "X"}]))
        assert "not found" in out["error"]

    def test_proposals_must_be_nonempty_list(self, setup_server):
        sid = _make_session(setup_server)
        out = json.loads(server.propose_codes(sid, []))
        assert "non-empty list" in out["error"]

    def test_basic_recording_is_session_only(self, setup_server,
                                             qualcoder_db_path):
        """Recording a proposal writes NOTHING to the project database."""
        before = _code_names(qualcoder_db_path)
        sid = _make_session(setup_server)
        guid = _propose_one(sid)
        assert _code_names(qualcoder_db_path) == before
        # but it IS persisted on the session file
        session = server.session_manager.load_session(sid)
        p = session.get_proposal_by_guid(guid)
        assert p is not None and p.status == "pending"
        assert p.memo.startswith("Talk about")

    def test_name_required(self, setup_server):
        sid = _make_session(setup_server)
        out = json.loads(server.propose_codes(sid, [{"memo": "no name"}]))
        assert out["recorded_count"] == 0
        assert "name" in out["rejected"][0]["reason"]

    def test_duplicate_name_within_session_refused(self, setup_server):
        sid = _make_session(setup_server)
        _propose_one(sid, name="Support seeking")
        out = json.loads(server.propose_codes(
            sid, [{"name": "SUPPORT SEEKING"}]))
        assert out["recorded_count"] == 0
        assert "already" in out["rejected"][0]["reason"]

    def test_invalid_color_rejected(self, setup_server):
        sid = _make_session(setup_server)
        out = json.loads(server.propose_codes(
            sid, [{"name": "X", "color": "#zzzzzz"}]))
        assert out["recorded_count"] == 0
        assert "#RRGGBB" in out["rejected"][0]["reason"]

    def test_unknown_category_rejected_with_options(self, setup_server):
        sid = _make_session(setup_server)
        out = json.loads(server.propose_codes(
            sid, [{"name": "X", "category": "Nope"}]))
        assert out["recorded_count"] == 0
        assert "not found" in out["rejected"][0]["reason"]
        assert "Category A" in out["rejected"][0]["available_categories"]

    def test_category_name_canonicalized(self, setup_server):
        sid = _make_session(setup_server)
        out = json.loads(server.propose_codes(
            sid, [{"name": "X", "category": "category a"}]))
        assert out["recorded"][0]["category"] == "Category A"

    def test_collision_flagged_not_blocked(self, setup_server):
        """A name matching an existing code (case-insensitively) is flagged
        collides_with but still recorded — blocking happens at creation."""
        sid = _make_session(setup_server)
        out = json.loads(server.propose_codes(
            sid, [{"name": "stress"}]))
        assert out["recorded_count"] == 1
        assert out["recorded"][0]["collides_with"] == "Stress"
        assert "collision_note" in out

    def test_evidence_validated_with_authoritative_slice(self, setup_server):
        """Positions omitted -> unique-locate fills them; slice stored."""
        sid = _make_session(setup_server)
        guid = _propose_one(sid)
        session = server.session_manager.load_session(sid)
        seg = session.get_proposal_by_guid(guid).example_segments[0]
        assert seg["start_pos"] == 24 and seg["end_pos"] == 55
        assert seg["segment_text"] == "I feel stressed about deadlines"
        assert FULLTEXT[seg["start_pos"]:seg["end_pos"]] == seg["segment_text"]

    def test_bad_evidence_rejected_proposal_still_recorded(self, setup_server):
        sid = _make_session(setup_server)
        out = json.loads(server.propose_codes(sid, [{
            "name": "X",
            "example_segments": [
                {"file_id": 1, "segment_text": "text that is not there"},
                {"file_id": 1, "segment_text": "I cope by exercising"},
            ],
        }]))
        assert out["recorded_count"] == 1
        entry = out["recorded"][0]
        assert entry["evidence_count"] == 1
        assert len(entry["evidence_rejected"]) == 1

    def test_replace_discards_pending_only(self, setup_server):
        sid = _make_session(setup_server)
        keep = _propose_one(sid, name="Keeper")
        server.update_proposal_status(sid, approve=[keep])
        _propose_one(sid, name="Draft")
        out = json.loads(server.propose_codes(
            sid, [{"name": "Fresh"}], replace=True))
        assert out["replaced_pending"] == 1
        session = server.session_manager.load_session(sid)
        names = {p.name for p in session.proposed_codes}
        assert names == {"Keeper", "Fresh"}

    def test_position_safety_warning_relayed(self, setup_server,
                                             qualcoder_db_path):
        conn = sqlite3.connect(str(Path(qualcoder_db_path) / "data.qda"))
        conn.execute(
            "INSERT INTO source (id, name, fulltext, owner, date) "
            "VALUES (9, 'emoji.txt', 'Fun \U0001F600 marker here', 'T', '2024-01-01')")
        conn.commit()
        conn.close()
        sid = _make_session(setup_server)
        out = json.loads(server.propose_codes(sid, [{
            "name": "X",
            "example_segments": [{"file_id": 9, "segment_text": "marker here"}],
        }]))
        assert "position_safety_warning" in out


# ============================================================================
# A.2 review_proposals
# ============================================================================

class TestA2ReviewProposals:

    def test_shows_definition_and_collision(self, setup_server):
        sid = _make_session(setup_server)
        _propose_one(sid, name="stress")
        out = server.review_proposals(sid)
        assert "stress" in out
        assert "Collides with existing code: Stress" in out
        assert "PENDING" in out

    def test_specific_guids_and_examples(self, setup_server):
        sid = _make_session(setup_server)
        g1 = _propose_one(sid, name="One")
        _propose_one(sid, name="Two")
        out = server.review_proposals(sid, proposal_guids=[g1],
                                      show_examples=True)
        assert "One" in out and "Two" not in out
        assert "I feel stressed about deadlines" in out

    def test_empty(self, setup_server):
        sid = _make_session(setup_server)
        assert "No proposals" in server.review_proposals(sid)


# ============================================================================
# A.3 update_proposal
# ============================================================================

class TestA3UpdateProposal:

    def test_rename_refreshes_collision_both_ways(self, setup_server):
        sid = _make_session(setup_server)
        guid = _propose_one(sid, name="Fresh name")
        out = json.loads(server.update_proposal(sid, guid, name="Coping"))
        assert out["collides_with"] == "Coping"
        out = json.loads(server.update_proposal(sid, guid, name="Fresh again"))
        assert "collides_with" not in out
        session = server.session_manager.load_session(sid)
        assert session.get_proposal_by_guid(guid).collides_with is None

    def test_rename_clash_with_sibling_refused(self, setup_server):
        sid = _make_session(setup_server)
        _propose_one(sid, name="One")
        g2 = _propose_one(sid, name="Two")
        out = json.loads(server.update_proposal(sid, g2, name="ONE"))
        assert "already named" in out["error"]

    def test_category_set_and_clear(self, setup_server):
        sid = _make_session(setup_server)
        guid = _propose_one(sid)
        out = json.loads(server.update_proposal(sid, guid,
                                                category="category a"))
        assert out["changes"]["category"]["to"] == "Category A"
        out = json.loads(server.update_proposal(sid, guid, category=""))
        assert out["changes"]["category"]["to"] is None
        out = json.loads(server.update_proposal(sid, guid, category="Ghost"))
        assert "not found" in out["error"]

    def test_color_and_memo(self, setup_server):
        sid = _make_session(setup_server)
        guid = _propose_one(sid)
        assert "error" in json.loads(
            server.update_proposal(sid, guid, color="red"))
        out = json.loads(server.update_proposal(
            sid, guid, color="#AA00BB", memo="Sharper definition"))
        session = server.session_manager.load_session(sid)
        p = session.get_proposal_by_guid(guid)
        assert p.color == "#AA00BB" and p.memo == "Sharper definition"

    def test_nothing_to_change(self, setup_server):
        sid = _make_session(setup_server)
        guid = _propose_one(sid)
        assert "Nothing to change" in json.loads(
            server.update_proposal(sid, guid))["error"]

    def test_created_proposals_immutable(self, setup_server):
        sid = _make_session(setup_server)
        guid = _propose_one(sid)
        server.update_proposal_status(sid, approve=[guid])
        assert "error" not in json.loads(server.create_proposed_codes(sid))
        out = json.loads(server.update_proposal(sid, guid, name="Rename"))
        assert "already created" in out["error"]
        assert "rename_code" in out["error"]


# ============================================================================
# A.4 merge_proposals
# ============================================================================

class TestA4MergeProposals:

    def test_merge_moves_evidence_and_rejects_source(self, setup_server):
        sid = _make_session(setup_server)
        g1 = _propose_one(sid, name="Deadline stress")
        g2 = _propose_one(sid, name="Time pressure", example_segments=[
            {"file_id": 1, "segment_text": "I feel stressed about deadlines"},
            {"file_id": 1, "segment_text": "I cope by exercising"},
        ])
        out = json.loads(server.merge_proposals(sid, g2, g1))
        assert out["success"] is True
        # duplicate span deduplicated: only the coping segment moves
        assert out["evidence_moved"] == 1
        assert out["target"]["evidence_count"] == 2
        session = server.session_manager.load_session(sid)
        assert session.get_proposal_by_guid(g2).status == "rejected"

    def test_self_merge_refused(self, setup_server):
        sid = _make_session(setup_server)
        g1 = _propose_one(sid)
        out = json.loads(server.merge_proposals(sid, g1, g1))
        assert "itself" in out["error"]

    def test_created_participant_refused(self, setup_server):
        sid = _make_session(setup_server)
        g1 = _propose_one(sid, name="Created one")
        g2 = _propose_one(sid, name="Pending one")
        server.update_proposal_status(sid, approve=[g1])
        server.create_proposed_codes(sid)
        out = json.loads(server.merge_proposals(sid, g2, g1))
        assert "merge_codes" in out["error"]

    def test_missing_guid_refused(self, setup_server):
        sid = _make_session(setup_server)
        g1 = _propose_one(sid)
        out = json.loads(server.merge_proposals(sid, g1, "ghost"))
        assert "must exist" in out["error"]


# ============================================================================
# A.5 update_proposal_status
# ============================================================================

class TestA5UpdateProposalStatus:

    def test_approve_reject_and_stats(self, setup_server):
        sid = _make_session(setup_server)
        g1 = _propose_one(sid, name="One")
        g2 = _propose_one(sid, name="Two")
        g3 = _propose_one(sid, name="Three")
        out = json.loads(server.update_proposal_status(
            sid, approve=[g1, g2], reject=[g3]))
        assert out["approved"] == 2 and out["rejected"] == 1
        stats = out["proposal_statistics"]
        assert stats["approved"] == 2 and stats["rejected"] == 1
        assert stats["pending"] == 0

    def test_created_skipped(self, setup_server):
        sid = _make_session(setup_server)
        g1 = _propose_one(sid)
        server.update_proposal_status(sid, approve=[g1])
        server.create_proposed_codes(sid)
        out = json.loads(server.update_proposal_status(sid, reject=[g1]))
        assert out["skipped_created"] == 1 and out["rejected"] == 0


# ============================================================================
# A.6 create_proposed_codes
# ============================================================================

class TestA6CreateProposedCodes:

    def test_no_approved_proposals(self, setup_server):
        sid = _make_session(setup_server)
        _propose_one(sid)
        out = json.loads(server.create_proposed_codes(sid))
        assert "No approved proposals" in out["error"]

    def test_default_creates_codes_only(self, setup_server,
                                        qualcoder_db_path):
        """apply_coded_segments defaults to False: codes land, codings
        do NOT, palette colour fills in, category is honoured, the
        proposal flips to created, and a backup is made."""
        sid = _make_session(setup_server)
        guid = _propose_one(sid, category="Category A")
        server.update_proposal_status(sid, approve=[guid])
        codings_before = _coding_count(qualcoder_db_path)

        out = json.loads(server.create_proposed_codes(sid))
        assert out["success"] is True
        assert out["codings_applied"] == 0
        assert "backup_path" in out
        assert "Deadline pressure" in _code_names(qualcoder_db_path)
        assert _coding_count(qualcoder_db_path) == codings_before

        conn = sqlite3.connect(str(Path(qualcoder_db_path) / "data.qda"))
        row = conn.execute(
            "SELECT color, catid, memo FROM code_name WHERE name = ?",
            ("Deadline pressure",)).fetchone()
        conn.close()
        assert row[0] and row[0].startswith("#")  # palette pick
        assert row[1] == 1                        # Category A
        assert "time pressure" in row[2]

        session = server.session_manager.load_session(sid)
        p = session.get_proposal_by_guid(guid)
        assert p.status == "created"
        assert p.created_code_id == out["created_codes"][0]["code_id"]

    def test_apply_coded_segments_writes_evidence(self, setup_server,
                                                  qualcoder_db_path):
        sid = _make_session(setup_server)
        guid = _propose_one(sid)
        server.update_proposal_status(sid, approve=[guid])
        out = json.loads(server.create_proposed_codes(
            sid, apply_coded_segments=True))
        assert out["codings_applied"] == 1
        conn = sqlite3.connect(str(Path(qualcoder_db_path) / "data.qda"))
        row = conn.execute(
            "SELECT seltext, pos0, pos1, owner, memo FROM code_text "
            "WHERE cid = ?", (out["created_codes"][0]["code_id"],)).fetchone()
        conn.close()
        assert row[0] == "I feel stressed about deadlines"
        assert (row[1], row[2]) == (24, 55)
        assert row[3] == "AI Coding Assistant"
        assert "Recurs across the interview" in row[4]

    def test_collision_blocks_atomically(self, setup_server,
                                         qualcoder_db_path):
        """A case-variant collision with the live codebook refuses the WHOLE
        batch before any backup or write (flag-then-block)."""
        sid = _make_session(setup_server)
        g_ok = _propose_one(sid, name="Safe code")
        g_bad = _propose_one(sid, name="STRESS")
        server.update_proposal_status(sid, approve=[g_ok, g_bad])
        before = _code_names(qualcoder_db_path)

        out = json.loads(server.create_proposed_codes(sid))
        assert "failed validation" in out["error"]
        reasons = {f["name"]: f["reason"] for f in out["failures"]}
        assert "collides with existing code 'Stress'" in reasons["STRESS"]
        assert _code_names(qualcoder_db_path) == before
        # no backup folder appeared
        parent = Path(qualcoder_db_path).parent
        assert not list(parent.glob("*_backup_*"))
        # rename resolves it
        server.update_proposal(sid, g_bad, name="Strain")
        out = json.loads(server.create_proposed_codes(sid))
        assert out["success"] is True
        assert {"Safe code", "Strain"} <= _code_names(qualcoder_db_path)

    def test_batch_duplicate_names_refused(self, setup_server):
        """propose_codes/update_proposal already refuse in-session name
        clashes, so reach the create-time belt-and-braces guard the only
        way it can be reached: a hand-edited session file."""
        sid = _make_session(setup_server)
        g1 = _propose_one(sid, name="Echo")
        g2 = _propose_one(sid, name="Other")
        session = server.session_manager.load_session(sid)
        session.get_proposal_by_guid(g2).name = "echo "
        server.session_manager.save_session(session)
        server.update_proposal_status(sid, approve=[g1, g2])
        out = json.loads(server.create_proposed_codes(sid))
        assert "same name" in json.dumps(out["failures"])

    def test_vanished_category_refused(self, setup_server,
                                       qualcoder_db_path):
        """Category deleted between approval and creation -> refuse (Q-A3)."""
        sid = _make_session(setup_server)
        guid = _propose_one(sid, category="Category A")
        server.update_proposal_status(sid, approve=[guid])
        conn = sqlite3.connect(str(Path(qualcoder_db_path) / "data.qda"))
        conn.execute("DELETE FROM code_cat WHERE catid = 1")
        conn.commit()
        conn.close()
        out = json.loads(server.create_proposed_codes(sid))
        assert "does not exist" in out["failures"][0]["reason"]
        assert "create_category" in out["failures"][0]["reason"]

    def test_evidence_drift_refused_when_applying(self, setup_server,
                                                  qualcoder_db_path):
        """File text changed after approval: creation with
        apply_coded_segments must refuse, not write shifted codings."""
        sid = _make_session(setup_server)
        guid = _propose_one(sid)
        server.update_proposal_status(sid, approve=[guid])
        conn = sqlite3.connect(str(Path(qualcoder_db_path) / "data.qda"))
        conn.execute("UPDATE source SET fulltext = ? WHERE id = 1",
                     ("EDITED. " + FULLTEXT,))
        conn.commit()
        conn.close()
        out = json.loads(server.create_proposed_codes(
            sid, apply_coded_segments=True))
        assert "no longer" in out["failures"][0]["reason"]
        # codes-only creation is unaffected by drift
        out = json.loads(server.create_proposed_codes(sid))
        assert out["success"] is True

    def test_lock_gate_refuses(self, setup_server, qualcoder_db_path):
        sid = _make_session(setup_server)
        guid = _propose_one(sid)
        server.update_proposal_status(sid, approve=[guid])
        lock = Path(qualcoder_db_path) / "project_in_use.lock"
        lock.write_text(f"gemma\n{time.time()}", encoding="utf-8")
        try:
            out = json.loads(server.create_proposed_codes(sid))
            assert "error" in out
            assert "close the project" in out["error"].lower() or \
                "qualcoder" in out["error"].lower()
        finally:
            lock.unlink()

    def test_second_run_reports_already_created(self, setup_server):
        sid = _make_session(setup_server)
        guid = _propose_one(sid)
        server.update_proposal_status(sid, approve=[guid])
        assert json.loads(server.create_proposed_codes(sid))["success"]
        out = json.loads(server.create_proposed_codes(sid))
        assert "already created" in out["error"]

    def test_rejected_never_created(self, setup_server, qualcoder_db_path):
        sid = _make_session(setup_server)
        g1 = _propose_one(sid, name="Wanted")
        g2 = _propose_one(sid, name="Unwanted")
        server.update_proposal_status(sid, approve=[g1], reject=[g2])
        out = json.loads(server.create_proposed_codes(sid))
        assert out["success"] is True
        names = _code_names(qualcoder_db_path)
        assert "Wanted" in names and "Unwanted" not in names
