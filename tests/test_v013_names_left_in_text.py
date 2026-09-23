"""v0.13 Brief 1, items 3 and 4: the names left in the file text.

The residue block a researcher is read before approving a run did not
count the file text at all, and a file the rewrite never fires on was
absent from the whole preview (the counts study's `quiet.txt`). It now
carries `residue["file_text"]`: every source row with stored text, the
one this run rewrites, the files it does not, and the PDF sources it
refuses, read as the text will be after the run, each count two
readings (`{"wide", "whole_word"}` under `occurrences`), the wide one
split by kind.

The engine is pinned in `test_v012_pseudonymise_engine.py`
(`TestNamesLeftInText`, the properties and the performance guard); this
file drives the block through the tool, on the flagship's own fixture.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server
from qualcoder_mcp import pseudonymise as P
from qualcoder_mcp.database import QualcoderDatabase
import test_v012_pseudonymise_tool as flagship
from test_v012_pseudonymise_tool import (  # noqa: F401  (`project` is a fixture)
    MAPPING, _house_rules, backups, build_project, call, execute_from,
    preview_of, project, query, without_fixed_prose, wired,
    write_fixture_sidecar)

ONE_SHAPE_KEYS = ("wide", "whole_word")


def _reconnect(folder):
    server.db.close()
    server.db = QualcoderDatabase(str(folder))


def _set_text(folder, fid, text, name=None, mediapath=None):
    """Give source `fid` this text (inserting the row if it is new)."""
    con = sqlite3.connect(str(folder / "data.qda"))
    exists = con.execute("SELECT 1 FROM source WHERE id=?",
                         (fid,)).fetchone()
    if exists:
        con.execute("UPDATE source SET fulltext=? WHERE id=?", (text, fid))
        if name is not None:
            con.execute("UPDATE source SET name=? WHERE id=?", (name, fid))
    else:
        con.execute("INSERT INTO source (id,name,fulltext,mediapath,memo,"
                    "owner,date) VALUES (?,?,?,?,'','TestCoder','d')",
                    (fid, name or f"file_{fid}.txt", text, mediapath))
    con.commit()
    con.close()
    _reconnect(folder)


def _block(out):
    return out["preview"]["residue"]["file_text"]


def _rows(out):
    return {row["file_id"]: row for row in _block(out)["files"]}


def _sidecar(folder, entries):
    (folder / "pseudonyms.json").write_text(json.dumps(entries),
                                            encoding="utf-8")
    _reconnect(folder)


def _file_text_warning(out):
    found = [w for w in out.get("warnings", [])
             if "occurrence(s) of these names would still be in the text"
             in w or "would still show one of these names in their text"
             in w or "own whole-word rule would still match" in w
             or "were not checked; preview them one at a time" in w]
    assert len(found) <= 1, found
    return found[0] if found else None


# =============================================================================
# THE REGRESSION THE ITEM EXISTS TO FIX
# =============================================================================

class TestAFileTheRewriteNeverFiresOnIsNamed:
    """The counts study, 6.1, driven at the tool: file 99, `quiet.txt`,
    holds `THOMAS_P01 and Thomasin and THOMAS only.`; the mapping fires
    on file 1 and nowhere in file 99. Before v0.13 file 99 was in
    neither `files` nor `skipped_files` nor the residue: a researcher
    could not learn from the preview that it exists."""

    @pytest.fixture
    def two_files(self, tmp_path):
        folder = build_project(tmp_path / "two.qda", "Thomas spoke first.")
        write_fixture_sidecar(str(folder))
        # The study's two-file project: the fixture's own `quiet.txt`
        # (file 4) is replaced by file 99 of the same name.
        con = sqlite3.connect(str(folder / "data.qda"))
        con.execute("DELETE FROM source WHERE id=4")
        con.commit()
        con.close()
        with wired(folder):
            _set_text(folder, 99, "THOMAS_P01 and Thomasin and THOMAS only.",
                      name="quiet.txt")
            yield folder

    MAPPING = [{"original": "Thomas", "pseudonym": "Alex"}]

    def test_the_quiet_file_is_named_with_its_counts(self, two_files):
        out = preview_of(residue_detail="project", mapping=self.MAPPING,
                         file_id=1)
        assert [item["file_id"] for item in out["preview"]["files"]] == [1]
        row = _rows(out)[99]
        assert row["name"] == "quiet.txt"
        assert row["rewritten_by_this_run"] is False
        assert row["file_not_rewritten_because"] == "another_file"
        assert row["counted"] is True
        assert row["occurrences"]["wide"] == 3
        assert row["occurrences"]["whole_word"] == 0
        entry = row["entries"][0]
        assert entry["inside_a_longer_word"] == 2
        assert entry["case_only"] == 1
        # The rewritten file left nothing, so it is omitted, and the
        # totals say how many files show a name so the omission cannot
        # be read as absence.
        assert 1 not in _rows(out)
        assert _block(out)["totals"]["files_showing_a_name"] == 1

    def test_previewing_the_quiet_file_itself_says_no_match(self, two_files):
        out = preview_of(residue_detail="project", mapping=self.MAPPING,
                         file_id=99)
        assert out["preview"]["files"] == []
        rows = _block(out)["files"]
        # The file this call names comes first, whatever its id.
        assert [row["file_id"] for row in rows] == [99, 1]
        assert rows[0]["file_not_rewritten_because"] == "no_match"
        assert rows[1]["file_not_rewritten_because"] == "another_file"
        assert rows[1]["occurrences"]["whole_word"] == 1


# =============================================================================
# ONE SHAPE, AND THE KINDS
# =============================================================================

class TestTheBlockHasOneShape:

    @pytest.fixture
    def residue(self, project):
        _set_text(project, 4, "Thomas_P01 met thomas and MaryAnn. Tom!")
        return preview_of(residue_detail="project")["preview"]["residue"]

    def test_the_units_are_named_by_value(self, residue):
        assert residue["counts"] == "fields, not occurrences"
        assert residue["file_text"]["counts"] == \
            "occurrences in the stored text, not fields"

    def test_every_count_is_the_one_object_and_nothing_is_flat(self,
                                                               residue):
        """Night ruling 1: every count in the block is `{"wide",
        "whole_word"}` under `occurrences`, asserted by name; no flat
        `wide` or `whole_word` sits on a row or in the totals."""
        block = residue["file_text"]
        places = [block["totals"]]
        for row in block["files"]:
            places.append(row)
            places.extend(row["entries"])
        assert len(places) >= 3
        for place in places:
            for key in ONE_SHAPE_KEYS:
                assert key in place["occurrences"], place
                assert key not in place, place

    def test_the_kinds_add_up_on_every_row_and_in_the_totals(self, residue):
        block = residue["file_text"]
        total = 0
        for row in block["files"]:
            row_total = row["unattributed"]
            for entry in row["entries"]:
                kinds = sum(entry[kind] for kind in P.TEXT_RESIDUE_REASONS)
                assert kinds == entry["occurrences"]["wide"], entry
                row_total += kinds
            assert row_total == row["occurrences"]["wide"], row
            total += row_total
        by_reason = block["totals"]["by_reason"]
        assert sum(by_reason.values()) == \
            block["totals"]["occurrences"]["wide"] == total
        assert set(by_reason) == set(P.TEXT_RESIDUE_REASONS) | {
            "unattributed"}

    def test_unattributed_is_present_even_at_zero(self, residue):
        block = residue["file_text"]
        assert block["totals"]["by_reason"]["unattributed"] == 0
        for row in block["files"]:
            assert row["unattributed"] == 0

    def test_the_rows_carry_what_the_brief_draws(self, residue):
        row = {r["file_id"]: r for r in residue["file_text"]["files"]}[4]
        assert list(row)[:4] == ["file_id", "name", "rewritten_by_this_run",
                                 "file_not_rewritten_because"]
        entry = row["entries"][0]
        assert entry["entry"] == 0 and entry["form"] == "Thomas"
        assert entry["longer_words"] == [{"word": "Thomas_P01", "count": 1}]
        scanned = residue["file_text"]["scanned"]
        # Files 1, 2 (the PDF) and 4 have text; file 3 is audio.
        assert scanned["files"] == 3
        assert scanned["not_rewritten_by_this_run"] == 2
        assert scanned["characters"] > 0


class TestTheCountsStudyFixtureThroughThePreview:

    BEFORE = ("Thomas said the file was ready. See Thomas_Smith.txt and "
              "Thomas_P01. Thomasin arrived later. THOMAS shouted, and "
              "thomas whispered. Tho­mas signed the form. "
              "Ｔｈｏｍａｓ in fullwidth. Mary Ann "
              "and MaryAnn and Mary_Ann.")
    MAPPING = [{"original": "Thomas", "pseudonym": "Alex"},
               {"original": "Mary Ann", "pseudonym": "Robin Lee"}]

    @pytest.mark.parametrize("mode,wide,case_only", [
        ("exact", 7, 2), ("insensitive", 5, 0),
        ("insensitive_preserve", 5, 0)])
    def test_class_by_class(self, tmp_path, mode, wide, case_only):
        folder = build_project(tmp_path / "study.qda", self.BEFORE)
        write_fixture_sidecar(str(folder))
        with wired(folder):
            out = preview_of(mapping=self.MAPPING, case_mode=mode)
            row = _rows(out)[1]
            assert row["rewritten_by_this_run"] is True
            thomas, mary = row["entries"]
            assert thomas["occurrences"]["wide"] == wide
            assert thomas["occurrences"]["whole_word"] == 0
            assert thomas["inside_a_longer_word"] == 3
            assert thomas["case_only"] == case_only
            assert thomas["normalisation_variants"] == 2
            assert mary["occurrences"]["wide"] == 2
            assert mary["joined_differently"] == 2
            # The two spelling diagnostics sit together in the row; the
            # per-file copy in `preview.files` is unchanged in key, shape
            # and value, and under `exact` the two agree.
            assert row["normalisation_variants_seen"] == [
                {"entry": 0, "form": "Thomas", "count": 2}]
            per_file = out["preview"]["files"][0]["case_variants_seen"]
            if mode == "exact":
                assert per_file == [{"entry": 0, "form": "Thomas",
                                     "other_case_count": 2}]
                assert row["case_variants_seen"] == per_file
            else:
                assert per_file == []
                assert row["case_variants_seen"] == []


# =============================================================================
# FILES THIS RUN DOES NOT REWRITE
# =============================================================================

class TestAFileThisRunDoesNotRewrite:

    def test_a_pdf_source_is_counted_and_never_rewritten(self, project):
        text = "Thomas wrote to Thomas and Tom."
        _set_text(project, 2, text)
        out = preview_of(residue_detail="project")
        row = _rows(out)[2]
        assert row["name"] == "paper.pdf"
        assert row["rewritten_by_this_run"] is False
        assert row["file_not_rewritten_because"] == "pdf_source"
        # The whole-word count is what the rewrite would have replaced
        # had the file been eligible.
        compiled = P.Compiled(P.validate_mapping(MAPPING))
        assert row["occurrences"]["whole_word"] == len(
            P.find_replacements(compiled, text)) == 3
        assert row["occurrences"]["wide"] == 3
        for entry in row["entries"]:
            assert entry["whole_word_in_a_file_not_rewritten"] == \
                entry["occurrences"]["whole_word"]
            assert entry["put_back_by_a_pseudonym"] == 0

    def test_nothing_is_put_back_in_a_file_this_run_did_not_rewrite(
            self, project):
        """Night ruling 2, driven with the item 5 mapping so the file
        this run rewrites shows the put-back kind in the same preview.
        On every other row it is 0 by construction, the whole words
        stand as they are, and they are their own kind."""
        mapping = [{"original": "Smith", "pseudonym": "Jones"},
                   {"original": "Thomas Smith", "pseudonym": "Alex Smith"}]
        _set_text(project, 1, "Thomas Smith met Smith.")
        _set_text(project, 2, "Thomas Smith wrote.")            # the PDF
        _set_text(project, 4, "Smith was there.")               # another file
        out = preview_of(residue_detail="project", mapping=mapping)
        rows = _rows(out)
        rewritten = rows[1]
        assert rewritten["rewritten_by_this_run"] is True
        smith = {e["entry"]: e for e in rewritten["entries"]}[0]
        assert smith["occurrences"]["wide"] == 1
        assert smith["occurrences"]["whole_word"] == 1
        assert smith["put_back_by_a_pseudonym"] == 1
        assert smith["whole_word_in_a_file_not_rewritten"] == 0
        for fid, entry_index in ((2, 1), (4, 0)):
            row = rows[fid]
            assert row["rewritten_by_this_run"] is False
            for entry in row["entries"]:
                assert entry["put_back_by_a_pseudonym"] == 0
            entry = {e["entry"]: e for e in row["entries"]}[entry_index]
            assert entry["occurrences"]["whole_word"] == 1
            assert entry["whole_word_in_a_file_not_rewritten"] == 1
        totals = _block(out)["totals"]
        assert totals["by_reason"]["put_back_by_a_pseudonym"] == 1
        assert totals["by_reason"]["whole_word_in_a_file_not_rewritten"] == 2
        # The total whole-word count is the rows' own, never the sum of
        # the kinds.
        assert totals["occurrences"]["whole_word"] == 3

    def test_the_files_this_call_does_not_touch_are_named(self, project):
        """`test_the_readable_preview_still_names_the_skipped_files`,
        restated: a file this call does not touch is named by the
        file-text block when a name shows in it, a PDF among them."""
        _set_text(project, 2, "Thomas on page one.")
        _set_text(project, 4, "and THOMAS on the quiet one")
        rows = _rows(preview_of(residue_detail="project"))
        assert rows[2]["name"] == "paper.pdf"
        assert rows[4]["name"] == "quiet.txt"


# =============================================================================
# THE TWO MAPPING PATHS
# =============================================================================

class TestTheTwoPaths:

    TEXT = ("Thomas_P01 and Thomasin and Thomas_Smith.txt, THOMAS too, "
            "and Tho\u00admas.")

    def test_the_typed_path_lists_the_longer_words(self, project):
        _set_text(project, 4, self.TEXT)
        entry = _rows(preview_of(residue_detail="project"))[4][
            "entries"][0]
        assert entry["form"] == "Thomas"
        assert entry["longer_words"] == [
            {"word": "Thomas_P01", "count": 1},
            {"word": "Thomas_Smith", "count": 1},
            {"word": "Thomasin", "count": 1}]
        assert "longer_words_truncated" not in entry

    def test_the_longer_words_are_capped_and_say_so(self, project):
        words = " ".join(f"Thomas_{n:02d}" for n in
                         range(P.MAX_LONGER_WORDS_PER_ENTRY + 3))
        _set_text(project, 4, words)
        entry = _rows(preview_of(residue_detail="project"))[4][
            "entries"][0]
        assert len(entry["longer_words"]) == P.MAX_LONGER_WORDS_PER_ENTRY
        assert entry["longer_words_truncated"] is True
        assert entry["inside_a_longer_word"] == \
            P.MAX_LONGER_WORDS_PER_ENTRY + 3

    def test_the_sidecar_path_withholds_every_form_and_every_word(
            self, project):
        _set_text(project, 4, self.TEXT, name="Thomas_notes.txt")
        _sidecar(project, [{"original": "Thomas", "pseudonym": "Alex"},
                           {"original": "Mary Ann", "pseudonym": "Sam"}])
        out = preview_of(residue_detail="project", mapping=None,
                         use_project_pseudonyms=True)
        row = _rows(out)[4]
        # Ruling 12: the file is named on this path too.
        assert row["name"] == "Thomas_notes.txt"
        entry = row["entries"][0]
        assert entry["entry"] == 0
        assert entry["inside_a_longer_word"] == 3
        assert "form" not in entry
        assert "longer_words" not in entry
        assert "longer_words_truncated" not in entry
        for key in ("case_variants_seen", "normalisation_variants_seen"):
            for item in row[key]:
                assert "form" not in item
        assert row["case_variants_seen"] == [{"entry": 0,
                                              "other_case_count": 1}]
        # Fix round 1, QA-5: a spelling with a soft hyphen inside the name
        # is in the text, so this list is not empty and its withholding
        # is really exercised.
        assert row["normalisation_variants_seen"] == [{"entry": 0,
                                                       "count": 1}]
        # No word of the file text, and no name, anywhere in the block
        # but the file's own name and the server's fixed prose.
        residue = without_fixed_prose(out["preview"]["residue"])
        for row_out in residue["file_text"]["files"]:
            row_out.pop("name")
        dumped = json.dumps(residue)
        for word in ("Thomas", "THOMAS", "Thomasin", "P01", "Smith"):
            assert word not in dumped, word

    def test_the_typed_path_still_shows_the_caller_their_own_forms(
            self, project):
        _set_text(project, 4, self.TEXT)
        row = _rows(preview_of(residue_detail="project"))[4]
        assert row["case_variants_seen"] == [
            {"entry": 0, "form": "Thomas", "other_case_count": 1}]


# =============================================================================
# THE TOGGLE, THE TOKEN AND THE LOCK
# =============================================================================

class TestTheBlockIsPresentationOnly:

    def test_scan_residue_false_removes_the_whole_block(self, project):
        _set_text(project, 4, "THOMAS was here.")
        out = preview_of(scan_residue=False)
        assert "residue" not in out["preview"]
        assert _file_text_warning(out) is None

    def test_the_block_changes_nothing_the_token_binds(self, project):
        """A preview with the block and one without issue one bind, and
        the token of either executes: nothing in the block is signed."""
        _set_text(project, 4, "THOMAS was here.")
        with_block = preview_of()
        without = preview_of(scan_residue=False)
        assert "file_text" in with_block["preview"]["residue"]
        assert with_block["preview_token"].split(".")[2] == \
            without["preview_token"].split(".")[2]
        result = execute_from(without)
        assert result.get("success") is True, result

    def test_a_change_to_a_file_this_call_does_not_touch_keeps_the_token(
            self, project):
        """The block reads every file, and none of that reading is in
        the signed state: another file gaining a name between the
        preview and the execute changes the report, not the run."""
        out = preview_of()
        _set_text(project, 4, "Now THOMAS is here too.")
        assert _rows(preview_of())[4]["occurrences"]["wide"] == 1
        result = execute_from(out)
        assert result.get("success") is True, result


# =============================================================================
# THE BUDGET AND THE CAP
# =============================================================================

class TestTheWorkBudget:

    def _several(self, project):
        _set_text(project, 4, "THOMAS in four.")
        _set_text(project, 5, "nothing here at all.", name="five.txt")
        _set_text(project, 6, "Thomasin in six.", name="six.txt")
        _set_text(project, 7, "and THOMAS_P01 in seven.", name="seven.txt")

    def test_the_boolean_tier_past_the_budget(self, project, monkeypatch):
        self._several(project)
        compiled = P.Compiled(P.validate_mapping(MAPPING))
        forms = len(compiled.forms)
        rewritten = query(project, "SELECT fulltext FROM source WHERE id=1"
                          )[0]["fulltext"]
        after = P.apply_replacements(
            rewritten, P.find_replacements(compiled, rewritten))
        # Room for the file this call rewrites and nothing more, in the
        # work model of fix round 1 (forms plus a per-character term).
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK", len(after) * (
            forms + P.RESIDUE_WORK_PER_CHARACTER))
        out = preview_of(residue_detail="project")
        block = _block(out)
        assert block["files_counted"] == 1
        # In the order of the budget: the file this call names first,
        # then every other file in id order.
        assert block["files_not_counted"] == [2, 4, 5, 6, 7]
        rows = _rows(out)
        for fid, shows in ((2, False), (4, True), (5, False), (6, True),
                           (7, True)):
            row = rows[fid]
            assert row["counted"] is False
            assert row["shows_a_name"] is shows, fid
            assert "occurrences" not in row and "entries" not in row
        assert block["totals"]["files_showing_a_name"] == 3
        note = block["files_not_counted_note"]
        assert note.startswith("5 file(s) were not counted in full")
        # Two budgets since fix round 1 (S-4): the work and the matches,
        # and a smaller mapping is the remedy for the first only.
        assert ("The budgets are fixed: a smaller mapping lowers the work, "
                "and a file past the match budget is one to read directly."
                in note)
        assert "file_ids" not in note
        _house_rules([note], ["files_not_counted_note"])
        # Deterministic: the same preview twice is the same block.
        assert json.dumps(_block(preview_of(
            residue_detail="project"))) == json.dumps(block)

    def test_a_budget_cannot_make_a_quiet_preview(self, project,
                                                  monkeypatch):
        """With no budget at all nothing is counted, and the warning
        still says, out loud, that files show a name."""
        self._several(project)
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK", 0)
        out = preview_of()
        block = _block(out)
        assert block["files_counted"] == 0
        assert block["totals"]["occurrences"]["wide"] == 0
        assert block["totals"]["files_showing_a_name"] == 3
        warning = _file_text_warning(out)
        assert warning == (
            "Warning: after this run, 3 file(s) would still show one of "
            "these names in their text, and were not counted in full "
            "because counting them passed this preview's budget; see "
            "files_not_counted. See residue.file_text, which names the "
            "files.")

    def test_within_the_budget_there_is_no_note(self, project):
        self._several(project)
        block = _block(preview_of())
        assert block["files_not_counted"] == []
        assert "files_not_counted_note" not in block


class TestTheRowCap:

    def test_the_totals_stay_complete_and_the_rest_are_listed_by_id(
            self, project, monkeypatch):
        for fid in range(5, 10):
            _set_text(project, fid, f"THOMAS in {fid}.", name=f"f{fid}.txt")
        full = _block(preview_of())
        # By default the other files have compact rows, under a cap of
        # their own (fix round 2, the lead's ruling on CORR-4); the cap
        # on full rows does not touch them.
        monkeypatch.setattr(P, "MAX_RESIDUE_FILE_ROWS", 1)
        monkeypatch.setattr(P, "MAX_RESIDUE_COMPACT_ROWS", 2)
        capped = _block(preview_of())
        assert [row["file_id"] for row in capped["files"]] == [5, 6]
        assert capped["files_truncated"] is True
        assert capped["more_files_showing_a_name"] == [7, 8, 9]
        assert capped["totals"] == full["totals"]
        assert full["files_truncated"] is False
        assert full["more_files_showing_a_name"] == []

    def test_project_detail_gives_full_rows_then_compact_rows_then_ids(
            self, project, monkeypatch):
        """In project detail the first files get full rows up to their
        cap, the next compact rows up to theirs, the rest their ids."""
        for fid in range(5, 10):
            _set_text(project, fid, f"THOMAS in {fid}.", name=f"f{fid}.txt")
        monkeypatch.setattr(P, "MAX_RESIDUE_FILE_ROWS", 2)
        monkeypatch.setattr(P, "MAX_RESIDUE_COMPACT_ROWS", 2)
        block = _block(preview_of(residue_detail="project"))
        rows = block["files"]
        assert [row["file_id"] for row in rows] == [5, 6, 7, 8]
        assert "entries" in rows[0] and "entries" in rows[1]
        assert set(rows[2]) == set(rows[3]) == {"file_id", "name",
                                                "occurrences"}
        assert block["more_files_showing_a_name"] == [9]
        assert block["files_truncated"] is True
        assert block["totals"]["files_showing_a_name"] == 5


# =============================================================================
# UNATTRIBUTED
# =============================================================================

class TestACountIsNeverLost:

    def test_an_occurrence_no_entry_can_be_charged_to_is_still_counted(
            self, project, monkeypatch):
        """Forced: the lookup is emptied, so no match can be placed. Every
        occurrence is still in the file's wide count and in
        `unattributed`, and the totals still add up."""
        _set_text(project, 4, "THOMAS_P01 and thomas and Mary_Ann.")
        original = P.Compiled.text_lookup

        def emptied(self):
            lookup = original(self)
            lookup.direct = {}
            lookup.folded = {}
            return lookup

        monkeypatch.setattr(P.Compiled, "text_lookup", emptied)
        out = preview_of(residue_detail="project")
        row = _rows(out)[4]
        assert row["unattributed"] == 3
        assert row["occurrences"]["wide"] == 3
        assert row["entries"] == []
        by_reason = _block(out)["totals"]["by_reason"]
        assert by_reason["unattributed"] == 3
        assert sum(by_reason.values()) == \
            _block(out)["totals"]["occurrences"]["wide"]
        assert "3 of those could not be charged to an entry." in \
            _file_text_warning(out)


# =============================================================================
# THE WRITE PATH
# =============================================================================

class TestNothingOfThisRunsUnderTheLock:

    def test_the_block_is_built_from_the_read_phase_plan_only(
            self, project, monkeypatch):
        calls = []
        original = P.names_left_in_text

        def counting(compiled, text, list_longer_words,
                     rewritten_by_this_run, **kwargs):
            calls.append(bool(server.db.conn.in_transaction)
                         if server.db is not None else None)
            return original(compiled, text, list_longer_words,
                            rewritten_by_this_run, **kwargs)

        out = preview_of()
        monkeypatch.setattr(P, "names_left_in_text", counting)
        assert execute_from(out)["success"] is True
        # Three files with text, each read once, by the read phase.
        assert len(calls) == 3
        assert not any(calls)


# =============================================================================
# THE WORDS THE RESEARCHER IS READ
# =============================================================================

class TestTheWarnings:

    def test_the_file_text_warning_with_every_clause(self, project):
        _set_text(project, 1, "Thomas Smith met Smith.")
        _set_text(project, 2, "Thomas Smith wrote.")
        _set_text(project, 4, "Smithson, SMITH and Thomas_Smith and "
                              "Tho­mas Smith and ThomasSmith.")
        out = preview_of(mapping=[
            {"original": "Smith", "pseudonym": "Jones"},
            {"original": "Thomas Smith", "pseudonym": "Alex Smith"}])
        warning = _file_text_warning(out)
        by_reason = _block(out)["totals"]["by_reason"]
        wide = _block(out)["totals"]["occurrences"]["wide"]
        assert warning.startswith(
            f"Warning: after this run, {wide} occurrence(s) of these names "
            f"would still be in the text of 3 file(s), because the rewrite "
            f"replaces whole words only and in one file per call. ")
        for kind, words in (
                ("inside_a_longer_word",
                 "are a name inside a longer word (usually a different "
                 "word, left as it is)"),
                ("case_only", "differ only in letter case"),
                ("joined_differently",
                 "are a name of several words with its parts joined "
                 "differently"),
                ("normalisation_variants",
                 "are spelled with an invisible character or a different "
                 "Unicode normalisation"),
                ("put_back_by_a_pseudonym",
                 "are a name that one of your pseudonyms puts back"),
                ("whole_word_in_a_file_not_rewritten",
                 "are whole words in files this run did not rewrite (a PDF "
                 "source, or a file to be run with its own mapping)")):
            assert by_reason[kind] > 0, kind
            assert f"{by_reason[kind]}" in warning
            assert words in warning, kind
        assert "The split by kind is a heuristic; the total is not." in \
            warning
        assert warning.endswith(
            "See residue.file_text, which names the files.")
        _house_rules([warning], ["file-text warning"])

    def test_a_clause_at_zero_is_dropped(self, project):
        _set_text(project, 4, "Thomas_P01 only.")
        warning = _file_text_warning(preview_of())
        assert warning == (
            "Warning: after this run, 1 occurrence(s) of these names would "
            "still be in the text of 1 file(s), because the rewrite replaces "
            "whole words only and in one file per call. 1 of those are a "
            "name inside a longer word (usually a different word, left as it "
            "is). The split by kind is a heuristic; the total is not. See "
            "residue.file_text, which names the files.")

    def test_two_clauses_are_joined_with_and(self, project):
        _set_text(project, 4, "Thomas_P01 and THOMAS.")
        warning = _file_text_warning(preview_of())
        assert ("1 of those are a name inside a longer word (usually a "
                "different word, left as it is) and 1 differ only in letter "
                "case.") in warning

    def test_no_name_left_no_warning(self, project):
        out = preview_of()
        assert _block(out)["totals"]["occurrences"]["wide"] == 0
        assert _file_text_warning(out) is None

    def test_fields_and_occurrences_are_never_added_together(self, project):
        con = sqlite3.connect(str(project / "data.qda"))
        con.execute("UPDATE source SET memo='about Thomas' WHERE id=1")
        con.commit()
        con.close()
        _set_text(project, 4, "Thomas_P01 and THOMAS.")
        warnings = preview_of()["warnings"]
        fields = [w for w in warnings if "note(s), label(s)" in w]
        text = [w for w in warnings if "occurrence(s) of these names" in w]
        assert len(fields) == 1 and len(text) == 1
        assert fields[0].startswith("Warning: 1 note(s)")
        assert text[0].startswith("Warning: after this run, 2 occurrence(s)")

    def test_the_short_form_warning_names_the_file_text_too(self, project):
        warning = [w for w in preview_of()["warnings"]
                   if "surface form of fewer" in w][0]
        assert warning.endswith(
            "A short form makes the file-text counts generous too; the "
            "whole-word number beside each wide one shows how far.")

    def test_every_new_string_keeps_the_house_rules(self, project):
        _set_text(project, 4, "Thomas_P01 and THOMAS.")
        out = preview_of()
        residue = out["preview"]["residue"]
        _house_rules([residue["reading_note"], residue["scope_note"],
                      residue["file_text"]["reading_note"],
                      QualcoderDatabase.PSEUDONYMISE_NOT_COUNTED_NOTE]
                     + out["warnings"])

    def test_the_file_text_note_says_its_heuristics(self, project):
        note = _block(preview_of())["reading_note"]
        assert note.startswith("These are occurrences in the file text, "
                               "not fields.")
        for fragment in (
                "The wide count is a heuristic that reads the text the way "
                "a person would",
                "the split is a heuristic and the total is not, and so is "
                "the charging of an occurrence to one entry where two "
                "entries could claim it (the occurrence is counted either "
                "way)",
                "a spelling that only case-folding reaches is charged to "
                "normalisation_variants, which is part of that heuristic",
                "whole_word_in_a_file_not_rewritten, so the kinds still add "
                "up",
                "A name inside a longer word is usually a different word "
                "(Thomasson) and is normally left as it is; a case label "
                "such as Thomas_P01 is the exception, renamed by hand.",
                "a count in another file may be a different person with the "
                "same name, to be run with its own mapping",
                "a look-alike letter from another script is not caught."):
            assert fragment in note, fragment


# =============================================================================
# WHERE THE SCOPE NOTE SENDS THE RESEARCHER
# =============================================================================

class TestTheScopeNoteIsTrueOfTheSearchTools:

    def test_search_memos_answers_for_three_of_the_twelve_notes(
            self, project):
        """The scope note says `search_memos` can answer for three of the
        twelve note fields by name. Driven rather than read: one word
        planted in all twelve, counted in all twelve, and found by
        `search_memos` in exactly the code, file and annotation notes."""
        word = "Zanzibarfield"
        con = sqlite3.connect(str(project / "data.qda"))
        for field, sql in \
                flagship.TestTheLabelsAndMemosTheResidueCounts.RESIDUE_FIELDS:
            if not field.startswith("memos."):
                continue
            for statement in ([sql] if isinstance(sql, str) else sql):
                con.execute(statement, (f"about {word}",)
                            if "?" in statement else ())
        con.commit()
        con.close()
        _reconnect(project)
        mapping = [{"original": word, "pseudonym": "Elsewhere"}]
        residue = preview_of(mapping=mapping)["preview"]["residue"]
        assert all(count["wide"] == 1 for count in residue["memos"].values())
        assert len(residue["memos"]) == 12
        found = json.loads(server.search_memos(word, limit=100))
        kinds = {item["type"] for item in found["results"]}
        assert kinds == {"code", "file", "annotation"}


# =============================================================================
# ITEM 5: A PSEUDONYM THAT CONTAINS A MAPPED NAME (ruling 8)
# =============================================================================

class TestAPseudonymThatContainsAName:
    """Warned about and counted, never refused. The four mappings the
    counts study drove (6.2), each through the preview."""

    SURNAME = [{"original": "Smith", "pseudonym": "Jones"},
               {"original": "Thomas Smith", "pseudonym": "Alex Smith"}]

    WARNING = (
        "Warning: pseudonym(s) of entry [1] contain a name from this "
        "mapping (see pseudonyms_containing_a_name), so the rewrite puts "
        "that name back wherever it writes them; residue.file_text counts "
        "those occurrences under put_back_by_a_pseudonym. A pseudonym that "
        "puts a name back inside a longer word (xThomasx) is not caught by "
        "this check and is counted under inside_a_longer_word instead. "
        "Choose a pseudonym that contains no name from the mapping, or run "
        "the contained name in a second pass and check the count.")

    @staticmethod
    def _warning(out):
        found = [w for w in out["warnings"]
                 if "pseudonym(s) of entry" in w or "put back by the "
                 "pseudonyms it writes" in w]
        assert len(found) <= 1, found
        return found[0] if found else None

    def test_the_surname_mapping_is_found_counted_and_warned(self,
                                                              tmp_path):
        """The one that is not contrived: a careful researcher could write
        it, and it leaves the real surname in the text while the preview
        of v0.12 reported the run as a success. (A project of its own,
        with no codings, so the execute is not refused for the fixture's
        codings colliding on a text they were not made on.)"""
        folder = build_project(tmp_path / "surname.qda",
                               "Thomas Smith met Smith.")
        write_fixture_sidecar(str(folder))
        with wired(folder):
            self._surname_run(folder)

    def _surname_run(self, project):
        out = preview_of(mapping=self.SURNAME)
        assert out["preview"]["pseudonyms_containing_a_name"] == [
            {"entry": 1, "pseudonym": "Alex Smith", "contains_entry": 0,
             "form": "Smith"}]
        assert _block(out)["totals"]["by_reason"][
            "put_back_by_a_pseudonym"] == 1
        assert self._warning(out) == self.WARNING
        _house_rules([self.WARNING], ["pseudonym warning"])
        # Not refused: the execute still runs, and puts the name back.
        result = execute_from(out, mapping=self.SURNAME)
        assert result.get("success") is True, result
        assert query(project, "SELECT fulltext FROM source WHERE id=1"
                     )[0]["fulltext"] == "Alex Smith met Jones."

    @pytest.mark.parametrize("mapping,expected", [
        ([{"original": "Thomas", "pseudonym": "Alex"},
          {"original": "Mary", "pseudonym": "a Thomas b"}],
         [{"entry": 1, "pseudonym": "a Thomas b", "contains_entry": 0,
           "form": "Thomas"}]),
        ([{"original": "Thomas", "pseudonym": "not Thomas"}],
         [{"entry": 0, "pseudonym": "not Thomas", "contains_entry": 0,
           "form": "Thomas"}]),
    ], ids=["another-entry", "its-own-entry"])
    def test_the_other_two_shapes_the_check_catches(self, project, mapping,
                                                    expected):
        _set_text(project, 1, "Thomas met Mary at noon.")
        out = preview_of(mapping=mapping)
        assert out["preview"]["pseudonyms_containing_a_name"] == expected
        assert _block(out)["totals"]["by_reason"][
            "put_back_by_a_pseudonym"] >= 1
        assert self._warning(out).startswith(
            f"Warning: pseudonym(s) of entry [{expected[0]['entry']}] "
            f"contain a name from this mapping")

    def test_the_shape_the_check_cannot_catch_is_counted_and_quiet(
            self, project):
        """The cross-check's correction, pinned as the limitation it is:
        a pseudonym that puts the name back with no word boundary on
        either side is invisible to a whole-word rule. The occurrence is
        COUNTED, under inside_a_longer_word; the check and its warning
        stay quiet, and the warning's own text says so."""
        _set_text(project, 1, "Thomas met Mary at noon.")
        mapping = [{"original": "Thomas", "pseudonym": "Alex"},
                   {"original": "Mary", "pseudonym": "xThomasx"}]
        out = preview_of(mapping=mapping)
        assert "pseudonyms_containing_a_name" not in out["preview"]
        entry = _rows(out)[1]["entries"][0]
        assert entry["inside_a_longer_word"] == 1
        assert entry["put_back_by_a_pseudonym"] == 0
        assert entry["longer_words"] == [{"word": "xThomasx", "count": 1}]
        assert self._warning(out) is None

    def test_a_pseudonym_that_forms_a_name_with_its_neighbours(self,
                                                               project):
        """No pseudonym contains a name on its own, and yet one forms a
        name with the words around it: "Mary" written for Thomas, before
        an "Ann" that was already there, makes "Mary Ann". The static
        check cannot see it; the count does, and a warning says so."""
        _set_text(project, 1, "Thomas Ann spoke.")
        mapping = [{"original": "Thomas", "pseudonym": "Mary"},
                   {"original": "Mary Ann", "pseudonym": "Sam"}]
        out = preview_of(mapping=mapping)
        assert "pseudonyms_containing_a_name" not in out["preview"]
        assert _block(out)["totals"]["by_reason"][
            "put_back_by_a_pseudonym"] == 1
        assert self._warning(out) == (
            "Warning: after this run, 1 occurrence(s) of a name from this "
            "mapping would be put back by the pseudonyms it writes, "
            "although no pseudonym contains a name on its own: a pseudonym "
            "can form a name with the words around it. residue.file_text "
            "counts them under put_back_by_a_pseudonym. Choose a different "
            "pseudonym, or run the name in a second pass and check the "
            "count.")
        _house_rules([self._warning(out)], ["neighbour warning"])

    def test_the_check_needs_no_text_and_no_residue_scan(self, project):
        """Static, before any text is read: it is there with the residue
        scan off, and its warning with it."""
        out = preview_of(mapping=self.SURNAME, scan_residue=False)
        assert out["preview"]["pseudonyms_containing_a_name"][0][
            "contains_entry"] == 0
        assert self._warning(out) == self.WARNING

    def test_a_clean_mapping_carries_no_key_and_no_warning(self, project):
        out = preview_of()
        assert "pseudonyms_containing_a_name" not in out["preview"]
        assert self._warning(out) is None

    def test_the_sidecar_path_quotes_neither_the_pseudonym_nor_the_name(
            self, project):
        _set_text(project, 1, "Thomas Smith met Smith.")
        _sidecar(project, self.SURNAME)
        out = preview_of(mapping=None, use_project_pseudonyms=True)
        assert out["preview"]["pseudonyms_containing_a_name"] == [
            {"entry": 1, "contains_entry": 0}]
        assert self._warning(out) == self.WARNING
        assert "Smith" not in json.dumps(
            out["preview"]["pseudonyms_containing_a_name"])
        assert "Smith" not in self._warning(out)

    def test_the_rewrite_itself_is_unchanged(self, project):
        """Presentation, not effect: the key is not signed, so the same
        mapping with and without the finding binds and signs the same as
        it did in v0.12."""
        _set_text(project, 1, "Thomas Smith met Smith.")
        out = preview_of(mapping=self.SURNAME)
        effect = server.db.pseudonymise_effect(
            server.db.pseudonymise_plan(
                P.Compiled(P.validate_mapping(self.SURNAME)),
                "snap_to_pseudonym", [1]))
        assert "pseudonyms_containing_a_name" not in json.dumps(effect)
        assert out["preview_token"]


