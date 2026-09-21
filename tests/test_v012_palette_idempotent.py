"""v0.12 Batch A, items A1 and A2 (dossier D5, owner ruling X2).

Palette colour snapping with QualCoder's exact color_matcher arithmetic,
idempotent creates (created: false, reason: already_exists), the
case-insensitive duplicate rule (Unicode casefold plus NFC), no-op moves,
renames and recolours (changed: false, reason: unchanged), and per-coding
idempotency in apply_codings.

Parity pins vendor literal copies of the upstream palette and matcher at
QualCoder master 9bddf17 (src/qualcoder/color_selector.py:52-65 and
:144-162; byte-identical at the 3.8.2 tag, :53-66 and :145-163) so a
drift in either direction fails here.

Windows-safe: no paths beyond the tmp fixtures, no wall-clock waits,
non-ASCII names travel through SQLite as text only.
"""

import json
import sqlite3
import random
import time
import unicodedata
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))  # sibling test helpers

import qualcoder_mcp.server as server
from qualcoder_mcp.database import (QualcoderDatabase, QUALCODER_COLORS,
                                    QUALCODER_LOCK_FILENAME, snap_to_palette,
                                    normalize_name, name_key, validate_qda_path)
from test_v17_support import make_project, add_subcode  # noqa: E402
from test_qc40_visibility import _apply_visibility_schema  # noqa: E402


# ---------------------------------------------------------------------------
# Vendored upstream ground truth (color_selector.py at 9bddf17)
# ---------------------------------------------------------------------------

UPSTREAM_COLORS = [
    "#F5F6CE", "#F2F5A9", "#F4FA58", "#F7FE2E", "#DDE600", "#F8ECE0", "#F6E3CE", "#F5D0A9", "#F7BE81", "#FAAC58",
    "#F5ECCE", "#F3E2A9", "#F5DA81", "#F7D358", "#FACC2E", "#FFE2CC", "#FFC599", "#FFA866", "#FF8B33", "#FF6F00",
    "#F8E6E0", "#F6D8CE", "#F5BCA9", "#F79F81", "#FA8258", "#FADCCC", "#F5B999", "#F09666", "#EB7333", "#E65100",
    "#F8E0E0", "#F6CECE", "#F5A9A9", "#F78181", "#FA5858", "#F0D1D1", "#E2A4A4", "#D37676", "#C54949", "#B71C1C",
    "#F2D6CE", "#E5AE9D", "#D8866D", "#CB5E3C", "#BF360C", "#E7CEDB", "#CF9EB8", "#B76E95", "#9F3E72", "#880E4F",
    "#F8E0E6", "#F6CED8", "#F5A9BC", "#F7819F", "#FA5882", "#F8E0F7", "#F6CEF5", "#F5A9F2", "#F781F3", "#FA58F4",
    "#D1DED2", "#A3BEA5", "#769E78", "#487E4B", "#1B5E20", "#DEE9E4", "#BED3C9", "#9EBDAE", "#7EA793", "#5E9179",
    "#CEF6E3", "#A9F5D0", "#81F7BE", "#58FAAC", "#00FF7F", "#E0F8E0", "#CEF6CE", "#A9F5A9", "#81F781", "#58FA58",
    "#D0F5A9", "#BEF781", "#ACFA58", "#9AFE2E", "#80FF00", "#CEF6F5", "#A9F5F2", "#81F7F3", "#58FAF4", "#00F0F0",
    "#E4D3F5", "#CAA8EB", "#B07CE1", "#9651D7", "#7D26CD", "#ECE0F8", "#E3CEF6", "#D0A9F5", "#BE81F7", "#AC58FA",
    "#DADAF5", "#B5B5EC", "#9090E3", "#6B6BDA", "#4646D1", "#CEE3F6", "#A9D0F5", "#81BEF7", "#3498DB", "#5882FA",
    "#CEDAEC", "#9EB5D9", "#6D91C6", "#3D6CB3", "#0D47A1", "#E8E8E8", "#D8D8D8", "#C8C8C8", "#B8B8B8", "#A8A8A8"
    ]


def upstream_color_matcher(hex_color):
    """Literal copy of color_selector.color_matcher at 9bddf17 (:144-162)."""
    if len(hex_color) != 7:
        return "#D8D8D8"  # light gray
    test_r = int(hex_color[1:3], 16)
    test_g = int(hex_color[3:5], 16)
    test_b = int(hex_color[5:7], 16)

    best_match = ["#D8D8D8", 255.0]  # light gray default, colour difference
    for c in UPSTREAM_COLORS:
        r = int(c[1:3], 16)
        g = int(c[3:5], 16)
        b = int(c[5:7], 16)
        diff = (abs(r - test_r) + abs(g - test_g) + abs(b - test_b)) / 3
        if diff < best_match[1]:
            best_match = [c, diff]
    return best_match[0]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _con(project_path):
    con = sqlite3.connect(str(Path(project_path) / "data.qda"))
    con.row_factory = sqlite3.Row
    return con


def _row(project_path, sql, args=()):
    con = _con(project_path)
    try:
        return con.execute(sql, args).fetchone()
    finally:
        con.close()


def _rows(project_path, sql, args=()):
    con = _con(project_path)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def _exec(project_path, sql, args=()):
    con = _con(project_path)
    try:
        con.execute(sql, args)
        con.commit()
    finally:
        con.close()


def _reload():
    server.switch_project(server.current_project_path)


def _backups(project_path):
    project = Path(project_path)
    return sorted(project.parent.glob(f"{project.stem}_backup_*"))


def _lock(project_path):
    return validate_qda_path(project_path).parent / QUALCODER_LOCK_FILENAME


def _count(project_path, table):
    return _row(project_path, f"SELECT COUNT(*) AS n FROM {table}")["n"]


def _sid():
    return json.loads(server.analyze_for_coding([1]))["coding_session_id"]


def _record(sid, items):
    out = json.loads(server.record_suggestions(sid, items))
    assert out["recorded_count"] == len(items), out
    return [r["guid"] for r in out["recorded"]]


STRESS = {"file_id": 1, "code_name": "Stress",
          "segment_text": "I feel stressed about deadlines",
          "reasoning": "explicit", "confidence": 0.9}
COPING = {"file_id": 1, "code_name": "Coping",
          "segment_text": "I cope by exercising",
          "reasoning": "explicit", "confidence": 0.9}


# ===========================================================================
# A1: palette and matcher parity
# ===========================================================================

class TestPaletteParity:

    def test_palette_identical_to_pinned_upstream(self):
        assert QUALCODER_COLORS == UPSTREAM_COLORS
        assert len(QUALCODER_COLORS) == 120
        assert len(set(QUALCODER_COLORS)) == 120
        for c in QUALCODER_COLORS:
            assert c == c.upper() and c.startswith("#") and len(c) == 7
        assert QUALCODER_COLORS[115:120] == [
            "#E8E8E8", "#D8D8D8", "#C8C8C8", "#B8B8B8", "#A8A8A8"]

    def test_matcher_parity_on_grid_and_sample(self):
        """Stride-9 RGB grid (24,389 inputs) plus a seeded random sample of
        5,000: snap_to_palette equals upstream color_matcher everywhere."""
        for r in range(0, 256, 9):
            for g in range(0, 256, 9):
                for b in range(0, 256, 9):
                    c = f"#{r:02X}{g:02X}{b:02X}"
                    assert snap_to_palette(c) == upstream_color_matcher(c), c
        rng = random.Random(9_0_1)
        for _ in range(5000):
            c = f"#{rng.randrange(256):02X}{rng.randrange(256):02X}{rng.randrange(256):02X}"
            assert snap_to_palette(c) == upstream_color_matcher(c), c

    def test_palette_members_map_to_themselves_in_upper_case(self):
        for c in QUALCODER_COLORS:
            assert snap_to_palette(c) == c
            assert snap_to_palette(c.lower()) == c

    @pytest.mark.parametrize("given, expected", [
        # dossier D5 section 1.3, computed with the upstream function
        ("#FF0000", "#E65100"), ("#00FF00", "#00FF7F"), ("#0000FF", "#0D47A1"),
        ("#123456", "#0D47A1"), ("#0d47a1", "#0D47A1"), ("#D8D8D8", "#D8D8D8"),
        # tie: equidistant from #880E4F (index 49) and #1B5E20 (index 64);
        # strict less-than in palette order keeps the lower index
        ("#000046", "#880E4F"),
        # achromatic quirk kept as parity: the metric ignores saturation
        ("#FFFFFF", "#F8E0F7"), ("#000000", "#1B5E20"), ("#808080", "#769E78"),
        ("#888888", "#7EA793"), ("#E0E0E0", "#DEE9E4"),
    ])
    def test_named_cases(self, given, expected):
        assert snap_to_palette(given) == expected
        assert upstream_color_matcher(given.upper()) == expected

    @pytest.mark.parametrize("value", ["#FFF", "red", "#12345", "#1234567"])
    def test_the_one_place_the_port_deviates_is_recorded(self, value):
        """Measured against the restored 9bddf17 clone (fix round 4).

        color_matcher opens with `if len(hex_color) != 7: return
        "#D8D8D8"`, so upstream substitutes light grey for every value of
        the wrong length. This port carries no length guard, which is the
        refuse-rather-than-substitute stance D5 section 3.1 chose over
        4.0's silent substitution, so it either raises (when a slice is
        not hex) or snaps a short value as if it were a colour. Recorded
        rather than changed: no call site can reach it, because each one
        validates first, and the review screen's guard (fix round 4, T7)
        is what closed the last path that did not. The docstring used to
        claim this behaviour was "exactly as upstream", and it is not.
        """
        assert upstream_color_matcher(value) == "#D8D8D8"
        try:
            ours = snap_to_palette(value)
        except ValueError:
            return                       # the 'red' and '#FFF' shape
        assert ours != "#D8D8D8"         # the '#12345' shape: a real snap
        assert ours in QUALCODER_COLORS

    def test_a_seven_character_non_hex_value_raises_on_both_sides(self):
        """And where upstream does raise, the port agrees."""
        with pytest.raises(ValueError):
            upstream_color_matcher("#GGHHII")
        with pytest.raises(ValueError):
            snap_to_palette("#GGHHII")

    def test_invalid_colours_still_refused_everywhere(self, setup_server,
                                                       qualcoder_db_path):
        bad = ("#zzzzzz", "#FFF", "red", "FF0000", "#12345G", "")
        for b in bad:
            assert "hex format" in json.loads(
                server.create_code(f"X{b}", color=b, create_backup=False))["error"]
            assert "hex format" in json.loads(
                server.recolor_code(1, b, create_backup=False))["error"]
        sid = _sid()
        out = json.loads(server.propose_codes(
            sid, [{"name": f"P{i}", "color": b} for i, b in enumerate(bad)]))
        assert out["recorded_count"] == 0 and out["rejected_count"] == len(bad)
        guid = json.loads(server.propose_codes(
            sid, [{"name": "Good"}]))["recorded"][0]["guid"]
        for b in bad:
            assert "error" in json.loads(server.update_proposal(sid, guid, color=b))
        assert _backups(qualcoder_db_path) == []


