"""QA v0.8 gate — surface 5: D1/D2 write completions.

Annotations (overlap/empty/anid semantics + REFI-born tolerance),
merge_category differential vs the dossier §9 recipe + cycle refusal,
create_case/import back-fill exactness (single-domain filter),
create_attribute_type + unified set_attribute domain rules, and the two
existing-code fixes (CAST-''-as-0 numeric bug; overlap-aware case-link
dedupe across both pos1 conventions).
"""

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server


FULLTEXT = "This is interview text. I feel stressed about deadlines. I cope by exercising."


def _db(p) -> Path:
    return Path(p) / "data.qda"


def _exec(p, sql, args=()):
    conn = sqlite3.connect(str(_db(p)))
    conn.execute(sql, args)
    conn.commit()
    conn.close()


def _row(p, sql, args=()):
    conn = sqlite3.connect(str(_db(p)))
    conn.row_factory = sqlite3.Row
    r = conn.execute(sql, args).fetchone()
    conn.close()
    return r


def _rows(p, sql, args=()):
    conn = sqlite3.connect(str(_db(p)))
    conn.row_factory = sqlite3.Row
    rs = conn.execute(sql, args).fetchall()
    conn.close()
    return rs


def _dump(p):
    conn = sqlite3.connect(str(_db(p)))
    lines = list(conn.iterdump())
    conn.close()
    return lines


def _reload():
    server.switch_project(server.current_project_path)


# =============================================================================
# D.1-D.3 Annotations
# =============================================================================

class TestAnnotations:

    def test_add_requires_nonempty_and_valid_span(self, setup_server,
                                                  qualcoder_db_path):
        assert "error" in json.loads(server.add_annotation(1, 0, 10, ""))
        assert "error" in json.loads(server.add_annotation(1, 0, 10, "   "))
        assert "error" in json.loads(
            server.add_annotation(1, 10, 10, "zero length"))
        assert "error" in json.loads(
            server.add_annotation(1, 0, len(FULLTEXT) + 5, "oob"))
        out = json.loads(server.add_annotation(1, 0, 10, "a real note"))
        assert out.get("success") is True, out
        row = _row(qualcoder_db_path,
                   "SELECT * FROM annotation WHERE memo = 'a real note'")
        assert row["owner"] == "AI Coding Assistant" and row["date"]

    def test_same_coder_overlap_refused_naming_anid(self, setup_server,
                                                    qualcoder_db_path):
        first = json.loads(server.add_annotation(1, 0, 20, "first note"))
        anid = first["annotation"]["annotation_id"] if "annotation" in first \
            else _row(qualcoder_db_path,
                      "SELECT anid FROM annotation WHERE memo='first note'")["anid"]
        # overlapping span, same coder -> refused, existing anid named
        out = json.loads(server.add_annotation(1, 10, 30, "overlap"))
        assert "error" in out
        assert str(anid) in json.dumps(out)
        # a DIFFERENT coder's overlapping row (via SQL) must not block a new
        # non-overlapping same-coder note
        _exec(qualcoder_db_path,
              "INSERT INTO annotation (fid, pos0, pos1, memo, owner, date) "
              "VALUES (1, 0, 20, 'other coder note', 'someone_else', '2024')")
        _reload()
        ok = json.loads(server.add_annotation(1, 40, 50, "disjoint note"))
        assert ok.get("success") is True, ok

    def test_update_by_anid_bumps_date_keeps_owner_empty_deletes(
            self, setup_server, qualcoder_db_path):
        json.loads(server.add_annotation(1, 0, 10, "note body"))
        row = _row(qualcoder_db_path,
                   "SELECT * FROM annotation WHERE memo='note body'")
        anid = row["anid"]
        # backdate so the date bump is observable
        _exec(qualcoder_db_path,
              "UPDATE annotation SET date='2020-01-01 00:00:00' WHERE anid=?",
              (anid,))
        _reload()
        out = json.loads(server.update_annotation(anid, "edited body"))
        assert out.get("success") is True
        after = _row(qualcoder_db_path,
                     "SELECT * FROM annotation WHERE anid=?", (anid,))
        assert after["memo"] == "edited body"
        assert after["date"] != "2020-01-01 00:00:00"     # date IS bumped
        assert after["owner"] == row["owner"]             # owner untouched

        # clearing DELETES the row (QualCoder-faithful), response says so
        out = json.loads(server.update_annotation(anid, ""))
        blob = json.dumps(out).lower()
        assert "delet" in blob
        assert _row(qualcoder_db_path,
                    "SELECT anid FROM annotation WHERE anid=?", (anid,)) is None

    def test_delete_by_anid_and_refi_born_empty_tolerance(
            self, setup_server, qualcoder_db_path):
        json.loads(server.add_annotation(1, 0, 10, "to delete"))
        anid = _row(qualcoder_db_path,
                    "SELECT anid FROM annotation WHERE memo='to delete'")["anid"]
        # a second annotation sharing pos0 in another file must SURVIVE the
        # delete (anid-keyed, never the upstream pos0 bug)
        _exec(qualcoder_db_path,
              "INSERT INTO source (id, name, fulltext) VALUES (30, 'b.txt', ?)",
              ("Other file text for the pos0 collision test.",))
        _exec(qualcoder_db_path,
              "INSERT INTO annotation (fid, pos0, pos1, memo, owner, date) "
              "VALUES (30, 0, 10, 'same pos0 other file', 'TestCoder', '2024')")
        # REFI-born empty-memo rows exist in real projects: reads must cope
        _exec(qualcoder_db_path,
              "INSERT INTO annotation (fid, pos0, pos1, memo, owner, date) "
              "VALUES (1, 60, 70, '', 'importer', '2024')")
        _reload()
        out = json.loads(server.delete_annotation(anid))
        assert out.get("success") is True, out
        assert _row(qualcoder_db_path,
                    "SELECT COUNT(*) AS n FROM annotation "
                    "WHERE memo='same pos0 other file'")["n"] == 1
        assert "error" not in json.loads(server.analyze_file_with_coding(1))
        assert "error" not in json.loads(server.search_memos("note"))
        assert "error" in json.loads(server.delete_annotation(424242))


