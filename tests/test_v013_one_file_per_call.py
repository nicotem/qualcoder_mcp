"""v0.13 Brief 1, item 1: one file per call (decision A).

`pseudonymise_source` rewrote every eligible text source, or the ones a
`file_ids` list named, under one mapping, so two people who share a name
could not be given two pseudonyms. It now takes one required `file_id`.
A file it cannot rewrite is refused with the reason the old
`skipped_files` list carried, the list is gone, and the report still
covers the whole project.

The fixture and the helpers are the flagship's own
(`test_v012_pseudonymise_tool.py`), imported rather than re-derived, so
this file drives exactly the project shape every other pin drives.
"""

import ast
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server
from qualcoder_mcp.database import QualcoderDatabase
from test_v012_pseudonymise_tool import (  # noqa: F401  (`project` is a fixture)
    MAPPING, TEXT, _house_rules, backups, call, execute_from, preview_of,
    project, query)

SERVER_SOURCE = Path(server.__file__).read_text(encoding="utf-8")


def _reconnect(folder):
    server.db.close()
    server.db = QualcoderDatabase(str(folder))


def _published_schema():
    """The input schema as the tool manager publishes it."""
    return server.mcp._tool_manager._tools["pseudonymise_source"].parameters


def _preview_body(answer):
    blocks = answer[0] if isinstance(answer, tuple) else answer
    return json.loads("".join(block.text for block in blocks))


# =============================================================================
# THE SIGNATURE
# =============================================================================

class TestTheSignature:

    def test_file_id_is_required_and_file_ids_is_gone(self):
        schema = _published_schema()
        assert "file_id" in schema["required"]
        assert "file_ids" not in schema["properties"]
        assert schema["properties"]["file_id"]["type"] == "integer"

    def test_file_id_comes_first_after_mapping(self):
        assert list(_published_schema()["properties"])[:2] == [
            "mapping", "file_id"]

    def test_a_direct_call_without_it_names_the_missing_argument(
            self, project):
        """The plain answer: Python's own missing-argument error, which
        the tool guard turns into the ordinary error envelope."""
        out = json.loads(server.pseudonymise_source(mapping=MAPPING))
        assert "file_id" in out["error"]
        assert "missing" in out["error"]
        assert backups(project) == []

    def test_an_mcp_call_that_still_passes_file_ids_gets_the_missing_argument(
            self, project):
        """What the Upgrading note says, measured rather than expected.

        The server validates a call against a model whose extra-field
        policy is `ignore`, and no published schema carries
        `additionalProperties`, so `file_ids` is dropped without a word:
        a caller that has not moved to `file_id` is refused for the
        missing required argument, and one that passes both gets the
        same preview as `file_id` alone.
        """
        with pytest.raises(Exception) as refused:
            asyncio.run(server.mcp.call_tool(
                "pseudonymise_source", {"mapping": MAPPING, "file_ids": [1]}))
        assert "file_id" in str(refused.value)
        assert "Field required" in str(refused.value)
        both = _preview_body(asyncio.run(server.mcp.call_tool(
            "pseudonymise_source",
            {"mapping": MAPPING, "file_id": 1, "file_ids": [1]})))
        alone = _preview_body(asyncio.run(server.mcp.call_tool(
            "pseudonymise_source", {"mapping": MAPPING, "file_id": 1})))
        for answer in (both, alone):
            assert answer["requires_confirmation"] is True
            answer.pop("preview_token")
            answer["execute_with"]["arguments"].pop("preview_token")
        assert both == alone
        assert backups(project) == []


