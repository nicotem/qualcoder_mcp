# SPDX-License-Identifier: LGPL-3.0-or-later
"""B2 (v0.12, D3 3.7): a preview says whose work is at stake.

"20 codings will be deleted" does not tell the researcher whether those
are theirs, this server's, or a colleague's, and that is the question
that decides whether the operation is acceptable. These tests pin the
per-owner breakdown, the warnings, the hidden-coder gate on execute, and
the rule that a hidden coder is a count and never a name.

Parity: the warning texts follow QualCoder 4.0's own non-AI collateral
warnings (`ai_mcp_server.py:1684-1688`, `:1716-1720` and
`ai_llm.py:2955-2960` at 9bddf17), worded for this server's attribution
model; the split between "ours" and "another coder's" is by the project's
AI coder names rather than by upstream's single `owner != 'AI Agent'`
test (`:3234-3236`).
"""

import json
import sqlite3
from pathlib import Path

import pytest

import qualcoder_mcp.server as server
import track5_helpers as H
from qualcoder_mcp.database import QualcoderDatabase
from qualcoder_mcp.project_settings import (DEFAULT_AI_CODER_NAME,
                                            KNOWN_AI_ASSISTANT_OWNER)


def _reopen(project_path):
    if server.db is not None:
        try:
            server.db.close()
        except Exception:
            pass
    server.db = QualcoderDatabase(project_path)


def _add_coding(project_path, ctid, cid, fid, pos0, pos1, owner,
                memo="", text="x"):
    H.execute(project_path,
              "INSERT INTO code_text (ctid, cid, fid, seltext, pos0, pos1, "
              "owner, date, memo, important) VALUES (?,?,?,?,?,?,?,?,?,0)",
              (ctid, cid, fid, text, pos0, pos1, owner, "2024-01-15", memo))


def _preview(tool, *args, **kwargs):
    return json.loads(tool(*args, **kwargs))


def _backups(project_path):
    parent = Path(project_path).parent
    stem = Path(project_path).stem
    return sorted(p.name for p in parent.glob(f"{stem}_backup_*"))


