"""QUALCODER_MCP_TOOLSET modes (EXPERIMENTAL, multi-host plan section 3.1).

Default full = backward compatible 67-tool surface. core = the 20-tool
supervised-coding-loop subset for local-model hosts. Unknown values fail
loudly at startup. The functional smoke drives a core-mode server over the
REAL stdio transport through the whole suggest -> apply loop, proving the
core set is a coherent workflow rather than just a shorter list.
"""

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO = Path(__file__).resolve().parent.parent
VENV_PY = Path(sys.executable)

EXPECTED_FULL = 67
EXPECTED_CORE = 20

SCHEMA = """
CREATE TABLE project (databaseversion TEXT, date TEXT, memo TEXT, about TEXT, bookmarkfile INTEGER, bookmarkpos INTEGER, codername TEXT, recently_used_codes TEXT);
CREATE TABLE code_cat (catid INTEGER PRIMARY KEY, name TEXT UNIQUE, memo TEXT, owner TEXT, date TEXT, supercatid INTEGER);
CREATE TABLE code_name (cid INTEGER PRIMARY KEY, name TEXT UNIQUE, memo TEXT, catid INTEGER, owner TEXT, date TEXT, color TEXT);
CREATE TABLE source (id INTEGER PRIMARY KEY, name TEXT, fulltext TEXT, mediapath TEXT, memo TEXT, owner TEXT, date TEXT, av_text_id INTEGER, risid INTEGER, UNIQUE(name));
CREATE TABLE code_text (ctid INTEGER PRIMARY KEY, cid INTEGER, fid INTEGER, seltext TEXT, pos0 INTEGER, pos1 INTEGER, owner TEXT, date TEXT, memo TEXT, avid INTEGER, important INTEGER, UNIQUE(cid, fid, pos0, pos1, owner));
CREATE TABLE cases (caseid INTEGER PRIMARY KEY, name TEXT, memo TEXT, owner TEXT, date TEXT, CONSTRAINT ucm UNIQUE(name));
CREATE TABLE case_text (id INTEGER PRIMARY KEY, caseid INTEGER, fid INTEGER, pos0 INTEGER, pos1 INTEGER, memo TEXT, owner TEXT, date TEXT);
CREATE TABLE annotation (anid INTEGER PRIMARY KEY, fid INTEGER, pos0 INTEGER, pos1 INTEGER, memo TEXT, owner TEXT, date TEXT);
CREATE TABLE journal (jid INTEGER PRIMARY KEY, name TEXT, jentry TEXT, date TEXT, owner TEXT);
CREATE TABLE coder_names (name TEXT UNIQUE NOT NULL, visibility INTEGER NOT NULL DEFAULT 1 CHECK (visibility IN (0, 1)));
CREATE TABLE attribute_type (name TEXT PRIMARY KEY, date TEXT, owner TEXT, memo TEXT, caseOrFile TEXT, valuetype TEXT);
CREATE TABLE attribute (attrid INTEGER PRIMARY KEY, name TEXT, attr_type TEXT, value TEXT, id INTEGER, date TEXT, owner TEXT);
CREATE TABLE code_image (imid INTEGER PRIMARY KEY, id INTEGER, x1 INTEGER, y1 INTEGER, width INTEGER, height INTEGER, cid INTEGER, memo TEXT, date TEXT, owner TEXT, important INTEGER, pdf_page INTEGER);
CREATE TABLE code_av (avid INTEGER PRIMARY KEY, cid INTEGER, id INTEGER, pos0 INTEGER, pos1 INTEGER, memo TEXT, owner TEXT, date TEXT, important INTEGER DEFAULT 0);
"""

FULLTEXT = ("The interviews were exhausting to schedule. I feel stressed "
            "about deadlines. I cope by exercising in the evenings.")