class TestTheTransportCoercesFileId:
    """QA-3, and the lead's ruling on it: kept, for usability (local
    models often send numbers as strings), documented in the tool's
    description, and pinned as it behaves. Over MCP the arguments are
    validated in pydantic's lax mode before the tool runs, which reads as
    an integer anything Python's own integer syntax does: "1", 1.0, true,
    "01", "+1", " 1 " and "1.0" reach the tool as the integer 1, and
    "1_0" as the integer 10 (the underscore is a digit separator in that
    syntax; fix round 2, CORR-5, where the description now says so);
    false reaches it as 0; 1.5, "one" and "abc" are refused in pydantic's
    words; 0, -1 and false reach the tool and are refused in ours."""

    @pytest.mark.parametrize("sent", ["1", 1.0, True, "01", "+1", " 1 ",
                                      "1.0"],
                             ids=["string", "float", "true", "leading-zero",
                                  "plus", "spaces", "string-float"])
    def test_a_host_value_that_means_one_is_file_1(self, project, sent):
        answer = _preview_body(asyncio.run(server.mcp.call_tool(
            "pseudonymise_source", {"mapping": MAPPING, "file_id": sent})))
        assert answer["requires_confirmation"] is True
        assert answer["execute_with"]["arguments"]["file_id"] == 1
        assert isinstance(answer["execute_with"]["arguments"]["file_id"],
                          int)
        assert backups(project) == []

    @pytest.mark.parametrize("sent", [1.5, "one"], ids=["fraction", "word"])
    def test_a_value_that_is_not_an_integer_is_refused_by_the_transport(
            self, project, sent):
        with pytest.raises(Exception) as refused:
            asyncio.run(server.mcp.call_tool(
                "pseudonymise_source", {"mapping": MAPPING, "file_id": sent}))
        assert "file_id" in str(refused.value)

    @pytest.mark.parametrize("sent", [0, -1, False],
                             ids=["zero", "minus-one", "false"])
    def test_zero_and_below_reach_the_tool_and_are_refused_in_our_words(
            self, project, sent):
        answer = _preview_body(asyncio.run(server.mcp.call_tool(
            "pseudonymise_source", {"mapping": MAPPING, "file_id": sent})))
        assert answer == {"error": "file_id must be a positive integer."}

    def test_an_underscore_is_a_digit_separator(self, project):
        """"1_0" is file 10, which this project does not have, so it is
        refused by that number: the surprise the description names."""
        answer = _preview_body(asyncio.run(server.mcp.call_tool(
            "pseudonymise_source", {"mapping": MAPPING, "file_id": "1_0"})))
        assert "10" in answer["error"]
        assert answer.get("reason") == "unknown_file_id"



class TestTheTransportCoercesTheNewSwitches:
    """v0.13 Brief 2 (its hand-off note, H.2.6, and the lead's second
    answer): the three new booleans are treated as `file_id` was under
    QA-3: the transport's coercion is kept, documented in the
    description, and pinned as it behaves. Measured on mcp 1.30.0:
    1, "1", "true", "yes", "on", "t" and "y" reach the tool as true;
    0, "0", "false", "no" and "off" as false; 2 and "maybe" are refused
    by the transport. The attestation is among them: a host that sends
    "yes" attests, which is why the description says so."""

    TRUE = [1, "1", "true", "yes", "on", "t", "y"]
    FALSE = [0, "0", "false", "no", "off"]

    def _recipe(self, argument, sent):
        answer = _preview_body(asyncio.run(server.mcp.call_tool(
            "pseudonymise_source",
            {"mapping": MAPPING, "file_id": 1, argument: sent})))
        return answer["execute_with"]["arguments"]

    @pytest.mark.parametrize("argument", ["rewrite_memos",
                                          "save_mapping_to_project",
                                          "researcher_keeps_mapping"])
    def test_the_truthy_and_falsy_spellings(self, project, argument):
        for sent in self.TRUE:
            assert self._recipe(argument, sent).get(argument) is True, sent
        for sent in self.FALSE:
            assert self._recipe(argument, sent).get(argument, False) \
                is False, sent
        assert backups(project) == []

    @pytest.mark.parametrize("argument", ["rewrite_memos",
                                          "save_mapping_to_project",
                                          "researcher_keeps_mapping"])
    @pytest.mark.parametrize("sent", [2, "maybe"])
    def test_a_value_that_is_not_a_boolean_is_refused(self, project,
                                                      argument, sent):
        with pytest.raises(Exception) as refused:
            asyncio.run(server.mcp.call_tool(
                "pseudonymise_source",
                {"mapping": MAPPING, "file_id": 1, argument: sent}))
        assert argument in str(refused.value)

    def test_include_pseudonyms_takes_yes_for_true(self, project):
        (project / "pseudonyms.json").write_text(json.dumps(
            [{"original": "Thomas", "pseudonym": "Alex"}]), encoding="utf-8")
        for sent, listed in (("yes", True), ("on", True), ("no", False)):
            answer = _preview_body(asyncio.run(server.mcp.call_tool(
                "get_current_project", {"include_pseudonyms": sent})))
            assert ("entries_list" in answer["pseudonyms_json"]) is listed


