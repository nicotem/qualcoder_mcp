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
             or "were not checked; preview them one at a time" in w
             or "too large to count in full with this many names" in w
             or "found before counting them stopped" in w
             or "too large to count in a preview with any mapping" in w
             or "a PDF source cannot be named for a preview" in w]
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
        """Past the budget: the files read before spent it. The file this
        call names is read first from the same budget (ruling 1 as
        amended, fix round 4); it is made small here, and the budget is
        room for it and file 2 and none after them, every file fitting
        it on its own."""
        self._several(project)
        _set_text(project, 1, "Thomas left.")
        work = {1: _work_of(_after(project)),
                2: _work_of("extracted page text"),
                4: _work_of("THOMAS in four."), 7: _work_of(
                    "and THOMAS_P01 in seven.")}
        budget = work[1] + work[2]
        assert work[7] <= budget < budget + work[4]
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK", budget)
        out = preview_of(residue_detail="project")
        block = _block(out)
        assert block["files_counted"] == 2          # files 1 and 2
        # In the order of the budget: the file this call names first,
        # then every other file in id order; once passed it stays passed.
        assert block["files_not_counted"] == [4, 5, 6, 7]
        assert block["files_too_large_for_this_mapping"] == []
        rows = _rows(out)
        for fid, shows in ((4, True), (5, False), (6, True), (7, True)):
            row = rows[fid]
            assert row["counted"] is False
            assert row["shows_a_name"] is shows, fid
            assert "occurrences" not in row and "entries" not in row
            assert "too_large_for_this_mapping" not in row
        assert block["totals"]["files_showing_a_name"] == 3
        note = block["files_not_counted_note"]
        assert note.startswith("4 file(s) were not counted in full, "
                               "because the files read before them had "
                               "spent one of this preview's two budgets")
        assert note.endswith(
            "Preview each on its own to count more of it: the file a call "
            "names is read first, with the first claim on the budgets, and "
            "none of these is too large for them, so each is counted there, "
            "in full or, if it holds more matches than the budget allows, in "
            "part.")
        assert "files_too_large_note" not in block
        _house_rules([note], ["files_not_counted_note"])
        # Deterministic: the same preview twice is the same block.
        assert json.dumps(_block(preview_of(
            residue_detail="project"))) == json.dumps(block)

    def test_a_budget_cannot_make_a_quiet_preview(self, project,
                                                  monkeypatch):
        """With no budget at all nothing is counted: every file is too
        large for it even under one name (the coordinator's follow-on to
        fix round 5: fewer names is not offered), the named one is still
        asked, and the warning says, out loud, which files show a name."""
        self._several(project)
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK", 0)
        out = preview_of()
        block = _block(out)
        assert block["files_counted"] == 0
        assert block["totals"]["occurrences"]["wide"] == 0
        assert block["totals"]["files_showing_a_name"] == 3
        assert block["files_too_large_for_this_mapping"] == []
        assert block["files_too_large_for_any_mapping"] == [1, 2, 4, 5, 6, 7]
        warning = _file_text_warning(out)
        assert warning == (
            "Warning: the file this call rewrites is too large to count in "
            "a preview with any mapping, even of one name, and it was asked "
            "whether a name would still show in it after this run: none "
            "does. The rewrite still applies to it (see "
            "files_too_large_for_any_mapping). 5 other file(s) are too "
            "large to count in a preview with any mapping, even of one name "
            "(3 of them would still show one of these names); no preview "
            "can count them (see files_too_large_for_any_mapping). See "
            "residue.file_text, which names the files.")
        assert "fewer names" not in warning

    def test_the_other_files_take_what_the_named_file_leaves(
            self, project, monkeypatch):
        """Ruling 1 as amended (fix round 4): the file this call names is
        counted first from the shared budget, and the others take what
        it leaves. A budget of the named file's work and file 2's: file 2
        is counted, and file 4, which would fit the budget alone and
        after file 2 alone, is past it."""
        self._several(project)
        named = _work_of(_after(project))
        work = {2: _work_of("extracted page text"),
                4: _work_of("THOMAS in four.")}
        assert work[2] + work[4] <= named
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK", named + work[2])
        block = _block(preview_of(residue_detail="project"))
        assert block["files_counted"] == 2                  # 1 and 2
        assert block["files_not_counted"] == [4, 5, 6, 7]
        assert block["files_too_large_for_this_mapping"] == []

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
    # Fix round 2, the re-verification's DR-1: a pseudonym only the
    # rewriter reads as carrying a mapped name ("Rene" before a combining
    # acute, the decomposed spelling of Rene with its accent). The union's
    # rewriter half withholds it.
    "rewriter_only": ([{"original": "Rene", "pseudonym": "Paul"},
                       {"original": "Thomas",
                        "pseudonym": "Rene\u0301 Martin"}],
                      "Thomas met Rene. Later Thomas left.", {1}),
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

    # v0.13, Brief 2 (its hand-off note, H.2.3): the preview's note block
    # quotes a pseudonym through the same `pseudonym_of`, so on the
    # sidecar path a pseudonym carrying a name is null there too, and the
    # preview's one count and sentence cover it; typed, it is quoted.
    @staticmethod
    def _note(folder, text):
        con = sqlite3.connect(str(folder / "data.qda"))
        con.execute("UPDATE source SET memo=? WHERE id=1", (text,))
        con.execute("UPDATE cases SET memo=? WHERE caseid=1", (text,))
        con.commit()
        con.close()

    @pytest.mark.parametrize("shape", sorted(CARRYING))
    def test_the_note_block_withholds_it_on_the_sidecar_path(self, tmp_path,
                                                             shape):
        mapping, text, withheld = CARRYING[shape]
        folder = self._project(tmp_path, text, mapping, sidecar=True)
        self._note(folder, text)
        with wired(folder):
            out = call(mapping=None, use_project_pseudonyms=True,
                       rewrite_memos=True)
        block = out["preview"]["memo_rewrites"]
        assert block["by_entry"], shape
        for item in block["by_entry"]:
            assert (item["pseudonym"] is None) == (
                item["entry"] in withheld), (shape, item)
        # Test 25: no original and no variant anywhere in the block, and
        # nowhere in the preview beyond the declared routes.
        serialised = json.dumps(block).lower()
        for name in _originals(mapping):
            assert name.lower() not in serialised, (shape, name)
            assert _outside_fixed_prose(out, name) == [], (shape, name)
        assert out["preview"]["pseudonyms_withheld"] == len(withheld)
        assert "pseudonyms_withheld" not in block

    def test_the_typed_path_note_block_quotes_it(self, tmp_path):
        mapping, text, _ = CARRYING["contains_other"]
        folder = self._project(tmp_path, text, mapping, sidecar=False)
        self._note(folder, text)
        with wired(folder):
            out = preview_of(mapping=mapping, rewrite_memos=True)
        assert [(item["entry"], item["pseudonym"]) for item in
                out["preview"]["memo_rewrites"]["by_entry"]] == [
            (0, "Jones"), (1, "Alex Smith")]

    # Brief 2's hand-off note, H.2.4: the note section of the run record
    # carries each replacement's entry and span and no pseudonym, and the
    # journal's note lines carry counts, so a withheld pseudonym reaches
    # neither on either mapping path.
    @pytest.mark.parametrize("sidecar", [True, False],
                             ids=["sidecar-path", "typed-path"])
    def test_the_note_records_carry_no_withheld_pseudonym(self, tmp_path,
                                                          sidecar):
        mapping, text, withheld = CARRYING["contains_other"]
        folder = self._project(tmp_path, text, mapping, sidecar=sidecar)
        self._note(folder, text)
        with wired(folder):
            if sidecar:
                out = call(mapping=None, use_project_pseudonyms=True,
                           rewrite_memos=True)
                result = flagship.execute_as_recipe(out)
            else:
                out = preview_of(mapping=mapping, rewrite_memos=True)
                result = execute_from(out, mapping=mapping)
            assert result.get("success") is True, result
            manifest, journal = self._records(folder, result)
        record = json.loads(manifest)
        assert len(record["memos"]) == 2
        for row in record["memos"]:
            for replacement in row["replacements"]:
                assert set(replacement) == {"entry", "new_span"}
        for name in _originals(mapping) + ["Alex Smith"]:
            assert name.lower() not in manifest.lower(), name
            for row in journal:
                assert name.lower() not in row["jentry"].lower(), name
        body = [row["jentry"] for row in journal
                if "Notes rewritten" in row["jentry"]][0]
        assert "Notes rewritten: 2 (source 1, cases 1); replacements: 6." \
            in body

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
            "up to 200 files and one such row for up to 1,000 more.")
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

    def test_the_match_budget_stops_a_dense_file_part_way(
            self, project, monkeypatch):
        """Fix round 5, the lead's rules 1 and 3. File 4's count passes
        the match budget part-way: a first sight charged 5 and 1 for each
        later match, so the 27th match of "Thomas" is the first past 30.
        The count is charged all it spent, so the budget has run out and
        file 5 after it is past it; file 4 certainly shows a name and is
        counted in part with a lower bound, never with a remedy."""
        _set_text(project, 4, "Thomas " * 40)
        _set_text(project, 5, "Thomas once.", name="five.txt")
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_MATCHES", 30)
        out = preview_of(residue_detail="project")
        block = _block(out)
        assert block["files_counted_in_part"] == [4]
        assert block["files_not_counted"] == [5]
        assert block["files_too_large_for_this_mapping"] == []
        rows = _rows(out)
        assert rows[4] == {
            "file_id": 4, "name": "quiet.txt",
            "rewritten_by_this_run": False,
            "file_not_rewritten_because": "another_file",
            "counted": False, "counted_in_part": True,
            "occurrences_at_least": 27, "shows_a_name": True}
        assert rows[5]["counted"] is False and rows[5]["shows_a_name"]
        totals = block["totals"]
        assert totals["files_counted_in_part"] == 1
        assert totals["occurrences_at_least"] == 27
        assert totals["files_showing_a_name"] == 2
        assert totals["files_showing_a_name_not_counted"] == 1
        assert _file_text_warning(out) == (
            "Warning: after this run, 1 file(s) would still show at least "
            "27 occurrence(s) of these names in their text, found before "
            "counting them stopped at this preview's budget (see "
            "files_counted_in_part). 1 further file(s) would still show one "
            "of these names in their text, and were not counted in full "
            "because the files before them spent this preview's budget; "
            "preview each on its own to count more of it (see "
            "files_not_counted). See residue.file_text, which names the "
            "files.")
        note = block["files_counted_in_part_note"]
        assert note.startswith("1 file(s) were counted in part: each count "
                               "stopped at this preview's budget")
        assert "(27 in all)" in note
        # The fourth re-verification's N-5: a file after it that is too
        # large for this mapping is not past the budget.
        assert note.endswith("so every file after it that is not too large "
                             "for this mapping is past the budget.")
        _house_rules([note], ["files_counted_in_part_note"])
        # The default detail's compact row carries the lower bound too.
        compact = _rows(preview_of())[4]
        assert compact == {"file_id": 4, "name": "quiet.txt",
                           "counted": False, "occurrences_at_least": 27}

    def test_the_engine_stops_at_the_match_budget(self):
        """Ten matches in each of the two passes that run on ASCII text,
        the first of each charged `RESIDUE_FIRST_SIGHT_MATCHES` (fix round
        2: placing a new spelling costs several matches), so 2 x (5 + 9).
        A fresh compiled mapping for each call: the whole-word pass places
        a spelling once per PREVIEW, so a second call on the same one is
        charged four less."""
        def compiled():
            return P.Compiled(P.validate_mapping(
                [{"original": "Thomas", "pseudonym": "Alex"}]))
        text = "Thomas " * 10
        assert P.RESIDUE_FIRST_SIGHT_MATCHES == 5
        assert P.names_left_in_text(compiled(), text, True, False,
                                    max_matches=27) is None
        found = P.names_left_in_text(compiled(), text, True, False,
                                     max_matches=28)
        assert found["matches"] == 28
        assert found["occurrences"] == {"wide": 10, "whole_word": 10}
        again = P.Compiled(P.validate_mapping(
            [{"original": "Thomas", "pseudonym": "Alex"}]))
        P.names_left_in_text(again, text, True, False)
        assert P.names_left_in_text(again, text, True, False)[
            "matches"] == 24

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
        # Measured on the file this call names, read first against the
        # whole shared budget (ruling 1 as amended, fix round 4; the
        # estimate against an EMPTY budget, fix round 5's rule 2): counted
        # at exactly its work, too large one unit of the term below it.
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK", exact)
        block = _block(preview_of())
        assert 1 not in block["files_too_large_for_this_mapping"]
        assert 1 not in block["files_not_counted"]
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK", len(after) * forms)
        block = _block(preview_of())
        # Below one name's work too (forms 3, the term 3): too large for
        # any mapping.
        assert block["files_too_large_for_any_mapping"][:1] == [1]
        assert block["files_not_counted"][:1] == [1]
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

    # These shapes are about the files AFTER the one this call names
    # spending what it leaves of the budget (ruling 1 as amended, fix
    # round 4: it is read first, from the same budget), so it is made
    # small ("Alex left." after the run) and every other file fits the
    # budget on its own.
    PAST = ("Warning: after this run, {n} file(s) would still show one of "
            "these names in their text, and were not counted in full "
            "because the files before them spent this preview's budget; "
            "preview each on its own to count more of it (see "
            "files_not_counted).")

    def test_the_budget_and_the_cap_together(self, project, monkeypatch):
        """The QA gate's `test_h` shape: rows past the budget that show
        nothing fill the full-row cap in project detail, and the files
        that show a name fall past it, and past the compact cap too (the
        re-verification's CORR2-3: compact rows have had a cap of their
        own since fix round 2); the warning still counts them all."""
        _set_text(project, 1, "Thomas left.")
        _set_text(project, 2, "nothing on this page.")
        _set_text(project, 4, "nothing here either.")
        for fid in (5, 6, 7):
            _set_text(project, fid, "plain words only.", name=f"f{fid}.txt")
        for fid in (8, 9):
            _set_text(project, fid, "THOMAS was here.", name=f"f{fid}.txt")
        budget = (_work_of(_after(project))
                  + _work_of("nothing on this page."))   # files 1 and 2
        assert _work_of("nothing on this page.") <= budget
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK", budget)
        monkeypatch.setattr(P, "MAX_RESIDUE_FILE_ROWS", 3)
        monkeypatch.setattr(P, "MAX_RESIDUE_COMPACT_ROWS", 1)
        for detail in ("file", "project"):
            out = preview_of(residue_detail=detail)
            block = _block(out)
            assert block["files_not_counted"] == [4, 5, 6, 7, 8, 9]
            listed = sum(1 for row in block["files"]
                         if row.get("counted") is False
                         and (row.get("shows_a_name") or "shows_a_name"
                              not in row))
            assert listed < 2 == block["totals"][
                "files_showing_a_name_not_counted"], detail
            assert _file_text_warning(out) == self.PAST.format(n=2) + (
                " See residue.file_text, which names the files."), detail

    def test_counted_and_uncounted_files_are_told_apart(self, project,
                                                        monkeypatch):
        """The QA gate's `test_e` shape: a counted file with a name left,
        then the budget, then files past it that show one."""
        _set_text(project, 1, "Thomas left.")
        for fid in range(5, 10):
            _set_text(project, fid, f"THOMAS in {fid}.", name=f"f{fid}.txt")
        _set_text(project, 4, "Thomas_P01 in four.")
        budget = (_work_of(_after(project)) + _work_of("extracted page text")
                  + _work_of("Thomas_P01 in four."))
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK", budget)
        monkeypatch.setattr(P, "MAX_RESIDUE_FILE_ROWS", 3)
        out = preview_of()
        assert _file_text_warning(out) == (
            "Warning: after this run, 1 occurrence(s) of these names would "
            "still be in the text of 1 file(s), because the rewrite replaces "
            "whole words only and in one file per call. 1 of those are a "
            "name inside a longer word (usually a different word, left as it "
            "is). The split by kind is a heuristic; the total is not. 5 "
            "further file(s) would still show one of these names in their "
            "text, and were not counted in full because the files before "
            "them spent this preview's budget; preview each on its own to "
            "count more of it (see files_not_counted). See residue.file_text, "
            "which names the files.")

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


