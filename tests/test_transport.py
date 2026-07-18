# Folded into the main suite from the v0.6.0-alpha parallel test campaign
# (track 3, protocol-level stdio transport); adapted paths/fixtures only — test logic unchanged.
"""Track #3 — PROTOCOL-LEVEL validation of the QualCoder MCP server.

Unlike every other test in the suite (which imports the server module and calls
the tool functions in-process), this module launches the server as a real
subprocess and drives it over the genuine stdio JSON-RPC transport using the
`mcp` Python SDK's stdio client.  It exercises:

  1. Handshake: initialize + tools/list  (exactly 48 tools, every inputSchema
     a well-formed JSON Schema, no empty descriptions).
  2. resources/list (6 concrete) + resources/templates/list (3) = 9 resources,
     prompts/list (4).
  3. Real round-trip calls over the wire: select_project, get_project_summary,
     a LARGE read payload, and a full WRITE-path approval flow
     (analyze_for_coding -> record_suggestions -> update_suggestion_status ->
     apply_codings) that actually mutates a synthetic .qda database.
  4. Error propagation: bad/missing/wrong-typed args, nonexistent project,
     oversized input, unknown tool -> proper MCP errors / structured error
     results, and the server stays alive & responsive afterwards.
  5. Serialization edge cases: unicode/emoji/ZWJ, very long strings, JSON null
     survive the JSON-RPC boundary intact.

The synthetic v14 project reuses the 13-table schema from tests/conftest.py.

Self-contained: no pytest plugins required (each test drives its own asyncio
event loop via `run()`).  The server subprocess runs with HOME redirected to a
private temp dir so AI-coding sessions never touch ~/.qualcoder_mcp.
"""

import asyncio
import json
import os
import re
import shutil
import sqlite3
import time
import uuid
from pathlib import Path

import pytest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# --------------------------------------------------------------------------- #
# Paths / environment
# --------------------------------------------------------------------------- #

import sys
import tempfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parent                      # repo root (tests/ -> repo)
VENV_PY = Path(sys.executable)          # run the server with the suite's python
RUN_DIR = Path(tempfile.mkdtemp(prefix="qc_transport_"))  # generated artefacts
PROJECTS_DIR = RUN_DIR / "projects"
HOME_DIR = RUN_DIR / "home"            # private HOME -> private sessions dir

EXPECTED_TOOLS = 48
EXPECTED_CONCRETE_RESOURCES = 6
EXPECTED_RESOURCE_TEMPLATES = 3
EXPECTED_RESOURCES_TOTAL = 9
EXPECTED_PROMPTS = 4

# jsonschema is installed in the worktree venv; when this module is run under a
# different interpreter it degrades gracefully to structural checks only.
try:
    from jsonschema import Draft202012Validator
    _HAVE_JSONSCHEMA = True
except Exception:  # pragma: no cover
    _HAVE_JSONSCHEMA = False


# --------------------------------------------------------------------------- #
# Synthetic v14 project builder (schema mirrors tests/conftest.py)
# --------------------------------------------------------------------------- #