# =============================================================================
# FIX ROUND 1, F2: THE WIDE READING SEES AT LEAST WHAT THE REWRITE MATCHES
# =============================================================================

class TestTheWideReadingIsAtLeastTheRewrite:
    """QA-1, and the lead's ruling on it. "Rene" followed by U+0301 (an
    NFD "René") is a whole word to the rewriter, which reads a combining
    mark as a boundary, and is composed away by the detector's NFKC
    reading. Before the fix a note and another file each reported
    `{"wide": 0, "whole_word": 1}` and neither warning spoke. An
    occurrence now counts in the wide reading when either matcher finds
    it, both warnings speak when either count is non-zero, and the
    withholding predicate is the same union."""

    MAPPING = [{"original": "Rene", "pseudonym": "Alex"}]
    NFD = "René"

    def _nfd_project(self, project):
        con = sqlite3.connect(str(project / "data.qda"))
        con.execute("UPDATE source SET memo=? WHERE id=1",
                    (f"{self.NFD} said so",))
        con.commit()
        con.close()
        _set_text(project, 1, "Rene met the team.")
        _set_text(project, 4, f"Later {self.NFD} arrived.")
        return preview_of(residue_detail="project", mapping=self.MAPPING)

    def test_a_note_is_counted_in_both_readings(self, project):
        out = self._nfd_project(project)
        source = out["preview"]["residue"]["memos"]["source"]
        assert source["wide"] == 1 and source["whole_word"] == 1

    def test_another_file_is_counted_in_both_readings(self, project):
        out = self._nfd_project(project)
        row = _rows(out)[4]
        assert row["occurrences"]["wide"] == 1
        assert row["occurrences"]["whole_word"] == 1
        assert row["entries"][0]["whole_word_in_a_file_not_rewritten"] == 1
        assert _block(out)["totals"]["files_showing_a_name"] == 1

    def test_both_warnings_speak(self, project):
        out = self._nfd_project(project)
        fields = [w for w in out["warnings"] if "note(s), label(s)" in w]
        assert len(fields) == 1
        assert fields[0].startswith(
            "Warning: 1 note(s), label(s) or attribute value(s) may still "
            "show one of these names, and this tool does not rewrite any of "
            "them; 1 of those match this run's own whole-word rule.")
        text = _file_text_warning(out)
        assert text is not None
        assert "1 occurrence(s) of these names would still be in the text" \
            in text

    def test_a_file_name_carrying_the_nfd_spelling_is_withheld(self):
        """The same union withholds a file name, a path and (F1) a
        pseudonym: a rule that withholds more, never less."""
        compiled = P.Compiled(P.validate_mapping(self.MAPPING))
        name = f"{self.NFD}_interview.txt"
        assert not compiled.detector.contains(name)
        assert server._pseudonymise_safe_name(name, compiled) is None
        assert server._pseudonymise_safe_name(
            "interview_07.txt", compiled) == "interview_07.txt"