class TestColourSnappingOnWrites:

    def test_create_code_snaps_and_discloses(self, setup_server, qualcoder_db_path):
        out = json.loads(server.create_code("Red-ish", color="#FF0000",
                                            create_backup=False))
        assert out["success"] is True and out["created"] is True
        assert out["code"]["color"] == "#E65100"
        assert out["color_requested"] == "#FF0000" and out["color_snapped"] is True
        assert "nearest QualCoder palette colour to #FF0000" in out["message"]
        assert _row(qualcoder_db_path, "SELECT color FROM code_name WHERE cid=?",
                    (out["code"]["id"],))["color"] == "#E65100"

    def test_create_code_palette_member_not_reported_as_snapped(
            self, setup_server, qualcoder_db_path):
        out = json.loads(server.create_code("Blue", color="#0d47a1",
                                            create_backup=False))
        assert out["code"]["color"] == "#0D47A1"
        assert out["color_snapped"] is False  # same colour, canonical case
        assert "nearest" not in out["message"]
        out = json.loads(server.create_code("Plain", create_backup=False))
        assert "color_requested" not in out and "color_snapped" not in out
        assert out["code"]["color"] in QUALCODER_COLORS

    def test_recolor_unchanged_when_stored_equals_target(self, setup_server,
                                                          qualcoder_db_path):
        _exec(qualcoder_db_path, "UPDATE code_name SET color='#0D47A1' WHERE cid=1")
        _reload()
        before = _backups(qualcoder_db_path)
        row_before = dict(_row(qualcoder_db_path, "SELECT * FROM code_name WHERE cid=1"))
        out = json.loads(server.recolor_code(1, "#0d47a1"))
        assert out == {
            "changed": False, "reason": "unchanged",
            "message": "Code 'Stress' already has colour #0D47A1; nothing was written.",
            "code": {"id": 1, "name": "Stress", "color": "#0D47A1"},
            "color_requested": "#0d47a1", "color_snapped": False,
        }
        assert _backups(qualcoder_db_path) == before
        assert dict(_row(qualcoder_db_path, "SELECT * FROM code_name WHERE cid=1")) == row_before

    def test_recolor_repairs_legacy_lower_case(self, setup_server, qualcoder_db_path):
        _exec(qualcoder_db_path, "UPDATE code_name SET color='#0d47a1' WHERE cid=1")
        _reload()
        out = json.loads(server.recolor_code(1, "#0D47A1", create_backup=False))
        assert out["success"] is True and out["changed"] is True
        assert out["old_color"] == "#0d47a1" and out["new_color"] == "#0D47A1"
        assert out["color_snapped"] is False
        assert _row(qualcoder_db_path, "SELECT color FROM code_name WHERE cid=1")["color"] == "#0D47A1"

    def test_recolor_off_palette_stored_value_is_a_change(self, setup_server,
                                                            qualcoder_db_path):
        # fixture code 1 carries #FF0000, which is not a palette colour
        out = json.loads(server.recolor_code(1, "#FF0000", create_backup=False))
        assert out["changed"] is True and out["new_color"] == "#E65100"
        assert out["color_snapped"] is True and out["color_requested"] == "#FF0000"
        assert "nearest palette colour to #FF0000" in out["message"]
        assert "not a palette colour" in out["message"]

    def test_recolor_unknown_code_costs_no_backup(self, setup_server, qualcoder_db_path):
        out = json.loads(server.recolor_code(999, "#0D47A1"))
        assert out["error"] == "Code ID 999 does not exist"
        assert _backups(qualcoder_db_path) == []

    def test_proposals_snap_at_record_time(self, setup_server, qualcoder_db_path):
        sid = _sid()
        out = json.loads(server.propose_codes(
            sid, [{"name": "Deadline pressure", "color": "#FF0000"},
                  {"name": "Calm", "color": "#a9f5d0"},
                  {"name": "Nocolour"}]))
        rec = {r["name"]: r for r in out["recorded"]}
        assert rec["Deadline pressure"]["color"] == "#E65100"
        assert rec["Deadline pressure"]["color_snapped"] is True
        assert rec["Deadline pressure"]["color_requested"] == "#FF0000"
        assert rec["Calm"]["color"] == "#A9F5D0" and rec["Calm"]["color_snapped"] is False
        assert "color" not in rec["Nocolour"]
        review = server.review_proposals(sid)
        assert "#E65100" in review and "#FF0000" not in review
        # update_proposal likewise
        guid = rec["Nocolour"]["guid"]
        out = json.loads(server.update_proposal(sid, guid, color="#0000FF"))
        assert out["changes"]["color"]["to"] == "#0D47A1"
        assert out["color_snapped"] is True and out["color_requested"] == "#0000FF"
        # create_proposed_codes stores the palette colour
        server.update_proposal_status(sid, approve=[rec["Deadline pressure"]["guid"], guid])
        out = json.loads(server.create_proposed_codes(sid, create_backup=False))
        assert out["success"] is True
        colours = {r["name"]: r["color"] for r in _rows(
            qualcoder_db_path, "SELECT name, color FROM code_name")}
        assert colours["Deadline pressure"] == "#E65100"
        assert colours["Nocolour"] == "#0D47A1"

    def test_the_collision_rule_is_described_with_the_scope_it_has(self):
        """Fix round 4, T8. Fix round 1 (730de9d) widened
        _code_name_collisions from `lower()` to `name_key`, which folds
        whitespace runs, NFC and casefold, so the rule catches whitespace
        and Unicode twins as well as case ones. Two descriptions of it
        were left at the old scope: this tool description, which a model
        reads at plan time and would otherwise not expect "Work  stress"
        to refuse a batch against a codebook holding "Work stress", and
        the runtime collision_note. Same stale-description class as R7,
        R12 and R15."""
        # Flattened: the description is wrapped, and where it wraps
        # differs between the interpreters that strip docstring indent
        # (3.13) and those that do not.
        description = " ".join(server.mcp._tool_manager._tools[
            "create_proposed_codes"].description.split())
        assert "case-variant" not in description
        assert ("once letter case, spacing and Unicode form are ignored"
                in description)

    def test_a_whitespace_variant_really_does_refuse_the_batch(
        self, setup_server, qualcoder_db_path
    ):
        """The description above, exercised: the codebook holds "Stress",
        so a proposal spelled "St ress" does not collide but " Stress "
        does, on spacing alone."""
        sid = _sid()
        out = json.loads(server.propose_codes(
            sid, [{"name": "  Stress  "}]))
        recorded = out["recorded"][0]
        assert recorded["collides_with"] == "Stress"
        assert ("letter case, spacing and Unicode form ignored"
                in out["collision_note"])
        server.update_proposal_status(sid, approve=[recorded["guid"]])
        before = _backups(qualcoder_db_path)
        created = json.loads(server.create_proposed_codes(sid))
        assert "error" in created
        assert created["failures"][0]["reason"].startswith(
            "name collides with existing code 'Stress'")
        assert _backups(qualcoder_db_path) == before

    def test_create_proposed_codes_collision_rule_unchanged(self, setup_server,
                                                             qualcoder_db_path):
        sid = _sid()
        out = json.loads(server.propose_codes(
            sid, [{"name": "stress"}, {"name": "Fresh"}]))
        assert out["recorded"][0]["collides_with"] == "Stress"
        server.update_proposal_status(sid, approve=[r["guid"] for r in out["recorded"]])
        before = _backups(qualcoder_db_path)
        out = json.loads(server.create_proposed_codes(sid))
        assert "error" in out and out["failures"][0]["name"] == "stress"
        assert "nothing was written and no backup was created" in out["error"]
        assert _backups(qualcoder_db_path) == before
        assert "Fresh" not in {r["name"] for r in _rows(
            qualcoder_db_path, "SELECT name FROM code_name")}


# ===========================================================================
# A2: name helpers
# ===========================================================================

class TestNameHelpers:

    def test_normalize_name_collapses_unicode_whitespace(self):
        assert normalize_name("  Work \t stress  now ") == "Work stress now"
        assert normalize_name("   ") == "" and normalize_name(None) == ""

    def test_name_key_folds_case_and_unicode_form(self):
        nfd = unicodedata.normalize("NFD", "Émotion")
        assert name_key("Émotion") == name_key("émotion") == name_key(nfd)
        assert name_key("Coping") == name_key("COPING ") == name_key(" coping")
        assert name_key("Stress") != name_key("Stressed")


