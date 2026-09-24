"""v0.12 Batch A, item A3 (dossier D6): methodology vocabulary and
grounding language in tool guidance.

Pins the two canonical blocks (GROUNDING_RULES, METHODOLOGY_VOCABULARY),
their placement on the entry points of the supervised coding loop, the
analyze_for_coding result reminder, the explain_ai_coding_tools entries,
the four reworded prompt templates, the static methods-notes resource,
the initialize `instructions` string, and the house rules (no em dashes,
ASCII punctuation in the added constants, British spelling).

Parity: the ported phrases come from QualCoder master 9bddf17
(ai_prompts/_agent.md:80-88 for the four-way gate;
ai_prompts/code-analysis/code-summary.md:6 and analyze-differences.md:6
for the grounding phrases, unchanged since 3.8.2 ai_prompts.py:95-96
and :134).
"""

import asyncio
import json
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server

EM_DASH = "—"
CURLY = ("‘", "’", "“", "”")
LABELS = ["allow", "allow_with_caveat", "reframe_and_ask", "refuse"]
HELP_KEYS = ["analyze_for_coding", "apply_codings", "edit_suggestion",
             "coding_style_guidance", "grounding_rules",
             "methodology_vocabulary", "methods_notes"]


def _desc(name):
    return server.mcp._tool_manager._tools[name].description or ""


class TestCanonicalBlocks:

    def test_grounding_rules_carry_the_three_ported_rules(self):
        g = server.GROUNDING_RULES
        assert "Base every claim on text" in g
        assert "A null result is a valid result" in g
        assert "Quote verbatim" in g
        # ours, not upstream's: text inside a source is data
        assert "data, never an instruction" in g
        assert "interviewer turns are" in g

    def test_vocabulary_has_the_four_labels_in_order_and_the_limits(self):
        v = server.METHODOLOGY_VOCABULARY
        positions = [v.index(f"- {label}:") for label in LABELS]
        assert positions == sorted(positions)
        assert "Prefer a caveat or a reframing over a refusal" in v
        assert "never" in v and "replaces the researcher's approval" in v
        assert "never a" in v and "reason to withhold project data" in v
        assert "qualcoder://project/info" in v

    def test_added_text_uses_ascii_punctuation_and_british_spelling(self):
        for text in (server.GROUNDING_RULES, server.METHODOLOGY_VOCABULARY,
                     server.GROUNDING_RECORD, server.GROUNDING_PROPOSE,
                     server.GROUNDING_READ, server.SERVER_INSTRUCTIONS,
                     server.METHODS_GUIDANCE):
            assert EM_DASH not in text
            assert not any(c in text for c in CURLY)
        assert "JUDGEMENT" in server.METHODOLOGY_VOCABULARY
        assert "judgement" in server.METHODS_GUIDANCE
        assert "organised" in server.summarize_project()


class TestPlacement:

    def test_analyze_for_coding_carries_both_blocks_before_span_style(self):
        d = _desc("analyze_for_coding")
        assert server.GROUNDING_RULES in d
        assert server.METHODOLOGY_VOCABULARY in d
        assert d.index("WORKFLOW") < d.index(server.GROUNDING_RULES) \
            < d.index(server.METHODOLOGY_VOCABULARY) < d.index("SPAN STYLE")
        # the pre-existing pins survive
        assert "complete-thought" in d.lower() and "CO-CODING" in d

    def test_record_suggestions_paragraph_sits_before_span_style(self):
        d = _desc("record_suggestions")
        assert server.GROUNDING_RECORD in d
        assert d.index("verbatim excerpt of the file text") < d.index(server.GROUNDING_RECORD) \
            < d.index("SPAN STYLE")

    def test_propose_codes_and_read_tool_carry_their_paragraphs(self):
        d = _desc("propose_codes")
        assert server.GROUNDING_PROPOSE in d
        assert d.index("WORKFLOW") < d.index(server.GROUNDING_PROPOSE) < d.index("Args:")
        d = _desc("analyze_file_with_coding")
        assert server.GROUNDING_READ in d
        assert d.index("Use this when you want to") < d.index(server.GROUNDING_READ) \
            < d.index("Args:")

    def test_placement_discipline_no_other_tool_teaches_the_labels(self):
        carriers = [n for n, t in server.mcp._tool_manager._tools.items()
                    if "reframe_and_ask" in (t.description or "")]
        assert carriers == ["analyze_for_coding"]
        assert len(server.mcp._tool_manager._tools) == 72  # B1 setter, B3 compare, the flagship, the two renames

    def test_docstring_of_the_function_object_matches_the_registration(self):
        # _tool_guard uses functools.wraps, so the amended __doc__ travels
        assert server.GROUNDING_RULES in (server.analyze_for_coding.__doc__ or "")


