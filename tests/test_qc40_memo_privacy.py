"""P1-1: QualCoder 4.0 '#####' memo privacy convention.

Pins the upstream semantics (ai_memo.py:28-59 at pin 9bddf17) edge case
by edge case, then the server behavior built on them: every
memo-returning read path strips the private suffix silently, every
memo-writing path preserves it, and file exports keep full memos by
owner-ruled parity with QualCoder's own exports.
"""

import json
import sqlite3
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server
from qualcoder_mcp.database import QualcoderDatabase
from qualcoder_mcp.memo_privacy import (
    PERSONAL_NOTE_MARK,
    extract_ai_memo,
    merge_public_memo,
    neutralize_marker,
    split_public_private_memo,
    strip_private_memos,
)


def _data(project_path) -> Path:
    return Path(project_path) / "data.qda"


def _row(project_path, sql, args=()):
    con = sqlite3.connect(str(_data(project_path)))
    con.row_factory = sqlite3.Row
    r = con.execute(sql, args).fetchone()
    con.close()
    return r


def _exec(project_path, sql, args=()):
    con = sqlite3.connect(str(_data(project_path)))
    con.execute(sql, args)
    con.commit()
    con.close()


def _reopen(project_path):
    """Reconnect the server global after direct sqlite edits."""
    if server.db is not None:
        try:
            server.db.close()
        except Exception:
            pass
    server.db = QualcoderDatabase(project_path)


SECRET = "the-private-word-zanzibar"


# =============================================================================
# MODULE SEMANTICS: byte-for-byte parity with upstream ai_memo.py
# =============================================================================

class TestSplitSemantics:

    def test_none_is_empty(self):
        assert split_public_private_memo(None) == ("", "")

    def test_no_marker_all_public(self):
        assert split_public_private_memo("plain note") == ("plain note", "")

    def test_marker_at_position_zero_all_private(self):
        assert split_public_private_memo("#####priv") == ("", "#####priv")

    def test_first_of_multiple_markers_wins(self):
        pub, priv = split_public_private_memo("a#####b#####c")
        assert pub == "a"
        assert priv == "#####b#####c"

    def test_marker_with_no_suffix_still_private(self):
        assert split_public_private_memo("note #####") == ("note ", "#####")

    def test_marker_constant_matches_upstream(self):
        assert PERSONAL_NOTE_MARK == "#####"

    def test_four_hashes_are_public(self):
        # The marker is exactly five '#'; four do not split
        assert split_public_private_memo("h4 #### x") == ("h4 #### x", "")

    def test_six_hashes_split_at_first_five(self):
        # find() locates the five-char marker at index 3; the sixth '#'
        # belongs to the private suffix (upstream find semantics)
        pub, priv = split_public_private_memo("ab ######x")
        assert pub == "ab "
        assert priv == "######x"


class TestNeutralizeMarker:
    """neutralize_marker makes names/owners safe to embed in a memo (S-M1)."""

    def test_plain_text_unchanged(self):
        assert neutralize_marker("Stress") == "Stress"
        assert neutralize_marker("a #### b") == "a #### b"

    def test_exact_marker_collapsed(self):
        assert neutralize_marker("Stress ##### x") == "Stress #### x"

    def test_longer_run_cannot_reform_a_marker(self):
        # A plain replace("#####", "####") would turn six hashes into
        # five and re-create the marker; the whole run must collapse
        for n in range(5, 13):
            out = neutralize_marker("#" * n)
            assert out == "####", n
            assert PERSONAL_NOTE_MARK not in out

    def test_every_run_collapsed(self):
        assert neutralize_marker("a#####b######c") == "a####b####c"
        assert PERSONAL_NOTE_MARK not in neutralize_marker("x ##### y ##### z")

    def test_none_is_empty(self):
        assert neutralize_marker(None) == ""

    def test_result_never_contains_marker(self):
        for text in ("#####", " ##### ", "##########", "a#####", "#####b",
                     "#" * 50, "x" + "#" * 7 + "y" + "#" * 5):
            assert PERSONAL_NOTE_MARK not in neutralize_marker(text), text


class TestExtractSemantics:

    def test_extract_keeps_whitespace_before_marker(self):
        # Upstream does NOT trim the public part on extract
        assert extract_ai_memo("pub  \n#####priv") == "pub  \n"

    def test_extract_none(self):
        assert extract_ai_memo(None) == ""