# ===========================================================================
# A2: idempotent creates
# ===========================================================================

class TestIdempotentCreates:

    def test_exact_duplicates_cost_nothing(self, setup_server, qualcoder_db_path):
        before = _backups(qualcoder_db_path)
        counts = {t: _count(qualcoder_db_path, t) for t in ("code_name", "code_cat", "cases")}

        out = json.loads(server.create_code("Stress"))
        assert out["created"] is False and out["reason"] == "already_exists"
        assert out["match"] == "exact" and "success" not in out and "error" not in out
        assert out["code"] == {
            "id": 1, "name": "Stress", "category": "Category A", "category_id": 1,
            "color": "#FF0000", "memo": "Stress code", "owner": "TestCoder",
            "date": "2024-01-15"}
        assert "requested" not in out
        assert out["message"] == ("A code named 'Stress' already exists (id 1); "
                                  "nothing was created. Use id 1.")

        out = json.loads(server.create_category("Category A"))
        assert out["created"] is False and out["match"] == "exact"
        assert out["category"] == {
            "id": 1, "name": "Category A", "parent_id": None, "parent_name": None,
            "memo": "", "owner": "TestCoder", "date": "2024-01-15"}

        out = json.loads(server.create_case("Case A"))
        assert out["created"] is False and out["match"] == "exact"
        assert out["case"] == {"id": 1, "name": "Case A", "memo": "First case",
                               "owner": "TestCoder", "date": "2024-01-15"}

        assert _backups(qualcoder_db_path) == before
        assert {t: _count(qualcoder_db_path, t) for t in counts} == counts

    def test_duplicate_echoes_the_colour_argument_not_the_snapped_one(
            self, setup_server, qualcoder_db_path):
        """Fix round 1, F6. `requested` lists the ARGUMENTS that differ from
        the stored row (D5 section 3.3), so the colour echoed there is the
        one the caller gave. The comparison still uses the snapped target,
        because that is what a write would have stored, and the snap is
        disclosed with the same color_requested / color_snapped pair every
        other colour-carrying result in this batch uses."""
        before = _backups(qualcoder_db_path)
        out = json.loads(server.create_code("stress", color="#FF0000"))
        assert out["created"] is False and out["reason"] == "already_exists"
        assert out["code"]["color"] == "#FF0000"        # the stored row
        assert out["requested"] == {"name": "stress", "color": "#FF0000"}
        assert out["color_requested"] == "#FF0000"
        assert out["color_snapped"] is True
        assert "#E65100" in out["message"]              # the nearest palette colour
        # Fix round 2, R13: `requested.color` and `code.color` print the
        # SAME string here, deliberately. The row holds an off-palette
        # colour a GUI created, the argument asks for that same colour,
        # and a write would have stored #E65100, so the argument does
        # differ from what the row would hold; D5 section 3.3 fixes that
        # reading of "differs" on this exact colour pair. The message and
        # color_snapped carry the explanation, and this assertion makes
        # the identical pair a pinned decision rather than an accident.
        assert out["requested"]["color"] == out["code"]["color"] == "#FF0000"
        assert "is not a QualCoder palette colour" in out["message"]
        assert _backups(qualcoder_db_path) == before
        assert _count(qualcoder_db_path, "code_name") == 2

    def test_duplicate_with_a_palette_colour_reports_no_snap(
            self, setup_server, qualcoder_db_path):
        # Stored #0D47A1, asked for the same colour in lower case: nothing
        # differs, so no `requested` colour and no snap.
        _exec(qualcoder_db_path, "UPDATE code_name SET color='#0D47A1' WHERE cid=1")
        _reload()
        out = json.loads(server.create_code("Stress", color="#0d47a1"))
        assert out["created"] is False
        assert out["color_requested"] == "#0d47a1"
        assert out["color_snapped"] is False
        assert "requested" not in out
        assert "nearest" not in out["message"]

    def test_whitespace_only_difference_is_an_exact_match(
            self, setup_server, qualcoder_db_path):
        """Fix round 1, F11. D5 section 3.2 compares after whitespace
        normalisation in BOTH tiers. A GUI-created row with a double space
        used to fall through to tier 2 and be labelled a case difference
        that was not there."""
        _exec(qualcoder_db_path,
              "INSERT INTO code_name (cid, name, memo, catid, owner, date, color) "
              "VALUES (9, 'Work  stress', '', NULL, 'gui_user', '2024-01-15', "
              "'#F5D0A9')")
        _reload()
        out = json.loads(server.create_code("Work stress"))
        assert out["created"] is False
        assert out["match"] == "exact"                  # not case_insensitive
        assert out["code"]["id"] == 9
        assert out["requested"] == {"name": "Work stress"}
        assert _count(qualcoder_db_path, "code_name") == 3

    def test_whitespace_twins_are_an_ambiguity_like_case_twins(
            self, setup_server, qualcoder_db_path):
        """Both tiers now normalise, so a codebook holding both spellings is
        ambiguous in tier 1 exactly as it already was in tier 2. The advice
        the refusal gives is pinned separately (TestAmbiguityGuidance)."""
        for cid, name in ((9, "Work  stress"), (10, "Work stress")):
            _exec(qualcoder_db_path,
                  "INSERT INTO code_name (cid, name, memo, catid, owner, date, color) "
                  "VALUES (?, ?, '', NULL, 'gui_user', '2024-01-15', '#F5D0A9')",
                  (cid, name))
        _reload()
        out = json.loads(server.create_code("work stress"))
        assert "error" in out and "spacing" in out["error"]
        assert sorted(c["id"] for c in out["candidates"]) == [9, 10]
        assert _count(qualcoder_db_path, "code_name") == 4
        assert _backups(qualcoder_db_path) == []

    def test_case_variant_duplicate(self, setup_server, qualcoder_db_path):
        out = json.loads(server.create_code("coping"))
        assert out["created"] is False and out["match"] == "case_insensitive"
        assert out["code"]["name"] == "Coping" and out["code"]["id"] == 2
        assert out["requested"] == {"name": "coping"}
        assert _count(qualcoder_db_path, "code_name") == 2

    def test_unicode_case_and_form(self, setup_server, qualcoder_db_path):
        out = json.loads(server.create_code("Émotion", create_backup=False))
        assert out["created"] is True
        cid = out["code"]["id"]
        for variant in ("émotion", unicodedata.normalize("NFD", "Émotion"),
                        unicodedata.normalize("NFD", "émotion")):
            out = json.loads(server.create_code(variant))
            assert out["created"] is False and out["code"]["id"] == cid, variant
        out = json.loads(server.create_code(unicodedata.normalize("NFD", "Émotion")))
        assert out["match"] == "exact"  # same letters, NFC and NFD agree
        assert _count(qualcoder_db_path, "code_name") == 3

    def test_whitespace_normalisation(self, setup_server, qualcoder_db_path):
        out = json.loads(server.create_code("Work  stress", create_backup=False))
        assert out["created"] is True and out["code"]["name"] == "Work stress"
        for variant in ("Coping ", " Coping", "Cop ing", "Work\tstress",
                        "Work  stress", "  work   STRESS "):
            out = json.loads(server.create_code(variant))
            expect = "Coping" if "op" in variant else "Work stress"
            if variant == "Cop ing":
                assert out["created"] is True, out  # a different name
                continue
            assert out["created"] is False and out["code"]["name"] == expect, variant
        assert _count(qualcoder_db_path, "code_name") == 4
        assert "non-empty" in json.loads(server.create_code("  \t "))["error"]

    def test_category_mismatch_is_disclosed_not_moved(self, setup_server,
                                                       qualcoder_db_path):
        json.loads(server.create_category("Category B", create_backup=False))
        before = _backups(qualcoder_db_path)
        out = json.loads(server.create_code("Stress", category="category b"))
        assert out["created"] is False
        assert out["requested"] == {"category": "category b"}
        assert "move_code_to_category" in out["message"]
        assert "in category 'Category A'" in out["message"]
        assert _row(qualcoder_db_path, "SELECT catid FROM code_name WHERE cid=1")["catid"] == 1
        assert _backups(qualcoder_db_path) == before
        # same category requested: nothing to disclose
        out = json.loads(server.create_code("Stress", category="Category A"))
        assert "requested" not in out

    def test_parent_category_mismatch_on_create_category(self, setup_server,
                                                          qualcoder_db_path):
        json.loads(server.create_category("Category B", create_backup=False))
        out = json.loads(server.create_category("Category A", parent_category="Category B"))
        assert out["created"] is False
        assert out["requested"] == {"parent_category": "Category B"}
        assert "move_category" in out["message"] and "at the top level" in out["message"]
        assert _row(qualcoder_db_path, "SELECT supercatid FROM code_cat WHERE catid=1")["supercatid"] is None

    def test_memo_not_applied_and_private_zone_hidden(self, setup_server,
                                                       qualcoder_db_path):
        _exec(qualcoder_db_path,
              "UPDATE code_name SET memo='public part\n#####\nsecret note' WHERE cid=1")
        _reload()
        out = json.loads(server.create_code("Stress", memo="a new definition"))
        assert out["created"] is False
        assert "Its memo was not changed; use set_memo." in out["message"]
        assert out["code"]["memo"].strip() == "public part"
        assert "secret" not in json.dumps(out)
        assert _row(qualcoder_db_path, "SELECT memo FROM code_name WHERE cid=1")["memo"].endswith("secret note")
        out = json.loads(server.create_category("Category A", memo="x"))
        assert "use set_memo" in out["message"]
        out = json.loads(server.create_case("Case A", memo="x"))
        assert "use set_memo" in out["message"]
        out = json.loads(server.create_case("Case A"))
        assert "set_memo" not in out["message"]

    def test_ambiguity_lists_candidates(self, setup_server, qualcoder_db_path):
        for name in ("Theme", "theme"):  # only the GUI can create these now
            _exec(qualcoder_db_path,
                  "INSERT INTO code_cat (name, memo, owner, date) VALUES (?, '', 'TestCoder', '2024-01-15')",
                  (name,))
        _reload()
        out = json.loads(server.create_category("THEME"))
        assert "error" in out and "matches 2 existing categories" in out["error"]
        assert {c["name"] for c in out["candidates"]} == {"Theme", "theme"}
        out = json.loads(server.create_category("Theme"))
        assert out["created"] is False and out["match"] == "exact"
        assert out["category"]["name"] == "Theme"
        assert _count(qualcoder_db_path, "code_cat") == 3

    def test_race_path_discloses_backup(self, setup_server, qualcoder_db_path,
                                         monkeypatch):
        """The read-only pre-check misses (simulated), the in-transaction
        re-check finds the row: same answer, plus the backup_path that was
        taken (disclosed, not hidden)."""
        real = QualcoderDatabase.list_codes
        calls = {"n": 0}

        def flaky(self_):
            calls["n"] += 1
            return [] if calls["n"] == 1 else real(self_)

        monkeypatch.setattr(QualcoderDatabase, "list_codes", flaky)
        out = json.loads(server.create_code("Stress"))
        assert out["created"] is False and out["reason"] == "already_exists"
        assert "backup_path" in out and calls["n"] >= 2
        assert _count(qualcoder_db_path, "code_name") == 2

    def test_successful_creates_carry_created_true(self, setup_server,
                                                    qualcoder_db_path):
        out = json.loads(server.create_code("New code", create_backup=False))
        assert out["success"] is True and out["created"] is True
        out = json.loads(server.create_category("New cat", create_backup=False))
        assert out["success"] is True and out["created"] is True
        assert out["category"]["name"] == "New cat"
        out = json.loads(server.create_case("New case", create_backup=False))
        assert out["success"] is True and out["created"] is True

    def test_create_category_echo_goes_through_ai_json(self, setup_server,
                                                        qualcoder_db_path):
        _exec(qualcoder_db_path,
              "UPDATE code_cat SET memo='shown\n#####\nprivate' WHERE catid=1")
        _reload()
        out = json.loads(server.create_category("category a"))
        assert out["category"]["memo"].strip() == "shown"
        assert "private" not in json.dumps(out)


