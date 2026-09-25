# SPDX-License-Identifier: LGPL-3.0-or-later
"""v0.12 Batch A, item A4: the deprecated `session_id` duplicate is gone.

Announced in 0.10.1 and 0.11.0 as "a later release": every session-tool
response now carries `coding_session_id` only, the on-disk session format
is unchanged, and no registered tool has an argument named `session_id`
(or another routing-flavoured name some MCP middleware reserves).
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server

RESERVED_ARGUMENT_NAMES = {"session_id", "request_id", "conversation_id",
                           "user_id", "context", "metadata"}


# ===========================================================================
# A4: session_id duplicate removed
# ===========================================================================

class TestSessionIdDuplicateRemoved:

    def test_no_tool_argument_uses_a_reserved_routing_name(self):
        # Self-checking: without this the guard would pass on an emptied
        # or half-registered registry.
        assert len(server.mcp._tool_manager._tools) == TOOL_COUNT
        for name, tool in server.mcp._tool_manager._tools.items():
            props = set((tool.parameters or {}).get("properties", {}))
            assert not (props & RESERVED_ARGUMENT_NAMES), (name, props)

    def test_session_tool_responses_carry_only_coding_session_id(self, setup_server):
        env = json.loads(server.analyze_for_coding([1]))
        sid = env["coding_session_id"]
        assert "session_id" not in env

        rec = json.loads(server.record_suggestions(sid, [{
            "file_id": 1, "code_name": "Stress",
            "segment_text": "stressed about deadlines",   # 31-55, widened below
            "reasoning": "explicit", "confidence": 0.9}]))
        assert rec["coding_session_id"] == sid and "session_id" not in rec
        guid = rec["recorded"][0]["guid"]

        edit = json.loads(server.edit_suggestion(sid, guid, start_pos=24, end_pos=55))
        assert edit["coding_session_id"] == sid and "session_id" not in edit

        prop = json.loads(server.propose_codes(sid, [{"name": "Fresh"}]))
        assert prop["coding_session_id"] == sid and "session_id" not in prop

        info = json.loads(server.get_coding_session_info(sid))
        assert info["coding_session_id"] == sid and "session_id" not in info
        assert len(info["suggestions"]) == 1

        listing = json.loads(server.list_coding_sessions())
        assert listing["session_count"] == 1
        entry = listing["sessions"][0]
        assert entry["coding_session_id"] == sid and "session_id" not in entry

        deleted = json.loads(server.delete_coding_session(sid))
        assert deleted["coding_session_id"] == sid and "session_id" not in deleted

    def test_on_disk_session_format_is_unchanged(self, setup_server, tmp_path):
        env = json.loads(server.analyze_for_coding([1]))
        sid = env["coding_session_id"]
        path = Path(server.session_manager.storage_dir) / f"session_{sid}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["session_id"] == sid          # internal schema keeps its key
        assert "coding_session_id" not in data


# ===========================================================================
# A4 fix round 1 (F1): nothing anywhere in a JSON response may carry
# `session_id`, including the error envelopes, and the scan is driven from
# the live registry so a tool added later is covered without an edit here.
# ===========================================================================

TOOL_COUNT = 73          # pinned in tests/test_v012_cli.py too

UNKNOWN_SESSION_ID = "00000000-0000-4000-8000-000000000000"

# The tools that answer an unknown coding_session_id with the
# {"error": "Session X not found", "available_sessions": [...]} envelope.
# That envelope is the shape F1's bug lived in, so the sweep asserts it
# reaches all five and not merely "some JSON" (fix round 2, R1).
SESSION_ENVELOPE_TOOLS = {
    "edit_suggestion",
    "export_refi_qda",
    "get_coding_session_info",
    "propose_codes",
    "record_suggestions",
}

# A required *_path argument is pointed at a directory under tmp_path,
# which is all most tools need. A tool that validates the suffix BEFORE
# looking the session up answers with that refusal instead, and drops out
# of the envelope coverage: export_refi_qda did exactly that, silently,
# until this table was added (fix round 2, R1).
PROBE_PATH_SUFFIXES = {"export_refi_qda": ".qdpx"}


def _forbidden_key_paths(value, path="$"):
    """Every JSON path under `value` whose key is the forbidden name."""
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            here = f"{path}.{key}"
            if key == "session_id":
                found.append(here)
            found.extend(_forbidden_key_paths(item, here))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_forbidden_key_paths(item, f"{path}[{index}]"))
    return found


def _available_session_lists(value):
    """Every non-empty `available_sessions` list under `value`."""
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "available_sessions" and isinstance(item, list) and item:
                found.append(item)
            found.extend(_available_session_lists(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_available_session_lists(item))
    return found


def _package_modules():
    """Every module of the package sys.modules already holds, by DOTTED name.

    Keyed by the dotted name rather than the leaf (carried from Batch A):
    two modules whose dotted names share a leaf collided, and because
    sys.modules is walked in sorted order the deeper one won, evicting
    the shallower one from the sweep while the loud
    `modules.get("server") is not None` assertion still passed, since the
    key existed and merely pointed at the wrong module. v0.12 added four
    modules to this package, so the collision was no longer hypothetical
    enough to leave.
    """
    return {name: module
            for name, module in sorted(sys.modules.items())
            if name == "qualcoder_mcp" or name.startswith("qualcoder_mcp.")}


def _home_with_nested_sandbox(tmp_path):
    r"""A home directory with the test's sandbox nested inside it.

    Mirrors the CI runner layout: home C:\Users\runneradmin, sandbox
    C:\Users\runneradmin\AppData\Local\Temp\pytest-of-runner.
    """
    home = tmp_path / "home"
    sandbox = home / "AppData" / "Local" / "Temp" / "pytest-of-runner"
    sandbox.mkdir(parents=True)
    return home, sandbox


def _module_paths_under(root, sandbox):
    """Module-level paths in the package that still lie inside `root`.

    The sweep calls every registered tool for real with production
    defaults, so a path constant frozen at import time is the sweep's
    blast radius. `root` is the real home directory; paths inside the
    checkout are skipped, because constants derived from `__file__`
    legitimately live there and the checkout is itself under the home
    directory on a developer machine and on the Linux CI images.

    `sandbox` is the directory the caller has already redirected
    everything into, normally `tmp_path`, and paths inside it are skipped
    BEFORE the `root` question is asked. That ordering is the whole point
    (fix round 4, W1). pytest's temporary root is not always outside the
    home directory: on GitHub's windows-latest runners the job's `%TEMP%`
    is `C:\\Users\\runneradmin\\AppData\\Local\\Temp`, so `tmp_path`
    is itself inside `Path.home()` and every redirected constant satisfies
    both tests at once. Without the skip the guard reported the
    redirection it had just been handed and failed for doing its job,
    which is what broke both windows-latest jobs at 1fc70a1 while the four
    Linux and macOS jobs passed. It is a TMPDIR property rather than a
    Windows one: `pytest --basetemp="$HOME/somewhere"` reproduces it on
    any platform, and that is how it was found.

    The skip costs the guard nothing it was built for. A constant still
    pointing at the real `~/.qualcoder_mcp` or `~/Documents` is not inside
    the sandbox, so it is still reported, and the two pins in
    TestTheRotGuardSurvivesATempRootInsideHome hold both halves down.
    All three directories are resolved before comparing, so a short 8.3
    `%TEMP%`, a `/tmp` symlinked to `/private/tmp`, or a `..` inside a
    constant cannot make the comparison answer by accident.

    What is walked (fix round 4, T9). Every module of the package that
    sys.modules already holds, not a hand-written list of three: the
    round-3 version named server, database and sessions, so a constant
    added to memo_privacy was invisible to a guard whose own comment
    said "nothing else". Values are taken as Path OR as an absolute
    string, because `str(Path.home() / ".qualcoder_mcp")` is as easy to
    write as the Path and rotted the pin just as silently. A module NOT
    yet imported is deliberately out of scope: refi_export is imported
    inside export_refi_qda, long after the caller's setenv has moved
    Path.home(), so the setenv is its guard and importing it here would
    only hide that. In a FULL-suite run refi_export is in fact already in
    sys.modules, because tests/test_refi_export.py imports it at module
    scope and collects earlier, so the walk does cover it there; the rule
    above is what holds when this file runs alone (carried from Batch A:
    the example did not survive measurement). The six modules asserted
    present below make an import change that shrinks the walk fail loudly
    rather than quietly.
    """
    repo_root = Path(server.__file__).resolve().parent.parent.parent
    sandbox = Path(sandbox).expanduser().resolve()
    root = Path(root).expanduser().resolve()
    found = []
    modules = _package_modules()
    for label in ("qualcoder_mcp.server", "qualcoder_mcp.database",
                  "qualcoder_mcp.sessions", "qualcoder_mcp.project_settings",
                  "qualcoder_mcp.preview_tokens", "qualcoder_mcp.cursors"):
        assert modules.get(label) is not None, (
            f"{label} is not imported, so this sweep is walking less of "
            f"the package than it claims to")
    candidates = [(f"{label}.{name}", value)
                  for label, module in modules.items()
                  for name, value in sorted(vars(module).items())
                  if not name.startswith("__")
                  and (isinstance(value, Path)
                       or (isinstance(value, str) and os.path.isabs(value)))]
    candidates.append(("server.session_manager.storage_dir",
                       Path(server.session_manager.storage_dir)))
    for where, value in candidates:
        resolved = Path(value).expanduser()
        if not resolved.is_absolute():
            continue
        resolved = resolved.resolve()
        if resolved.is_relative_to(sandbox):
            continue
        if resolved.is_relative_to(repo_root):
            continue
        if resolved.is_relative_to(root):
            found.append(f"{where} = {resolved}")
    return found


def _probe_arguments(tool, tmp_path):
    """Synthesise a call for `tool` from its own input schema.

    Ids are deliberately out of range and paths point outside any real
    project, so the sweep exercises refusals and error envelopes (where
    the bug lived) rather than mutating anything that matters. Every
    argument named coding_session_id is supplied, required or not, so the
    session-not-found envelopes are actually produced, and the sweep
    asserts that all five of them were reached rather than trusting it.
    """
    schema = tool.parameters or {}
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    existing_dir = tmp_path / "probe_out"
    existing_dir.mkdir(exist_ok=True)

    arguments = {}
    for name, spec in properties.items():
        if name == "coding_session_id":
            arguments[name] = UNKNOWN_SESSION_ID
            continue
        if name == "search_directories":
            # Default would walk the real home directory.
            arguments[name] = [str(tmp_path)]
            continue
        if name not in required:
            continue
        if name.endswith("_path") or name == "filename":
            suffix = PROBE_PATH_SUFFIXES.get(getattr(tool, "name", None))
            arguments[name] = (str(existing_dir / f"probe{suffix}")
                               if suffix else str(existing_dir))
            continue
        json_type = spec.get("type") or "string"
        if json_type == "integer":
            arguments[name] = 999999
        elif json_type == "number":
            arguments[name] = 0.5
        elif json_type == "boolean":
            arguments[name] = False
        elif json_type == "array":
            arguments[name] = []
        elif json_type == "object":
            arguments[name] = {}
        else:
            arguments[name] = "qc-mcp probe value"
    return arguments


class TestNoResponseCarriesSessionId:

    def test_registry_size_is_pinned(self):
        # Without this the sweeps below, and the reserved-argument guard
        # above, would pass vacuously on an emptied registry.
        assert len(server.mcp._tool_manager._tools) == TOOL_COUNT

    def test_no_tool_response_anywhere_carries_session_id(
        self, setup_server, tmp_path, monkeypatch
    ):
        # Every tool is called for real with its required arguments only,
        # so every OPTIONAL argument takes its production default. Pin the
        # OBJECTS, not just the environment (fix round 3, S5). Two
        # home-derived paths in the server are computed once, at IMPORT:
        # server._MRU_FILE and the SessionManager built at server module
        # scope. This test body runs long after the import at the top of
        # this file, so the setenv below moves Path.home() for code that
        # resolves it at CALL time (discover_projects, and since the 0.12
        # release preparation database.default_workspace(), which the
        # sweep reaches through copy_project_to_workspace because its
        # `workspace` is not a tool argument) and for nothing else. Keep
        # it for that class and redirect the frozen constants as well:
        # cleanup_old_sessions takes its production default (days_old=30)
        # and unlinks session files (fix round 2, R2, which pinned the
        # environment alone and therefore pinned nothing).
        real_home = Path.home()
        home = tmp_path / "probe_home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))          # call-time Path.home()
        monkeypatch.setenv("USERPROFILE", str(home))   # ... and on Windows

        from qualcoder_mcp import database
        monkeypatch.setattr(server, "_MRU_FILE",
                            home / ".qualcoder_mcp" / "mru_project.json")

        # A guard is only a guard if it moved what it claims to move.
        # setup_server already points session_manager at tmp_path; assert
        # that rather than relying on the fixture staying as it is,
        # because cleanup_old_sessions is called below and deletes session
        # files wherever the manager is pointing.
        assert Path(database.default_workspace()).is_relative_to(tmp_path)
        assert Path(server._MRU_FILE).is_relative_to(tmp_path)
        assert Path(server.session_manager.storage_dir).is_relative_to(tmp_path)
        # And no module-level constant of any imported module of the
        # package still points into the real home, whether it is typed as
        # a Path or as an absolute string: a constant added later would
        # rot this pin in silence, which is exactly how the
        # environment-only version of it came to protect nothing (fix
        # round 4, T9 widened the walk from three hand-listed modules and
        # Path alone). A Path held INSIDE a module-level list, tuple, set
        # or dict is still invisible to the walk; the sentence says
        # "constant" rather than "anything" for that reason (carried from
        # Batch A: the shipped comment claimed more than the code covers).
        # tmp_path is handed over as the sandbox because on a machine
        # whose temporary root lives under the home directory (every
        # Windows CI runner) the redirections above are inside BOTH, and
        # the question is what was NOT redirected (fix round 4, W1).
        assert _module_paths_under(real_home, tmp_path) == []

        tools = server.mcp._tool_manager._tools
        assert len(tools) == TOOL_COUNT

        # A real session has to exist, or the not-found envelopes would
        # list nothing and the sweep would pass without seeing the shape
        # that carried the bug.
        json.loads(server.analyze_for_coding([1]))["coding_session_id"]

        scanned, envelopes, offenders = set(), set(), []
        excluded = {}
        for name, tool in sorted(tools.items()):
            try:
                response = tool.fn(**_probe_arguments(tool, tmp_path))
            except Exception as exc:
                excluded[name] = f"raised {type(exc).__name__}: {exc}"
                continue
            if not isinstance(response, str):
                excluded[name] = f"answered {type(response).__name__}"
                continue
            try:
                payload = json.loads(response)
            except ValueError:
                excluded[name] = f"not JSON: {response[:60]!r}"
                continue
            scanned.add(name)
            if _available_session_lists(payload):
                envelopes.add(name)
            for where in _forbidden_key_paths(payload):
                offenders.append(f"{name}: {where}")

        assert offenders == []
        # Anti-vacuity (fix round 2, R8). Today every registered tool
        # answers these arguments with a parseable JSON string, so the
        # sweep covers 69 of 69; the old guard, `json_responses >= 40`,
        # would have stayed green while 27 tools, including all five
        # envelopes, dropped out of it. Asserting the SET makes a tool
        # that starts raising, or starts answering in prose, something to
        # look at rather than a silent hole.
        assert scanned == set(tools), excluded
        # And the shape that carried the bug is actually reached (R1). A
        # new required argument validated ahead of the session lookup
        # would otherwise remove the envelope from the sweep while it kept
        # passing, which is how export_refi_qda was missing from it.
        assert envelopes == SESSION_ENVELOPE_TOOLS

    def test_session_not_found_envelopes_use_the_api_key(
        self, setup_server, tmp_path
    ):
        """The five envelopes of F1, each one run for real."""
        real = json.loads(server.analyze_for_coding([1]))["coding_session_id"]

        export_dir = tmp_path / "refi_out"
        export_dir.mkdir()
        envelopes = {
            "record_suggestions": server.record_suggestions(
                UNKNOWN_SESSION_ID, [{"file_id": 1, "code_name": "Stress",
                                      "segment_text": "stressed",
                                      "reasoning": "x", "confidence": 0.9}]),
            "edit_suggestion": server.edit_suggestion(
                UNKNOWN_SESSION_ID, "no-such-guid"),
            "get_coding_session_info": server.get_coding_session_info(
                UNKNOWN_SESSION_ID),
            "propose_codes": server.propose_codes(
                UNKNOWN_SESSION_ID, [{"name": "Fresh"}]),
            "export_refi_qda": server.export_refi_qda(
                str(export_dir / "probe.qdpx"),
                coding_session_id=UNKNOWN_SESSION_ID),
        }

        # The sweep above asserts it reaches exactly these five; keep the
        # two lists in step, or one of them can quietly shrink.
        assert set(envelopes) == SESSION_ENVELOPE_TOOLS

        for name, raw in envelopes.items():
            payload = json.loads(raw)
            assert payload["error"] == f"Session {UNKNOWN_SESSION_ID} not found", name
            listed = payload["available_sessions"]
            assert [entry["coding_session_id"] for entry in listed] == [real], name
            assert _forbidden_key_paths(payload) == [], name

    def test_list_coding_sessions_needs_no_rename_of_its_own(
        self, setup_server, tmp_path
    ):
        """The manager hands the API key over; the tool just serialises it."""
        real = json.loads(server.analyze_for_coding([1]))["coding_session_id"]
        entries = server.session_manager.list_sessions()
        assert [entry["coding_session_id"] for entry in entries] == [real]
        assert all("session_id" not in entry for entry in entries)

        listing = json.loads(server.list_coding_sessions())
        assert listing["sessions"][0]["coding_session_id"] == real
        assert _forbidden_key_paths(listing) == []


# ===========================================================================
# Fix round 4 (W1): the rot guard above has to survive a temporary root that
# lives INSIDE the home directory, which is the layout of every Windows CI
# runner (%TEMP% sits under C:\Users\<user>) and of any machine with TMPDIR
# set below $HOME. At 1fc70a1 it did not: both windows-latest jobs failed on
# it, 3.10 and 3.13 alike, while all four Linux and macOS jobs passed. These
# two pins force that layout on any platform, so the regression cannot come
# back unnoticed on a platform this suite is not routinely run on.
# ===========================================================================


class TestTheRotGuardSurvivesATempRootInsideHome:

    def test_constants_redirected_into_the_sandbox_are_not_reported(
        self, tmp_path, monkeypatch
    ):
        """The Windows failure itself: redirected, inside the home, silent."""
        home, sandbox = _home_with_nested_sandbox(tmp_path)
        monkeypatch.setattr(server, "_MRU_FILE",
                            sandbox / ".qualcoder_mcp" / "mru_project.json")
        monkeypatch.setattr(server.session_manager, "storage_dir",
                            sandbox / "sessions")
        assert _module_paths_under(home, sandbox) == []

    def test_a_constant_left_in_the_home_is_still_reported(
        self, tmp_path, monkeypatch
    ):
        """And the guard still guards: one unredirected constant, named.

        Without this the fix above could have been `return []`.
        """
        home, sandbox = _home_with_nested_sandbox(tmp_path)
        monkeypatch.setattr(server.session_manager, "storage_dir",
                            sandbox / "sessions")
        # The one a careless redirection would leave behind.
        monkeypatch.setattr(server, "_MRU_FILE",
                            home / ".qualcoder_mcp" / "mru_project.json")
        reported = _module_paths_under(home, sandbox)
        assert len(reported) == 1, reported
        assert reported[0].startswith("qualcoder_mcp.server._MRU_FILE = "), \
            reported


class TestTheRotGuardWalksThePackageItClaims:
    """Fix round 4, T9: the guard's prose said the package, its code said
    three hand-listed modules and the Path type.

    qualcoder_mcp also ships memo_privacy and refi_export, and a constant
    written as `str(Path.home() / "...")` is as easy to write as the Path
    version. Both were invisible to a guard whose whole purpose is that
    it cannot rot in silence, and whose own comment read "and nothing
    else still points into the real home". Today's tree is clean, so this
    was never a live exposure; it was a guard that would not have seen
    the two most natural ways of creating one.
    """

    @staticmethod
    def _redirect_the_known_constants(monkeypatch, sandbox):
        # Two since the 0.12 release preparation: the workspace default
        # is resolved at call time and is no longer a constant to move.
        monkeypatch.setattr(server, "_MRU_FILE", sandbox / "mru_project.json")
        monkeypatch.setattr(server.session_manager, "storage_dir",
                            sandbox / "sessions")

    def test_the_walk_covers_every_imported_module_of_the_package(self):
        """Non-vacuity for the walk itself, not only for what it found."""
        walked = set(_package_modules())
        assert {"qualcoder_mcp", "qualcoder_mcp.server",
                "qualcoder_mcp.database", "qualcoder_mcp.sessions",
                "qualcoder_mcp.memo_privacy",
                "qualcoder_mcp.project_settings",
                "qualcoder_mcp.preview_tokens",
                "qualcoder_mcp.cursors",
                "qualcoder_mcp.coder_comparison"} <= walked, walked

    def test_a_path_in_a_module_the_old_walk_missed_is_reported(
        self, tmp_path, monkeypatch
    ):
        from qualcoder_mcp import memo_privacy

        home, sandbox = _home_with_nested_sandbox(tmp_path)
        self._redirect_the_known_constants(monkeypatch, sandbox)
        monkeypatch.setattr(memo_privacy, "_PLANTED_CACHE",
                            home / ".qualcoder_mcp" / "memo_cache",
                            raising=False)
        reported = _module_paths_under(home, sandbox)
        assert len(reported) == 1, reported
        assert reported[0].startswith(
            "qualcoder_mcp.memo_privacy._PLANTED_CACHE = "), reported

    def test_an_absolute_string_constant_is_reported_too(
        self, tmp_path, monkeypatch
    ):
        home, sandbox = _home_with_nested_sandbox(tmp_path)
        self._redirect_the_known_constants(monkeypatch, sandbox)
        monkeypatch.setattr(server, "_PLANTED_DIR",
                            str(home / ".qualcoder_mcp" / "cache"),
                            raising=False)
        reported = _module_paths_under(home, sandbox)
        assert len(reported) == 1, reported
        assert reported[0].startswith(
            "qualcoder_mcp.server._PLANTED_DIR = "), reported

    def test_a_relative_string_is_not_mistaken_for_a_path(
        self, tmp_path, monkeypatch
    ):
        """Most module-level strings are not paths at all, and none of
        them may make this guard shout: QUALCODER_LOCK_FILENAME is one."""
        home, sandbox = _home_with_nested_sandbox(tmp_path)
        self._redirect_the_known_constants(monkeypatch, sandbox)
        monkeypatch.setattr(server, "_PLANTED_NAME", ".qualcoder.lock",
                            raising=False)
        assert _module_paths_under(home, sandbox) == []