class TestMergeSemantics:

    def test_plain_replace_without_suffix(self):
        assert merge_public_memo("old", "new") == "new"

    def test_marker_in_new_text_is_dropped_even_without_suffix(self):
        # An AI write can never CREATE a private zone
        assert merge_public_memo("old", "a#####b") == "a"

    def test_suffix_preserved_verbatim(self):
        assert merge_public_memo("pub#####priv", "new") == "new#####priv"

    def test_separator_whitespace_run_preserved(self):
        # The whitespace run between old public text and the marker is
        # re-used as the joint (upstream ai_memo.py:57-58)
        assert (merge_public_memo("pub \t\n#####priv", "new")
                == "new \t\n#####priv")

    def test_empty_new_public_leaves_suffix_alone(self):
        assert merge_public_memo("pub#####priv", "") == "#####priv"

    def test_new_marker_cannot_replace_existing_private_zone(self):
        assert (merge_public_memo("pub#####real", "x#####fake")
                == "x#####real")

    def test_merge_onto_fully_private_memo(self):
        # Marker at position 0: public is '', separator is ''
        assert merge_public_memo("#####priv", "new") == "new#####priv"

    def test_merge_none_existing(self):
        assert merge_public_memo(None, "new") == "new"

    def test_crlf_separator_preserved(self):
        # Upstream rstrips exactly " \t\r\n" (ai_memo.py:57), so a CRLF
        # joint survives byte-for-byte (QA round 1, F7)
        assert (merge_public_memo("pub\r\n#####priv", "new")
                == "new\r\n#####priv")

    def test_marker_on_its_own_crlf_line(self):
        assert (merge_public_memo("l1\r\nl2\r\n#####\r\npriv", "x")
                == "x\r\n#####\r\npriv")

    def test_whitespace_outside_upstream_set_is_not_a_separator(self):
        # A vertical tab or a no-break space before the marker is NOT
        # in upstream's separator set: it belongs to the old public
        # text and goes with it, never re-used as the joint (a bare
        # rstrip() would wrongly keep it)
        assert merge_public_memo("pub\x0b#####priv", "new") == "new#####priv"
        assert (merge_public_memo("pub\u00a0#####priv", "new")
                == "new#####priv")


class TestStripPrivateMemos:

    def test_nested_structures(self):
        payload = {
            "memo": "a#####b",
            "items": [{"memo": "c#####d", "name": "keep#####this"}],
            "tup": ({"memo": "#####x"},),
            "count": 3,
        }
        out = strip_private_memos(payload)
        assert out["memo"] == "a"
        assert out["items"][0]["memo"] == "c"
        # Only 'memo' keys are treated as memos
        assert out["items"][0]["name"] == "keep#####this"
        assert out["tup"][0]["memo"] == ""
        assert out["count"] == 3

    def test_non_string_memo_untouched(self):
        assert strip_private_memos({"memo": None}) == {"memo": None}

    def test_input_not_mutated(self):
        payload = {"memo": "a#####b"}
        strip_private_memos(payload)
        assert payload["memo"] == "a#####b"


# =============================================================================
# WRITE PATHS: merge-preserving, marker never written
# =============================================================================