# ===========================================================================
# A2 fix round 2 (R6, R15): what an ambiguity refusal tells the caller to do
# ===========================================================================

class TestAmbiguityGuidance:
    """An ambiguity refusal has to describe a remedy that exists.

    Tier 1 of `_find_existing_by_name` normalises the REQUEST as well as
    the stored name (D5 section 3.2, applied on both sides in fix round 1,
    F11), so rows that share one normalised form (whitespace twins,
    NFC/NFD twins) cannot be told apart by any spelling; only case twins
    can. Every one of them used to be told to "use the exact spelling of
    the one you mean", which for the first two is advice that cannot be
    followed and leaves the caller with no way to name the row.

    `_resolve_category_by_name` compares byte for byte in tier 1, so the
    exact spelling does select a row there; its message keeps that advice
    and now names the comparison it actually makes (name_key folds
    spacing and Unicode form, not only letter case).
    """

    def _seed_codes(self, db_path, names, first_cid=9):
        for offset, name in enumerate(names):
            _exec(db_path,
                  "INSERT INTO code_name (cid, name, memo, catid, owner, "
                  "date, color) VALUES (?, ?, '', NULL, 'gui_user', "
                  "'2024-01-15', '#F5D0A9')", (first_cid + offset, name))
        _reload()

    def test_whitespace_twins_are_sent_to_the_ids_not_to_a_spelling(
            self, setup_server, qualcoder_db_path):
        self._seed_codes(qualcoder_db_path, ("Work  stress", "Work stress"))
        out = json.loads(server.create_code("work stress"))
        error = out["error"]
        assert "spacing" in error
        assert "2 of them are one and the same name" in error
        assert "no spelling of the name can single those out" in error
        assert "rename_code and merge_codes take a code id" in error
        assert "use the exact spelling" not in error
        assert sorted(c["id"] for c in out["candidates"]) == [9, 10]

        # Why the old advice could not be followed: every spelling of the
        # pair, including each row's own, answers the same refusal.
        for spelling in ("Work  stress", "Work stress", "WORK  STRESS"):
            again = json.loads(server.create_code(spelling))
            assert again.get("candidates") == out["candidates"], spelling
        assert _count(qualcoder_db_path, "code_name") == 4
        assert _backups(qualcoder_db_path) == []

    def test_unicode_form_twins_get_the_same_answer(
            self, setup_server, qualcoder_db_path):
        # NFC and NFD spellings of one name: legal under the BINARY
        # unique(name), and identical once tier 1 normalises both sides.
        self._seed_codes(qualcoder_db_path, (
            unicodedata.normalize("NFC", "Émotion"),
            unicodedata.normalize("NFD", "Émotion")))
        out = json.loads(server.create_code("Émotion"))
        assert "Unicode form" in out["error"]
        assert "no spelling of the name can single those out" in out["error"]
        assert sorted(c["id"] for c in out["candidates"]) == [9, 10]

    def test_case_twins_keep_the_exact_spelling_advice_because_it_works(
            self, setup_server, qualcoder_db_path):
        self._seed_codes(qualcoder_db_path, ("Theme", "theme"))
        out = json.loads(server.create_code("THEME"))
        assert ("match it once letter case, spacing and Unicode form are "
                "ignored") in out["error"]
        assert ("the exact spelling of the one you mean selects it"
                in out["error"])
        assert "no spelling" not in out["error"]

        # Followed, it resolves: tier 1 finds one row and the create is
        # the ordinary idempotent answer.
        resolved = json.loads(server.create_code("theme"))
        assert resolved["created"] is False and resolved["match"] == "exact"
        assert resolved["code"]["id"] == 10
        assert _backups(qualcoder_db_path) == []

    @pytest.mark.parametrize("seeded, requested, selects", [
        # Differ by letter case AND by a run of whitespace.
        (("Work  Stress", "work stress"), "WORK STRESS", "work stress"),
        # Do not differ by letter case at all: str.casefold maps the
        # eszett to "ss", so name_key folds these two together.
        (("Stra\u00dfe", "Strasse"), "stra\u00dfe", "Stra\u00dfe"),
        # Nor these: casefold maps the fi ligature to "fi".
        (("\ufb01le", "file"), "FILE", "file"),
    ])
    def test_the_refusal_describes_the_match_not_a_difference_they_lack(
            self, setup_server, qualcoder_db_path, seeded, requested, selects):
        """Fix round 4, T3. The twins == 1 branch is reached by every
        group whose normalised forms are distinct, which is wider than
        "case twins": the old wording told three kinds of caller that
        their candidates "differ only by letter case" when two of them
        do not differ by letter case at all.
        """
        self._seed_codes(qualcoder_db_path, seeded)
        out = json.loads(server.create_code(requested))
        assert "differ only by letter case" not in out["error"]
        assert ("match it once letter case, spacing and Unicode form are "
                "ignored") in out["error"]
        assert sorted(c["id"] for c in out["candidates"]) == [9, 10]

        # And the remedy it offers is followable in every one of them:
        # each candidate has its own normalised form, so its own spelling
        # resolves in tier 1.
        resolved = json.loads(server.create_code(selects))
        assert resolved["created"] is False and resolved["match"] == "exact"
        assert resolved["code"]["name"] == selects
        assert _backups(qualcoder_db_path) == []

    def test_case_twin_cases_name_no_tool_this_server_does_not_have(
            self, setup_server, qualcoder_db_path):
        # Cases have no rename or merge tool here, so the hint points at
        # QualCoder rather than inventing one.
        for name in ("Case  one", "Case one"):
            _exec(qualcoder_db_path,
                  "INSERT INTO cases (name, memo, owner, date) "
                  "VALUES (?, '', 'gui_user', '2024-01-15')", (name,))
        _reload()
        out = json.loads(server.create_case("case one"))
        assert "cases are renamed and merged in QualCoder, not here" in out["error"]
        assert "rename_case" not in out["error"]

    def test_category_resolution_names_the_comparison_it_makes(
            self, setup_server, qualcoder_db_path):
        for name in ("Cat  A", "Cat A"):
            _exec(qualcoder_db_path,
                  "INSERT INTO code_cat (name, memo, owner, date) "
                  "VALUES (?, '', 'gui_user', '2024-01-15')", (name,))
        _reload()
        out = json.loads(server.move_code_to_category(1, "cat a"))
        assert "ambiguous" in out["error"]
        # name_key folds spacing and Unicode form as well as letter case,
        # which is what the message used to leave out (R15).
        assert "letter case, spacing and Unicode form are ignored" in out["error"]
        assert "byte for byte" in out["error"]
        assert len(out["candidates"]) == 2
        assert _backups(qualcoder_db_path) == []

        # And here the advice IS followable: tier 1 is byte for byte, so
        # the double-spaced spelling selects the double-spaced row.
        moved = json.loads(server.move_code_to_category(1, "Cat  A",
                                                        create_backup=False))
        assert moved["success"] is True and moved["changed"] is True
        target = [c for c in out["candidates"] if c["name"] == "Cat  A"][0]
        assert moved["new_category_id"] == target["id"]


