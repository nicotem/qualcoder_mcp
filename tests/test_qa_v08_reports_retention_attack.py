"""QA v0.8 gate — surfaces 3-4: report exports (QualCoder-parity
differential on hostile fixtures) + backup retention.

The frequencies oracle hand-executes the dossier's R2 counting rules
(reporting.md §3.1): one count per coding row over ALL THREE media tables,
per-coder, NO source join (orphans included), recursive category roll-ups.
"""

import csv
import io
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server


FULLTEXT = "This is interview text. I feel stressed about deadlines. I cope by exercising."


def _db(p) -> Path:
    return Path(p) / "data.qda"


def _sql(p, script):
    conn = sqlite3.connect(str(_db(p)))
    conn.executescript(script)
    conn.commit()
    conn.close()


def _reload():
    server.switch_project(server.current_project_path)


def _hostile_report_fixture(p):
    """Deep tree, two coders, orphaned coding, media codings, zero-coding
    code — every counting trap from the dossier at once.

    Tree: Top(1) > Mid(2) > code1 'Deep code'; code2 'Shallow' in Top;
    code3 'Unfiled' uncategorised; code4 'Silent' zero codings.
    Codings: code1 x2 by coderA + x1 by coderB (text);
             code2 x1 by coderB with ORPHAN fid 999 (text);
             code3: one code_av row by coderA + one code_image row by coderB.
    """
    _sql(p, """
        DELETE FROM code_text; DELETE FROM code_name; DELETE FROM code_cat;
        INSERT INTO code_cat (catid, name, supercatid) VALUES (1, 'Top', NULL);
        INSERT INTO code_cat (catid, name, supercatid) VALUES (2, 'Mid', 1);
        INSERT INTO code_name (cid, name, catid, owner, date, color)
            VALUES (1, 'Deep code', 2, 'coderA', '2024', '#FF0000');
        INSERT INTO code_name (cid, name, catid, owner, date, color)
            VALUES (2, 'Shallow', 1, 'coderA', '2024', '#00FF00');
        INSERT INTO code_name (cid, name, catid, owner, date, color)
            VALUES (3, 'Unfiled', NULL, 'coderA', '2024', '#0000FF');
        INSERT INTO code_name (cid, name, catid, owner, date, color)
            VALUES (4, 'Silent', NULL, 'coderA', '2024', '#00FFFF');
        INSERT INTO code_text (cid, fid, seltext, pos0, pos1, owner)
            VALUES (1, 1, 'This is in', 0, 10, 'coderA');
        INSERT INTO code_text (cid, fid, seltext, pos0, pos1, owner)
            VALUES (1, 1, 'terview te', 10, 20, 'coderA');
        INSERT INTO code_text (cid, fid, seltext, pos0, pos1, owner)
            VALUES (1, 1, 'This is in', 0, 10, 'coderB');
        INSERT INTO code_text (cid, fid, seltext, pos0, pos1, owner)
            VALUES (2, 999, 'orphan row', 0, 10, 'coderB');
        INSERT INTO code_av (cid, id, pos0, pos1, owner)
            VALUES (3, 1, 0, 5000, 'coderA');
        INSERT INTO code_image (id, x1, y1, width, height, cid, owner)
            VALUES (1, 0, 0, 10, 10, 3, 'coderB');
    """)
    _reload()


# The R2 oracle, hand-executed
def _r2_oracle(p):
    conn = sqlite3.connect(str(_db(p)))
    coders = sorted({r[0] for r in conn.execute(
        "SELECT DISTINCT owner FROM code_text UNION "
        "SELECT DISTINCT owner FROM code_image UNION "
        "SELECT DISTINCT owner FROM code_av")})
    rows = list(conn.execute(
        "SELECT cid, owner FROM code_text UNION ALL "
        "SELECT cid, owner FROM code_image UNION ALL "
        "SELECT cid, owner FROM code_av"))
    codes = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT cid, name, catid FROM code_name")}
    cats = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT catid, name, supercatid FROM code_cat")}
    conn.close()

    per_code = {cid: {c: 0 for c in coders} for cid in codes}
    for cid, owner in rows:
        if cid in per_code:
            per_code[cid][owner] += 1

    def subtree_codes(catid):
        out = [cid for cid, (_, cc) in codes.items() if cc == catid]
        for sub, (_, parent) in cats.items():
            if parent == catid:
                out += subtree_codes(sub)
        return out

    per_cat = {}
    for catid in cats:
        cids = subtree_codes(catid)
        per_cat[catid] = {c: sum(per_code[cid][c] for cid in cids)
                          for c in coders}
    return coders, per_code, per_cat, codes, cats


