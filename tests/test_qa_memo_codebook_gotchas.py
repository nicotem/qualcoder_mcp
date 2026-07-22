"""QA adversarial round: INDEPENDENT verification of the 18 groundtruth2
gotchas (memos-journals.md §7 x8 + code-edits.md §11 x10), re-derived from
the dossiers — not from the developer's tests — plus QualCoder-fidelity
DIFFERENTIAL tests: the MCP op on one project vs QualCoder 3.8.2's exact SQL
recipe hand-executed on a byte-identical twin, compared dump-for-dump.

Gotcha IDs used here:
  M1-M8  = memos-journals.md §7 items 1-8
  C1-C10 = code-edits.md §11 items 1-10
"""

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server
from qualcoder_mcp.database import QualcoderDatabase, QUALCODER_COLORS


FULLTEXT = "This is interview text. I feel stressed about deadlines. I cope by exercising."


def _db(project_path) -> Path:
    return Path(project_path) / "data.qda"


def _exec(project_path, sql, args=()):
    conn = sqlite3.connect(str(_db(project_path)))
    conn.execute(sql, args)
    conn.commit()
    conn.close()


def _row(project_path, sql, args=()):
    conn = sqlite3.connect(str(_db(project_path)))
    conn.row_factory = sqlite3.Row
    r = conn.execute(sql, args).fetchone()
    conn.close()
    return r


def _rows(project_path, sql, args=()):
    conn = sqlite3.connect(str(_db(project_path)))
    conn.row_factory = sqlite3.Row
    rs = conn.execute(sql, args).fetchall()
    conn.close()
    return rs


def _dump(project_path) -> list:
    """Deterministic logical dump for twin comparison."""
    conn = sqlite3.connect(str(_db(project_path)))
    lines = list(conn.iterdump())
    conn.close()
    return lines


def _reload():
    server.switch_project(server.current_project_path)


# =============================================================================
# M1-M8 — memo/journal gotchas
# =============================================================================