class TestResolvedCategoryNameIsReported:
    """Fix round 3, S4. _resolve_category_by_name's tier 2 widened in this
    batch from `lower()` to `name_key` (whitespace collapse, NFC,
    casefold), and the write paths that consume it reported the id alone.
    For move_code_to_category the write lands, so nothing in the result
    revealed that the code had been filed under a row spelled differently
    from the name the caller gave. create_code already echoed `category`;
    these three now say the same thing."""

    def _seed(self, db_path, name):
        _exec(db_path,
              "INSERT INTO code_cat (name, memo, owner, date) "
              "VALUES (?, '', 'gui_user', '2024-01-15')", (name,))
        _reload()
        return _row(db_path, "SELECT catid FROM code_cat WHERE name = ?",
                    (name,))["catid"]

    def test_move_code_names_the_category_it_resolved_to(
            self, setup_server, qualcoder_db_path):
        catid = self._seed(qualcoder_db_path, "Wellbeing  Themes")
        # Differs by letter case AND spacing, so tier 2 resolves it.
        out = json.loads(server.move_code_to_category(
            1, "wellbeing themes", create_backup=False))
        assert out["changed"] is True
        assert out["new_category_id"] == catid
        assert out["new_category"] == "Wellbeing  Themes"
        assert "into category 'Wellbeing  Themes'" in out["message"]
        stored = _row(qualcoder_db_path,
                      "SELECT name FROM code_cat WHERE catid = ?",
                      (catid,))["name"]
        assert stored == out["new_category"]

    def test_move_code_out_of_any_category_says_so(
            self, setup_server, qualcoder_db_path):
        self._seed(qualcoder_db_path, "Wellbeing  Themes")
        json.loads(server.move_code_to_category(1, "wellbeing themes",
                                                create_backup=False))
        out = json.loads(server.move_code_to_category(1, None,
                                                      create_backup=False))
        assert out["changed"] is True
        assert out["new_category"] is None
        assert "out of any category" in out["message"]

    def test_move_category_names_its_new_parent(
            self, setup_server, qualcoder_db_path):
        parent = self._seed(qualcoder_db_path, "Parent  Cat")
        child = self._seed(qualcoder_db_path, "Child Cat")
        out = json.loads(server.move_category(child, "parent cat",
                                              create_backup=False))
        assert out["changed"] is True
        assert out["new_supercatid"] == parent
        assert out["new_parent"] == "Parent  Cat"
        assert "under category 'Parent  Cat'" in out["message"]

        back = json.loads(server.move_category(child, None,
                                               create_backup=False))
        assert back["new_parent"] is None
        assert "to the top level" in back["message"]

    def test_create_category_names_the_parent_it_resolved_to(
            self, setup_server, qualcoder_db_path):
        parent = self._seed(qualcoder_db_path, "Parent  Cat")
        out = json.loads(server.create_category(
            "Nested", parent_category="parent cat", create_backup=False))
        assert out["created"] is True
        assert out["category"]["supercatid"] == parent
        assert out["category"]["parent_name"] == "Parent  Cat"
        assert "under 'Parent  Cat'" in out["message"]

        # The already_exists echo beside it has always reported
        # parent_name (D5 section 3.3); the two now agree.
        again = json.loads(server.create_category(
            "nested", parent_category="parent cat", create_backup=False))
        assert again["created"] is False
        assert again["category"]["parent_name"] == "Parent  Cat"

    def test_a_top_level_create_names_no_parent(
            self, setup_server, qualcoder_db_path):
        out = json.loads(server.create_category("Loose", create_backup=False))
        assert out["category"]["parent_name"] is None
        assert "under" not in out["message"]


# ===========================================================================
# A2: no-op moves and renames
# ===========================================================================

class TestRecolourRacePath:
    """Fix round 1, F7. D5 section 3.4 step 3: when the stored colour
    becomes equal to the target between the read-only pre-check and the
    write, the op returns the same `unchanged` answer decorated with
    backup_path. Only the create_code race was pinned."""

    def test_recolour_race_returns_unchanged_with_the_backup_disclosed(
            self, setup_server, qualcoder_db_path, monkeypatch):
        real = QualcoderDatabase.get_code_details
        calls = {"n": 0}

        def flaky(self_, code_id):
            calls["n"] += 1
            details = real(self_, code_id)
            if calls["n"] == 1 and details is not None:
                # The pre-check sees the old colour; by the time the write
                # connection looks, another writer has set the target.
                details = dict(details)
                details["color"] = "#F5D0A9"
            return details

        _exec(qualcoder_db_path, "UPDATE code_name SET color='#0D47A1' WHERE cid=1")
        _reload()
        before = _backups(qualcoder_db_path)
        monkeypatch.setattr(QualcoderDatabase, "get_code_details", flaky)

        out = json.loads(server.recolor_code(1, "#0d47a1"))
        assert out["changed"] is False and out["reason"] == "unchanged"
        assert out["color_requested"] == "#0d47a1"
        assert out["color_snapped"] is False
        assert out["code"] == {"id": 1, "name": "Stress", "color": "#0D47A1"}
        assert "backup_path" in out                 # taken, so disclosed
        assert calls["n"] >= 2
        assert len(_backups(qualcoder_db_path)) == len(before) + 1
        assert _row(qualcoder_db_path,
                    "SELECT color FROM code_name WHERE cid=1")["color"] == "#0D47A1"

    def test_code_deleted_between_the_precheck_and_the_write(
            self, setup_server, qualcoder_db_path, monkeypatch):
        """The other arm of the same re-check: the row is gone by the time
        the write connection looks, so the op raises and _perform_write
        turns that into an error with nothing written."""
        real = QualcoderDatabase.get_code_details
        calls = {"n": 0}

        def vanishing(self_, code_id):
            calls["n"] += 1
            return real(self_, code_id) if calls["n"] == 1 else None

        # Fix round 2, R14: discriminate the raise from a plain
        # `return answer`. That used to be done with the ABSENCE of
        # backup_path from the envelope, which stopped discriminating in
        # v0.13 A5: a failure after the backup now names the backup, so
        # both spellings carry the key. The commit path is watched
        # instead, which is what the distinction is actually about and
        # is stronger than the old proxy: the raise leaves
        # _perform_write's try block BEFORE the pre-commit lock re-check,
        # so that re-check never runs; `return answer` reaches it and
        # commits. Without this assertion both spellings pass.
        reached_the_commit = []
        original_recheck = server._recheck_lock_before_commit

        def watched_recheck(folder, held):
            reached_the_commit.append(True)
            return original_recheck(folder, held)

        monkeypatch.setattr(server, "_recheck_lock_before_commit",
                            watched_recheck)

        before = _backups(qualcoder_db_path)
        monkeypatch.setattr(QualcoderDatabase, "get_code_details", vanishing)
        out = json.loads(server.recolor_code(1, "#00FF7F"))
        assert "error" in out and "does not exist" in out["error"]
        assert calls["n"] >= 2
        assert _row(qualcoder_db_path,
                    "SELECT color FROM code_name WHERE cid=1")["color"] == "#FF0000"
        assert reached_the_commit == [], (
            "the op returned instead of raising: the transaction reached "
            "the commit rather than being rolled back")
        # The backup was taken before the op ran, so it is on disk, and
        # since A5 the envelope says which one it is.
        assert Path(out["backup_path"]) in _backups(qualcoder_db_path)
        assert len(_backups(qualcoder_db_path)) == len(before) + 1