class TestByOwner:

    def test_the_breakdown_names_visible_owners_and_folds_ours(
            self, setup_server, qualcoder_db_path):
        _add_coding(qualcoder_db_path, 50, 1, 1, 5, 9, DEFAULT_AI_CODER_NAME)
        _add_coding(qualcoder_db_path, 51, 1, 1, 10, 14, "Maria")
        _add_coding(qualcoder_db_path, 52, 1, 1, 15, 19, "Maria")
        _reopen(qualcoder_db_path)
        block = _preview(server.delete_code, 1)["preview"]["collateral"]
        assert block["ai_coder_names"] == [DEFAULT_AI_CODER_NAME]
        assert block["ai_owned_codings"] == 1
        assert block["other_owned_codings"] == 3    # 2 Maria + the fixture's
        owners = {e["owner"]: e for e in block["by_owner"]}
        assert DEFAULT_AI_CODER_NAME not in owners, "ours is folded, not listed"
        assert owners["Maria"]["codings"] == 2
        assert owners["Maria"]["text"] == 2
        assert owners["Maria"]["av"] == 0

    def test_sorted_by_count_then_name(self, setup_server,
                                       qualcoder_db_path):
        for ctid, owner in ((60, "Zoe"), (61, "Adam"), (62, "Adam"),
                            (63, "Beth")):
            _add_coding(qualcoder_db_path, ctid, 1, 1, ctid, ctid + 2, owner)
        _reopen(qualcoder_db_path)
        block = _preview(server.delete_code, 1)["preview"]["collateral"]
        names = [e["owner"] for e in block["by_owner"]]
        assert names[0] == "Adam"          # two codings
        assert names[1:] == sorted(names[1:])

    def test_capped_at_twenty_with_a_count(self, setup_server,
                                           qualcoder_db_path):
        for i in range(25):
            _add_coding(qualcoder_db_path, 100 + i, 1, 1, i * 2, i * 2 + 1,
                        f"Coder {i:02d}")
        _reopen(qualcoder_db_path)
        block = _preview(server.delete_code, 1)["preview"]["collateral"]
        assert len(block["by_owner"]) == 20
        assert block["more_owners"] == 6      # 25 plus the fixture's TestCoder

    def test_the_assistant_string_is_labelled_as_a_heuristic(
            self, setup_server, qualcoder_db_path):
        """D3 Q5 and H3: rows under QualCoder 4.0's own assistant string
        that this project has not adopted are another coder's work, with a
        label that says what they probably are."""
        _add_coding(qualcoder_db_path, 70, 1, 1, 5, 9,
                    KNOWN_AI_ASSISTANT_OWNER)
        _reopen(qualcoder_db_path)
        block = _preview(server.delete_code, 1)["preview"]["collateral"]
        entry = next(e for e in block["by_owner"]
                     if e["owner"] == KNOWN_AI_ASSISTANT_OWNER)
        assert entry["known_ai_assistant"] is True

    def test_a_project_that_chose_that_name_owns_those_rows(
            self, setup_server, qualcoder_db_path):
        """Membership of the project's AI names wins over the label."""
        server.set_project_ai_coder_name(KNOWN_AI_ASSISTANT_OWNER)
        _add_coding(qualcoder_db_path, 71, 1, 1, 5, 9,
                    KNOWN_AI_ASSISTANT_OWNER)
        _reopen(qualcoder_db_path)
        block = _preview(server.delete_code, 1)["preview"]["collateral"]
        assert KNOWN_AI_ASSISTANT_OWNER in block["ai_coder_names"]
        assert block["ai_owned_codings"] == 1
        assert KNOWN_AI_ASSISTANT_OWNER not in [e["owner"]
                                                for e in block["by_owner"]]

    def test_a_history_name_still_counts_as_ours(self, setup_server,
                                                 qualcoder_db_path):
        """A name change never re-attributes rows, so rows written under
        the project's EARLIER name are still this server's work (H3)."""
        server.set_project_ai_coder_name("Qwen 3.8 6bit")
        _add_coding(qualcoder_db_path, 72, 1, 1, 5, 9, "Qwen 3.8 6bit")
        server.set_project_ai_coder_name("Llama 4 8bit")
        _reopen(qualcoder_db_path)
        block = _preview(server.delete_code, 1)["preview"]["collateral"]
        assert "Qwen 3.8 6bit" in block["ai_coder_names"]
        assert block["ai_owned_codings"] == 1

    def test_the_code_row_owner_is_reported(self, setup_server,
                                            qualcoder_db_path):
        block = _preview(server.delete_code, 1)["preview"]["collateral"]
        assert block["code_row_owner"] == "TestCoder"

    def test_the_category_tools_report_the_category_owner(
            self, setup_server, qualcoder_db_path):
        block = _preview(server.delete_category, 1)["preview"]["collateral"]
        assert block["category_row_owner"] == "TestCoder"
        assert block["by_owner"] == []
        assert block["ai_owned_codings"] == 0
        assert block["other_owned_codings"] == 0

    def test_merge_codes_says_whose_codings_it_discards(self, setup_server,
                                                        qualcoder_db_path):
        """The discarded duplicate is the one irreversible part of a
        merge: its memo and important flag are lost."""
        _add_coding(qualcoder_db_path, 80, 2, 1, 24, 55, "TestCoder",
                    memo="the source memo that dies")
        _reopen(qualcoder_db_path)
        preview = _preview(server.merge_codes, 2, 1)["preview"]
        assert preview["text_codings_discarded_as_duplicates"] == 1
        assert preview["collateral"]["discarded_by_owner"] == [
            {"owner": "TestCoder", "codings": 1}]


