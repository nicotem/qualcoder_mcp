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
