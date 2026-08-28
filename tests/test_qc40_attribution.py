"""P1-2: configurable AI coder attribution (QUALCODER_MCP_AI_CODER_NAME).

Owner verdict (b), 2026-08-28: the coding owner string is configurable,
defaults to "AI Coding Assistant" (distinct-by-default, continuity with
existing projects), and "AI Agent" (QualCoder 4.0's exact string,
ai_mcp_server.py:85) is the documented opt-in for 4.0-coherent mixed
workflows. The configured owner applies to EVERY row this server
writes: codings, annotations, journal entries, imports, cases,
categories, codes, attributes and attribute types.
"""

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server
from qualcoder_mcp.server import (
    AI_CODER_NAME_ENV,
    DEFAULT_AI_CODER_NAME,
    MAX_AI_CODER_NAME_LENGTH,
    _ai_coder_name,
)


def _row(project_path, sql, args=()):
    con = sqlite3.connect(str(Path(project_path) / "data.qda"))
    con.row_factory = sqlite3.Row
    r = con.execute(sql, args).fetchone()
    con.close()
    return r


# =============================================================================
# CONFIG RESOLUTION AND VALIDATION
# =============================================================================

class TestAiCoderNameConfig:

    def test_unset_returns_default(self, monkeypatch):
        monkeypatch.delenv(AI_CODER_NAME_ENV, raising=False)
        assert _ai_coder_name() == DEFAULT_AI_CODER_NAME
        assert DEFAULT_AI_CODER_NAME == "AI Coding Assistant"

    def test_set_value_used_verbatim_after_trim(self, monkeypatch):
        monkeypatch.setenv(AI_CODER_NAME_ENV, "  AI Agent  ")
        assert _ai_coder_name() == "AI Agent"

    def test_empty_value_refused(self, monkeypatch):
        monkeypatch.setenv(AI_CODER_NAME_ENV, "   ")
        with pytest.raises(ValueError) as e:
            _ai_coder_name()
        assert AI_CODER_NAME_ENV in str(e.value)

    def test_overlong_value_refused(self, monkeypatch):
        monkeypatch.setenv(AI_CODER_NAME_ENV,
                           "x" * (MAX_AI_CODER_NAME_LENGTH + 1))
        with pytest.raises(ValueError) as e:
            _ai_coder_name()
        assert str(MAX_AI_CODER_NAME_LENGTH) in str(e.value)

    def test_max_length_value_accepted(self, monkeypatch):
        name = "x" * MAX_AI_CODER_NAME_LENGTH
        monkeypatch.setenv(AI_CODER_NAME_ENV, name)
        assert _ai_coder_name() == name

    def test_control_characters_refused(self, monkeypatch):
        # NUL cannot even be placed in the environment by the OS, so it
        # is covered by construction; the rest must be refused by us
        for bad in ("AI\nAgent", "AI\tAgent", "AI\rAgent", "AI\x7fAgent"):
            monkeypatch.setenv(AI_CODER_NAME_ENV, bad)
            with pytest.raises(ValueError):
                _ai_coder_name()

    def test_tool_call_with_broken_config_returns_clean_error(
            self, setup_server, monkeypatch):
        # Runtime defense in depth: if the env turns invalid after start,
        # the tool guard converts the ValueError into error JSON instead
        # of writing rows under a broken name
        monkeypatch.setenv(AI_CODER_NAME_ENV, "bad\nname")
        out = json.loads(server.create_code("Doomed", create_backup=False))
        assert "error" in out
        assert AI_CODER_NAME_ENV in out["error"]


# =============================================================================
# DEFAULT ATTRIBUTION ON EVERY WRITE PATH
# =============================================================================

