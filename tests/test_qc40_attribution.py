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

    def test_broken_declaration_after_start_cannot_reach_an_owner_column(
            self, setup_server, qualcoder_db_path, monkeypatch):
        """v0.12: the variable declares, it no longer attributes.

        main() still refuses to start on an invalid value (the unit tests
        above), so this is the case where the environment turns invalid
        after start-up. The declaration is then treated as absent: the
        write proceeds under the PROJECT's AI coder name, no row can
        carry the broken string, and no mismatch is raised over a name we
        would refuse to store anyway.
        """
        monkeypatch.setenv(AI_CODER_NAME_ENV, "bad\nname")
        out = json.loads(server.create_code("Declared", create_backup=False))
        assert out.get("success") is True, out
        assert _row(qualcoder_db_path,
                    "SELECT owner FROM code_name WHERE name='Declared'"
                    )["owner"] == DEFAULT_AI_CODER_NAME
        assert _row(qualcoder_db_path,
                    "SELECT COUNT(*) AS n FROM code_name "
                    "WHERE owner LIKE '%bad%'")["n"] == 0


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

    def test_a_host_declaration_asks_before_it_changes_attribution(
            self, setup_server, qualcoder_db_path, monkeypatch):
        """Rule c2: a declaration that differs from the project's name is
        a question for the user, never a silent re-attribution."""
        monkeypatch.setenv(AI_CODER_NAME_ENV, "AI Agent")
        out = json.loads(server.create_code("AgentCode", create_backup=False))
        assert out["host_declared_ai_coder_name"] == "AI Agent"
        assert out["ai_coder_name"] == DEFAULT_AI_CODER_NAME
        assert out["quick_picks"] == ["AI Agent", DEFAULT_AI_CODER_NAME]
        assert _row(qualcoder_db_path,
                    "SELECT COUNT(*) AS n FROM code_name "
                    "WHERE name='AgentCode'")["n"] == 0

        # Answering the question either way unblocks the write, and the
        # answer decides the attribution.
        out = json.loads(server.set_project_ai_coder_name("AI Agent"))
        assert out["success"] is True
        server.create_code("AgentCode", create_backup=False)
        server.add_journal_entry("AgentJournal", "text", create_backup=False)
        assert _row(qualcoder_db_path,
                    "SELECT owner FROM code_name WHERE name='AgentCode'"
                    )["owner"] == "AI Agent"
        assert _row(qualcoder_db_path,
                    "SELECT owner FROM journal WHERE name='AgentJournal'"
                    )["owner"] == "AI Agent"

    def test_keeping_the_project_name_acknowledges_the_declaration(
            self, setup_server, qualcoder_db_path, monkeypatch):
        """The other answer: keep the project's name. The host does not
        ask again while its declaration stays the same."""
        monkeypatch.setenv(AI_CODER_NAME_ENV, "AI Agent")
        out = json.loads(server.set_project_ai_coder_name(
            DEFAULT_AI_CODER_NAME))
        assert out["success"] is True
        assert out["ai_coder_name"]["host_declaration"] == "AI Agent"
        assert "Unchanged; the choice was recorded again (acknowledging " \
            "this host's declaration)." in out["warnings"]
        out = json.loads(server.create_code("KeptCode", create_backup=False))
        assert out.get("success") is True, out
        assert _row(qualcoder_db_path,
                    "SELECT owner FROM code_name WHERE name='KeptCode'"
                    )["owner"] == DEFAULT_AI_CODER_NAME

    def test_explicit_owner_argument_is_refused_and_costs_nothing(
            self, session_with_suggestions, qualcoder_db_path):
        """D7 6.3 inverted: the owner argument no longer beats anything.

        The refusal comes before the backup and before the write, and the
        approved suggestions stay approved, so the caller can set the
        name and retry without re-approving anything.
        """
        session = session_with_suggestions
        server.update_suggestion_status(
            session.session_id,
            approve=[session.suggestions[0].guid])
        before = _row(qualcoder_db_path,
                      "SELECT COUNT(*) AS n FROM code_text")["n"]
        out = json.loads(server.apply_codings(
            session.session_id, create_backup=False,
            owner="Handpicked Coder"))
        assert out["action_required"] == \
            "omit_owner_or_set_project_ai_coder_name"
        assert out["ai_coder_name"] == DEFAULT_AI_CODER_NAME
        assert "Handpicked Coder" not in json.dumps(out)
        assert _row(qualcoder_db_path,
                    "SELECT COUNT(*) AS n FROM code_text")["n"] == before
        reloaded = server.session_manager.load_session(session.session_id)
        assert len(reloaded.filter_by_status("approved")) == 1

        # Retrying without the argument writes under the project's name.
        out = server.apply_codings(session.session_id, create_backup=False)
        assert "CODINGS APPLIED" in out
        assert _row(qualcoder_db_path,
                    "SELECT owner FROM code_text WHERE pos0=0 AND pos1=10"
                    )["owner"] == DEFAULT_AI_CODER_NAME

    def test_explicit_owner_in_import_is_refused(self, setup_server,
                                                 qualcoder_db_path):
        out = json.loads(server.import_text_file(
            "explicit_owner.txt", "Body.", owner="Legacy Import",
            create_backup=False))
        assert "error" in out
        assert "Legacy Import" not in json.dumps(out)
        assert _row(qualcoder_db_path,
                    "SELECT COUNT(*) AS n FROM source "
                    "WHERE name='explicit_owner.txt'")["n"] == 0

    def test_refi_export_user_name_follows_the_project(self, setup_server,
                                                       tmp_path, monkeypatch):
        """The export names the project's AI coder, not this host's
        declaration, and it never asks (ruling 6)."""
        monkeypatch.setenv(AI_CODER_NAME_ENV, "AI Agent")
        out_file = tmp_path / "attributed.qdpx"
        out = json.loads(server.export_refi_qda(output_path=str(out_file)))
        assert out.get("success") is True, out
        assert out["ai_user_name_source"] == "project"
        with zipfile.ZipFile(out_file) as zf:
            qde = zf.read("project.qde").decode("utf-8")
        assert f'name="{DEFAULT_AI_CODER_NAME}"' in qde

        server.set_project_ai_coder_name("Qwen 3.8 6bit")
        out_file2 = tmp_path / "renamed.qdpx"
        out = json.loads(server.export_refi_qda(output_path=str(out_file2)))
        assert out["ai_user_name_source"] == "project"
        with zipfile.ZipFile(out_file2) as zf:
            assert 'name="Qwen 3.8 6bit"' in \
                zf.read("project.qde").decode("utf-8")

    def test_refi_export_on_an_unset_project_uses_the_declaration(
            self, setup_server_unset, tmp_path, monkeypatch):
        """An unset project is asked by WRITES, never by the export."""
        monkeypatch.setenv(AI_CODER_NAME_ENV, "AI Agent")
        out = json.loads(server.export_refi_qda(
            output_path=str(tmp_path / "unset.qdpx")))
        assert out.get("success") is True, out
        assert out["ai_user_name_source"] == "host_declaration"
        with zipfile.ZipFile(tmp_path / "unset.qdpx") as zf:
            assert 'name="AI Agent"' in zf.read("project.qde").decode("utf-8")

        monkeypatch.delenv(AI_CODER_NAME_ENV, raising=False)
        out = json.loads(server.export_refi_qda(
            output_path=str(tmp_path / "unset2.qdpx")))
        assert out["ai_user_name_source"] == "built_in_default"

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
            server.set_project_ai_coder_name(name)
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

    def test_a_well_formed_owner_is_now_refused_by_the_rule_not_the_rules(
            self, setup_server, qualcoder_db_path):
        """"Researcher B" is a perfectly valid coder name, and that is the
        point: it passes validation and is then refused because a human
        coder's name is never used for rows this server writes."""
        out = json.loads(server.import_text_file(
            "fine_owner.txt", "Body.", owner="Researcher B",
            create_backup=False))
        assert "error" in out
        assert "owner argument no longer chooses" in out["error"]
        assert _row(qualcoder_db_path,
                    "SELECT COUNT(*) AS n FROM source "
                    "WHERE name='fine_owner.txt'")["n"] == 0

    def test_the_project_name_itself_is_accepted_as_owner(
            self, setup_server, qualcoder_db_path):
        out = json.loads(server.import_text_file(
            "same_name.txt", "Body.", owner=DEFAULT_AI_CODER_NAME,
            create_backup=False))
        assert out["success"] is True
        assert _row(qualcoder_db_path,
                    "SELECT owner FROM source WHERE name='same_name.txt'"
                    )["owner"] == DEFAULT_AI_CODER_NAME