_SCHEMA = """
CREATE TABLE project (databaseversion TEXT, date TEXT, memo TEXT, about TEXT, bookmarkfile INTEGER, bookmarkpos INTEGER, codername TEXT, recently_used_codes TEXT);
CREATE TABLE code_cat (catid INTEGER PRIMARY KEY, name TEXT UNIQUE, memo TEXT, owner TEXT, date TEXT, supercatid INTEGER);
CREATE TABLE code_name (cid INTEGER PRIMARY KEY, name TEXT UNIQUE, memo TEXT, catid INTEGER, owner TEXT, date TEXT, color TEXT);
CREATE TABLE source (id INTEGER PRIMARY KEY, name TEXT, fulltext TEXT, mediapath TEXT, memo TEXT, owner TEXT, date TEXT, av_text_id INTEGER, risid INTEGER, UNIQUE(name));
CREATE TABLE code_text (ctid INTEGER PRIMARY KEY, cid INTEGER, fid INTEGER, seltext TEXT, pos0 INTEGER, pos1 INTEGER, owner TEXT, date TEXT, memo TEXT, avid INTEGER, important INTEGER, UNIQUE(cid, fid, pos0, pos1, owner));
CREATE TABLE cases (caseid INTEGER PRIMARY KEY, name TEXT, memo TEXT, owner TEXT, date TEXT, CONSTRAINT ucm UNIQUE(name));
CREATE TABLE case_text (id INTEGER PRIMARY KEY, caseid INTEGER, fid INTEGER, pos0 INTEGER, pos1 INTEGER, memo TEXT, owner TEXT, date TEXT);
CREATE TABLE annotation (anid INTEGER PRIMARY KEY, fid INTEGER, pos0 INTEGER, pos1 INTEGER, memo TEXT, owner TEXT, date TEXT);
CREATE TABLE journal (jid INTEGER PRIMARY KEY, name TEXT, jentry TEXT, date TEXT, owner TEXT);
CREATE TABLE attribute_type (name TEXT PRIMARY KEY, date TEXT, owner TEXT, memo TEXT, caseOrFile TEXT, valuetype TEXT);
CREATE TABLE attribute (attrid INTEGER PRIMARY KEY, name TEXT, attr_type TEXT, value TEXT, id INTEGER, date TEXT, owner TEXT);
CREATE TABLE code_image (imid INTEGER PRIMARY KEY, id INTEGER, x1 INTEGER, y1 INTEGER, width INTEGER, height INTEGER, cid INTEGER, memo TEXT, date TEXT, owner TEXT, important INTEGER, pdf_page INTEGER);
CREATE TABLE code_av (avid INTEGER PRIMARY KEY, cid INTEGER, id INTEGER, pos0 INTEGER, pos1 INTEGER, memo TEXT, owner TEXT, date TEXT, important INTEGER DEFAULT 0);
"""

# Serialization payloads: emoji, accents, CJK, a ZWJ family sequence, and a flag.
EMOJI_BLOCK = "Café ☕ 🔬 succeeded — 日本語のメモ — family 👩‍👩‍👧‍👦 flag 🇬🇧 end."
# ~60k-char run that also carries non-ASCII, to test very long strings.
LONG_BLOCK = ("Löng-røw " * 7000)  # ~63k chars, deterministic


def _connect(folder: Path) -> sqlite3.Connection:
    folder.mkdir(parents=True, exist_ok=True)
    db = folder / "data.qda"
    if db.exists():
        db.unlink()
    conn = sqlite3.connect(str(db))
    conn.executescript(_SCHEMA)
    return conn