class TestMemoGotchas:

    MEMO_FIXTURES = [
        # (target_type, table, id_col, target_id)
        ("code", "code_name", "cid", 1),
        ("category", "code_cat", "catid", 1),
        ("file", "source", "id", 1),
        ("coding", "code_text", "ctid", 1),
        ("case", "cases", "caseid", 1),
    ]

    @pytest.mark.parametrize("target_type,table,id_col,target_id", MEMO_FIXTURES)
    def test_m1_m5_memo_edit_byte_preserves_every_other_column(
            self, setup_server, qualcoder_db_path,
            target_type, table, id_col, target_id):
        """M1 (coding date untouched) + M5 (owner immutable) generalized:
        a memo edit must change the memo column and NOTHING else,
        byte-for-byte, on every target type."""
        before = dict(_row(qualcoder_db_path,
                           f"SELECT * FROM {table} WHERE {id_col} = ?",
                           (target_id,)))
        out = json.loads(server.set_memo(target_type, target_id,
                                         "independent QA memo"))
        assert out.get("success") is True, out
        after = dict(_row(qualcoder_db_path,
                          f"SELECT * FROM {table} WHERE {id_col} = ?",
                          (target_id,)))
        assert after.pop("memo") == "independent QA memo"
        before.pop("memo")
        assert after == before  # date, owner, and every other column identical

    def test_m1_add_memo_to_coding_no_longer_stamps_date(self, qualcoder_db_path):
        """M1, db layer: the pre-existing date-stamping bug is gone."""
        before = dict(_row(qualcoder_db_path,
                           "SELECT * FROM code_text WHERE ctid = 1"))
        wdb = QualcoderDatabase(qualcoder_db_path, read_only=False)
        try:
            wdb.add_memo_to_coding(1, "db-layer memo", "whoever")
        finally:
            wdb.close()
        after = dict(_row(qualcoder_db_path,
                          "SELECT * FROM code_text WHERE ctid = 1"))
        assert after["date"] == before["date"]
        assert after["owner"] == before["owner"]
        assert after["memo"] == "db-layer memo"

    def test_m2_code_av_memo_target_not_exposed(self, setup_server):
        """M2: code_av is the one date-on-edit exception upstream; the tool
        deliberately does not expose it — the error must not accept any
        av-flavoured target."""
        for target in ("av", "code_av", "av_coding", "image"):
            out = json.loads(server.set_memo(target, 1, "x"))
            assert "error" in out
            assert "target_type must be one of" in out["error"]
        # and the valid list is exactly the five contract targets
        out = json.loads(server.set_memo("bogus", 1, "x"))
        for t in ("code", "category", "file", "coding", "case"):
            assert t in out["error"]

    def test_m3_no_annotation_write_surface(self):
        """M3: annotations (insert-only-nonempty / clear-deletes) are out of
        scope — no annotation write tool may exist on the surface."""
        import asyncio
        names = {t.name for t in asyncio.run(server.mcp.list_tools())}
        assert not any("annotation" in n for n in names)
        assert len(names) == 49

    @pytest.mark.parametrize("target_type,table,id_col,target_id", MEMO_FIXTURES)
    def test_m4_clear_stores_empty_string_never_null(
            self, setup_server, qualcoder_db_path,
            target_type, table, id_col, target_id):
        json.loads(server.set_memo(target_type, target_id, "something"))
        out = json.loads(server.set_memo(target_type, target_id, ""))
        assert out.get("cleared") is True
        r = _row(qualcoder_db_path,
                 f"SELECT memo, memo IS NULL AS is_null FROM {table} "
                 f"WHERE {id_col} = ?", (target_id,))
        assert r["is_null"] == 0
        assert r["memo"] == ""

    def test_m6_journal_name_charset_unique_and_no_row_per_edit(
            self, setup_server, qualcoder_db_path):
        """M6: name regex ^[ \\w-]+$ enforced, uniqueness pre-checked; and
        the tool never creates a second row for the same name."""
        ok = json.loads(server.add_journal_entry("Week 1 - reflections_A", "body"))
        assert ok.get("success") is True, ok
        for bad in ("bad/name", "dots.dots", "semi;colon", "a\tb", "q'uote"):
            out = json.loads(server.add_journal_entry(bad, "body"))
            assert "error" in out, bad
        dup = json.loads(server.add_journal_entry("Week 1 - reflections_A", "again"))
        assert "already exists" in dup["error"]
        n = _row(qualcoder_db_path,
                 "SELECT COUNT(*) AS n FROM journal WHERE name = ?",
                 ("Week 1 - reflections_A",))["n"]
        assert n == 1
        # owner attribution = the project's codername (contract)
        r = _row(qualcoder_db_path,
                 "SELECT owner, jentry FROM journal WHERE name = ?",
                 ("Week 1 - reflections_A",))
        assert r["owner"] == "TestCoder"
        assert r["jentry"] == "body"

    def test_m7_coding_memo_targets_by_ctid_not_position(
            self, setup_server, qualcoder_db_path):
        """M7: two codings sharing pos0 (and a U+2029-bearing seltext) must
        not confuse the write — set_memo keys on ctid only."""
        # second coding at the same pos0 as ctid 1, different code, U+2029 text
        _exec(qualcoder_db_path,
              "INSERT INTO code_text (ctid, cid, fid, seltext, pos0, pos1, owner) "
              "VALUES (77, 2, 1, ?, 24, 40, 'gui_user')",
              (FULLTEXT[24:40].replace(" ", " "),))
        _reload()
        out = json.loads(server.set_memo("coding", 77, "only on 77"))
        assert out.get("success") is True
        assert _row(qualcoder_db_path,
                    "SELECT memo FROM code_text WHERE ctid = 77")["memo"] == "only on 77"
        assert _row(qualcoder_db_path,
                    "SELECT memo FROM code_text WHERE ctid = 1")["memo"] == "key passage"

    def test_m8_no_silent_truncation_50k_intact_1m_rejected(
            self, setup_server, qualcoder_db_path):
        """M8: 50k-char memo stored INTACT; >1e6 cleanly rejected, row
        unchanged. NEVER truncated. Applies to set_memo, journal entries,
        and the create_code/create_category memo params (which pass through
        validate_string whose truncated return value must NOT be used)."""
        big = ("x" * 49_999) + "Z"
        out = json.loads(server.set_memo("code", 1, big))
        assert out.get("success") is True
        stored = _row(qualcoder_db_path,
                      "SELECT memo FROM code_name WHERE cid = 1")["memo"]
        assert len(stored) == 50_000 and stored.endswith("Z")  # intact

        too_big = "y" * 1_000_001
        out = json.loads(server.set_memo("code", 1, too_big))
        assert "error" in out and "too long" in out["error"]
        assert _row(qualcoder_db_path,
                    "SELECT memo FROM code_name WHERE cid = 1")["memo"] == big

        # journal: 50k intact, >1e6 rejected
        ok = json.loads(server.add_journal_entry("Long journal", big))
        assert ok.get("success") is True
        assert len(_row(qualcoder_db_path,
                        "SELECT jentry FROM journal WHERE name='Long journal'")
                   ["jentry"]) == 50_000
        out = json.loads(server.add_journal_entry("Too long", too_big))
        assert "error" in out and "too long" in out["error"]

        # create_code/create_category memo params: 50k must survive intact
        # (validate_string() truncates its RETURN value at 10k — the stored
        # value must be the caller's original, not the truncated copy)
        out = json.loads(server.create_code("QA long memo code", memo=big))
        assert out.get("success") is True, out
        cid = out["code"]["id"]
        assert len(_row(qualcoder_db_path,
                        "SELECT memo FROM code_name WHERE cid = ?",
                        (cid,))["memo"]) == 50_000
        out = json.loads(server.create_category("QA long memo cat", memo=big))
        assert out.get("success") is True, out
        assert len(_row(qualcoder_db_path,
                        "SELECT memo FROM code_cat WHERE name = 'QA long memo cat'")
                   ["memo"]) == 50_000


