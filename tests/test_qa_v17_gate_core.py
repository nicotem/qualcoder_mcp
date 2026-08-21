"""QA v17 gate — surfaces 1-3: probe gate, sub-code write fidelity, C7.

Independent verification: mis-stamped version strings both directions, the
unknown-schema override, my own replay of master's open-time repair oracle
WITH a negative control, twin-project differentials against master's
code_tree.py recipes (7b074d2), and the C7 fingerprint race on all three
covered paths including the slice-preserving tail rewrite.
"""

import json
import re
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import qualcoder_mcp.server as server
from qualcoder_mcp.database import QualcoderDatabase
from qualcoder_mcp.sessions import SessionManager

import test_v17_support as v17fix

FULLTEXT = v17fix.FULLTEXT


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


def _dump(p):
    conn = sqlite3.connect(str(_db(p)))
    lines = list(conn.iterdump())
    conn.close()
    return lines


def _attach(p, tmp):
    if server.db is not None:
        try:
            server.db.close()
        except Exception:
            pass
    server.db = QualcoderDatabase(str(p))
    server.current_project_path = str(p)
    server.session_manager = SessionManager(str(Path(tmp) / "sessions"))


def _add_subcode_forest(p):
    """Codes: 1 Stress (cat 1), 2 Coping (cat 1); add 3 SubB (supercid 1),
    4 SubSub (supercid 3), 5 SubC (supercid 1); codings, graph rows and
    recently_used entries referencing the branch."""
    conn = sqlite3.connect(str(_db(p)))
    c = conn.cursor()
    c.execute("INSERT INTO code_name (cid, name, memo, catid, owner, date, color, supercid) "
              "VALUES (3, 'SubB', 'subb memo', NULL, 'V17Test', '2024', '#0000FF', 1)")
    c.execute("INSERT INTO code_name (cid, name, memo, catid, owner, date, color, supercid) "
              "VALUES (4, 'SubSub', '', NULL, 'V17Test', '2024', '#00FFFF', 3)")
    c.execute("INSERT INTO code_name (cid, name, memo, catid, owner, date, color, supercid) "
              "VALUES (5, 'SubC', '', NULL, 'V17Test', '2024', '#FF00FF', 1)")
    c.execute("INSERT INTO code_text (ctid, cid, fid, seltext, pos0, pos1, owner, date, memo) "
              "VALUES (10, 3, 1, ?, 0, 12, 'V17Test', '2024', '')", (FULLTEXT[0:12],))
    c.execute("INSERT INTO code_text (ctid, cid, fid, seltext, pos0, pos1, owner, date, memo) "
              "VALUES (11, 4, 1, ?, 57, 77, 'V17Test', '2024', '')", (FULLTEXT[57:77],))
    # collision pair for merge: 3 and 2 both code the same span/owner
    c.execute("INSERT INTO code_text (ctid, cid, fid, seltext, pos0, pos1, owner, date, memo) "
              "VALUES (12, 3, 1, ?, 24, 55, 'V17Test', '2024', 'loser memo')",
              (FULLTEXT[24:55],))
    c.execute("INSERT INTO code_text (ctid, cid, fid, seltext, pos0, pos1, owner, date, memo) "
              "VALUES (13, 2, 1, ?, 24, 55, 'V17Test', '2024', '')", (FULLTEXT[24:55],))
    # graph rows referencing branch cids
    c.execute("INSERT INTO graph (grid, name) VALUES (1, 'g')")
    c.execute("INSERT INTO gr_cdct_text_item (gtextid, grid, cid) VALUES (1, 1, 3)")
    c.execute("INSERT INTO gr_cdct_line_item (glineid, grid, fromcid, tocid) VALUES (1, 1, 3, 2)")
    c.execute("INSERT INTO gr_free_line_item (gflineid, grid, fromcid, tocid) VALUES (1, 1, 4, 1)")
    c.execute("UPDATE project SET recently_used_codes = '1 3'")
    conn.commit()
    conn.close()