# =============================================================================
# THE REFUSALS THAT REPLACE `skipped_files`
# =============================================================================

# The fixture: file 2 is a PDF source with extracted text, file 3 an
# audio file whose fulltext is NULL, and there is no file 999.
REFUSALS = [
    (2, "pdf_source",
     "file 2 is a PDF source; this tool rewrites text sources only, as "
     "QualCoder's own editor does."),
    (3, "no_fulltext",
     "file 3 has no stored text (a media file, or an empty source), so "
     "there is nothing to rewrite."),
    (999, "unknown_file_id",
     "file_id 999 is not a file in this project. Use search_files or the "
     "qualcoder://files/list resource to list files."),
]


class TestAFileThisToolCannotRewriteIsRefused:

    @pytest.mark.parametrize("fid,reason,text", REFUSALS,
                             ids=[r for _, r, _ in REFUSALS])
    def test_each_is_refused_with_its_reason(self, project, fid, reason,
                                             text):
        out = preview_of(file_id=fid)
        assert out == {"error": text, "reason": reason}
        assert backups(project) == []

    def test_an_empty_text_source_is_no_fulltext_too(self, project):
        """Eligibility is decided by data: an empty string is no text,
        whatever the file is called."""
        con = sqlite3.connect(str(project / "data.qda"))
        con.execute("UPDATE source SET fulltext='' WHERE id=4")
        con.commit()
        con.close()
        _reconnect(project)
        assert preview_of(file_id=4)["reason"] == "no_fulltext"

    @pytest.mark.parametrize("fid,reason,text", REFUSALS[:2],
                             ids=[r for _, r, _ in REFUSALS[:2]])
    def test_the_refusal_names_the_file_by_id_never_by_name(
            self, project, fid, reason, text):
        """A refusal is not the report, and a file's own name can carry
        a name from the mapping."""
        con = sqlite3.connect(str(project / "data.qda"))
        con.execute("UPDATE source SET name=? WHERE id=?",
                    (f"Thomas_file_{fid}.bin", fid))
        con.commit()
        con.close()
        _reconnect(project)
        out = preview_of(file_id=fid)
        assert out["reason"] == reason
        assert "Thomas" not in json.dumps(out)
        assert f"file {fid}" in out["error"]

    @pytest.mark.parametrize("fid,reason,text", REFUSALS,
                             ids=[r for _, r, _ in REFUSALS])
    def test_each_comes_before_any_token_check(self, project, fid, reason,
                                               text):
        """`test_validation_happens_before_any_token_check` is the model:
        the caller cannot fix a file choice by previewing again."""
        out = call(mapping=MAPPING, file_id=fid,
                   preview_token="qcp1.1.aaaaaaaa." + "a" * 32)
        assert out["reason"] == reason
        assert "token" not in out["error"].lower()
        assert backups(project) == []

    def test_the_texts_keep_the_house_rules(self):
        _house_rules([text for _, _, text in REFUSALS])
        assert set(server._PSEUDONYMISE_INELIGIBLE) == {
            reason for _, reason, _ in REFUSALS}