# =============================================================================
# FIX ROUND 1, F1: A PSEUDONYM THAT CONTAINS A MAPPED NAME IS WITHHELD
# =============================================================================

# The Security gate's five shapes (S-2), each a mapping and a text.
CARRYING = {
    "contains_other": ([{"original": "Smith", "pseudonym": "Jones"},
                        {"original": "Thomas Smith",
                         "pseudonym": "Alex Smith"}],
                       "Thomas Smith met Smith. Later Thomas Smith left.",
                       {1}),
    "contains_own": ([{"original": "Thomas", "pseudonym": "Thomas Jr"}],
                     "Thomas said hello. Thomas left.", {0}),
    "wide_only": ([{"original": "Thomas", "pseudonym": "Thomasina"}],
                  "Thomas said hello. Thomas left.", {0}),
    "pre_existing": ([{"original": "Smith", "pseudonym": "Jones"},
                      {"original": "Thomas Smith",
                       "pseudonym": "Alex Smith"}],
                     "Thomas Smith met Alex Smith and Smith.", {1}),
    "shared": ([{"original": "Smith", "pseudonym": "Jones"},
                {"original": "Thomas Smith", "pseudonym": "Alex Smith"},
                {"original": "Tom Smith", "pseudonym": "Alex Smith"}],
               "Thomas Smith met Tom Smith and Smith.", {1, 2}),
}