# =============================================================================
# FIX ROUND 2 (BRIEF1_FIX2_MANDATE.md): THE THIRD TIER, AND THE PINS THE
# RE-VERIFICATION SHOWED MISSING
# =============================================================================

def _work_of(text, mapping=MAPPING):
    """`residue_work` of `text` under `mapping`, in the budgets' units."""
    return P.residue_work(P.Compiled(P.validate_mapping(mapping)), text)


def _after(project, fid=1, mapping=MAPPING):
    """File `fid`'s text as this run leaves it."""
    compiled = P.Compiled(P.validate_mapping(mapping))
    text = query(project, "SELECT fulltext FROM source WHERE id=?",
                 (fid,))[0]["fulltext"]
    return P.apply_replacements(text, P.find_replacements(compiled, text))


# The fixture's mapping and thirty entries no text holds: 33 surface forms,
# so a file costs about nine times more under it than under one name, and
# a work budget can make every fixture file too large for this mapping and
# none too large for any (the coordinator's follow-on to fix round 5).
PADDED = MAPPING + [
    {"original": f"Zyx{chr(97 + i // 26)}{chr(97 + i % 26)}q",
     "pseudonym": f"Pp{i:03d}"} for i in range(30)]


def _room_for_one_name(project, mapping=PADDED):
    """The least work budget every file with text fits under ONE name;
    each is asserted too large for `mapping` at it."""
    compiled = P.Compiled(P.validate_mapping(mapping))
    texts = [_after(project, 1, mapping)] + [
        row["fulltext"] for row in query(
            project, "SELECT fulltext FROM source WHERE id != 1 AND "
            "fulltext IS NOT NULL AND fulltext != ''")]
    budget = max(P.residue_work_at_one_form(text) for text in texts)
    assert all(P.residue_work(compiled, text) > budget for text in texts)
    return budget


class TestTheCheckBudget:
    """B-2 and the lead's ruling on it: past the count's budgets a file is
    asked the cheap question, which costs a count on a file where no name
    shows, so the question has a budget of its own; past that a file is
    not checked, named in `files_not_checked` and in the warning with how
    to get it checked, and never reported clean."""

    def _several(self, project):
        """The work budget's shape, with the file this call names made
        small, and a work budget that fits every file on its own and
        files 1 and 2 together: files 4 to 7 are past it, and go to the
        question."""
        TestTheWorkBudget()._several(project)
        _set_text(project, 1, "Thomas left.")
        budget = _work_of(_after(project)) + _work_of("extracted page text")
        assert _work_of("and THOMAS_P01 in seven.") <= budget
        return budget

    WORK = {4: "THOMAS in four.", 5: "nothing here at all.",
            6: "Thomasin in six.", 7: "and THOMAS_P01 in seven."}

    def test_past_the_check_budget_a_file_is_not_checked(self, project,
                                                         monkeypatch):
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK",
                            self._several(project))
        # Room to ask files 4 and 5; file 6 fits the question's budget on
        # its own but not after them, so it closes it: 6 and 7 are not
        # checked.
        work = {fid: _work_of(text) for fid, text in self.WORK.items()}
        monkeypatch.setattr(P, "MAX_RESIDUE_CHECK_WORK", work[4] + work[5])
        out = preview_of(residue_detail="project")
        block = _block(out)
        assert block["files_counted"] == 2
        assert block["files_not_counted"] == [4, 5]
        assert block["files_not_checked"] == [6, 7]
        assert block["totals"]["files_not_checked"] == 2
        assert block["files_too_large_for_this_mapping"] == []
        rows = _rows(out)
        for fid in (6, 7):
            # Never reported clean: the row says it was not checked, and
            # it has no answer to "does a name show".
            assert rows[fid] == {"file_id": fid, "name": rows[fid]["name"],
                                 "rewritten_by_this_run": False,
                                 "file_not_rewritten_because":
                                     rows[fid]["file_not_rewritten_because"],
                                 "counted": False, "checked": False}
        assert block["files_not_checked_note"] == (
            "2 file(s) were not checked at all: the files read before them "
            "had spent this preview's budget for counting, and then its "
            "budget for asking whether a name shows (which costs as much as "
            "a count on a file where no name shows), or the question alone "
            "was larger than that budget. They are listed in "
            "files_not_checked and are not reported clean: preview them one "
            "at a time, or use fewer names, to check them. The file a call "
            "names is read first, and none of these is too large to be "
            "counted there, in full or in part.")
        _house_rules([block["files_not_checked_note"]],
                     ["files_not_checked_note"])
        assert _file_text_warning(out) == (
            "Warning: after this run, 1 file(s) would still show one of "
            "these names in their text, and were not counted in full "
            "because the files before them spent this preview's budget; "
            "preview each on its own to count more of it (see "
            "files_not_counted). 2 file(s) were not checked; preview them "
            "one at a time, or use fewer names, to check them (see "
            "files_not_checked). See residue.file_text, which names the "
            "files.")

    def test_the_default_detail_names_them_by_id_only(self, project,
                                                      monkeypatch):
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK",
                            self._several(project))
        monkeypatch.setattr(P, "MAX_RESIDUE_CHECK_WORK",
                            _work_of(self.WORK[4]))
        out = preview_of()
        block = _block(out)
        assert block["files_not_checked"] == [5, 6, 7]
        # File 4 was asked and shows a name: its compact row. The files
        # not checked have none.
        assert block["files"] == [{"file_id": 4, "name": "quiet.txt",
                                   "counted": False}]
        assert block["totals"]["files_showing_a_name"] == 1
        assert "3 file(s) were not checked" in _file_text_warning(out)

    def test_a_file_too_large_for_the_question_does_not_close_it(
            self, project, monkeypatch):
        """The lead's ruling 2 (fix round 3) on the question's tier: a
        file larger than the question's budget on its own is not checked
        and leaves the budget open for the smaller files after it; one
        that fits on its own but not after the others closes it."""
        _set_text(project, 1, "Thomas left.")
        big = "THOMAS in four, with a good many more words here."
        _set_text(project, 4, big)
        _set_text(project, 5, "nothing.", name="five.txt")
        _set_text(project, 6, "Thomasin.", name="six.txt")
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK", _work_of(big))
        monkeypatch.setattr(P, "MAX_RESIDUE_CHECK_WORK",
                            _work_of(big) - 1)
        block = _block(preview_of(residue_detail="project"))
        assert block["files_counted"] == 2                 # files 1 and 2
        assert block["files_not_checked"] == [4]
        assert block["files_not_counted"] == [5, 6]
        assert block["files_too_large_for_this_mapping"] == []
        assert block["totals"]["files_showing_a_name_not_counted"] == 1
        # Sticky when the files before spent it (the second
        # re-verification's PN-8 and T4): file 6 fits the question's
        # budget on its own but not after file 5, so it closes it, and
        # file 7, small enough for what is left, is not asked either.
        _set_text(project, 7, "ok.", name="seven.txt")
        room = _work_of("nothing.") + _work_of("Thomasin.") - 1
        assert _work_of("nothing.") + _work_of("ok.") <= room
        monkeypatch.setattr(P, "MAX_RESIDUE_CHECK_WORK", room)
        block = _block(preview_of(residue_detail="project"))
        assert block["files_not_counted"] == [5]
        assert block["files_not_checked"] == [4, 6, 7]

    def test_a_checked_file_after_a_counted_one_and_the_warning_joins(
            self, project, monkeypatch):
        """All three tiers in one preview: counted, checked, not checked;
        the warning's sentences in that order."""
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK",
                            self._several(project))
        monkeypatch.setattr(P, "MAX_RESIDUE_CHECK_WORK",
                            _work_of(self.WORK[4]) + _work_of(self.WORK[5]))
        out = preview_of(residue_detail="project")
        block = _block(out)
        assert block["files_counted"] == 2
        assert block["files_not_counted"] == [4, 5]
        assert block["files_not_checked"] == [6, 7]
        assert _rows(out)[4]["shows_a_name"] is True
        warning = _file_text_warning(out)
        assert warning.index("were not counted in full") < warning.index(
            "were not checked") < warning.index("See residue.file_text")