class TestSetMemoMergePreserving:

    def test_private_suffix_survives_rewrite(self, setup_server,
                                             qualcoder_db_path):
        _exec(qualcoder_db_path,
              "UPDATE code_name SET memo = ? WHERE cid = 1",
              (f"old public#####{SECRET}",))
        _reopen(qualcoder_db_path)

        out = json.loads(server.set_memo("code", 1, "new public",
                                         create_backup=False))
        assert out["success"] is True
        stored = _row(qualcoder_db_path,
                      "SELECT memo FROM code_name WHERE cid = 1")["memo"]
        assert stored == f"new public#####{SECRET}"
        # The echo shows the public part only, with no hint of the zone
        assert out["memo"] == "new public"
        assert SECRET not in json.dumps(out)
        assert "#####" not in json.dumps(out)

    def test_marker_in_new_memo_cannot_enter_private_zone(
            self, setup_server, qualcoder_db_path):
        _exec(qualcoder_db_path,
              "UPDATE code_name SET memo = ? WHERE cid = 1",
              (f"pub#####{SECRET}",))
        _reopen(qualcoder_db_path)

        json.loads(server.set_memo("code", 1, "evil#####fake",
                                   create_backup=False))
        stored = _row(qualcoder_db_path,
                      "SELECT memo FROM code_name WHERE cid = 1")["memo"]
        assert stored == f"evil#####{SECRET}"

    def test_clearing_public_keeps_private_zone(self, setup_server,
                                                qualcoder_db_path):
        _exec(qualcoder_db_path,
              "UPDATE cases SET memo = ? WHERE caseid = 1",
              (f"pub#####{SECRET}",))
        _reopen(qualcoder_db_path)

        out = json.loads(server.set_memo("case", 1, "", create_backup=False))
        assert out["cleared"] is True
        stored = _row(qualcoder_db_path,
                      "SELECT memo FROM cases WHERE caseid = 1")["memo"]
        assert stored == f"#####{SECRET}"

    def test_separator_whitespace_preserved_through_tool(
            self, setup_server, qualcoder_db_path):
        _exec(qualcoder_db_path,
              "UPDATE source SET memo = ? WHERE id = 1",
              (f"pub \n#####{SECRET}",))
        _reopen(qualcoder_db_path)

        json.loads(server.set_memo("file", 1, "replaced",
                                   create_backup=False))
        stored = _row(qualcoder_db_path,
                      "SELECT memo FROM source WHERE id = 1")["memo"]
        assert stored == f"replaced \n#####{SECRET}"

    def test_crlf_separator_preserved_through_tool(
            self, setup_server, qualcoder_db_path):
        _exec(qualcoder_db_path,
              "UPDATE source SET memo = ? WHERE id = 1",
              (f"pub\r\n#####{SECRET}",))
        _reopen(qualcoder_db_path)

        json.loads(server.set_memo("file", 1, "replaced",
                                   create_backup=False))
        stored = _row(qualcoder_db_path,
                      "SELECT memo FROM source WHERE id = 1")["memo"]
        assert stored == f"replaced\r\n#####{SECRET}"

    def test_all_five_targets_preserve_suffix(self, setup_server,
                                              qualcoder_db_path):
        targets = [
            ("code", 1, "code_name", "cid"),
            ("category", 1, "code_cat", "catid"),
            ("file", 1, "source", "id"),
            ("coding", 1, "code_text", "ctid"),
            ("case", 1, "cases", "caseid"),
        ]
        for ttype, tid, table, idcol in targets:
            _exec(qualcoder_db_path,
                  f"UPDATE {table} SET memo = ? WHERE {idcol} = ?",
                  (f"p#####{SECRET}-{ttype}", tid))
        _reopen(qualcoder_db_path)
        for ttype, tid, table, idcol in targets:
            out = json.loads(server.set_memo(ttype, tid, "np",
                                             create_backup=False))
            assert out["success"] is True, (ttype, out)
            stored = _row(qualcoder_db_path,
                          f"SELECT memo FROM {table} WHERE {idcol} = ?",
                          (tid,))["memo"]
            assert stored == f"np#####{SECRET}-{ttype}", ttype


class TestCreatePathsStripMarker:

    def test_create_code_memo_is_public_only(self, setup_server,
                                             qualcoder_db_path):
        out = json.loads(server.create_code(
            "Fresh", memo="definition#####not-a-zone", create_backup=False))
        assert out["success"] is True
        stored = _row(qualcoder_db_path,
                      "SELECT memo FROM code_name WHERE name = 'Fresh'")["memo"]
        assert stored == "definition"

    def test_import_text_file_memo_is_public_only(self, setup_server,
                                                  qualcoder_db_path):
        out = json.loads(server.import_text_file(
            "imported.txt", "Body text.", memo="note#####tail",
            create_backup=False))
        assert out["success"] is True
        stored = _row(qualcoder_db_path,
                      "SELECT memo FROM source WHERE name = 'imported.txt'"
                      )["memo"]
        assert stored == "note"

    def test_create_case_memo_is_public_only(self, setup_server,
                                             qualcoder_db_path):
        out = json.loads(server.create_case(
            "CaseP1", memo="m#####t", create_backup=False))
        assert out["success"] is True
        stored = _row(qualcoder_db_path,
                      "SELECT memo FROM cases WHERE name = 'CaseP1'")["memo"]
        assert stored == "m"

    def test_journal_entry_is_public_only(self, setup_server,
                                          qualcoder_db_path):
        out = json.loads(server.add_journal_entry(
            "P1 entry", "visible#####invisible", create_backup=False))
        assert out["success"] is True
        stored = _row(qualcoder_db_path,
                      "SELECT jentry FROM journal WHERE name = 'P1 entry'"
                      )["jentry"]
        assert stored == "visible"

    def test_annotation_all_private_refused(self, setup_server,
                                            qualcoder_db_path):
        out = json.loads(server.add_annotation(
            1, 0, 4, "#####only private", create_backup=False))
        assert "error" in out
        assert "#####" in out["error"]