class TestSuiteIsolation:

    def test_ambient_coder_name_is_isolated(self):
        # conftest's autouse fixture removes an ambient export so the
        # default-owner pins across the suite stay valid (QA F21)
        assert AI_CODER_NAME_ENV not in os.environ


# =============================================================================
# INVISIBLE CHARACTERS IN A NAME DEFEAT ATTRIBUTION (fix round 4, S3)
# =============================================================================

class TestFormatCharactersAreRefusedAsAClass:
    """A coder name exists to tell AI rows from a person's. A name
    carrying U+FEFF, U+200B or U+2060 renders identically to the
    researcher's own everywhere QualCoder shows it, so category Cf is
    refused as a class, with ZWNJ and ZWJ kept because they spell words
    in Persian and Indic scripts rather than hiding them.

    Every character below is written as an escape on purpose: a test
    about invisible characters must not itself contain one a reader
    cannot see.
    """

    # The first three are inside Unicode's own Bidi_Control set and the
    # hand-listed range missed all three; the rest render as nothing.
    INVISIBLE = ("؜", "‎", "‏", "﻿", "​",
                 "⁠", "­", "⁡", "⁪", "￹",
                 "\U000E0001", "\U000E0041")

    PERSIAN = "می‌خواهم"

    def test_every_format_character_is_refused(self):
        from qualcoder_mcp.database import validate_coder_name
        for ch in self.INVISIBLE:
            with pytest.raises(ValueError) as e:
                validate_coder_name("AI" + ch + "Agent")
            assert "invisible formatting characters" in str(e.value), repr(ch)

    def test_the_class_is_closed_not_a_list(self):
        """Every Cf character in the BMP except the two, swept rather
        than listed, so a Unicode release that adds one is covered."""
        import unicodedata
        from qualcoder_mcp.database import (validate_coder_name,
                                            forbidden_display_char)
        allowed, refused = [], 0
        for cp in range(0x10000):
            ch = chr(cp)
            if unicodedata.category(ch) != "Cf":
                continue
            if forbidden_display_char(ch) is None:
                allowed.append(ch)
            else:
                refused += 1
        assert allowed == ["‌", "‍"]
        assert refused >= 30
        assert validate_coder_name(self.PERSIAN) == self.PERSIAN

    def test_the_environment_declaration_refuses_them_too(self, monkeypatch):
        for ch in self.INVISIBLE:
            monkeypatch.setenv(AI_CODER_NAME_ENV, "AI" + ch + "Agent")
            with pytest.raises(ValueError) as e:
                _ai_coder_name()
            assert AI_CODER_NAME_ENV in str(e.value), repr(ch)

    def test_the_note_beside_the_name_refuses_them_too(self):
        from qualcoder_mcp.database import validate_coder_note
        with pytest.raises(ValueError):
            validate_coder_note("qwen﻿ 3")
        assert validate_coder_note("qwen 3") == "qwen 3"

    def test_the_sidecar_refuses_one_on_write_and_on_read(self, tmp_path):
        """The sidecar validates on both sides, so neither a tool call
        nor a hand-edited file can put an invisible name in an owner
        column."""
        import json as _json
        from qualcoder_mcp import project_settings as ps
        folder = tmp_path / "p.qda"
        folder.mkdir()
        with pytest.raises(ValueError):
            ps.write_ai_coder_name(folder, "Qwen﻿ 3")
        assert not (folder / ps.SIDECAR_NAME).exists()
        (folder / ps.SIDECAR_NAME).write_text(_json.dumps({
            "format": ps.SIDECAR_FORMAT, "format_version": 1,
            "ai_coder_name": {"name": "Qwen﻿ 3", "set_at": None,
                              "note": "", "host_declaration": None},
        }), encoding="utf-8")
        assert ps.read_sidecar(folder).status == ps.SIDECAR_UNREADABLE

    def test_ordinary_names_are_still_accepted(self):
        from qualcoder_mcp.database import validate_coder_name
        for good in ("AI Agent", "研究助手", self.PERSIAN,
                     "\U0001F469‍\U0001F4BB coder", "Zoë O'Brien"):
            assert validate_coder_name(good) == good, repr(good)

    def test_the_mru_hint_path_uses_the_same_rule(self):
        """The same false docstring sat on the path guard: it listed a
        bidi RANGE, which let U+061C, U+200E and U+200F through."""
        for ch in ("؜", "‎", "‏", "﻿", "​"):
            assert server._mru_path_is_canonical(
                "/home/me/a" + ch + "b.qda/data.qda") is False, repr(ch)
        assert server._mru_path_is_canonical(
            "/home/me/study.qda/data.qda") is True

    def test_the_two_documents_say_what_the_code_does(self):
        """The shipped docstring and the README sentence both used to
        promise that bidi characters were refused while three of them
        were accepted."""
        from qualcoder_mcp.database import validate_coder_name
        doc = validate_coder_name.__doc__
        assert "category Cf" in doc
        assert "ZWNJ and ZWJ" in doc
        root = Path(__file__).resolve().parents[1]
        readme = (root / "README.md").read_text(encoding="utf-8")
        paragraphs = [" ".join(p.split()) for p in readme.split("\n\n")]
        sentence = [p for p in paragraphs
                    if "stop the server at startup" in p]
        assert len(sentence) == 1, sentence
        assert "Unicode category Cf" in sentence[0]
        assert "ZWNJ and ZWJ" in sentence[0]
