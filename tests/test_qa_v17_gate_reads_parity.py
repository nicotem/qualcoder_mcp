"""QA v17 gate — surfaces 4-5: sub-code reads/exports (WS3) and parity
hardening (WS4), independently verified against master's recipes.
"""

import csv
import io
import json
import sqlite3
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import qualcoder_mcp.server as server
from qualcoder_mcp.database import QualcoderDatabase
from qualcoder_mcp.sessions import SessionManager

import test_v17_support as v17fix
from test_qa_v17_gate_core import _add_subcode_forest, _attach, _db, _exec, _one

FULLTEXT = v17fix.FULLTEXT
NS = "urn:QDA-XML:project:1.0"


def _forest(tmp_path):
    p = v17fix.make_project(tmp_path, "v17")
    _add_subcode_forest(p)
    _attach(p, tmp_path)
    return p


# =============================================================================
# WS3 — sub-code-aware reads and exports
# =============================================================================

class TestSubcodeListings:

    def test_listings_expose_parent_and_path(self, tmp_path):
        p = _forest(tmp_path)
        codes = {c["name"]: c for c in json.loads(server.list_all_codes())}
        subb, subsub = codes["SubB"], codes["SubSub"]
        assert subb["parent_code_id"] == 1
        assert subb["parent_code_name"] == "Stress"
        assert subb["path"] == "Category A > Stress > SubB"
        assert subsub["parent_code_id"] == 3
        assert subsub["path"] == "Category A > Stress > SubB > SubSub"
        # never presented as a bare uncategorised code
        assert "Category A" in json.dumps(subb)

        det = json.loads(server.get_code_info(4))
        assert det["parent_code_id"] == 3
        assert det["parent_code_name"] == "SubB"
        assert det["path"].endswith("SubB > SubSub")

    def test_frequencies_top_ancestor_attribution_differential(self, tmp_path):
        """Master's math (reports.py:287-313): a code's counts attribute to
        its TOP ANCESTOR code's category; per-code counts stay per-cid."""
        p = _forest(tmp_path)
        # oracle by hand: counts per cid from raw SQL
        conn = sqlite3.connect(str(_db(p)))
        raw = dict(conn.execute(
            "SELECT cid, COUNT(*) FROM code_text GROUP BY cid"))
        conn.close()
        # SubB(3): ctids 10,12 -> 2 ; SubSub(4): 11 -> 1 ; Stress(1): 1 ;
        # Coping(2): 13 -> 1
        assert raw == {1: 1, 2: 1, 3: 2, 4: 1}

        freq = json.loads(server.get_coding_frequencies())
        by_id = {c["code_id"]: c for c in freq["codes"]}
        # per-cid counts unchanged (no parent rollup into the code rows)
        for cid, n in raw.items():
            assert by_id[cid]["frequency"] == n, cid
        # top-ancestor category attribution: sub-codes carry Category A
        assert by_id[3]["category"] == "Category A"
        assert by_id[4]["category"] == "Category A"

        # export: category totals roll the whole branch in
        out = json.loads(server.export_frequencies_csv(str(tmp_path)))
        assert out["success"] is True, out
        raw_bytes = Path(out["output_path"]).read_bytes()
        rows = list(csv.reader(io.StringIO(raw_bytes.decode("utf-8-sig"))))
        table = {r[1]: r for r in rows[1:]}
        # Category A total = Stress(1)+Coping(1)+SubB(2)+SubSub(1) = 5
        assert int(table["catid:1"][-1]) == 5
        # nesting: SubSub sits deeper in the tree column than SubB
        depth = lambda s: len(s) - len(s.lstrip("-"))
        assert depth(table["cid:4"][0]) > depth(table["cid:3"][0])
        assert depth(table["cid:3"][0]) > depth(table["cid:1"][0])

    def test_report_chain_parent_codes_then_category_lineage(self, tmp_path):
        """categories_of_code (report_codes.py:1131-1175): parent CODE names
        first, then the top ancestor's category lineage."""
        p = _forest(tmp_path)
        out = json.loads(server.export_coded_segments_report(
            str(tmp_path / "coded.csv"), code_names=["SubSub"]))
        assert out["success"] is True, out
        body = Path(out["output_path"]).read_bytes().decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(body)))
        header, data = rows[0], rows[1]
        cat_cols = [i for i, h in enumerate(header)
                    if h.lower().startswith("category")]
        chain = [data[i] for i in cat_cols if data[i]]
        # leaf-to-root: SubB (parent code), Stress (top ancestor code),
        # then Category A (its category lineage)
        assert chain == ["SubB", "Stress", "Category A"], (header, data)

    def test_codebook_nests_subcodes_under_parent_code(self, tmp_path):
        p = _forest(tmp_path)
        out = json.loads(server.export_codebook(str(tmp_path / "cb.csv")))
        assert out["success"] is True, out
        body = Path(out["output_path"]).read_bytes().decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(body)))
        tree = {r[1]: r[0] for r in rows[1:]}          # Id -> Tree cell
        depth = lambda s: (len(s) - len(s.lstrip("."))) // 3   # '...' per level
        assert depth(tree["cid:3"]) == depth(tree["cid:1"]) + 1   # SubB under Stress
        assert depth(tree["cid:4"]) == depth(tree["cid:3"]) + 1   # SubSub under SubB

    def test_refi_export_nested_subcodes_and_importer_replay(self, tmp_path):
        """Sub-codes emit NESTED inside their parent's codable Code element
        (refi.py:3228-3248); master's importer rule (codable child of a
        codable parent -> supercid sub-code, refi.py:242-254) is replayed
        over the archive to prove the hierarchy round-trips."""
        p = _forest(tmp_path)
        out_path = tmp_path / "sub.qdpx"
        out = json.loads(server.export_refi_qda(str(out_path)))
        assert out["success"] is True, out

        with zipfile.ZipFile(out_path) as z:
            root = ET.fromstring(z.read("project.qde"))

        def find_code(elem, name):
            for c in elem.iter(f"{{{NS}}}Code"):
                if c.get("name") == name:
                    return c
            return None

        codes_root = root.find(f"{{{NS}}}CodeBook/{{{NS}}}Codes")
        stress = find_code(codes_root, "Stress")
        assert stress is not None and stress.get("isCodable") == "true"
        subb = stress.find(f"{{{NS}}}Code[@name='SubB']")
        assert subb is not None and subb.get("isCodable") == "true"
        subsub = subb.find(f"{{{NS}}}Code[@name='SubSub']")
        assert subsub is not None

        # importer replay: reconstruct supercid links the way master does —
        # a codable Code child of a codable Code parent becomes a sub-code
        links = {}

        def walk(elem, parent_name, parent_codable):
            for child in elem.findall(f"{{{NS}}}Code"):
                codable = child.get("isCodable", "true") == "true"
                if codable and parent_codable and parent_name is not None:
                    links[child.get("name")] = parent_name
                walk(child, child.get("name"), codable)

        for top in codes_root.findall(f"{{{NS}}}Code"):
            walk(top, top.get("name"),
                 top.get("isCodable", "true") == "true")
        assert links.get("SubB") == "Stress"
        assert links.get("SubSub") == "SubB"
        # categories never become parents of sub-code links in the replay
        assert "Category A" not in links.values() or links.get("SubB") != "Category A"

    def test_refi_referenced_subcode_pulls_parent_chain(self, tmp_path):
        """A project whose ONLY coding sits on a deep sub-code must still
        export the parent-code chain so the nesting survives."""
        p = v17fix.make_project(tmp_path, "v17")
        _add_subcode_forest(p)
        _exec(p, "DELETE FROM code_text WHERE ctid != 11")   # only SubSub's
        _attach(p, tmp_path)
        out_path = tmp_path / "leaf.qdpx"
        out = json.loads(server.export_refi_qda(str(out_path)))
        assert out["success"] is True, out
        with zipfile.ZipFile(out_path) as z:
            root = ET.fromstring(z.read("project.qde"))
        names = [c.get("name") for c in root.iter(f"{{{NS}}}Code")]
        assert "SubSub" in names and "SubB" in names and "Stress" in names