# =============================================================================
# D.4 merge_category — differential + cycle refusal
# =============================================================================

class TestMergeCategory:

    def _tree(self, p):
        """Top(1, fixture) ; Src(2) with code1 + subcat(3, holding code2);
        Other(4) as merge target."""
        _exec(p, "INSERT INTO code_cat (catid, name, supercatid) VALUES (2, 'Src', NULL)")
        _exec(p, "INSERT INTO code_cat (catid, name, supercatid) VALUES (3, 'Subcat', 2)")
        _exec(p, "INSERT INTO code_cat (catid, name, supercatid) VALUES (4, 'Other', NULL)")
        _exec(p, "UPDATE code_name SET catid = 2 WHERE cid = 1")
        _exec(p, "UPDATE code_name SET catid = 3 WHERE cid = 2")
        _reload()

    def test_differential_vs_dossier_recipe(self, setup_server,
                                            qualcoder_db_path, tmp_path):
        self._tree(qualcoder_db_path)
        twin = tmp_path / "twin.qda"
        shutil.copytree(qualcoder_db_path, twin)

        pv = json.loads(server.merge_category(2, "Other"))
        assert pv["requires_confirmation"] is True
        out = json.loads(server.merge_category(2, "Other", confirm=True))
        assert out.get("success") is True, out

        # category-tree.md §9 recipe on the twin
        conn = sqlite3.connect(str(twin / "data.qda"))
        cur = conn.cursor()
        cur.execute("UPDATE code_name SET catid=? WHERE catid=?", (4, 2))
        cur.execute("UPDATE code_cat SET supercatid=? WHERE supercatid=?", (4, 2))
        cur.execute("DELETE FROM code_cat WHERE catid=?", (2,))
        cur.execute("UPDATE code_cat SET supercatid=NULL WHERE supercatid IS "
                    "NOT NULL AND supercatid NOT IN (SELECT catid FROM code_cat)")
        conn.commit()
        conn.close()

        a = [l for l in _dump(qualcoder_db_path) if "code_cat" in l or "code_name" in l]
        b = [l for l in list(sqlite3.connect(str(twin / "data.qda")).iterdump())
             if "code_cat" in l or "code_name" in l]
        assert a == b
        # semantics: code1 -> target cat; Subcat -> target; codings untouched
        assert _row(qualcoder_db_path,
                    "SELECT catid FROM code_name WHERE cid=1")["catid"] == 4
        assert _row(qualcoder_db_path,
                    "SELECT supercatid FROM code_cat WHERE catid=3")["supercatid"] == 4
        assert _row(qualcoder_db_path,
                    "SELECT COUNT(*) AS n FROM code_text")["n"] == 2

    def test_cycle_refusal_and_top_level_target(self, setup_server,
                                                qualcoder_db_path):
        self._tree(qualcoder_db_path)
        # target is the source's own descendant -> refuse
        out = json.loads(server.merge_category(2, "Subcat", confirm=True))
        assert "error" in out
        # target == source -> refuse
        assert "error" in json.loads(server.merge_category(2, "Src", confirm=True))
        # None target -> everything to top level
        out = json.loads(server.merge_category(2, confirm=True))
        assert out.get("success") is True, out
        assert _row(qualcoder_db_path,
                    "SELECT catid FROM code_name WHERE cid=1")["catid"] is None
        assert _row(qualcoder_db_path,
                    "SELECT supercatid FROM code_cat WHERE catid=3")["supercatid"] is None