def _originals(mapping):
    names = set()
    for item in mapping:
        names.add(item["original"])
        names.update(item["original"].split())
    return sorted(names)


def _outside_fixed_prose(payload, name):
    """Where `name` reaches `payload` beyond the declared routes and the
    item 5 warning's fixed example ("xThomasx")."""
    found = flagship.routes_carrying(payload, name, flagship.DECLARED_ROUTES)
    return [(path, text) for path, text in found
            if not (path.startswith(".warnings[")
                    and name.lower() not in text.replace(
                        "xThomasx", "").lower())]


class TestAPseudonymCarryingANameIsWithheld:
    """The owner's F-1 ruling and the lead's ruling on S-2. A pseudonym
    that contains a name from the mapping is withheld from the run record
    and the journal entry on both mapping paths, and from every route of
    the preview that quotes a pseudonym on the `use_project_pseudonyms`
    path; the entry index stays and one fixed sentence says why. The run
    itself goes ahead, with its warning (ruling 8)."""

    @staticmethod
    def _project(tmp_path, text, mapping, sidecar):
        folder = build_project(tmp_path / "study.qda", text)
        write_fixture_sidecar(str(folder))
        if sidecar:
            (folder / "pseudonyms.json").write_text(json.dumps(mapping),
                                                    encoding="utf-8")
        return folder

    @staticmethod
    def _records(folder, result):
        manifest = Path(result["manifest_path"]).read_text(encoding="utf-8")
        journal = query(folder, "SELECT name, jentry FROM journal")
        return manifest, journal

    @pytest.mark.parametrize("shape", sorted(CARRYING))
    def test_the_sidecar_path_withholds_it_everywhere(self, tmp_path, shape):
        mapping, text, withheld = CARRYING[shape]
        folder = self._project(tmp_path, text, mapping, sidecar=True)
        with wired(folder):
            out = call(mapping=None, use_project_pseudonyms=True)
            preview = out["preview"]
            for name in _originals(mapping):
                assert _outside_fixed_prose(out, name) == [], (shape, name)
            for block in preview["files"][0]["replacements"]:
                if block["entry"] in withheld:
                    assert block["pseudonym"] is None, block
                else:
                    assert block["pseudonym"] is not None, block
            for item in preview["files"][0][
                    "pre_existing_pseudonym_occurrences"]:
                assert (item["pseudonym"] is None) == (
                    item["entry"] in withheld), item
            for item in preview.get("shared_pseudonyms", []):
                assert item["pseudonym"] is None, item
            assert preview["pseudonyms_withheld"] == len(withheld)
            assert preview["pseudonyms_withheld_note"] == \
                P.PSEUDONYMS_WITHHELD_NOTE
            result = flagship.execute_as_recipe(out)
            assert result.get("success") is True, result
            manifest, journal = self._records(folder, result)
        record = json.loads(manifest)
        for entry in record["entries"]:
            assert (entry["pseudonym"] is None) == (
                entry["index"] in withheld), entry
        assert record["pseudonyms_withheld"] == len(withheld)
        for name in _originals(mapping):
            assert name.lower() not in manifest.lower(), (shape, name)
            for row in journal:
                assert name.lower() not in row["jentry"].lower(), name
                assert name.lower() not in row["name"].lower(), name
            assert name.lower() not in json.dumps(result).lower(), name
        body = [row["jentry"] for row in journal
                if "Pseudonyms applied" in row["jentry"]][0]
        assert P.PSEUDONYMS_WITHHELD_NOTE in body
        for index in withheld:
            assert f"entry {index}, withheld (" in body

    def test_the_typed_path_quotes_it_and_the_records_withhold_it(
            self, tmp_path):
        mapping, text, _ = CARRYING["contains_other"]
        folder = self._project(tmp_path, text, mapping, sidecar=False)
        with wired(folder):
            out = preview_of(mapping=mapping)
            # The caller typed it: the preview quotes it back.
            assert [block["pseudonym"] for block in
                    out["preview"]["files"][0]["replacements"]] == [
                "Jones", "Alex Smith"]
            assert "pseudonyms_withheld" not in out["preview"]
            result = execute_from(out, mapping=mapping)
            assert result.get("success") is True, result
            manifest, journal = self._records(folder, result)
        record = json.loads(manifest)
        assert record["entries"] == [{"index": 0, "pseudonym": "Jones"},
                                     {"index": 1, "pseudonym": None}]
        assert record["pseudonyms_withheld"] == 1
        assert record["pseudonyms_withheld_note"] == \
            P.PSEUDONYMS_WITHHELD_NOTE
        assert "smith" not in manifest.lower()
        body = journal[0]["jentry"]
        assert "Pseudonyms applied: Jones (1), entry 1, withheld (2)." in body
        assert "smith" not in body.lower()

    def test_a_clean_mapping_withholds_nothing(self, project):
        (project / "pseudonyms.json").write_text(json.dumps(
            [{"original": "Thomas", "pseudonym": "Alex"},
             {"original": "Mary Ann", "pseudonym": "Sam"}]),
            encoding="utf-8")
        out = preview_of(mapping=None, use_project_pseudonyms=True)
        assert "pseudonyms_withheld" not in out["preview"]
        result = flagship.execute_as_recipe(out)
        record = json.loads(Path(result["manifest_path"]).read_text(
            encoding="utf-8"))
        assert [entry["pseudonym"] for entry in record["entries"]] == [
            "Alex", "Sam"]
        assert "pseudonyms_withheld" not in record

    def test_the_import_withholds_it_too(self, project):
        """`import_text_file(apply_project_pseudonyms=True)` reads the
        researcher's own `pseudonyms.json`: the same rule."""
        mapping, _, _ = CARRYING["contains_other"]
        (project / "pseudonyms.json").write_text(json.dumps(mapping),
                                                 encoding="utf-8")
        out = json.loads(server.import_text_file(
            filename="new.txt", content="Thomas Smith met Smith.",
            create_backup=False, apply_project_pseudonyms=True))
        report = out["project_pseudonyms"]
        assert report["per_pseudonym"] == [
            {"entry": 0, "pseudonym": "Jones", "count": 1},
            {"entry": 1, "pseudonym": None, "count": 1}]
        assert report["pseudonyms_withheld"] == 1
        assert report["pseudonyms_withheld_note"] == \
            P.PSEUDONYMS_WITHHELD_NOTE
        assert "smith" not in json.dumps(out).lower()

    def test_the_sentence_keeps_the_house_rules(self):
        _house_rules([P.PSEUDONYMS_WITHHELD_NOTE], ["withheld note"])