# =============================================================================
# WS4 — parity hardening
# =============================================================================

class TestParityHardening:

    def test_w12_link_writes_len_and_dedupes_both_spellings(
            self, setup_server, qualcoder_db_path):
        notes_len = len(_one(qualcoder_db_path,
                             "SELECT fulltext FROM source WHERE id=2")[0])
        out = json.loads(server.link_file_to_case(2, case_id=1))
        assert out["success"] is True
        # master convention: pos1 = len(fulltext), not len-1
        assert out["link"]["position_end"] == notes_len
        # a second link is a duplicate in EITHER spelling
        out = json.loads(server.link_file_to_case(2, case_id=1))
        assert "error" in out

        # seed the OTHER spelling on file 1 (len-1, the 3.8.2 case-manager
        # convention) and confirm the dedupe floor still catches it
        fl = len(_one(qualcoder_db_path,
                      "SELECT fulltext FROM source WHERE id=1")[0])
        _exec(qualcoder_db_path,
              "INSERT INTO case_text (caseid, fid, pos0, pos1, owner, date, memo) "
              "VALUES (1, 1, 0, ?, 'T', '2024', '')", (fl - 1,))
        server.switch_project(qualcoder_db_path)
        out = json.loads(server.link_file_to_case(1, case_id=1))
        assert "error" in out, out

    def test_p7_lone_cr_normalized_with_crlf_and_bom(self, setup_server,
                                                     qualcoder_db_path):
        out = json.loads(server.import_text_file(
            "cr.txt", "﻿alpha\rbeta\r\ngamma\rdelta",
            create_backup=False))
        stored = _one(qualcoder_db_path,
                      "SELECT fulltext FROM source WHERE id=?",
                      (out["file_id"],))[0]
        assert stored == "alpha\nbeta\ngamma\ndelta"

    def test_d3_backup_ignores_sqlite_sidecars_and_locks(self, setup_server,
                                                         qualcoder_db_path):
        folder = Path(qualcoder_db_path)
        decoys = ["search.sqlite", "search.sqlite-tmp42", "ai.sqlite-wal",
                  "ai.sqlite-shm", "vec.sqlite-journal", "stale.lock"]
        for name in decoys:
            (folder / name).write_bytes(b"x" * 64)
        out = json.loads(server.import_text_file("d3probe.txt", "content"))
        backup = Path(out["backup_path"])
        copied = {p.name for p in backup.rglob("*") if p.is_file()}
        for name in decoys:
            assert name not in copied, name
        assert "data.qda" in copied

    def test_d4_settings_directory_note_scoped(self, setup_server,
                                               qualcoder_db_path):
        json.loads(server.import_text_file("d4probe.txt", "x"))
        listed = json.loads(server.list_backups())
        notes = " ".join(listed["notes"])
        assert "3.8.0" in notes and "3.8.2" in notes   # scoped, not universal

    def test_w15_journal_attribute_domain_tolerated_not_counted(
            self, setup_server, qualcoder_db_path):
        _exec(qualcoder_db_path,
              "INSERT INTO attribute_type VALUES "
              "('Mood', '2024', 'T', '', 'journal', 'character')")
        _exec(qualcoder_db_path,
              "INSERT INTO attribute VALUES "
              "(70, 'Mood', 'journal', 'high', 1, '2024', 'T')")
        server.switch_project(qualcoder_db_path)
        # listed with its own domain
        listed = json.loads(server.list_attribute_types())
        mood = next(a for a in listed["attributes"] if a["name"] == "Mood")
        assert mood["applies_to"] == "journal"
        # never counted into file/case surfaces
        assert all(a["name"] != "Mood" for a in
                   json.loads(server.get_file_attributes(1))["attributes"])
        assert all(a["name"] != "Mood" for a in
                   json.loads(server.get_case_attributes(1))["attributes"])
        # import back-fill is domain-exact: no journal placeholder on files
        out = json.loads(server.import_text_file("w15.txt", "x",
                                                 create_backup=False))
        assert _one(qualcoder_db_path,
                    "SELECT COUNT(*) FROM attribute WHERE name='Mood' "
                    "AND id=? AND attr_type='file'", (out["file_id"],))[0] == 0

    def test_w16_emoji_and_agent_owner_names_opaque(self, setup_server,
                                                    qualcoder_db_path):
        for owner in ("📌 Speaker coding", "AI Agent"):
            _exec(qualcoder_db_path,
                  "INSERT INTO code_text (cid, fid, seltext, pos0, pos1, owner) "
                  "VALUES (2, 1, ?, 57, 77, ?)",
                  ("I cope by exercising", owner))
        server.switch_project(qualcoder_db_path)
        segs = json.loads(server.get_coded_segments(2))
        owners = {s["owner"] for s in segs["segments"]}
        assert {"📌 Speaker coding", "AI Agent"} <= owners
        res = json.loads(server.search_coded_text("exercising"))
        assert res["result_count"] >= 2
        freq = json.loads(server.get_coding_frequencies())
        assert "error" not in freq