# =============================================================================
# C1-C10 — code-edit gotchas (independent probes)
# =============================================================================

def _seed_merge_fixture(project_path):
    """Codes 1 (source) and 2 (dest) with: a same-owner collision where ONLY
    the source row carries memo/important, a no-collision reassign, a
    different-owner same-span pair, plus identical-coordinate av and image
    rows under both codes, dangling-ref tables, and recently_used_codes."""
    conn = sqlite3.connect(str(_db(project_path)))
    c = conn.cursor()
    c.execute("DELETE FROM code_text")
    rows = [
        # collision pair: same (fid,pos0,pos1,owner) under both codes
        (10, 1, 1, FULLTEXT[10:20], 10, 20, "ownerX", "2024-01-01 00:00:00",
         "SOURCE MEMO to lose", 1),
        (11, 2, 1, FULLTEXT[10:20], 10, 20, "ownerX", "2024-01-02 00:00:00",
         "", None),
        # source-only span -> plain reassign, ctid preserved
        (12, 1, 1, FULLTEXT[30:40], 30, 40, "ownerX", "2024-01-03 00:00:00",
         "keep me", None),
        # same span, DIFFERENT owner -> both must survive under dest
        (13, 1, 1, FULLTEXT[10:20], 10, 20, "ownerY", "2024-01-04 00:00:00",
         "other coder", None),
    ]
    c.executemany(
        "INSERT INTO code_text (ctid,cid,fid,seltext,pos0,pos1,owner,date,memo,important) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    # identical-coordinate AV segments under both codes (no unique constraint)
    c.execute("INSERT INTO code_av (avid,cid,id,pos0,pos1,memo,owner,date,important) "
              "VALUES (20,1,1,0,5000,'src av','ownerX','2024-01-01 00:00:00',0)")
    c.execute("INSERT INTO code_av (avid,cid,id,pos0,pos1,memo,owner,date,important) "
              "VALUES (21,2,1,0,5000,'dst av','ownerX','2024-01-01 00:00:00',0)")
    # identical-region image codings under both codes
    c.execute("INSERT INTO code_image (imid,id,x1,y1,width,height,cid,memo,date,owner,important,pdf_page) "
              "VALUES (30,1,1,1,10,10,1,'src img','2024-01-01 00:00:00','ownerX',NULL,NULL)")
    c.execute("INSERT INTO code_image (imid,id,x1,y1,width,height,cid,memo,date,owner,important,pdf_page) "
              "VALUES (31,1,1,1,10,10,2,'dst img','2024-01-01 00:00:00','ownerX',NULL,NULL)")
    # dangling-ref surfaces that must be LEFT ALONE (gotcha C10)
    c.execute("CREATE TABLE IF NOT EXISTS gr_cdct_text_item "
              "(gtextid integer primary key, gid integer, cid integer)")
    c.execute("INSERT INTO gr_cdct_text_item VALUES (1, 1, 1)")
    c.execute("CREATE TABLE IF NOT EXISTS gr_free_text_item "
              "(gfreeid integer primary key, gid integer, ctid integer)")
    c.execute("INSERT INTO gr_free_text_item VALUES (1, 1, 10)")
    c.execute("UPDATE project SET recently_used_codes = '1 2'")
    conn.commit()
    conn.close()


class TestMergeGotchas:

    def test_c1_c2_c3_c5_merge_semantics(self, setup_server, qualcoder_db_path):
        _seed_merge_fixture(qualcoder_db_path)
        _reload()

        preview = json.loads(server.merge_codes(1, 2))
        assert preview["requires_confirmation"] is True
        p = preview["preview"]
        assert p["text_codings_reassigned"] == 2       # ctid 12 + 13
        assert p["text_codings_discarded_as_duplicates"] == 1  # ctid 10
        assert p["av_codings_reassigned"] == 1
        assert p["image_codings_reassigned"] == 1

        out = json.loads(server.merge_codes(1, 2, confirm=True))
        assert out.get("success") is True, out

        # C2: destination wins — the surviving row at (1,10,20,ownerX) is the
        # DEST row (ctid 11) with its empty memo/NULL important; the source
        # memo and important flag are gone, not merged
        survivors = _rows(qualcoder_db_path,
                          "SELECT * FROM code_text WHERE fid=1 AND pos0=10 "
                          "AND pos1=20 AND owner='ownerX'")
        assert len(survivors) == 1
        s = dict(survivors[0])
        assert s["ctid"] == 11 and s["cid"] == 2
        assert s["memo"] == "" and s["important"] is None
        assert not _rows(qualcoder_db_path,
                         "SELECT 1 FROM code_text WHERE ctid = 10")

        # C3: different-owner same-span row survived (owner in the dedup key)
        assert _row(qualcoder_db_path,
                    "SELECT cid FROM code_text WHERE ctid = 13")["cid"] == 2
        # plain reassign preserved ctid and every column but cid
        r12 = dict(_row(qualcoder_db_path,
                        "SELECT * FROM code_text WHERE ctid = 12"))
        assert r12["cid"] == 2 and r12["memo"] == "keep me"
        assert r12["date"] == "2024-01-03 00:00:00"

        # C1: av/image NOT deduplicated — two identical-coordinate rows each
        avs = _rows(qualcoder_db_path,
                    "SELECT avid FROM code_av WHERE cid=2 AND id=1 "
                    "AND pos0=0 AND pos1=5000 AND owner='ownerX'")
        assert {r["avid"] for r in avs} == {20, 21}
        imgs = _rows(qualcoder_db_path,
                     "SELECT imid FROM code_image WHERE cid=2 AND x1=1 AND y1=1")
        assert {r["imid"] for r in imgs} == {30, 31}

        # C5: the source code definition is gone; destination intact
        assert not _rows(qualcoder_db_path,
                         "SELECT 1 FROM code_name WHERE cid = 1")
        assert _row(qualcoder_db_path,
                    "SELECT name FROM code_name WHERE cid = 2")["name"] == "Coping"

    def test_c10_merge_and_delete_leave_dangling_refs_alone(
            self, setup_server, qualcoder_db_path):
        _seed_merge_fixture(qualcoder_db_path)
        _reload()
        json.loads(server.merge_codes(1, 2, confirm=True))
        # gr_* rows and recently_used_codes untouched (still referencing the
        # merged-away cid/deleted ctid — matching QualCoder's dangling policy)
        assert _row(qualcoder_db_path,
                    "SELECT cid FROM gr_cdct_text_item WHERE gtextid=1")["cid"] == 1
        assert _row(qualcoder_db_path,
                    "SELECT ctid FROM gr_free_text_item WHERE gfreeid=1")["ctid"] == 10
        assert _row(qualcoder_db_path,
                    "SELECT recently_used_codes AS r FROM project")["r"] == "1 2"

        out = json.loads(server.delete_code(2, confirm=True))
        assert out.get("success") is True
        assert _row(qualcoder_db_path,
                    "SELECT cid FROM gr_cdct_text_item WHERE gtextid=1")["cid"] == 1
        assert _row(qualcoder_db_path,
                    "SELECT recently_used_codes AS r FROM project")["r"] == "1 2"

    def test_c4_merge_all_or_nothing_on_late_failure(
            self, setup_server, qualcoder_db_path, monkeypatch):
        """C4: any hard error inside the write leaves the DB untouched."""
        _seed_merge_fixture(qualcoder_db_path)
        _reload()
        before = _dump(qualcoder_db_path)

        original = QualcoderDatabase.merge_codes

        def merge_then_explode(self, *a, **k):
            original(self, *a, **k)
            raise RuntimeError("simulated post-mutation failure")

        monkeypatch.setattr(QualcoderDatabase, "merge_codes", merge_then_explode)
        out = json.loads(server.merge_codes(1, 2, confirm=True))
        assert "error" in out
        # backup folders are siblings; the DB itself must be byte-restored
        assert _dump(qualcoder_db_path) == before

    def test_c6_delete_code_preview_counts_and_exact_cascade(
            self, setup_server, qualcoder_db_path):
        _seed_merge_fixture(qualcoder_db_path)
        _reload()
        preview = json.loads(server.delete_code(1))
        p = preview["preview"]
        assert p["text_codings_to_delete"] == 3
        assert p["av_codings_to_delete"] == 1
        assert p["image_codings_to_delete"] == 1
        assert p["total_codings_to_delete"] == 5

        tables = [r["name"] for r in _rows(
            qualcoder_db_path, "SELECT name FROM sqlite_master WHERE type='table'")]
        before = {t: _rows(qualcoder_db_path, f"SELECT COUNT(*) AS n FROM {t}")[0]["n"]
                  for t in tables}
        out = json.loads(server.delete_code(1, confirm=True))
        assert out.get("success") is True
        after = {t: _rows(qualcoder_db_path, f"SELECT COUNT(*) AS n FROM {t}")[0]["n"]
                 for t in tables}
        # exactly the four cid-keyed tables shrink, by exactly the counts
        assert before["code_name"] - after["code_name"] == 1
        assert before["code_text"] - after["code_text"] == 3
        assert before["code_av"] - after["code_av"] == 1
        assert before["code_image"] - after["code_image"] == 1
        for t in tables:
            if t not in ("code_name", "code_text", "code_av", "code_image"):
                assert after[t] == before[t], f"table {t} changed"

    def test_c8_rename_collision_precheck_and_binary_case_sensitivity(
            self, setup_server, qualcoder_db_path):
        out = json.loads(server.rename_code(1, "Coping"))
        assert "error" in out and "already exists" in out["error"]
        # BINARY collation: a case-variant of another code's name is DISTINCT
        out = json.loads(server.rename_code(1, "coping"))
        assert out.get("success") is True
        names = {r["name"] for r in _rows(qualcoder_db_path,
                                          "SELECT name FROM code_name")}
        assert {"coping", "Coping"} <= names
        # same for categories (global, case-sensitive unique)
        json.loads(server.create_category("Theme"))
        out = json.loads(server.create_category("theme"))
        assert out.get("success") is True, out
        out = json.loads(server.create_category("Theme"))
        assert "error" in out

    def test_c9_palette_default_and_strict_recolor(self, setup_server,
                                                   qualcoder_db_path):
        out = json.loads(server.create_code("QA palette probe"))
        assert out["code"]["color"] in QUALCODER_COLORS
        cid = out["code"]["id"]
        for bad in ("#zzzzzz", "#FFF", "red", "FF0000", "#12345G", ""):
            out = json.loads(server.recolor_code(cid, bad))
            assert "error" in out, bad
        ok = json.loads(server.recolor_code(cid, "#0d47a1"))
        assert ok.get("success") is True
        assert _row(qualcoder_db_path,
                    "SELECT color FROM code_name WHERE cid = ?",
                    (cid,))["color"] == "#0d47a1"


# =============================================================================
# DIFFERENTIAL twins — MCP op vs QualCoder's exact SQL recipe
# =============================================================================

def _twin(project_path, tmp_path) -> Path:
    twin = tmp_path / "twin_project.qda"
    shutil.copytree(project_path, twin)
    return twin


def _qualcoder_merge(twin: Path, old_cid: int, new_cid: int):
    """Hand-execution of QualCoder 3.8.2 merge_codes (code_text.py:2772-2831):
    per-row try/except reassign loops + final code_name delete, one txn."""
    conn = sqlite3.connect(str(_db(twin)))
    cur = conn.cursor()
    cur.execute("select ctid from code_text where cid=?", (old_cid,))
    for (ctid,) in cur.fetchall():
        try:
            cur.execute("update code_text set cid=? where ctid=?", (new_cid, ctid))
        except sqlite3.IntegrityError:
            cur.execute("delete from code_text where ctid=?", (ctid,))
    cur.execute("select avid from code_av where cid=?", (old_cid,))
    for (avid,) in cur.fetchall():
        try:
            cur.execute("update code_av set cid=? where avid=?", (new_cid, avid))
        except sqlite3.IntegrityError:
            cur.execute("delete from code_av where avid=?", (avid,))
    cur.execute("select imid from code_image where cid=?", (old_cid,))
    for (imid,) in cur.fetchall():
        try:
            cur.execute("update code_image set cid=? where imid=?", (new_cid, imid))
        except sqlite3.IntegrityError:
            cur.execute("delete from code_image where imid=?", (imid,))
    cur.execute("delete from code_name where cid=?", (old_cid,))
    conn.commit()
    conn.close()


class TestQualCoderFidelityDifferential:

    def test_merge_differential(self, setup_server, qualcoder_db_path, tmp_path):
        """The MCP's set-based merge must produce a database logically
        identical to QualCoder's per-row loop — including collision
        discards, ctid preservation, and av/image duplicate creation."""
        _seed_merge_fixture(qualcoder_db_path)
        _reload()
        twin = _twin(qualcoder_db_path, tmp_path)

        out = json.loads(server.merge_codes(1, 2, confirm=True))
        assert out.get("success") is True
        _qualcoder_merge(twin, 1, 2)

        assert _dump(qualcoder_db_path) == _dump(twin)

    def test_delete_code_differential(self, setup_server, qualcoder_db_path,
                                      tmp_path):
        _seed_merge_fixture(qualcoder_db_path)
        _reload()
        twin = _twin(qualcoder_db_path, tmp_path)

        assert json.loads(server.delete_code(1, confirm=True))["success"] is True
        # QualCoder delete_code (code_text.py:2953-2956)
        conn = sqlite3.connect(str(_db(twin)))
        cur = conn.cursor()
        cur.execute("delete from code_name where cid=?", (1,))
        cur.execute("delete from code_text where cid=?", (1,))
        cur.execute("delete from code_av where cid=?", (1,))
        cur.execute("delete from code_image where cid=?", (1,))
        conn.commit()
        conn.close()

        assert _dump(qualcoder_db_path) == _dump(twin)

    def test_delete_category_differential_with_grandchildren(
            self, setup_server, qualcoder_db_path, tmp_path):
        """Shallow delete: direct children to top level, grandchildren keep
        their parents — exactly QualCoder's 4-statement sequence."""
        # tree: 1 (fixture) -> 2 -> 3, plus a code in 2 and a code in 3
        _exec(qualcoder_db_path,
              "INSERT INTO code_cat (catid, name, supercatid) VALUES (2, 'Mid', 1)")
        _exec(qualcoder_db_path,
              "INSERT INTO code_cat (catid, name, supercatid) VALUES (3, 'Leafcat', 2)")
        _exec(qualcoder_db_path, "UPDATE code_name SET catid = 2 WHERE cid = 1")
        _exec(qualcoder_db_path, "UPDATE code_name SET catid = 3 WHERE cid = 2")
        _reload()
        twin = _twin(qualcoder_db_path, tmp_path)

        out = json.loads(server.delete_category(2, confirm=True))
        assert out.get("success") is True
        assert out["codes_moved_to_top_level"] == 1
        assert out["subcategories_moved_to_top_level"] == 1

        # QualCoder delete_category (code_text.py:2988-2996)
        conn = sqlite3.connect(str(_db(twin)))
        cur = conn.cursor()
        cur.execute("update code_name set catid=null where catid=?", (2,))
        cur.execute("update code_cat set supercatid=null where catid = ?", (2,))
        cur.execute("delete from code_cat where catid = ?", (2,))
        cur.execute("update code_cat set supercatid=null where supercatid is not null "
                    "and supercatid not in (select catid from code_cat)")
        conn.commit()
        conn.close()

        assert _dump(qualcoder_db_path) == _dump(twin)
        # semantics spot-check: child cat 3 promoted to TOP LEVEL (not to 1),
        # its code untouched; code from cat 2 now uncategorised
        assert _row(qualcoder_db_path,
                    "SELECT supercatid FROM code_cat WHERE catid = 3")["supercatid"] is None
        assert _row(qualcoder_db_path,
                    "SELECT catid FROM code_name WHERE cid = 2")["catid"] == 3
        assert _row(qualcoder_db_path,
                    "SELECT catid FROM code_name WHERE cid = 1")["catid"] is None

    def test_rename_recolor_move_differential(self, setup_server,
                                              qualcoder_db_path, tmp_path):
        _reload()
        twin = _twin(qualcoder_db_path, tmp_path)

        assert json.loads(server.rename_code(1, "Strain"))["success"] is True
        assert json.loads(server.recolor_code(1, "#F5F6CE"))["success"] is True
        assert json.loads(server.move_code_to_category(2))["success"] is True  # uncategorise
        assert json.loads(server.rename_category(1, "Renamed cat"))["success"] is True

        conn = sqlite3.connect(str(_db(twin)))
        cur = conn.cursor()
        cur.execute("update code_name set name=? where cid=?", ("Strain", 1))     # :3092
        cur.execute("update code_name set color=? where cid=?", ("#F5F6CE", 1))   # :3156
        cur.execute("update code_name set catid=? where cid=?", (None, 2))        # :1905
        cur.execute("update code_cat set name=? where catid=?", ("Renamed cat", 1))  # :3125
        conn.commit()
        conn.close()

        assert _dump(qualcoder_db_path) == _dump(twin)

    def test_move_category_differential(self, setup_server, qualcoder_db_path,
                                        tmp_path):
        _exec(qualcoder_db_path,
              "INSERT INTO code_cat (catid, name, supercatid) VALUES (2, 'Branch B', NULL)")
        _reload()
        twin = _twin(qualcoder_db_path, tmp_path)

        # legal move between branches, then to top level
        assert json.loads(server.move_category(2, "Category A"))["success"] is True
        assert json.loads(server.move_category(2))["success"] is True

        conn = sqlite3.connect(str(_db(twin)))
        cur = conn.cursor()
        cur.execute("update code_cat set supercatid=? where catid=?", (1, 2))  # :2740
        cur.execute("update code_cat set supercatid=? where catid=?", (None, 2))
        conn.commit()
        conn.close()

        assert _dump(qualcoder_db_path) == _dump(twin)

    def test_create_code_differential_modulo_timestamp(
            self, setup_server, qualcoder_db_path, tmp_path):
        """CREATE writes a timestamp, so compare the full row minus date and
        assert the date format instead of dump equality."""
        import re as _re
        out = json.loads(server.create_code("Diff create", color="#F4FA58",
                                            memo="def"))
        cid = out["code"]["id"]
        row = dict(_row(qualcoder_db_path,
                        "SELECT * FROM code_name WHERE cid = ?", (cid,)))
        assert row["name"] == "Diff create"
        assert row["memo"] == "def"          # "" default convention: caller-set here
        assert row["catid"] is None
        assert row["color"] == "#F4FA58"
        assert row["owner"] == "TestCoder"   # project codername attribution
        assert _re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", row["date"])
        # memo defaults to "" (never NULL) when omitted
        out2 = json.loads(server.create_code("Diff create 2"))
        assert _row(qualcoder_db_path,
                    "SELECT memo FROM code_name WHERE cid = ?",
                    (out2["code"]["id"],))["memo"] == ""