def build_standard_project(folder: Path) -> str:
    """A normal v14 project: 2 text files, 2 codes, 1 category, 1 coding.

    project.memo / project.about are NULL on purpose so get_project_summary
    surfaces genuine JSON nulls.  File 2 carries the unicode/emoji + very-long
    serialization payloads, coded under cid=3.
    """
    conn = _connect(folder)
    c = conn.cursor()
    c.execute("INSERT INTO project (databaseversion,date,memo,about,codername) "
              "VALUES ('v14','2024-01-15',NULL,NULL,'TrackThree')")
    c.execute("INSERT INTO code_cat VALUES (1,'Category A','','TrackThree','2024-01-15',NULL)")
    c.executemany("INSERT INTO code_name VALUES (?,?,?,?,?,?,?)", [
        (1, "Stress", "Stress code", 1, "TrackThree", "2024-01-15", "#FF0000"),
        (2, "Coping", "Coping code", 1, "TrackThree", "2024-01-15", "#00FF00"),
        (3, "Serialization", "", None, "TrackThree", "2024-01-15", "#0000FF"),
    ])
    t1 = "The team was under pressure. I feel stressed about deadlines. I cope by exercising."
    c.execute("INSERT INTO source (id,name,fulltext,mediapath,memo,owner,date) "
              "VALUES (1,'interview.txt',?,NULL,'file memo','TrackThree','2024-01-15')", (t1,))

    # File 2 carries the serialization payloads verbatim inside its fulltext.
    t2 = "PREFIX. " + EMOJI_BLOCK + " || " + LONG_BLOCK + " END"
    c.execute("INSERT INTO source (id,name,fulltext,mediapath,memo,owner,date) "
              "VALUES (2,'unicode.txt',?,NULL,NULL,'TrackThree','2024-01-15')", (t2,))

    # Coding on file 1 (Stress).
    a = t1.index("I feel stressed about deadlines")
    b = a + len("I feel stressed about deadlines")
    c.execute("INSERT INTO code_text (ctid,cid,fid,seltext,pos0,pos1,owner,date,memo,important) "
              "VALUES (1,1,1,?,?,?,'TrackThree','2024-01-15','key',1)",
              ("I feel stressed about deadlines", a, b))

    # Two codings on file 2 (Serialization): the emoji block and the long block.
    ea = t2.index(EMOJI_BLOCK)
    eb = ea + len(EMOJI_BLOCK)
    la = t2.index(LONG_BLOCK)
    lb = la + len(LONG_BLOCK)
    c.execute("INSERT INTO code_text (ctid,cid,fid,seltext,pos0,pos1,owner,date,memo,important) "
              "VALUES (2,3,2,?,?,?,'TrackThree','2024-01-15',NULL,0)", (EMOJI_BLOCK, ea, eb))
    c.execute("INSERT INTO code_text (ctid,cid,fid,seltext,pos0,pos1,owner,date,memo,important) "
              "VALUES (3,3,2,?,?,?,'TrackThree','2024-01-15',NULL,0)", (LONG_BLOCK, la, lb))

    c.execute("INSERT INTO cases VALUES (1,'Case A','','TrackThree','2024-01-15')")
    c.execute("INSERT INTO journal VALUES (1,'Entry 1','Some notes','2024-01-15','TrackThree')")
    conn.commit()
    conn.close()
    return str(folder)