# =============================================================================
# FIX ROUND 1, F4: COMPACT BY DEFAULT
# =============================================================================

class TestTheReportIsCompactByDefault:
    """The owner's ruling of 2026-09-23. Full detail for the file this
    call names; for every other file that still shows a name, one row with
    its id, its name and the two counts, nothing else. `residue_detail=
    "project"` gives every file its full row. The totals are complete and
    the warnings read them, so folding the detail never silences one."""

    COMPACT_KEYS = {"file_id", "name", "occurrences"}

    def _three_files(self, project):
        _set_text(project, 4, "THOMAS in four, and Thomas_P01.")
        _set_text(project, 5, "Thomasin in five.", name="five.txt")
        _set_text(project, 6, "nothing here at all.", name="six.txt")

    def test_other_files_get_one_compact_row_each(self, project):
        self._three_files(project)
        out = preview_of()
        block = _block(out)
        assert block["detail"] == "file"
        assert block["detail_note"] == (
            "Full detail is given for the file this call names; every "
            "other file that still shows a name has one row, its id, its "
            "name and the two readings' counts, for up to 1,000 files, and "
            "past that is listed by id only in more_files_showing_a_name. "
            "The totals and the warnings cover every file either way. Call "
            "again with residue_detail=\"project\" for the full detail of "
            "up to 200 files, with one such row for the rest.")
        rows = _rows(out)
        assert sorted(rows) == [4, 5]          # six shows nothing, one is clean
        for fid in (4, 5):
            assert set(rows[fid]) == self.COMPACT_KEYS, rows[fid]
        assert rows[4]["occurrences"]["wide"] == 2
        assert rows[4]["occurrences"]["whole_word"] == 0
        # No word of another file's text, not even a longer word (beyond
        # the server's own fixed prose, whose example is Thomas_P01).
        assert "Thomas_P01" not in json.dumps(without_fixed_prose(block))

    def test_the_named_file_keeps_its_full_row(self, project):
        self._three_files(project)
        out = preview_of(file_id=4)
        rows = _rows(out)
        assert rows[4]["file_not_rewritten_because"] == "no_match"
        assert rows[4]["entries"][0]["longer_words"] == [
            {"word": "Thomas_P01", "count": 1}]
        assert set(rows[5]) == self.COMPACT_KEYS

    def test_project_detail_gives_every_file_its_full_row(self, project):
        self._three_files(project)
        block = _block(preview_of(residue_detail="project"))
        assert block["detail"] == "project"
        assert "detail_note" not in block
        rows = {row["file_id"]: row for row in block["files"]}
        assert rows[4]["entries"] and rows[5]["entries"]
        assert rows[4]["file_not_rewritten_because"] == "another_file"

    def test_the_totals_and_the_warnings_are_the_same_either_way(
            self, project):
        self._three_files(project)
        compact = preview_of()
        full = preview_of(residue_detail="project")
        assert _block(compact)["totals"] == _block(full)["totals"]
        assert compact["warnings"] == full["warnings"]
        assert _file_text_warning(compact) is not None

    def test_a_file_past_the_budget_that_shows_a_name_gets_a_compact_row(
            self, project, monkeypatch):
        self._three_files(project)
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK", 0)
        block = _block(preview_of())
        rows = {row["file_id"]: row for row in block["files"]}
        assert rows[4] == {"file_id": 4, "name": "quiet.txt",
                           "counted": False}
        assert rows[5] == {"file_id": 5, "name": "five.txt",
                           "counted": False}
        assert 6 not in rows and 6 in block["files_not_counted"]
        assert block["totals"]["files_showing_a_name_not_counted"] == 2

    def test_the_argument_is_checked_and_not_bound(self, project):
        refused = preview_of(residue_detail="everything")
        assert refused == {
            "error": "residue_detail must be one of file, project."}
        a = preview_of()
        b = preview_of(residue_detail="project")
        assert a["preview_token"].split(".")[2] == \
            b["preview_token"].split(".")[2]
        assert execute_from(b).get("success") is True

    def test_the_sidecar_compact_rows_carry_no_form(self, project):
        self._three_files(project)
        _sidecar(project, [{"original": "Thomas", "pseudonym": "Alex"}])
        out = preview_of(mapping=None, use_project_pseudonyms=True)
        for row in _block(out)["files"]:
            assert set(row) <= self.COMPACT_KEYS | {"counted"}
        residue = without_fixed_prose(out["preview"]["residue"])
        for row in residue["file_text"]["files"]:
            row.pop("name")
        assert "Thomas" not in json.dumps(residue)