def _build_project(parent: Path) -> Path:
    folder = parent / "toolset_project.qda"
    folder.mkdir()
    conn = sqlite3.connect(str(folder / "data.qda"))
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO project (databaseversion,date,memo,about,codername) "
                 "VALUES ('v14','2024-01-15','','','ToolsetTest')")
    conn.execute("INSERT INTO code_name VALUES "
                 "(1,'Stress','',NULL,'ToolsetTest','2024-01-15','#FF0000')")
    conn.execute("INSERT INTO source (id,name,fulltext,mediapath,memo,owner,date) "
                 "VALUES (1,'interview.txt',?,NULL,'','ToolsetTest','2024-01-15')",
                 (FULLTEXT,))
    conn.commit()
    conn.close()
    return folder


def _text_of(result) -> str:
    return "".join(b.text for b in result.content
                   if getattr(b, "type", None) == "text")


def _server_env(home: Path, project: Path, toolset=None) -> dict:
    """Subprocess env for the spawned server, Windows-portable.

    Start from os.environ.copy() rather than a minimal dict: Windows
    Python subprocesses crash at startup without SYSTEMROOT (and need
    TEMP etc.), and expanduser() there uses USERPROFILE, not HOME —
    the exact pattern the Windows-green transport tests use.
    """
    env = os.environ.copy()
    env["HOME"] = str(home)          # POSIX ~
    env["USERPROFILE"] = str(home)   # Windows ~
    env["QUALCODER_PROJECT_PATH"] = str(project)
    env.pop("QUALCODER_MCP_TOOLSET", None)
    if toolset is not None:
        env["QUALCODER_MCP_TOOLSET"] = toolset
    return env


# ---------------------------------------------------------------------------
# Unit level
# ---------------------------------------------------------------------------

class TestToolsetResolution:

    def test_default_is_full(self, monkeypatch):
        monkeypatch.delenv("QUALCODER_MCP_TOOLSET", raising=False)
        assert server._resolve_toolset_mode() == "full"

    def test_explicit_values_case_insensitive(self, monkeypatch):
        for raw, expect in [("core", "core"), ("FULL", "full"),
                            (" Core ", "core")]:
            monkeypatch.setenv("QUALCODER_MCP_TOOLSET", raw)
            assert server._resolve_toolset_mode() == expect

    def test_unknown_value_raises_loudly(self, monkeypatch):
        monkeypatch.setenv("QUALCODER_MCP_TOOLSET", "banana")
        with pytest.raises(ValueError) as ei:
            server._resolve_toolset_mode()
        msg = str(ei.value)
        assert "banana" in msg and "core" in msg and "full" in msg

    def test_default_surface_is_backward_compatible(self):
        """No env var -> the full 67-tool surface, untouched."""
        tools = asyncio.run(server.mcp.list_tools())
        assert len(tools) == EXPECTED_FULL

    def test_core_exact_membership(self):
        """core registers exactly the documented 20-tool list."""
        removed = server._apply_toolset("core")
        try:
            tools = asyncio.run(server.mcp.list_tools())
            names = {t.name for t in tools}
            assert names == set(server.CORE_TOOLSET)
            assert len(names) == EXPECTED_CORE
        finally:
            for name, tool in removed.items():
                server.mcp._tool_manager._tools[name] = tool
        assert len(asyncio.run(server.mcp.list_tools())) == EXPECTED_FULL

    def test_full_mode_removes_nothing(self):
        removed = server._apply_toolset("full")
        assert removed == {}
        assert len(asyncio.run(server.mcp.list_tools())) == EXPECTED_FULL

    def test_core_list_is_a_subset_of_the_real_surface(self):
        """Guards against a core name drifting out of sync after a rename."""
        names = {t.name for t in asyncio.run(server.mcp.list_tools())}
        assert server.CORE_TOOLSET <= names


# ---------------------------------------------------------------------------
# Startup level: unknown value fails loudly, before serving anything
# ---------------------------------------------------------------------------