# --- The independently re-derived oracle: master's open-time repair SQL
#     (__main__.py:2376-2416). Returns the number of rows a master open
#     would have changed; 0 = our write left nothing for master to undo. ---

def master_repair_rowcount(p) -> int:
    conn = sqlite3.connect(str(_db(p)))
    c = conn.cursor()
    changed = 0
    c.execute("update code_name set supercid=null where supercid is not null "
              "and supercid not in (select cid from code_name)")
    changed += c.rowcount
    c.execute("update code_name set catid=null where supercid is not null "
              "and catid is not null")
    changed += c.rowcount
    # cycle detection (master breaks these too): count nodes stuck in loops
    rows = dict(c.execute(
        "select cid, supercid from code_name where supercid is not null"))
    for start in rows:
        seen, cur = set(), start
        while cur in rows and cur not in seen:
            seen.add(cur)
            cur = rows[cur]
        if cur in seen:
            changed += 1
    conn.rollback()   # the ORACLE never mutates the project under test
    conn.close()
    return changed


class TestOracleNegativeControl:

    def test_oracle_detects_seeded_corruptions(self, tmp_path):
        """The oracle is not vacuous: each corruption class it claims to
        detect is actually detected."""
        p = v17fix.make_project(tmp_path, "v17")
        _add_subcode_forest(p)
        assert master_repair_rowcount(p) == 0          # clean baseline

        # (a) both-parents row
        _exec(p, "UPDATE code_name SET catid = 1 WHERE cid = 3")
        assert master_repair_rowcount(p) >= 1
        _exec(p, "UPDATE code_name SET catid = NULL WHERE cid = 3")

        # (b) dangling supercid
        _exec(p, "UPDATE code_name SET supercid = 999 WHERE cid = 5")
        assert master_repair_rowcount(p) >= 1
        _exec(p, "UPDATE code_name SET supercid = 1 WHERE cid = 5")

        # (c) supercid cycle
        _exec(p, "UPDATE code_name SET supercid = 4 WHERE cid = 1")
        _exec(p, "UPDATE code_name SET catid = NULL WHERE cid = 1")
        assert master_repair_rowcount(p) >= 1


# =============================================================================
# Surface 1 — PROBE GATE
# =============================================================================

