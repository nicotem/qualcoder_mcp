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
import re
import sys
import tempfile
from pathlib import Path

import pytest

from track5_helpers import write_fixture_sidecar

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO = Path(__file__).resolve().parent.parent
VENV_PY = Path(sys.executable)

EXPECTED_FULL = 71
EXPECTED_CORE = 21

SCHEMA = """
CREATE TABLE project (databaseversion TEXT, date TEXT, memo TEXT, about TEXT, bookmarkfile INTEGER, bookmarkpos INTEGER, codername TEXT, recently_used_codes TEXT);
CREATE TABLE code_cat (catid INTEGER PRIMARY KEY, name TEXT UNIQUE, memo TEXT, owner TEXT, date TEXT, supercatid INTEGER);
CREATE TABLE code_name (cid INTEGER PRIMARY KEY, name TEXT UNIQUE, memo TEXT, catid INTEGER, owner TEXT, date TEXT, color TEXT);
CREATE TABLE source (id INTEGER PRIMARY KEY, name TEXT, fulltext TEXT, mediapath TEXT, memo TEXT, owner TEXT, date TEXT, av_text_id INTEGER, risid INTEGER, UNIQUE(name));
CREATE TABLE code_text (ctid INTEGER PRIMARY KEY, cid INTEGER, fid INTEGER, seltext TEXT, pos0 INTEGER, pos1 INTEGER, owner TEXT, date TEXT, memo TEXT, avid INTEGER, important INTEGER, UNIQUE(cid, fid, pos0, pos1, owner));
CREATE TABLE cases (caseid INTEGER PRIMARY KEY, name TEXT, memo TEXT, owner TEXT, date TEXT, CONSTRAINT ucm UNIQUE(name));
CREATE TABLE case_text (id INTEGER PRIMARY KEY, caseid INTEGER, fid INTEGER, pos0 INTEGER, pos1 INTEGER, memo TEXT, owner TEXT, date TEXT);
CREATE TABLE annotation (anid INTEGER PRIMARY KEY, fid INTEGER, pos0 INTEGER, pos1 INTEGER, memo TEXT, owner TEXT, date TEXT, unique(fid,pos0,pos1,owner));
CREATE TABLE journal (jid INTEGER PRIMARY KEY, name TEXT, jentry TEXT, date TEXT, owner TEXT, unique(name));
CREATE TABLE coder_names (name TEXT UNIQUE NOT NULL);
CREATE TABLE attribute_type (name TEXT PRIMARY KEY, date TEXT, owner TEXT, memo TEXT, caseOrFile TEXT, valuetype TEXT);
CREATE TABLE attribute (attrid INTEGER PRIMARY KEY, name TEXT, attr_type TEXT, value TEXT, id INTEGER, date TEXT, owner TEXT, unique(name,attr_type,id));
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
    write_fixture_sidecar(folder)          # a project that has been asked
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


class TestThePublishedSchemaBudget:
    """One measurement, propagated to every site (QA round 1, F8 and F21).

    Three documents quote the size of the serialised tool definitions,
    and a reader sizes a context window from them. The 0.12 entry was
    written from two different interpreters' measurements, which made
    the recorded growth arithmetically impossible, and the README block
    was never updated at all: it still quoted the pre-batch figures while
    claiming to be the 0.12 measurement.

    Fix round 2 goes further, because agreement between three stale
    documents is still three stale documents: the figures published were
    reproducible at no commit on the branch, and the previous version of
    this class could not tell, which a verifier demonstrated by adding a
    docstring and watching it stay green. These tests now RE-MEASURE the
    tool surface and compare the result with what the documents say.

    The measurement is the one the documents describe, on the final tree
    through the toolset gate: the `tools/list` payload, which is the
    name, description and input schema of every registered tool,
    serialised together with `json.dumps` defaults.

    The figure moves with the interpreter (3.13 strips docstring
    indentation that 3.10 to 3.12 keep, worth about five per cent) and
    with the installed mcp and pydantic, which is the trap the last
    round fell into. So the comparison is EXACT on the interpreter the
    documents name, Python 3.13, and within a stated tolerance on 3.10
    to 3.12.
    """

    # The published measurement, to the character. Re-measure every tree
    # the same way before changing these, and say in the CHANGELOG which
    # interpreter and which environment directory it was taken in.
    FULL_MEASURED = 163_281          # 71 tools, Python 3.13.5, mcp 1.30.0
    CORE_MEASURED = 56_568           # 21 tools, same environment
    FULL_MEASURED_310 = 171_649      # the same tree on Python 3.11.13
    CORE_MEASURED_310 = 59_520

    # Why two per cent, away from the reference environment.
    #
    # Within one interpreter band the only thing that moves this number
    # without a source change is the schema generation in mcp and
    # pydantic, which perturbs field descriptions and key ordering by a
    # few hundred characters: 0.1 to 0.5 per cent across the versions
    # measured. Two per cent is four times that, so a dependency bump
    # inside `mcp>=1.17.0,<2` does not fail a green suite for a reason no
    # reader of the figure would care about.
    #
    # What the loose half does NOT do, stated plainly because the
    # previous version of this comment claimed the opposite: away from
    # the reference interpreter it does not report a single tool added
    # or removed. The average tool contributes 2,081 characters, about
    # 1.5 per cent, which is INSIDE two per cent; two tools, about 2.9
    # per cent, are outside it. So away from 3.13 this is an alarm for
    # large drift and not a gate on the figure.
    #
    # The gate is the exact half. It runs on the interpreter the
    # documents name, where a single added docstring paragraph is
    # enough to turn it red, and CI runs 3.13 on all three platforms,
    # so every change to the tool surface meets it there. The pin below
    # drives both facts so this paragraph cannot rot away from them.
    TOLERANCE = 0.02

    FULL_CHARS = "163,281"
    CORE_CHARS = "56,568"
    FULL_ROUNDED = "163,000"
    CORE_ROUNDED = "56,000"
    FULL_TOKENS = "40k"
    CORE_TOKENS = "14k"

    @staticmethod
    def _read(name):
        """The document with its line wrapping flattened: these figures
        straddle line breaks, and a wrap must not hide a stale number."""
        text = (REPO / name).read_text(encoding="utf-8")
        return " ".join(text.replace("\n>", " ").split())

    @classmethod
    def _current_entry(cls):
        """The entry that carries the figure this class pins.

        The current measurement belongs to the release being written,
        not to the last one that shipped: a reader sizing a context
        window wants today's number, and an older entry's figure is
        history the moment a tool surface moves. v0.13 removed an
        argument from six tools, so the two live in different entries
        for the first time and this class follows the current one.
        """
        return cls._read("CHANGELOG.md").split("## [0.12")[0]

    @classmethod
    def _v012_entries(cls):
        """The 0.12 entries, whose figures are history and stay put."""
        text = cls._read("CHANGELOG.md")
        return text[text.index("## [0.12"):text.index("## [0.11")]

    @staticmethod
    def _measure(mode):
        """The published measurement, recomputed.

        Through `_apply_toolset`, the call that actually gates the
        surface, and restoring what it removed so the rest of the suite
        sees the tools it expects.
        """
        removed = server._apply_toolset(mode)
        try:
            tools = asyncio.run(server.mcp.list_tools())
            payload = [{"name": t.name,
                        "description": t.description or "",
                        "inputSchema": t.inputSchema} for t in tools]
            return len(tools), len(json.dumps(payload))
        finally:
            for name, tool in removed.items():
                server.mcp._tool_manager._tools[name] = tool

    # The mcp the documents say the figure was taken with. Deliberately
    # NOT part of the exactness test: keying exactness on the installed
    # mcp would mean a dependency bump silently drops the comparison to
    # the loose half, which is the same silent-degradation shape this
    # round is fixing. On 3.13 the comparison is exact whatever mcp is
    # installed, so a bump that moves the schema turns the three 3.13 CI
    # jobs red and says to re-measure. That is intended: the documents
    # publish the figure TOGETHER with the mcp version, so if mcp moves,
    # both lines are stale and both are one edit.
    REFERENCE_MCP = "1.30.0"

    @staticmethod
    def _reference_environment():
        """Whether this is the interpreter the documents name."""
        return sys.version_info[:2] == (3, 13)

    def _assert_matches(self, mode, expected, expected_310, tools_expected):
        count, measured = self._measure(mode)
        assert count == tools_expected
        if self._reference_environment():
            assert measured == expected, (
                f"the {mode} toolset now measures {measured:,} characters "
                f"and the documents say {expected:,}. This is the "
                f"environment they name, so the figure has rotted: "
                f"re-measure both toolsets and update CHANGELOG.md, "
                f"README.md, INSTALL.md and this class together. If the "
                f"installed mcp is no longer {self.REFERENCE_MCP}, the "
                f"version those documents name is stale as well.")
            return
        target = expected_310 if sys.version_info[:2] < (3, 13) else expected
        drift = abs(measured - target) / target
        assert drift <= self.TOLERANCE, (
            f"the {mode} toolset measures {measured:,} characters against "
            f"the documented {target:,}, a drift of {drift:.1%}, past the "
            f"{self.TOLERANCE:.0%} allowed away from the reference "
            f"interpreter. Re-measure on Python 3.13 with mcp "
            f"{self.REFERENCE_MCP}.")

    def test_the_full_toolset_measures_what_the_documents_say(self):
        self._assert_matches("full", self.FULL_MEASURED,
                             self.FULL_MEASURED_310, EXPECTED_FULL)

    def test_the_core_toolset_measures_what_the_documents_say(self):
        self._assert_matches("core", self.CORE_MEASURED,
                             self.CORE_MEASURED_310, EXPECTED_CORE)

    def test_the_tolerance_is_a_real_comparison(self):
        """A tolerance nobody drives is a tolerance that passes anything.

        Both directions, against the reference figure, with no tool
        surface involved: one drift inside the band and one outside it.
        """
        inside = self.FULL_MEASURED + int(self.FULL_MEASURED * 0.01)
        outside = self.FULL_MEASURED + int(self.FULL_MEASURED * 0.03)
        assert abs(inside - self.FULL_MEASURED) / self.FULL_MEASURED \
            <= self.TOLERANCE
        assert abs(outside - self.FULL_MEASURED) / self.FULL_MEASURED \
            > self.TOLERANCE

    def test_one_average_tool_is_inside_the_loose_band(self):
        """The limit of the loose half, asserted rather than described.

        The comment above used to say a tool added or removed without
        re-measuring is reported. It is not, away from the reference
        interpreter: one average tool is about 1.5 per cent and the band
        is two. Two tools are outside it. What catches one tool is the
        exact half, on 3.13, which CI runs on all three platforms.
        """
        average_tool = self.FULL_MEASURED / EXPECTED_FULL
        assert average_tool / self.FULL_MEASURED < self.TOLERANCE
        assert 2 * average_tool / self.FULL_MEASURED > self.TOLERANCE

    def test_the_changelog_entry_carries_the_measurement(self):
        entry = self._current_entry()
        assert f"full = {self.FULL_CHARS} characters" in entry
        assert f"core = {self.CORE_CHARS}" in entry
        assert f"Python 3.13.5 with mcp {self.REFERENCE_MCP}" in entry
        # The environment, by path: this repository holds two, at
        # different interpreters, and a version alone does not say which
        # was used.
        assert "repository's own `venv/`" in entry
        assert "Python 3.11.13" in entry and "`.venv/`" in entry

    def test_each_entry_carries_one_measurement_of_its_own(self):
        """The 0.12 entry used to state two, a batch apart, both in the
        present tense; a reader sizing a context window met whichever
        they read first. The caveat that separates them is pinned, and
        so is the rule that the entry being written states exactly one
        figure: its own."""
        assert self._current_entry().count("Serialised tool") == 1
        history = self._v012_entries()
        assert history.count("Serialised tool") == 2
        assert "at the Batch A point, which is where this section stops " \
               "and NOT the size of the release" in history
        # And the history was not quietly restated as today's figure.
        assert self.FULL_CHARS not in history

    def test_the_current_entry_carries_the_other_interpreter_too(self):
        """The loose half of the comparison has a documented target;
        until now no test read it, so the 3.10-to-3.12 figures could
        rot in the document while the pin moved."""
        entry = self._current_entry()
        assert f"{self.FULL_MEASURED_310:,}" in entry
        assert f"{self.CORE_MEASURED_310:,}" in entry

    def test_the_readme_quotes_the_same_measurement(self):
        readme = self._read("README.md")
        assert f"about {self.FULL_ROUNDED} characters for `full`" in readme
        assert f"about {self.CORE_ROUNDED} characters for" in readme
        assert f"roughly {self.FULL_TOKENS} tokens" in readme
        assert f"roughly {self.CORE_TOKENS} tokens" in readme

    def test_install_quotes_the_same_measurement(self):
        install = self._read("INSTALL.md")
        assert f"about {self.FULL_ROUNDED} characters" in install
        assert f"roughly {self.FULL_TOKENS} tokens" in install
        assert f"about {self.CORE_ROUNDED} characters, roughly " \
               f"{self.CORE_TOKENS} tokens" in install

    # `pseudonymise_source`'s own share of the full figure, which
    # INSTALL.md states rounded to the nearest 500 (v0.13: the one-file
    # signature and the file-text count moved it from about 10,500 to
    # about 11,500 characters without anything saying so, and Brief 1's
    # fix round to about 12,500).
    FLAGSHIP_ROUNDED = "17,500"

    def test_install_quotes_the_flagships_own_share(self):
        install = self._read("INSTALL.md")
        assert (f"accounts for about {self.FLAGSHIP_ROUNDED} characters of "
                f"that on its own") in install
        if not self._reference_environment():
            return
        tools = asyncio.run(server.mcp.list_tools())
        flagship = next(t for t in tools if t.name == "pseudonymise_source")
        share = len(json.dumps({"name": flagship.name,
                                "description": flagship.description or "",
                                "inputSchema": flagship.inputSchema}))
        assert round(share / 500) * 500 == int(
            self.FLAGSHIP_ROUNDED.replace(",", "")), share

    def test_install_advises_a_context_the_core_schema_fits_in(self):
        """Step 4's advice is arithmetic, not a number to swap: at a 14k
        schema the old 16k floor leaves about 2k for the transcript."""
        install = self._read("INSTALL.md")
        assert "at least 32k for the core toolset" in install
        assert "16k would leave barely 2k and is not workable" in install

    def test_the_growth_is_arithmetically_possible(self):
        """The defect that gave this away: a full delta smaller than the
        core delta plus the tools outside core."""
        entry = self._v012_entries()
        # Anchored on the stable prefix: the entry names WHICH batch
        # the figure follows, and that wording moves with each one.
        block = entry[entry.index("Serialised tool JSON after"):]
        numbers = [int(n.replace(",", "")) for n in
                   re.findall(r"\d{2,3},\d{3}", block)]
        full_after, core_after, full_before, core_before = numbers[:4]
        assert full_after > core_after
        assert full_before > core_before
        assert full_after - full_before > core_after - core_before


class TestTheDeclaredMcpFloorSupportsCoreMode:
    """The floor has to support the mode the same package documents.

    Pre-existing rather than a Batch B defect: `mcp.remove_tool` arrives
    with the core toolset in 0.10.0-alpha and the floor has read
    `mcp>=1.2.0` since. `FastMCP.remove_tool` does not exist before mcp
    1.17.0, so a user installing at or near the declared floor and
    setting QUALCODER_MCP_TOOLSET=core got
    `AttributeError: 'FastMCP' object has no attribute 'remove_tool'`
    rather than a 21-tool server.

    Established by testing, not by reading release notes: throwaway venvs
    on Python 3.10.16 with this tree on PYTHONPATH, calling
    `_apply_toolset("core")` exactly as the startup path does. 1.2.0
    fails, 1.16.0 fails, 1.17.0 registers 21 tools. The floor is 1.17.0
    for that reason and this pin is what keeps the two together.
    """

    FIRST_MCP_WITH_REMOVE_TOOL = (1, 17, 0)

    @staticmethod
    def _declared_floor():
        """The lower bound of the mcp requirement, from pyproject."""
        text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        found = re.search(r'"mcp>=(\d+)\.(\d+)\.(\d+),<2"', text)
        assert found, "the mcp requirement is not in the shape this reads"
        return tuple(int(part) for part in found.groups())

    def test_the_declared_floor_is_not_below_the_measured_one(self):
        assert self._declared_floor() >= self.FIRST_MCP_WITH_REMOVE_TOOL, (
            "the declared mcp floor does not support "
            "QUALCODER_MCP_TOOLSET=core, which calls FastMCP.remove_tool "
            f"(first present in mcp "
            f"{'.'.join(str(n) for n in self.FIRST_MCP_WITH_REMOVE_TOOL)})")

    def test_the_call_core_mode_makes_exists_here(self):
        """The other half: the floor is only right while this is the call
        the gate makes. If `_apply_toolset` stops using the public
        helper, this pin says so and the floor can be revisited."""
        assert hasattr(server.mcp, "remove_tool")
        source = (REPO / "src" / "qualcoder_mcp" / "server.py").read_text(
            encoding="utf-8")
        assert "mcp.remove_tool(name)" in source

    def test_the_changelog_records_the_raised_floor(self):
        entry = " ".join((REPO / "CHANGELOG.md").read_text(
            encoding="utf-8").split("## [0.11")[0].split())
        assert "`mcp>=1.17.0,<2`" in entry

    def test_the_checked_in_lock_records_the_same_requirement(self):
        """The lock file kept `>=1.2.0` in its requires-dist after the
        floor was raised, so the one artefact a reader consults to learn
        what this package requires disagreed with the package (fix round
        4). Regenerating the whole lock is a separate decision: it would
        add the dev extra's build and twine trees, which no gate has
        reviewed."""
        text = (REPO / "uv.lock").read_text(encoding="utf-8")
        recorded = re.findall(r'\{ name = "mcp", specifier = "([^"]+)" \}',
                              text)
        assert recorded == [">=1.17.0,<2"], recorded
