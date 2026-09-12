"""v0.12 Batch A, item A4: the deprecated `session_id` duplicate is gone.

Announced in 0.10.1 and 0.11.0 as "a later release": every session-tool
response now carries `coding_session_id` only, the on-disk session format
is unchanged, and no registered tool has an argument named `session_id`
(or another routing-flavoured name some MCP middleware reserves).
"""

import json
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

TOOL_COUNT = 67          # pinned in tests/test_v012_cli.py too

UNKNOWN_SESSION_ID = "00000000-0000-4000-8000-000000000000"


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


def _probe_arguments(tool, tmp_path):
    """Synthesise a call for `tool` from its own input schema.

    Ids are deliberately out of range and paths point outside any real
    project, so the sweep exercises refusals and error envelopes (where
    the bug lived) rather than mutating anything that matters. Every
    argument named coding_session_id is supplied, required or not, so the
    session-not-found envelopes are actually produced.
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
            arguments[name] = str(existing_dir)
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
        self, setup_server, tmp_path
    ):
        tools = server.mcp._tool_manager._tools
        assert len(tools) == TOOL_COUNT

        # A real session has to exist, or the not-found envelopes would
        # list nothing and the sweep would pass without seeing the shape
        # that carried the bug.
        json.loads(server.analyze_for_coding([1]))["coding_session_id"]

        json_responses = 0
        offenders = []
        for name, tool in sorted(tools.items()):
            try:
                response = tool.fn(**_probe_arguments(tool, tmp_path))
            except Exception:
                # A refusal raised rather than returned is not a response
                # shape, so there is nothing to scan.
                continue
            if not isinstance(response, str):
                continue
            try:
                payload = json.loads(response)
            except ValueError:
                continue          # Markdown tools answer in prose
            json_responses += 1
            for where in _forbidden_key_paths(payload):
                offenders.append(f"{name}: {where}")

        assert offenders == []
        # Guard against a sweep that scanned nothing useful.
        assert json_responses >= 40, json_responses

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