class TestTheUnionAtTheCheapQuestion:
    """The re-verification's lane 4, finding 2 (O4): the cheap question
    past a budget asks the union too, so a file whose only name is
    written before a combining mark is said to show one and the preview
    is never quiet. Past the work budget and past the match budget,
    separately."""

    MAPPING = [{"original": "Rene", "pseudonym": "Alex"}]

    NFD_FILE = "Later Rene\u0301 arrived."

    def _project(self, project, file_2):
        _set_text(project, 1, "Rene met the team.")
        _set_text(project, 2, file_2)
        _set_text(project, 4, self.NFD_FILE)

    def _past_one_budget(self, project, monkeypatch, budget):
        """File 4 past a budget that file 2, read before it, spent: the
        work (a budget that fits file 4 on its own and not after file 2),
        or the matches (file 2 names Rene twice and spends them all)."""
        if budget == "MAX_RESIDUE_SCAN_WORK":
            self._project(project, "extracted page text")
            monkeypatch.setattr(P, budget, _work_of(self.NFD_FILE,
                                                    self.MAPPING))
        else:
            self._project(project, "Rene, then Rene.")
            compiled = P.Compiled(P.validate_mapping(self.MAPPING))
            spent = P.names_left_in_text(compiled, "Rene, then Rene.",
                                         True, False)["matches"]
            monkeypatch.setattr(P, budget, spent)

    @pytest.mark.parametrize("budget", ["MAX_RESIDUE_SCAN_WORK",
                                        "MAX_RESIDUE_SCAN_MATCHES"])
    def test_a_file_past_a_budget_shows_its_nfd_name(self, project,
                                                     monkeypatch, budget):
        self._past_one_budget(project, monkeypatch, budget)
        out = preview_of(residue_detail="project", mapping=self.MAPPING)
        block = _block(out)
        assert block["files_not_counted"] == [4]
        assert block["files_too_large_for_this_mapping"] == []
        assert _rows(out)[4]["shows_a_name"] is True
        assert block["totals"]["files_showing_a_name_not_counted"] == 1
        assert ("1 further file(s) would still show one of these names in "
                "their text, and were not counted in full because the files "
                "before them spent this preview's budget"
                if budget == "MAX_RESIDUE_SCAN_MATCHES" else
                "Warning: after this run, 1 file(s) would still show one of "
                "these names in their text, and were not counted in full "
                "because the files before them spent this preview's budget"
                ) in _file_text_warning(out)


class TestTheWarningReadsTheTotalsPastTheCaps:
    """The re-verification's CORR-3: with more files showing a name than
    the rows have room for, the warning's count is the totals', not the
    listed rows' sum."""

    def test_the_occurrence_count_is_the_totals(self, project, monkeypatch):
        for fid in range(5, 10):
            _set_text(project, fid, f"THOMAS and THOMAS in {fid}.",
                      name=f"f{fid}.txt")
        monkeypatch.setattr(P, "MAX_RESIDUE_FILE_ROWS", 1)
        monkeypatch.setattr(P, "MAX_RESIDUE_COMPACT_ROWS", 2)
        out = preview_of()
        block = _block(out)
        wide = block["totals"]["occurrences"]["wide"]
        listed = sum(row["occurrences"]["wide"] for row in block["files"]
                     if "occurrences" in row)
        assert block["more_files_showing_a_name"] == [7, 8, 9]
        assert wide == 10 and listed == 4
        assert _file_text_warning(out).startswith(
            f"Warning: after this run, {wide} occurrence(s) of these names "
            f"would still be in the text of 5 file(s), ")


class TestTheMatchBudgetAcrossFiles:
    """The re-verification's B-5 (MB1): each file alone is under the
    match budget and the two together are over it, so the second is not
    counted. Proposed by lane 3."""

    def test_the_match_budget_is_spent_across_files(self, project,
                                                    monkeypatch):
        _set_text(project, 5, "Thomas " * 10, name="five.txt")
        _set_text(project, 6, "Thomas " * 10, name="six.txt")
        alone = _block(preview_of(residue_detail="project"))
        assert alone["files_not_counted"] == []
        # File 5 spends 28 (ten matches in each of two passes, the first
        # sight of the spelling charged five in each); file 6 would spend
        # 24 on its own (the whole-word pass placed "Thomas" in file 5).
        # Fix round 5: file 6's count stops at the 9th match of its direct
        # pass (5 + 8 past the 12 left), is charged all it spent and is
        # counted in part; the budget has run out, so file 7 is past it.
        _set_text(project, 7, "Thomas once.", name="seven.txt")
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_MATCHES", 40)
        out = preview_of(residue_detail="project")
        block = _block(out)
        assert block["files_counted_in_part"] == [6]
        assert block["files_not_counted"] == [7]
        assert block["files_too_large_for_this_mapping"] == []
        assert _rows(out)[6]["occurrences_at_least"] == 9

    def test_the_entry_cap_holds_on_every_row_in_project_detail(
            self, project):
        """B-5 (MC2): every full row is capped, not only the named file's."""
        people = _people(P.MAX_RESIDUE_ENTRY_ROWS + 10)
        mapping = [{"original": name, "pseudonym": f"P{name}"}
                   for name in people]
        for fid in (5, 6):
            _set_text(project, fid, " ".join(people) + ".",
                      name=f"f{fid}.txt")
        _set_text(project, 1, people[0] + " said so.")
        rows = _rows(preview_of(mapping=mapping, residue_detail="project"))
        for fid in (5, 6):
            assert len(rows[fid]["entries"]) == P.MAX_RESIDUE_ENTRY_ROWS
            assert rows[fid]["entries_truncated"] is True
            assert rows[fid]["occurrences"]["wide"] == len(people)


class TestTheUnionForLabelsAndAttributeValues:
    """The re-verification's lane 4, finding 3 (O5): a label and an
    attribute value written before a combining mark count in the wide
    reading too, as a note does, and the fields warning says so."""

    MAPPING = [{"original": "Rene", "pseudonym": "Alex"}]

    def test_a_label_and_an_attribute_value(self, project):
        con = sqlite3.connect(str(project / "data.qda"))
        con.execute("UPDATE code_name SET name=? WHERE cid=1",
                    ("Rene\u0301",))
        con.execute("INSERT INTO attribute_type (name, date, owner, memo, "
                    "caseOrFile, valuetype) VALUES ('Group', 'd', "
                    "'TestCoder', '', 'case', 'character')")
        con.execute("INSERT INTO attribute (attrid, name, attr_type, value, "
                    "id, date, owner) VALUES (1, 'Group', 'case', ?, 1, "
                    "'d', 'TestCoder')", ("Rene\u0301 group",))
        con.commit()
        con.close()
        _reconnect(project)
        out = preview_of(mapping=self.MAPPING)
        residue = out["preview"]["residue"]
        assert residue["code_names"] == {"wide": 1, "whole_word": 1}
        attribute = residue["attribute_values"]
        assert attribute == {"wide": 1, "whole_word": 1}
        fields = [w for w in out["warnings"] if "note(s), label(s)" in w]
        assert fields[0].startswith(
            "Warning: 2 note(s), label(s) or attribute value(s) may still "
            "show one of these names, and this tool does not rewrite any of "
            "them; 2 of those match this run's own whole-word rule.")


class TestTwoUnreadablePartsAreNamed:
    """The re-verification's lane 4, finding 4 (O1): with two parts
    unreadable the warning names both, in the order of the list."""

    def test_both_in_order(self, project):
        con = sqlite3.connect(str(project / "data.qda"))
        con.execute("DROP TABLE code_av")
        con.execute("DROP TABLE code_image")
        con.commit()
        con.close()
        _reconnect(project)
        out = preview_of()
        unreadable = out["preview"]["residue"]["unreadable"]
        assert unreadable == ["code_av.memo", "code_image.memo"]
        found = [w for w in out["warnings"]
                 if w.startswith("Warning: this report could not read ")]
        assert found == [
            "Warning: this report could not read code_av.memo, "
            "code_image.memo, so it does not cover them, and a name there is "
            "counted nowhere in residue. Preview again; if the same parts "
            "still cannot be read, check them in QualCoder before sharing "
            "the project."]


class TestTheMatchBudgetCountsTheFoldedPass:
    """The re-verification's lane 4, finding 5 (O2): FD-9's "the match
    budget counts all three passes", for the folded pass, which runs only
    on text that is not ASCII. "Straße" ten times: ten matches in each of
    the direct, folded and whole-word passes, the first sight of the
    spelling charged five, 3 x 14 = 42; without the folded pass's share
    it would be 28."""

    def test_straße_ten_times(self):
        def compiled():
            return P.Compiled(P.validate_mapping(
                [{"original": "Straße", "pseudonym": "Alex"}]))
        text = "Straße " * 10
        found = P.names_left_in_text(compiled(), text, True, False)
        assert found["matches"] == 42
        assert found["occurrences"] == {"wide": 10, "whole_word": 10}
        assert P.names_left_in_text(compiled(), text, True, False,
                                    max_matches=41) is None
        assert P.names_left_in_text(compiled(), text, True, False,
                                    max_matches=42) is not None


class TestTheLongerWordsAreAPrefix:
    """The re-verification's lane 4, finding 6 (O3): the listing stops at
    the first word that does not fit the shared budget, so what is listed
    is a prefix of the rows' order; a shorter word after it is never
    listed. Words of 14, 14 and 14 characters, then one of 8, under a
    budget of 40: the first two fit, the third does not, and the fourth,
    which would fit, is not listed."""

    def test_a_shorter_word_after_the_first_that_does_not_fit(
            self, project, monkeypatch):
        _set_text(project, 5, "Thomas_P05_abc and Thomas_P05_abd.",
                  name="f5.txt")
        _set_text(project, 6, "Thomas_P06_abc here.", name="f6.txt")
        _set_text(project, 7, "Thomasin here.", name="f7.txt")
        monkeypatch.setattr(P, "MAX_LONGER_WORDS_TOTAL_CHARS", 40)
        block = _block(preview_of(residue_detail="project"))
        rows = {row["file_id"]: row for row in block["files"]}
        listed = [item["word"] for row in block["files"]
                  for entry in row.get("entries", [])
                  for item in entry.get("longer_words", [])]
        assert listed == ["Thomas_P05_abc", "Thomas_P05_abd"]
        for fid in (6, 7):
            entry = rows[fid]["entries"][0]
            assert entry["longer_words"] == []
            assert entry["longer_words_truncated"] is True


class TestTheImportCountsTheAppliedWithheldOnly:
    """The re-verification's lane 4, finding 7 (O7): FD-14's rule, the
    import report counts the withheld entries among those it APPLIED. An
    import whose content applies only the entry that is not withheld
    carries no `pseudonyms_withheld`."""

    def test_a_withheld_entry_not_applied_is_not_counted(self, project):
        mapping, _, _ = CARRYING["contains_other"]
        (project / "pseudonyms.json").write_text(json.dumps(mapping),
                                                 encoding="utf-8")
        out = json.loads(server.import_text_file(
            filename="alone.txt", content="Smith came alone.",
            create_backup=False, apply_project_pseudonyms=True))
        report = out["project_pseudonyms"]
        assert report["per_pseudonym"] == [
            {"entry": 0, "pseudonym": "Jones", "count": 1}]
        assert "pseudonyms_withheld" not in report
        assert "pseudonyms_withheld_note" not in report


def _plain(value):
    """`value` with its combining marks taken off and casefolded, so a
    decomposed, a composed and an ASCII-sanitised "Rene" all read "rene"."""
    import unicodedata
    return "".join(ch for ch in unicodedata.normalize("NFD", value)
                   if not unicodedata.combining(ch)).casefold()


class TestAnNfdFolderAndFileNameLeaveNoTrace:
    """The re-verification's DR-2: the union at every call site of the
    file-name and path rule, end to end. A project folder `René study.qda`
    and file 1 named `René_interview.txt`, both with the accent
    decomposed, under a mapping of "Rene": the detector does not see
    either name, the rewriter does. On both mapping paths, preview and
    execute: the run record's project path, backup path and file name are
    withheld, the journal's name and body carry neither, and no log
    record does (the journal's name reaches the host log at INFO). The
    preview and the execute result name the folder and the file, which
    ruling 12 declares; they are not what this pins."""

    MAPPING = [{"original": "Rene", "pseudonym": "Alex"}]
    NFD = "Rene\u0301"

    @pytest.mark.parametrize("path", ["typed", "sidecar"])
    def test_every_record_and_log(self, tmp_path, caplog, path):
        import logging
        folder = build_project(tmp_path / f"{self.NFD} study.qda",
                               "Rene met the team. Rene left.")
        con = sqlite3.connect(str(folder / "data.qda"))
        con.execute("UPDATE source SET name=? WHERE id=1",
                    (f"{self.NFD}_interview.txt",))
        con.commit()
        con.close()
        write_fixture_sidecar(str(folder))
        compiled = P.Compiled(P.validate_mapping(self.MAPPING))
        assert not compiled.detector.contains(f"{self.NFD}_interview.txt")
        assert compiled.pattern.search(f"{self.NFD}_interview.txt")
        if path == "sidecar":
            (folder / "pseudonyms.json").write_text(
                json.dumps(self.MAPPING), encoding="utf-8")
        caplog.set_level(logging.DEBUG)
        with wired(folder):
            if path == "sidecar":
                out = call(mapping=None, use_project_pseudonyms=True)
                result = flagship.execute_as_recipe(out)
            else:
                out = call(mapping=self.MAPPING)
                result = execute_from(out, mapping=self.MAPPING)
            assert result.get("success") is True, result
            journal = query(folder, "SELECT name, jentry FROM journal")
        manifest = Path(result["manifest_path"]).read_text(encoding="utf-8")
        record = json.loads(manifest)
        assert record["project_path"] is None
        assert record["backup_path"] is None
        assert record.get("paths_withheld")
        assert [item["name"] for item in record["files"]] == [None]
        assert "rene" not in _plain(manifest)
        assert journal, "the run wrote no journal entry"
        for row in journal:
            assert "rene" not in _plain(row["name"]), row["name"]
            assert "rene" not in _plain(row["jentry"])
        logged = [item.getMessage() for item in caplog.records]
        assert [line for line in logged if "rene" in _plain(line)] == []


