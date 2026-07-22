"""v0.8 Phase D2 — attributes & case-link fixes (cases-attributes.md).

Covers the two existing-code fixes the dossier exposed (§6.4 numeric
comparison semantics against ''-placeholders, §2.6 dual whole-file-link
conventions) and the new attribute schema/value tools (§3.1-§3.3 create
with placeholder back-fill, §4.1-§4.3 set with the numeric gate), plus
the §7 annotation addenda (overlap refusal, REFI empty-memo tolerance).
"""

import json
import sqlite3
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server


def _conn(project_path):
    return sqlite3.connect(str(Path(project_path) / "data.qda"))


def _rows(project_path, sql, args=()):
    conn = _conn(project_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return rows


# ============================================================================
# §6.4 fix — numeric attribute comparisons vs '' placeholders
# ============================================================================

class TestS64NumericComparisonFix:
    """The fixture has Age (numeric, case) = '30' for case 1. A second
    case gets the '' placeholder via create_case's back-fill."""

    @pytest.fixture
    def with_placeholder_case(self, setup_server, qualcoder_db_path):
        out = json.loads(server.create_case("Unset participant",
                                            create_backup=False))
        assert out["success"] is True
        return qualcoder_db_path, out["case"]["id"]

    def test_unset_placeholder_never_matches_lt(self, with_placeholder_case):
        """CAST('' AS REAL)=0.0 made every unset attribute match lt/gt —
        the dossier-exposed bug. Unset rows are now excluded."""
        out = json.loads(server.query_by_attribute("Age", "100",
                                                   operator="lt"))
        names = {m["name"] for m in out}
        assert "Case A" in names            # 30 < 100
        assert "Unset participant" not in names

    def test_unset_placeholder_never_matches_gt(self, with_placeholder_case):
        out = json.loads(server.query_by_attribute("Age", "-5",
                                                   operator="gt"))
        names = {m["name"] for m in out}
        assert names == {"Case A"}          # '' would cast to 0.0 > -5

    def test_numeric_equals_normalized(self, with_placeholder_case):
        """'equals' compares numerically for numeric attributes: '30.0'
        finds the stored '30' (plain string equality missed it)."""
        for probe in ("30", "30.0", "3e1"):
            out = json.loads(server.query_by_attribute("Age", probe))
            assert {m["name"] for m in out} == {"Case A"}, probe

    def test_equals_empty_still_finds_unset(self, with_placeholder_case):
        """'' keeps string semantics — the legitimate way to find unset
        attributes (do NOT fix that away, §6.4)."""
        out = json.loads(server.query_by_attribute("Age", ""))
        assert {m["name"] for m in out} == {"Unset participant"}

    def test_non_numeric_probe_on_numeric_attr(self, with_placeholder_case):
        """A non-castable probe can only string-match — no crash, no hit."""
        out = json.loads(server.query_by_attribute("Age", "thirty"))
        assert out == []

    def test_character_equals_stays_string(self, setup_server,
                                           qualcoder_db_path):
        json.loads(server.create_attribute_type("Region", "case",
                                                create_backup=False))
        json.loads(server.set_attribute("case", 1, "Region", "North",
                                        create_backup=False))
        out = json.loads(server.query_by_attribute("Region", "North"))
        assert len(out) == 1


# ============================================================================
# §2.6 fix — overlap-aware whole-file link dedupe
# ============================================================================

class TestS26CaseLinkDedupe:

    FULLTEXT_LEN = 79  # conftest interview.txt

    def test_len_convention_row_detected(self, setup_server,
                                         qualcoder_db_path):
        """A GUI 'Assign case' / survey-import row uses pos1=len (not
        len-1); each upstream probe only matches its own convention. Our
        link must refuse on EITHER convention."""
        conn = _conn(qualcoder_db_path)
        conn.execute(
            "INSERT INTO case_text (caseid, fid, pos0, pos1, memo, owner, "
            "date) VALUES (1, 2, 0, ?, '', 'TestCoder', '2024-01-15')",
            (len("Field notes from observation session."),))
        conn.commit()
        conn.close()
        out = json.loads(server.link_file_to_case(2, case_id=1,
                                                   create_backup=False))
        assert "already linked" in out["error"]
        assert "convention" in out["error"]

    def test_exact_convention_still_refused(self, setup_server,
                                            qualcoder_db_path):
        assert json.loads(server.link_file_to_case(
            2, case_id=1, create_backup=False))["success"]
        out = json.loads(server.link_file_to_case(2, case_id=1,
                                                  create_backup=False))
        assert "already linked" in out["error"]

    def test_partial_span_does_not_block(self, setup_server,
                                         qualcoder_db_path):
        """A portion link is not a whole-file link — linking the whole
        file must still be possible (matches upstream)."""
        conn = _conn(qualcoder_db_path)
        conn.execute(
            "INSERT INTO case_text (caseid, fid, pos0, pos1, memo, owner, "
            "date) VALUES (1, 2, 0, 10, '', 'TestCoder', '2024-01-15')")
        conn.commit()
        conn.close()
        out = json.loads(server.link_file_to_case(2, case_id=1,
                                                   create_backup=False))
        assert out.get("success") is True


# ============================================================================
# §3.1/§3.2 create_attribute_type — row shape + placeholder back-fill
# ============================================================================

class TestS31CreateAttributeType:

    def test_case_domain_backfill(self, setup_server, qualcoder_db_path):
        out = json.loads(server.create_attribute_type(
            "Occupation", "case", memo="What they do",
            create_backup=False))
        assert out["success"] is True
        at = _rows(qualcoder_db_path,
                   "SELECT * FROM attribute_type WHERE name='Occupation'")[0]
        assert at["caseOrFile"] == "case"
        assert at["valuetype"] == "character"
        assert at["memo"] == "What they do"
        assert at["owner"] == "TestCoder"
        # one '' placeholder per existing case, value never NULL
        ph = _rows(qualcoder_db_path,
                   "SELECT value, id, attr_type FROM attribute "
                   "WHERE name='Occupation'")
        assert len(ph) == 1 and out["attribute_type"][
            "placeholders_created"] == 1
        assert ph[0]["value"] == "" and ph[0]["value"] is not None
        assert (ph[0]["id"], ph[0]["attr_type"]) == (1, "case")

    def test_file_domain_backfill(self, setup_server, qualcoder_db_path):
        out = json.loads(server.create_attribute_type(
            "Language", "file", create_backup=False))
        ph = _rows(qualcoder_db_path,
                   "SELECT id FROM attribute WHERE name='Language' "
                   "AND attr_type='file' ORDER BY id")
        assert [r["id"] for r in ph] == [1, 2]  # both fixture sources

    def test_journal_domain_supported(self, setup_server,
                                      qualcoder_db_path):
        """'journal' is a REAL domain (§1) — must not be coerced into
        case/file or rejected."""
        out = json.loads(server.create_attribute_type(
            "Phase", "journal", create_backup=False))
        assert out["success"] is True
        ph = _rows(qualcoder_db_path,
                   "SELECT id, attr_type FROM attribute WHERE name='Phase'")
        assert len(ph) == 1
        assert (ph[0]["id"], ph[0]["attr_type"]) == (1, "journal")

    def test_both_is_not_a_domain(self, setup_server):
        out = json.loads(server.create_attribute_type("X", "both",
                                                      create_backup=False))
        assert "no 'both'" in out["error"]

    def test_numeric_valuetype(self, setup_server, qualcoder_db_path):
        json.loads(server.create_attribute_type(
            "Height", "case", value_type="numeric", create_backup=False))
        at = _rows(qualcoder_db_path,
                   "SELECT valuetype FROM attribute_type WHERE name='Height'")
        assert at[0]["valuetype"] == "numeric"

    def test_invalid_valuetype(self, setup_server):
        out = json.loads(server.create_attribute_type(
            "X", "case", value_type="integer", create_backup=False))
        assert "character" in out["error"]


# ============================================================================
# §3.3 name rules — global namespace + reserved Ref_* (both spellings)
# ============================================================================

class TestS33NameRules:

    def test_global_namespace_across_domains(self, setup_server):
        """'Age' exists as a CASE attribute — a FILE attribute of the
        same name must be refused (attribute_type.name is the PK)."""
        out = json.loads(server.create_attribute_type("Age", "file",
                                                      create_backup=False))
        assert "global" in out["error"]
        assert "case attribute" in out["error"]

    @pytest.mark.parametrize("reserved", [
        "Ref_Type", "Ref_Author", "Ref_Authors", "Ref_Title", "Ref_Year",
        "Ref_Journal",
    ])
    def test_reserved_names_both_spellings(self, setup_server, reserved):
        """Upstream reserves only the singular Ref_Author but its RIS
        importer creates Ref_Authors — we reserve BOTH."""
        out = json.loads(server.create_attribute_type(reserved, "file",
                                                      create_backup=False))
        assert "reserved" in out["error"]

    def test_empty_name_refused(self, setup_server):
        out = json.loads(server.create_attribute_type("   ", "case",
                                                      create_backup=False))
        assert "error" in out


# ============================================================================
# §4.1/§4.2 set_attribute — per-domain fidelity + the numeric gate
# ============================================================================

class TestS41SetAttribute:

    def test_case_update_refreshes_owner_and_date(self, setup_server,
                                                  qualcoder_db_path):
        """Case path: value + date + owner updated (cases.py:670-679)."""
        out = json.loads(server.set_attribute("case", 1, "Age", "44",
                                              create_backup=False))
        assert out["success"] is True
        assert out["attribute"]["previous_value"] == "30"
        row = _rows(qualcoder_db_path,
                    "SELECT value, owner, date FROM attribute "
                    "WHERE name='Age' AND id=1 AND attr_type='case'")[0]
        assert row["value"] == "44"
        assert row["owner"] == "TestCoder"       # refreshed to codername
        assert row["date"] != "2024-01-15"       # refreshed

    def test_file_update_touches_value_only(self, setup_server,
                                            qualcoder_db_path):
        """File path: value only (manage_files.py:1470-1471) — the
        placeholder's owner/date are untouched."""
        json.loads(server.create_attribute_type("Language", "file",
                                                create_backup=False))
        before = _rows(qualcoder_db_path,
                       "SELECT owner, date FROM attribute "
                       "WHERE name='Language' AND id=1")[0]
        json.loads(server.set_attribute("file", 1, "Language", "Italian",
                                        create_backup=False))
        after = _rows(qualcoder_db_path,
                      "SELECT value, owner, date FROM attribute "
                      "WHERE name='Language' AND id=1")[0]
        assert after["value"] == "Italian"
        assert (after["owner"], after["date"]) == (before["owner"],
                                                   before["date"])

    def test_journal_set(self, setup_server, qualcoder_db_path):
        json.loads(server.create_attribute_type("Phase", "journal",
                                                create_backup=False))
        out = json.loads(server.set_attribute("journal", 1, "Phase",
                                              "pilot", create_backup=False))
        assert out["attribute"]["target_name"] == "Entry 1"
        row = _rows(qualcoder_db_path,
                    "SELECT value FROM attribute WHERE name='Phase'")[0]
        assert row["value"] == "pilot"

    def test_insert_if_missing(self, setup_server, qualcoder_db_path):
        """§3.8: never assume the placeholder row exists — QualCoder's
        case-side heal is a no-op in 3.8.2. Deleting the row and setting
        must recreate it (the insert-if-missing dance)."""
        conn = _conn(qualcoder_db_path)
        conn.execute("DELETE FROM attribute WHERE name='Age' AND id=1")
        conn.commit()
        conn.close()
        out = json.loads(server.set_attribute("case", 1, "Age", "39",
                                              create_backup=False))
        assert out["attribute"]["row_created"] is True
        assert out["attribute"]["previous_value"] is None
        row = _rows(qualcoder_db_path,
                    "SELECT value, attr_type FROM attribute "
                    "WHERE name='Age' AND id=1")[0]
        assert (row["value"], row["attr_type"]) == ("39", "case")

    def test_numeric_gate_rejects(self, setup_server, qualcoder_db_path):
        """Deviation from GUI (documented): refuse instead of silently
        blanking a non-castable numeric value."""
        out = json.loads(server.set_attribute("case", 1, "Age", "thirty",
                                              create_backup=False))
        assert "not a number" in out["error"]
        row = _rows(qualcoder_db_path,
                    "SELECT value FROM attribute WHERE name='Age' AND id=1")
        assert row[0]["value"] == "30"  # untouched

    @pytest.mark.parametrize("ok", ["44", "4.5", "1e3", "-2"])
    def test_numeric_gate_accepts_floats(self, setup_server, ok):
        out = json.loads(server.set_attribute("case", 1, "Age", ok,
                                              create_backup=False))
        assert out["success"] is True, ok

    def test_empty_clears(self, setup_server, qualcoder_db_path):
        """'' is the canonical unset (§4.3) — always accepted, row kept."""
        out = json.loads(server.set_attribute("case", 1, "Age", "",
                                              create_backup=False))
        assert out["success"] is True
        row = _rows(qualcoder_db_path,
                    "SELECT value FROM attribute WHERE name='Age' AND id=1")
        assert row[0]["value"] == ""

    def test_wrong_domain_refused(self, setup_server):
        out = json.loads(server.set_attribute("file", 1, "Age", "30",
                                              create_backup=False))
        assert "case attribute" in out["error"]

    def test_unknown_attribute(self, setup_server):
        out = json.loads(server.set_attribute("case", 1, "Ghost", "x",
                                              create_backup=False))
        assert "create_attribute_type" in out["error"]

    def test_unknown_entity(self, setup_server):
        out = json.loads(server.set_attribute("case", 99, "Age", "30",
                                              create_backup=False))
        assert "does not exist" in out["error"]


# ============================================================================
# §2.1 create_case back-fill exactness (caseOrFile='case' only)
# ============================================================================

class TestS21CaseBackfillExact:

    def test_bogus_both_row_gets_no_placeholder(self, setup_server,
                                                qualcoder_db_path):
        conn = _conn(qualcoder_db_path)
        conn.execute(
            "INSERT INTO attribute_type (name, date, owner, memo, "
            "caseOrFile, valuetype) VALUES ('Shared', '2024-01-15', 'T', "
            "'', 'both', 'character')")
        conn.commit()
        conn.close()
        out = json.loads(server.create_case("Fresh case",
                                            create_backup=False))
        # only the real case attribute (Age) is back-filled
        assert out["case"]["attributes_created"] == 1


# ============================================================================
# §7 annotation addenda — overlap refusal + REFI empty-memo tolerance
# ============================================================================

class TestS7AnnotationAddenda:

    def test_same_owner_overlap_refused(self, setup_server):
        """§7.1: the GUI never creates a second annotation overlapping
        one by the same coder (it switches to editing). Refuse and point
        at the existing row."""
        a1 = json.loads(server.add_annotation(1, 0, 10, "first note",
                                              create_backup=False))
        out = json.loads(server.add_annotation(1, 5, 15, "overlapping",
                                               create_backup=False))
        assert "overlaps" in out["error"]
        assert str(a1["annotation"]["annotation_id"]) in out["error"]
        assert "update_annotation" in out["error"]

    def test_other_owner_overlap_allowed(self, setup_server,
                                         qualcoder_db_path):
        """Multi-coder annotation on the same passage is normal."""
        conn = _conn(qualcoder_db_path)
        conn.execute(
            "INSERT INTO annotation (fid, pos0, pos1, memo, owner, date) "
            "VALUES (1, 0, 10, 'other coder note', 'Gemma', "
            "'2024-01-15 00:00:00')")
        conn.commit()
        conn.close()
        out = json.loads(server.add_annotation(1, 5, 15, "my note",
                                               create_backup=False))
        assert out.get("success") is True

    def test_adjacent_spans_allowed(self, setup_server):
        """End-exclusive adjacency [0,10)+[10,20) is not an overlap."""
        json.loads(server.add_annotation(1, 0, 10, "first",
                                         create_backup=False))
        out = json.loads(server.add_annotation(1, 10, 20, "second",
                                               create_backup=False))
        assert out.get("success") is True

    def test_refi_born_empty_memo_tolerated_on_read(self, setup_server,
                                                    qualcoder_db_path):
        """§7.5: REFI import can insert ''/NULL-memo annotation rows.
        Read paths tolerate and normalize them; our writers never create
        them."""
        conn = _conn(qualcoder_db_path)
        conn.execute(
            "INSERT INTO annotation (fid, pos0, pos1, memo, owner, date) "
            "VALUES (1, 0, 5, NULL, 'RefiCoder', '2024-01-15 00:00:00')")
        conn.commit()
        conn.close()
        out = json.loads(server.analyze_file_with_coding(1))
        anns = out["annotations"]
        assert len(anns) == 1
        assert anns[0]["memo"] == ""  # normalized, no crash

    def test_empty_memo_row_deletable_by_anid(self, setup_server,
                                              qualcoder_db_path):
        conn = _conn(qualcoder_db_path)
        cur = conn.execute(
            "INSERT INTO annotation (fid, pos0, pos1, memo, owner, date) "
            "VALUES (1, 0, 5, '', 'RefiCoder', '2024-01-15 00:00:00')")
        anid = cur.lastrowid
        conn.commit()
        conn.close()
        out = json.loads(server.delete_annotation(anid,
                                                  create_backup=False))
        assert out["deleted"] is True