class TestAnnotationUpdateMergePreserving:

    def _make_annotation(self, qualcoder_db_path, memo):
        _exec(qualcoder_db_path,
              "INSERT INTO annotation (fid, pos0, pos1, memo, owner, date) "
              "VALUES (1, 0, 4, ?, 'TestCoder', '2024-01-15')", (memo,))
        _reopen(qualcoder_db_path)
        return _row(qualcoder_db_path,
                    "SELECT anid FROM annotation ORDER BY anid DESC")["anid"]

    def test_update_preserves_suffix(self, setup_server, qualcoder_db_path):
        anid = self._make_annotation(qualcoder_db_path,
                                     f"note#####{SECRET}")
        out = json.loads(server.update_annotation(anid, "edited",
                                                  create_backup=False))
        assert out["success"] is True
        assert out["memo"] == "edited"
        assert SECRET not in json.dumps(out)
        stored = _row(qualcoder_db_path,
                      "SELECT memo FROM annotation WHERE anid = ?",
                      (anid,))["memo"]
        assert stored == f"edited#####{SECRET}"

    def test_clearing_with_suffix_keeps_row(self, setup_server,
                                            qualcoder_db_path):
        anid = self._make_annotation(qualcoder_db_path,
                                     f"note#####{SECRET}")
        out = json.loads(server.update_annotation(anid, "",
                                                  create_backup=False))
        assert out.get("deleted_because_cleared") is None
        assert out.get("cleared") is True
        stored = _row(qualcoder_db_path,
                      "SELECT memo FROM annotation WHERE anid = ?",
                      (anid,))
        assert stored is not None
        assert stored["memo"] == f"#####{SECRET}"

    def test_clearing_without_suffix_still_deletes(self, setup_server,
                                                   qualcoder_db_path):
        anid = self._make_annotation(qualcoder_db_path, "plain note")
        out = json.loads(server.update_annotation(anid, "",
                                                  create_backup=False))
        assert out.get("deleted_because_cleared") is True
        assert _row(qualcoder_db_path,
                    "SELECT anid FROM annotation WHERE anid = ?",
                    (anid,)) is None

    def test_delete_annotation_echo_is_stripped(self, setup_server,
                                                qualcoder_db_path):
        anid = self._make_annotation(qualcoder_db_path,
                                     f"note#####{SECRET}")
        out = json.loads(server.delete_annotation(anid, create_backup=False))
        assert out["success"] is True
        assert SECRET not in json.dumps(out)


