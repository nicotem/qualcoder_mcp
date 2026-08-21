"""QA gate additions for QUALCODER_MCP_TOOLSET (feat/local-hosts).

Independent verification beyond test_toolset_modes.py: case-insensitivity
proven over a REAL subprocess (not just the unit resolver), registry
mutation double-applied and restored in one process (contamination guard),
and resources/prompts byte-parity between core and full modes.
"""

import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

VENV_PY = Path(sys.executable)
FULLTEXT = "This is interview text. I feel stressed about deadlines."

SCHEMA_MIN = """
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


def _project(parent: Path) -> Path:
    folder = parent / "gate_project.qda"
    folder.mkdir()
    conn = sqlite3.connect(str(folder / "data.qda"))
    conn.executescript(SCHEMA_MIN)
    conn.execute("INSERT INTO project (databaseversion, codername) "
                 "VALUES ('v14', 'Gate')")
    conn.execute("INSERT INTO source (id, name, fulltext) "
                 "VALUES (1, 'a.txt', ?)", (FULLTEXT,))
    conn.commit()
    conn.close()
    return folder


def _session_lists(tmp_path, toolset_value):
    """Spawn a real server with the given env value; return
    (tool_names, resources_json, prompts_json)."""
    project = _project(tmp_path)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    # Windows-portable subprocess env: inherit os.environ (a minimal dict
    # drops SYSTEMROOT, which crashes Windows Python children at startup)
    # and set USERPROFILE alongside HOME (expanduser() on Windows), the
    # same pattern as the Windows-green transport tests.
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["QUALCODER_PROJECT_PATH"] = str(project)
    env.pop("QUALCODER_MCP_TOOLSET", None)
    if toolset_value is not None:
        env["QUALCODER_MCP_TOOLSET"] = toolset_value
    params = StdioServerParameters(command=str(VENV_PY),
                                   args=["-m", "qualcoder_mcp.server"],
                                   env=env)

    async def drive():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                resources = await session.list_resources()
                prompts = await session.list_prompts()
                return (
                    sorted(t.name for t in tools.tools),
                    json.dumps(sorted(
                        (str(r.uri), r.name, r.description or "")
                        for r in resources.resources)),
                    json.dumps(sorted(
                        (p.name, p.description or "",
                         json.dumps([a.model_dump() for a in
                                     (p.arguments or [])], sort_keys=True))
                        for p in prompts.prompts)),
                )

    return asyncio.run(drive())


class TestSubprocessBehavior:

    def test_case_insensitive_core_in_real_subprocess(self, tmp_path):
        """' CoRe ' (case + whitespace) must serve exactly the 20-tool set
        through the real transport, not just the unit resolver."""
        names, _, _ = _session_lists(tmp_path, " CoRe ")
        assert names == sorted(server.CORE_TOOLSET)
        assert len(names) == 20

    def test_resources_and_prompts_byte_identical_across_modes(self, tmp_path):
        """The mode must not touch resources or prompts AT ALL — byte-equal
        listings, not just non-empty ones."""
        _, res_core, prm_core = _session_lists(tmp_path, "core")
        shutil_tmp = tmp_path / "full_run"
        shutil_tmp.mkdir()
        _, res_full, prm_full = _session_lists(shutil_tmp, "full")
        assert res_core == res_full
        assert prm_core == prm_full

    def test_unset_and_full_are_identical_tool_lists(self, tmp_path):
        """Default (unset) and explicit 'full' serve the same 67 names."""
        unset_dir = tmp_path / "unset"
        unset_dir.mkdir()
        names_unset, _, _ = _session_lists(unset_dir, None)
        full_dir = tmp_path / "full"
        full_dir.mkdir()
        names_full, _, _ = _session_lists(full_dir, "full")
        assert names_unset == names_full
        assert len(names_full) == 67


class TestRegistryIsolation:

    def test_double_apply_and_restore_in_one_process(self):
        """Contamination guard: apply core twice with restoration between,
        and confirm the registry returns to the identical 67-tool state
        (same objects, not lookalikes)."""
        before = dict(server.mcp._tool_manager._tools)
        assert len(before) == 67

        for _ in range(2):
            removed = server._apply_toolset("core")
            try:
                assert len(server.mcp._tool_manager._tools) == 20
                assert set(server.mcp._tool_manager._tools) \
                    == set(server.CORE_TOOLSET)
            finally:
                for name, tool in removed.items():
                    server.mcp._tool_manager._tools[name] = tool

        after = dict(server.mcp._tool_manager._tools)
        assert after.keys() == before.keys()
        for name in before:
            assert after[name] is before[name]      # identity, not equality

    def test_core_filter_is_startup_only_not_import_time(self):
        """Importing the module must never shrink the surface — the filter
        runs only in main(). (A regression here would contaminate every
        in-process consumer, including the whole test suite.)"""
        assert len(server.mcp._tool_manager._tools) == 67
        # and CORE_TOOLSET stays a strict subset of the live surface
        assert server.CORE_TOOLSET < set(server.mcp._tool_manager._tools)
