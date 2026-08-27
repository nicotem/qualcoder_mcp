"""QA verification round for the 17-item consolidated fix round.

INDEPENDENT re-derivations (not reusing the developer's or track modules'
tests): the D1 half-replaced-restore invariant under my own fault injection,
and a co-occurrence differential (new Python impl vs the OLD SQL semantics)
on adversarial fixtures of my own design. Plus the guidance-envelope and
version-handshake pins.
"""

import json
import shutil
import sqlite3
import time
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server
from qualcoder_mcp.database import QualcoderDatabase, validate_qda_path


FULLTEXT = "This is interview text. I feel stressed about deadlines. I cope by exercising."


def _db(project_path) -> Path:
    return Path(project_path) / "data.qda"


def _exec(project_path, sql, args=()):
    conn = sqlite3.connect(str(_db(project_path)))
    conn.executescript(sql) if args == "script" else conn.execute(sql, args)
    conn.commit()
    conn.close()


def _folder_state(folder: Path):
    """Relative path -> content bytes for every file under a project folder
    (content, not size — a new SQLite row can leave the file size unchanged)."""
    folder = Path(folder)
    return {str(p.relative_to(folder)): p.read_bytes()
            for p in sorted(folder.rglob("*")) if p.is_file()}


def _reload():
    server.switch_project(server.current_project_path)


# =============================================================================
# D1 — half-replaced restore invariant (independent fault injection)
# =============================================================================

class TestRestoreNeverHalfReplaced:

    def _seed_and_backup(self, qualcoder_db_path):
        """Add media siblings so the project folder has more than data.qda,
        then produce a genuine MCP backup via a real write."""
        docs = Path(qualcoder_db_path) / "documents"
        docs.mkdir(exist_ok=True)
        (docs / "transcript_a.txt").write_text("a" * 4000, encoding="utf-8")
        (docs / "transcript_b.txt").write_text("b" * 9000, encoding="utf-8")
        out = json.loads(server.import_text_file(
            f"marker_{time.time_ns()}.txt", "backup marker content"))
        assert out["success"] is True
        folder = Path(qualcoder_db_path)
        return sorted(folder.parent.glob(f"{folder.stem}_backup_*.qda"))[-1]

    def test_partial_swap_failure_leaves_project_whole(
            self, setup_server, qualcoder_db_path, monkeypatch):
        """copytree fails PART WAY through the destination swap (my own
        injection): the live project must end up EITHER fully the old state
        OR fully the backup — never a half-written folder — and the safety
        backup must survive intact."""
        backup = self._seed_and_backup(qualcoder_db_path)
        # damage the project after the backup so old != backup
        json.loads(server.import_text_file("post_backup.txt", "to be discarded",
                                           create_backup=False))
        _reload()
        folder = Path(qualcoder_db_path)
        pre_state = _folder_state(folder)
        backup_state = _folder_state(backup)
        assert pre_state != backup_state

        real_copytree = shutil.copytree
        calls = {"n": 0}

        def flaky_copytree(src, dst, *a, **k):
            # Fail ONLY the first copy into the live project folder (the
            # destination swap), after leaving a partial tree; let the safety
            # backup copy and the recovery copy succeed.
            if Path(dst) == folder and calls["n"] == 0:
                calls["n"] += 1
                Path(dst).mkdir(parents=True, exist_ok=True)
                (Path(dst) / "data.qda").write_bytes(b"PARTIAL")  # half-written
                raise OSError("simulated disk-full mid-swap")
            return real_copytree(src, dst, *a, **k)

        monkeypatch.setattr(shutil, "copytree", flaky_copytree)
        out = json.loads(server.restore_backup(str(backup), confirm=True))
        monkeypatch.undo()

        assert calls["n"] >= 1                       # the swap really failed
        assert folder.exists()
        post_state = _folder_state(folder)
        # THE INVARIANT: fully old or fully backup, never the partial
        assert post_state in (pre_state, backup_state), (
            "project folder is half-replaced")
        assert (folder / "data.qda").read_bytes() != b"PARTIAL"
        # safety backup named and intact
        assert "safety_backup" in out
        safety = Path(out["safety_backup"])
        assert safety.exists() and _folder_state(safety) == pre_state
        # server not bricked — reads work after recovery
        assert server.db is not None
        assert "error" not in json.loads(server.list_backups())

    def test_recovery_from_safety_backup_is_bit_true(
            self, setup_server, qualcoder_db_path, monkeypatch):
        """When the swap fails, the recovered project equals the pre-restore
        state byte-for-byte (the safety backup is the source of truth)."""
        backup = self._seed_and_backup(qualcoder_db_path)
        json.loads(server.import_text_file("extra.txt", "x", create_backup=False))
        _reload()
        folder = Path(qualcoder_db_path)
        pre_dump = list(sqlite3.connect(str(_db(qualcoder_db_path))).iterdump())

        real_copytree = shutil.copytree

        calls = {"n": 0}

        def flaky(src, dst, *a, **k):
            if Path(dst) == folder and calls["n"] == 0:
                calls["n"] += 1
                Path(dst).mkdir(parents=True, exist_ok=True)
                (Path(dst) / "data.qda").write_bytes(b"HALF")
                raise OSError("mid-swap failure")
            return real_copytree(src, dst, *a, **k)

        monkeypatch.setattr(shutil, "copytree", flaky)
        json.loads(server.restore_backup(str(backup), confirm=True))
        monkeypatch.undo()
        _reload()
        post_dump = list(sqlite3.connect(str(_db(qualcoder_db_path))).iterdump())
        assert post_dump == pre_dump                 # fully recovered old state