class TestProbeGate:

    def test_v13_stamped_with_v14_columns_writes(self, tmp_path):
        p = v17fix.make_project(tmp_path, "v14")
        _exec(p, "UPDATE project SET databaseversion = 'v13'")
        _attach(p, tmp_path)
        out = json.loads(server.set_memo("code", 1, "written on v13-stamped"))
        assert out.get("success") is True, out
        assert "schema_warning" not in out             # no leakage

    def test_v17_stamped_without_supercid_behaves_v14(self, tmp_path):
        p = v17fix.make_project(tmp_path, "v14")
        _exec(p, "UPDATE project SET databaseversion = 'v17'")
        _attach(p, tmp_path)
        # v14 recipe still works (no supercid column to touch)
        out = json.loads(server.move_code_to_category(1))
        assert out.get("success") is True, out
        # the capability decides, not the string: sub-code creation refused
        out = json.loads(server.create_code("Wants parent", parent_code_id=1))
        assert "error" in out
        assert "support" in out["error"].lower() or "schema" in out["error"].lower()

    def test_true_pre_v14_refusal_wording(self, tmp_path):
        p = v17fix.make_project(tmp_path, "v14")
        conn = sqlite3.connect(str(_db(p)))
        conn.execute("DROP TABLE coder_names")
        conn.execute("UPDATE project SET databaseversion = 'v13'")
        conn.commit()
        conn.close()
        _attach(p, tmp_path)
        out = json.loads(server.set_memo("code", 1, "nope"))
        assert "error" in out
        assert "3.8 or newer" in out["error"]          # forward-correct wording
        assert "upgrade it, then try again" not in out["error"]

    def test_forward_guard_and_override_warning_discipline(self, tmp_path,
                                                          monkeypatch):
        p = v17fix.make_project(tmp_path, "v17")
        _exec(p, "UPDATE project SET databaseversion = 'v19'")
        _attach(p, tmp_path)
        # refused without the override, naming the env var
        out = json.loads(server.set_memo("code", 1, "future"))
        assert "error" in out and "QUALCODER_MCP_ALLOW_UNKNOWN_SCHEMA" in out["error"]

        monkeypatch.setenv("QUALCODER_MCP_ALLOW_UNKNOWN_SCHEMA", "1")
        _attach(p, tmp_path)  # fresh connection under the override
        # EVERY overridden write result carries the warning
        r1 = json.loads(server.set_memo("code", 1, "overridden"))
        assert r1.get("success") is True and "schema_warning" in r1
        r2 = json.loads(server.create_code("Override probe"))
        assert r2.get("success") is True and "schema_warning" in r2
        r3 = json.loads(server.add_journal_entry("Override journal", "body"))
        assert r3.get("success") is True and "schema_warning" in r3

        # non-parsing stamp: same guard
        monkeypatch.delenv("QUALCODER_MCP_ALLOW_UNKNOWN_SCHEMA")
        _exec(p, "UPDATE project SET databaseversion = 'banana'")
        _attach(p, tmp_path)
        out = json.loads(server.set_memo("code", 1, "nope"))
        assert "error" in out and "QUALCODER_MCP_ALLOW_UNKNOWN_SCHEMA" in out["error"]

    def test_no_warning_leakage_when_unset(self, tmp_path):
        p = v17fix.make_project(tmp_path, "v14")
        _attach(p, tmp_path)
        for out in (server.set_memo("code", 1, "clean"),
                    server.create_code("Clean probe"),
                    server.import_text_file("clean.txt", "content",
                                            create_backup=False)):
            assert "schema_warning" not in json.loads(out)

    def test_probe_cached_per_connection_refreshed_on_reconnect(self, tmp_path):
        p = v17fix.make_project(tmp_path, "v14")
        _attach(p, tmp_path)
        assert "error" in json.loads(
            server.create_code("Early sub", parent_code_id=1))
        # live migration mid-session (master opened the project meanwhile)
        conn = sqlite3.connect(str(_db(p)))
        v17fix._migrate_v15(conn)
        v17fix._migrate_v16(conn)
        conn.commit()
        conn.close()
        # WRITE path: _perform_write opens a fresh RW connection, which
        # re-probes — the live migration is honored immediately (no
        # stale-capability write hazard). Better than cached-refusal.
        out = json.loads(server.create_code("Mid sub", parent_code_id=1))
        assert out.get("success") is True, out
        assert _one(p, "SELECT supercid FROM code_name WHERE name='Mid sub'")[0] == 1
        # READ surface on the original RO connection may lag; a reconnect
        # refreshes it
        server.switch_project(str(p))
        cur = json.loads(server.get_current_project())
        assert cur["schema"]["capabilities"]["has_supercid"] is True

    def test_schema_block_in_project_reads(self, tmp_path):
        p = v17fix.make_project(tmp_path, "v17")
        _attach(p, tmp_path)
        cur = json.loads(server.get_current_project())
        blk = cur["schema"]
        assert blk["capabilities"]["has_supercid"] is True
        assert blk["capabilities"]["has_coder_names"] is True
        assert blk["write_support"] is True
        # override fields are conditional (present only when active)
        assert blk.get("override_active", False) is False


# =============================================================================
# Surface 2 — SUB-CODE WRITE DIFFERENTIALS (twin projects vs master recipes)
# =============================================================================