class TestFrequenciesDifferential:

    def _parse(self, path):
        raw = Path(path).read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf")            # utf-8-sig BOM
        rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
        return rows

    def test_counts_match_qualcoder_r2_exactly(self, setup_server,
                                               qualcoder_db_path, tmp_path):
        _hostile_report_fixture(qualcoder_db_path)
        out = json.loads(server.export_frequencies_csv(
            str(tmp_path / "freq.csv")))
        assert out["success"] is True, out
        rows = self._parse(out["output_path"])
        header = rows[0]
        coders, per_code, per_cat, codes, cats = _r2_oracle(qualcoder_db_path)

        assert header == ["Code Tree", "Id"] + coders + ["Total"]
        table = {r[1]: r for r in rows[1:]}                # by Id

        # code rows: per-coder counts + Total — orphans AND media INCLUDED
        for cid, per in per_code.items():
            row = table[f"cid:{cid}"]
            for i, coder in enumerate(coders):
                assert int(row[2 + i]) == per[coder], (cid, coder)
            assert int(row[-1]) == sum(per.values())
        # 'Deep code'总 = 3 incl. both coders; orphan counts under Shallow
        assert int(table["cid:2"][-1]) == 1                # the ORPHAN row
        assert int(table["cid:3"][-1]) == 2                # av + image rows
        assert int(table["cid:4"][-1]) == 0                # zero-coding code

        # category rows: recursive subtree roll-ups per coder
        for catid, per in per_cat.items():
            row = table[f"catid:{catid}"]
            for i, coder in enumerate(coders):
                assert int(row[2 + i]) == per[coder], (catid, coder)
        assert int(table["catid:1"][-1]) == 4              # Deep(3)+Shallow(1)
        assert int(table["catid:2"][-1]) == 3

        # depth prefixes carry hierarchy: Mid nested under Top
        assert table["catid:1"][0].startswith("Top") \
            or table["catid:1"][0].lstrip("-").startswith("Top")
        assert table["catid:2"][0].startswith("--")

        # the divergence note names get_coding_frequencies and is accurate
        note = out["divergence_note"]
        assert "get_coding_frequencies" in note
        gcf = json.loads(server.get_coding_frequencies())
        gcf_totals = {c["code_id"]: c["frequency"] for c in gcf["codes"]}
        assert gcf_totals[2] == 0        # orphan EXCLUDED there
        assert gcf_totals[3] == 0        # media EXCLUDED there
        assert int(table["cid:2"][-1]) == 1                # but included HERE

    def test_directory_target_default_name_and_collision_suffixes(
            self, setup_server, qualcoder_db_path, tmp_path):
        _hostile_report_fixture(qualcoder_db_path)
        names = []
        for _ in range(3):
            out = json.loads(server.export_frequencies_csv(str(tmp_path)))
            assert out["success"] is True, out
            names.append(Path(out["output_path"]).name)
        assert names == ["Code_frequencies.csv", "Code_frequencies_0.csv",
                         "Code_frequencies_1.csv"]   # first suffix is _0


class TestCodedSegmentsReport:

    def test_byte_conventions_quote_all_crlf_bom(self, setup_server,
                                                 qualcoder_db_path, tmp_path):
        out = json.loads(server.export_coded_segments_report(
            str(tmp_path / "coded.csv")))
        assert out["success"] is True, out
        raw = Path(out["output_path"]).read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf")             # utf-8-sig
        body = raw.decode("utf-8-sig")
        assert "\r\n" in body                              # CRLF rows
        # QUOTE_ALL: every field quoted, incl. the header
        first_line = body.split("\r\n")[0]
        assert first_line.startswith('"') and '","' in first_line

    def test_case_mode_states_containment_rule(self, setup_server,
                                               qualcoder_db_path, tmp_path):
        out = json.loads(server.export_coded_segments_report(
            str(tmp_path / "bycase.csv"), case_names=["Case A"]))
        assert out["success"] is True, out
        blob = json.dumps(out).lower()
        assert "contain" in blob                           # rule STATED

    def test_coder_filter_exact_never_like(self, setup_server,
                                           qualcoder_db_path, tmp_path):
        _hostile_report_fixture(qualcoder_db_path)
        out = json.loads(server.export_coded_segments_report(
            str(tmp_path / "coder.csv"), coder="coder"))
        # 'coder' must match NOTHING (exact match; 'coderA'/'coderB' exist)
        assert out.get("rows_exported", out.get("rows", 0)) == 0 or \
            "no rows" in json.dumps(out).lower() or out.get("success") is True
        if out.get("success"):
            body = Path(out["output_path"]).read_bytes().decode("utf-8-sig")
            assert "coderA" not in body and "coderB" not in body