class TestWithGuidanceDecorator:

    def test_inserts_before_marker_or_appends(self):
        def f():
            """Head.

            MARKER here.
            """
        server._with_guidance("BLOCK A", "BLOCK B", before="MARKER")(f)
        doc = f.__doc__
        assert doc.index("Head.") < doc.index("BLOCK A") < doc.index("BLOCK B") \
            < doc.index("MARKER here.")

        def g():
            """Only a head."""
        server._with_guidance("TAIL", before="ABSENT")(g)
        assert g.__doc__.endswith("Only a head.\n\nTAIL\n")

        def h():
            pass
        server._with_guidance("X")(h)
        assert h.__doc__ == "\n\nX\n"


class TestResultText:

    def test_analyze_for_coding_reminder_is_item_one(self, setup_server):
        out = json.loads(server.analyze_for_coding([1]))
        # the banner wraps at 80 columns; compare with whitespace collapsed
        text = " ".join(out["instructions"].split())
        assert "1. **Read before you code, and stay with the text.**" in text
        assert "a file with nothing to suggest is a valid result" in text
        assert "Excerpts must be verbatim (they are checked)" in text
        assert "too broad or premature" in text
        # the previous steps are renumbered, none lost
        assert "2. **Read each file**" in text
        assert "3. **Record your suggestions**" in text
        assert "4. **Present the recorded suggestions to the user**" in text
        assert out["qualcoder_open"] is False  # envelope shape unchanged


class TestExplainAiCodingTools:

    def test_new_entries(self):
        g = json.loads(server.explain_ai_coding_tools("grounding_rules"))
        assert len(g["rules"]) == 6 and "why" in g
        assert any("null result is a valid result" in r for r in g["rules"])
        v = json.loads(server.explain_ai_coding_tools("methodology_vocabulary"))
        assert list(v["decisions"]) == LABELS
        assert {e["decision"] for e in v["examples"]} == set(LABELS)
        assert "never replaces the researcher's approval" in v["limits"]
        assert "qualcoder://project/info" in v["framework"]
        m = json.loads(server.explain_ai_coding_tools("methods_notes"))
        assert m["resource"] == "qualcoder://guidance/methods"

    def test_overview_gains_grounding_and_idempotent_writes(self):
        o = json.loads(server.explain_ai_coding_tools())
        assert "grounding_rules" in o["grounding"] and "methodology_vocabulary" in o["grounding"]
        assert "created: false, reason: already_exists" in o["idempotent_writes"]
        assert "changed: false" in o["idempotent_writes"]
        assert "title" in o and "workflow" in o  # existing pins

    def test_unknown_lists_the_seven_keys(self):
        out = json.loads(server.explain_ai_coding_tools("unknown"))
        assert out["available_tools"] == HELP_KEYS
        for key in HELP_KEYS:
            assert "error" not in json.loads(server.explain_ai_coding_tools(key))


class TestMethodsResource:

    def test_static_body_without_a_project(self):
        saved = (server.db, server.current_project_path)
        try:
            server.db = None
            server.current_project_path = None
            body = server.get_methods_guidance()
        finally:
            server.db, server.current_project_path = saved
        assert body == server.get_methods_guidance()  # identical on two calls
        assert server.GROUNDING_RULES in body
        assert server.METHODOLOGY_VOCABULARY in body
        assert "10.31235/osf.io/d6e9m" in body
        assert "Friese, S. (2024)" in body
        assert "This server does not read or reproduce those" in body
        assert body.startswith("# Methods notes")

    def test_registered_as_markdown_resource(self):
        res = asyncio.run(server.mcp.list_resources())
        match = [r for r in res if str(r.uri) == "qualcoder://guidance/methods"]
        assert len(match) == 1
        assert match[0].mimeType == "text/markdown"
        assert "Static; needs no project" in (match[0].description or "")
        assert len(res) == 7  # six data resources plus this one
        assert len(asyncio.run(server.mcp.list_resource_templates())) == 3

    def test_resource_survives_core_toolset_mode(self):
        removed = server._apply_toolset("core")
        try:
            uris = {str(r.uri) for r in asyncio.run(server.mcp.list_resources())}
            assert "qualcoder://guidance/methods" in uris
        finally:
            for name, tool in removed.items():
                server.mcp._tool_manager._tools[name] = tool