class TestNoOpMovesAndRenames:

    def test_move_code_to_current_category_unchanged(self, setup_server,
                                                      qualcoder_db_path):
        before = _backups(qualcoder_db_path)
        out = json.loads(server.move_code_to_category(1, "Category A"))
        assert out["changed"] is False and out["reason"] == "unchanged"
        assert out["code"]["category_id"] == 1
        assert _backups(qualcoder_db_path) == before
        # a real move still writes and says so
        out = json.loads(server.move_code_to_category(1, None, create_backup=False))
        assert out["success"] is True and out["changed"] is True
        out = json.loads(server.move_code_to_category(1, None))
        assert out["changed"] is False and "uncategorised" in out["message"]

    def test_move_category_under_current_parent_unchanged(self, setup_server,
                                                           qualcoder_db_path):
        b = json.loads(server.create_category("B", parent_category="Category A",
                                              create_backup=False))["category"]["id"]
        before = _backups(qualcoder_db_path)
        out = json.loads(server.move_category(b, "Category A"))
        assert out["changed"] is False and "under 'Category A'" in out["message"]
        out = json.loads(server.move_category(1, None))
        assert out["changed"] is False and "top level" in out["message"]
        assert _backups(qualcoder_db_path) == before
        # the cycle guard still wins on a real move
        out = json.loads(server.move_category(1, "B", create_backup=False))
        assert "cycle" in out["error"]
        out = json.loads(server.move_category(b, None, create_backup=False))
        assert out["changed"] is True and out["new_supercatid"] is None

    def test_rename_code_rules(self, setup_server, qualcoder_db_path):
        before = _backups(qualcoder_db_path)
        out = json.loads(server.rename_code(1, "Stress"))
        assert out["changed"] is False and out["reason"] == "unchanged"
        out = json.loads(server.rename_code(1, " Stress  "))
        assert out["changed"] is False
        out = json.loads(server.rename_code(1, "coping"))
        assert out["error"] == "Another code already uses the name 'Coping' (id 2)."
        assert out["candidates"] == [{"id": 2, "name": "Coping"}]
        assert _backups(qualcoder_db_path) == before
        out = json.loads(server.rename_code(1, "STRESS", create_backup=False))
        assert out["success"] is True and out["changed"] is True
        assert out["new_name"] == "STRESS"
        assert _row(qualcoder_db_path, "SELECT name FROM code_name WHERE cid=1")["name"] == "STRESS"

    def test_rename_category_rules(self, setup_server, qualcoder_db_path):
        other = json.loads(server.create_category("Other", create_backup=False))["category"]["id"]
        before = _backups(qualcoder_db_path)
        out = json.loads(server.rename_category(1, "Category A"))
        assert out["changed"] is False and out["reason"] == "unchanged"
        out = json.loads(server.rename_category(1, "other"))
        assert out["error"] == f"Another category already uses the name 'Other' (id {other})."
        assert _backups(qualcoder_db_path) == before
        out = json.loads(server.rename_category(1, "CATEGORY A", create_backup=False))
        assert out["changed"] is True and out["new_name"] == "CATEGORY A"

    def test_unknown_ids_cost_no_backup(self, setup_server, qualcoder_db_path):
        assert json.loads(server.rename_code(99, "x"))["error"] == "Code ID 99 does not exist"
        assert json.loads(server.move_code_to_category(99, None))["error"] == "Code ID 99 does not exist"
        assert json.loads(server.rename_category(99, "x"))["error"] == "Category ID 99 does not exist"
        assert json.loads(server.move_category(99, None))["error"] == "Category ID 99 does not exist"
        assert _backups(qualcoder_db_path) == []

    def test_out_of_range_ids_answer_in_the_envelope(self, setup_server,
                                                    qualcoder_db_path):
        """Fix round 3, S7: an id too large for SQLite is a refusal.

        recolor_code's pre-check, new in this batch, handed the value
        straight to a parameterised query (get_code_details) because
        validate_id had no upper bound, and sqlite3 raised OverflowError.
        OverflowError is an ArithmeticError, which _tool_guard does not
        catch, so the tool lost its error envelope and the caller got an
        MCP protocol error where every other bad id gets a refusal.
        validate_id now carries SQLite's own bound, so the whole family
        answers the same way, including the three tools this batch never
        touched.
        """
        too_big = 2 ** 63
        bound = 2 ** 63 - 1
        calls = {
            "recolor_code": lambda: server.recolor_code(too_big, "#FF0000"),
            "rename_code": lambda: server.rename_code(too_big, "x"),
            "move_code_to_category":
                lambda: server.move_code_to_category(too_big, None),
            "rename_category": lambda: server.rename_category(too_big, "x"),
            "move_category": lambda: server.move_category(too_big, None),
            "delete_code": lambda: server.delete_code(too_big),
            "get_coded_segments":
                lambda: server.get_coded_segments(too_big),
            "set_memo": lambda: server.set_memo("code", too_big, "x"),
        }
        for name, call in calls.items():
            raw = call()          # a raise here is the defect itself
            assert isinstance(raw, str), name
            out = json.loads(raw)
            assert out["error"].endswith(
                f"must be at most {bound}, got {too_big}"), (name, out)
            # Seven of the eight validate the id before the write gate, so
            # the refusal costs no backup. set_memo is the exception and it
            # is pre-existing, not this batch's: it validates target_type
            # on the read-only connection but leaves target_id to
            # db.set_memo, which runs inside _perform_write, after the
            # backup. Pinned as it is so a change either way is deliberate.
            expected = 1 if name == "set_memo" else 0
            assert len(_backups(qualcoder_db_path)) == expected, name
        # The bound is SQLite's: the largest id it can hold is still a
        # normal "does not exist" answer, not a refusal.
        assert json.loads(server.recolor_code(bound, "#FF0000"))["error"] == \
            f"Code ID {bound} does not exist"

    def test_the_three_tools_the_census_added_answer_in_the_envelope(
        self, setup_server, qualcoder_db_path, tmp_path
    ):
        """Carried from Batch A: the shipped census was two short.

        The entry said twenty-two tools lost their envelope; re-measuring
        with validate_id's bound lifted (the pre-fix state) shows
        link_file_to_case raising through file_id, set_attribute through
        target_id, and export_coded_segments_report through file_ids once
        output_path ends in .csv, which the earlier counts missed. Pinned
        here so the corrected number in CHANGELOG.md rests on assertions
        rather than on a count nobody can re-run.
        """
        too_big = 2 ** 63
        bound = 2 ** 63 - 1
        calls = {
            "link_file_to_case":
                lambda: server.link_file_to_case(too_big, 1),
            "set_attribute":
                lambda: server.set_attribute("file", too_big, "Site", "x"),
            "export_coded_segments_report":
                lambda: server.export_coded_segments_report(
                    str(tmp_path / "segments.csv"), file_ids=[too_big]),
        }
        for name, call in calls.items():
            raw = call()          # a raise here is the defect itself
            assert isinstance(raw, str), name
            out = json.loads(raw)
            assert out["error"].endswith(
                f"must be at most {bound}, got {too_big}"), (name, out)


class TestProposalNameNormalisation:
    """Fix round 1, F10. add_code stores normalize_name, so the proposal
    pipeline has to key its duplicate checks the same way. It used to key
    them on strip().lower(), which let whitespace twins through
    pre-validation and collide on the insert AFTER the backup, breaking
    the docstring promise that every approved proposal is validated before
    the backup and the write (D5 section 3.3)."""

    def test_whitespace_twins_are_refused_at_propose_time(self, setup_server,
                                                          qualcoder_db_path):
        sid = _sid()
        out = json.loads(server.propose_codes(sid, [{"name": "Work  stress"}]))
        assert out["recorded_count"] == 1
        assert out["recorded"][0]["name"] == "Work stress"   # stored collapsed

        out = json.loads(server.propose_codes(sid, [{"name": "Work stress"}]))
        assert out["recorded_count"] == 0
        assert "already exists in this session" in out["rejected"][0]["reason"]

    def test_twins_in_one_batch_never_reach_the_backup(self, setup_server,
                                                       qualcoder_db_path):
        """Both spellings forced onto the session (an old session file could
        hold them), then approved: pre-validation refuses the batch, so no
        backup is taken and no row is written."""
        from qualcoder_mcp.sessions import ProposedCode

        sid = _sid()
        session = server.session_manager.load_session(sid)
        for spelling in ("Work  stress", "Work stress"):
            proposal = ProposedCode(name=spelling)
            proposal.status = "approved"
            session.add_proposal(proposal)
        server.session_manager.save_session(session)

        before = _backups(qualcoder_db_path)
        n_before = _count(qualcoder_db_path, "code_name")
        out = json.loads(server.create_proposed_codes(sid))
        assert "failed validation" in out["error"]
        assert "another approved proposal in this batch has the same name" in \
            out["failures"][0]["reason"]
        assert _backups(qualcoder_db_path) == before
        assert _count(qualcoder_db_path, "code_name") == n_before

    def test_created_echo_matches_the_stored_row(self, setup_server,
                                                 qualcoder_db_path):
        """An old session file can still hold an un-collapsed name; the echo
        has to report the name as stored, not as proposed."""
        from qualcoder_mcp.sessions import ProposedCode

        sid = _sid()
        session = server.session_manager.load_session(sid)
        proposal = ProposedCode(name="Work  stress")
        proposal.name = "Work  stress"          # as an older release recorded it
        proposal.status = "approved"
        session.add_proposal(proposal)
        server.session_manager.save_session(session)

        out = json.loads(server.create_proposed_codes(sid, create_backup=False))
        echo = out["created_codes"][0]
        assert echo["name"] == "Work stress"
        stored = _row(qualcoder_db_path,
                      "SELECT name FROM code_name WHERE cid = ?",
                      (echo["code_id"],))["name"]
        assert stored == echo["name"]

    def test_update_proposal_rename_uses_the_same_key(self, setup_server,
                                                      qualcoder_db_path):
        sid = _sid()
        first = json.loads(server.propose_codes(sid, [{"name": "Work stress"}]))
        second = json.loads(server.propose_codes(sid, [{"name": "Home strain"}]))
        guid = second["recorded"][0]["guid"]
        assert first["recorded_count"] == 1

        out = json.loads(server.update_proposal(sid, guid, name="work  stress"))
        assert "error" in out and "already named" in out["error"]

        out = json.loads(server.update_proposal(sid, guid, name="Home  strain "))
        assert out.get("error") is None
        assert server.session_manager.load_session(sid) \
            .get_proposal_by_guid(guid).name == "Home strain"