class TestExportPathConfinement:

    CASES = "escape battery shared with export_refi_qda posture"

    @pytest.mark.parametrize("tool", ["export_codebook",
                                      "export_coded_segments_report",
                                      "export_frequencies_csv",
                                      "export_case_code_matrix_csv"])
    def test_escape_battery(self, setup_server, qualcoder_db_path, tmp_path,
                            tool):
        fn = getattr(server, tool)
        inside = Path(qualcoder_db_path) / "leak.csv"
        assert "error" in json.loads(fn(str(inside)))          # inside project
        assert "error" in json.loads(fn(str(tmp_path / "no_dir" / "x.csv")))
        assert "error" in json.loads(fn(str(tmp_path / "wrong.exe")))
        target = tmp_path / f"{tool}_dup.csv"
        target.write_bytes(b"existing")
        assert "error" in json.loads(fn(str(target)))          # no overwrite
        ok = json.loads(fn(str(target), overwrite=True))
        assert ok.get("success") is True, (tool, ok)
        # traversal out of an allowed dir resolves and is judged post-resolve
        sneaky = tmp_path / "sub" / ".." / f"{tool}_sneak.csv"
        res = json.loads(fn(str(sneaky)))
        if res.get("success"):
            assert Path(res["output_path"]).parent == tmp_path

    def test_codebook_counts_include_media_all_coders(self, setup_server,
                                                      qualcoder_db_path,
                                                      tmp_path):
        _hostile_report_fixture(qualcoder_db_path)
        out = json.loads(server.export_codebook(str(tmp_path / "cb.csv")))
        assert out["success"] is True, out
        body = Path(out["output_path"]).read_bytes().decode("utf-8-sig")
        rows = {r[0].strip("."): r for r in csv.reader(io.StringIO(body))
                if r and r[0] != "Tree"}
        unfiled = next(r for k, r in rows.items() if "Unfiled" in k)
        assert int(unfiled[4]) == 2       # av + image counted (codebook rule)

    def test_matrix_no_totals_row(self, setup_server, qualcoder_db_path,
                                  tmp_path):
        out = json.loads(server.export_case_code_matrix_csv(
            str(tmp_path / "mx.csv")))
        assert out["success"] is True, out
        body = Path(out["output_path"]).read_bytes().decode("utf-8-sig")
        assert "Total" not in body        # parity: no totals row/column
        blob = json.dumps(out).lower()
        assert "contain" in blob          # counting rule stated


# =============================================================================
# Surface 4 — RETENTION
# =============================================================================

def _seed_backups(project_path, specs):
    """Create sibling backup folders with controlled ages.
    specs: list of (suffix_name, age_days)."""
    folder = Path(project_path)
    created = []
    for name, age in specs:
        b = folder.parent / name
        if b.exists():
            shutil.rmtree(b)
        b.mkdir()
        shutil.copy2(_db(project_path), b / "data.qda")
        stamp = time.time() - age * 86400
        os.utime(b, (stamp, stamp))
        created.append(b)
    return created