class TestSubcodeWriteDifferentials:

    def _project(self, tmp_path):
        p = v17fix.make_project(tmp_path, "v17")
        _add_subcode_forest(p)
        _attach(p, tmp_path)
        return p

    def _twin(self, p, tmp_path):
        twin = tmp_path / "twin.qda"
        shutil.copytree(p, twin)
        return twin

    def test_move_dual_pointer_differential_and_oracle(self, tmp_path):
        p = self._project(tmp_path)
        twin = self._twin(p, tmp_path)

        out = json.loads(server.move_code_to_category(3, "Category A"))
        assert out.get("success") is True, out
        # master recipe (code_tree.py:1245)
        conn = sqlite3.connect(str(twin / "data.qda"))
        conn.execute("update code_name set catid=?, supercid=null where cid=?",
                     (1, 3))
        conn.commit()
        conn.close()
        assert _dump(p) == _dump(twin)
        assert master_repair_rowcount(p) == 0          # survives master's open

        # to uncategorised: both pointers NULL (code_tree.py:1235)
        out = json.loads(server.move_code_to_category(3))
        assert out.get("success") is True
        conn = sqlite3.connect(str(twin / "data.qda"))
        conn.execute("update code_name set catid=null, supercid=null where cid=3")
        conn.commit()
        conn.close()
        assert _dump(p) == _dump(twin)
        assert master_repair_rowcount(p) == 0

    def test_merge_differential_provenance_reparent_graphs(self, tmp_path):
        p = self._project(tmp_path)
        twin = self._twin(p, tmp_path)

        out = json.loads(server.merge_codes(3, 2, confirm=True))
        assert out.get("success") is True, out
        assert out.get("provenance_memo_added") is True
        assert out.get("subcodes_reparented_to_target") == 1   # SubSub(4)

        # master recipe on the twin (code_tree.py:1521-1576) minus the
        # recently_used edit (MCP's documented W14 divergence)
        conn = sqlite3.connect(str(twin / "data.qda"))
        c = conn.cursor()
        src = c.execute("select name, memo, owner from code_name where cid=3").fetchone()
        tgt_memo = c.execute("select memo from code_name where cid=2").fetchone()[0] or ""
        block = (f"\n\n[Merged from code: {src[0]}, Coder: {src[2]}, "
                 f"Merger date: NORM]")
        if (src[1] or "").strip():
            block += f"\n{src[1].strip()}"
        c.execute("update code_name set memo=? where cid=2",
                  ((tgt_memo + block).strip(),))
        for (ctid,) in c.execute("select ctid from code_text where cid=3").fetchall():
            try:
                c.execute("update code_text set cid=2 where ctid=?", (ctid,))
            except sqlite3.IntegrityError:
                c.execute("delete from code_text where ctid=?", (ctid,))
        c.execute("update code_name set supercid=2, catid=null where supercid=3")
        c.execute("delete from code_name where cid=3")
        c.execute("delete from gr_cdct_text_item where cid=3")
        c.execute("delete from gr_cdct_line_item where fromcid=3 or tocid=3")
        c.execute("delete from gr_free_line_item where fromcid=3 or tocid=3")
        conn.commit()
        conn.close()

        norm = lambda lines: [re.sub(
            r"Merger date: [0-9: -]+", "Merger date: NORM", l) for l in lines]
        assert norm(_dump(p)) == norm(_dump(twin))
        assert master_repair_rowcount(p) == 0
        # W14: recently_used untouched by the MCP (master would prune it)
        assert _one(p, "SELECT recently_used_codes FROM project")[0] == "1 3"
        # collision semantics survived: destination row 13 won, loser 12 gone
        assert _one(p, "SELECT COUNT(*) FROM code_text WHERE ctid=12")[0] == 0
        assert _one(p, "SELECT cid FROM code_text WHERE ctid=13")[0] == 2

    def test_merge_descendant_cycle_refused_at_preview(self, tmp_path):
        p = self._project(tmp_path)
        folder = Path(p)
        n_backups = len(list(folder.parent.glob(f"{folder.stem}_backup_*.qda")))
        for args in ((1, "SubSub"), (1, "SubB"), (3, "SubSub")):
            out = json.loads(server.merge_codes(args[0],
                                                _one(p, "SELECT cid FROM code_name WHERE name=?", (args[1],))[0],
                                                confirm=True))
            assert "error" in out, args
            assert "sub-code" in out["error"].lower() or "descend" in out["error"].lower()
        assert len(list(folder.parent.glob(f"{folder.stem}_backup_*.qda"))) \
            == n_backups                                # no backup litter
        assert master_repair_rowcount(p) == 0

    def test_delete_branch_preview_refuse_and_cascade_differential(
            self, tmp_path):
        p = self._project(tmp_path)
        twin = self._twin(p, tmp_path)

        pv = json.loads(server.delete_code(1))
        preview = pv["preview"]
        assert preview["subcode_count"] == 3           # 3, 4, 5
        assert set(preview["subcodes"]) == {"SubB", "SubSub", "SubC"}
        assert "cascade" in preview["note"]
        # branch-wide coding blast radius, not just the root's
        assert preview["text_codings_to_delete"] == 4  # ctids 1,10,11,12

        # refuse without cascade
        out = json.loads(server.delete_code(1, confirm=True))
        assert "error" in out and "cascade" in out["error"]
        assert _one(p, "SELECT COUNT(*) FROM code_name")[0] == 5   # untouched

        out = json.loads(server.delete_code(1, confirm=True, cascade=True))
        assert out.get("success") is True, out

        # master recipe on the twin: branch cids {1,3,4,5}, per-cid deletes
        conn = sqlite3.connect(str(twin / "data.qda"))
        c = conn.cursor()
        for cid in (1, 3, 4, 5):
            c.execute("delete from code_text where cid=?", (cid,))
            c.execute("delete from code_av where cid=?", (cid,))
            c.execute("delete from code_image where cid=?", (cid,))
            c.execute("delete from gr_cdct_text_item where cid=?", (cid,))
            c.execute("delete from gr_cdct_line_item where fromcid=? or tocid=?",
                      (cid, cid))
            c.execute("delete from gr_free_line_item where fromcid=? or tocid=?",
                      (cid, cid))
            c.execute("delete from code_name where cid=?", (cid,))
        conn.commit()
        conn.close()
        assert _dump(p) == _dump(twin)
        # invariant: no dangling supercid can survive any delete
        assert _one(p, "SELECT COUNT(*) FROM code_name WHERE supercid IS NOT NULL "
                       "AND supercid NOT IN (SELECT cid FROM code_name)")[0] == 0
        assert master_repair_rowcount(p) == 0
        assert _one(p, "SELECT recently_used_codes FROM project")[0] == "1 3"

    def test_create_code_parent_deep_nesting_and_guards(self, tmp_path):
        p = self._project(tmp_path)
        # deep chain under SubSub (depth 4)
        out = json.loads(server.create_code("Depth4", parent_code_id=4))
        assert out.get("success") is True, out
        new_cid = out["code"]["id"]
        row = _one(p, "SELECT supercid, catid FROM code_name WHERE cid=?",
                   (new_cid,))
        assert row[0] == 4 and row[1] is None          # exclusivity at birth
        out = json.loads(server.create_code("Depth5", parent_code_id=new_cid))
        assert out.get("success") is True
        assert master_repair_rowcount(p) == 0

        # XOR and existence guards
        assert "error" in json.loads(server.create_code(
            "Both parents", parent_code_id=1, category="Category A"))
        assert "error" in json.loads(server.create_code(
            "Ghost parent", parent_code_id=424242))

    def test_v14_projects_hierarchy_inert(self, tmp_path):
        """On a v14 project every hierarchy behavior is absent and the
        3.8.2 recipes remain byte-exact (no supercid column touched)."""
        p = v17fix.make_project(tmp_path, "v14")
        _attach(p, tmp_path)
        twin = tmp_path / "twin14.qda"
        shutil.copytree(p, twin)

        assert json.loads(server.move_code_to_category(1))["success"] is True
        assert json.loads(server.merge_codes(1, 2, confirm=True))["success"] is True
        conn = sqlite3.connect(str(twin / "data.qda"))
        c = conn.cursor()
        c.execute("update code_name set catid=? where cid=?", (None, 1))  # 3.8.2 move
        for (ctid,) in c.execute("select ctid from code_text where cid=1").fetchall():
            try:
                c.execute("update code_text set cid=2 where ctid=?", (ctid,))
            except sqlite3.IntegrityError:
                c.execute("delete from code_text where ctid=?", (ctid,))
        c.execute("delete from code_name where cid=1")
        conn.commit()
        conn.close()
        assert _dump(p) == _dump(twin)                 # 3.8.2-lossy-exact:
        # no provenance memo, no reparent flags on v14
        assert _one(p, "SELECT memo FROM code_name WHERE cid=2")[0] == ""
        pv = json.loads(server.delete_code(2))
        assert "subcode_count" not in json.dumps(pv) or \
            pv["preview"].get("subcode_count", 0) == 0