class TestProposalColourDisclosure:
    """Fix round 3, S1. create_proposed_codes snapped the approved
    proposal's colour through add_code and then reported no colour at all,
    the one colour-carrying path that never told the researcher what was
    stored (D5 section 3.1 asks for it in every result that stores one).
    """

    def test_fresh_proposal_reports_the_stored_colour(self, setup_server,
                                                      qualcoder_db_path):
        """A v0.12 proposal is snapped when it is made, so the echo repeats
        what review_proposals showed and color_snapped is false."""
        sid = _sid()
        out = json.loads(server.propose_codes(
            sid, [{"name": "Palette proposal", "color": "#FF0000"}]))
        guid = out["recorded"][0]["guid"]
        snapped = snap_to_palette("#FF0000")
        assert snapped != "#FF0000"

        review = server.review_proposals(sid)
        assert f"Colour: {snapped}" in review
        assert "#FF0000" not in review       # the snap happened at propose time

        json.loads(server.update_proposal_status(sid, approve=[guid]))
        created = json.loads(server.create_proposed_codes(
            sid, create_backup=False))["created_codes"][0]
        assert created["color"] == snapped
        assert created["color_requested"] == snapped
        assert created["color_snapped"] is False
        stored = _row(qualcoder_db_path,
                      "SELECT color FROM code_name WHERE cid = ?",
                      (created["code_id"],))["color"]
        assert stored == created["color"]

    def test_legacy_session_colour_is_disclosed_as_snapped(self, setup_server,
                                                           qualcoder_db_path):
        """propose_codes did not snap before v0.12, so a session file
        written by v0.11 (or edited by hand) carries an off-palette colour.
        The researcher approved that colour and a different one was written
        with nothing in the result saying so."""
        from qualcoder_mcp.sessions import ProposedCode

        sid = _sid()
        session = server.session_manager.load_session(sid)
        proposal = ProposedCode(name="Legacy hue")
        proposal.color = "#FF0000"          # as an older release recorded it
        proposal.status = "approved"
        session.add_proposal(proposal)
        server.session_manager.save_session(session)

        snapped = snap_to_palette("#FF0000")
        # The approval screen names the colour the write will store.
        review = server.review_proposals(sid)
        assert f"Colour: {snapped}" in review and "#FF0000" in review

        created = json.loads(server.create_proposed_codes(
            sid, create_backup=False))["created_codes"][0]
        assert created["color"] == snapped
        assert created["color_requested"] == "#FF0000"
        assert created["color_snapped"] is True
        stored = _row(qualcoder_db_path,
                      "SELECT color FROM code_name WHERE cid = ?",
                      (created["code_id"],))["color"]
        assert stored == snapped

    def test_proposal_without_a_colour_still_names_the_one_stored(
        self, setup_server, qualcoder_db_path
    ):
        """add_code picks a random palette colour when none is given, and
        create_code reports it; this path now does too. No colour was
        requested, so the disclosure pair is absent, as everywhere else."""
        sid = _sid()
        out = json.loads(server.propose_codes(sid, [{"name": "Random hue"}]))
        json.loads(server.update_proposal_status(
            sid, approve=[out["recorded"][0]["guid"]]))

        created = json.loads(server.create_proposed_codes(
            sid, create_backup=False))["created_codes"][0]
        assert created["color"] in QUALCODER_COLORS
        assert "color_requested" not in created
        assert "color_snapped" not in created
        stored = _row(qualcoder_db_path,
                      "SELECT color FROM code_name WHERE cid = ?",
                      (created["code_id"],))["color"]
        assert stored == created["color"]


class TestReviewScreenSurvivesACorruptedProposalColour:
    """Fix round 4, T7. The S1 extension above reads the proposal colour
    off disk and snapped it without the validation snap_to_palette
    declares as its precondition (database.py:160), so one hand-edited or
    corrupted value replaced the WHOLE review screen with
    {"error": "invalid literal for int() with base 16: ''"}. That screen
    is where the researcher gives approval, so a malformed value has to
    degrade its own row and nothing else, and a value the create path
    will refuse must not be advertised as what will be stored.
    """

    MALFORMED = ["red", "#FFF", "#GGHHII", "rgb(1,2,3)", "#12345",
                 "#1234567", "", "  #FF0000  "]

    @staticmethod
    def _seed(sid, pairs):
        """Put proposals straight into the session file, colours and all.

        Bypasses propose_codes deliberately: it enforces #RRGGBB, so the
        only way to the state this class is about is a session file
        written by an older release, edited by hand, or corrupted.
        """
        from qualcoder_mcp.sessions import ProposedCode

        session = server.session_manager.load_session(sid)
        guids = []
        for name, colour in pairs:
            proposal = ProposedCode(name=name)
            proposal.color = colour
            proposal.status = "approved"
            session.add_proposal(proposal)
            guids.append(proposal.guid)
        server.session_manager.save_session(session)
        return guids

    @pytest.mark.parametrize("colour", MALFORMED)
    def test_one_malformed_colour_costs_one_row_not_the_screen(
        self, setup_server, qualcoder_db_path, colour
    ):
        sid = _sid()
        self._seed(sid, [("Before", "#FF0000"), ("Corrupted", colour),
                         ("After", "#1B5E20")])

        review = server.review_proposals(sid)
        # The screen, not an error envelope.
        assert review.startswith("**Review of 3 Code Proposal(s)**"), review
        assert "invalid literal for int()" not in review
        # Every proposal still reviewable, the two sound ones unaffected.
        assert "**Name:** Before" in review
        assert "**Name:** Corrupted" in review
        assert "**Name:** After" in review
        assert f"Colour: {snap_to_palette('#FF0000')} (the nearest palette" \
            in review
        assert "Colour: #1B5E20\n" in review
        # The corrupted row carries the refusal it is heading for. Without
        # this the parameters "" and "#1234567" passed on the pre-fix code
        # (the first took the falsy branch, the second snapped cleanly on
        # [5:7] == '67'), so two of the eight pinned nothing (carried from
        # Batch A).
        assert f"Colour: {colour} (not a #RRGGBB value" in review
        # And it is never advertised as what the create will store.
        assert f"colour to {colour}," not in review

    def test_a_malformed_colour_is_named_with_the_refusal_it_earns(
        self, setup_server, qualcoder_db_path
    ):
        sid = _sid()
        self._seed(sid, [("Corrupted", "red")])
        review = server.review_proposals(sid)
        assert "Colour: red (not a #RRGGBB value" in review
        assert "create_proposed_codes refuses the batch on it" in review
        assert "update_proposal" in review
        # And the remedy is a real one: update_proposal takes a colour.
        assert "what will be stored" not in review

    def test_a_six_character_value_is_not_promised_as_what_gets_stored(
        self, setup_server, qualcoder_db_path
    ):
        """'#12345' snapped silently (int('5', 16) is legal), so the screen
        named a palette colour 'which is what will be stored' while
        create_proposed_codes refuses the whole batch on it."""
        sid = _sid()
        self._seed(sid, [("Short", "#12345")])
        review = server.review_proposals(sid)
        assert "what will be stored" not in review
        assert "Colour: #12345 (not a #RRGGBB value" in review

    @pytest.mark.parametrize("colour", ["", 0, False, []])
    def test_a_falsy_colour_is_not_called_a_palette_pick(
        self, setup_server, qualcoder_db_path, colour
    ):
        """Only None means "no colour given".

        create_proposed_codes reads None as "pick the next palette
        colour" and refuses every other falsy value with "color '' is not
        #RRGGBB", so the screen must not promise a palette pick for them
        (carried from Batch A: the falsy branch tested `if not p.color`
        and swallowed four corrupted values).
        """
        sid = _sid()
        self._seed(sid, [("Falsy", colour)])
        review = server.review_proposals(sid)
        assert "(palette pick at creation)" not in review
        assert "not a #RRGGBB value" in review

    def test_a_non_string_colour_degrades_its_row_too(
        self, setup_server, qualcoder_db_path
    ):
        """A corrupted file can hold any JSON scalar, and `p.color.upper()`
        raised AttributeError on the pre-fix branch for every one of them."""
        sid = _sid()
        self._seed(sid, [("Numeric", 12345), ("Listed", ["#FF0000"])])
        review = server.review_proposals(sid)
        assert review.startswith("**Review of 2 Code Proposal(s)**"), review
        assert "not a #RRGGBB value" in review

    def test_the_snap_disclosure_the_extension_added_is_unchanged(
        self, setup_server, qualcoder_db_path
    ):
        """The guard must not cost the S1 extension anything: a legal
        off-palette colour is still reported as the snapped one, and a
        palette member is still reported plainly."""
        sid = _sid()
        self._seed(sid, [("Legacy", "#FF0000"), ("Member", "#1B5E20"),
                         ("Lower", "#1b5e20"), ("Unset", None)])
        review = server.review_proposals(sid)
        snapped = snap_to_palette("#FF0000")
        assert (f"Colour: {snapped} (the nearest palette colour to #FF0000, "
                f"which is what will be stored)") in review
        assert "Colour: #1B5E20\n" in review
        # Lower case is the same palette colour, so no snap is claimed.
        assert "Colour: #1b5e20\n" in review
        assert "Colour: (palette pick at creation)" in review

    def test_a_corrupted_session_file_end_to_end(
        self, setup_server, qualcoder_db_path
    ):
        """From the bytes on disk to the write, on a file this process did
        not author: edit the JSON, drop the in-memory copy, review, then
        create."""
        sid = _sid()
        before = _count(qualcoder_db_path, "code_name")
        self._seed(sid, [("Sound", "#FF0000"), ("Corrupted", "#FF0000")])

        path = (Path(server.session_manager.storage_dir)
                / f"session_{sid}.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert [p["name"] for p in data["proposed_codes"]] == ["Sound",
                                                               "Corrupted"]
        data["proposed_codes"][1]["color"] = "not a colour"
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        _reload()          # nothing cached from before the edit

        review = server.review_proposals(sid)
        assert review.startswith("**Review of 2 Code Proposal(s)**"), review
        assert "Colour: not a colour (not a #RRGGBB value" in review
        assert (f"Colour: {snap_to_palette('#FF0000')} (the nearest palette "
                f"colour to #FF0000, which is what will be stored)") in review

        # And the screen's claim about the write is the write's behaviour:
        # the batch is refused, named per proposal, with create_backup left
        # at its default True and no backup taken (fix round 4, T7: the
        # colour used to be refused by add_code INSIDE the write, after the
        # backup, which broke the tool's own promise that every approved
        # proposal is validated before the backup and the write).
        backups_before = _backups(qualcoder_db_path)
        created = json.loads(server.create_proposed_codes(sid))
        assert "1 approved proposal(s) failed validation" in created["error"]
        assert "nothing was written and no backup was created" in created["error"]
        failure = created["failures"][0]
        assert failure["name"] == "Corrupted"
        assert failure["reason"].startswith("color 'not a colour' is not")
        assert "update_proposal" in failure["reason"]
        assert _backups(qualcoder_db_path) == backups_before
        # Nothing written: the batch refuses as a batch, so the sound
        # proposal beside the corrupted one goes unwritten with it.
        assert _count(qualcoder_db_path, "code_name") == before
        assert _rows(qualcoder_db_path,
                     "SELECT cid FROM code_name WHERE name = ?",
                     ("Sound",)) == []