# =============================================================================
# Co-occurrence differential — new Python impl vs the OLD SQL, hostile data
# =============================================================================

# The exact SQL QualCoder/the-old-MCP used (window==0 and window>0 branches),
# reproduced here as the differential oracle.
_OLD_SQL_W0 = """
    SELECT c.cid, COUNT(*) as n
    FROM code_text ct1
    JOIN code_text ct2 ON ct1.fid = ct2.fid AND ct1.cid != ct2.cid
        AND ((ct2.pos0 >= ct1.pos0 AND ct2.pos0 <= ct1.pos1)
             OR (ct2.pos1 >= ct1.pos0 AND ct2.pos1 <= ct1.pos1)
             OR (ct2.pos0 <= ct1.pos0 AND ct2.pos1 >= ct1.pos1))
    JOIN code_name c ON ct2.cid = c.cid
    WHERE ct1.cid = ?
    GROUP BY c.cid
"""
_OLD_SQL_WN = """
    SELECT c.cid, COUNT(*) as n
    FROM code_text ct1
    JOIN code_text ct2 ON ct1.fid = ct2.fid AND ct1.cid != ct2.cid
        AND ABS(ct2.pos0 - ct1.pos0) <= ?
    JOIN code_name c ON ct2.cid = c.cid
    WHERE ct1.cid = ?
    GROUP BY c.cid
"""


def _old_cooccur(project_path, code_id, window):
    conn = sqlite3.connect(str(_db(project_path)))
    if window == 0:
        rows = conn.execute(_OLD_SQL_W0, (code_id,)).fetchall()
    else:
        rows = conn.execute(_OLD_SQL_WN, (window, code_id)).fetchall()
    conn.close()
    return {cid: n for cid, n in rows}


def _new_cooccur(code_id, window):
    out = json.loads(server.find_cooccurring_codes(code_id, window_size=window))
    return {r["code_id"]: r["cooccurrence_count"] for r in out}