class TestCompactRowsHaveACapOfTheirOwn:
    """The re-verification's CORR-4 and the lead's ruling on it: the
    owner's "compact by default" promises every file still showing a name
    a row (id, name, counts). Compact rows have their own cap of 1,000,
    full rows keep 200; past both a file is listed by id, and every
    document that describes the report says so."""

    @staticmethod
    def _many(project, count, first=10):
        con = sqlite3.connect(str(project / "data.qda"))
        con.executemany(
            "INSERT INTO source (id,name,fulltext,mediapath,memo,owner,date)"
            " VALUES (?,?,?,NULL,'','TestCoder','d')",
            [(fid, f"interview_{fid:04d}.txt", "THOMAS was here.")
             for fid in range(first, first + count)])
        con.commit()
        con.close()
        _reconnect(project)

    def test_the_two_caps(self):
        assert P.MAX_RESIDUE_FILE_ROWS == 200
        assert P.MAX_RESIDUE_COMPACT_ROWS == 1000

    def test_past_two_hundred_every_file_keeps_its_row(self, project):
        """The QA gate's 250-file shape, at the real caps: 250 compact
        rows by default (fix round 1 gave 200 and 50 ids); in project
        detail 200 full rows and 50 compact ones."""
        self._many(project, 250)
        block = _block(preview_of())
        assert len(block["files"]) == 250
        assert all(set(row) == {"file_id", "name", "occurrences"}
                   for row in block["files"])
        assert block["more_files_showing_a_name"] == []
        assert block["files_truncated"] is False
        project_block = _block(preview_of(residue_detail="project"))
        full = [row for row in project_block["files"] if "entries" in row]
        assert len(full) == 200
        assert len(project_block["files"]) == 250
        assert project_block["totals"] == block["totals"]

    def test_past_a_thousand_the_rest_are_ids(self, project):
        self._many(project, 1005)
        out = preview_of()
        block = _block(out)
        assert len(block["files"]) == 1000
        assert block["more_files_showing_a_name"] == list(
            range(1010, 1015))
        assert block["files_truncated"] is True
        assert block["totals"]["files_showing_a_name"] == 1005
        assert "in the text of 1005 file(s)" in _file_text_warning(out)

    @pytest.mark.parametrize("document", ["CHANGELOG.md", "PRIVACY.md"])
    def test_the_documents_state_both_caps(self, document):
        text = " ".join((Path(__file__).resolve().parents[1] / document)
                        .read_text(encoding="utf-8").split())
        assert "up to 1,000 files" in text, document
        assert "more_files_showing_a_name" in text, document


class TestACleanProjectPastBothBudgets:
    """B-2's pin at the real budgets: a clean project large enough to pass
    the count's budget and then the question's. Before fix round 2 the
    question was charged to nothing, so a clean project cost its size
    times its forms (17.7 s for twenty interviews at 1,997 forms). Now
    the files past the second budget are not checked, the warning says
    so in the lead's words, and what the preview reads stays inside the
    two budgets: deterministic, by what the engine was given, and timed
    against a ceiling far above the bound and far below the shape
    without it (about 11 s here)."""

    CEILING_SECONDS = 6.0

    @staticmethod
    def _mapping():
        """500 entries of four forms, the documented ceiling, spelt like
        names (any first letter): forms sharing a prefix let the regex
        engine skip most positions and would measure nothing."""
        import random
        rng = random.Random(1302)
        letters = "abcdefghijklmnopqrstuvwxyz"
        names = set()
        while len(names) < 4 * P.MAX_ENTRIES:
            names.add(rng.choice(letters).upper() + "".join(
                rng.choice(letters) for _ in range(rng.randint(5, 8))))
        names = sorted(names)
        rng.shuffle(names)
        return [{"original": names[4 * i], "pseudonym": f"Pp{i:03d}",
                 "variants": names[4 * i + 1:4 * i + 4]}
                for i in range(P.MAX_ENTRIES)]

    def test_the_question_has_a_budget_and_the_rest_are_not_checked(
            self, project, monkeypatch):
        import time
        mapping = self._mapping()
        clean = ("the interview was long and we talked about the day at "
                 "work and at home. " * 140)[:10_000]
        con = sqlite3.connect(str(project / "data.qda"))
        con.executemany(
            "INSERT INTO source (id,name,fulltext,mediapath,memo,owner,date)"
            " VALUES (?,?,?,NULL,'','TestCoder','d')",
            [(fid, f"clean_{fid:03d}.txt", clean)
             for fid in range(10, 110)])
        con.commit()
        con.close()
        _reconnect(project)
        compiled = P.Compiled(P.validate_mapping(mapping))
        assert len(compiled.forms) == 2000
        assert not compiled.carries_a_name(clean)
        counted, checked, inside = [], [], []
        real_count = P.names_left_in_text
        real_check = P.Compiled.carries_a_name
        real_block = QualcoderDatabase._pseudonymise_file_text

        def count(compiled, text, *args, **kwargs):
            counted.append(P.residue_work(compiled, text))
            return real_count(compiled, text, *args, **kwargs)

        def check(self, value):
            if inside:
                checked.append(P.residue_work(self, value))
            return real_check(self, value)

        def block(self, *args, **kwargs):
            inside.append(time.perf_counter())
            try:
                return real_block(self, *args, **kwargs)
            finally:
                inside.append(time.perf_counter() - inside.pop())

        monkeypatch.setattr(P, "names_left_in_text", count)
        monkeypatch.setattr(P.Compiled, "carries_a_name", check)
        monkeypatch.setattr(QualcoderDatabase, "_pseudonymise_file_text",
                            block)
        out = preview_of(mapping=mapping)
        elapsed = inside[-1]
        file_text = _block(out)
        assert file_text["files_not_checked"], file_text["totals"]
        assert file_text["totals"]["files_showing_a_name"] == 0
        assert sum(counted) <= P.MAX_RESIDUE_SCAN_WORK
        assert sum(checked) <= P.MAX_RESIDUE_CHECK_WORK
        unchecked = len(file_text["files_not_checked"])
        assert _file_text_warning(out) == (
            f"Warning: {unchecked} file(s) were not checked; preview them "
            f"one at a time, or use fewer names, to check them (see "
            f"files_not_checked). See residue.file_text, which names the "
            f"files.")
        assert elapsed < self.CEILING_SECONDS, elapsed


class TestTheBudgetsAreDeliberate:
    """The re-verification's N-4: none of the budgets' numbers was
    pinned, so a budget ten times larger passed the suite. Each is pinned
    at the value fix round 2 derived (BRIEF1_FIX2_REPORT.md), and the
    figures the documents quote are tied to the constants."""

    def test_the_values(self):
        assert P.RESIDUE_WORK_PER_CHARACTER == 3
        assert P.RESIDUE_WORK_PER_CHARACTER_NON_ASCII == 7
        # Frozen under ruling 7 from the six CI platforms' worst case
        # (fix round 2's 200 million, 60 million and 150,000 scaled by
        # 0.45: about 2 s on Windows 3.10, the slowest).
        assert P.MAX_RESIDUE_SCAN_WORK == 90_000_000
        assert P.MAX_RESIDUE_CHECK_WORK == 27_000_000
        assert P.MAX_RESIDUE_SCAN_MATCHES == 67_500
        assert P.RESIDUE_FIRST_SIGHT_MATCHES == 5
        assert P.MAX_RESIDUE_ENTRY_ROWS == 50

    def test_the_changelog_quotes_the_constants(self):
        text = " ".join((Path(__file__).resolve().parents[1] /
                         "CHANGELOG.md").read_text(encoding="utf-8").split())
        entry = text.split("## [0.12")[0]
        assert (f"A row lists at most {P.MAX_RESIDUE_ENTRY_ROWS} entries"
                in entry)
        assert (f"for up to {P.MAX_RESIDUE_COMPACT_ROWS:,} files" in entry)
        assert (f"full detail for up to {P.MAX_RESIDUE_FILE_ROWS} files"
                in entry)

    def test_the_text_class_prices_the_work(self):
        compiled = P.Compiled(P.validate_mapping(MAPPING))
        forms = len(compiled.forms)
        assert P.residue_work(compiled, "Thomas said") == 11 * (forms + 3)
        assert P.residue_work(compiled, "Thomas’s") == 8 * (forms + 7)


# =============================================================================
# FIX ROUND 3 (BRIEF1_FIX3_MANDATE.md, H1): A LARGE FILE UNDER A LARGE MAPPING
# =============================================================================

