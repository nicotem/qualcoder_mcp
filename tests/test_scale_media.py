# Folded into the main suite from the v0.6.0-alpha parallel test campaign
# (track 6, scale & media); adapted paths/fixtures only — test logic unchanged.
"""Track 6 — scale + non-text media stress tests for the QualCoder MCP server.

Two jobs live here:

1. A **pytest** suite (suite-friendly small sizes; the 10k-coding build is
   opt-in behind TRACK6_GIANT=1) asserting the read surface, the
   record->approve->apply / delete / restore write loop, media graceful
   degradation, and the read-only + full-backup safety invariants.

2. A **benchmark runner** (``python test_track6_scale_media.py``) that builds
   progressively larger valid v14 projects (up to 320 files / ~17k codings /
   a 500k-char document), times every read tool and the write loop with
   per-call wall-clock + peak Python-heap memory, exercises a mixed
   text+media project, and writes ``report.md`` next to this file.

Run ONLY against the worktree venv. Generated projects live under
``tests-out/track6/`` and nothing here touches real QDA projects or the MCP
default workspace.
"""

import os
import gc
import sys
import json
import time
import shutil
import sqlite3
import tempfile
import tracemalloc
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))
import tempfile
_GEN = Path(tempfile.mkdtemp(prefix="qc_scale_"))   # generated projects/exports
import track6_build as tb  # noqa: E402

import qualcoder_mcp.server as server  # noqa: E402
from qualcoder_mcp.database import QualcoderDatabase  # noqa: E402
from qualcoder_mcp.sessions import SessionManager, AICodingSession, CodingSuggestion  # noqa: E402

GIANT = bool(os.environ.get("TRACK6_GIANT"))
SLOW_THRESHOLD_MS = 1000.0  # mission: flag anything > ~1s


# ---------------------------------------------------------------------------
# harness helpers
# ---------------------------------------------------------------------------
def connect(project_folder: Path, sessions_dir: Path, read_only: bool = True):
    """Point the server globals at a project (mirrors conftest.setup_server)."""
    if server.db is not None:
        try:
            server.db.close()
        except Exception:
            pass
    server.db = QualcoderDatabase(str(project_folder), read_only=read_only)
    server.current_project_path = str(project_folder)
    sessions_dir.mkdir(parents=True, exist_ok=True)
    server.session_manager = SessionManager(str(sessions_dir))
    return server