class TestWarnings:

    def test_other_work_warning_wording(self, setup_server,
                                        qualcoder_db_path):
        out = _preview(server.delete_code, 1)
        warning = next(w for w in out["warnings"] if "other coders' work" in w)
        assert warning == (
            f"Warning: 1 of the 1 affected coding(s) were not made under "
            f"this server's AI coder name(s) ({DEFAULT_AI_CODER_NAME}); they "
            f"are other coders' work. Show the user the by_owner breakdown "
            f"and get an explicit go-ahead before executing.")

    def test_no_warning_when_every_row_is_ours(self, setup_server,
                                               qualcoder_db_path):
        """P6: the warning appears only when its count is positive."""
        H.execute(qualcoder_db_path,
                  "UPDATE code_text SET owner = ? WHERE cid = 1",
                  (DEFAULT_AI_CODER_NAME,))
        _reopen(qualcoder_db_path)
        out = _preview(server.delete_code, 1)
        assert not [w for w in out.get("warnings", [])
                    if "other coders' work" in w]

    def test_the_split_follows_the_projects_names_not_a_fixed_string(
            self, setup_server, qualcoder_db_path):
        """P7: upstream splits on `owner != 'AI Agent'` (:3234-3236); ours
        splits on the project's own AI coder names."""
        _add_coding(qualcoder_db_path, 90, 1, 1, 5, 9, "Qwen 3.8 6bit")
        _reopen(qualcoder_db_path)
        before = _preview(server.delete_code, 1)["preview"]["collateral"]
        assert before["ai_owned_codings"] == 0
        server.set_project_ai_coder_name("Qwen 3.8 6bit")
        after = _preview(server.delete_code, 1)["preview"]["collateral"]
        assert after["ai_owned_codings"] == 1

    def test_private_note_warning(self, setup_server, qualcoder_db_path):
        H.execute(qualcoder_db_path,
                  "UPDATE code_text SET memo = ? WHERE cid = 1",
                  ("public ##### private",))
        _reopen(qualcoder_db_path)
        out = _preview(server.delete_code, 1)
        assert any("carry a private note the assistant cannot see" in w
                   for w in out["warnings"])
        assert "private" not in json.dumps(out["preview"]).replace(
            "private_notes_affected", "")

    def test_merge_discard_warning(self, setup_server, qualcoder_db_path):
        _add_coding(qualcoder_db_path, 81, 2, 1, 24, 55, "TestCoder")
        _reopen(qualcoder_db_path)
        out = _preview(server.merge_codes, 2, 1)
        assert any("discarded as duplicates" in w for w in out["warnings"])


class TestHiddenCoders:

    @pytest.fixture
    def hidden(self, setup_server, qualcoder_db_path):
        from test_qc40_visibility import _apply_visibility_schema, HIDDEN
        _apply_visibility_schema(qualcoder_db_path)
        _reopen(qualcoder_db_path)
        return qualcoder_db_path, HIDDEN

    def test_a_hidden_coder_is_a_count_never_a_name(self, hidden):
        """H1 of D3 6.6: the hidden name appears nowhere in the serialised
        preview, not in by_owner, not in a warning, not in a note."""
        project, name = hidden
        raw = server.delete_code(1)
        assert name not in raw
        out = json.loads(raw)
        block = out["preview"]["collateral"]
        assert block["hidden_coder_codings"] >= 1
        assert block["hidden_coder_codings"] == \
            out["preview"]["hidden_coder_codings_affected"]
        assert name not in [e["owner"] for e in block["by_owner"]]

    def test_hidden_rows_land_in_other_owned(self, hidden):
        project, name = hidden
        block = json.loads(server.delete_code(1))["preview"]["collateral"]
        assert block["other_owned_codings"] >= block["hidden_coder_codings"]

    def test_execute_requires_the_override(self, hidden):
        project, name = hidden
        out = _preview(server.delete_code, 1)
        assert out["preview"]["hidden_coder_codings_affected"] >= 1
        assert any("allow_hidden_coder=true" in w for w in out["warnings"])
        refused = _preview(server.delete_code, 1,
                           preview_token=out["preview_token"])
        assert refused["reason"] == "hidden_coder_override_required"
        assert refused["nothing_changed"] is True
        assert _backups(project) == []
        assert name not in json.dumps(refused)

    def test_the_override_lets_it_through(self, hidden):
        project, name = hidden
        out = _preview(server.delete_code, 1)
        done = _preview(server.delete_code, 1,
                        preview_token=out["preview_token"],
                        allow_hidden_coder=True)
        assert done["success"] is True

    def test_the_execute_recipe_includes_the_override(self, hidden):
        out = _preview(server.delete_code, 1)
        assert out["execute_with"]["arguments"].get("allow_hidden_coder") \
            is True

    def test_a_hidden_owner_of_the_code_row_is_masked(self, hidden):
        """Ruling Q6: a disclosure rule for this new block, not a gate.
        QualCoder shows such owners in its own tree, and list_codes is
        unchanged."""
        project, name = hidden
        H.execute(project, "UPDATE code_name SET owner = ? WHERE cid = 2",
                  (name,))
        _reopen(project)
        block = _preview(server.delete_code, 2)["preview"]["collateral"]
        assert block["code_row_owner"] == "(hidden coder)"
        assert name not in json.dumps(block)

    def test_the_category_tools_need_no_override(self, hidden):
        """R5: they touch no coding rows, so the row-level rule does not
        apply to them."""
        project, name = hidden
        out = _preview(server.delete_category, 1)
        assert out["preview"]["hidden_coder_codings_affected"] == 0
        done = _preview(server.delete_category, 1,
                        preview_token=out["preview_token"])
        assert done["success"] is True

    def test_without_the_capability_no_hidden_key(self, setup_server,
                                                  qualcoder_db_path):
        block = _preview(server.delete_code, 1)["preview"]["collateral"]
        assert "hidden_coder_codings" not in block

    def test_a_broken_view_fails_closed(self, hidden):
        """A preview must never report FEWER hidden rows than the cascade
        would remove, so a view that is present but unqueryable is an
        error for the call. The connection has already probed the
        capability as present; the table behind the view then goes away,
        which is the schema-drift case the guard exists for."""
        project, name = hidden
        assert server.db.capabilities.has_coder_visibility is True
        H.execute(project, "DROP TABLE coder_names")   # no reopen: probed
        out = _preview(server.delete_code, 1)
        assert "error" in out, out
        assert "nothing was changed" in out["error"].lower() or \
            "hidden" in out["error"].lower()