class TestMergeProvenancePreservesSuffix:

    def _enable_supercid(self, qualcoder_db_path):
        _exec(qualcoder_db_path,
              "ALTER TABLE code_name ADD COLUMN supercid INTEGER")
        _reopen(qualcoder_db_path)

    def test_provenance_block_lands_before_private_suffix(
            self, setup_server, qualcoder_db_path):
        self._enable_supercid(qualcoder_db_path)
        _exec(qualcoder_db_path,
              "UPDATE code_name SET memo = ? WHERE cid = 2",
              (f"target public#####{SECRET}",))
        _reopen(qualcoder_db_path)

        out = json.loads(server.merge_codes(1, 2, confirm=True))
        assert out.get("merged") is True, out
        stored = _row(qualcoder_db_path,
                      "SELECT memo FROM code_name WHERE cid = 2")["memo"]
        assert "[Merged from code: Stress" in stored
        # The suffix survives verbatim at the very end
        assert stored.endswith(f"#####{SECRET}")
        # And the provenance block is in the PUBLIC zone
        assert "[Merged from code: Stress" in extract_ai_memo(stored)

    def test_source_private_zone_not_destroyed(self, setup_server,
                                               qualcoder_db_path):
        self._enable_supercid(qualcoder_db_path)
        _exec(qualcoder_db_path,
              "UPDATE code_name SET memo = ? WHERE cid = 1",
              (f"source pub#####{SECRET}",))
        _reopen(qualcoder_db_path)

        json.loads(server.merge_codes(1, 2, confirm=True))
        stored = _row(qualcoder_db_path,
                      "SELECT memo FROM code_name WHERE cid = 2")["memo"]
        # Carried into the target whole; the marker keeps it AI-hidden
        assert SECRET in stored
        assert SECRET not in extract_ai_memo(stored)

    def test_no_suffix_keeps_parity_recipe(self, setup_server,
                                           qualcoder_db_path):
        self._enable_supercid(qualcoder_db_path)
        _exec(qualcoder_db_path,
              "UPDATE code_name SET memo = 'src note' WHERE cid = 1")
        _exec(qualcoder_db_path,
              "UPDATE code_name SET memo = 'target note' WHERE cid = 2")
        _reopen(qualcoder_db_path)
        json.loads(server.merge_codes(1, 2, confirm=True))
        stored = _row(qualcoder_db_path,
                      "SELECT memo FROM code_name WHERE cid = 2")["memo"]
        assert stored.startswith("target note")
        assert "[Merged from code: Stress, Coder: TestCoder," in stored
        assert stored.endswith("src note")

    def test_marker_in_source_name_cannot_move_the_private_boundary(
            self, setup_server, qualcoder_db_path):
        # S-M1: a code name carrying '#####' is written verbatim into the
        # provenance block; unneutralized it would plant a marker BEFORE
        # the researcher's own private zone and lock the AI's block into it
        self._enable_supercid(qualcoder_db_path)
        _exec(qualcoder_db_path,
              "UPDATE code_name SET memo = ? WHERE cid = 2",
              (f"target public#####{SECRET}",))
        _reopen(qualcoder_db_path)
        out = json.loads(server.rename_code(1, "Stress ##### x",
                                            create_backup=False))
        assert out.get("success") is True, out
        out = json.loads(server.merge_codes(1, 2, confirm=True))
        assert out.get("merged") is True, out
        stored = _row(qualcoder_db_path,
                      "SELECT memo FROM code_name WHERE cid = 2")["memo"]
        # The researcher's private zone is exactly what it was
        assert split_public_private_memo(stored)[1] == f"#####{SECRET}"
        # The provenance record keeps the name, neutralized, in the public zone
        assert "[Merged from code: Stress #### x, Coder: TestCoder," in \
            extract_ai_memo(stored)

    def test_marker_in_source_name_cannot_create_a_private_zone(
            self, setup_server, qualcoder_db_path):
        # S-M1: two AI calls (create_code with a marker in the NAME, then
        # merge into a target with no private zone) must not produce a
        # memo the AI can no longer read back
        self._enable_supercid(qualcoder_db_path)
        _exec(qualcoder_db_path,
              "UPDATE code_name SET memo = 'target note' WHERE cid = 2")
        _reopen(qualcoder_db_path)
        out = json.loads(server.create_code("Probe #####y", memo="src public",
                                            create_backup=False))
        cid = out["code"]["id"] if "code" in out else out["code_id"]
        out = json.loads(server.merge_codes(cid, 2, confirm=True))
        assert out.get("merged") is True, out
        stored = _row(qualcoder_db_path,
                      "SELECT memo FROM code_name WHERE cid = 2")["memo"]
        assert PERSONAL_NOTE_MARK not in stored
        assert "src public" in extract_ai_memo(stored)
        assert "[Merged from code: Probe ####y, Coder:" in stored

    def test_marker_in_source_owner_is_neutralized(self, setup_server,
                                                   qualcoder_db_path):
        # A GUI-authored or legacy owner string can carry the marker too;
        # the owner component is neutralized like the name
        self._enable_supercid(qualcoder_db_path)
        _exec(qualcoder_db_path,
              "UPDATE code_name SET owner = 'Coder #####hidden', "
              "memo = 'src' WHERE cid = 1")
        _exec(qualcoder_db_path,
              "UPDATE code_name SET memo = 'target' WHERE cid = 2")
        _reopen(qualcoder_db_path)
        json.loads(server.merge_codes(1, 2, confirm=True))
        stored = _row(qualcoder_db_path,
                      "SELECT memo FROM code_name WHERE cid = 2")["memo"]
        assert PERSONAL_NOTE_MARK not in stored
        assert "Coder: Coder ####hidden," in stored

    def test_source_memo_marker_is_the_only_one_that_survives(
            self, setup_server, qualcoder_db_path):
        # Only the source MEMO may legitimately carry the marker into the
        # target (its private zone travels whole, still AI-hidden)
        self._enable_supercid(qualcoder_db_path)
        _exec(qualcoder_db_path,
              "UPDATE code_name SET name = 'N #####n', owner = 'O #####o', "
              "memo = ? WHERE cid = 1", (f"src pub#####{SECRET}",))
        _reopen(qualcoder_db_path)
        json.loads(server.merge_codes(1, 2, confirm=True))
        stored = _row(qualcoder_db_path,
                      "SELECT memo FROM code_name WHERE cid = 2")["memo"]
        assert stored.count(PERSONAL_NOTE_MARK) == 1
        assert split_public_private_memo(stored)[1] == f"#####{SECRET}"
        assert "[Merged from code: N ####n, Coder: O ####o," in \
            extract_ai_memo(stored)


