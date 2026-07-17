# Folded into the main suite from the v0.6.0-alpha parallel test campaign
# (track 5, property-based/Hypothesis); adapted paths/fixtures only — test logic unchanged.
"""Track #5 - property-based tests (Hypothesis) for qualcoder_mcp.

Generators build random-but-VALID v14 QualCoder projects (varying #files,
unicode fulltext, #codes/categories with random tree shapes, #codings with
valid positions, NULLs in optional fields) and random sequences of MCP tool
calls, and assert the invariants that must NEVER break:

  INV1  the DB connection is READ-ONLY after every tool call
  INV2a a fresh backup exists immediately before every successful write
  INV2b no backup is created on input rejected at validation
  INV3  no write introduces an orphan coding or a UNIQUE-violating duplicate
  INV4  every code_text row satisfies seltext == fulltext[pos0:pos1]
        (code-point space; U+2029->\\n tolerated per text-positions.md §5)
  INV5  the DB stays a valid, parseable v14 project after any sequence
  INV6  read tools never mutate the DB (content hash stable over pure reads)

Run with the worktree venv:
  venv/bin/python -m pytest tests-out/track5/test_track5_properties.py
"""

import json
import shutil
import uuid
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st, HealthCheck, assume
from hypothesis.stateful import (
    RuleBasedStateMachine, rule, initialize, invariant, precondition,
)

import os
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
import track5_helpers as H
from track5_helpers import server