class TestTheIdIsValidatedInOurOwnWords:
    """`validate_id` alone passes a boolean and zero, and says "must be
    an integer" and "must be non-negative"; a file id is a positive
    integer, said once."""

    @pytest.mark.parametrize("bad", [0, -1, True, False, "1", 1.5, None,
                                     [1]],
                             ids=["zero", "negative", "true", "false",
                                  "string", "float", "none", "list"])
    def test_a_bad_file_id_is_refused_as_a_positive_integer(self, project,
                                                             bad):
        out = call(mapping=MAPPING, file_id=bad)
        assert out == {"error": "file_id must be a positive integer."}
        assert backups(project) == []

    def test_sqlites_upper_bound_still_applies(self, project):
        out = call(mapping=MAPPING, file_id=2 ** 63)
        assert "file_id must be at most" in out["error"]

    def test_the_check_comes_before_any_token_check(self, project):
        out = call(mapping=MAPPING, file_id=0,
                   preview_token="qcp1.1.aaaaaaaa." + "a" * 32)
        assert out == {"error": "file_id must be a positive integer."}


# =============================================================================
# WHAT WENT WITH THE LIST
# =============================================================================

def _server_tree():
    return ast.parse(SERVER_SOURCE)


class TestWhatWentWithTheList:

    def test_the_cap_on_the_list_is_not_bound_in_the_server(self):
        """`MAX_PSEUDONYMISE_FILE_IDS` capped a list that no longer
        exists. Read from the syntax: no assignment binds the name and
        no expression reads it."""
        for node in ast.walk(_server_tree()):
            if isinstance(node, ast.Name):
                assert node.id != "MAX_PSEUDONYMISE_FILE_IDS", node.lineno
        assert not hasattr(server, "MAX_PSEUDONYMISE_FILE_IDS")

    def test_the_journal_label_for_several_files_is_gone(self):
        """The journal entry was named "<n> files" for a run over more
        than one file. Read from the syntax: no f-string in the server
        formats `len(files)` in front of " files", and the journal-name
        function no longer branches on how many files it was given.
        (`analyze_for_coding` formats `len(files_to_analyze)`, another
        list for another purpose, which is why the argument is read.)"""
        tree = _server_tree()
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            parts = node.values
            for here, after in zip(parts, parts[1:]):
                if (isinstance(here, ast.FormattedValue)
                        and isinstance(here.value, ast.Call)
                        and isinstance(here.value.func, ast.Name)
                        and here.value.func.id == "len"
                        and len(here.value.args) == 1
                        and isinstance(here.value.args[0], ast.Name)
                        and here.value.args[0].id == "files"
                        and isinstance(after, ast.Constant)
                        and str(after.value).startswith(" files")):
                    pytest.fail(f"a several-files label at line "
                                f"{node.lineno}")
        naming = next(node for node in ast.walk(tree)
                      if isinstance(node, ast.FunctionDef)
                      and node.name == "_pseudonymise_journal_name")
        for node in ast.walk(naming):
            if isinstance(node, ast.If):
                for sub in ast.walk(node.test):
                    assert not (isinstance(sub, ast.Call)
                                and isinstance(sub.func, ast.Name)
                                and sub.func.id == "len"), node.lineno

    def test_the_journal_name_names_the_one_file(self, project):
        result = execute_from(preview_of())
        # The name is sanitised the way upstream sanitises its own
        # (`[^ \\w-]` to an underscore), so the dot is an underscore.
        assert result["journal_entry"].startswith(
            "Pseudonymisation interview_01_txt ")
        assert [item["file_id"] for item in result["files"]] == [1]
        # And its body says so in the singular (fix round 1, QA-9).
        body = query(project, "SELECT jentry FROM journal")[0]["jentry"]
        assert ("Positions after the first replacement in this file have "
                "changed.") in body
        assert "these files" not in body

    def test_neither_the_preview_nor_the_nothing_to_do_answer_lists_skips(
            self, project):
        out = preview_of()
        assert "skipped_files" not in out["preview"]
        quiet = preview_of(file_id=4)
        assert "skipped_files" not in quiet["preview"]
        answer = execute_from(quiet)
        assert answer["nothing_changed"] is True
        assert "skipped_files" not in answer
        assert backups(project) == []

    def test_the_nothing_to_do_texts_say_this_file(self, project):
        quiet = preview_of(file_id=4)
        assert ("None of the names in this mapping occurs in this file, "
                "so an execute would rewrite nothing." in quiet["warnings"])
        answer = execute_from(quiet)
        assert answer["message"] == (
            "None of the names in this mapping occurs in this file, so "
            "nothing was rewritten and no backup was taken.")
        joined = json.dumps(quiet) + json.dumps(answer)
        assert "selected files" not in joined