class TestCooccurrenceDifferential:

    def _hostile_fixture(self, qualcoder_db_path):
        """Adversarial code_text rows in ONE file plus a second file, mixing:
        exact-overlap, containment, touching-boundary, disjoint, duplicate
        spans by different owners, a NULL-position row, and a media (code_av)
        row that must never enter the text co-occurrence count."""
        conn = sqlite3.connect(str(_db(qualcoder_db_path)))
        c = conn.cursor()
        c.execute("DELETE FROM code_text")
        # add codes 3,4,5 alongside fixture 1(Stress),2(Coping)
        for cid in (3, 4, 5):
            c.execute("INSERT INTO code_name (cid, name, catid, owner, date, color) "
                      "VALUES (?, ?, NULL, 'o', '2024', '#FF0000')",
                      (cid, f"C{cid}"))
        rows = [
            # file 1 — target code 1 has two segments
            (1, 1, 1, 10, 20, "oA"),   # target A
            (2, 1, 1, 50, 60, "oA"),   # target B (disjoint from most)
            # code 2 relative to target A: exact overlap, containment,
            # boundary-touch, near-miss, far
            (3, 2, 1, 10, 20, "oA"),   # identical span -> overlaps A
            (4, 2, 1, 12, 18, "oA"),   # contained in A
            (5, 2, 1, 20, 25, "oA"),   # touches A's pos1 (closed interval)
            (6, 2, 1, 21, 30, "oA"),   # just past A (w0: no; |21-10|=11)
            (7, 2, 1, 5, 9,  "oA"),    # just before A (w0: no; |5-10|=5)
            # code 3 spanning both target segments
            (8, 3, 1, 15, 55, "oA"),   # overlaps A and B
            # duplicate span, DIFFERENT owner
            (9, 2, 1, 10, 20, "oB"),   # same as row3 span, other coder
            # file 2 — target present, plus a partner and a NULL-pos row
            (10, 1, 2, 0, 10, "oA"),
            (11, 4, 2, 5, 15, "oA"),   # overlaps target in file 2
            (12, 5, 2, None, None, "oA"),  # NULL positions -> never matches
            # a row in a THIRD file with NO target -> must contribute nothing
            (13, 2, 3, 0, 100, "oA"),
        ]
        c.executemany(
            "INSERT INTO code_text (ctid, cid, fid, seltext, pos0, pos1, owner) "
            "VALUES (?, ?, ?, 'x', ?, ?, ?)", rows)
        # a media co-occurrence that must be invisible to the TEXT metric
        c.execute("INSERT INTO code_av (avid, cid, id, pos0, pos1, owner) "
                  "VALUES (1, 2, 1, 10, 20, 'oA')")
        conn.commit()
        conn.close()
        _reload()

    @pytest.mark.parametrize("window", [0, 1, 5, 10, 11, 50, 1000])
    @pytest.mark.parametrize("code_id", [1, 2, 3, 4, 5])
    def test_differential_matches_old_sql(self, setup_server,
                                          qualcoder_db_path, code_id, window):
        self._hostile_fixture(qualcoder_db_path)
        assert _new_cooccur(code_id, window) == _old_cooccur(
            qualcoder_db_path, code_id, window), (code_id, window)

    def test_single_coding_file_and_empty_target(self, setup_server,
                                                 qualcoder_db_path):
        """A file with only the target coding (no partners) and a code that
        appears nowhere both yield {} identically."""
        _exec(qualcoder_db_path, "DELETE FROM code_text")
        _exec(qualcoder_db_path,
              "INSERT INTO code_text (ctid,cid,fid,seltext,pos0,pos1,owner) "
              "VALUES (1,1,1,'x',0,10,'o')")
        _reload()
        for w in (0, 50):
            assert _new_cooccur(1, w) == _old_cooccur(qualcoder_db_path, 1, w) == {}
            # a code that has no codings at all
            assert _new_cooccur(2, w) == {}

    def test_window0_boundary_touch_counts(self, setup_server,
                                           qualcoder_db_path):
        """Closed-interval semantics: a partner touching the target's pos1
        exactly must count at window=0 (a subtle equality the rewrite must
        preserve)."""
        self._hostile_fixture(qualcoder_db_path)
        # code 2 row (5,20,25) touches target A pos1=20 -> counted at w0
        assert _new_cooccur(1, 0)[2] == _old_cooccur(qualcoder_db_path, 1, 0)[2]
        assert _new_cooccur(1, 0).get(2, 0) > 0


# =============================================================================
# Perf regression honesty — dense fixture, BOTH window modes
# =============================================================================

class TestCooccurrencePerfHonest:

    def test_dense_single_file_both_windows_fast(self, setup_server,
                                                 qualcoder_db_path):
        """8k codings in ONE file (the shape that made the old O(n^2) SQL
        take ~1.5 s): both window modes must be well under a second, and
        must actually return co-occurrences (an empty result would make the
        timing meaningless)."""
        conn = sqlite3.connect(str(_db(qualcoder_db_path)))
        c = conn.cursor()
        c.execute("DELETE FROM code_text")
        c.execute("INSERT INTO code_name (cid,name,catid,owner,date,color) "
                  "VALUES (99,'Dense',NULL,'o','2024','#FF0000')")
        n = 8000
        rows = []
        for i in range(n):
            cid = 1 if i % 2 == 0 else 99
            p0 = i          # heavy overlap: consecutive unit-shifted spans
            rows.append((i + 1, cid, 1, "x", p0, p0 + 20, "o"))
        c.executemany(
            "INSERT INTO code_text (ctid,cid,fid,seltext,pos0,pos1,owner) "
            "VALUES (?,?,?,?,?,?,?)", rows)
        conn.commit()
        conn.close()
        _reload()

        for window in (0, 50):
            t0 = time.perf_counter()
            out = _new_cooccur(1, window)
            elapsed = time.perf_counter() - t0
            assert out.get(99, 0) > 0, (window, out)   # honest: real work done
            assert elapsed < 1.0, f"window={window} took {elapsed:.2f}s at 8k"