# =============================================================================
# D.5 create_case + back-fill exactness
# =============================================================================

class TestCreateCaseAndBackfill:

    def test_row_shape_and_single_domain_backfill(self, setup_server,
                                                  qualcoder_db_path):
        # a 'case' type must be back-filled; a legacy 'both' type must NOT
        # (the IN (...,'both') superset was removed to match upstream)
        _exec(qualcoder_db_path,
              "INSERT INTO attribute_type VALUES ('CaseAttr','2024','T','','case','character')")
        _exec(qualcoder_db_path,
              "INSERT INTO attribute_type VALUES ('LegacyBoth','2024','T','','both','character')")
        _reload()
        out = json.loads(server.create_case("Participant X", memo=None))
        assert out.get("success") is True, out
        row = _row(qualcoder_db_path,
                   "SELECT * FROM cases WHERE name='Participant X'")
        assert row["memo"] == ""                          # '' never NULL
        assert row["owner"] == "AI Coding Assistant"  # P1-2 attribution
        caseid = row["caseid"]
        atts = {r["name"] for r in _rows(
            qualcoder_db_path,
            "SELECT name FROM attribute WHERE attr_type='case' AND id=?",
            (caseid,))}
        assert "CaseAttr" in atts and "Age" in atts
        assert "LegacyBoth" not in atts                   # exact-domain filter
        # placeholder value is '' (never NULL)
        assert _row(qualcoder_db_path,
                    "SELECT value FROM attribute WHERE name='CaseAttr' AND id=?",
                    (caseid,))["value"] == ""
        # duplicate name refused
        assert "error" in json.loads(server.create_case("participant x")) or \
            "error" in json.loads(server.create_case("Participant X"))

    def test_import_backfill_file_domain_only(self, setup_server,
                                              qualcoder_db_path):
        _exec(qualcoder_db_path,
              "INSERT INTO attribute_type VALUES ('FileAttr','2024','T','','file','character')")
        _exec(qualcoder_db_path,
              "INSERT INTO attribute_type VALUES ('BothAttr','2024','T','','both','character')")
        _reload()
        out = json.loads(server.import_text_file("bf.txt", "content",
                                                 create_backup=False))
        fid = out["file_id"]
        atts = {r["name"] for r in _rows(
            qualcoder_db_path,
            "SELECT name FROM attribute WHERE attr_type='file' AND id=?",
            (fid,))}
        assert "FileAttr" in atts
        assert "BothAttr" not in atts                     # superset removed