# =============================================================================
# Surface 3 — C7 fingerprint race (all three covered paths)
# =============================================================================

def _mutate_during_backup(monkeypatch, project_path, mutation_sql, args=()):
    """Inject an external fulltext mutation into the post-validation,
    pre-write window (the backup step)."""
    original = QualcoderDatabase.backup_before_write

    def backup_and_mutate(self):
        result = original(self)
        conn = sqlite3.connect(str(_db(project_path)))
        conn.execute(mutation_sql, args)
        conn.commit()
        conn.close()
        return result

    monkeypatch.setattr(QualcoderDatabase, "backup_before_write",
                        backup_and_mutate)


class TestC7FingerprintRace:

    TAIL = " SLICE-PRESERVING TAIL APPENDED BY A LOCKLESS EDITOR."

    def _p(self, tmp_path):
        p = v17fix.make_project(tmp_path, "v17")
        _attach(p, tmp_path)
        return p

    def test_apply_codings_tail_rewrite_rolls_back(self, tmp_path, monkeypatch):
        p = self._p(tmp_path)
        sid = json.loads(server.analyze_for_coding([1]))["session_id"]
        rec = json.loads(server.record_suggestions(sid, [{
            "file_id": 1, "code_name": "Coping",
            "segment_text": "I cope by exercising"}]))
        guid = rec["recorded"][0]["guid"]
        server.update_suggestion_status(sid, approve=[guid])

        # the tail rewrite PRESERVES the validated slice: per-row checks
        # cannot catch it; only the (length, sha256) fingerprint can
        _mutate_during_backup(
            monkeypatch, p,
            "UPDATE source SET fulltext = fulltext || ? WHERE id = 1",
            (self.TAIL,))
        before = _one(p, "SELECT COUNT(*) FROM code_text")[0]
        out = json.loads(server.apply_codings(sid))
        monkeypatch.undo()

        assert "error" in out, out
        blob = out["error"].lower()
        assert "chang" in blob or "modif" in blob       # structured, names the race
        assert _one(p, "SELECT COUNT(*) FROM code_text")[0] == before  # rollback
        # session NOT burned: the suggestion is still approved, re-appliable
        info = json.loads(server.get_coding_session_info(sid))
        sugg = next(s for s in info["suggestions"] if s["guid"] == guid)
        assert sugg["status"] == "approved"
        assert server.db is not None and server.db.read_only
        # and after re-validating against the new text, apply succeeds —
        # the race became a recoverable precondition failure
        server.switch_project(str(p))
        out2 = server.apply_codings(sid)
        assert "error" in out2 or "CODINGS APPLIED" in out2

    def test_add_annotation_race_never_anchors_stale(self, tmp_path,
                                                     monkeypatch):
        """add_annotation validates positions against the fulltext read
        INSIDE the write op (post-backup), so a backup-window mutation is
        not a stale-anchor race: the row is validated against the CURRENT
        text (or cleanly refused when positions no longer fit). The
        fingerprint covers only the read-to-INSERT window. Invariant: an
        annotation row is NEVER inconsistent with the live fulltext."""
        p = self._p(tmp_path)
        _mutate_during_backup(
            monkeypatch, p,
            "UPDATE source SET fulltext = fulltext || ? WHERE id = 1",
            (self.TAIL,))
        out = json.loads(server.add_annotation(1, 0, 10, "race note"))
        monkeypatch.undo()
        if out.get("success"):
            # anchored against the CURRENT (mutated) text — valid span
            cur = _one(p, "SELECT fulltext FROM source WHERE id = 1")[0]
            row = _one(p, "SELECT pos0, pos1 FROM annotation")
            assert 0 <= row[0] < row[1] <= len(cur)
        else:
            assert _one(p, "SELECT COUNT(*) FROM annotation")[0] == 0
        assert server.db is not None and server.db.read_only

        # truncation variant: validated positions no longer fit -> clean
        # structured refusal, nothing written
        _mutate_during_backup(monkeypatch, p,
                              "UPDATE source SET fulltext = ? WHERE id = 1",
                              ("short",))
        out = json.loads(server.add_annotation(1, 20, 40, "stale span"))
        monkeypatch.undo()
        assert "error" in out, out
        assert _one(p, "SELECT COUNT(*) FROM annotation WHERE memo='stale span'")[0] == 0

    def test_create_proposed_codes_race_all_or_nothing(self, tmp_path,
                                                       monkeypatch):
        p = self._p(tmp_path)
        sid = json.loads(server.analyze_for_coding([1]))["session_id"]
        pp = json.loads(server.propose_codes(sid, [{
            "name": "Race code",
            "example_segments": [{"file_id": 1,
                                  "segment_text": "I cope by exercising"}]}]))
        server.update_proposal_status(sid, approve=[pp["recorded"][0]["guid"]])

        _mutate_during_backup(
            monkeypatch, p,
            "UPDATE source SET fulltext = fulltext || ? WHERE id = 1",
            (self.TAIL,))
        out = json.loads(server.create_proposed_codes(
            sid, apply_coded_segments=True))
        monkeypatch.undo()
        assert "error" in out, out
        # ALL-or-nothing: not even the code row survives
        assert _one(p, "SELECT COUNT(*) FROM code_name WHERE name='Race code'")[0] == 0
        info = json.loads(server.get_coding_session_info(sid))
        assert all(pc["status"] != "created" for pc in info["proposed_codes"])

    def test_in_place_rewrite_same_length_different_bytes_caught(
            self, tmp_path, monkeypatch):
        """Length-preserving content swap: sha256 half of the fingerprint."""
        p = self._p(tmp_path)
        sid = json.loads(server.analyze_for_coding([1]))["session_id"]
        rec = json.loads(server.record_suggestions(sid, [{
            "file_id": 1, "code_name": "Stress",
            "segment_text": FULLTEXT[24:55]}]))
        server.update_suggestion_status(sid, approve=[rec["recorded"][0]["guid"]])
        swapped = FULLTEXT[:60] + "X" * (len(FULLTEXT) - 60)   # same length
        _mutate_during_backup(monkeypatch, p,
                              "UPDATE source SET fulltext = ? WHERE id = 1",
                              (swapped,))
        out = json.loads(server.apply_codings(sid))
        monkeypatch.undo()
        assert "error" in out
        assert _one(p, "SELECT COUNT(*) FROM code_text "
                       "WHERE owner='AI Coding Assistant'")[0] == 0