class TestTheExecutePath:

    def test_the_collateral_rides_along_into_the_result(self, setup_server,
                                                        qualcoder_db_path):
        out = _preview(server.delete_code, 1)
        done = _preview(server.delete_code, 1,
                        preview_token=out["preview_token"])
        assert "collateral" in done
        assert done["preview_verified"] is True

    def test_exactly_one_backup_and_a_read_only_connection_afterwards(
            self, setup_server, qualcoder_db_path):
        """R7 of D3 6.4."""
        out = _preview(server.delete_code, 1)
        _preview(server.delete_code, 1, preview_token=out["preview_token"])
        assert len(_backups(qualcoder_db_path)) == 1
        assert server.db.read_only is True

    def test_a_row_added_INSIDE_the_write_window_stops_it(
            self, setup_server, qualcoder_db_path, monkeypatch):
        """The in-transaction re-check, which is the only guard for the
        window between verifying the token and mutating (H2).

        Another writer commits after the verification and before the
        delete. BEGIN IMMEDIATE takes the RESERVED lock first, so this
        can only happen strictly before it; the re-read then sees the new
        row, and the operation refuses with the backup already taken and
        says so.
        """
        out = _preview(server.delete_code, 1)
        original = QualcoderDatabase.begin_immediate
        fired = []

        def racing_begin(self):
            if not fired:
                fired.append(True)
                _add_coding(qualcoder_db_path, 96, 1, 1, 61, 63, "Racer")
            return original(self)

        monkeypatch.setattr(QualcoderDatabase, "begin_immediate",
                            racing_begin)
        refused = _preview(server.delete_code, 1,
                           preview_token=out["preview_token"])
        assert fired, "the race never happened; the test proves nothing"
        assert refused["error"].startswith("The project changed since this "
                                           "preview was made")
        assert "A backup had already been taken" in refused["error"]
        # B2.5: every token refusal is machine-readable, this one
        # included. It used to reach the model as prose alone, because
        # _perform_write maps a bare ValueError to {"error": ...} (QA
        # round 1, F11).
        assert refused["reason"] == "project_changed"
        assert refused["nothing_changed"] is True
        assert H.query(qualcoder_db_path,
                       "SELECT COUNT(*) AS n FROM code_name WHERE cid=1"
                       )[0]["n"] == 1
        assert len(_backups(qualcoder_db_path)) == 1
        # v0.13 A5 (fix round 1, F5): the refusal names that backup. The
        # arm shipped without this, and reverting it turned nothing red
        # at full-suite scope (QA Q-2).
        assert Path(refused["backup_path"]).is_dir()
        assert Path(refused["backup_path"]).name \
            == _backups(qualcoder_db_path)[0]
        assert server.db.read_only is True

    def test_a_row_added_between_preview_and_execute_stops_it(
            self, setup_server, qualcoder_db_path):
        out = _preview(server.delete_code, 1)
        _add_coding(qualcoder_db_path, 95, 1, 1, 60, 62, "Someone New")
        _reopen(qualcoder_db_path)
        refused = _preview(server.delete_code, 1,
                           preview_token=out["preview_token"])
        assert refused["reason"] == "project_changed"
        assert H.query(qualcoder_db_path,
                       "SELECT COUNT(*) AS n FROM code_name WHERE cid=1"
                       )[0]["n"] == 1