# =============================================================================
# D.6-D.8 Attributes
# =============================================================================

class TestAttributeType:

    def test_domains_and_reserved_names(self, setup_server, qualcoder_db_path):
        assert "error" in json.loads(
            server.create_attribute_type("X", "both"))     # 'both' rejected
        assert "error" in json.loads(
            server.create_attribute_type("X", "bogus"))
        for reserved in ("Ref_Author", "Ref_Authors", "Ref_Type", "Ref_Title",
                         "Ref_Year", "Ref_Journal"):
            assert "error" in json.loads(
                server.create_attribute_type(reserved, "case")), reserved
        assert "error" in json.loads(
            server.create_attribute_type("X", "case", value_type="date"))

    def test_journal_domain_with_backfill(self, setup_server,
                                          qualcoder_db_path):
        out = json.loads(server.create_attribute_type("Mood", "journal"))
        assert out.get("success") is True, out
        # one placeholder per existing journal (fixture has jid=1), value=''
        ph = _rows(qualcoder_db_path,
                   "SELECT * FROM attribute WHERE name='Mood'")
        assert len(ph) == 1 and ph[0]["attr_type"] == "journal" \
            and ph[0]["value"] == ""

    def test_global_namespace_collision_names_domain(self, setup_server,
                                                     qualcoder_db_path):
        assert json.loads(server.create_attribute_type("Shared", "file")
                          ).get("success") is True
        out = json.loads(server.create_attribute_type("Shared", "case"))
        assert "error" in out
        assert "file" in out["error"]                     # colliding domain named

    def test_case_backfill_on_create(self, setup_server, qualcoder_db_path):
        out = json.loads(server.create_attribute_type("NewCase", "case"))
        assert out.get("success") is True
        ph = _rows(qualcoder_db_path,
                   "SELECT * FROM attribute WHERE name='NewCase'")
        assert len(ph) == 1 and ph[0]["id"] == 1          # fixture case 1


class TestSetAttribute:

    def test_byte_fidelity_per_domain(self, setup_server, qualcoder_db_path):
        """Case path updates value+date+owner; file path updates value ONLY."""
        _exec(qualcoder_db_path,
              "INSERT INTO attribute_type VALUES ('FA','2024','T','','file','character')")
        _exec(qualcoder_db_path,
              "INSERT INTO attribute VALUES (50,'FA','file','old',1,"
              "'2020-01-01 00:00:00','orig_owner')")
        _reload()
        out = json.loads(server.set_attribute("file", 1, "FA", "  new  "))
        assert out.get("success") is True, out
        row = _row(qualcoder_db_path,
                   "SELECT * FROM attribute WHERE attrid=50")
        assert row["value"] == "new"                      # stripped
        assert row["date"] == "2020-01-01 00:00:00"       # file: value ONLY
        assert row["owner"] == "orig_owner"

        # case path: value + date + owner refreshed
        out = json.loads(server.set_attribute("case", 1, "Age", "31"))
        assert out.get("success") is True, out
        row = _row(qualcoder_db_path,
                   "SELECT * FROM attribute WHERE name='Age' AND id=1 "
                   "AND attr_type='case'")
        assert row["value"] == "31"
        assert row["date"] != "2024-01-15 10:00:00"       # bumped
        assert row["owner"] == "AI Coding Assistant"  # P1-2 attribution

    def test_insert_if_missing_and_domain_gates(self, setup_server,
                                                qualcoder_db_path):
        _exec(qualcoder_db_path,
              "DELETE FROM attribute WHERE name='Age'")   # placeholder gone
        _reload()
        out = json.loads(server.set_attribute("case", 1, "Age", "44"))
        assert out.get("success") is True                 # insert-if-missing
        assert _row(qualcoder_db_path,
                    "SELECT value FROM attribute WHERE name='Age' AND id=1"
                    )["value"] == "44"
        # cross-domain refusal: Age is a case attribute
        out = json.loads(server.set_attribute("file", 1, "Age", "44"))
        assert "error" in out
        # unknown attribute / entity
        assert "error" in json.loads(server.set_attribute("case", 1, "Nope", "1"))
        assert "error" in json.loads(server.set_attribute("case", 999, "Age", "1"))
        assert "error" in json.loads(server.set_attribute("code", 1, "Age", "1"))

    def test_numeric_gate_float_semantics(self, setup_server,
                                          qualcoder_db_path):
        # Age is numeric in the fixture
        for ok_val in ("1e3", "nan", "inf", "-2.5", ""):
            out = json.loads(server.set_attribute("case", 1, "Age", ok_val))
            assert out.get("success") is True, (ok_val, out)
        out = json.loads(server.set_attribute("case", 1, "Age", "abc"))
        assert "error" in out                             # refused, not blanked
        # the refusal left the previous value intact ('' from the loop above)
        assert _row(qualcoder_db_path,
                    "SELECT value FROM attribute WHERE name='Age' AND id=1"
                    )["value"] == ""