class TestPruneBackups:

    def _seed(self, p):
        stem = Path(p).stem
        return _seed_backups(p, [
            (f"{stem}_backup_20260601_010101.qda", 40),
            (f"{stem}_backup_20260710_010101.qda", 10),
            (f"{stem}_backup_20260715_010101.qda", 5),
            (f"{stem}_backup_20260720_010101.qda", 1),
            (f"{stem}_backup_20260701_010101_prerestore.qda", 20),
            (f"{stem}_BKUP_20260501_09.qda", 60),          # QualCoder decoy
            (f"{stem}_BKUP_20260502_09.qda", 59),          # QualCoder decoy
        ])

    def test_list_backups_reports_age_days(self, setup_server,
                                           qualcoder_db_path):
        self._seed(qualcoder_db_path)
        out = json.loads(server.list_backups())
        assert out["backup_count"] >= 6
        for b in out["backups"]:
            assert "age_days" in b and isinstance(b["age_days"], (int, float))

    def test_policyless_refused_and_preview_mutates_nothing(
            self, setup_server, qualcoder_db_path):
        seeded = self._seed(qualcoder_db_path)
        assert "error" in json.loads(server.prune_backups())
        pv = json.loads(server.prune_backups(keep_last=2))
        assert pv["requires_confirmation"] is True
        assert all(b.exists() for b in seeded)             # nothing removed
        removed_names = {r["name"] for r in pv["would_remove"]}
        assert not any("_BKUP_" in n for n in removed_names)

    def test_confirm_keep_last_floor_and_bkup_family_untouched(
            self, setup_server, qualcoder_db_path):
        seeded = self._seed(qualcoder_db_path)
        stem = Path(qualcoder_db_path).stem
        out = json.loads(server.prune_backups(keep_last=2, confirm=True))
        assert out["success"] is True, out
        survivors = {p.name for p in
                     Path(qualcoder_db_path).parent.glob(f"{stem}_*")
                     if "_backup_" in p.name or "_BKUP_" in p.name}
        # the two newest MCP backups kept; BOTH QualCoder decoys untouched
        assert f"{stem}_backup_20260720_010101.qda" in survivors
        assert f"{stem}_backup_20260715_010101.qda" in survivors
        assert f"{stem}_backup_20260601_010101.qda" not in survivors
        assert f"{stem}_BKUP_20260501_09.qda" in survivors
        assert f"{stem}_BKUP_20260502_09.qda" in survivors

    def test_conservative_intersection_of_both_criteria(self, setup_server,
                                                        qualcoder_db_path):
        self._seed(qualcoder_db_path)
        stem = Path(qualcoder_db_path).stem
        # keep_last=1 AND older_than_days=30: prune only backups that are
        # BOTH beyond the newest-1 AND older than 30 days -> only the 40d one
        pv = json.loads(server.prune_backups(keep_last=1, older_than_days=30))
        removed = {r["name"] for r in pv["would_remove"]}
        assert f"{stem}_backup_20260601_010101.qda" in removed
        assert f"{stem}_backup_20260710_010101.qda" not in removed   # 10d
        assert f"{stem}_backup_20260715_010101.qda" not in removed
        assert not any("_BKUP_" in n for n in removed)

    def test_keep_floor_unless_explicit_zero(self, setup_server,
                                             qualcoder_db_path):
        self._seed(qualcoder_db_path)
        stem = Path(qualcoder_db_path).stem
        # older_than_days=0 targets everything, but the >=1 floor holds
        out = json.loads(server.prune_backups(older_than_days=0.0,
                                              confirm=True))
        assert out["success"] is True, out
        remaining = [p for p in
                     Path(qualcoder_db_path).parent.glob(f"{stem}_backup_*")]
        assert len(remaining) >= 1                        # floor kept >=1
        # explicit keep_last=0 removes the rest
        out = json.loads(server.prune_backups(keep_last=0, confirm=True))
        assert out["success"] is True, out
        assert not list(Path(qualcoder_db_path).parent.glob(
            f"{stem}_backup_*"))
        # decoys STILL untouched
        assert len(list(Path(qualcoder_db_path).parent.glob(
            f"{stem}_BKUP_*"))) == 2

    def test_prerestore_flagged_and_prunable(self, setup_server,
                                             qualcoder_db_path):
        self._seed(qualcoder_db_path)
        pv = json.loads(server.prune_backups(keep_last=0))
        blob = json.dumps(pv)
        assert "_prerestore" in blob                      # listed
        assert "prerestore" in blob.lower()               # and flagged in note

    def test_works_while_qualcoder_lock_present(self, setup_server,
                                                qualcoder_db_path):
        """Pruning never touches data.qda — a live QualCoder lock must not
        block it (contract C.2)."""
        from qualcoder_mcp.database import QUALCODER_LOCK_FILENAME
        self._seed(qualcoder_db_path)
        lock = Path(qualcoder_db_path) / QUALCODER_LOCK_FILENAME
        lock.write_text(f"livecoder\n{time.time()}")
        try:
            out = json.loads(server.prune_backups(keep_last=1, confirm=True))
            assert out.get("success") is True, out
        finally:
            lock.unlink()