# =============================================================================
# READ PATHS: silent strip on every memo-returning tool and resource
# =============================================================================

@pytest.fixture
def private_everywhere(setup_server, qualcoder_db_path):
    """Plant a private suffix on every memo-bearing row of the fixture."""
    stmts = [
        ("UPDATE project SET memo = ?", (f"proj#####{SECRET}",)),
        ("UPDATE code_name SET memo = ? WHERE cid = 1",
         (f"code#####{SECRET}",)),
        ("UPDATE code_cat SET memo = ? WHERE catid = 1",
         (f"cat#####{SECRET}",)),
        ("UPDATE source SET memo = ? WHERE id = 1",
         (f"file#####{SECRET}",)),
        ("UPDATE code_text SET memo = ? WHERE ctid = 1",
         (f"coding#####{SECRET}",)),
        ("UPDATE cases SET memo = ? WHERE caseid = 1",
         (f"case#####{SECRET}",)),
        ("UPDATE journal SET jentry = ? WHERE jid = 1",
         (f"journal#####{SECRET}",)),
        ("UPDATE attribute_type SET memo = ? WHERE name = 'Age'",
         (f"attr#####{SECRET}",)),
        # A file-scoped attribute type plus a value on file 1, so that
        # get_file_attributes(1) is a real read rather than an empty one
        ("INSERT INTO attribute_type VALUES ('Source', '2024-01-15', "
         "'TestCoder', ?, 'file', 'character')",
         (f"fattr#####{SECRET}",)),
        ("INSERT INTO attribute VALUES (NULL, 'Source', 'file', "
         "'interview', 1, '2024-01-15', 'TestCoder')", ()),
        ("INSERT INTO annotation (fid, pos0, pos1, memo, owner, date) "
         "VALUES (1, 0, 4, ?, 'TestCoder', '2024-01-15')",
         (f"ann#####{SECRET}",)),
    ]
    for sql, args in stmts:
        _exec(qualcoder_db_path, sql, args)
    _reopen(qualcoder_db_path)
    return qualcoder_db_path


def _assert_clean(output: str):
    assert SECRET not in output
    assert "#####" not in output


def _memos_in(obj):
    """Every string under a 'memo' key anywhere in a parsed payload."""
    found = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "memo" and isinstance(value, str):
                found.append(value)
            else:
                found.extend(_memos_in(value))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_memos_in(item))
    return found


class TestReadPathsStrip:

    def test_resources_strip(self, private_everywhere):
        for fn, args in [
            (server.get_project_info, ()),
            (server.list_all_codes, ()),
            (server.list_all_categories, ()),
            (server.get_code_info, (1,)),
            (server.list_all_files, ()),
            (server.get_file_content, (1,)),
            (server.list_all_cases, ()),
            (server.get_case_info, (1,)),
            (server.get_journal_entries, ()),
        ]:
            out = fn(*args)
            _assert_clean(out)
            parsed = json.loads(out)
            if isinstance(parsed, dict):
                assert "error" not in parsed, fn.__name__

    @pytest.mark.parametrize("fn,args,expected", [
        (server.get_project_info, (), "proj"),
        (server.list_all_codes, (), "code"),
        (server.get_code_info, (1,), "code"),
        (server.list_all_categories, (), "cat"),
        (server.list_all_files, (), "file"),
        (server.get_file_content, (1,), "file"),
        (server.list_all_cases, (), "case"),
        (server.get_case_info, (1,), "case"),
        (server.list_attribute_types, (), "attr"),
        (server.get_case_attributes, (1,), "attr"),
        (server.get_file_attributes, (1,), "fattr"),
        (server.analyze_file_with_coding, (1,), "ann"),
    ], ids=lambda v: getattr(v, "__name__", None) or str(v))
    def test_public_part_survives_per_kind(self, private_everywhere, fn,
                                           args, expected):
        # Silent strip, not blanking: the public part of every memo
        # kind must still be returned (QA round 1, F7)
        out = fn(*args)
        _assert_clean(out)
        memos = _memos_in(json.loads(out))
        assert expected in memos, (fn.__name__, memos)

    def test_journal_public_part_survives(self, private_everywhere):
        out = json.loads(server.get_journal_entries())
        assert out[0]["content"] == "journal"

    def test_tools_strip(self, private_everywhere):
        for fn, args, kwargs in [
            (server.get_current_project, (), {}),
            (server.get_project_summary, (), {}),
            (server.get_coded_segments, (1,), {}),
            (server.search_coded_text, ("stressed",), {}),
            (server.analyze_file_with_coding, (1,), {}),
            (server.export_code_report, ("Stress",), {}),
            (server.list_attribute_types, (), {}),
            (server.get_case_attributes, (1,), {}),
            (server.get_file_attributes, (1,), {}),
            (server.query_by_attribute, ("Age", "30"), {}),
            (server.get_cases_by_code, (1,), {}),
        ]:
            out = fn(*args, **kwargs)
            _assert_clean(out)

    def test_select_project_strips_project_memo(self, private_everywhere):
        out = server.select_project(private_everywhere)
        _assert_clean(out)
        assert json.loads(out)["success"] is True

    def test_coded_segments_public_part_survives(self, private_everywhere):
        out = json.loads(server.get_coded_segments(1))
        assert out["segments"][0]["memo"] == "coding"

    def test_delete_coding_echo_stripped(self, private_everywhere):
        out = server.delete_coding(1, create_backup=False)
        _assert_clean(out)
        assert json.loads(out)["success"] is True