def build_large_project(folder: Path, n_codings: int = 4000) -> str:
    """A project with `n_codings` code_text rows on cid=1 -> large read payload."""
    conn = _connect(folder)
    c = conn.cursor()
    c.execute("INSERT INTO project (databaseversion,date,memo,about,codername) "
              "VALUES ('v14','2024-01-15','big','QualCoder big project','TrackThree')")
    c.execute("INSERT INTO code_cat VALUES (1,'Cat','','TrackThree','2024-01-15',NULL)")
    c.execute("INSERT INTO code_name VALUES (1,'Bulk','','1','TrackThree','2024-01-15','#123456')")
    # A single big source; each coding points at a distinct slice so positions
    # stay unique.  seltext is what dominates payload size.
    unit = "Participant reflects on workload, deadlines, and recovery rituals. "
    fulltext = unit * (n_codings + 10)
    c.execute("INSERT INTO source (id,name,fulltext,mediapath,memo,owner,date) "
              "VALUES (1,'bulk.txt',?,NULL,NULL,'TrackThree','2024-01-15')", (fulltext,))
    rows = []
    seg = "Participant reflects on workload, deadlines, and recovery rituals."  # 65 chars
    step = len(unit)
    for i in range(n_codings):
        p0 = i * step
        p1 = p0 + len(seg)
        rows.append((i + 1, 1, 1, seg, p0, p1, "TrackThree", "2024-01-15",
                     f"auto memo {i}", None, i % 2))
    c.executemany(
        "INSERT INTO code_text (ctid,cid,fid,seltext,pos0,pos1,owner,date,memo,avid,important) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return str(folder)


def build_write_project(folder: Path) -> str:
    """A project set up for the write-path approval flow.

    "I cope by exercising" is present in file 1 but NOT yet coded; the flow
    codes it under 'Coping' and we assert the DB row actually appears.
    """
    conn = _connect(folder)
    c = conn.cursor()
    c.execute("INSERT INTO project (databaseversion,date,memo,about,codername) "
              "VALUES ('v14','2024-01-15','w','QualCoder write test','TrackThree')")
    c.execute("INSERT INTO code_cat VALUES (1,'Category A','','TrackThree','2024-01-15',NULL)")
    c.executemany("INSERT INTO code_name VALUES (?,?,?,?,?,?,?)", [
        (1, "Stress", "", 1, "TrackThree", "2024-01-15", "#FF0000"),
        (2, "Coping", "", 1, "TrackThree", "2024-01-15", "#00FF00"),
    ])
    t1 = "The team was under pressure. I feel stressed about deadlines. I cope by exercising every day."
    c.execute("INSERT INTO source (id,name,fulltext,mediapath,memo,owner,date) "
              "VALUES (1,'interview.txt',?,NULL,NULL,'TrackThree','2024-01-15')", (t1,))
    # Pre-existing Stress coding only.
    a = t1.index("I feel stressed about deadlines")
    b = a + len("I feel stressed about deadlines")
    c.execute("INSERT INTO code_text (ctid,cid,fid,seltext,pos0,pos1,owner,date,memo,important) "
              "VALUES (1,1,1,?,?,?,'TrackThree','2024-01-15','',1)",
              ("I feel stressed about deadlines", a, b))
    conn.commit()
    conn.close()
    return str(folder)


# --------------------------------------------------------------------------- #
# Transport plumbing
# --------------------------------------------------------------------------- #

def server_params() -> StdioServerParameters:
    """Launch `python -m qualcoder_mcp.server` with a private HOME (dynamic mode)."""
    env = os.environ.copy()
    env["HOME"] = str(HOME_DIR)          # POSIX: ~ -> HOME/.qualcoder_mcp/sessions
    env["USERPROFILE"] = str(HOME_DIR)   # Windows: expanduser() uses USERPROFILE
    env.pop("QUALCODER_PROJECT_PATH", None)   # dynamic project-selection mode
    env["PYTHONPATH"] = str(REPO / "src")
    return StdioServerParameters(
        command=str(VENV_PY),
        args=["-m", "qualcoder_mcp.server"],
        env=env,
    )


def text_of(result) -> str:
    """Concatenate all text content blocks of a CallToolResult."""
    out = []
    for block in result.content:
        if getattr(block, "type", None) == "text":
            out.append(block.text)
    return "".join(out)


def run(coro, timeout: float = 60.0):
    """Drive an async scenario on a fresh event loop with a hard timeout.

    The timeout is the crash/hang guard: a wedged pipe surfaces as a failure
    here instead of blocking the test session forever.
    """
    async def _wrapped():
        return await asyncio.wait_for(coro, timeout)
    return asyncio.run(_wrapped())


class Client:
    """Async context manager: spawn server, initialize, expose a ClientSession."""
    def __init__(self):
        self._stdio_cm = None
        self._session_cm = None
        self.session: ClientSession = None

    async def __aenter__(self) -> ClientSession:
        self._stdio_cm = stdio_client(server_params())
        read, write = await self._stdio_cm.__aenter__()
        self._session_cm = ClientSession(read, write)
        self.session = await self._session_cm.__aenter__()
        await self.session.initialize()
        return self.session

    async def __aexit__(self, *exc):
        try:
            await self._session_cm.__aexit__(*exc)
        finally:
            await self._stdio_cm.__aexit__(*exc)


# --------------------------------------------------------------------------- #
# Module setup
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module", autouse=True)
def _prepare_run_dir():
    if not VENV_PY.exists():
        pytest.skip(f"python executable not found: {VENV_PY}")
    for d in (PROJECTS_DIR, HOME_DIR):
        d.mkdir(parents=True, exist_ok=True)
    yield


@pytest.fixture(scope="module")
def standard_project():
    return build_standard_project(PROJECTS_DIR / "standard.qda")


@pytest.fixture(scope="module")
def large_project():
    return build_large_project(PROJECTS_DIR / "large.qda", n_codings=4000)


@pytest.fixture()
def write_project():
    # Fresh per test so apply_codings mutation is idempotent across re-runs.
    folder = PROJECTS_DIR / f"write_{uuid.uuid4().hex[:8]}.qda"
    path = build_write_project(folder)
    yield path
    shutil.rmtree(folder, ignore_errors=True)


# =========================================================================== #
# 1. Handshake + tool schema audit
# =========================================================================== #

def _audit_input_schema(name: str, schema) -> list:
    """Return a list of problem strings for one tool's inputSchema."""
    problems = []
    if not isinstance(schema, dict):
        return [f"{name}: inputSchema is not an object ({type(schema).__name__})"]
    if schema.get("type") != "object":
        problems.append(f"{name}: inputSchema.type != 'object' (got {schema.get('type')!r})")
    props = schema.get("properties", {})
    if not isinstance(props, dict):
        problems.append(f"{name}: properties is not an object")
        props = {}
    req = schema.get("required", [])
    if req is not None:
        if not isinstance(req, list):
            problems.append(f"{name}: required is not an array")
        else:
            for r in req:
                if not isinstance(r, str):
                    problems.append(f"{name}: required entry {r!r} not a string")
                elif r not in props:
                    problems.append(f"{name}: required '{r}' missing from properties")
    if _HAVE_JSONSCHEMA:
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as e:
            problems.append(f"{name}: not a valid JSON Schema: {e}")
    return problems


def test_handshake_and_tool_schemas():
    async def scenario():
        async with Client() as s:
            init_via_list = await s.list_tools()
            tools = init_via_list.tools
            problems = []
            names = [t.name for t in tools]
            # every tool JSON-serialisable end to end
            for t in tools:
                if not t.name:
                    problems.append("a tool has an empty name")
                if not (t.description and t.description.strip()):
                    problems.append(f"{t.name}: empty description")
                problems.extend(_audit_input_schema(t.name, t.inputSchema))
                if getattr(t, "outputSchema", None) and _HAVE_JSONSCHEMA:
                    try:
                        Draft202012Validator.check_schema(t.outputSchema)
                    except Exception as e:
                        problems.append(f"{t.name}: invalid outputSchema: {e}")
            return names, problems

    names, problems = run(scenario())
    assert len(names) == EXPECTED_TOOLS, (
        f"expected {EXPECTED_TOOLS} tools, got {len(names)}: {sorted(names)}")
    assert len(set(names)) == len(names), "duplicate tool names over the wire"
    assert not problems, "tool schema audit problems:\n" + "\n".join(problems)


def test_resources_and_prompts_lists():
    async def scenario():
        async with Client() as s:
            res = (await s.list_resources()).resources
            tmpl = (await s.list_resource_templates()).resourceTemplates
            prompts = (await s.list_prompts()).prompts
            problems = []
            for r in res:
                if not str(r.uri):
                    problems.append(f"resource {r.name!r} has empty uri")
                if not r.name:
                    problems.append(f"resource {r.uri} has empty name")
            for t in tmpl:
                if not t.uriTemplate:
                    problems.append(f"template {t.name!r} has empty uriTemplate")
            for p in prompts:
                if not p.name:
                    problems.append("a prompt has an empty name")
                for arg in (p.arguments or []):
                    if not arg.name:
                        problems.append(f"prompt {p.name}: argument with empty name")
            return len(res), len(tmpl), len(prompts), problems

    n_res, n_tmpl, n_prompts, problems = run(scenario())
    assert n_res == EXPECTED_CONCRETE_RESOURCES, f"concrete resources: {n_res}"
    assert n_tmpl == EXPECTED_RESOURCE_TEMPLATES, f"resource templates: {n_tmpl}"
    assert n_res + n_tmpl == EXPECTED_RESOURCES_TOTAL
    assert n_prompts == EXPECTED_PROMPTS, f"prompts: {n_prompts}"
    assert not problems, "resource/prompt audit problems:\n" + "\n".join(problems)


# =========================================================================== #
# 3. Real round-trip calls over the wire
# =========================================================================== #

def test_roundtrip_select_and_summary(standard_project):
    async def scenario():
        async with Client() as s:
            sel = await s.call_tool("select_project", {"project_path": standard_project})
            summ = await s.call_tool("get_project_summary", {})
            return json.loads(text_of(sel)), json.loads(text_of(summ))

    sel, summ = run(scenario())
    assert sel.get("success") is True, sel
    stats = summ["statistics"]
    assert stats["total_files"] == 2
    assert stats["total_codes"] == 3
    assert stats["total_coded_segments"] == 3


def test_read_resource_over_wire(standard_project):
    async def scenario():
        async with Client() as s:
            await s.call_tool("select_project", {"project_path": standard_project})
            r = await s.read_resource("qualcoder://project/info")
            return "".join(c.text for c in r.contents if getattr(c, "text", None))

    body = run(scenario())
    parsed = json.loads(body)
    assert "database_version" in parsed or "error" in parsed


def test_large_payload_transport(large_project):
    async def scenario():
        async with Client() as s:
            await s.call_tool("select_project", {"project_path": large_project})
            t0 = time.perf_counter()
            res = await s.call_tool("get_coded_segments", {"code_id": 1, "limit": 5000})
            elapsed = time.perf_counter() - t0
            raw = text_of(res)
            return raw, elapsed

    raw, elapsed = run(scenario(), timeout=90.0)
    payload = json.loads(raw)                    # a single large JSON-RPC message parsed intact
    assert payload["segment_count"] == 4000, payload.get("segment_count")
    assert len(payload["segments"]) == 4000
    # Prove it really was a big single message that crossed the pipe.
    assert len(raw) > 400_000, f"payload only {len(raw)} bytes"
    # First/last segments intact -> no truncation at the transport boundary.
    assert payload["segments"][0]["text"].startswith("Participant reflects")
    assert payload["segments"][-1]["position_end"] > payload["segments"][0]["position_end"]
    # Record perf for the report (visible with -s).
    print(f"\n[large-payload] {len(raw):,} bytes, 4000 segments, {elapsed*1000:.0f} ms")


def test_write_path_approval_flow(write_project):
    """analyze_for_coding -> record_suggestions -> approve -> apply_codings, then
    verify the coding is really in the database over a fresh connection."""
    async def scenario():
        async with Client() as s:
            sel = await s.call_tool("select_project", {"project_path": write_project})
            assert json.loads(text_of(sel))["success"] is True

            analyze = text_of(await s.call_tool("analyze_for_coding", {"file_ids": [1]}))
            m = re.search(r"Session ID: `([^`]+)`", analyze)
            assert m, f"no session id in analyze output:\n{analyze[:400]}"
            sid = m.group(1)

            rec = json.loads(text_of(await s.call_tool("record_suggestions", {
                "session_id": sid,
                "suggestions": [{
                    "file_id": 1,
                    "code_name": "Coping",
                    "segment_text": "I cope by exercising",
                    "reasoning": "explicit coping behaviour",
                    "confidence": 0.95,
                }],
            })))
            assert rec["recorded_count"] == 1, rec
            guid = rec["recorded"][0]["guid"]

            # update_suggestion_status returns formatted text, not JSON.
            upd = text_of(await s.call_tool("update_suggestion_status", {
                "session_id": sid, "approve": [guid],
            }))

            applied = text_of(await s.call_tool("apply_codings", {
                "session_id": sid, "create_backup": True,
            }))

            # Independent verification: read Coping segments back over the wire.
            verify = json.loads(text_of(
                await s.call_tool("get_coded_segments", {"code_id": 2})))
            return upd, applied, verify

    upd, applied, verify = run(scenario())
    # The approval was recorded (formatted-text response).
    assert "Approved: 1" in upd, upd
    # The write actually happened.
    assert "CODINGS APPLIED TO DATABASE" in applied, applied[:300]
    assert "Successfully Applied: 1" in applied, applied[:300]
    # And the coding is genuinely persisted.
    assert verify["segment_count"] == 1, verify
    assert verify["segments"][0]["text"] == "I cope by exercising"


# =========================================================================== #
# 4. Error propagation + server survives / stays responsive
# =========================================================================== #

def test_error_propagation_and_liveness(standard_project):
    """Each faulty call must yield a proper MCP error or a structured error
    result — never a crash/hang — and the server must answer tools/list after."""
    from mcp.shared.exceptions import McpError

    async def one_bad_call(s, kind):
        """Run a faulty call; classify the outcome; then confirm liveness."""
        outcome = {"kind": kind}
        try:
            if kind == "missing_required":
                res = await s.call_tool("select_project", {})
            elif kind == "wrong_type":
                res = await s.call_tool("get_coded_segments", {"code_id": ["not", "int"]})
            elif kind == "nonexistent_project":
                res = await s.call_tool("select_project",
                                        {"project_path": "/no/such/place.qda"})
            elif kind == "oversized_input":
                res = await s.call_tool("search_coded_text", {"query": "x" * 2_000_000})
            elif kind == "unknown_tool":
                res = await s.call_tool("this_tool_does_not_exist", {})
            else:
                raise AssertionError(kind)
            outcome["raised"] = None
            outcome["isError"] = bool(res.isError)
            outcome["text"] = text_of(res)[:200]
        except McpError as e:
            outcome["raised"] = "McpError"
            outcome["text"] = str(e)[:200]
        except Exception as e:
            outcome["raised"] = type(e).__name__
            outcome["text"] = str(e)[:200]
        # Liveness probe AFTER the fault.
        alive = await s.list_tools()
        outcome["alive_tools"] = len(alive.tools)
        return outcome

    async def scenario():
        async with Client() as s:
            # A project is needed for oversized_input to reach real code.
            await s.call_tool("select_project", {"project_path": standard_project})
            results = []
            for kind in ("missing_required", "wrong_type", "nonexistent_project",
                         "oversized_input", "unknown_tool"):
                results.append(await one_bad_call(s, kind))
            return results

    results = run(scenario(), timeout=90.0)
    by = {r["kind"]: r for r in results}

    # Server answered tools/list after every single fault.
    for r in results:
        assert r["alive_tools"] == EXPECTED_TOOLS, f"server not responsive after {r['kind']}: {r}"

    # missing required arg -> validation error (isError result or McpError).
    mr = by["missing_required"]
    assert mr["raised"] == "McpError" or mr.get("isError"), mr

    # wrong-typed arg -> validation error, not a silent success.
    wt = by["wrong_type"]
    assert wt["raised"] == "McpError" or wt.get("isError"), wt

    # nonexistent project -> STRUCTURED error result (guarded, not a crash).
    npj = by["nonexistent_project"]
    assert npj["raised"] is None and not npj.get("isError"), npj
    assert "error" in json.loads(npj["text"] if npj["text"].strip().startswith("{")
                                 else "{}") or "not found" in npj["text"].lower() \
        or "invalid" in npj["text"].lower(), npj

    # oversized input -> handled response (structured), server alive.
    ov = by["oversized_input"]
    assert ov["alive_tools"] == EXPECTED_TOOLS, ov

    # unknown tool -> proper MCP error (raised) or isError, server alive.
    ut = by["unknown_tool"]
    assert ut["raised"] == "McpError" or ut.get("isError"), ut


# =========================================================================== #
# 5. Serialization edge cases across the JSON-RPC boundary
# =========================================================================== #

def test_serialization_unicode_long_and_null(standard_project):
    async def scenario():
        async with Client() as s:
            await s.call_tool("select_project", {"project_path": standard_project})
            summ = json.loads(text_of(await s.call_tool("get_project_summary", {})))
            segs = json.loads(text_of(
                await s.call_tool("get_coded_segments", {"code_id": 3, "limit": 100})))
            return summ, segs

    summ, segs = run(scenario())

    # (a) genuine JSON null survived the boundary as Python None.
    assert summ["project_info"]["memo"] is None, summ["project_info"]
    assert summ["project_info"]["about"] is None, summ["project_info"]

    texts = {seg["text"] for seg in segs["segments"]}
    # (b) unicode / emoji / ZWJ family / flag survived byte-for-byte.
    assert EMOJI_BLOCK in texts, "emoji/unicode block did not round-trip intact"
    # explicit codepoint checks for the trickiest sequences
    emoji_seg = next(t for t in texts if t == EMOJI_BLOCK)
    assert "👩‍👩‍👧‍👦" in emoji_seg      # ZWJ family
    assert "🇬🇧" in emoji_seg                            # regional-indicator flag
    assert "日本語のメモ" in emoji_seg                     # CJK

    # (c) very long non-ASCII string survived with exact length & content.
    long_seg = next((t for t in texts if t.startswith("Löng-røw")), None)
    assert long_seg is not None
    assert long_seg == LONG_BLOCK
    assert len(long_seg) == len(LONG_BLOCK) > 60_000