class TestDefaultAttribution:

    def test_create_code_category_case_journal_annotation(
            self, setup_server, qualcoder_db_path, monkeypatch):
        monkeypatch.delenv(AI_CODER_NAME_ENV, raising=False)
        server.create_code("OwnCode", create_backup=False)
        server.create_category("OwnCat", create_backup=False)
        server.create_case("OwnCase", create_backup=False)
        server.add_journal_entry("OwnJournal", "text", create_backup=False)
        server.add_annotation(1, 0, 4, "note", create_backup=False)

        checks = [
            ("SELECT owner FROM code_name WHERE name='OwnCode'",),
            ("SELECT owner FROM code_cat WHERE name='OwnCat'",),
            ("SELECT owner FROM cases WHERE name='OwnCase'",),
            ("SELECT owner FROM journal WHERE name='OwnJournal'",),
            ("SELECT owner FROM annotation WHERE memo='note'",),
        ]
        for (sql,) in checks:
            assert _row(qualcoder_db_path, sql)["owner"] == \
                DEFAULT_AI_CODER_NAME, sql

    def test_import_and_link_and_attributes(self, setup_server,
                                            qualcoder_db_path, monkeypatch):
        monkeypatch.delenv(AI_CODER_NAME_ENV, raising=False)
        out = json.loads(server.import_text_file(
            "own_import.txt", "Some text.", create_backup=False))
        assert out["success"] is True
        assert out["owner"] == DEFAULT_AI_CODER_NAME
        fid = out["file_id"]
        server.link_file_to_case(fid, case_id=1, create_backup=False)
        server.create_attribute_type("OwnAttr", "case", create_backup=False)
        server.set_attribute("case", 1, "OwnAttr", "yes", create_backup=False)

        assert _row(qualcoder_db_path,
                    "SELECT owner FROM source WHERE id=?",
                    (fid,))["owner"] == DEFAULT_AI_CODER_NAME
        assert _row(qualcoder_db_path,
                    "SELECT owner FROM case_text WHERE fid=?",
                    (fid,))["owner"] == DEFAULT_AI_CODER_NAME
        assert _row(qualcoder_db_path,
                    "SELECT owner FROM attribute_type WHERE name='OwnAttr'"
                    )["owner"] == DEFAULT_AI_CODER_NAME
        assert _row(qualcoder_db_path,
                    "SELECT owner FROM attribute WHERE name='OwnAttr' "
                    "AND attr_type='case' AND id=1"
                    )["owner"] == DEFAULT_AI_CODER_NAME

    def test_apply_codings_default_owner(self, session_with_suggestions,
                                         qualcoder_db_path, monkeypatch):
        monkeypatch.delenv(AI_CODER_NAME_ENV, raising=False)
        session = session_with_suggestions
        server.update_suggestion_status(
            session.session_id,
            approve=[session.suggestions[0].guid])
        out = server.apply_codings(session.session_id, create_backup=False)
        assert "CODINGS APPLIED" in out
        row = _row(qualcoder_db_path,
                   "SELECT owner FROM code_text WHERE pos0=0 AND pos1=10")
        assert row["owner"] == DEFAULT_AI_CODER_NAME


# =============================================================================
# THE 4.0-COHERENT OPT-IN AND EXPLICIT OVERRIDES
# =============================================================================

class TestConfiguredAttribution:

    def test_env_override_applies_across_writes(self, setup_server,
                                                qualcoder_db_path,
                                                monkeypatch):
        monkeypatch.setenv(AI_CODER_NAME_ENV, "AI Agent")
        server.create_code("AgentCode", create_backup=False)
        server.add_journal_entry("AgentJournal", "text", create_backup=False)
        assert _row(qualcoder_db_path,
                    "SELECT owner FROM code_name WHERE name='AgentCode'"
                    )["owner"] == "AI Agent"
        assert _row(qualcoder_db_path,
                    "SELECT owner FROM journal WHERE name='AgentJournal'"
                    )["owner"] == "AI Agent"

    def test_explicit_owner_argument_beats_config(
            self, session_with_suggestions, qualcoder_db_path, monkeypatch):
        monkeypatch.setenv(AI_CODER_NAME_ENV, "AI Agent")
        session = session_with_suggestions
        server.update_suggestion_status(
            session.session_id,
            approve=[session.suggestions[0].guid])
        out = server.apply_codings(session.session_id, create_backup=False,
                                   owner="Handpicked Coder")
        assert "CODINGS APPLIED" in out
        row = _row(qualcoder_db_path,
                   "SELECT owner FROM code_text WHERE pos0=0 AND pos1=10")
        assert row["owner"] == "Handpicked Coder"

    def test_explicit_owner_in_import(self, setup_server, qualcoder_db_path,
                                      monkeypatch):
        monkeypatch.setenv(AI_CODER_NAME_ENV, "AI Agent")
        out = json.loads(server.import_text_file(
            "explicit_owner.txt", "Body.", owner="Legacy Import",
            create_backup=False))
        assert out["success"] is True
        assert _row(qualcoder_db_path,
                    "SELECT owner FROM source WHERE name='explicit_owner.txt'"
                    )["owner"] == "Legacy Import"

    def test_refi_export_user_name_follows_config(self, setup_server,
                                                  tmp_path, monkeypatch):
        monkeypatch.setenv(AI_CODER_NAME_ENV, "AI Agent")
        out_file = tmp_path / "attributed.qdpx"
        out = json.loads(server.export_refi_qda(output_path=str(out_file)))
        assert out.get("success") is True, out
        with zipfile.ZipFile(out_file) as zf:
            qde = zf.read("project.qde").decode("utf-8")
        assert 'name="AI Agent"' in qde