class TestExistingCodeFixes:

    def test_cast_empty_as_zero_bug_fixed(self, setup_server,
                                          qualcoder_db_path):
        """Numeric gt/lt must EXCLUDE ''-unset rows; equals compares
        numerically ('5' finds '5.0'); equals '' finds unset."""
        _exec(qualcoder_db_path,
              "INSERT INTO cases VALUES (2, 'Case B', '', 'T', '2024')")
        _exec(qualcoder_db_path,
              "INSERT INTO cases VALUES (3, 'Case C', '', 'T', '2024')")
        _exec(qualcoder_db_path,
              "INSERT INTO attribute VALUES (60,'Age','case','',2,'2024','T')")
        _exec(qualcoder_db_path,
              "INSERT INTO attribute VALUES (61,'Age','case','5.0',3,'2024','T')")
        _reload()
        # gt -10: the ''-unset case must NOT match (CAST('')=0.0 bug)
        out = json.loads(server.query_by_attribute("Age", "-10", operator="gt"))
        ids = {m.get("case_id") for m in out}
        assert 2 not in ids
        assert {1, 3} <= ids
        # numeric equals: '5' finds the '5.0' row
        out = json.loads(server.query_by_attribute("Age", "5", operator="equals"))
        assert {m.get("case_id") for m in out} == {3}
        # equals '' keeps string semantics: finds the unset row
        out = json.loads(server.query_by_attribute("Age", "", operator="equals"))
        assert 2 in {m.get("case_id") for m in out}

    def test_case_link_dedupe_across_both_conventions(self, setup_server,
                                                      qualcoder_db_path):
        """A pre-existing whole-file link stored with the OTHER convention
        (pos1 = len, not len-1) must still be detected as a duplicate."""
        notes_len = len(_row(qualcoder_db_path,
                             "SELECT fulltext FROM source WHERE id=2")["fulltext"])
        _exec(qualcoder_db_path,
              "INSERT INTO case_text (caseid, fid, pos0, pos1, owner, date, memo) "
              "VALUES (1, 2, 0, ?, 'T', '2024', '')", (notes_len,))  # len convention
        _reload()
        before = _row(qualcoder_db_path,
                      "SELECT COUNT(*) AS n FROM case_text")["n"]
        out = json.loads(server.link_file_to_case(2, case_id=1))
        assert "error" in out, out
        assert "already linked" in out["error"] or "convention" in out["error"]
        assert _row(qualcoder_db_path,
                    "SELECT COUNT(*) AS n FROM case_text")["n"] == before