# =============================================================================
# ONE FILE IS THE RUN
# =============================================================================

class TestOneFileIsTheRun:

    @staticmethod
    def _second_thomas(project):
        """A second transcript, about a different Thomas."""
        con = sqlite3.connect(str(project / "data.qda"))
        con.execute("INSERT INTO source (id,name,fulltext,mediapath,memo,"
                    "owner,date) VALUES (5,'interview_02.txt',"
                    "'Thomas from the second site spoke.',NULL,'',"
                    "'TestCoder','d')")
        con.commit()
        con.close()
        _reconnect(project)

    def test_the_other_file_is_left_exactly_as_it_was(self, project):
        self._second_thomas(project)
        out = preview_of(file_id=1)
        assert [item["file_id"] for item in out["preview"]["files"]] == [1]
        result = execute_from(out)
        assert result["success"] is True
        texts = {row["id"]: row["fulltext"] for row in
                 query(project, "SELECT id, fulltext FROM source")}
        assert texts[1].startswith("Alex said")
        assert texts[5] == "Thomas from the second site spoke."

    def test_two_people_who_share_a_name_get_two_pseudonyms(self, project):
        """The owner's reason for decision A, driven: the second file is
        run with its own mapping, and each Thomas gets his own name."""
        self._second_thomas(project)
        assert execute_from(preview_of(file_id=1))["success"] is True
        other = [{"original": "Thomas", "pseudonym": "Jordan"}]
        result = execute_from(preview_of(mapping=other, file_id=5),
                              mapping=other)
        assert result["success"] is True, result
        texts = {row["id"]: row["fulltext"] for row in
                 query(project, "SELECT id, fulltext FROM source")}
        assert texts[1] == ("Alex said he met Alex yesterday. Sam agreed "
                            "with Alex. Later Alex left.")
        assert texts[5] == "Jordan from the second site spoke."

    def test_a_token_for_one_file_does_not_execute_on_another(self,
                                                               project):
        self._second_thomas(project)
        out = preview_of(file_id=1)
        refused = call(mapping=MAPPING, file_id=5,
                       preview_token=out["preview_token"])
        assert refused["reason"] == "token_other_operation"
        assert backups(project) == []

    def test_the_lists_that_stay_lists_hold_one_entry(self, project):
        """`preview.files`, the result's `files` and the run record's
        `files` stay lists, because reshaping the run record is not this
        change's to make; each holds at most one entry."""
        out = preview_of()
        assert len(out["preview"]["files"]) == 1
        result = execute_from(out)
        assert len(result["files"]) == 1
        record = json.loads(Path(result["manifest_path"]).read_text(
            encoding="utf-8"))
        # Format 2 since v0.13's Brief 2 (ruling 2), which added the note
        # section beside `files` and left `files` as it was.
        assert record["format"] == 2
        assert [item["file_id"] for item in record["files"]] == [1]