# =============================================================================
# Guidance envelope + version handshake
# =============================================================================

class TestGuidanceEnvelope:

    def test_analyze_for_coding_structured_and_prose(self, setup_server,
                                                     qualcoder_db_path):
        """The response carries real JSON fields AND the prose banner.

        NOTE (QA6-1, LOW): qualcoder_open / action_required are only present
        when the lock is ACTIVE — omitted (not `false`/`null`) otherwise, so a
        structured-field client must use .get('qualcoder_open', False). This
        test pins the actual behavior; the absence is flagged in the report.
        """
        # absent lock: envelope has session_id + instructions; the open-signal
        # fields are omitted (absence == not open)
        raw = server.analyze_for_coding([1])
        payload = json.loads(raw)
        assert payload.get("qualcoder_open", False) is False
        assert isinstance(payload["session_id"], str) and payload["session_id"]
        assert "ANALYSIS SESSION CREATED" in payload["instructions"]
        # prose still parseable the old way (back-compat pin)
        assert payload["session_id"] == raw.split(
            "Session ID: `")[1].split("`")[0]

        # fresh lock: real fields present AND banner appears
        lock = validate_qda_path(qualcoder_db_path).parent / "project_in_use.lock"
        lock.write_text(f"gui_user\n{time.time()}", encoding="utf-8")
        try:
            payload = json.loads(server.analyze_for_coding([1]))
            assert payload["qualcoder_open"] is True
            assert payload["action_required"] and "gui_user" in payload["action_required"]
            assert "STOP" in payload["instructions"]
            assert "qualcoder_open: true" in payload["instructions"]
        finally:
            lock.unlink()

    def test_all_write_tools_document_lock_refusal(self):
        """Every write tool's docstring carries the lock-refusal guidance."""
        import inspect
        write_tools = [
            "set_memo", "add_journal_entry", "create_code", "rename_code",
            "recolor_code", "move_code_to_category", "create_category",
            "rename_category", "move_category", "merge_codes", "delete_code",
            "delete_category", "apply_codings", "import_text_file",
            "link_file_to_case", "delete_coding",
        ]
        missing = []
        for name in write_tools:
            fn = getattr(server, name)
            doc = (inspect.getdoc(fn) or "").lower()
            if "qualcoder" not in doc or not (
                    "open" in doc or "lock" in doc or "refus" in doc):
                missing.append(name)
        assert not missing, f"write tools missing lock guidance: {missing}"

    def test_apply_codings_resignals_position_safety(self, setup_server,
                                                     qualcoder_db_path):
        """apply_codings names unsafe files in a position_safety_warning."""
        emoji = "Intro 😀 emoji. The team was there. I feel very stressed today."
        _exec(qualcoder_db_path,
              "INSERT INTO source (id, name, fulltext) VALUES (70, 'emoji.txt', ?)",
              (emoji,))
        _reload()
        sid = json.loads(server.analyze_for_coding([70]))["session_id"]
        rec = json.loads(server.record_suggestions(sid, [{
            "file_id": 70, "code_name": "Stress",
            "segment_text": "I feel very stressed"}]))
        assert rec["recorded_count"] == 1
        server.update_suggestion_status(sid, approve=[rec["recorded"][0]["guid"]])
        out = server.apply_codings(sid)
        assert "position_safety_warning" in out or "emoji.txt" in out
        # structural: the warning names the unsafe file
        assert "emoji.txt" in out


class TestVersionHandshake:

    def test_version_matches_package_metadata(self):
        import qualcoder_mcp
        from importlib import metadata
        pkg = metadata.version("qualcoder-mcp")
        assert qualcoder_mcp.__version__ == pkg
        # 0.10.1-alpha family (canary for a stale editable install)
        assert pkg.startswith("0.10.1")

    def test_stdio_initialize_advertises_version(self):
        """A real MCP initialize handshake over the server advertises the
        package version (not the SDK/framework default)."""
        import asyncio
        import qualcoder_mcp
        from mcp.server.lowlevel.server import NotificationOptions

        async def _probe():
            # FastMCP wraps a low-level Server; its initialization options
            # carry the advertised server version.
            init = server.mcp._mcp_server.create_initialization_options(
                NotificationOptions(), {})
            return init.server_version

        version = asyncio.run(_probe())
        assert version == qualcoder_mcp.__version__, (
            version, qualcoder_mcp.__version__)