def time_call(fn, *args, repeats: int = 1, **kwargs):
    """Return (result, wall_ms_best, peak_kib).

    peak_kib is the Python-heap ALLOCATION DELTA of a single call (peak minus
    the live baseline), measured after gc.collect() so uncollected garbage from
    a prior call cannot inflate it. Note: SQLite's C-level work (the co-occurrence
    self-join, etc.) is invisible to tracemalloc — those tools cost CPU/wall, not
    Python heap.
    """
    if not tracemalloc.is_tracing():
        tracemalloc.start()
    best_ms = None
    peak_delta_kib = 0
    result = None
    for _ in range(repeats):
        gc.collect()
        tracemalloc.reset_peak()
        base, _ = tracemalloc.get_traced_memory()
        t0 = time.perf_counter()
        result = fn(*args, **kwargs)
        dt = (time.perf_counter() - t0) * 1000.0
        _, peak = tracemalloc.get_traced_memory()
        peak_delta_kib = max(peak_delta_kib, (peak - base) // 1024)
        best_ms = dt if best_ms is None else min(best_ms, dt)
    return result, round(best_ms, 2), peak_delta_kib


def _err(result_json: str):
    """Return the 'error' string if a tool returned an error JSON, else None."""
    try:
        obj = json.loads(result_json)
    except (ValueError, TypeError):
        return None
    if isinstance(obj, dict) and "error" in obj:
        return obj["error"]
    return None


def db_counts(project_folder: Path) -> dict:
    """Raw row counts + data.qda size, read straight from SQLite (ground truth)."""
    conn = sqlite3.connect(str(project_folder / "data.qda"))
    try:
        out = {}
        for t in ("code_text", "code_av", "code_image", "source", "code_name",
                  "code_cat", "cases", "case_text", "attribute"):
            out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    finally:
        conn.close()
    out["db_bytes"] = (project_folder / "data.qda").stat().st_size
    return out


def file_fulltext(project_folder: Path, fid: int) -> str:
    conn = sqlite3.connect(str(project_folder / "data.qda"))
    try:
        row = conn.execute("SELECT fulltext FROM source WHERE id=?", (fid,)).fetchone()
        return row[0] if row else ""
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# the read surface, as (label, callable-factory) so we can run it per project
# ---------------------------------------------------------------------------
def read_surface(hot_code: int, big_fid: int, ordinary_fid: int):
    """Yield (label, fn) for every read tool exercised at scale."""
    return [
        ("get_project_summary", lambda: server.get_project_summary()),
        ("get_coding_frequencies", lambda: server.get_coding_frequencies()),
        ("list_all_files", lambda: server.list_all_files()),
        ("search_coded_text", lambda: server.search_coded_text("stress", limit=50)),
        ("get_coded_segments(100)", lambda: server.get_coded_segments(hot_code, limit=100)),
        ("get_coded_segments(5000)", lambda: server.get_coded_segments(hot_code, limit=5000)),
        ("find_cooccurring_codes(w=0)", lambda: server.find_cooccurring_codes(hot_code)),
        ("find_cooccurring_codes(w=50)", lambda: server.find_cooccurring_codes(hot_code, window_size=50)),
        ("get_case_code_matrix", lambda: server.get_case_code_matrix()),
        ("search_files(filename)", lambda: server.search_files("interview", limit=100)),
        ("search_files(content)", lambda: server.search_files("stress", search_content=True, limit=100)),
        ("search_memos", lambda: server.search_memos("memo", limit=50)),
        ("query_by_attribute(case)", lambda: server.query_by_attribute("Age", "30", attr_type="case", operator="gte")),
        ("list_attribute_types", lambda: server.list_attribute_types()),
        ("get_file_attributes", lambda: server.get_file_attributes(ordinary_fid)),
        ("analyze_file_with_coding(BIG)", lambda: server.analyze_file_with_coding(big_fid)),
        ("get_file_content(BIG)", lambda: server.get_file_content(big_fid)),
    ]


# ===========================================================================
# PYTEST FIXTURES (small, built once per session)
# ===========================================================================
@pytest.fixture(scope="session")
def scratch(tmp_path_factory):
    return tmp_path_factory.mktemp("track6")


@pytest.fixture(scope="session")
def small_scale(scratch):
    proj = scratch / "scale_small.qda"
    stats = tb.build_scale_project(
        proj, n_files=40, n_codes=12, n_categories=10, n_cases=8,
        target_codings=1200, big_doc_chars=60_000, n_hot_files=2)
    return proj, stats


@pytest.fixture(scope="session")
def media_proj(scratch):
    proj = scratch / "media.qda"
    stats = tb.build_media_project(proj)
    return proj, stats


# ===========================================================================
# 1. READ SURFACE AT SCALE
# ===========================================================================
class TestReadSurface:
    def test_all_read_tools_run_clean_and_fast(self, small_scale, scratch):
        proj, stats = small_scale
        connect(proj, scratch / "sess_read")
        slow = []
        for label, fn in read_surface(hot_code=1, big_fid=stats["big_doc_fid"],
                                      ordinary_fid=stats["files"]):
            result, ms, peak = time_call(fn)
            assert isinstance(result, str) and result, f"{label} returned empty"
            assert _err(result) is None, f"{label} errored: {_err(result)}"
            if ms > SLOW_THRESHOLD_MS:
                slow.append((label, ms))
        assert not slow, f"tools exceeded {SLOW_THRESHOLD_MS}ms at small scale: {slow}"

    def test_cooccurrence_returns_partners(self, small_scale, scratch):
        proj, stats = small_scale
        connect(proj, scratch / "sess_read")
        cooc = json.loads(server.find_cooccurring_codes(1))
        assert isinstance(cooc, list) and cooc, "expected co-occurrence partners in a hot file"
        assert all("cooccurrence_count" in c and c["cooccurrence_count"] > 0 for c in cooc)

    def test_matrix_shape(self, small_scale, scratch):
        proj, stats = small_scale
        connect(proj, scratch / "sess_read")
        m = json.loads(server.get_case_code_matrix())
        assert len(m["cases"]) == stats["cases"]
        assert len(m["codes"]) == stats["codes"]
        assert sum(len(v) for v in m["matrix"].values()) > 0

    def test_big_document_handled(self, small_scale, scratch):
        proj, stats = small_scale
        connect(proj, scratch / "sess_read")
        fc = json.loads(server.get_file_content(stats["big_doc_fid"]))
        assert len(fc.get("content", "")) == stats["big_doc_chars"]


# ===========================================================================
# 2. WRITE LOOP  (record -> approve -> apply -> delete -> restore)
# ===========================================================================
class TestWriteLoop:
    def _mk_writable(self, scratch):
        proj = scratch / "scale_write.qda"
        if proj.exists():
            shutil.rmtree(proj)
        stats = tb.build_scale_project(
            proj, n_files=20, n_codes=8, n_categories=6, n_cases=4,
            target_codings=300, big_doc_chars=20_000, n_hot_files=1)
        connect(proj, scratch / "sess_write")
        return proj, stats

    def test_full_write_loop(self, scratch):
        proj, stats = self._mk_writable(scratch)
        before = db_counts(proj)

        # a session bound to this project
        session = AICodingSession(project_path=str(proj), description="track6 write",
                                  file_ids=[10], code_names=["Code 001"])
        server.session_manager.save_session(session)
        sid = session.session_id

        # build valid suggestions with exact fulltext slices on an ordinary file
        fid = 10
        text = file_fulltext(proj, fid)
        suggs = []
        for i in range(5):
            p0 = 200 + i * 60
            p1 = p0 + 25
            suggs.append({"file_id": fid, "code_id": 1, "start_pos": p0, "end_pos": p1,
                          "segment_text": text[p0:p1], "reasoning": f"s{i}", "confidence": 0.9})

        rec = json.loads(server.record_suggestions(sid, suggs))
        assert rec["recorded_count"] == 5, rec
        guids = [r["guid"] for r in rec["recorded"]]

        server.update_suggestion_status(sid, approve=guids)
        applied = server.apply_codings(sid, create_backup=True)
        assert "CODINGS APPLIED" in applied, applied

        after_apply = db_counts(proj)
        assert after_apply["code_text"] == before["code_text"] + 5

        # a backup was made and is a COMPLETE copy (safety invariant)
        backups = json.loads(server.list_backups())
        assert backups["backup_count"] >= 1
        newest = Path(backups["backups"][0]["path"])
        assert (newest / "data.qda").exists()
        assert (newest / "data.qda").stat().st_size >= before["db_bytes"] * 0.9

        # find a ctid we just wrote and delete it
        conn = sqlite3.connect(str(proj / "data.qda"))
        ctid = conn.execute(
            "SELECT ctid FROM code_text WHERE owner='AI Coding Assistant' LIMIT 1").fetchone()[0]
        conn.close()
        deleted = json.loads(server.delete_coding(ctid))
        assert deleted.get("success") is True, deleted
        assert db_counts(proj)["code_text"] == after_apply["code_text"] - 1

        # restore from the newest backup (which predates the delete) and confirm
        restore_target = Path(json.loads(server.list_backups())["backups"][-1]["path"])
        preview = json.loads(server.restore_backup(str(restore_target)))
        assert preview.get("requires_confirmation") is True
        done = json.loads(server.restore_backup(str(restore_target), confirm=True))
        assert done.get("success") is True, done


# ===========================================================================
# 3. MEDIA GRACEFUL DEGRADATION
# ===========================================================================
class TestMedia:
    def test_summary_reports_media_file_types(self, media_proj, scratch):
        proj, stats = media_proj
        connect(proj, scratch / "sess_media")
        s = json.loads(server.get_project_summary())
        ft = s["file_types"]
        for t in ("image", "audio", "video", "pdf", "text"):
            assert t in ft, f"file_types missing '{t}': {ft}"
        # text-oriented count reflects code_text only (documented limitation)
        assert s["statistics"]["total_coded_segments"] == stats["text_codings"]

    def test_list_files_labels_media(self, media_proj, scratch):
        proj, stats = media_proj
        connect(proj, scratch / "sess_media")
        files = json.loads(server.list_all_files())
        by_name = {f["name"]: f for f in files["files"]} if isinstance(files, dict) else {f["name"]: f for f in files}
        assert by_name["photo_scene.png"]["type"] == "image"
        assert by_name["call_recording.mp3"]["type"] == "audio"
        assert by_name["session_video.mp4"]["type"] == "video"
        assert by_name["handbook.pdf"]["type"] == "pdf"

    def test_content_search_skips_media_and_discloses(self, media_proj, scratch):
        proj, stats = media_proj
        connect(proj, scratch / "sess_media")
        res = json.loads(server.search_files("the", search_content=True, limit=50))
        perf = res["performance_info"]
        assert perf["files_skipped_no_text"] >= 3, perf  # png, jpg, mp3, mp4
        assert "skip_note" in perf
        # no image/audio/video source appears as a CONTENT match
        for r in res["results"]:
            if r["matched_in"].get("content"):
                assert r["file_type"] in ("text", "pdf"), r

    def test_text_tools_do_not_mutate_media_rows(self, media_proj, scratch):
        proj, stats = media_proj
        connect(proj, scratch / "sess_media")
        before = db_counts(proj)
        for label, fn in read_surface(hot_code=1, big_fid=1, ordinary_fid=1):
            fn()
        after = db_counts(proj)
        assert after["code_av"] == before["code_av"] == stats["av_codings"]
        assert after["code_image"] == before["code_image"] == stats["image_codings"]

    def test_get_file_content_flags_media_nontext(self, media_proj, scratch):
        proj, stats = media_proj
        connect(proj, scratch / "sess_media")
        fc = json.loads(server.get_file_content(6))  # audio
        assert fc["is_text"] is False
        assert fc["media_path"] == "/audio/call_recording.mp3"
        assert fc["content"] == ""  # no text, no crash

    def test_media_only_code_reads_do_not_crash(self, media_proj, scratch):
        proj, stats = media_proj
        connect(proj, scratch / "sess_media")
        # Gesture (cid 1) exists only on code_image/code_av, never code_text
        seg = json.loads(server.get_coded_segments(1))
        assert seg["segment_count"] == 0  # text tool sees no text codings, graceful
        cooc = json.loads(server.find_cooccurring_codes(1))
        assert cooc == []
        freq = {c["code_id"]: c["frequency"] for c in json.loads(server.get_coding_frequencies())["codes"]}
        assert freq[1] == 0  # media-only code shows 0 text frequency

    def test_analyze_media_source_flagged(self, media_proj, scratch):
        """Track6 LOW finding, FIXED: analyze_file_with_coding on a media
        source now carries is_text/type in file_info plus an explanatory
        note, so an empty full_text is no longer indistinguishable from a
        genuinely empty text file."""
        proj, stats = media_proj
        connect(proj, scratch / "sess_media")
        img = json.loads(server.analyze_file_with_coding(4))  # image source
        assert img.get("full_text") == "" and img["statistics"]["total_segments"] == 0
        fi = img["file_info"]
        assert fi["is_text"] is False
        assert fi["type"] == "image"
        assert "not" in img["note"] and "text" in img["note"]
        # a text source is flagged as text and carries no media note
        txt = json.loads(server.analyze_file_with_coding(1))
        assert txt["file_info"]["is_text"] is True
        assert "note" not in txt

    def test_refi_export_mixed_project(self, media_proj, scratch):
        proj, stats = media_proj
        connect(proj, scratch / "sess_media")
        before = db_counts(proj)
        out = _GEN / "media_export.qdpx"
        out.parent.mkdir(exist_ok=True)
        if out.exists():
            out.unlink()
        res = json.loads(server.export_refi_qda(str(out), overwrite=True))
        assert res.get("success") is True, res
        assert out.exists()
        # export carries text codings only; media rows are NOT represented
        assert res["codings_exported"] == stats["text_codings"]
        # DB media rows are untouched by the export
        after = db_counts(proj)
        assert after["code_av"] == before["code_av"]
        assert after["code_image"] == before["code_image"]
        # the .qdpx contains no code_av/code_image concept — confirm archive is
        # a well-formed zip with project.qde and only text source payloads
        import zipfile
        with zipfile.ZipFile(out) as z:
            names = z.namelist()
            assert "project.qde" in names
            assert all(n == "project.qde" or n.startswith("sources/") for n in names)


# ===========================================================================
# 4. SAFETY INVARIANTS
# ===========================================================================
class TestSafety:
    def test_reads_never_write(self, small_scale, scratch):
        proj, stats = small_scale
        connect(proj, scratch / "sess_read")
        before = db_counts(proj)
        for label, fn in read_surface(hot_code=1, big_fid=stats["big_doc_fid"],
                                      ordinary_fid=stats["files"]):
            fn()
        assert db_counts(proj) == before

    def test_readonly_connection_rejects_writes(self, small_scale, scratch):
        proj, stats = small_scale
        connect(proj, scratch / "sess_read", read_only=True)
        with pytest.raises(sqlite3.OperationalError):
            server.db.conn.execute("INSERT INTO journal (jid,name,jentry) VALUES (999,'x','y')")

    def test_large_backup_not_skipped_or_truncated(self, scratch):
        """A write on a larger project must produce a COMPLETE backup: the
        backup's row counts equal the pre-write state and its data.qda is a
        full-size copy (not skipped, not truncated)."""
        proj = scratch / "scale_backup.qda"
        if proj.exists():
            shutil.rmtree(proj)
        tb.build_scale_project(proj, n_files=120, n_codes=20, n_categories=12,
                               n_cases=15, target_codings=3000, big_doc_chars=200_000,
                               n_hot_files=2)
        connect(proj, scratch / "sess_backup")
        before = db_counts(proj)

        # a delete triggers an automatic backup of the whole project
        conn = sqlite3.connect(str(proj / "data.qda"))
        ctid = conn.execute("SELECT ctid FROM code_text LIMIT 1").fetchone()[0]
        conn.close()
        res = json.loads(server.delete_coding(ctid))
        assert res.get("success") is True

        newest = Path(json.loads(server.list_backups())["backups"][0]["path"])
        backup_counts = db_counts(newest)
        # backup preserves the FULL pre-write state (delete happened after backup)
        assert backup_counts["code_text"] == before["code_text"], "backup truncated codings"
        assert backup_counts["source"] == before["source"]
        assert backup_counts["case_text"] == before["case_text"]
        # full-size copy, not a stub
        assert backup_counts["db_bytes"] >= before["db_bytes"] * 0.99
        # and the live project really did lose exactly one coding
        assert db_counts(proj)["code_text"] == before["code_text"] - 1


# ===========================================================================
# 5. OPT-IN GIANT BUILD (10k+ codings, 500k doc) — TRACK6_GIANT=1
# ===========================================================================
@pytest.mark.giant
@pytest.mark.skipif(not GIANT, reason="giant build is opt-in: set TRACK6_GIANT=1")
def test_giant_scale(scratch):
    proj = scratch / "scale_giant.qda"
    stats = tb.build_scale_project(
        proj, n_files=320, n_codes=60, n_categories=45, n_cases=55,
        target_codings=12000, big_doc_chars=500_000, n_hot_files=3,
        mega_code_codings=5200)
    assert stats["codings"] >= 10000
    assert stats["big_doc_chars"] == 500_000
    connect(proj, scratch / "sess_giant")
    slow = []
    for label, fn in read_surface(hot_code=1, big_fid=stats["big_doc_fid"],
                                  ordinary_fid=stats["files"]):
        result, ms, peak = time_call(fn)
        assert _err(result) is None, f"{label}: {_err(result)}"
        if ms > SLOW_THRESHOLD_MS:
            slow.append((label, ms, peak))
    # informational: co-occurrence / matrix are the O(n^2) suspects
    print("GIANT slow tools (>1s):", slow)


# ===========================================================================
# BENCHMARK RUNNER  ->  report.md
# ===========================================================================
SIZES = {
    "S": dict(n_files=50, n_codes=20, n_categories=12, n_cases=10,
              target_codings=800, big_doc_chars=100_000, n_hot_files=2, mega_code_codings=0),
    "M": dict(n_files=150, n_codes=40, n_categories=30, n_cases=30,
              target_codings=4000, big_doc_chars=250_000, n_hot_files=3, mega_code_codings=0),
    "L": dict(n_files=320, n_codes=60, n_categories=45, n_cases=55,
              target_codings=12000, big_doc_chars=500_000, n_hot_files=3, mega_code_codings=5200),
}


def _bench_reads(proj, stats, sess):
    connect(proj, sess)
    rows = {}
    for label, fn in read_surface(hot_code=1, big_fid=stats["big_doc_fid"],
                                  ordinary_fid=stats["files"]):
        try:
            result, ms, peak = time_call(fn, repeats=3)
            rows[label] = dict(ms=ms, peak_kib=peak, error=_err(result),
                               size=len(result) if isinstance(result, str) else 0)
        except Exception as e:  # never let one tool abort the whole benchmark
            rows[label] = dict(ms=None, peak_kib=None, error=f"EXCEPTION {type(e).__name__}: {e}")
    return rows


def _bench_refi(proj, stats, sess, tag):
    connect(proj, sess)
    out = _GEN / f"scale_{tag}.qdpx"
    out.parent.mkdir(exist_ok=True)
    if out.exists():
        out.unlink()
    result, ms, peak = time_call(lambda: server.export_refi_qda(str(out), overwrite=True))
    obj = json.loads(result)
    return dict(ms=ms, peak_kib=peak, result=obj,
                qdpx_bytes=out.stat().st_size if out.exists() else 0)


def _bench_write_loop(scratch):
    proj = scratch / "scale_write_bench.qda"
    if proj.exists():
        shutil.rmtree(proj)
    stats = tb.build_scale_project(proj, n_files=60, n_codes=15, n_categories=10,
                                   n_cases=8, target_codings=1500, big_doc_chars=50_000,
                                   n_hot_files=2)
    connect(proj, scratch / "sess_write_bench")
    out = {}
    session = AICodingSession(project_path=str(proj), description="bench",
                              file_ids=[20], code_names=["Code 001"])
    server.session_manager.save_session(session)
    sid = session.session_id
    fid = 20
    text = file_fulltext(proj, fid)
    suggs = [{"file_id": fid, "code_id": 1, "start_pos": 200 + i * 60,
              "end_pos": 200 + i * 60 + 25, "segment_text": text[200 + i * 60:200 + i * 60 + 25],
              "reasoning": f"s{i}", "confidence": 0.9} for i in range(20)]
    _, out["record_suggestions(20)"], _ = time_call(lambda: server.record_suggestions(sid, suggs))
    rec = json.loads(server.record_suggestions(sid, suggs, replace=True))
    guids = [r["guid"] for r in rec["recorded"]]
    _, out["update_suggestion_status(20)"], _ = time_call(lambda: server.update_suggestion_status(sid, approve=guids))
    _, out["apply_codings(20+backup)"], peak = time_call(lambda: server.apply_codings(sid, create_backup=True))
    out["_apply_peak_kib"] = peak
    conn = sqlite3.connect(str(proj / "data.qda"))
    ctid = conn.execute("SELECT ctid FROM code_text WHERE owner='AI Coding Assistant' LIMIT 1").fetchone()[0]
    conn.close()
    _, out["delete_coding(+backup)"], _ = time_call(lambda: server.delete_coding(ctid))
    _, out["list_backups"], _ = time_call(lambda: server.list_backups())
    target = Path(json.loads(server.list_backups())["backups"][-1]["path"])
    server.restore_backup(str(target))  # preview
    _, out["restore_backup(confirm)"], _ = time_call(lambda: server.restore_backup(str(target), confirm=True))
    # backup completeness check
    bk = json.loads(server.list_backups())["backups"][0]
    out["_backup_complete"] = (Path(bk["path"]) / "data.qda").exists()
    return out, stats


def _cooc_cliff(scratch):
    """Isolate the co-occurrence O(n^2) self-join: one file, all overlapping,
    growing coding count. Returns list of (n_codings, w0_ms, w50_ms)."""
    out = []
    for n in (1000, 2000, 4000, 6000, 8000, 12000):
        proj = scratch / f"cooc_{n}.qda"
        if proj.exists():
            shutil.rmtree(proj)
        tb.build_cooc_stress(proj, n)
        connect(proj, scratch / f"sess_cooc_{n}")
        _, w0, _ = time_call(lambda: server.find_cooccurring_codes(1))
        _, w50, _ = time_call(lambda: server.find_cooccurring_codes(1, window_size=50))
        out.append((n, w0, w50))
    return out


def _media_report(scratch):
    proj = scratch / "media_bench.qda"
    if proj.exists():
        shutil.rmtree(proj)
    stats = tb.build_media_project(proj)
    connect(proj, scratch / "sess_media_bench")
    findings = []
    before = db_counts(proj)

    summary = json.loads(server.get_project_summary())
    files = json.loads(server.list_all_files())
    filelist = files["files"] if isinstance(files, dict) else files
    types = {f["name"]: f["type"] for f in filelist}
    content = json.loads(server.search_files("the", search_content=True, limit=50))

    out = _GEN / "media_bench.qdpx"
    if out.exists():
        out.unlink()
    refi = json.loads(server.export_refi_qda(str(out), overwrite=True))
    after = db_counts(proj)

    # verdicts
    media_types_ok = all(types.get(n) == t for n, t in {
        "photo_scene.png": "image", "call_recording.mp3": "audio",
        "session_video.mp4": "video", "handbook.pdf": "pdf"}.items())
    skip_disclosed = "skip_note" in content["performance_info"]
    rows_intact = (after["code_av"] == before["code_av"] == stats["av_codings"] and
                   after["code_image"] == before["code_image"] == stats["image_codings"])
    refi_ok = refi.get("success") and refi.get("codings_exported") == stats["text_codings"]

    # DEFECT: whole-project REFI export silently omits code_av/code_image and
    # the success payload's note does not mention media codings at all.
    note = refi.get("note", "")
    media_disclosed_in_export = ("image" in note.lower() or "audio" in note.lower()
                                 or "video" in note.lower() or "av" in note.lower()
                                 or "media" in note.lower())
    if not media_disclosed_in_export and (stats["av_codings"] or stats["image_codings"]):
        findings.append(dict(
            sev="LOW",
            title="REFI export silently omits image/AV codings without disclosure",
            detail=("export_refi_qda exported {t} text codings but the project also has "
                    "{a} code_av + {i} code_image rows. These are dropped and the result "
                    "'note' only lists 'Categories, cases, annotations and journals', so a "
                    "researcher exporting a mixed project is not told media codings were "
                    "lost.").format(t=stats["text_codings"], a=stats["av_codings"], i=stats["image_codings"])))

    # OBSERVATION: get_project_summary has no media-coding counts
    if "code_av" not in json.dumps(summary) and "image" not in json.dumps(summary["statistics"]):
        findings.append(dict(
            sev="INFO",
            title="get_project_summary omits media-coding counts",
            detail=("summary.statistics.total_coded_segments counts code_text only; "
                    "image/AV codings ({i}+{a}) are invisible in the summary even though "
                    "file_types lists image/audio/video sources.").format(
                        i=stats["image_codings"], a=stats["av_codings"])))

    # OBSERVATION: analyze_file_with_coding on a media source doesn't flag non-text.
    # (image fid 4 in build_media_project has 4 code_image rows.)
    img = json.loads(server.analyze_file_with_coding(4))
    fi = img.get("file_info", {})
    analyze_flags_nontext = ("is_text" in fi) or ("type" in fi) or bool(img.get("note"))
    if not analyze_flags_nontext and (img.get("full_text") or "") == "":
        findings.append(dict(
            sev="LOW",
            title="analyze_file_with_coding does not indicate a source is non-text",
            detail=("Running analyze_file_with_coding on an image/audio/video source returns "
                    "full_text='' with all-zero statistics and NO is_text/type flag or note — "
                    "indistinguishable from a genuinely empty text file, and the source's "
                    "code_image/code_av codings are not surfaced. get_file_content on the same "
                    "source DOES set is_text=false and media_path, so the signal exists in the "
                    "codebase but is missing from this tool. Mission asks tools to 'clearly "
                    "indicate when a source isn't text' — this one does not.")))

    return dict(stats=stats, media_types_ok=media_types_ok, skip_disclosed=skip_disclosed,
                rows_intact=rows_intact, refi_ok=refi_ok, refi=refi,
                content_perf=content["performance_info"], summary_file_types=summary["file_types"],
                findings=findings, qdpx_bytes=out.stat().st_size if out.exists() else 0)


def _fmt(ms):
    return "-" if ms is None else f"{ms:.1f}"


def run_benchmark():
    scratch = Path(tempfile.mkdtemp(prefix="track6_bench_"))
    built = {}
    read_rows = {}
    refi_rows = {}
    for tag, kw in SIZES.items():
        proj = scratch / f"scale_{tag}.qda"
        t0 = time.perf_counter()
        stats = tb.build_scale_project(proj, **kw)
        stats["build_s"] = round(time.perf_counter() - t0, 1)
        built[tag] = (proj, stats)
        read_rows[tag] = _bench_reads(proj, stats, scratch / f"sess_{tag}")
        refi_rows[tag] = _bench_refi(proj, stats, scratch / f"sess_{tag}", tag)

    write_rows, write_stats = _bench_write_loop(scratch)
    cliff = _cooc_cliff(scratch)
    media = _media_report(scratch)

    _write_report(built, read_rows, refi_rows, write_rows, write_stats, media, cliff)
    shutil.rmtree(scratch, ignore_errors=True)
    print("report written to", HERE / "report.md")


def _write_report(built, read_rows, refi_rows, write_rows, write_stats, media, cliff):
    tags = list(SIZES.keys())
    labels = [l for l, _ in read_surface(1, 1, 1)]
    lines = []
    A = lines.append
    A("# Track 6 — Scale & Media Stress Test Report\n")
    A("QualCoder MCP server **v0.6.0-alpha** (worktree, detached HEAD 7f4e05b). "
      "Projects use QualCoder's real v14 DDL — notably **no secondary index on "
      "`code_text.fid` / `case_text.fid`**, so the self-join tools below reflect a "
      "real researcher's project.\n")
    A(f"Wall-clock = best of 3 runs, in-process (no MCP/JSON-RPC transport). "
      f"Peak = per-call Python-heap allocation delta (`tracemalloc`, measured after "
      f"gc so it is the call's own cost, not a retained baseline). SQLite C-side work "
      f"is not counted there. Slow threshold flagged at **{SLOW_THRESHOLD_MS:.0f} ms**.\n")

    A("## Project sizes\n")
    A("| Size | Files | Codes | Cats | Cases | Codings | Big doc (chars) | data.qda | build |")
    A("|------|------:|------:|-----:|------:|--------:|----------------:|---------:|------:|")
    for t in tags:
        s = built[t][1]
        A(f"| {t} | {s['files']} | {s['codes']} | {s['categories']} | {s['cases']} | "
          f"{s['codings']} | {s['big_doc_chars']:,} | {s['db_bytes']/1e6:.1f} MB | {s['build_s']}s |")
    A("")

    A("## Read-tool wall-clock (ms)\n")
    A("| Tool | " + " | ".join(tags) + " | scaling S→L |")
    A("|------|" + "|".join(["----:"] * len(tags)) + "|----:|")
    for lb in labels:
        cells = []
        for t in tags:
            r = read_rows[t].get(lb, {})
            cells.append(_fmt(r.get("ms")))
        s_ms = read_rows[tags[0]].get(lb, {}).get("ms")
        l_ms = read_rows[tags[-1]].get(lb, {}).get("ms")
        ratio = f"{l_ms / s_ms:.1f}x" if (s_ms and l_ms and s_ms > 0) else "-"
        flag = ""
        if l_ms and l_ms > SLOW_THRESHOLD_MS:
            flag = " ⚠️"
        A(f"| {lb} | " + " | ".join(cells) + f" | {ratio}{flag} |")
    A("")

    A("## Read-tool peak Python-heap allocation per call (KiB)\n")
    A("_Allocation delta of a single call (peak − live baseline, after gc). SQLite's "
      "C-side join work is NOT counted here — see the co-occurrence cliff below._\n")
    A("| Tool | " + " | ".join(tags) + " |")
    A("|------|" + "|".join(["----:"] * len(tags)) + "|")
    for lb in labels:
        cells = [str(read_rows[t].get(lb, {}).get("peak_kib", "-")) for t in tags]
        A(f"| {lb} | " + " | ".join(cells) + " |")
    A("")

    A("## Co-occurrence O(n²) cliff (one file, all codings overlapping)\n")
    A("Isolates the `code_text` self-join with every row sharing one `fid`. This is "
      "the worst realistic case for `find_cooccurring_codes` (a single densely-coded "
      "document).\n")
    A("| Codings in file | window=0 (ms) | window=50 (ms) |")
    A("|----------------:|--------------:|---------------:|")
    for n, w0, w50 in cliff:
        f0 = " ⚠️" if w0 > SLOW_THRESHOLD_MS else ""
        f5 = " ⚠️" if w50 > SLOW_THRESHOLD_MS else ""
        A(f"| {n:,} | {w0:.1f}{f0} | {w50:.1f}{f5} |")
    # quadratic check: ms should ~4x when codings ~2x
    if len(cliff) >= 2:
        n1, w1, _ = cliff[1]
        n2, w2, _ = cliff[-1]
        if w1 > 0:
            A(f"\n> Growth {n1:,}→{n2:,} codings ({n2/n1:.0f}x) multiplied window=0 "
              f"wall-clock by **{w2/w1:.0f}x** — consistent with O(n²) in codings-per-file.\n")

    A("## REFI whole-project export\n")
    A("| Size | wall (ms) | peak (KiB) | codings | files | .qdpx bytes | truncated codes |")
    A("|------|----------:|-----------:|--------:|------:|------------:|----------------:|")
    for t in tags:
        r = refi_rows[t]
        res = r["result"]
        A(f"| {t} | {r['ms']:.1f} | {r['peak_kib']} | {res.get('codings_exported','-')} | "
          f"{res.get('files_exported','-')} | {r['qdpx_bytes']:,} | "
          f"{len(res.get('truncated_codes', []))} |")
    A("")
    # surface the truncation disclosure from L
    tr = refi_rows[tags[-1]]["result"].get("truncated_codes")
    if tr:
        A(f"> L build exercised the 5000-per-code REFI cap: `truncated_codes` disclosed "
          f"{len(tr)} code(s), e.g. {tr[0]}.\n")

    A("## Write loop (single project, ~1.5k existing codings)\n")
    A("| Step | wall (ms) |")
    A("|------|----------:|")
    for k, v in write_rows.items():
        if k.startswith("_"):
            continue
        A(f"| {k} | {v:.1f} |")
    A("")
    A(f"- apply_codings peak Python-heap: **{write_rows.get('_apply_peak_kib','?')} KiB** "
      f"(the backup is a filesystem `copytree`, not held in Python memory).")
    A(f"- Backup completeness after write loop: **{'PASS' if write_rows.get('_backup_complete') else 'FAIL'}** "
      f"(newest backup contains a full `data.qda`).\n")

    A("## Media handling (mixed text + image/audio/video/PDF)\n")
    ms = media["stats"]
    A(f"Sources: {ms['sources']} (text, imported doc, PDF-with-text, 2 images, audio, video, "
      f"AV transcript). Codings: {ms['text_codings']} text, {ms['image_codings']} image, "
      f"{ms['av_codings']} AV.\n")
    A(f"- **File-type detection**: {media['summary_file_types']} → "
      f"{'PASS' if media['media_types_ok'] else 'FAIL'} (image/audio/video/pdf all labelled).")
    A(f"- **Content search graceful degradation**: {media['content_perf']} → "
      f"skip disclosed = {media['skip_disclosed']}. Media sources are counted as "
      f"`files_skipped_no_text` and never returned as content matches.")
    A(f"- **Media rows untouched by text tools + export**: "
      f"{'PASS' if media['rows_intact'] else 'FAIL'} (code_av/code_image counts stable).")
    A(f"- **REFI mixed export**: success={media['refi'].get('success')}, "
      f"text codings exported={media['refi'].get('codings_exported')} "
      f"(= all {ms['text_codings']} text codings), .qdpx={media['qdpx_bytes']:,} bytes; "
      f"archive contains only project.qde + text source payloads.\n")

    A("## Findings\n")
    scale_findings = []
    # REFI export cost (measured)
    refi_L = refi_rows[tags[-1]]
    refi_M = refi_rows[tags[1]]
    if refi_L["ms"] > SLOW_THRESHOLD_MS or refi_M["ms"] > SLOW_THRESHOLD_MS:
        scale_findings.append(dict(
            sev="MEDIUM",
            title="export_refi_qda is slow and memory-heavy at scale (>1s, high peak heap)",
            detail=(f"Whole-project REFI export took {refi_M['ms']:.0f} ms at M ({built[tags[1]][1]['codings']} "
                    f"codings) and {refi_L['ms']:.0f} ms at L ({built[tags[-1]][1]['codings']} codings), "
                    f"with a Python-heap peak of ~{refi_L['peak_kib']/1024:.0f} MB for a "
                    f"{built[tags[-1]][1]['db_bytes']/1e6:.1f} MB project. The exporter builds the whole "
                    f"ElementTree in memory, serialises it, then re-parses it with minidom for "
                    f"pretty-printing (prettify_xml) — that reparse roughly doubles peak memory and "
                    f"dominates wall-clock. Not a correctness bug, but a long-running blocking call a "
                    f"researcher will notice on a real project; consider streaming/iterative "
                    f"serialisation or dropping the minidom reparse.")))
    # co-occurrence O(n^2)
    over = [(n, w0) for (n, w0, _w) in cliff if w0 > SLOW_THRESHOLD_MS]
    if over:
        first = over[0]
        scale_findings.append(dict(
            sev="MEDIUM",
            title="find_cooccurring_codes is O(n²) in codings-per-file (crosses 1s on one dense file)",
            detail=(f"The code_text self-join has no index on `code_text.fid`, so a single heavily-coded "
                    f"document drives quadratic cost: window=0 crossed {SLOW_THRESHOLD_MS:.0f} ms at "
                    f"{first[0]:,} codings in one file ({first[1]:.0f} ms) and keeps climbing. "
                    f"get_case_code_matrix / get_codes_by_case share the same unindexed "
                    f"`case_text ⋈ code_text` join. Adding an index on code_text(fid) would remove the cliff.")))
    else:
        scale_findings.append(dict(
            sev="INFO",
            title="find_cooccurring_codes scales super-linearly but stayed under 1s in tests",
            detail=("The unindexed code_text self-join grew ~100x wall-clock for a 15x coding increase; "
                    "it stayed <1s at the sizes tested but is the first tool to watch as projects grow.")))

    all_findings = scale_findings + media["findings"]
    if not all_findings:
        A("No defects.\n")
    else:
        for f in all_findings:
            A(f"### [{f['sev']}] {f['title']}\n")
            A(f["detail"] + "\n")

    A("## Scaling observations\n")
    # compute the worst scaling ratio automatically
    worst = None
    for lb in labels:
        s_ms = read_rows[tags[0]].get(lb, {}).get("ms")
        l_ms = read_rows[tags[-1]].get(lb, {}).get("ms")
        if s_ms and l_ms and s_ms > 0:
            ratio = l_ms / s_ms
            if worst is None or ratio > worst[1]:
                worst = (lb, ratio, s_ms, l_ms)
    if worst:
        A(f"- Steepest scaler S→L: **{worst[0]}** ({worst[1]:.1f}x, {worst[2]:.1f}→{worst[3]:.1f} ms) "
          f"while the project grew ~15x in codings.")
    A("- The `code_text` self-join in `find_cooccurring_codes` and the "
      "`case_text ⋈ code_text` join in `get_case_code_matrix` / `get_codes_by_case` have no "
      "supporting index on `fid`; watch these as projects grow the number of codings per file.")
    A("- `search_files(content)` and `analyze_file_with_coding` scan/return the full 500k-char "
      "document; their peak heap tracks document size, not coding count.\n")

    (HERE / "report.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    run_benchmark()


# ---------------------------------------------------------------------------
# Perf regression: find_cooccurring_codes on ONE dense file (consolidated fix
# round item 10). The old SQL self-join was O(n^2) in codings-per-file and
# crossed 1s at 8k codings (track6 report); the Python sorted-bisect join is
# O(n log n). No indexes may be added to user DBs (QualCoder owns the schema),
# so this pins the algorithmic fix instead.
# ---------------------------------------------------------------------------
def test_cooccurrence_dense_file_perf(tmp_path):
    import random
    proj = tmp_path / "dense.qda"
    tb.build_scale_project(proj, n_files=2, n_codes=6, n_categories=2,
                           n_cases=2, target_codings=10, big_doc_chars=60_000,
                           n_hot_files=1)
    con = sqlite3.connect(str(proj / "data.qda"))
    random.seed(7)
    rows = []
    for i in range(8000):
        p0 = random.randint(0, 50_000)
        rows.append((random.randint(1, 6), 1, "x", p0,
                     p0 + random.randint(1, 60), f"c{random.randint(0, 2)}"))
    con.executemany(
        "INSERT OR IGNORE INTO code_text (cid, fid, seltext, pos0, pos1, owner) "
        "VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    con.close()

    srv = connect(proj, tmp_path / "sessions")
    for window in (0, 50):
        t0 = time.perf_counter()
        out = json.loads(srv.find_cooccurring_codes(1, window))
        elapsed = time.perf_counter() - t0
        assert isinstance(out, list) and len(out) >= 1
        # was 1.46 s (w=0) at this density before the rewrite; huge headroom
        assert elapsed < 1.0, f"co-occurrence w={window} took {elapsed:.2f}s at 8k dense codings"