# =============================================================================
# FIX ROUND 1, F5: SIZE AND WORK BOUNDS
# =============================================================================

def _people(count):
    """Distinct, valid, name-like forms that share no letters' run."""
    names = []
    for index in range(count):
        a, b = divmod(index, 26)
        names.append("Q" + chr(97 + a) + chr(97 + b) + "x")
    return names


class TestTheBlockIsBounded:

    def test_the_entry_rows_are_capped_and_the_row_stays_complete(
            self, project):
        people = _people(P.MAX_RESIDUE_ENTRY_ROWS + 10)
        mapping = [{"original": name, "pseudonym": f"P{name}"}
                   for name in people]
        # Each name once, and the last one three times, so wide-count
        # order is visible: the most frequent entry comes first.
        text = " ".join(people) + " " + " ".join([people[-1]] * 2) + "."
        _set_text(project, 4, text)
        out = preview_of(mapping=mapping, file_id=4)
        row = _rows(out)[4]
        assert len(row["entries"]) == P.MAX_RESIDUE_ENTRY_ROWS
        assert row["entries_truncated"] is True
        assert row["entries"][0]["entry"] == len(people) - 1
        assert row["entries"][0]["occurrences"]["wide"] == 3
        assert [e["entry"] for e in row["entries"][1:4]] == [0, 1, 2]
        # The row's own count, and the block's, are complete.
        assert row["occurrences"]["wide"] == len(people) + 2
        assert _block(out)["totals"]["occurrences"]["wide"] == \
            len(people) + 2

    def test_the_spelling_lists_are_capped_too(self, project):
        people = _people(P.MAX_RESIDUE_ENTRY_ROWS + 5)
        mapping = [{"original": name, "pseudonym": f"P{name}"}
                   for name in people]
        text = " ".join(name.upper() for name in people) + ". " + " ".join(
            name[:2] + "­" + name[2:] for name in people) + "."
        _set_text(project, 4, text)
        row = _rows(preview_of(mapping=mapping, file_id=4))[4]
        assert len(row["case_variants_seen"]) == P.MAX_RESIDUE_ENTRY_ROWS
        assert row["case_variants_seen_truncated"] is True
        assert len(row["normalisation_variants_seen"]) == \
            P.MAX_RESIDUE_ENTRY_ROWS
        assert row["normalisation_variants_seen_truncated"] is True

    def test_the_match_budget_drops_a_dense_file_to_the_cheap_question(
            self, project, monkeypatch):
        _set_text(project, 4, "Thomas " * 40)
        _set_text(project, 5, "Thomas once.", name="five.txt")
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_MATCHES", 30)
        out = preview_of(residue_detail="project")
        block = _block(out)
        # The PDF (file 2) has no match and is counted; file 4 passes the
        # budget halfway through its count.
        assert block["files_not_counted"] == [4, 5]
        rows = _rows(out)
        assert rows[4]["counted"] is False and rows[4]["shows_a_name"] is True
        # Once passed, a budget stays passed: file 5 is small but after it.
        assert rows[5]["counted"] is False
        assert block["totals"]["files_showing_a_name_not_counted"] == 2
        assert "2 file(s) would still show one of these names" in \
            _file_text_warning(out)

    def test_the_engine_stops_at_the_match_budget(self):
        """Ten matches in each of the two passes that run on ASCII text,
        and one more in each for the first sight of the spelling (fix
        round 2: placing a new spelling costs about a match again). A
        fresh compiled mapping for each call: the whole-word pass places a
        spelling once per PREVIEW, so a second call on the same one would
        be charged one less."""
        def compiled():
            return P.Compiled(P.validate_mapping(
                [{"original": "Thomas", "pseudonym": "Alex"}]))
        text = "Thomas " * 10
        assert P.names_left_in_text(compiled(), text, True, False,
                                    max_matches=21) is None
        found = P.names_left_in_text(compiled(), text, True, False,
                                     max_matches=22)
        assert found["matches"] == 22
        assert found["occurrences"] == {"wide": 10, "whole_word": 10}
        again = P.Compiled(P.validate_mapping(
            [{"original": "Thomas", "pseudonym": "Alex"}]))
        P.names_left_in_text(again, text, True, False)
        assert P.names_left_in_text(again, text, True, False)[
            "matches"] == 21

    def test_the_work_has_a_per_character_term(self, project, monkeypatch):
        """The work of a file is its length times the surface forms PLUS
        `RESIDUE_WORK_PER_CHARACTER`: the model of S-4, in which a
        one-form mapping no longer gets four hundred megabytes."""
        compiled = P.Compiled(P.validate_mapping(MAPPING))
        text1 = query(project, "SELECT fulltext FROM source WHERE id=1"
                      )[0]["fulltext"]
        after = P.apply_replacements(text1,
                                     P.find_replacements(compiled, text1))
        forms = len(compiled.forms)
        exact = len(after) * (forms + P.RESIDUE_WORK_PER_CHARACTER)
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK", exact)
        assert _block(preview_of())["files_counted"] == 1
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK", len(after) * forms)
        assert _block(preview_of())["files_counted"] == 0
        assert P.RESIDUE_WORK_PER_CHARACTER == 3