def _documents_prose(size):
    """`size` characters of the repository's own documents, curly-quoted,
    with the fixture's names taken out: the second re-verification's
    corpus for its B2-2 shapes."""
    base = ""
    for name in ("README.md", "PRIVACY.md", "CHANGELOG.md", "INSTALL.md"):
        base += (Path(__file__).resolve().parents[1] / name).read_text(
            encoding="utf-8")
    base = base.encode("ascii", "ignore").decode("ascii")
    for name in ("Thomas", "Mary", "Ann", "Ali", "Tom"):
        base = base.replace(name, "Robert")
    base = base[:200_000].replace("'", "’").replace('"', "“")
    return (base * (size // len(base) + 1))[:size]


def _name_mapping(forms, seed=1303):
    """`forms` name-like forms, four to an entry, no shared prefix (the
    developer's and the lanes' shape)."""
    import random
    rng = random.Random(seed)
    letters = "abcdefghijklmnopqrstuvwxyz"
    pool = set()
    while len(pool) < forms:
        pool.add(rng.choice(letters).upper() + "".join(
            rng.choice(letters) for _ in range(rng.randint(5, 8))))
    pool = sorted(pool)
    return [{"original": pool[i], "pseudonym": f"Pp{i // 4:03d}",
             "variants": pool[i + 1:i + 4]} for i in range(0, forms, 4)]


class TestALargeFileUnderALargeMapping:
    """The second re-verification's B2-2 and CORR2-1, the lead's rulings
    of fix rounds 3 and 4 and the four rules of fix round 5, at the REAL
    budgets. Since ruling 7's freeze a 90,000-character interview under
    1,000 forms is past the work budget on its own, and so past the
    question's. The file this call names is read first from the shared
    budgets, with no allowance of its own: such a file is too large to
    count or to check, "too large to count in full with this many
    names"; another such file is not checked either and never closes a
    tier for the files after it."""

    MAPPING = _name_mapping(1000)

    def test_the_shape_is_past_the_budget_on_its_own(self):
        compiled = P.Compiled(P.validate_mapping(self.MAPPING,
                                                 "insensitive"))
        assert len(compiled.forms) == 1000
        assert P.residue_work(compiled, _documents_prose(90_000)) > \
            P.MAX_RESIDUE_SCAN_WORK
        assert not compiled.carries_a_name(_documents_prose(90_000))

    @pytest.mark.parametrize("left", [True, False],
                             ids=["a-name-left", "clean"])
    def test_the_named_interview_is_said_too_large_and_bounded(
            self, project, left):
        """The lane's `named1000_left`: the interview carries one whole
        word of a form (rewritten) and the same form inside a longer word
        (left), and the call names it. Ruling 1 as amended (fix round 4):
        it is read first, from the same budgets; its own work is past the
        work budget and past the question's, so it is too large to count
        or to check with this many names, never reported clean, and the
        other files are counted from the budgets it left untouched."""
        form = self.MAPPING[0]["original"]
        body = _documents_prose(90_000 - 40)
        if left:
            body += f" {form} said {form}son. "
        _set_text(project, 100, body, name="interview_100.txt")
        out = preview_of(mapping=self.MAPPING, case_mode="insensitive",
                         file_id=100, residue_detail="project")
        block = _block(out)
        row = _rows(out)[100]
        assert block["files_too_large_for_this_mapping"] == [100]
        assert block["files_not_checked"] == [100]
        assert block["files_not_counted"] == []
        assert block["files_counted"] >= 2          # the others: counted
        assert block["totals"]["named_file_too_large"] is True
        assert block["totals"]["named_file_shows_a_name"] is None
        assert row["checked"] is False and "shows_a_name" not in row
        assert row["too_large_for_this_mapping"] is True
        assert row["rewritten_by_this_run"] is left
        assert _file_text_warning(out).startswith(
            "Warning: the file this call rewrites is too large to count in "
            "full with this many names, or to check whether a name would "
            "still show in it after this run. The rewrite still applies to "
            "it, and fewer names would let it be counted (see "
            "files_too_large_for_this_mapping).")
        assert "preview them one at a time" not in _file_text_warning(out)
        assert "1 other file(s)" not in _file_text_warning(out)
        note = block["files_too_large_note"]
        assert note.startswith("1 file(s) are too large to count in full "
                               "with this many names")
        _house_rules([note], ["files_too_large_note"])
        assert "files_not_counted_note" not in block
        assert "files_not_checked_note" not in block

    def test_the_named_file_is_asked_when_its_question_fits(
            self, project, monkeypatch):
        """Too large to count but small enough to ask (a small work budget
        and the question's budget as it is): the named file is asked,
        charged to the question's shared budget, and the warning says
        what the question found."""
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK",
                            _work_of(_after(project)) - 1)
        out = preview_of(residue_detail="project")
        block = _block(out)
        assert block["files_too_large_for_this_mapping"][:1] == [1]
        assert block["files_not_counted"][:1] == [1]
        assert block["totals"]["named_file_shows_a_name"] is False
        assert _file_text_warning(out).startswith(
            "Warning: the file this call rewrites is too large to count in "
            "full with this many names, and it was asked whether a name "
            "would still show in it after this run: none does.")

    def test_the_named_files_question_is_charged_to_the_shared_budget(
            self, project, monkeypatch):
        """Ruling 1 as amended: the named file's question is charged to
        the question's shared budget, so a file after it that would fit
        the budget alone is not asked once the named file has spent it."""
        _set_text(project, 1, "Thomas and Thomas and Thomas left.")
        after = _after(project)
        _set_text(project, 4, "THOMAS in four.")
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK", 1)
        monkeypatch.setattr(P, "MAX_RESIDUE_CHECK_WORK",
                            _work_of(after) + _work_of("THOMAS in four.") - 1)
        assert _work_of("THOMAS in four.") <= P.MAX_RESIDUE_CHECK_WORK
        block = _block(preview_of(residue_detail="project"))
        assert block["files_not_counted"] == [1]
        assert 4 in block["files_not_checked"]

    def test_a_large_file_never_closes_a_tier_for_the_files_after_it(
            self, project):
        """The lane's `blank1000`: file 1 is named; file 100, one clean
        interview of 90,000 characters, is too large for either budget on
        its own; file 101 after it is a note of 5,000 characters that
        carries a name. Before fix round 3 file 100 closed both tiers and
        file 101's name went unreported with both budgets almost unspent;
        now file 100 is not checked and said too large, and file 101 is
        counted and its name reported."""
        form = self.MAPPING[0]["original"]
        _set_text(project, 100, _documents_prose(90_000),
                  name="interview_100.txt")
        _set_text(project, 101, _documents_prose(5_000 - 40)
                  + f" {form} said so. ", name="note_101.txt")
        out = preview_of(mapping=self.MAPPING, case_mode="insensitive",
                         residue_detail="project")
        block = _block(out)
        assert block["files_too_large_for_this_mapping"] == [100]
        assert block["files_not_checked"] == [100]
        assert block["files_not_counted"] == []
        rows = _rows(out)
        assert rows[100]["checked"] is False
        assert rows[100]["too_large_for_this_mapping"] is True
        assert rows[101]["counted"] is True
        assert rows[101]["occurrences"] == {"wide": 1, "whole_word": 1}
        assert block["totals"]["files_showing_a_name"] == 1
        assert block["totals"]["named_file_too_large"] is False
        warning = _file_text_warning(out)
        assert warning.startswith(
            "Warning: after this run, 1 occurrence(s) of these names would "
            "still be in the text of 1 file(s)")
        # Fix round 5 (CORR3-1): the clause "and previewing one on its own
        # checks it" was false for this file (its question passes the
        # question's budget alone, named or not) and is gone.
        assert ("1 file(s) are too large to count in full with this many "
                "names (1 were not checked); fewer names would let them be "
                "counted (see files_too_large_for_this_mapping).") in warning
        assert "preview them one at a time" not in warning
        assert "on its own" not in warning

    def test_a_file_too_large_for_the_work_budget_leaves_it_open(
            self, project, monkeypatch):
        """Ruling 2 on the count's tier, at a small budget: a file larger
        than the work budget on its own is not counted and the smaller
        files after it still are; one that fits on its own but not after
        the others closes it, as before."""
        _set_text(project, 1, "Thomas left.")
        big = "THOMAS in four, with a good many more words here."
        _set_text(project, 4, big)
        _set_text(project, 5, "THOMAS in five.", name="five.txt")
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK",
                            _work_of(_after(project))
                            + _work_of("extracted page text")
                            + _work_of("THOMAS in five."))
        assert _work_of(big) > P.MAX_RESIDUE_SCAN_WORK
        block = _block(preview_of(residue_detail="project"))
        assert block["files_too_large_for_this_mapping"] == [4]
        assert block["files_counted"] == 3                  # 1, 2 and 5
        assert block["files_not_counted"] == [4]            # asked
        assert block["totals"]["files_showing_a_name"] == 2


class TestTheQuestionsWorkIsCarriedToTheNextFile:
    """The second re-verification's PN-3 (P6): the work of the whole
    detector's questions in one file is charged to the preview's work, so
    it reduces what the next file may spend. File 4 spends its own work
    and the work of one question; the budget is the three shared files'
    work and that question's, less one unit: file 5 is past it."""

    MAPPING = [{"original": "Rene", "pseudonym": "Alex"}]

    def test_the_next_file_is_past_by_one_unit(self, project, monkeypatch):
        nfd = "Later René̖ arrived."
        _set_text(project, 1, "Rene met.")
        _set_text(project, 4, nfd)
        _set_text(project, 5, "Rene came back.", name="five.txt")
        compiled = P.Compiled(P.validate_mapping(self.MAPPING))
        extra = P.names_left_in_text(compiled, nfd, True, False)[
            "extra_work"]
        assert extra > 0
        shared = sum(_work_of(text, self.MAPPING) for text in (
            "Alex met.", "extracted page text", nfd, "Rene came back."))
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK", shared + extra - 1)
        block = _block(preview_of(mapping=self.MAPPING,
                                  residue_detail="project"))
        assert block["files_not_counted"] == [5]
        assert block["files_too_large_for_this_mapping"] == []
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK", shared + extra)
        block = _block(preview_of(mapping=self.MAPPING,
                                  residue_detail="project"))
        assert block["files_not_counted"] == []