class TestSubcodeMoves:
    """v16+ compares both parent pointers (4.0, ai_mcp_server.py:2046)."""

    @pytest.fixture
    def env(self, tmp_path, monkeypatch):
        from qualcoder_mcp.sessions import SessionManager
        saved = (server.db, server.current_project_path, server.session_manager)
        server.db = None
        server.current_project_path = None
        server.session_manager = SessionManager(str(tmp_path / "sessions"))

        def open_version(version):
            folder = make_project(tmp_path, version)
            out = json.loads(server.select_project(str(folder)))
            assert out.get("success") is True, out
            return folder

        yield open_version
        if server.db is not None:
            try:
                server.db.close()
            except Exception:
                pass
        server.db, server.current_project_path, server.session_manager = saved

    def test_subcode_to_no_category_is_a_change_on_v16(self, env):
        folder = env("v16")
        add_subcode(folder, 3, "Sub", supercid=1)
        _reload()
        out = json.loads(server.move_code_to_category(3, None, create_backup=False))
        assert out["changed"] is True
        row = _row(folder, "SELECT catid, supercid FROM code_name WHERE cid=3")
        assert row["catid"] is None and row["supercid"] is None
        out = json.loads(server.move_code_to_category(3, None))
        assert out["changed"] is False

    def test_uncategorised_code_to_no_category_unchanged_on_v14(self, env):
        folder = env("v14")
        _exec(folder, "UPDATE code_name SET catid=NULL WHERE cid=2")
        _reload()
        out = json.loads(server.move_code_to_category(2, None))
        assert out["changed"] is False and out["reason"] == "unchanged"
        assert _backups(folder) == []

    def test_parent_code_id_disclosed_on_a_v14_duplicate(self, env):
        """Fix round 1, F9. A fresh create with parent_code_id is refused on
        a pre-v16 project, but a DUPLICATE create used to answer
        already_exists and say nothing about the parameter, so the model was
        told "use id 1" and never learned that the project cannot hold
        sub-codes. The parameter is now disclosed under `requested` with the
        reason. The refusal itself stays in the DB layer, which re-probes on
        the write connection: a cached refusal here would block a write to a
        project migrated mid-session (pinned in test_qa_v17_gate_core.py)."""
        folder = env("v14")
        existing = _row(folder, "SELECT name FROM code_name WHERE cid=1")["name"]

        out = json.loads(server.create_code(existing, parent_code_id=2))
        assert out["created"] is False and out["reason"] == "already_exists"
        assert out["requested"] == {"parent_code_id": 2}
        assert "no sub-code support" in out["message"]
        assert "parent_code_id" not in out["code"]      # v14 echo has no slot
        assert _backups(folder) == []

    def test_parent_code_mismatch_disclosed_on_v16(self, env):
        folder = env("v16")
        add_subcode(folder, 3, "Sub", supercid=1)
        _reload()
        out = json.loads(server.create_code("sub", parent_code_id=2))
        assert out["created"] is False and out["match"] == "case_insensitive"
        assert out["requested"] == {"name": "sub", "parent_code_id": 2}
        assert out["code"]["parent_code_id"] == 1
        assert "re-parenting a sub-code is done in QualCoder" in out["message"]


# ===========================================================================
# A2: apply_codings per-coding idempotency
# ===========================================================================

class TestApplyCodingsAlreadyExisting:

    def test_existing_identical_coding_is_skipped_and_marked(self, setup_server,
                                                              qualcoder_db_path):
        _exec(qualcoder_db_path,
              "INSERT INTO code_text (cid, fid, seltext, pos0, pos1, owner, date, memo) "
              "VALUES (1, 1, 'I feel stressed about deadlines', 24, 55, "
              "'AI Coding Assistant', '2024-01-15', 'earlier run')")
        _reload()
        ctid = _row(qualcoder_db_path,
                    "SELECT ctid FROM code_text WHERE owner='AI Coding Assistant'")["ctid"]
        sid = _sid()
        g_stress, g_coping = _record(sid, [STRESS, COPING])
        server.update_suggestion_status(sid, approve=[g_stress, g_coping])
        n_before = _count(qualcoder_db_path, "code_text")
        out = server.apply_codings(sid, create_backup=False)
        assert "Successfully Applied: 1 codings" in out
        assert "already_existing_count: 1" in out
        assert f"ctid={ctid}, guid={g_stress}" in out
        assert _count(qualcoder_db_path, "code_text") == n_before + 1
        session = server.session_manager.load_session(sid)
        assert {s.status for s in session.suggestions} == {"applied"}

    def test_all_existing_writes_nothing(self, setup_server, qualcoder_db_path):
        _exec(qualcoder_db_path,
              "INSERT INTO code_text (cid, fid, seltext, pos0, pos1, owner, date, memo) "
              "VALUES (1, 1, 'I feel stressed about deadlines', 24, 55, "
              "'AI Coding Assistant', '2024-01-15', '')")
        _reload()
        sid = _sid()
        (guid,) = _record(sid, [STRESS])
        server.update_suggestion_status(sid, approve=[guid])
        before = _backups(qualcoder_db_path)
        n_before = _count(qualcoder_db_path, "code_text")
        out = server.apply_codings(sid)  # default create_backup=True
        assert "EVERY APPROVED CODING IS ALREADY IN THE DATABASE" in out
        assert "No backup was made and nothing was written" in out
        assert _backups(qualcoder_db_path) == before
        assert _count(qualcoder_db_path, "code_text") == n_before
        session = server.session_manager.load_session(sid)
        assert session.get_suggestion_by_guid(guid).status == "applied"
        again = json.loads(server.apply_codings(sid))
        assert "already applied" in again["error"]

    def test_all_existing_answers_before_the_write_gate(self, setup_server,
                                                        qualcoder_db_path):
        """The declared exception to "the gate comes first" (D5, fix round 1
        F8). Eight codebook tools refuse under a QualCoder lock whatever the
        arguments; apply_codings scans for codings that are already in the
        database first, so a fully redundant batch answers "nothing to
        write" even while the project is open. Nothing reaches the project
        either way, and the CHANGELOG now says so."""
        _exec(qualcoder_db_path,
              "INSERT INTO code_text (cid, fid, seltext, pos0, pos1, owner, date, memo) "
              "VALUES (1, 1, 'I feel stressed about deadlines', 24, 55, "
              "'AI Coding Assistant', '2024-01-15', '')")
        _reload()
        sid = _sid()
        (guid,) = _record(sid, [STRESS])
        server.update_suggestion_status(sid, approve=[guid])

        before = _backups(qualcoder_db_path)
        n_before = _count(qualcoder_db_path, "code_text")
        lock = _lock(qualcoder_db_path)
        lock.write_text(f"gui_user\n{time.time()}", encoding="utf-8")
        try:
            out = server.apply_codings(sid)
        finally:
            lock.unlink()

        assert "EVERY APPROVED CODING IS ALREADY IN THE DATABASE" in out
        assert "gui_user" not in out            # not the lock refusal
        assert _backups(qualcoder_db_path) == before
        assert _count(qualcoder_db_path, "code_text") == n_before
        # The session file is still updated: the suggestion is applied.
        assert server.session_manager.load_session(sid) \
            .get_suggestion_by_guid(guid).status == "applied"

    def test_a_batch_with_work_to_do_still_refuses_under_the_lock(
            self, setup_server, qualcoder_db_path):
        """The exception is narrow: as soon as one approved suggestion would
        be written, the lock refusal comes back and nothing is touched."""
        sid = _sid()
        (guid,) = _record(sid, [STRESS])
        server.update_suggestion_status(sid, approve=[guid])
        before = _backups(qualcoder_db_path)
        n_before = _count(qualcoder_db_path, "code_text")
        lock = _lock(qualcoder_db_path)
        lock.write_text(f"gui_user\n{time.time()}", encoding="utf-8")
        try:
            out = server.apply_codings(sid)
        finally:
            lock.unlink()

        assert "gui_user" in out
        assert _backups(qualcoder_db_path) == before
        assert _count(qualcoder_db_path, "code_text") == n_before

    def test_same_span_under_another_owner_is_written(self, setup_server,
                                                       qualcoder_db_path):
        # fixture ctid 1 is TestCoder's coding of exactly this span
        sid = _sid()
        (guid,) = _record(sid, [STRESS])
        server.update_suggestion_status(sid, approve=[guid])
        n_before = _count(qualcoder_db_path, "code_text")
        out = server.apply_codings(sid, create_backup=False)
        assert "Successfully Applied: 1 codings" in out
        assert "already_existing" not in out
        assert _count(qualcoder_db_path, "code_text") == n_before + 1

    def test_hidden_row_detected_through_base_table(self, setup_server,
                                                     qualcoder_db_path):
        """A 4.0 project hides the AI coder's earlier rows: the visible view
        does not show them, the unique constraint still applies, so the
        detection reads the base table. The result carries the ctid and
        says nothing else about hidden coders."""
        _apply_visibility_schema(qualcoder_db_path, hidden_coder="AI Coding Assistant")
        _reload()
        assert server.db.capabilities.has_coder_names
        sid = _sid()
        (guid,) = _record(sid, [STRESS])
        server.update_suggestion_status(sid, approve=[guid])
        out = server.apply_codings(sid, create_backup=False)
        assert "ctid=3" in out and "already_existing_count: 1" in out
        assert "hidden" not in out.lower()