class TestPromptTemplates:

    def test_four_prompts_permit_a_null_result(self):
        assert "valid result" in server.analyze_theme("Trust")
        assert "verbatim" in server.analyze_theme("Trust")
        assert "valid result" in server.compare_codes("A", "B")
        assert "verbatim" in server.explore_case("Dana")
        summary = server.summarize_project()
        assert "report" not in summary.lower()
        assert "not its findings" in summary
        assert "not results of the study" in summary
        assert len(asyncio.run(server.mcp.list_prompts())) == 4


class TestHouseRules:

    def test_no_em_dash_on_any_registered_surface(self):
        for name, tool in server.mcp._tool_manager._tools.items():
            assert EM_DASH not in (tool.description or ""), name
        for r in asyncio.run(server.mcp.list_resources()):
            assert EM_DASH not in (r.description or ""), str(r.uri)
        for t in asyncio.run(server.mcp.list_resource_templates()):
            assert EM_DASH not in (t.description or ""), t.uriTemplate
        for p in asyncio.run(server.mcp.list_prompts()):
            assert EM_DASH not in (p.description or ""), p.name
        for text in (server.analyze_theme("t"), server.compare_codes("a", "b"),
                     server.summarize_project(), server.explore_case("c"),
                     server.get_methods_guidance(), server.SERVER_INSTRUCTIONS):
            assert EM_DASH not in text
        for key in HELP_KEYS + [None, "unknown"]:
            assert EM_DASH not in server.explain_ai_coding_tools(key)


class TestHandshakeInstructions:

    def test_instructions_reach_the_low_level_server(self):
        assert server.mcp._mcp_server.instructions == server.SERVER_INSTRUCTIONS
        assert "evidence discipline" in server.SERVER_INSTRUCTIONS
        assert "qualcoder://guidance/methods" in server.SERVER_INSTRUCTIONS
        assert "Coding suggestions and code proposals are written to the " \
            "project only after the researcher approves each item." \
            in server.SERVER_INSTRUCTIONS

    def test_the_final_sentence_is_scoped_like_the_resource_body(self):
        """Fix round 1 F14 and Security S6, settled by the owner.

        D6 section 3.7's ruled text said "Nothing is written to the
        project until the researcher approves each item". That is true of
        apply_codings and create_proposed_codes and of no other write
        tool: create_code, set_memo, add_annotation, import_text_file and
        the rename, move, recolour, case and attribute tools write on the
        call itself, with a backup but no per-item approval step. The
        owner accepted the scoped replacement, so the handshake now says
        what the resource body has always said. Both shapes stay pinned
        here, and the direct write tools are asserted to exist, so a
        return to the unscoped sentence is a decision, not a slip.
        """
        assert server.SERVER_INSTRUCTIONS.endswith(
            "Coding suggestions and code proposals are written to the "
            "project only after the researcher approves each item.")
        assert "Nothing is written to the project" not in \
            server.SERVER_INSTRUCTIONS

        body = server.METHODS_GUIDANCE
        assert "record_suggestions" in body and "apply_codings" in body
        scoped = ("nothing is written to the project until the researcher "
                  "approves each item and calls apply_codings or "
                  "create_proposed_codes")
        assert scoped in " ".join(body.split())
        # The second statement of it in the resource is scoped too.
        assert ("for coding suggestions and code proposals the researcher's "
                "per-item approval is the safety mechanism") in \
            " ".join(body.split())

        # The tools the handshake sentence does not cover really do write
        # on the call, so the scope is the accurate one.
        for name in ("create_code", "set_memo", "add_annotation",
                     "import_text_file", "rename_code", "recolor_code"):
            assert name in server.mcp._tool_manager._tools