class TestEveryWordingThroughTheTool:
    """Fix round 5, K1: every wording the file-text warning can produce,
    each reached through the tool by a shape of its own and pinned
    verbatim (the report lists them). The fixture: file 1 is the file
    the call names, file 2 a PDF source, file 4 a text file; the rest are
    added per shape. A padded mapping (33 forms) and a work budget each
    file fits under one name make every file too large for this mapping
    and none for any; the question's budget is set per shape."""

    END = " See residue.file_text, which names the files."
    NAMED = ("the file this call rewrites is too large to count in full "
             "with this many names, {}. The rewrite still applies to it, "
             "and fewer names would let it be counted (see "
             "files_too_large_for_this_mapping).")
    SHOWS = "and a name would still show in it after this run"
    NONE_DOES = ("and it was asked whether a name would still show in it "
                 "after this run: none does")
    UNCHECKED = ("or to check whether a name would still show in it after "
                 "this run")
    OTHERS = ("{} other file(s) are too large to count in full with this "
              "many names{}; fewer names would let them be counted (see "
              "files_too_large_for_this_mapping).")
    PDF = (", because the files before them spent this preview's budget; "
           "a PDF source cannot be named for a preview, so only a preview "
           "with fewer names, which costs less for every file, could reach "
           "them (see {}).")
    NOT_CHECKED = ("{} file(s) were not checked; preview them one at a "
                   "time, or use fewer names, to check them (see "
                   "files_not_checked).")
    PDF_LARGE = ("{} PDF source(s) are too large to count in full with "
                 "this many names{}; a PDF source cannot be named for a "
                 "preview, so only a preview with fewer names, which costs "
                 "less for every file, could reach them (see "
                 "files_too_large_for_this_mapping).")
    NAMED_BEYOND = ("the file this call rewrites is too large to count in a "
                    "preview with any mapping, even of one name, {}. The "
                    "rewrite still applies to it (see "
                    "files_too_large_for_any_mapping).")
    BEYOND = ("{} other file(s) are too large to count in a preview with "
              "any mapping, even of one name{}; no preview can count them "
              "(see files_too_large_for_any_mapping).")

    def test_the_named_file_shows_a_name_and_is_not_an_other(
            self, project, monkeypatch):
        """Shape D of the third correctness lane (X7, X8): every file too
        large for this mapping (not for one name) and every file asked.
        After the run a name shows inside a longer word in the named file:
        it is said to, and it is not counted again among the other files
        that show one."""
        _set_text(project, 1, "Thomas said Thomasson.")
        _set_text(project, 4, "THOMAS in four.")
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK",
                            _room_for_one_name(project))
        out = preview_of(mapping=PADDED, residue_detail="project")
        block = _block(out)
        assert block["files_too_large_for_this_mapping"] == [1, 2, 4]
        assert block["files_too_large_for_any_mapping"] == []
        assert block["files_not_counted"] == [1, 2, 4]
        totals = block["totals"]
        assert totals["named_file_shows_a_name"] is True
        assert totals["files_too_large_showing_a_name"] == 2
        assert totals["pdf_sources_too_large_for_this_mapping"] == 1
        assert _rows(out)[1]["shows_a_name"] is True
        # R4-1: the note hedges fewer names for the PDF source among them.
        assert block["files_too_large_note"].endswith(
            "Fewer names (the mapping split in two) would let the 2 text "
            "file(s) among them be counted; the 1 PDF source(s) among them "
            "cannot be named for a preview, so only a preview with fewer "
            "names, which costs less for every file, could reach them.")
        assert _file_text_warning(out) == (
            "Warning: " + self.NAMED.format(self.SHOWS) + " "
            + self.OTHERS.format(
                1, " (1 of them would still show one of these names)")
            + " " + self.PDF_LARGE.format(1, "") + self.END)

    def test_the_named_file_asked_clean_and_the_others_clean(
            self, project, monkeypatch):
        """The named file asked, none shows; the other files too large,
        asked and clean: the sentence says so with no detail."""
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK",
                            _room_for_one_name(project))
        out = preview_of(mapping=PADDED, residue_detail="project")
        assert _block(out)["totals"]["named_file_shows_a_name"] is False
        assert _file_text_warning(out) == (
            "Warning: " + self.NAMED.format(self.NONE_DOES) + " "
            + self.OTHERS.format(1, "") + " " + self.PDF_LARGE.format(1, "")
            + self.END)

    def test_the_named_file_not_checked_is_not_an_other(
            self, project, monkeypatch):
        """X1: every file too large for both budgets. The named file is
        not checked, and it is not counted again among the other files
        not checked."""
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK",
                            _room_for_one_name(project))
        monkeypatch.setattr(P, "MAX_RESIDUE_CHECK_WORK", 1)
        out = preview_of(mapping=PADDED, residue_detail="project")
        block = _block(out)
        assert block["files_not_checked"] == [1, 2, 4]
        assert block["totals"]["named_file_shows_a_name"] is None
        assert block["totals"]["files_too_large_not_checked"] == 3
        assert _file_text_warning(out) == (
            "Warning: " + self.NAMED.format(self.UNCHECKED) + " "
            + self.OTHERS.format(1, " (1 were not checked)") + " "
            + self.PDF_LARGE.format(1, " (1 were not checked)") + self.END)

    def test_the_others_both_ways_beside_the_named_file(
            self, project, monkeypatch):
        """The other too-large files' two sub-counts at once: file 4 is
        asked and shows a name, file 5 is past the question's budget and
        is not checked; file 2 is asked, clean."""
        _set_text(project, 4, "THOMAS in four.")
        _set_text(project, 5, "a clean note.", name="five.txt")
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK",
                            _room_for_one_name(project))
        # Room to ask files 1, 2 and 4 and none after them: file 5 is past
        # the question's budget.
        monkeypatch.setattr(P, "MAX_RESIDUE_CHECK_WORK", sum(
            _work_of(text, PADDED) for text in (
                _after(project, 1, PADDED), "extracted page text",
                "THOMAS in four.")))
        out = preview_of(mapping=PADDED, residue_detail="project")
        block = _block(out)
        assert block["files_not_counted"] == [1, 2, 4]
        assert block["files_not_checked"] == [5]
        assert _file_text_warning(out) == (
            "Warning: " + self.NAMED.format(self.NONE_DOES) + " "
            + self.OTHERS.format(
                2, " (1 of them would still show one of these names and 1 "
                "were not checked)") + " " + self.PDF_LARGE.format(1, "")
            + self.END)

    def test_the_named_file_after_the_counted_sentence(
            self, project, monkeypatch):
        """The named file too large while the smaller files after it are
        counted: its sentence follows the counted one, "The file"."""
        _set_text(project, 4, "THOMAS in four.")
        budget = P.residue_work_at_one_form(_after(project))
        assert _work_of(_after(project)) > budget >= _work_of(
            "extracted page text") + _work_of("THOMAS in four.")
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK", budget)
        out = preview_of(residue_detail="project")
        block = _block(out)
        assert block["files_too_large_for_this_mapping"] == [1]
        assert block["files_counted"] == 2
        warning = _file_text_warning(out)
        assert warning.startswith(
            "Warning: after this run, 1 occurrence(s) of these names would "
            "still be in the text of 1 file(s), because")
        assert warning.endswith(
            " The" + self.NAMED[3:].format(self.NONE_DOES) + self.END)

    def test_a_count_in_part_after_the_counted_sentence(
            self, project, monkeypatch):
        """The match budget spent across files (TestTheMatchBudgetAcross
        Files' shape): file 5 counted, file 6 counted in part, file 7 past
        the budget; each sentence after the counted one says "further"."""
        _set_text(project, 5, "Thomas " * 10, name="five.txt")
        _set_text(project, 6, "Thomas " * 10, name="six.txt")
        _set_text(project, 7, "Thomas once.", name="seven.txt")
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_MATCHES", 40)
        warning = _file_text_warning(preview_of())
        assert warning.startswith(
            "Warning: after this run, 10 occurrence(s) of these names would "
            "still be in the text of 1 file(s), because")
        assert warning.endswith(
            " 1 further file(s) would still show at least 9 occurrence(s) "
            "of these names in their text, found before counting them "
            "stopped at this preview's budget (see files_counted_in_part). "
            "1 further file(s) would still show one of these names in their "
            "text, and were not counted in full because the files before "
            "them spent this preview's budget; preview each on its own to "
            "count more of it (see files_not_counted)." + self.END)

    def _pdf(self, project, monkeypatch, check=None):
        """File 2, the PDF source, carries a name; the budget is the
        named file's alone, so every other file is past it."""
        _set_text(project, 2, "Thomas in the paper.")
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK",
                            _work_of(_after(project)))
        if check is not None:
            monkeypatch.setattr(P, "MAX_RESIDUE_CHECK_WORK", check)

    def test_a_pdf_source_past_the_budget_showing_a_name(
            self, project, monkeypatch):
        """CORR3-4 and the lead's rule 4: a PDF source is never told to
        be previewed on its own; it has a sentence and a note of its own,
        and file 4, a text file past the budget and clean, has the note
        with that remedy."""
        self._pdf(project, monkeypatch)
        out = preview_of(residue_detail="project")
        block = _block(out)
        assert block["files_not_counted"] == [2, 4]
        totals = block["totals"]
        assert totals["pdf_sources_past_the_budget_showing_a_name"] == 1
        assert totals["pdf_sources_past_the_budget_not_checked"] == 0
        assert _file_text_warning(out) == (
            "Warning: 1 PDF source(s) would still show one of these names "
            "in their text" + self.PDF.format("files_not_counted")
            + self.END)
        note = block["pdf_sources_not_counted_note"]
        assert note == (
            "1 PDF source(s) were not counted in full, because the files "
            "read before them had spent this preview's budget. Each is "
            "listed in files_not_counted (asked whether any name shows) or "
            "in files_not_checked, and none is reported clean. A PDF source "
            "cannot be named for a preview (this tool rewrites text sources "
            "only), so it cannot be previewed on its own; only a preview "
            "with fewer names, which costs less for every file, can reach "
            "further.")
        assert block["files_not_counted_note"].startswith(
            "1 file(s) were not counted in full")
        _house_rules([note], ["pdf_sources_not_counted_note"])

    def test_a_pdf_source_past_the_budget_not_checked(
            self, project, monkeypatch):
        """A question's budget of nothing: every question is too large for
        it on its own. The PDF source's sentence, then the lead's."""
        self._pdf(project, monkeypatch, check=0)
        out = preview_of(residue_detail="project")
        block = _block(out)
        assert block["files_not_checked"] == [2, 4]
        assert _file_text_warning(out) == (
            "Warning: 1 PDF source(s) were not checked"
            + self.PDF.format("files_not_checked") + " "
            + self.NOT_CHECKED.format(1) + self.END)
        assert block["files_not_checked_note"].startswith(
            "1 file(s) were not checked at all")

    def test_two_pdf_sources_one_showing_one_not_checked(
            self, project, monkeypatch):
        """The PDF source's sentence with both of its counts: file 2 asked
        and showing a name, the question's budget then spent, so file 4
        and file 8, a second PDF source, are not checked."""
        _set_text(project, 8, "another extracted page", name="other.pdf",
                  mediapath="/docs/other.pdf")
        self._pdf(project, monkeypatch,
                  check=_work_of("Thomas in the paper."))
        out = preview_of(residue_detail="project")
        block = _block(out)
        assert block["files_not_counted"] == [2]
        assert block["files_not_checked"] == [4, 8]
        assert _file_text_warning(out) == (
            "Warning: 1 PDF source(s) would still show one of these names "
            "in their text and 1 were not checked" + self.PDF.format(
                "files_not_counted and files_not_checked")
            + " " + self.NOT_CHECKED.format(1) + self.END)
        assert block["pdf_sources_not_counted_note"].startswith(
            "2 PDF source(s) were not counted in full")

    def test_every_sentence_at_once_in_order(self, project, monkeypatch):
        """Shape G of the third correctness lane, under fix round 5's
        rules: counted (2, 4), counted in part (5), the named file too
        large and not checked (1), past the budget and showing (6), too
        large and not checked (7), past both budgets (8), a PDF source
        past both (9), and too large for any mapping (10). Every sentence
        once, in order, the numbers agreeing with the lists."""
        _set_text(project, 1, "Thomas said so. " + "and then more. " * 30)
        _set_text(project, 4, "THOMAS in four.")
        _set_text(project, 5, "Thomas " * 40, name="five.txt")
        _set_text(project, 6, "Thomasin in six.", name="six.txt")
        _set_text(project, 7, "nothing much. " * 25, name="seven.txt")
        _set_text(project, 8, "a clean note, longer than the paper.",
                  name="eight.txt")
        _set_text(project, 9, "Thomas in the paper.", name="nine.pdf",
                  mediapath="/docs/nine.pdf")
        _set_text(project, 10, "quiet words. " * 50, name="ten.txt")
        work = _work_of("extracted page text") + _work_of(
            "THOMAS in four.") + _work_of("Thomas " * 40)
        check = _work_of("Thomasin in six.") + _work_of(
            "Thomas in the paper.")
        for big in (_after(project), "nothing much. " * 25):
            assert _work_of(big) > max(work, check)
            assert P.residue_work_at_one_form(big) <= work
        assert P.residue_work_at_one_form("quiet words. " * 50) > work
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK", work)
        monkeypatch.setattr(P, "MAX_RESIDUE_CHECK_WORK", check)
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_MATCHES", 30)
        out = preview_of(residue_detail="project")
        block = _block(out)
        assert block["files_counted"] == 2
        assert block["files_counted_in_part"] == [5]
        assert block["files_not_counted"] == [6]
        assert block["files_not_checked"] == [1, 7, 8, 9, 10]
        assert block["files_too_large_for_this_mapping"] == [1, 7]
        assert block["files_too_large_for_any_mapping"] == [10]
        assert _rows(out)[10]["too_large_for_any_mapping"] is True
        at_least = block["totals"]["occurrences_at_least"]
        assert at_least == _rows(out)[5]["occurrences_at_least"] >= 1
        warning = _file_text_warning(out)
        assert warning.startswith(
            "Warning: after this run, 1 occurrence(s) of these names would "
            "still be in the text of 1 file(s), because")
        assert warning.count("Warning") == 1
        assert warning.endswith(
            f" 1 further file(s) would still show at least {at_least} "
            f"occurrence(s) of these names in their text, found before "
            f"counting them stopped at this preview's budget (see "
            f"files_counted_in_part). The"
            + self.NAMED[3:].format(self.UNCHECKED)
            + " 1 further file(s) would still show one of these names in "
            "their text, and were not counted in full because the files "
            "before them spent this preview's budget; preview each on its "
            "own to count more of it (see files_not_counted). 1 PDF source(s) "
            "were not checked" + self.PDF.format("files_not_checked") + " "
            + self.OTHERS.format(1, " (1 were not checked)") + " "
            + self.BEYOND.format(1, " (1 were not checked)") + " "
            + self.NOT_CHECKED.format(1) + self.END)
        for key in ("files_counted_in_part_note", "files_not_counted_note",
                    "files_not_checked_note", "pdf_sources_not_counted_note",
                    "files_too_large_for_any_mapping_note"):
            assert block[key].startswith("1 ")
        assert block["files_too_large_note"].startswith("2 file(s) are too")
        assert "on its own checks" not in json.dumps(block)