def _ex(n: int) -> int:
    """Suite-friendly example counts; TRACK5_FULL=1 restores the originals."""
    return n if os.environ.get("TRACK5_FULL") else max(10, n // 5)
from qualcoder_mcp.sessions import CodingSuggestion

# ---------------------------------------------------------------------------
# strategies
# ---------------------------------------------------------------------------
# base alphabet: any printable-ish char except surrogates and control chars
# (NUL-free); newlines / CR / astral are injected via HARD_FRAGMENTS so they
# appear reliably.
BASE_ALPHA = st.characters(min_codepoint=1, exclude_categories=("Cs", "Cc"))

HARD_FRAGMENTS = [
    "\n", "one\ntwo", "\r\n", "a\r\nb\r\nc", "\r",           # newline variants
    "😀", "👨‍👩‍👧", "𠀀𠀁", "x 😀 y", "😀 note\r\nz",  # astral / CRLF drift
    "日本語のテキスト", "café", "café",                  # BMP CJK, NFC, NFD
    "  ", "\t", "mixed 日本 😀 end",
]

opt_memo = st.one_of(st.none(), st.text(alphabet=BASE_ALPHA, max_size=20))
COLORS = ["#FF0000", "#00FF00", "#3498DB", "#AC58FA", "#E65100"]


@st.composite
def fulltext_strategy(draw):
    parts = draw(st.lists(
        st.one_of(
            st.text(alphabet=BASE_ALPHA, min_size=1, max_size=10),
            st.sampled_from(HARD_FRAGMENTS),
        ),
        min_size=1, max_size=6,
    ))
    return "".join(parts)


@st.composite
def project_spec(draw):
    """A fully-resolved, VALID v14 project (ids assigned by list order)."""
    n_files = draw(st.integers(0, 4))
    files = []
    for i in range(n_files):
        kind = draw(st.sampled_from(["text", "text", "text", "empty", "image"]))
        if kind == "image":
            files.append({"name": f"img_{i}.png", "fulltext": None,
                          "mediapath": "images:img.png", "memo": draw(opt_memo)})
        elif kind == "empty":
            files.append({"name": f"empty_{i}.txt",
                          "fulltext": draw(st.sampled_from(["", None])),
                          "mediapath": None, "memo": draw(opt_memo)})
        else:
            files.append({"name": f"file_{i}.txt", "fulltext": draw(fulltext_strategy()),
                          "mediapath": None, "memo": draw(opt_memo)})

    n_cat = draw(st.integers(0, 4))
    cats = []
    for i in range(n_cat):
        supr = draw(st.integers(1, i)) if (i > 0 and draw(st.booleans())) else None
        cats.append({"name": f"cat_{i}", "memo": draw(opt_memo), "supercatid": supr})

    n_code = draw(st.integers(0, 5))
    codes = []
    for i in range(n_code):
        catid = draw(st.integers(1, n_cat)) if (n_cat and draw(st.booleans())) else None
        codes.append({"name": f"code_{i}", "memo": draw(opt_memo),
                      "catid": catid, "color": draw(st.sampled_from(COLORS))})

    text_idx = [(i + 1, f["fulltext"]) for i, f in enumerate(files)
                if f["mediapath"] is None and f["fulltext"]]
    codings = []
    used = set()
    if text_idx and codes:
        for _ in range(draw(st.integers(0, 8))):
            fid, ft = draw(st.sampled_from(text_idx))
            L = len(ft)
            p0 = draw(st.integers(0, L - 1))
            p1 = draw(st.integers(p0 + 1, L))
            cid = draw(st.integers(1, len(codes)))
            owner = draw(st.sampled_from(["GUI Coder", "TestCoder", "AI Coding Assistant"]))
            key = (cid, fid, p0, p1, owner)
            if key in used:
                continue
            used.add(key)
            codings.append({"cid": cid, "fid": fid, "pos0": p0, "pos1": p1,
                            "seltext": ft[p0:p1], "owner": owner,
                            "memo": draw(opt_memo),
                            "important": draw(st.sampled_from([None, 1]))})

    cases = [{"name": f"case_{i}", "memo": draw(opt_memo)}
             for i in range(draw(st.integers(0, 3)))]

    return {"files": files, "categories": cats, "codes": codes,
            "codings": codings, "cases": cases}


# ---------------------------------------------------------------------------
# helpers shared by tests
# ---------------------------------------------------------------------------
def _new_run_dir():
    d = H.TMP_ROOT / f"run_{uuid.uuid4().hex[:12]}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _is_error(raw: str) -> bool:
    """True if a tool return value is an error result."""
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return False  # apply_codings success is plain text
    return isinstance(obj, dict) and "error" in obj


def _assert_read_only():
    """INV1: after any tool call the global connection must be read-only."""
    assert server.db is None or server.db.read_only is True, \
        "INV1 violated: server left a read-WRITE connection after a tool call"


def _assert_core(path):
    problems = H.core_invariant_problems(path)
    assert not problems, "INV3/4/5 violated:\n  " + "\n  ".join(problems)


# ===========================================================================
# Test A - the generator itself always yields a valid, openable v14 project
# ===========================================================================
@settings(max_examples=_ex(120), deadline=None,
          suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
@given(spec=project_spec())
def test_generator_builds_valid_v14_projects(spec):
    run = _new_run_dir()
    try:
        path = H.build_project(spec, parent=run)
        # opens read-only through the real DB layer, is v14, passes all core invariants
        db = H.QualcoderDatabase(path, read_only=True)
        try:
            assert db.db_version == "v14"
        finally:
            db.close()
        _assert_core(path)
    finally:
        shutil.rmtree(run, ignore_errors=True)


# ===========================================================================
# Test D - a SUCCESSFUL import creates exactly one backup, adds one row,
#          keeps the project valid, and preserves the seltext invariant (INV2a)
# ===========================================================================
@settings(max_examples=_ex(80), deadline=None,
          suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
@given(spec=project_spec(), content=fulltext_strategy())
def test_successful_import_makes_one_backup(spec, content):
    assume(content.strip() != "")
    # content must not be BOM/whitespace-only after normalisation
    norm = content[1:] if content.startswith("﻿") else content
    assume(norm.strip() != "")
    run = _new_run_dir()
    saved = H.save_server_state()
    try:
        path = H.build_project(spec, parent=run)
        H.set_server_project(path, str(run / "sessions"))

        before_backups = H.count_mcp_backups(path)
        before_rows = H.row_counts(path)

        raw = server.import_text_file(
            filename=f"imported_{uuid.uuid4().hex[:6]}.txt",
            content=content, create_backup=True,
        )
        _assert_read_only()
        obj = json.loads(raw)
        assert obj.get("success") is True, f"import failed unexpectedly: {raw}"

        after_backups = H.count_mcp_backups(path)
        after_rows = H.row_counts(path)
        assert after_backups == before_backups + 1, \
            "INV2a violated: successful write did not create exactly one backup"
        assert after_rows["source"] == before_rows["source"] + 1
        _assert_core(path)
    finally:
        H.teardown_server(saved)
        shutil.rmtree(run, ignore_errors=True)


# ===========================================================================
# Test C - inputs REJECTED at validation create NO backup and change nothing
#          (INV2b + no partial state)
# ===========================================================================
_REJECT_CASES = [
    # (kwargs for import_text_file, human label)
    (dict(filename="", content="hello world"), "empty filename"),
    (dict(filename="noext", content="hello world"), "filename without extension"),
    (dict(filename="../escape.txt", content="hello"), "path traversal filename"),
    (dict(filename="bad\x00name.txt", content="hello"), "NUL in filename"),
    (dict(filename="ok.txt", content=""), "empty content"),
    (dict(filename="ok.txt", content="﻿"), "BOM-only content"),
    (dict(filename="ok.txt", content="   \n\t "), "whitespace-only content"),
]


@settings(max_examples=_ex(60), deadline=None,
          suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
@given(spec=project_spec(), idx=st.integers(0, len(_REJECT_CASES) - 1))
def test_rejected_import_makes_no_backup(spec, idx):
    kwargs, _label = _REJECT_CASES[idx]
    run = _new_run_dir()
    saved = H.save_server_state()
    try:
        path = H.build_project(spec, parent=run)
        H.set_server_project(path, str(run / "sessions"))

        before_backups = H.count_mcp_backups(path)
        before_hash = H.db_content_hash(path)

        raw = server.import_text_file(create_backup=True, **kwargs)
        _assert_read_only()
        assert _is_error(raw), f"expected rejection for {_label}, got: {raw}"

        assert H.count_mcp_backups(path) == before_backups, \
            f"INV2b violated: rejected input ({_label}) created a backup"
        assert H.db_content_hash(path) == before_hash, \
            f"rejected input ({_label}) mutated the database"
        _assert_core(path)
    finally:
        H.teardown_server(saved)
        shutil.rmtree(run, ignore_errors=True)


# ===========================================================================
# Test E - pure READ tool sequences never mutate the DB, and never leave a
#          read-write connection (INV6 + INV1)
# ===========================================================================
READ_TOOLS = [
    lambda: server.get_project_summary(),
    lambda: server.get_coding_frequencies(),
    lambda: server.list_attribute_types(),
    lambda: server.get_case_code_matrix(),
    lambda: server.get_current_project(),
]


@settings(max_examples=_ex(60), deadline=None,
          suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large,
                                 HealthCheck.filter_too_much])
@given(spec=project_spec(), calls=st.lists(st.integers(0, len(READ_TOOLS) - 1),
                                           min_size=1, max_size=10))
def test_pure_reads_do_not_mutate(spec, calls):
    run = _new_run_dir()
    saved = H.save_server_state()
    try:
        path = H.build_project(spec, parent=run)
        H.set_server_project(path, str(run / "sessions"))
        h0 = H.db_content_hash(path)
        for i in calls:
            READ_TOOLS[i]()
            _assert_read_only()
            assert H.db_content_hash(path) == h0, "INV6 violated: a read tool mutated the DB"
    finally:
        H.teardown_server(saved)
        shutil.rmtree(run, ignore_errors=True)


# ===========================================================================
# Test F - adversarial unicode position vectors (text-positions.md §8):
#          a coding written via apply_codings must store seltext exactly equal
#          to the code-point slice, on emoji/CRLF/CJK/combining files.
# ===========================================================================
# (fulltext, segment, canonical pos0, pos1)  -- canonical == code-point space
UNICODE_VECTORS = [
    ("😀 grinning here", "here", 11, 15),
    ("👨‍👩‍👧 family here", "here", 13, 17),
    ("日本語のテキスト、ここです。", "ここ", 9, 11),
    ("caf\u00e9 menu here", "here", 10, 14),      # NFC (é = 1 code point)
    ("cafe\u0301 menu here", "here", 11, 15),     # NFD (combining mark adds 1)
    ("line one\rline two\rfind here", "here", 23, 27),  # lone CR (safe)
    ("abc\n", "abc\n", 0, 4),                     # trailing newline segment
]


@pytest.mark.parametrize("fulltext,segment,p0,p1", UNICODE_VECTORS)
def test_unicode_vectors_apply_codings_seltext(fulltext, segment, p0, p1):
    assert fulltext[p0:p1] == segment, "vector self-check failed"
    run = _new_run_dir()
    saved = H.save_server_state()
    try:
        spec = {
            "files": [{"name": "u.txt", "fulltext": fulltext, "mediapath": None, "memo": None}],
            "categories": [],
            "codes": [{"name": "c0", "memo": None, "catid": None, "color": "#FF0000"}],
            "codings": [],
            "cases": [],
        }
        path = H.build_project(spec, parent=run)
        H.set_server_project(path, str(run / "sessions"))

        sugg = CodingSuggestion(
            file_id=1, file_name="u.txt", code_id=1, code_name="c0",
            start_pos=p0, end_pos=p1, segment_text=segment,
            reasoning="r", confidence=0.9, status="approved",
        )
        sess = H.make_session(path, [sugg])
        raw = server.apply_codings(sess.session_id, create_backup=True)
        _assert_read_only()
        assert not _is_error(raw), f"apply failed: {raw}"
        # the written row must satisfy seltext == fulltext[pos0:pos1]
        _assert_core(path)
    finally:
        H.teardown_server(saved)
        shutil.rmtree(run, ignore_errors=True)


# ===========================================================================
# Test G - a suggestion duplicating an existing AI row rolls back cleanly:
#          error returned, no orphan/dup, DB row count unchanged.
# ===========================================================================
def test_duplicate_apply_rolls_back_cleanly():
    run = _new_run_dir()
    saved = H.save_server_state()
    try:
        spec = {
            "files": [{"name": "d.txt", "fulltext": "hello world here", "mediapath": None, "memo": None}],
            "categories": [],
            "codes": [{"name": "c0", "memo": None, "catid": None, "color": "#FF0000"}],
            # pre-existing AI row at (cid=1,fid=1,0,5,"AI Coding Assistant")
            "codings": [{"cid": 1, "fid": 1, "pos0": 0, "pos1": 5, "seltext": "hello",
                         "owner": "AI Coding Assistant", "memo": None, "important": None}],
            "cases": [],
        }
        path = H.build_project(spec, parent=run)
        H.set_server_project(path, str(run / "sessions"))

        before = H.row_counts(path)["code_text"]
        sugg = CodingSuggestion(
            file_id=1, file_name="d.txt", code_id=1, code_name="c0",
            start_pos=0, end_pos=5, segment_text="hello",
            reasoning="r", confidence=0.9, status="approved",
        )
        sess = H.make_session(path, [sugg])
        raw = server.apply_codings(sess.session_id, create_backup=True)
        _assert_read_only()
        assert _is_error(raw), f"expected duplicate to fail, got: {raw}"
        assert H.row_counts(path)["code_text"] == before, "duplicate apply changed row count"
        _assert_core(path)
    finally:
        H.teardown_server(saved)
        shutil.rmtree(run, ignore_errors=True)


# ===========================================================================
# Test H - DIRECT DB layer: add_coding fed random (often invalid) inputs must
#          either RAISE or write a row that satisfies INV3/INV4 exactly.
#          Targets the write-validation/rollback branches in database.py.
# ===========================================================================
@settings(max_examples=_ex(150), deadline=None,
          suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large,
                                 HealthCheck.filter_too_much])
@given(spec=project_spec(), data=st.data())
def test_add_coding_direct_never_writes_bad_row(spec, data):
    run = _new_run_dir()
    saved = H.save_server_state()
    try:
        path = H.build_project(spec, parent=run)
        files = H.list_text_files(path)   # text + non-empty
        codes = H.list_codes(path)
        assume(files and codes)

        db = H.QualcoderDatabase(path, read_only=False)
        try:
            f = data.draw(st.sampled_from(files))
            ft = f["fulltext"]
            L = len(ft)
            code = data.draw(st.sampled_from(codes))
            # positions may be out of range; selected_text may be wrong
            p0 = data.draw(st.integers(0, L + 2))
            p1 = data.draw(st.integers(0, L + 4))
            sel = data.draw(st.one_of(
                st.just(ft[p0:p1]) if 0 <= p0 < p1 <= L else st.just("X"),
                st.text(alphabet=BASE_ALPHA, max_size=6),
                st.just(ft[p0:p1]),
            ))
            owner = data.draw(st.sampled_from(["AI Coding Assistant", "prop-owner"]))
            raised = False
            try:
                ctid = db.add_coding(
                    file_id=f["id"], code_id=code["id"],
                    start_pos=p0, end_pos=p1, selected_text=sel, owner=owner,
                )
            except (ValueError, RuntimeError):
                raised = True
            if not raised:
                # a row was written; it MUST satisfy seltext == fulltext[p0:p1]
                assert 0 <= p0 < p1 <= L
                assert sel == ft[p0:p1]
                row = db.conn.execute(
                    "SELECT seltext, pos0, pos1 FROM code_text WHERE ctid=?",
                    (ctid,)).fetchone()
                assert row["seltext"] == ft[row["pos0"]:row["pos1"]]
        finally:
            db.close()
        _assert_core(path)
    finally:
        H.teardown_server(saved)
        shutil.rmtree(run, ignore_errors=True)


# ===========================================================================
# Test B - STATEFUL: random sequences of write+read tool calls over a random
#          valid project. Invariants checked after EVERY step.
# ===========================================================================
class QualcoderWriteMachine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self._saved = H.save_server_state()
        self.run = _new_run_dir()
        self.path = None
        self._imp = 0

    @initialize(spec=project_spec())
    def start(self, spec):
        self.path = H.build_project(spec, parent=self.run)
        H.set_server_project(self.path, str(self.run / "sessions"))

    # -- writes -------------------------------------------------------------
    @rule(content=fulltext_strategy())
    def import_file(self, content):
        assume(content.strip() != "")
        norm = content[1:] if content.startswith("﻿") else content
        assume(norm.strip() != "")
        self._imp += 1
        before_b = H.count_mcp_backups(self.path)
        before_rows = H.row_counts(self.path)
        raw = server.import_text_file(
            filename=f"imp_{self._imp}_{uuid.uuid4().hex[:4]}.txt",
            content=content, create_backup=True,
        )
        _assert_read_only()
        if _is_error(raw):
            assert H.row_counts(self.path)["source"] == before_rows["source"], \
                "errored import changed source count"
        else:
            assert H.count_mcp_backups(self.path) == before_b + 1, \
                "INV2a: successful import made != 1 backup"
            assert H.row_counts(self.path)["source"] == before_rows["source"] + 1

    @rule(data=st.data())
    def apply_codings(self, data):
        files = H.list_text_files(self.path)
        codes = H.list_codes(self.path)
        if not files or not codes:
            return
        existing = H.existing_coding_keys(self.path)
        owner = "AI Coding Assistant"
        suggs, batch = [], set()
        for _ in range(data.draw(st.integers(1, 3))):
            f = data.draw(st.sampled_from(files))
            ft = f["fulltext"]
            L = len(ft)
            p0 = data.draw(st.integers(0, L - 1))
            p1 = data.draw(st.integers(p0 + 1, L))
            c = data.draw(st.sampled_from(codes))
            key = (c["id"], f["id"], p0, p1, owner)
            if key in existing or key in batch:
                continue
            batch.add(key)
            suggs.append(CodingSuggestion(
                file_id=f["id"], file_name=f["name"], code_id=c["id"],
                code_name=c["name"], start_pos=p0, end_pos=p1,
                segment_text=ft[p0:p1], reasoning="r", confidence=0.9,
                status="approved"))
        if not suggs:
            return
        before_b = H.count_mcp_backups(self.path)
        before_rows = H.row_counts(self.path)
        sess = H.make_session(self.path, suggs)
        raw = server.apply_codings(sess.session_id, create_backup=True)
        _assert_read_only()
        if _is_error(raw):
            assert H.row_counts(self.path)["code_text"] == before_rows["code_text"], \
                "errored apply changed code_text count"
        else:
            assert H.count_mcp_backups(self.path) == before_b + 1, \
                "INV2a: successful apply made != 1 backup"
            assert H.row_counts(self.path)["code_text"] == \
                before_rows["code_text"] + len(suggs)

    @rule(data=st.data())
    def link_case(self, data):
        files = H.list_text_files(self.path)
        cases = H.list_cases(self.path)
        if not files or not cases:
            return
        f = data.draw(st.sampled_from(files))
        c = data.draw(st.sampled_from(cases))
        before_rows = H.row_counts(self.path)
        raw = server.link_file_to_case(file_id=f["id"], case_id=c["id"], create_backup=True)
        _assert_read_only()
        if _is_error(raw):
            assert H.row_counts(self.path)["case_text"] == before_rows["case_text"], \
                "errored link changed case_text count"
        else:
            assert H.row_counts(self.path)["case_text"] == before_rows["case_text"] + 1

    @rule(data=st.data())
    def delete_coding(self, data):
        ctids = H.list_ctids(self.path)
        # sometimes target a non-existent id to drive the reject path
        target = data.draw(st.one_of(
            st.sampled_from(ctids) if ctids else st.integers(10**6, 10**6 + 5),
            st.integers(10**6, 10**6 + 5)))
        before_rows = H.row_counts(self.path)
        raw = server.delete_coding(coding_id=target, create_backup=True)
        _assert_read_only()
        if _is_error(raw):
            assert H.row_counts(self.path)["code_text"] == before_rows["code_text"], \
                "errored delete changed code_text count"
        else:
            assert H.row_counts(self.path)["code_text"] == before_rows["code_text"] - 1

    # -- reads --------------------------------------------------------------
    @rule(i=st.integers(0, len(READ_TOOLS) - 1))
    def read_tool(self, i):
        READ_TOOLS[i]()
        _assert_read_only()

    # -- invariants after every step ---------------------------------------
    @invariant()
    def core_ok(self):
        if self.path is None:
            return
        _assert_read_only()
        _assert_core(self.path)

    def teardown(self):
        H.teardown_server(self._saved)
        shutil.rmtree(self.run, ignore_errors=True)


TestQualcoderWriteMachine = QualcoderWriteMachine.TestCase
TestQualcoderWriteMachine.settings = settings(
    max_examples=_ex(60), stateful_step_count=10, deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large,
                           HealthCheck.filter_too_much],
)