# =============================================================================
# FIX ROUND 1, F6: A READ THE REPORT COULD NOT MAKE IS SAID
# =============================================================================

class TestAnUnreadablePartIsSaid:

    @staticmethod
    def _unreadable_warning(out):
        found = [w for w in out["warnings"]
                 if w.startswith("Warning: this report could not read ")]
        assert len(found) <= 1
        return found[0] if found else None

    def test_a_file_text_read_that_fails_is_warned_about(self, project,
                                                         monkeypatch):
        """The Security gate's fault injection (S-5)."""
        _set_text(project, 4, "Thomas again, and Thomas.")

        def boom(self, *args, **kwargs):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(QualcoderDatabase, "_pseudonymise_file_text",
                            boom)
        out = preview_of()
        assert out["preview"]["residue"]["unreadable"] == ["source.fulltext"]
        assert self._unreadable_warning(out) == (
            "Warning: this report could not read source.fulltext, so it "
            "does not cover it, and a name there is counted nowhere in "
            "residue. Preview again; if the same parts still cannot be "
            "read, check them in QualCoder before sharing the project.")

    def test_a_missing_note_table_is_warned_about(self, project):
        con = sqlite3.connect(str(project / "data.qda"))
        con.execute("DROP TABLE code_image")
        con.commit()
        con.close()
        _reconnect(project)
        out = preview_of()
        assert "code_image.memo" in out["preview"]["residue"]["unreadable"]
        assert "code_image.memo" in self._unreadable_warning(out)

    def test_a_readable_project_says_nothing_of_the_kind(self, project):
        assert self._unreadable_warning(preview_of()) is None