class TestTheWarningsGrammar:
    """Fix round 5, K1 (after the third correctness lane's sweep, its
    section 3.5), widened by the coordinator's follow-on:
    `_pseudonymise_file_text_warning` on every consistent combination of
    the totals it reads, each count 0 or 1, and the named file absent or
    too large (for this mapping, or for any) in each of its three states:
    57,344 warnings. Each is well formed, says every cause exactly when
    its count is non-zero, and says the right number, "further" and
    "other" where they belong. "Fewer names" is never offered for a file
    too large for any mapping; the remedy for a file past the budget is
    "each on its own to count more of it", never a full count, and never
    for a PDF; "on its own checks" is never said."""

    @staticmethod
    def _combinations():
        import itertools
        named_states = ["absent"] + [(kind, shows) for kind in ("this", "any")
                                     for shows in (True, False, None)]
        for (wide, whole, in_part, past_show, past_unch, pdf_show, pdf_unch,
             o_show, o_unch, o_clean, b_show, b_unch, b_clean,
             named) in itertools.product(*[range(2)] * 13, named_states):
            large = named != "absent"
            kind, shows = named if large else (None, None)
            here, beyond = kind == "this", kind == "any"
            large_showing = o_show + (here and shows is True)
            large_unchecked = o_unch + (here and shows is None)
            b_showing = b_show + (beyond and shows is True)
            b_unchecked = b_unch + (beyond and shows is None)
            totals = {
                "occurrences": {"wide": wide, "whole_word": whole},
                "by_reason": {"whole_word_in_a_file_not_rewritten": wide},
                "files_showing_a_name": int(bool(wide or whole)) + in_part
                + past_show + pdf_show + large_showing + b_showing,
                "files_showing_a_name_not_counted":
                    past_show + pdf_show + large_showing + b_showing,
                "files_counted_in_part": in_part,
                "occurrences_at_least": 7 * in_part,
                "files_not_checked": past_unch + pdf_unch + large_unchecked
                + b_unchecked,
                "files_too_large_for_this_mapping":
                    o_show + o_unch + o_clean + here,
                "files_too_large_showing_a_name": large_showing,
                "files_too_large_not_checked": large_unchecked,
                "files_too_large_for_any_mapping":
                    b_show + b_unch + b_clean + beyond,
                "files_too_large_for_any_mapping_showing_a_name": b_showing,
                "files_too_large_for_any_mapping_not_checked": b_unchecked,
                "pdf_sources_past_the_budget_showing_a_name": pdf_show,
                "pdf_sources_past_the_budget_not_checked": pdf_unch,
                "named_file_too_large": large,
                "named_file_too_large_for_any_mapping": beyond,
                "files_whole_word_above_wide": 0}
            if large:
                totals["named_file_shows_a_name"] = shows
            yield (dict(wide=wide, whole=whole, in_part=in_part,
                        past_show=past_show, past_unch=past_unch,
                        pdf_show=pdf_show, pdf_unch=pdf_unch, o_show=o_show,
                        o_unch=o_unch, others=o_show + o_unch + o_clean,
                        b_show=b_show, b_unch=b_unch,
                        b_others=b_show + b_unch + b_clean, large=large,
                        here=here, beyond=beyond, shows=shows), totals)

    @staticmethod
    def _detail(w, head, closing):
        """The count and the parenthesised detail of one too-large
        sentence, or None when it is not said."""
        import re
        d = re.search(r"(\d+) (other )?file\(s\) are too large to count "
                      + head + r"( \(([^)]*)\))?; " + closing, w)
        return d and (int(d.group(1)), bool(d.group(2)), d.group(4) or "")

    def test_every_combination(self):
        import re
        seen = 0
        for c, totals in self._combinations():
            seen += 1
            w = server._pseudonymise_file_text_warning({"totals": totals})
            anything = any(c[k] for k in (
                "wide", "whole", "in_part", "past_show", "past_unch",
                "pdf_show", "pdf_unch", "others", "b_others", "large"))
            assert (w is not None) == anything, c
            if w is None:
                continue
            assert w.startswith("Warning") and w.count("Warning") == 1, w
            assert not re.search(r"(^|[.(;] |\(| )0 ", w), w
            assert "  " not in w and " ." not in w and ".." not in w, w
            assert "on its own checks" not in w, w
            assert "one at a time to count" not in w, w
            said = {
                "whole": "own whole-word rule would still match" in w,
                "in_part": "1 {}file(s) would still show at least 7".format(
                    "further " if c["wide"] or c["whole"] else "") in w,
                "past_show": "1 {}file(s) would still show one of these "
                "names in their text, and were not counted in full because "
                "the files before them spent this preview's budget; preview "
                "each on its own to count more of it (see "
                "files_not_counted).".format(
                    "further " if c["wide"] or c["whole"] or c["in_part"]
                    else "") in w,
                "pdf": "PDF source(s)" in w,
                "past_unch": "1 file(s) were not checked; preview" in w,
                "named_here": "file this call rewrites is too large to count "
                "in full with this many names" in w,
                "named_beyond": "file this call rewrites is too large to "
                "count in a preview with any mapping, even of one name" in w,
            }
            assert said["whole"] == bool(c["whole"] and not c["wide"]), w
            assert said["in_part"] == bool(c["in_part"]), w
            assert said["past_show"] == bool(c["past_show"]), w
            assert said["pdf"] == bool(c["pdf_show"] or c["pdf_unch"]), w
            assert said["past_unch"] == bool(c["past_unch"]), w
            assert said["named_here"] == c["here"], w
            assert said["named_beyond"] == c["beyond"], w
            if c["beyond"]:
                named = w[w.index("file this call rewrites"):].split(").")[0]
                assert "fewer names" not in named, w
            if said["pdf"]:
                pdf = w[w.index("PDF source(s)") - 2:].split(").")[0]
                assert ("would still show" in pdf) == bool(c["pdf_show"]), w
                assert ("were not checked" in pdf) == bool(c["pdf_unch"]), w
                assert "on its own" not in pdf, w
            for key, head, closing, show, unch in (
                    ("others", "in full with this many names",
                     "fewer names would let them be counted", "o_show",
                     "o_unch"),
                    ("b_others", "in a preview with any mapping, even of one "
                     "name", "no preview can count them", "b_show",
                     "b_unch")):
                found = self._detail(w, head, closing)
                assert bool(found) == bool(c[key]), (key, w)
                if found:
                    count, other, detail = found
                    assert count == c[key], w
                    assert other == c["large"], w
                    assert ("1 of them would still show" in detail) == bool(
                        c[show]), w
                    assert ("1 were not checked" in detail) == bool(
                        c[unch]), w
                    assert not re.search(r"[2-9] (of them|were)", detail), w
        assert seen == 57344

    def test_the_too_large_pdf_sources_beside_the_others(self):
        """Fix round 6, R4-1: the PDF sources too large for this mapping
        have a sentence of their own, with the hedged remedy, and are not
        counted among the other files that fewer names would let be
        counted; every consistent combination of the two families' counts
        and the named file's states, 896 warnings."""
        import itertools
        import re
        seen = 0
        named_states = ["absent"] + [(kind, shows) for kind in ("this", "any")
                                     for shows in (True, False, None)]
        for (wide, o_show, o_unch, o_clean, p_show, p_unch, p_clean,
             named) in itertools.product(*[range(2)] * 7, named_states):
            seen += 1
            large = named != "absent"
            kind, shows = named if large else (None, None)
            here = kind == "this"
            pdfs = p_show + p_unch + p_clean
            totals = {
                "occurrences": {"wide": wide, "whole_word": 0},
                "by_reason": {"whole_word_in_a_file_not_rewritten": wide},
                "files_too_large_for_this_mapping":
                    o_show + o_unch + o_clean + pdfs + here,
                "files_too_large_showing_a_name":
                    o_show + p_show + (here and shows is True),
                "files_too_large_not_checked":
                    o_unch + p_unch + (here and shows is None),
                "pdf_sources_too_large_for_this_mapping": pdfs,
                "pdf_sources_too_large_showing_a_name": p_show,
                "pdf_sources_too_large_not_checked": p_unch,
                "files_too_large_for_any_mapping": kind == "any",
                "files_too_large_for_any_mapping_showing_a_name":
                    kind == "any" and shows is True,
                "files_too_large_for_any_mapping_not_checked":
                    kind == "any" and shows is None,
                "named_file_too_large": large,
                "named_file_too_large_for_any_mapping": kind == "any",
                "files_showing_a_name": 0, "files_not_checked": 0,
                "files_whole_word_above_wide": 0}
            if large:
                totals["named_file_shows_a_name"] = shows
            w = server._pseudonymise_file_text_warning({"totals": totals})
            others = o_show + o_unch + o_clean
            if not (wide or others or pdfs or large):
                assert w is None
                continue
            assert w.startswith("Warning") and w.count("Warning") == 1, w
            texts = self._detail(w, "in full with this many names",
                                 "fewer names would let them be counted")
            assert bool(texts) == bool(others), w
            if texts:
                assert texts[0] == others and texts[1] == large, w
                assert ("1 of them would still show" in texts[2]) == bool(
                    o_show), w
                assert ("1 were not checked" in texts[2]) == bool(o_unch), w
            p = re.search(r"(\d+) PDF source\(s\) are too large to count in "
                          r"full with this many names( \(([^)]*)\))?; a PDF "
                          r"source cannot be named for a preview, so only a "
                          r"preview with fewer names, which costs less for "
                          r"every file, could reach them \(see "
                          r"files_too_large_for_this_mapping\)\.", w)
            assert bool(p) == bool(pdfs), w
            if p:
                assert int(p.group(1)) == pdfs, w
                detail = p.group(3) or ""
                assert ("1 of them would still show" in detail) == bool(
                    p_show), w
                assert ("1 were not checked" in detail) == bool(p_unch), w
        assert seen == 896

    def test_the_whole_word_sentence_no_known_text_reaches(self):
        """The whole-word-only sentence (fix round 2's CORR-2 keeps it so
        the warning never goes quiet), verbatim, on the totals alone."""
        totals = {"occurrences": {"wide": 0, "whole_word": 2},
                  "files_showing_a_name": 1}
        assert server._pseudonymise_file_text_warning(
            {"totals": totals}) == (
            "Warning: after this run, this run's own whole-word rule would "
            "still match 2 occurrence(s) of these names in the text of 1 "
            "file(s), which the wide reading did not count. See "
            "residue.file_text, which names the files.")

class TestTheBoundsLanesPins:
    """The third bounds lane's candidate pins (its probes/test_zz_b3_pins.py),
    taken in by fix round 5 where they still apply after the lead's four
    rules: FC1 and TC3 as they were; FC3, FC4 and TC2 re-shaped, since a
    count that stops part-way is now counted in part (it has found a name)
    rather than past the budget or too large; TC1 is
    TestTheMatchBudgetAcrossFiles, re-shaped the same way."""

    def test_the_named_file_claims_first_whatever_its_id(self, project):
        """FC1, FC2. At the real budgets, 100 forms: file 100 is about 75
        million units, file 101 about 20 million, each fitting alone and
        not together. Naming file 101, a higher id, counts it first; file
        100 is then past the work budget (not too large: it fits alone)
        and too large for the question on its own, so it is not checked
        and closes nothing."""
        mapping = _name_mapping(100)
        form = mapping[0]["original"]
        per = 100 + P.RESIDUE_WORK_PER_CHARACTER_NON_ASCII
        _set_text(project, 100, _documents_prose(75_000_000 // per - 40)
                  + f" {form}son said. ", name="big_100.txt")
        _set_text(project, 101, _documents_prose(20_000_000 // per - 40)
                  + f" {form}son said. ", name="mid_101.txt")
        out = preview_of(mapping=mapping, case_mode="insensitive",
                         file_id=101, residue_detail="project")
        block = _block(out)
        assert block["files"][0]["file_id"] == 101          # read first
        assert _rows(out)[101]["counted"] is True
        assert block["files_not_checked"] == [100]
        assert block["files_too_large_for_this_mapping"] == []

    def test_a_count_stopped_on_its_questions_work_closes_the_tier(
            self, project, monkeypatch):
        """TC2, re-shaped. PN-3's shape: file 4's count asks the whole
        detector once. A work budget of every file's work before it, its
        own, and that question's less one: file 4's count stops on the
        work, is charged all it spent, the question too, and counted in
        part (a name shows); the budget has run out, so file 5, small
        enough for what would be left were the question not charged, is
        past it."""
        mapping = [{"original": "Rene", "pseudonym": "Alex"}]
        nfd = "Later Rene\u0301\u0316 arrived."
        _set_text(project, 1, "Rene met.")
        _set_text(project, 4, nfd)
        _set_text(project, 5, "Rene came.", name="five.txt")
        compiled = P.Compiled(P.validate_mapping(mapping))
        extra = P.names_left_in_text(compiled, nfd, True, False)["extra_work"]
        assert 0 < _work_of("Rene came.", mapping) <= extra - 1
        shared = sum(_work_of(text, mapping) for text in (
            "Alex met.", "extracted page text"))
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK",
                            shared + _work_of(nfd, mapping) + extra - 1)
        out = preview_of(mapping=mapping, residue_detail="project")
        block = _block(out)
        assert block["files_counted_in_part"] == [4]
        assert block["files_not_counted"] == [5]
        assert block["files_too_large_for_this_mapping"] == []
        assert _rows(out)[4]["occurrences_at_least"] == 1

    def test_the_count_tier_is_sticky(self, project, monkeypatch):
        """TC3. File 4 fits the work budget alone but not after files 1
        and 2; file 5 after it would fit what is left, and is not counted:
        once passed by what the files before spent, the budget stays
        passed."""
        _set_text(project, 1, "Thomas left.")
        big = "THOMAS in four, with a good many more words here."
        _set_text(project, 4, big)
        _set_text(project, 5, "ok.", name="five.txt")
        before = _work_of(_after(project)) + _work_of("extracted page text")
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK",
                            before + _work_of(big) - 1)
        assert _work_of(big) <= P.MAX_RESIDUE_SCAN_WORK
        assert _work_of("ok.") <= _work_of(big) - 1
        block = _block(preview_of(residue_detail="project"))
        assert block["files_not_counted"] == [4, 5]
        assert block["files_too_large_for_this_mapping"] == []

    def test_the_named_files_questions_are_charged_to_the_shared_budget(
            self, project, monkeypatch):
        """FC4, re-shaped. A pseudonym that puts a name back before a
        composing mark makes the named file's count ask the whole detector.
        One unit short of its work and its questions', its count stops on
        the work: it is counted in part, never too large (its estimate
        fits); at exactly that budget it is counted in full."""
        import itertools
        mapping = [{"original": "Rene", "pseudonym": "Jones"},
                   {"original": "Thomas Rene", "pseudonym": "Alex Rene"}]
        marks = ["\u0300", "\u0302", "\u0308"]
        text = "".join(f"Thomas Rene\u0301{''.join(c)} said. "
                       for c in itertools.product(marks, repeat=2))
        _set_text(project, 1, text)
        compiled = P.Compiled(P.validate_mapping(mapping))
        after = P.apply_replacements(text, P.find_replacements(compiled, text))
        found = P.names_left_in_text(compiled, after, True, True)
        assert found["extra_work"] > 0
        whole = P.residue_work(compiled, after) + found["extra_work"]
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK", whole - 1)
        block = _block(preview_of(mapping=mapping, residue_detail="project"))
        assert block["files_counted_in_part"][:1] == [1]
        assert 1 not in block["files_too_large_for_this_mapping"]
        assert block["totals"]["named_file_too_large"] is False
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK", whole)
        out = preview_of(mapping=mapping, residue_detail="project")
        assert _rows(out)[1]["counted"] is True
        assert _block(out)["files_counted_in_part"] == []
        # And its questions' work is charged to what the others share:
        # with room for file 2 less one unit after it, file 2 is past the
        # budget, where it would fit were the questions not charged.
        pdf = _work_of("extracted page text", mapping)
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK", whole + pdf - 1)
        assert pdf >= 1            # so the named file's questions still fit
        out = preview_of(mapping=mapping, residue_detail="project")
        assert _rows(out)[1]["counted"] is True
        assert _block(out)["files_not_counted"][:1] == [2]

    def test_the_named_files_matches_are_charged_to_the_shared_budget(
            self, project, monkeypatch):
        """FC3, re-shaped. The named file leaves names inside longer words,
        so its count spends matches; file 5 spends 28 on its own. A match
        budget of the named file's matches and 27: file 5 fits it alone,
        not after the named file, so its count stops part-way."""
        text = "Thomas said Thomasson, Thomasson and Thomasson."
        _set_text(project, 1, text)
        _set_text(project, 5, "Thomas " * 10, name="five.txt")
        compiled = P.Compiled(P.validate_mapping(MAPPING))
        after = P.apply_replacements(text, P.find_replacements(compiled, text))
        named = P.names_left_in_text(compiled, after, True, True)["matches"]
        assert named > 0
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_MATCHES", named + 27)
        block = _block(preview_of(residue_detail="project"))
        assert block["files_counted_in_part"] == [5]
        assert block["files_not_counted"] == []
        assert block["files_too_large_for_this_mapping"] == []