class TestSearchDoesNotLeakPrivateZone:

    def test_search_memos_private_only_match_suppressed(
            self, private_everywhere):
        out = json.loads(server.search_memos(SECRET))
        assert out["result_count"] == 0
        assert out["results"] == []

    def test_search_memos_public_match_returns_public_only(
            self, private_everywhere):
        out = json.loads(server.search_memos("code"))
        # The MARKED row (cid 1, "code#####...") must itself be returned
        # with its public part; the unmarked "Coping code" row alone
        # would satisfy a bare count check (QA round 1, F7)
        marked = [r for r in out["results"]
                  if r["type"] == "code" and r["id"] == 1]
        assert len(marked) == 1
        assert marked[0]["memo"] == "code"
        _assert_clean(json.dumps(out))

    # -- QA round 1, F1: the cap is enforced AFTER the public-part check,
    # so a private-only LIKE hit never shadows a public match and the
    # result count never depends on private content (no count oracle)

    def test_public_match_at_higher_rowid_survives_limit_one(
            self, setup_server, qualcoder_db_path):
        _exec(qualcoder_db_path,
              "UPDATE code_name SET memo = ? WHERE cid = 1",
              ("x#####needle",))
        _exec(qualcoder_db_path,
              "UPDATE code_name SET memo = ? WHERE cid = 2",
              ("needle public",))
        _reopen(qualcoder_db_path)
        out = json.loads(server.search_memos("needle", limit=1))
        assert out["result_count"] == 1
        assert out["results"][0]["id"] == 2
        assert out["results"][0]["memo"] == "needle public"
        _assert_clean(json.dumps(out))

    def test_result_count_does_not_vary_with_private_content(
            self, setup_server, qualcoder_db_path):
        _exec(qualcoder_db_path,
              "UPDATE code_name SET memo = ? WHERE cid = 2",
              ("needle public",))
        outcomes = []
        for private_tail in ("nothing here", "needle"):
            _exec(qualcoder_db_path,
                  "UPDATE code_name SET memo = ? WHERE cid = 1",
                  (f"x#####{private_tail}",))
            _reopen(qualcoder_db_path)
            out = json.loads(server.search_memos("needle", limit=1))
            outcomes.append((out["result_count"],
                             [r["id"] for r in out["results"]]))
        assert outcomes[0] == outcomes[1] == (1, [2])

    def test_planted_probe_cannot_read_private_zone(
            self, setup_server, qualcoder_db_path):
        # The QA round 1 harness extracted a private secret character by
        # character: plant a probe memo carrying the query word, search
        # with limit = public_count + 1, and watch whether the probe
        # drops out (it did whenever a lower-rowid private zone also
        # matched). The probe must now appear either way.
        _exec(qualcoder_db_path,
              "UPDATE code_name SET memo = ? WHERE cid = 2",
              ("needle public",))
        _reopen(qualcoder_db_path)
        out = json.loads(server.create_code(
            "Probe", memo="needle probe", create_backup=False))
        assert out["success"] is True
        public_count = 1  # cid 2, before the probe
        outcomes = []
        for private_tail in ("needle", "no match"):
            _exec(qualcoder_db_path,
                  "UPDATE code_name SET memo = ? WHERE cid = 1",
                  (f"x#####{private_tail}",))
            _reopen(qualcoder_db_path)
            out = json.loads(server.search_memos("needle",
                                                 limit=public_count + 1))
            outcomes.append((out["result_count"],
                             sorted(r["id"] for r in out["results"])))
        assert outcomes[0] == outcomes[1]
        assert outcomes[0][0] == 2
        assert 2 in outcomes[0][1]
        # And result_count < limit still means the search was exhaustive
        out = json.loads(server.search_memos("needle", limit=10))
        assert out["result_count"] == 2

    def test_file_memo_section_caps_after_public_check(
            self, setup_server, qualcoder_db_path):
        _exec(qualcoder_db_path,
              "UPDATE source SET memo = ? WHERE id = 1", ("x#####needle",))
        _exec(qualcoder_db_path,
              "UPDATE source SET memo = ? WHERE id = 2", ("needle in file",))
        _reopen(qualcoder_db_path)
        out = json.loads(server.search_memos("needle", limit=1))
        assert [(r["type"], r["id"]) for r in out["results"]] == [("file", 2)]
        _assert_clean(json.dumps(out))

    def test_annotation_section_caps_after_public_check(
            self, setup_server, qualcoder_db_path):
        for pos0, memo in ((0, "x#####needle"), (5, "needle note")):
            _exec(qualcoder_db_path,
                  "INSERT INTO annotation (fid, pos0, pos1, memo, owner, "
                  "date) VALUES (1, ?, ?, ?, 'TestCoder', '2024-01-15')",
                  (pos0, pos0 + 4, memo))
        _reopen(qualcoder_db_path)
        out = json.loads(server.search_memos("needle", limit=1))
        assert ([(r["type"], r["memo"]) for r in out["results"]]
                == [("annotation", "needle note")])
        _assert_clean(json.dumps(out))

    def test_search_files_memo_private_only_match_suppressed(
            self, private_everywhere):
        out = json.loads(server.search_files(
            SECRET, search_filename=False, search_memo=True))
        assert out["total_matches"] == 0

    def test_search_files_memo_preview_is_public(self, private_everywhere):
        out = json.loads(server.search_files(
            "file", search_filename=False, search_memo=True))
        assert out["total_matches"] == 1
        _assert_clean(json.dumps(out))