class TestStartupFailsLoudly:

    def test_unknown_toolset_exits_nonzero_with_clear_message(self, tmp_path):
        project = _build_project(tmp_path)
        home = tmp_path / "home"
        home.mkdir()
        env = _server_env(home, project, toolset="banana")
        proc = subprocess.run(
            [str(VENV_PY), "-m", "qualcoder_mcp.server"],
            env=env, capture_output=True, timeout=60,
            cwd=str(tmp_path),
            # decode captured output as UTF-8 explicitly: Windows would
            # otherwise use cp1252 and can garble server banners
            encoding="utf-8", errors="replace",
        )
        assert proc.returncode == 1
        assert "Unknown QUALCODER_MCP_TOOLSET" in proc.stderr
        assert "banana" in proc.stderr
        assert "Traceback" not in proc.stderr


# ---------------------------------------------------------------------------
# Functional smoke: core mode serves the whole supervised loop over stdio
# ---------------------------------------------------------------------------

class TestCoreModeEndToEnd:

    def test_core_mode_suggest_apply_loop_over_stdio(self, tmp_path):
        project = _build_project(tmp_path)
        home = tmp_path / "home"
        home.mkdir()
        params = StdioServerParameters(
            command=str(VENV_PY),
            args=["-m", "qualcoder_mcp.server"],
            env=_server_env(home, project, toolset="core"),
        )

        async def drive():
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    tools = await session.list_tools()
                    names = {t.name for t in tools.tools}
                    assert names == set(server.CORE_TOOLSET)
                    assert len(names) == EXPECTED_CORE

                    # resources and prompts are unaffected by the mode
                    resources = await session.list_resources()
                    prompts = await session.list_prompts()
                    assert len(resources.resources) >= 1
                    assert len(prompts.prompts) >= 1

                    # S1-style read
                    out = _text_of(await session.call_tool(
                        "get_project_summary", {}))
                    assert "interview.txt" in out or "project_info" in out

                    # full suggestion loop, S2-style
                    out = _text_of(await session.call_tool(
                        "analyze_for_coding", {"file_ids": [1]}))
                    sid = out.split("Session ID: `")[1].split("`")[0]

                    out = _text_of(await session.call_tool(
                        "record_suggestions", {
                            "coding_session_id": sid,
                            "suggestions": [{
                                "file_id": 1, "code_name": "Stress",
                                "segment_text":
                                    "I feel stressed about deadlines",
                                "reasoning": "explicit stress statement",
                                "confidence": 0.9,
                            }],
                        }))
                    rec = json.loads(out)
                    assert rec["recorded_count"] == 1
                    guid = rec["recorded"][0]["guid"]

                    out = _text_of(await session.call_tool(
                        "review_suggestions", {"coding_session_id": sid}))
                    assert "Stress" in out

                    out = _text_of(await session.call_tool(
                        "edit_suggestion", {
                            "coding_session_id": sid, "suggestion_guid": guid,
                            "use_alternative": "longer",
                        }))
                    assert json.loads(out).get("success") is True

                    out = _text_of(await session.call_tool(
                        "update_suggestion_status",
                        {"coding_session_id": sid, "approve": [guid]}))
                    assert "error" not in out.lower() or "approved" in out

                    out = _text_of(await session.call_tool(
                        "apply_codings",
                        {"coding_session_id": sid, "create_backup": True}))
                    assert "CODINGS APPLIED" in out

                    # undo half of the safety pair works too
                    out = _text_of(await session.call_tool(
                        "list_backups", {}))
                    assert json.loads(out)["backup_count"] >= 1

                    # an excluded tool is genuinely not callable
                    result = await session.call_tool(
                        "propose_codes",
                        {"coding_session_id": sid, "proposals": [{"name": "X"}]})
                    blob = _text_of(result).lower()
                    assert result.isError or "unknown tool" in blob \
                        or "not found" in blob

        asyncio.run(drive())

        # the coding landed in the database
        conn = sqlite3.connect(str(project / "data.qda"))
        rows = conn.execute(
            "SELECT seltext FROM code_text WHERE owner='AI Coding Assistant'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1