class TestAFileTooLargeForAnyMapping:
    """The coordinator's follow-on to fix round 5, from FD5-7: a text too
    large even for a mapping of one name (more than 11,250,000 characters
    that are not ASCII, 22,500,000 that are, at the frozen budget) is not
    told "fewer names": it has a list, a note and wordings of its own,
    "too large to count in a preview with any mapping", and the rewrite
    still applies to the file this call rewrites."""

    NAMED = TestEveryWordingThroughTheTool.NAMED_BEYOND
    END = TestEveryWordingThroughTheTool.END

    def test_the_threshold_at_the_real_budgets(self, project):
        """Two further files either side of the threshold, the fixture's
        mapping (3 forms): 11,250,000 characters fit one name exactly and
        are too large for this mapping; one more character is too large
        for any. Neither is checked (the question's budget is smaller)."""
        at = _documents_prose(11_250_000)
        assert not at.isascii()
        assert P.residue_work_at_one_form(at) == P.MAX_RESIDUE_SCAN_WORK
        _set_text(project, 100, at, name="at_100.txt")
        _set_text(project, 101, at + "x", name="past_101.txt")
        del at
        out = preview_of(residue_detail="project")
        block = _block(out)
        assert block["files_too_large_for_this_mapping"] == [100]
        assert block["files_too_large_for_any_mapping"] == [101]
        assert block["files_not_checked"] == [100, 101]
        rows = _rows(out)
        assert rows[101]["too_large_for_any_mapping"] is True
        assert "too_large_for_this_mapping" not in rows[101]
        assert rows[100]["too_large_for_this_mapping"] is True
        totals = block["totals"]
        assert totals["files_too_large_for_any_mapping"] == 1
        assert totals["files_too_large_for_any_mapping_not_checked"] == 1
        assert totals["named_file_too_large_for_any_mapping"] is False
        assert _file_text_warning(out) == (
            "Warning: 1 file(s) are too large to count in full with this "
            "many names (1 were not checked); fewer names would let them be "
            "counted (see files_too_large_for_this_mapping). 1 file(s) are "
            "too large to count in a preview with any mapping, even of one "
            "name (1 were not checked); no preview can count them (see "
            "files_too_large_for_any_mapping)." + self.END)
        note = block["files_too_large_for_any_mapping_note"]
        assert note == (
            "1 file(s) are too large to count in a preview with any "
            "mapping: even with one short name, each one's estimated cost, "
            "the characters the count reads times that one name and a "
            "little more for every character, is more than this preview's "
            "whole budget "
            "(90,000,000 units), so fewer names would not let it be "
            "counted. Each is listed in files_too_large_for_any_mapping and "
            "none is reported clean: each is asked whether any name shows "
            "only when that question fits its own budget (27,000,000 "
            "units), and is otherwise listed in files_not_checked. The "
            "rewrite applies to the file this call rewrites either way.")
        _house_rules([note], ["files_too_large_for_any_mapping_note"])
        assert block["files_too_large_note"].startswith("1 file(s) are too")
        # Neither is past the budget, so neither note of that cause is
        # given, with its remedy of previewing one at a time.
        assert "files_not_checked_note" not in block
        assert "files_not_counted_note" not in block

    @pytest.mark.parametrize("text,check,shown", [
        ("Thomas said Thomasson, and a good deal more was said after "
         "that.", None, TestEveryWordingThroughTheTool.SHOWS),
        (None, None, TestEveryWordingThroughTheTool.NONE_DOES),
        (None, 1, TestEveryWordingThroughTheTool.UNCHECKED),
    ], ids=["shows", "none-does", "not-checked"])
    def test_the_file_this_call_rewrites(self, project, monkeypatch, text,
                                         check, shown):
        """A work budget one unit short of the named file's work under one
        name: it is too large for any mapping, never told "fewer names",
        and the rewrite still applies; files 2 and 4 are counted."""
        if text is not None:
            _set_text(project, 1, text)
        after = _after(project)
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK",
                            P.residue_work_at_one_form(after) - 1)
        assert _work_of("extracted page text") + _work_of(
            "nothing to see here") <= P.MAX_RESIDUE_SCAN_WORK
        if check is not None:
            monkeypatch.setattr(P, "MAX_RESIDUE_CHECK_WORK", check)
        out = preview_of(residue_detail="project")
        block = _block(out)
        assert block["files_too_large_for_any_mapping"] == [1]
        assert block["files_too_large_for_this_mapping"] == []
        assert block["files_counted"] == 2
        assert block["totals"]["named_file_too_large_for_any_mapping"] is True
        row = _rows(out)[1]
        assert row["too_large_for_any_mapping"] is True
        assert row["rewritten_by_this_run"] is True
        warning = _file_text_warning(out)
        assert warning == "Warning: " + self.NAMED.format(shown) + self.END
        assert "fewer names" not in warning

    def test_a_text_nfkc_lengthens_is_priced_at_its_reading(self, project):
        """Fix round 6, the fourth re-verification's B4-1, at the real
        budgets under the fixture's mapping (3 forms): 600,000 of U+FDFA
        read as 10,800,000 characters, too large for this mapping and not
        for one name; 700,000 read as 12,600,000, too large for any.
        Priced at their stored length both would be counted, at a cost
        the budgets were never sized for."""
        _set_text(project, 100, "ﷺ" * 600_000, name="ligature_100.txt")
        _set_text(project, 101, "ﷺ" * 700_000, name="ligature_101.txt")
        block = _block(preview_of(residue_detail="project"))
        assert block["files_too_large_for_this_mapping"] == [100]
        assert block["files_too_large_for_any_mapping"] == [101]
        assert block["files_not_checked"] == [100, 101]
        assert block["files_counted"] == 3                  # 1, 2 and 4


class TestLongFormsArePricedByTheirLength:
    """Fix round 6, the fourth re-verification's B4-2, through the tool
    at the real budgets: ten entries of the bounds lane's long forms (four
    of 200 characters each, sharing 197) price a character at 800 units,
    so a run of 150,000 a's is too large for this mapping and not
    counted. Priced one unit a form it was 43 units a character, counted,
    at about 133 ns a unit where the budgets were sized on about 7."""

    def test_a_run_along_the_shared_prefix_is_too_large(self, project):
        from test_v012_pseudonymise_engine import \
            TestTheFormsArePricedByTheirLength as Engine
        mapping = Engine._long_mapping(10)
        compiled = P.Compiled(P.validate_mapping(mapping, "insensitive"))
        assert compiled.form_units == 9 * 4 * 20 + 3 * 20 + 1
        _set_text(project, 100, "a" * 150_000, name="run_100.txt")
        assert P.residue_work(compiled, "a" * 150_000) > \
            P.MAX_RESIDUE_SCAN_WORK
        block = _block(preview_of(mapping=mapping, case_mode="insensitive",
                                  residue_detail="project"))
        assert block["files_too_large_for_this_mapping"] == [100]
        assert block["files_not_checked"] == [100]


class TestTheOverlapCheckIsCapped:
    """Fix round 6, the fourth re-verification's B4-3, through the tool:
    past `MAX_OVERLAP_CANDIDATES` forms examined the overlap diagnostic
    stops and says so, in the file's row and a warning, the preview and
    the execute call cap it at the same place (it is signed), and the
    rewrite is the same."""

    MAPPING = [{"original": "Ann Marie", "pseudonym": "Sam"},
               {"original": "Marie Curie", "pseudonym": "Pat"}]
    TEXT = "Ann Marie Curie spoke. Ann Marie Curie spoke."

    def _preview(self, project):
        _set_text(project, 1, self.TEXT)
        return preview_of(mapping=self.MAPPING)

    def test_capped_it_says_so_and_the_run_is_the_same(self, project,
                                                        monkeypatch):
        whole = self._preview(project)
        monkeypatch.setattr(P, "MAX_OVERLAP_CANDIDATES", 20)
        capped = self._preview(project)
        row = capped["preview"]["files"][0]
        assert row["overlap_conflicts_capped"] is True
        assert len(row["overlap_conflicts"]) == 1
        assert whole["preview"]["files"][0]["overlap_conflicts_capped"] \
            is False
        assert len(whole["preview"]["files"][0]["overlap_conflicts"]) == 2
        warning = [w for w in capped["warnings"]
                   if "stopped early" in w]
        assert warning == [
            "Warning: the check for mapping entries competing for the "
            "same characters stopped early in file(s) 1 (see "
            "overlap_conflicts_capped): it examines every form that could "
            "start near each match, and stops after 20, so a conflict past "
            "that point is not listed. The rewrite itself is not affected."]
        assert not [w for w in whole["warnings"] if "stopped early" in w]
        assert capped["preview"]["files"][0]["replacements"] == \
            whole["preview"]["files"][0]["replacements"]
        result = execute_from(capped, mapping=self.MAPPING)
        assert result.get("success") is True, result
        assert query(project, "SELECT fulltext FROM source WHERE id=1")[0][
            "fulltext"] == "Sam Curie spoke. Sam Curie spoke."


class TestAPdfSourceTooLargeForThisMapping:
    """Fix round 6, the fourth re-verification's R4-1 and R4-2 (the rules
    lane's Q4): a PDF source too large for this mapping cannot be named,
    and is read after the file a call names and the files before it, so
    it is never promised that fewer names would let it be counted; it has
    the PDF sentence's hedged remedy, and it is not in the past-the-budget
    PDF note, since nothing before it spent the budget."""

    def test_a_pdf_too_large_is_not_in_the_past_the_budget_pdf_note(
            self, project, monkeypatch):
        """Q4, the rules lane's candidate pin as it wrote it, with R4-1's
        note and warning beside it."""
        pdf_text = "the cat sat on the mat, Thomas. " * 20
        monkeypatch.setattr(P, "MAX_RESIDUE_SCAN_WORK",
                            P.residue_work_at_one_form(pdf_text))
        monkeypatch.setattr(P, "MAX_RESIDUE_CHECK_WORK", 1)
        _set_text(project, 2, pdf_text)                   # the fixture's PDF
        out = preview_of(residue_detail="project")
        block = _block(out)
        assert block["files_too_large_for_this_mapping"] == [2]
        assert "files_too_large_note" in block
        assert "pdf_sources_not_counted_note" not in block
        assert block["files_too_large_note"].endswith(
            "The rewrite applies to the file this call rewrites either way. "
            "A PDF source cannot be named for a preview, so only a preview "
            "with fewer names, which costs less for every file, could reach "
            "them.")
        warning = _file_text_warning(out)
        assert warning == (
            "Warning: " + TestEveryWordingThroughTheTool.PDF_LARGE.format(
                1, " (1 were not checked)")
            + TestEveryWordingThroughTheTool.END)
        assert "fewer names would let" not in warning