# =============================================================================
# EXPORT EXCEPTION: files keep full memos (QualCoder export parity)
# =============================================================================

class TestExportsKeepFullMemos:

    def test_codebook_export_carries_private_suffix(self, private_everywhere,
                                                    tmp_path):
        out_file = tmp_path / "codebook.csv"
        out = json.loads(server.export_codebook(str(out_file)))
        assert out.get("success") is True, out
        content = out_file.read_text(encoding="utf-8-sig")
        assert SECRET in content

    def test_coded_segments_report_carries_private_suffix(
            self, private_everywhere, tmp_path):
        out_file = tmp_path / "report.csv"
        out = json.loads(server.export_coded_segments_report(str(out_file)))
        assert out.get("success") is True, out
        content = out_file.read_text(encoding="utf-8-sig")
        assert SECRET in content

    def test_refi_export_carries_private_suffix(self, private_everywhere,
                                                tmp_path):
        out_file = tmp_path / "project.qdpx"
        out = json.loads(server.export_refi_qda(output_path=str(out_file)))
        assert out.get("success") is True, out
        import zipfile
        with zipfile.ZipFile(out_file) as zf:
            qde = zf.read("project.qde").decode("utf-8")
        assert SECRET in qde


class TestExportDescriptionsDisclose:

    def test_file_exports_disclose_full_memos(self):
        for tool in (server.export_refi_qda, server.export_codebook,
                     server.export_coded_segments_report):
            doc = tool.__doc__ or ""
            assert "Full memos on export" in doc, tool.__name__
            assert "#####" in doc, tool.__name__

    def test_export_code_report_discloses_strip_and_no_override(self):
        # It returns into the conversation and strips; the description
        # must say so, symmetrically with its file-export siblings
        # (QA round 1, F4)
        doc = server.export_code_report.__doc__ or ""
        assert "#####" in doc
        assert "public part only" in doc
        assert "no coder override" in doc