# =============================================================================
# FIX ROUND 1, F7 AND F8: THE PINS THE GATES SHOWED MISSING
# =============================================================================

class TestTheFileTextWarningsArithmetic:
    """QA-2: the warning under the budget and the row cap together, and
    QA-7's clause. The warning reads the totals alone (F4)."""

    def test_the_budget_and_the_cap_together(self, project, monkeypatch):
        """The QA gate's `test_h` shape: rows past the budget that show
        nothing fill the cap in project detail, and the files that show a
        name fall past it; the warning still counts them."""
        compiled = P.Compiled(P.validate_mapping(MAPPING))
        _set_text(project, 2, "nothing on this page.")
        _set_text(project, 4, "nothing here either.")
        for fid in (5, 6, 7):
            _set_text(project, fid, "plain words only.", name=f"f{fid}.txt")
        for fid in (8, 9):
            _set_text(project, fid, "THOMAS was here.", name=f"f{fid}.txt")
        text1 = query(project, "SELECT fulltext FROM source WHERE id=1"
                      )[0]["fulltext"]
        after = P.apply_replacements(text1,
                                     P.find_replacements(compiled, text1))
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK", len(after) * (
            len(compiled.forms) + P.RESIDUE_WORK_PER_CHARACTER))
        monkeypatch.setattr(P, "MAX_RESIDUE_FILE_ROWS", 3)
        for detail in ("file", "project"):
            out = preview_of(residue_detail=detail)
            assert _file_text_warning(out) == (
                "Warning: after this run, 2 file(s) would still show one of "
                "these names in their text, and were not counted in full "
                "because counting them passed this preview's budget; see "
                "files_not_counted. See residue.file_text, which names the "
                "files."), detail

    def test_counted_and_uncounted_files_are_told_apart(self, project,
                                                        monkeypatch):
        """The QA gate's `test_e` shape: a counted file with a name left,
        then the budget, then files past it that show one."""
        compiled = P.Compiled(P.validate_mapping(MAPPING))
        for fid in range(5, 10):
            _set_text(project, fid, f"THOMAS in {fid}.", name=f"f{fid}.txt")
        _set_text(project, 4, "Thomas_P01 in four.")
        text1 = query(project, "SELECT fulltext FROM source WHERE id=1"
                      )[0]["fulltext"]
        after = P.apply_replacements(text1,
                                     P.find_replacements(compiled, text1))
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK", (
            len(after) + len("extracted page text")
            + len("Thomas_P01 in four.")) * (
            len(compiled.forms) + P.RESIDUE_WORK_PER_CHARACTER))
        monkeypatch.setattr(P, "MAX_RESIDUE_FILE_ROWS", 3)
        out = preview_of()
        assert _file_text_warning(out) == (
            "Warning: after this run, 1 occurrence(s) of these names would "
            "still be in the text of 1 file(s), because the rewrite replaces "
            "whole words only and in one file per call. 1 of those are a "
            "name inside a longer word (usually a different word, left as it "
            "is). The split by kind is a heuristic; the total is not. 5 "
            "further file(s) would still show one of these names in their "
            "text, and were not counted in full because counting them passed "
            "this preview's budget; see files_not_counted. See "
            "residue.file_text, which names the files.")

    def test_a_counted_file_shows_a_name_on_either_reading(self, project,
                                                           monkeypatch):
        """QA-6 (Q03): a counted file shows a name when EITHER reading is
        non-zero. The union of F2 makes a whole word without a wide
        occurrence unreachable through the real engine since fix round 2
        reads the whole run of marks after a word (fix round 1 read
        sixteen, and a seventeenth that composed reached it: the
        re-verification's CORR-2; the argument that nothing else can is
        its composers sweep), so the rule is driven with an engine whose
        answer for file 4 is exactly that. The warning then says what
        the whole-word rule would still match, with no list of kinds
        (every kind is zero) and without the Mary, Ann reason."""
        _set_text(project, 4, "whatever the engine says.")
        real = P.names_left_in_text

        def only_whole_word(compiled, text, *args, **kwargs):
            found = real(compiled, text, *args, **kwargs)
            if found is not None and text == "whatever the engine says.":
                found["occurrences"] = {"wide": 0, "whole_word": 1}
            return found

        monkeypatch.setattr(P, "names_left_in_text", only_whole_word)
        out = preview_of(residue_detail="project")
        assert 4 in _rows(out)
        assert _block(out)["totals"]["files_showing_a_name"] == 1
        assert _block(out)["totals"]["files_whole_word_above_wide"] == 1
        assert _file_text_warning(out) == (
            "Warning: after this run, this run's own whole-word rule would "
            "still match 1 occurrence(s) of these names in the text of 1 "
            "file(s), which the wide reading did not count. See "
            "residue.file_text, which names the files.")

    def test_a_file_with_more_whole_words_than_wide_ones_is_said(
            self, project):
        """QA-7: "Mary, Ann" in another file under {Mary Ann, Mary, Ann}
        is one wide occurrence and two whole words."""
        mapping = [{"original": "Mary Ann", "pseudonym": "Pat"},
                   {"original": "Mary", "pseudonym": "Sue"},
                   {"original": "Ann", "pseudonym": "Joy"}]
        _set_text(project, 4, "Mary, Ann came.")
        out = preview_of(mapping=mapping)
        assert _rows(out)[4]["occurrences"] == {"wide": 1, "whole_word": 2}
        assert _block(out)["totals"]["files_whole_word_above_wide"] == 1
        assert ("In 1 of those file(s) the whole-word count is higher than "
                "the wide one, because the two readings divide the text into "
                "names differently (Mary, Ann read as Mary Ann), so running "
                "them would replace more names than the wide count says."
                in _file_text_warning(out))

    def test_the_fields_warning_says_a_non_zero_whole_word_number(
            self, project):
        """QA-6 (Q08): the only pin on "{M} of those" had it at 0."""
        con = sqlite3.connect(str(project / "data.qda"))
        con.execute("UPDATE source SET memo='about Thomas' WHERE id=1")
        con.execute("UPDATE cases SET name='Thomas' WHERE caseid=1")
        con.execute("UPDATE code_name SET memo='Thomas_P01 said' "
                    "WHERE cid=1")
        con.commit()
        con.close()
        _reconnect(project)
        fields = [w for w in preview_of()["warnings"]
                  if "note(s), label(s)" in w]
        assert fields[0].startswith(
            "Warning: 3 note(s), label(s) or attribute value(s) may still "
            "show one of these names, and this tool does not rewrite any of "
            "them; 2 of those match this run's own whole-word rule.")


class TestTheLongerWordsThroughTheTool:
    """F3 at tool level: the Security gate's samples in another file, and
    the one budget every list in a preview shares."""

    @pytest.mark.parametrize("name,text", [
        ("Thomas", "See Thomas_Smith_DOB_1984_03_12_NHS_4857773456_Grimsby_"
                   "ward_7 today."),
        ("トーマス",
         "トーマスさんは病院で働"
         "いていました。"),
        ("托马斯",
         "托马斯在医院当护士。"),
    ], ids=["identifier", "japanese", "chinese"])
    def test_a_clause_or_an_identifier_is_never_listed(self, project, name,
                                                       text):
        _set_text(project, 7, text, name="other.txt")
        out = preview_of(mapping=[{"original": name,
                                   "pseudonym": "Alexandra"}],
                         residue_detail="project")
        entry = _rows(out)[7]["entries"][0]
        assert entry["inside_a_longer_word"] == 1
        assert entry["longer_words"] == []
        assert entry["longer_words_truncated"] is True
        assert "Grimsby" not in json.dumps(out)
        assert "病院" not in json.dumps(out)

    def test_every_list_in_a_preview_shares_one_budget(self, project,
                                                       monkeypatch):
        for fid in range(5, 9):
            _set_text(project, fid, f"Thomas_P0{fid} and Thomasin_{fid}.",
                      name=f"f{fid}.txt")
        monkeypatch.setattr(P, "MAX_LONGER_WORDS_TOTAL_CHARS", 40)
        block = _block(preview_of(residue_detail="project"))
        listed = [item["word"] for row in block["files"]
                  for entry in row.get("entries", [])
                  for item in entry.get("longer_words", [])]
        assert sum(len(word) for word in listed) <= 40
        # A prefix of the rows' order: file 5's words, then file 6's.
        assert listed == ["Thomas_P05", "Thomasin_5", "Thomas_P06",
                          "Thomasin_6"]
        rows = {row["file_id"]: row for row in block["files"]}
        assert rows[7]["entries"][0]["longer_words"] == []
        assert rows[7]["entries"][0]["longer_words_truncated"] is True
        assert block["longer_words_note"].startswith(
            "The longer words this preview lists share one budget of 40 "
            "characters")

