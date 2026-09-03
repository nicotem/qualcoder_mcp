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
import os
import sqlite3
import xml.etree.ElementTree as ET
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
        # is covered by construction; the rest must be refused by us.
        # S-H2: the check is Unicode-category based, so C1 controls
        # (U+0085 NEL, U+009B), the line/paragraph separators U+2028 and
        # U+2029, and the bidi embedding/override/isolate controls are
        # refused too, keeping the single-line guarantee true.
        for bad in ("AI\nAgent", "AI\tAgent", "AI\rAgent", "AI\x7fAgent",
                    "AI\x85Agent", "AI\x9bAgent", "AI\x0bAgent",
                    "AI Agent", "AI Agent",
                    "AI‪Agent", "AI‮Agent", "AI⁦Agent",
                    "AI⁩Agent"):
            monkeypatch.setenv(AI_CODER_NAME_ENV, bad)
            with pytest.raises(ValueError) as e:
                _ai_coder_name()
            assert AI_CODER_NAME_ENV in str(e.value), repr(bad)
            assert "single-line" in str(e.value), repr(bad)

    def test_ordinary_names_in_any_script_accepted(self, monkeypatch):
        # The guard must not over-reject: ZWJ emoji sequences, ZWNJ in
        # Persian, combining marks, CJK, Cyrillic, Arabic, Greek, Devanagari
        for good in ("AI Agent", "研究助手", "Ассистент ИИ", "مساعد ذكي",
                     "Βοηθός", "सहायक", "Ayudante Ñandú", "Zoë O'Brien",
                     "می‌خواهم", "\U0001F469‍\U0001F4BB coder",
                     "Hákon", "Coder (v2) #4"):
            monkeypatch.setenv(AI_CODER_NAME_ENV, good)
            assert _ai_coder_name() == good, repr(good)

    def test_memo_privacy_marker_refused(self, monkeypatch):
        # S-M1: the configured owner is written verbatim into merge
        # provenance memos, so it must never carry the '#####' marker
        for bad in ("AI ##### Agent", "#####", "AI Agent ######"):
            monkeypatch.setenv(AI_CODER_NAME_ENV, bad)
            with pytest.raises(ValueError) as e:
                _ai_coder_name()
            assert "#####" in str(e.value)
        monkeypatch.setenv(AI_CODER_NAME_ENV, "AI #### Agent")
        assert _ai_coder_name() == "AI #### Agent"

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

    def test_refi_user_guid_stable_across_coder_name_change(
            self, setup_server, tmp_path, monkeypatch):
        # The REFI User guid keys on the fixed "ai_coder" token while the
        # display name follows the config, so re-exports of one project
        # keep a stable User guid across a rename (dev report deviation
        # 10; QA round 1, F9)
        from qualcoder_mcp.refi_export import NAMESPACE
        seen = []
        for name, fname in ((DEFAULT_AI_CODER_NAME, "a.qdpx"),
                            ("AI Agent", "b.qdpx")):
            monkeypatch.setenv(AI_CODER_NAME_ENV, name)
            out = json.loads(server.export_refi_qda(
                output_path=str(tmp_path / fname)))
            assert out.get("success") is True, out
            with zipfile.ZipFile(tmp_path / fname) as zf:
                root = ET.fromstring(zf.read("project.qde"))
            users = root.findall(f"{{{NAMESPACE}}}Users/{{{NAMESPACE}}}User")
            assert len(users) == 1
            seen.append((users[0].get("guid"), users[0].get("name"),
                         root.get("creatingUserGUID")))
        assert seen[0][1] == DEFAULT_AI_CODER_NAME
        assert seen[1][1] == "AI Agent"
        assert seen[0][0] == seen[1][0]          # same User guid
        assert seen[0][2] == seen[1][2] == seen[0][0]  # and it is the creator


class TestToolSuppliedOwnerValidated:
    """S-H3: the owner ARGUMENT of apply_codings and import_text_file goes
    through the same validator as the configured name, so a hostile owner
    (control characters, newlines, 5000 characters, the memo marker) is
    never stored under any coder column."""

    HOSTILE = ("Test\nCoder\x01", "Evil‮Owner", "x" * 5000,
               "AI ##### Agent", "   ", "Own er")

    def _approved(self, session):
        server.update_suggestion_status(
            session.session_id, approve=[session.suggestions[0].guid])
        return session

    @pytest.mark.parametrize("bad", HOSTILE, ids=repr)
    def test_apply_codings_rejects_hostile_owner(
            self, session_with_suggestions, qualcoder_db_path, bad):
        session = self._approved(session_with_suggestions)
        before = _row(qualcoder_db_path,
                      "SELECT COUNT(*) AS n FROM code_text")["n"]
        out = json.loads(server.apply_codings(
            session.session_id, create_backup=False, owner=bad))
        assert "error" in out, out
        assert "owner" in out["error"]
        assert _row(qualcoder_db_path,
                    "SELECT COUNT(*) AS n FROM code_text")["n"] == before
        assert _row(qualcoder_db_path,
                    "SELECT COUNT(*) AS n FROM code_text WHERE owner = ?",
                    (bad,))["n"] == 0

    @pytest.mark.parametrize("bad", HOSTILE, ids=repr)
    def test_import_text_file_rejects_hostile_owner(
            self, setup_server, qualcoder_db_path, bad):
        out = json.loads(server.import_text_file(
            "hostile_owner.txt", "Body.", owner=bad, create_backup=False))
        assert "error" in out, out
        assert "owner" in out["error"]
        assert _row(qualcoder_db_path,
                    "SELECT COUNT(*) AS n FROM source "
                    "WHERE name = 'hostile_owner.txt'")["n"] == 0

    def test_db_layer_repeats_the_check(self, setup_server):
        # Defense in depth: validate_text_file_import refuses on its own
        from qualcoder_mcp.database import validate_coder_name
        with pytest.raises(ValueError, match="owner"):
            server.db.validate_text_file_import(
                name="x.txt", content="Body.", owner="Bad\nOwner")
        with pytest.raises(ValueError, match="owner"):
            validate_coder_name("y" * 81, "owner")
        assert validate_coder_name("  Fine Name ", "owner") == "Fine Name"

    def test_sane_explicit_owner_still_accepted(self, setup_server,
                                                qualcoder_db_path):
        out = json.loads(server.import_text_file(
            "fine_owner.txt", "Body.", owner="Researcher B",
            create_backup=False))
        assert out["success"] is True
        assert out["owner"] == "Researcher B"


class TestSuiteIsolation:

    def test_ambient_coder_name_is_isolated(self):
        # conftest's autouse fixture removes an ambient export so the
        # default-owner pins across the suite stay valid (QA F21)
        assert AI_CODER_NAME_ENV not in os.environ
