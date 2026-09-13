"""B1 (v0.12, D7): the project's AI coder name, asked once, never guessed.

Until v0.12 every row this server wrote carried a machine-wide name. It is
now the project's setting, stored in a sidecar beside data.qda, chosen by
the human, asked for by the first write that needs it, and never guessed
from the environment. These tests pin the ask, the refusals word for word,
the sidecar's reader and writer, the setter's checks and warnings, and the
fact that a name change never re-attributes a row.

Upstream citations are into the pinned clone (QualCoder master 9bddf17)
and the 3.8.2 tag, both verified with git rev-parse before being cited.
"""

import json
import os
import re
import sqlite3
import stat
import sys
from pathlib import Path

import pytest

import qualcoder_mcp.server as server
import track5_helpers as H
from qualcoder_mcp import project_settings as ps
from qualcoder_mcp.database import QualcoderDatabase
from qualcoder_mcp.project_settings import (
    AI_CODER_NAME_ENV,
    DEFAULT_AI_CODER_NAME,
    KNOWN_AI_ASSISTANT_OWNER,
    SIDECAR_NAME,
    read_sidecar,
    write_ai_coder_name,
)
from track5_helpers import write_fixture_sidecar

POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX file modes and symlinks: Windows chmod moves only the "
           "read-only flag and symlink creation needs a privilege")


def _sidecar(project_path) -> Path:
    return Path(project_path) / SIDECAR_NAME


def _row(project_path, sql, args=()):
    con = sqlite3.connect(str(Path(project_path) / "data.qda"))
    con.row_factory = sqlite3.Row
    r = con.execute(sql, args).fetchone()
    con.close()
    return r


def _backup_folders(project_path):
    parent = Path(project_path).parent
    stem = Path(project_path).stem
    return sorted(p.name for p in parent.glob(f"{stem}_backup_*"))


def _reopen(project_path):
    if server.db is not None:
        try:
            server.db.close()
        except Exception:
            pass
    server.db = QualcoderDatabase(project_path)


# Nine of the eleven database-writing tools that need an owner (D7 3.1).
# Each entry is a callable taking the server module, so the ask can be
# pinned on these nine with one parametrisation. The other two,
# apply_codings and create_proposed_codes, need a saved session before
# the call and are pinned by their own tests below, which also assert
# that the approvals and the proposals survive the refusal.
ASK_WRITES = {
    "import_text_file":
        lambda s: s.import_text_file("asked.txt", "Body.",
                                     create_backup=False),
    "link_file_to_case":
        lambda s: s.link_file_to_case(1, case_id=1, create_backup=False),
    "add_journal_entry":
        lambda s: s.add_journal_entry("J", "text", create_backup=False),
    "create_code": lambda s: s.create_code("AskedCode", create_backup=False),
    "create_category":
        lambda s: s.create_category("AskedCat", create_backup=False),
    "add_annotation":
        lambda s: s.add_annotation(1, 0, 4, "note", create_backup=False),
    "create_case": lambda s: s.create_case("AskedCase", create_backup=False),
    "create_attribute_type":
        lambda s: s.create_attribute_type("AskedAttr", "case",
                                          create_backup=False),
    "set_attribute":
        lambda s: s.set_attribute("case", 1, "Site", "x",
                                  create_backup=False),
}

# D7 3.1's full list, so a twelfth write tool cannot land uncovered and
# the count in the comment above cannot drift away from the code again
# (QA round 1, F9: the constant was called ELEVEN_WRITES and held nine).
ELEVEN_WRITE_TOOLS = frozenset(ASK_WRITES) | {"apply_codings",
                                              "create_proposed_codes"}


# Every read surface B1.17 names, keyed by name so a failure says which
# one asked. Both halves of the mandate are here: the read TOOLS and the
# nine resource handlers, which nothing exercised before (QA round 1,
# F10). No entry is guarded by hasattr: a guard that turns a missing
# surface into a passing assertion is the defect, not the fix.
READS_THAT_MUST_NEVER_ASK = {
    # read tools
    "get_current_project": lambda s: s.get_current_project(),
    "get_project_summary": lambda s: s.get_project_summary(),
    "get_coding_frequencies": lambda s: s.get_coding_frequencies(),
    "search_files": lambda s: s.search_files("stress"),
    "get_coded_segments": lambda s: s.get_coded_segments(1),
    "search_coded_text": lambda s: s.search_coded_text("stress"),
    "list_backups": lambda s: s.list_backups(),
    "search_memos": lambda s: s.search_memos("x"),
    "get_case_code_matrix": lambda s: s.get_case_code_matrix(),
    "list_available_projects": lambda s: s.list_available_projects(),
    # resource handlers (qualcoder://...)
    "get_project_info": lambda s: s.get_project_info(),
    "list_all_codes": lambda s: s.list_all_codes(),
    "list_all_categories": lambda s: s.list_all_categories(),
    "get_code_info": lambda s: s.get_code_info(1),
    "list_all_files": lambda s: s.list_all_files(),
    "get_file_content": lambda s: s.get_file_content(1),
    "list_all_cases": lambda s: s.list_all_cases(),
    "get_case_info": lambda s: s.get_case_info(1),
    "get_journal_entries": lambda s: s.get_journal_entries(),
}


def _house_rules(texts, labels=None):
    """No em dashes, British English, no forbidden vocabulary.

    The house rules are pinned on every text this batch adds, in the
    module that owns it, so a reworded string is checked where it is
    written rather than in one distant place (the convention Batch A's D6
    test 8 established).
    """
    forbidden_spellings = ("color", "colors", "behavior", "organize",
                           "recognize", "authorization", "analyze",
                           "labeled", "favor")
    for index, text in enumerate(texts):
        label = (labels[index] if labels else f"text {index}")
        assert "\u2014" not in text, label
        lowered = text.lower()
        for word in forbidden_spellings:
            # Identifiers are exempt by house rule (analyze_file_with_coding,
            # honor_visibility, color): the word counts only when it is
            # prose, that is, not glued to another identifier character.
            pattern = r"(?<![A-Za-z_])" + word + r"(?![A-Za-z_])"
            assert not re.search(pattern, lowered), (label, word)
        for token in re.findall(r"(?<![A-Za-z_])session_id(?![A-Za-z_])",
                                text):
            raise AssertionError((label, "session_id"))


def _approved_session(server_mod, project_path):
    """A session with one approved suggestion, for apply_codings."""
    from qualcoder_mcp.sessions import AICodingSession, CodingSuggestion
    session = AICodingSession(project_path=project_path,
                              description="ask flow", file_ids=[1],
                              code_names=["Stress"], instruction="t",
                              min_confidence=0.5)
    session.add_suggestion(CodingSuggestion(
        file_id=1, file_name="interview.txt", code_id=1, code_name="Stress",
        start_pos=0, end_pos=10, segment_text="This is in",
        reasoning="r", confidence=0.9, status="approved"))
    server_mod.session_manager.save_session(session)
    return session


# =============================================================================
# 10.1 ISOLATION AND SUITE HYGIENE
# =============================================================================

class TestSuiteHygiene:

    def test_the_default_fixture_is_a_project_that_has_been_asked(
            self, setup_server, qualcoder_db_path):
        state = read_sidecar(qualcoder_db_path)
        assert state.is_set
        assert state.name == DEFAULT_AI_CODER_NAME
        assert state.entry["note"] == "test fixture"
        assert state.entry["host_declaration"] is None

    def test_the_unset_fixture_has_no_sidecar(self, setup_server_unset,
                                              qualcoder_db_path):
        assert not _sidecar(qualcoder_db_path).exists()
        assert read_sidecar(qualcoder_db_path).status == ps.SIDECAR_UNSET

    def test_an_ambient_declaration_equal_to_the_name_never_nags(
            self, setup_server, qualcoder_db_path, monkeypatch):
        monkeypatch.setenv(AI_CODER_NAME_ENV, DEFAULT_AI_CODER_NAME)
        out = json.loads(server.create_code("Ambient", create_backup=False))
        assert out.get("success") is True, out

    def test_a_different_ambient_declaration_does_nag(
            self, setup_server, qualcoder_db_path, monkeypatch):
        monkeypatch.setenv(AI_CODER_NAME_ENV, "Someone Else")
        out = json.loads(server.create_code("Ambient2", create_backup=False))
        assert out["host_declared_ai_coder_name"] == "Someone Else"
        assert "error" in out


# =============================================================================
# 10.2 PARITY PINS AGAINST THE PINNED UPSTREAM
# =============================================================================

class TestUpstreamParity:
    """Facts about QualCoder that this design rests on, pinned as tests.

    Citations are file:line in the pinned clone (master 9bddf17) and in
    the 3.8.2 tag reachable through `git show 3.8.2:src/qualcoder/...`.
    """

    # app.py:1480-1494 at 9bddf17, byte-identical at
    # 3.8.2:__main__.py:1230-1244. Copied verbatim so a pin bump that
    # changes upstream's statement fails here rather than silently.
    HARVEST_SQL = """
                INSERT OR IGNORE INTO coder_names (name)
                    SELECT owner FROM code_image WHERE owner IS NOT NULL
                    UNION SELECT owner FROM code_text WHERE owner IS NOT NULL
                    UNION SELECT owner FROM code_av WHERE owner IS NOT NULL
                    UNION SELECT owner FROM code_name WHERE owner IS NOT NULL
                    UNION SELECT owner FROM code_cat WHERE owner IS NOT NULL
                    UNION SELECT owner FROM cases WHERE owner IS NOT NULL
                    UNION SELECT owner FROM case_text WHERE owner IS NOT NULL
                    UNION SELECT owner FROM attribute WHERE owner IS NOT NULL
                    UNION SELECT owner FROM attribute_type WHERE owner IS NOT NULL
                    UNION SELECT owner FROM source WHERE owner IS NOT NULL
                    UNION SELECT owner FROM annotation WHERE owner IS NOT NULL
                    UNION SELECT owner FROM journal WHERE owner IS NOT NULL
                    UNION SELECT owner FROM manage_files_display WHERE owner IS NOT NULL
                    UNION SELECT owner FROM files_filter WHERE owner IS NOT NULL;
            """

    # app.py:1470-1475 at 9bddf17 and 3.8.2:__main__.py:1220-1223
    CODER_NAMES_DDL = """
                CREATE TABLE IF NOT EXISTS coder_names (
                    name TEXT UNIQUE NOT NULL,
                    visibility INTEGER NOT NULL DEFAULT 1 CHECK (visibility IN (0, 1))
                );
            """

    def test_qualcoder_enrols_our_name_on_its_next_project_open(
            self, setup_server, qualcoder_db_path):
        """Why this server never inserts into coder_names (D7 section 5).

        QualCoder harvests every owner column into coder_names when it
        opens a project, with visibility defaulting to 1, so a name we
        write rows under appears in its coder list by itself.
        """
        json.loads(server.set_project_ai_coder_name("Qwen 3.8 6bit"))
        server.create_code("Harvested", create_backup=False)
        con = sqlite3.connect(str(Path(qualcoder_db_path) / "data.qda"))
        con.executescript(self.CODER_NAMES_DDL)
        # The harvest runs against the tables this fixture has; the two
        # 4.0-only tables are created empty so the verbatim statement can
        # run unchanged, which is the point of copying it verbatim.
        con.executescript(
            "CREATE TABLE IF NOT EXISTS manage_files_display "
            "(owner TEXT); CREATE TABLE IF NOT EXISTS files_filter "
            "(owner TEXT);")
        con.executescript(self.HARVEST_SQL)
        row = con.execute(
            "SELECT visibility FROM coder_names WHERE name = ?",
            ("Qwen 3.8 6bit",)).fetchone()
        con.close()
        assert row is not None, "QualCoder would not have enrolled the name"
        assert row[0] == 1

    def test_a_name_absent_from_coder_names_is_visible(
            self, setup_server, qualcoder_db_path):
        """The views' NOT EXISTS semantics (app.py:1530-1538): a coder
        with rows but no coder_names row is VISIBLE, which is why our
        visibility lookup treats "absent" as "not hidden"."""
        con = sqlite3.connect(str(Path(qualcoder_db_path) / "data.qda"))
        con.executescript(self.CODER_NAMES_DDL)
        con.executescript(
            """CREATE VIEW IF NOT EXISTS code_text_visible AS
               SELECT t.* FROM code_text t
               WHERE NOT EXISTS (SELECT 1 FROM coder_names c
                                 WHERE c.name = t.owner AND c.visibility = 0);""")
        con.execute(
            "INSERT INTO code_text (ctid, cid, fid, seltext, pos0, pos1, "
            "owner, date, memo, important) VALUES (77, 1, 1, 'x', 0, 1, "
            "'Never Enrolled', '2024-01-15', '', 0)")
        con.commit()
        n = con.execute("SELECT COUNT(*) FROM code_text_visible "
                        "WHERE owner = 'Never Enrolled'").fetchone()[0]
        con.close()
        assert n == 1

    def test_coder_names_is_case_sensitive(self, setup_server,
                                           qualcoder_db_path):
        """TEXT UNIQUE under SQLite's BINARY collation, so "AI Agent" and
        "AI agent" are two coders upstream: our exact comparison (X2) is
        the matching rule, and the casefold rule for code, category and
        case names must not be extended to coder names."""
        con = sqlite3.connect(str(Path(qualcoder_db_path) / "data.qda"))
        con.executescript(self.CODER_NAMES_DDL)
        con.execute("INSERT INTO coder_names (name) VALUES ('AI Agent')")
        con.execute("INSERT INTO coder_names (name) VALUES ('AI agent')")
        con.commit()
        n = con.execute("SELECT COUNT(*) FROM coder_names").fetchone()[0]
        con.close()
        assert n == 2


# =============================================================================
# 10.3 ASK-ONCE BEHAVIOUR
# =============================================================================

EXPECTED_ASK = (
    "No AI coder name is set for this project yet, so nothing was written. "
    "Ask the user which coder name this project's AI codings and other AI "
    "writes should be stored under, then call set_project_ai_coder_name "
    "with their answer and retry. The name is free text; a model name such "
    "as \"Qwen 3.8 6bit\" is a good choice, because codings by different "
    "models can then be compared later. Quick picks: \"AI Coding "
    "Assistant\" (this server's built-in default), \"AI Agent\" (the name "
    "QualCoder 4.0's built-in assistant uses). The name can be changed at "
    "any time with the same tool; earlier rows keep the name they were "
    "written under.")


class TestTheAsk:

    @pytest.mark.parametrize("tool", sorted(ASK_WRITES))
    def test_every_write_tool_asks_once_and_writes_nothing(
            self, setup_server_unset, qualcoder_db_path, tool):
        counts_before = {
            t: _row(qualcoder_db_path, f"SELECT COUNT(*) AS n FROM {t}")["n"]
            for t in ("code_name", "code_cat", "cases", "journal",
                      "annotation", "source", "attribute", "attribute_type",
                      "case_text", "code_text")}
        raw = ASK_WRITES[tool](server)
        out = json.loads(raw)
        assert out["error"] == EXPECTED_ASK, tool
        assert out["action_required"] == "set_project_ai_coder_name"
        assert out["ai_coder_name"] is None
        assert out["free_text_allowed"] is True
        assert out["quick_picks"] == [DEFAULT_AI_CODER_NAME,
                                      KNOWN_AI_ASSISTANT_OWNER]
        for table, before in counts_before.items():
            assert _row(qualcoder_db_path,
                        f"SELECT COUNT(*) AS n FROM {table}")["n"] == before, \
                (tool, table)
        assert _backup_folders(qualcoder_db_path) == []
        assert not _sidecar(qualcoder_db_path).exists()

    def test_apply_codings_asks_and_keeps_the_approvals(
            self, setup_server_unset, qualcoder_db_path):
        session = _approved_session(server, qualcoder_db_path)
        out = json.loads(server.apply_codings(session.session_id,
                                              create_backup=False))
        assert out["error"] == EXPECTED_ASK
        reloaded = server.session_manager.load_session(session.session_id)
        assert len(reloaded.filter_by_status("approved")) == 1
        assert _row(qualcoder_db_path,
                    "SELECT COUNT(*) AS n FROM code_text")["n"] == 2

    def test_create_proposed_codes_asks_and_keeps_the_proposals(
            self, setup_server_unset, qualcoder_db_path):
        from qualcoder_mcp.sessions import AICodingSession, ProposedCode
        session = AICodingSession(project_path=qualcoder_db_path,
                                  description="proposals")
        proposal = ProposedCode(name="Proposed One")
        proposal.status = "approved"
        session.add_proposal(proposal)
        server.session_manager.save_session(session)
        out = json.loads(server.create_proposed_codes(session.session_id,
                                                      create_backup=False))
        assert out["error"] == EXPECTED_ASK
        reloaded = server.session_manager.load_session(session.session_id)
        assert reloaded.proposed_codes[0].status == "approved"

    def test_the_eleven_are_exactly_the_tools_that_resolve_an_owner(self):
        """The drift guard the old constant's name was pretending to be.

        ELEVEN_WRITES held nine entries while its comment said the ask
        was pinned on all eleven (QA round 1, F9). The two the
        parametrisation cannot carry are pinned by their own tests above,
        and a twelfth write tool would land uncovered in silence, so the
        list is now derived from the source: every function that calls
        `_resolve_write_owner` must be one of the eleven.
        """
        import ast
        source = (Path(server.__file__)).read_text(encoding="utf-8")
        resolving = set()
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Name)
                        and inner.func.id == "_resolve_write_owner"):
                    resolving.add(node.name)
                    break
        assert resolving == set(ELEVEN_WRITE_TOOLS), (
            resolving ^ set(ELEVEN_WRITE_TOOLS))
        assert len(ELEVEN_WRITE_TOOLS) == 11

    def test_the_ask_repeats_byte_identically(self, setup_server_unset,
                                              qualcoder_db_path):
        answers = [server.create_code("Repeated", create_backup=False)
                   for _ in range(3)]
        assert answers[0] == answers[1] == answers[2]

    def test_after_the_answer_every_tool_proceeds_under_the_name(
            self, setup_server_unset, qualcoder_db_path):
        out = json.loads(server.set_project_ai_coder_name("Qwen 3.8 6bit"))
        assert out["success"] is True
        server.create_code("AfterCode", create_backup=False)
        server.create_category("AfterCat", create_backup=False)
        server.create_case("AfterCase", create_backup=False)
        server.add_journal_entry("AfterJournal", "t", create_backup=False)
        server.add_annotation(1, 0, 4, "note", create_backup=False)
        for sql in ("SELECT owner FROM code_name WHERE name='AfterCode'",
                    "SELECT owner FROM code_cat WHERE name='AfterCat'",
                    "SELECT owner FROM cases WHERE name='AfterCase'",
                    "SELECT owner FROM journal WHERE name='AfterJournal'",
                    "SELECT owner FROM annotation WHERE memo='note'"):
            assert _row(qualcoder_db_path, sql)["owner"] == "Qwen 3.8 6bit", sql

    def test_the_ask_names_an_existing_known_ai_name_first(
            self, setup_server_unset, qualcoder_db_path):
        con = sqlite3.connect(str(Path(qualcoder_db_path) / "data.qda"))
        con.execute("INSERT INTO code_name (cid,name,memo,catid,owner,date,"
                    "color) VALUES (90,'Legacy','',NULL,?,'2024-01-15',"
                    "'#FF0000')", (KNOWN_AI_ASSISTANT_OWNER,))
        con.commit()
        con.close()
        _reopen(qualcoder_db_path)
        out = json.loads(server.create_code("X", create_backup=False))
        assert out["existing_ai_coder_names_in_project"] == \
            [KNOWN_AI_ASSISTANT_OWNER]
        assert out["quick_picks"][0] == KNOWN_AI_ASSISTANT_OWNER
        assert (f"This project already holds rows under "
                f"\"{KNOWN_AI_ASSISTANT_OWNER}\"; choosing that name keeps "
                f"them together.") in out["error"]

    def test_the_ask_is_count_free_and_content_free(
            self, setup_server_unset, qualcoder_db_path):
        """The leak invariant: on a project with a hidden coder and a
        private memo zone, the ask discloses neither."""
        con = sqlite3.connect(str(Path(qualcoder_db_path) / "data.qda"))
        con.executescript(TestUpstreamParity.CODER_NAMES_DDL)
        con.execute("INSERT INTO coder_names (name, visibility) "
                    "VALUES ('Hidden Coder', 0)")
        con.execute("UPDATE code_text SET memo = ? WHERE ctid = 1",
                    ("public part ##### private part",))
        con.commit()
        con.close()
        _reopen(qualcoder_db_path)
        text = server.create_code("Leaky", create_backup=False)
        assert "Hidden Coder" not in text
        assert "#####" not in text
        assert "private part" not in text

    @pytest.mark.parametrize("read", sorted(READS_THAT_MUST_NEVER_ASK))
    def test_reads_never_ask(self, setup_server_unset, qualcoder_db_path,
                             read):
        raw = READS_THAT_MUST_NEVER_ASK[read](server)
        # The surface has to have answered: the parameter this replaced
        # asserted on a literal "{}" it produced itself, so it could not
        # fail whatever the code did (QA round 1, F10).
        assert isinstance(raw, str) and raw, read
        assert json.loads(raw) != {}, read
        assert "set_project_ai_coder_name" not in raw or \
            "action_required" not in raw, read

    def test_every_static_resource_is_in_that_matrix(self):
        """B1.17 asks for reads AND resources. The matrix is keyed by
        name so a failure says which surface asked, and this test says
        the resource half is actually there: the parametrisation used to
        carry an anonymous lambda for a tool that does not exist
        (`hasattr(s, "list_codes")` is False), which asserted on the
        literal string "{}" and could never fail (QA round 1, F10)."""
        static = {"get_project_info", "list_all_codes", "list_all_categories",
                  "list_all_files", "list_all_cases", "get_journal_entries"}
        assert static <= set(READS_THAT_MUST_NEVER_ASK), \
            static - set(READS_THAT_MUST_NEVER_ASK)
        for name in static:
            assert callable(getattr(server, name)), name

    def test_the_export_never_asks(self, setup_server_unset, tmp_path,
                                   monkeypatch):
        monkeypatch.delenv(AI_CODER_NAME_ENV, raising=False)
        out = json.loads(server.export_refi_qda(
            output_path=str(tmp_path / "unset.qdpx")))
        assert out.get("success") is True, out
        assert out["ai_user_name_source"] == "built_in_default"


# =============================================================================
# 10.4 PRECEDENCE AND THE c2 RULE
# =============================================================================

class TestMismatchRule:

    @pytest.mark.parametrize("env,current,expected", [
        (None, {"name": "A", "host_declaration": None}, False),
        ("A", {"name": "A", "host_declaration": None}, False),
        ("B", {"name": "A", "host_declaration": "B"}, False),
        ("B", {"name": "A", "host_declaration": None}, True),
        ("B", {"name": "A", "host_declaration": "C"}, True),
    ])
    def test_the_pure_rule(self, env, current, expected):
        assert ps.mismatch(env, current) is expected

    def test_end_to_end_declare_refuse_switch_and_stop_nagging(
            self, setup_server_unset, qualcoder_db_path, monkeypatch):
        monkeypatch.delenv(AI_CODER_NAME_ENV, raising=False)
        json.loads(server.set_project_ai_coder_name("A"))
        monkeypatch.setenv(AI_CODER_NAME_ENV, "B")
        out = json.loads(server.create_code("Blocked", create_backup=False))
        assert out["error"] == (
            "This host declares the AI coder name \"B\" "
            "(QUALCODER_MCP_AI_CODER_NAME), but this project's current AI "
            "coder name is \"A\". Nothing was written. Ask the user which "
            "name to use here, then call set_project_ai_coder_name with "
            "\"B\" to switch the project to it, or with \"A\" to keep it "
            "(that records the choice, and this host will not ask again "
            "while its declaration stays the same). Earlier rows keep the "
            "name they were written under.")
        assert out["quick_picks"] == ["B", "A"]
        assert _row(qualcoder_db_path, "SELECT COUNT(*) AS n FROM code_name "
                                       "WHERE name='Blocked'")["n"] == 0

        out = json.loads(server.set_project_ai_coder_name("B"))
        assert out["previous_name"] == "A"
        assert out["names_used_count"] == 2
        server.create_code("Unblocked", create_backup=False)
        assert _row(qualcoder_db_path,
                    "SELECT owner FROM code_name WHERE name='Unblocked'"
                    )["owner"] == "B"

        monkeypatch.delenv(AI_CODER_NAME_ENV, raising=False)
        server.create_code("Undeclared", create_backup=False)
        assert _row(qualcoder_db_path,
                    "SELECT owner FROM code_name WHERE name='Undeclared'"
                    )["owner"] == "B"

    def test_acknowledging_a_declaration_by_keeping_the_project_name(
            self, setup_server_unset, qualcoder_db_path, monkeypatch):
        monkeypatch.delenv(AI_CODER_NAME_ENV, raising=False)
        server.set_project_ai_coder_name("A")
        monkeypatch.setenv(AI_CODER_NAME_ENV, "B")
        out = json.loads(server.set_project_ai_coder_name("A"))
        assert out["ai_coder_name"]["host_declaration"] == "B"
        out = json.loads(server.create_code("Acked", create_backup=False))
        assert out.get("success") is True, out
        monkeypatch.setenv(AI_CODER_NAME_ENV, "C")
        out = json.loads(server.create_code("Nagged", create_backup=False))
        assert "error" in out
        assert out["host_declared_ai_coder_name"] == "C"

    def test_the_ask_carries_the_declaration_clause(
            self, setup_server_unset, qualcoder_db_path, monkeypatch):
        monkeypatch.setenv(AI_CODER_NAME_ENV, "Qwen 3.8 6bit")
        out = json.loads(server.create_code("Declared", create_backup=False))
        assert ("\"Qwen 3.8 6bit\" (declared in this host's server "
                "configuration)") in out["error"]
        assert out["quick_picks"][0] == "Qwen 3.8 6bit"

    def test_fixed_project_mode_changes_nothing(self, setup_server_unset,
                                                qualcoder_db_path,
                                                monkeypatch):
        monkeypatch.setenv("QUALCODER_PROJECT_PATH", qualcoder_db_path)
        out = json.loads(server.create_code("Fixed", create_backup=False))
        assert out["error"] == EXPECTED_ASK


# =============================================================================
# 10.5 SWITCHING AND HISTORY
# =============================================================================

class TestSwitchingAndHistory:

    def test_set_a_set_b_set_b_again(self, setup_server_unset,
                                     qualcoder_db_path):
        server.set_project_ai_coder_name("A")
        server.set_project_ai_coder_name("B")
        out = json.loads(server.set_project_ai_coder_name("B"))
        assert out["previous_name"] == "B"
        assert out["names_used_count"] == 2      # distinct names used
        assert "Unchanged; recorded again." in out["warnings"]
        state = read_sidecar(qualcoder_db_path)
        assert state.name == "B"
        assert [e["name"] for e in state.history] == ["A", "B", "B"]

    def test_rows_written_under_the_old_name_keep_it(
            self, setup_server_unset, qualcoder_db_path):
        server.set_project_ai_coder_name("A")
        server.create_code("UnderA", create_backup=False)
        server.set_project_ai_coder_name("B")
        server.create_code("UnderB", create_backup=False)
        assert _row(qualcoder_db_path,
                    "SELECT owner FROM code_name WHERE name='UnderA'"
                    )["owner"] == "A"
        assert _row(qualcoder_db_path,
                    "SELECT owner FROM code_name WHERE name='UnderB'"
                    )["owner"] == "B"

    def test_history_is_capped_at_two_hundred(self, setup_server_unset,
                                              qualcoder_db_path):
        for i in range(205):
            write_ai_coder_name(qualcoder_db_path, f"Name {i:03d}")
        state = read_sidecar(qualcoder_db_path)
        assert len(state.history) == 200
        assert state.history[-1]["name"] == "Name 204"
        assert state.name == "Name 204"
        assert state.history[0]["name"] == "Name 005"   # oldest dropped

    def test_unknown_top_level_keys_survive_a_set(self, setup_server_unset,
                                                  qualcoder_db_path):
        write_ai_coder_name(qualcoder_db_path, "A")
        path = _sidecar(qualcoder_db_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["researcher_note"] = "do not delete"
        data["ai_coder_name"]["unknown_inside"] = "dropped"
        path.write_text(json.dumps(data), encoding="utf-8")
        write_ai_coder_name(qualcoder_db_path, "B")
        after = json.loads(path.read_text(encoding="utf-8"))
        assert after["researcher_note"] == "do not delete"
        assert "unknown_inside" not in after["ai_coder_name"]
        assert after["written_by"].startswith("qualcoder-mcp ")
        assert after["updated"] == after["ai_coder_name"]["set_at"]

    def test_get_current_project_echoes_twenty_entries_and_the_total(
            self, setup_server_unset, qualcoder_db_path):
        for i in range(25):
            write_ai_coder_name(qualcoder_db_path, f"N{i:02d}")
        out = json.loads(server.get_current_project())
        assert out["ai_coder_names_used_total"] == 25
        assert len(out["ai_coder_names_used"]) == 20
        assert out["ai_coder_names_used"][-1]["name"] == "N24"
        assert out["ai_coder_name"]["source"] == "project"
        assert out["ai_coder_name"]["name"] == "N24"

    def test_get_project_summary_carries_one_string(self, setup_server,
                                                    qualcoder_db_path):
        out = json.loads(server.get_project_summary())
        assert out["project_info"]["ai_coder_name"] == DEFAULT_AI_CODER_NAME

    def test_select_project_reports_the_state(self, setup_server,
                                              qualcoder_db_path):
        out = json.loads(server.select_project(qualcoder_db_path))
        assert out["ai_coder_name"]["name"] == DEFAULT_AI_CODER_NAME
        assert out["ai_coder_name_mismatch"] is False
        assert out["ai_coder_name_rows_present"] in (True, False)

    def test_an_unset_project_reports_the_hint_not_a_refusal(
            self, setup_server_unset, qualcoder_db_path):
        out = json.loads(server.get_current_project())
        assert out["ai_coder_name"] == {
            "name": None, "source": "unset", "hint": ps.UNSET_HINT}
        assert "error" not in out


# =============================================================================
# 10.6 SETTER VALIDATION AND WARNINGS
# =============================================================================

class TestSetterValidation:

    @pytest.mark.parametrize("bad", [
        "AI\nAgent", "AI\tAgent", "AI Agent", "AI‮Agent",
        "x" * 81, "   ", "AI ##### Agent",
    ])
    def test_bad_names_refused(self, setup_server, qualcoder_db_path, bad):
        out = json.loads(server.set_project_ai_coder_name(bad))
        assert "error" in out
        assert read_sidecar(qualcoder_db_path).name == DEFAULT_AI_CODER_NAME

    def test_a_non_string_name_is_refused(self, setup_server):
        out = json.loads(server.set_project_ai_coder_name(17))
        assert out["error"] == "name must be a string"

    @pytest.mark.parametrize("bad_note", [
        "note\nwith newline", "x" * 501, "carries ##### a marker",
    ])
    def test_bad_notes_refused(self, setup_server, qualcoder_db_path,
                               bad_note):
        out = json.loads(server.set_project_ai_coder_name("Fine", bad_note))
        assert "error" in out
        assert "note" in out["error"]
        assert read_sidecar(qualcoder_db_path).name == DEFAULT_AI_CODER_NAME

    def test_the_projects_own_coder_name_is_refused(self, setup_server,
                                                    qualcoder_db_path):
        codername = server.db.get_project_info()["coder_name"]
        out = json.loads(server.set_project_ai_coder_name(codername))
        assert out["error"] == (
            f"\"{codername}\" is this project's own coder name, so AI rows "
            f"would be indistinguishable from the user's in QualCoder's "
            f"coder lists, visibility toggle, undo and reports. Choose a "
            f"different name. Nothing was changed.")

    def test_the_literal_default_is_refused(self, setup_server):
        out = json.loads(server.set_project_ai_coder_name("default"))
        assert out["error"] == (
            "\"default\" is QualCoder's own default coder name for any "
            "user who has not set one (it would collide with them); "
            "choose a different name. Nothing was changed.")

    def test_a_hidden_coder_name_needs_the_override(self, setup_server,
                                                    qualcoder_db_path):
        from test_qc40_visibility import _apply_visibility_schema, HIDDEN
        _apply_visibility_schema(qualcoder_db_path)
        _reopen(qualcoder_db_path)
        out = json.loads(server.set_project_ai_coder_name(HIDDEN))
        assert out["error"] == (
            f"\"{HIDDEN}\" is a coder currently hidden in QualCoder; rows "
            f"written under it would not be shown in QualCoder or in this "
            f"server's default reads. Pass allow_hidden_coder=true to "
            f"store it anyway, or ask the user to unhide the coder in "
            f"QualCoder. Nothing was changed.")
        assert read_sidecar(qualcoder_db_path).name == DEFAULT_AI_CODER_NAME

        out = json.loads(server.set_project_ai_coder_name(
            HIDDEN, allow_hidden_coder=True))
        assert out["success"] is True
        assert read_sidecar(qualcoder_db_path).name == HIDDEN

    def test_without_the_capability_no_hidden_check_runs(
            self, setup_server, qualcoder_db_path):
        """Capability probe, never a version string: a project with no
        coder_names table has nothing to hide, so the same name is
        accepted without the override."""
        assert server.db.capabilities.has_coder_visibility is False
        out = json.loads(server.set_project_ai_coder_name("Hidden Coder"))
        assert out["success"] is True

    def test_an_existing_owner_earns_a_warning_not_a_refusal(
            self, setup_server, qualcoder_db_path):
        con = sqlite3.connect(str(Path(qualcoder_db_path) / "data.qda"))
        con.execute("INSERT INTO journal (jid,name,jentry,date,owner) "
                    "VALUES (90,'Maria journal','x','2024-01-15','Maria')")
        con.commit()
        con.close()
        _reopen(qualcoder_db_path)
        out = json.loads(server.set_project_ai_coder_name("Maria"))
        assert out["success"] is True
        assert ("Rows already exist under this name in this project; if it "
                "is a person's coder name, choose another."
                ) in out["warnings"]
        assert "hidden" not in " ".join(out["warnings"]).lower()

    def test_a_case_only_difference_earns_a_warning(self, setup_server,
                                                    qualcoder_db_path):
        out = json.loads(server.set_project_ai_coder_name(
            DEFAULT_AI_CODER_NAME.lower()))
        assert out["success"] is True
        assert any("differs only by letter case" in w
                   for w in out["warnings"]), out["warnings"]

    def test_a_hidden_coders_spelling_is_not_echoed_by_the_warning(
            self, setup_server, qualcoder_db_path):
        """A hidden coder is disclosed as a count, never a name (7.1), so
        the case-only warning names the other spelling only when the
        coder it belongs to is visible."""
        from test_qc40_visibility import _apply_visibility_schema, HIDDEN
        _apply_visibility_schema(qualcoder_db_path)
        _reopen(qualcoder_db_path)
        out = json.loads(server.set_project_ai_coder_name(HIDDEN.lower()))
        assert out["success"] is True
        joined = " ".join(out["warnings"])
        assert "differs only by letter case" in joined
        assert HIDDEN not in joined

    def test_the_result_shape_and_the_round_trip(self, setup_server_unset,
                                                 qualcoder_db_path):
        out = json.loads(server.set_project_ai_coder_name(
            "Qwen 3.8 6bit", "LM Studio 0.4.22"))
        assert out["success"] is True
        assert out["ai_coder_name"]["name"] == "Qwen 3.8 6bit"
        assert out["ai_coder_name"]["note"] == "LM Studio 0.4.22"
        assert out["previous_name"] is None
        assert out["names_used_count"] == 1
        assert out["stored_in"] == str(_sidecar(qualcoder_db_path))
        assert out["warnings"] == []
        assert out["next"] == ("Retry the write that was refused; it will "
                               "now be attributed to \"Qwen 3.8 6bit\".")
        state = read_sidecar(qualcoder_db_path)
        assert state.entry["name"] == out["ai_coder_name"]["name"]
        assert state.entry["set_at"] == out["ai_coder_name"]["set_at"]

    def test_the_setter_needs_a_project(self, monkeypatch):
        monkeypatch.setattr(server, "db", None)
        monkeypatch.setattr(server, "current_project_path", None)
        monkeypatch.delenv("QUALCODER_PROJECT_PATH", raising=False)
        out = json.loads(server.set_project_ai_coder_name("X"))
        assert "No Qualcoder project selected" in out["error"]


# =============================================================================
# 10.7 SIDECAR ROBUSTNESS (READER)
# =============================================================================

class TestSidecarReader:

    def _corrupt(self, project_path, payload: bytes):
        _sidecar(project_path).write_bytes(payload)

    @pytest.mark.parametrize("payload", [
        b"not json at all",
        b"[]",
        b"\xff\xfe\x00garbage",
        json.dumps({"format": "something-else", "format_version": 1}).encode(),
        json.dumps({"format": "qualcoder-mcp-project",
                    "format_version": 1,
                    "ai_coder_name": {"name": "bad\nname"}}).encode(),
        json.dumps({"format": "qualcoder-mcp-project",
                    "format_version": 1,
                    "ai_coder_name": "a string, not an object"}).encode(),
        json.dumps({"format": "qualcoder-mcp-project",
                    "format_version": 1,
                    "ai_coder_name": {"name": "Fine",
                                      "note": "has ##### marker"}}).encode(),
    ], ids=["not-json", "not-an-object", "not-utf8", "wrong-format",
            "bad-name", "entry-not-an-object", "bad-note"])
    def test_unreadable_states(self, setup_server, qualcoder_db_path,
                               payload):
        self._corrupt(qualcoder_db_path, payload)
        before = _sidecar(qualcoder_db_path).read_bytes()
        assert read_sidecar(qualcoder_db_path).status == "unreadable"
        out = json.loads(server.create_code("Blocked", create_backup=False))
        assert out["error"] == ps.UNREADABLE_MESSAGE
        report = json.loads(server.get_current_project())
        assert report["ai_coder_name"] == {
            "name": None, "source": "unreadable",
            "hint": ps.UNREADABLE_MESSAGE}
        # Never repaired by a WRITE: an ordinary owner-bearing write
        # leaves the file exactly as it found it.
        assert _sidecar(qualcoder_db_path).read_bytes() == before
        # The setter is the researcher's route back (fix round 4). It is
        # not silent and it destroys nothing: the old bytes are renamed,
        # and the result names the file they are in.
        out = json.loads(server.set_project_ai_coder_name("Anything"))
        assert out.get("success") is True, out
        kept = Path(out["replaced_unreadable_file"])
        assert kept.read_bytes() == before
        assert any("could not be read" in w for w in out["warnings"]), out
        assert read_sidecar(qualcoder_db_path).name == "Anything"

    def test_an_oversized_file_is_unreadable(self, setup_server,
                                             qualcoder_db_path):
        payload = json.dumps({
            "format": "qualcoder-mcp-project", "format_version": 1,
            "padding": "x" * (ps.SIDECAR_READ_MAX_BYTES + 1),
            "ai_coder_name": {"name": "Fine"}})
        self._corrupt(qualcoder_db_path, payload.encode())
        assert read_sidecar(qualcoder_db_path).status == "unreadable"

    def test_a_newer_format_reports_but_refuses_to_write(
            self, setup_server, qualcoder_db_path):
        self._corrupt(qualcoder_db_path, json.dumps({
            "format": "qualcoder-mcp-project", "format_version": 2,
            "ai_coder_name": {"name": "From The Future",
                              "set_at": "2027-01-01T00:00:00+00:00",
                              "note": "", "host_declaration": None},
        }).encode())
        state = read_sidecar(qualcoder_db_path)
        assert state.status == "newer_format"
        assert state.name == "From The Future"
        out = json.loads(server.create_code("Blocked", create_backup=False))
        assert out["error"] == ps.NEWER_FORMAT_MESSAGE
        out = json.loads(server.set_project_ai_coder_name("Anything"))
        assert out["error"] == ps.NEWER_FORMAT_MESSAGE
        report = json.loads(server.get_current_project())
        assert report["ai_coder_name"]["name"] == "From The Future"
        assert report["ai_coder_name"]["source"] == "newer_format"

    def test_a_garbage_timestamp_is_dropped_not_echoed(
            self, setup_server, qualcoder_db_path):
        self._corrupt(qualcoder_db_path, json.dumps({
            "format": "qualcoder-mcp-project", "format_version": 1,
            "ai_coder_name": {"name": "Fine", "set_at": "yesterday-ish",
                              "note": "", "host_declaration": None},
        }).encode())
        state = read_sidecar(qualcoder_db_path)
        assert state.is_set
        assert state.entry["set_at"] is None

    def test_a_malformed_history_costs_the_history_not_the_name(
            self, setup_server, qualcoder_db_path):
        self._corrupt(qualcoder_db_path, json.dumps({
            "format": "qualcoder-mcp-project", "format_version": 1,
            "ai_coder_name": {"name": "Current", "set_at": None, "note": "",
                              "host_declaration": None},
            "ai_coder_name_history": ["not an entry", 17],
        }).encode())
        state = read_sidecar(qualcoder_db_path)
        assert state.name == "Current"
        assert state.history == []
        out = json.loads(server.get_current_project())
        assert out["ai_coder_names_used_total"] == 0
        # the next set rebuilds it
        json.loads(server.set_project_ai_coder_name("Next"))
        assert [e["name"] for e in
                read_sidecar(qualcoder_db_path).history] == ["Next"]

    def test_a_missing_file_is_unset(self, setup_server_unset,
                                     qualcoder_db_path):
        assert read_sidecar(qualcoder_db_path).status == "unset"

    def test_a_bom_and_crlf_are_tolerated(self, setup_server,
                                          qualcoder_db_path):
        """Windows editors add both; the sidecar is meant to be
        hand-editable, so it accepts them (ruling 12). The MRU reader
        stays strict."""
        payload = json.dumps({
            "format": "qualcoder-mcp-project", "format_version": 1,
            "ai_coder_name": {"name": "Hand Edited", "set_at": None,
                              "note": "", "host_declaration": None},
        }, indent=2).replace("\n", "\r\n")
        _sidecar(qualcoder_db_path).write_bytes(
            b"\xef\xbb\xbf" + payload.encode("utf-8"))
        assert read_sidecar(qualcoder_db_path).name == "Hand Edited"


# =============================================================================
# 10.8 SIDECAR WRITE ATOMICITY
# =============================================================================

class TestSidecarWriter:

    def test_no_temp_litter_after_a_successful_write(self, setup_server_unset,
                                                    qualcoder_db_path):
        write_ai_coder_name(qualcoder_db_path, "A")
        write_ai_coder_name(qualcoder_db_path, "B")
        leftovers = list(Path(qualcoder_db_path).glob(f"{SIDECAR_NAME}.*"))
        assert leftovers == []

    def test_a_failed_replace_leaves_the_old_file_and_no_temp(
            self, setup_server, qualcoder_db_path, monkeypatch):
        before = _sidecar(qualcoder_db_path).read_bytes()

        def boom(src, dst):
            raise OSError("no space left on device")

        monkeypatch.setattr(ps.os, "replace", boom)
        with pytest.raises(ps.SidecarWriteError):
            write_ai_coder_name(qualcoder_db_path, "Never Stored")
        assert _sidecar(qualcoder_db_path).read_bytes() == before
        assert list(Path(qualcoder_db_path).glob(f"{SIDECAR_NAME}.*")) == []

    def test_the_setter_reports_a_write_failure_without_changing_anything(
            self, setup_server, qualcoder_db_path, monkeypatch):
        before = _sidecar(qualcoder_db_path).read_bytes()
        monkeypatch.setattr(ps.os, "replace",
                            lambda src, dst: (_ for _ in ()).throw(
                                OSError("disk full")))
        out = json.loads(server.set_project_ai_coder_name("Doomed"))
        assert "error" in out
        assert "Nothing was changed." in out["error"]
        assert _sidecar(qualcoder_db_path).read_bytes() == before

    def test_fsync_is_called_once_per_write(self, setup_server_unset,
                                            qualcoder_db_path, monkeypatch):
        calls = []
        real = ps.os.fsync
        monkeypatch.setattr(ps.os, "fsync",
                            lambda fd: (calls.append(fd), real(fd))[1])
        write_ai_coder_name(qualcoder_db_path, "Synced")
        assert len(calls) == 1

    def test_temp_names_are_unique(self, setup_server_unset,
                                   qualcoder_db_path, monkeypatch):
        seen = []
        real = ps.tempfile.mkstemp

        def spy(**kwargs):
            fd, name = real(**kwargs)
            seen.append(name)
            return fd, name

        monkeypatch.setattr(ps.tempfile, "mkstemp", spy)
        for i in range(5):
            write_ai_coder_name(qualcoder_db_path, f"N{i}")
        assert len(set(seen)) == 5

    @POSIX_ONLY
    def test_a_symlinked_sidecar_is_refused_for_reading_and_writing(
            self, setup_server, qualcoder_db_path, tmp_path):
        """The link points at a PERFECTLY VALID sidecar outside the
        project, which is the case that matters: without the lstat
        refusal the read would take a name from a file the project does
        not own, and the write would follow the link and overwrite it.
        A link to garbage would fail on the JSON parse instead and pin
        nothing.
        """
        outside = tmp_path / "outside.json"
        outside.write_text(json.dumps({
            "format": "qualcoder-mcp-project", "format_version": 1,
            "ai_coder_name": {"name": "Outside The Project", "set_at": None,
                              "note": "", "host_declaration": None},
        }), encoding="utf-8")
        original_bytes = outside.read_bytes()
        path = _sidecar(qualcoder_db_path)
        path.unlink()
        path.symlink_to(outside)
        state = read_sidecar(qualcoder_db_path)
        assert state.status == "unreadable"
        assert state.name is None
        # The write tools refuse rather than writing under a name that
        # came from outside the project.
        out = json.loads(server.create_code("Linked", create_backup=False))
        assert out["error"] == ps.UNREADABLE_MESSAGE
        assert outside.read_bytes() == original_bytes
        # The setter clears it (fix round 4). The LINK is what moves
        # aside, so the file it pointed at is neither followed nor
        # touched, and the new sidecar is a real file of ours.
        out = json.loads(server.set_project_ai_coder_name("Through The Link"))
        assert out.get("success") is True, out
        assert outside.read_bytes() == original_bytes
        kept = Path(out["replaced_unreadable_file"])
        assert kept.is_symlink()
        assert Path(os.readlink(kept)) == outside
        assert not path.is_symlink()
        assert read_sidecar(qualcoder_db_path).name == "Through The Link"

    @POSIX_ONLY
    def test_mode_bits_follow_data_qda(self, setup_server_unset,
                                       qualcoder_db_path):
        db_file = Path(qualcoder_db_path) / "data.qda"
        os.chmod(db_file, 0o644)
        write_ai_coder_name(qualcoder_db_path, "Group Readable")
        mode = stat.S_IMODE(os.stat(_sidecar(qualcoder_db_path)).st_mode)
        assert mode == 0o644

    @POSIX_ONLY
    def test_no_execute_bit_is_ever_inherited(self, setup_server_unset,
                                              qualcoder_db_path):
        db_file = Path(qualcoder_db_path) / "data.qda"
        os.chmod(db_file, 0o755)
        write_ai_coder_name(qualcoder_db_path, "Executable Database")
        mode = stat.S_IMODE(os.stat(_sidecar(qualcoder_db_path)).st_mode)
        assert mode == 0o644
        assert not mode & 0o111

    def test_the_file_is_utf8_and_not_ascii_escaped(self, setup_server_unset,
                                                    qualcoder_db_path):
        write_ai_coder_name(qualcoder_db_path, "研究助手")
        raw = _sidecar(qualcoder_db_path).read_bytes()
        assert "研究助手".encode("utf-8") in raw
        assert read_sidecar(qualcoder_db_path).name == "研究助手"


# =============================================================================
# 10.9 READ-ONLY FOLDER
# =============================================================================

@POSIX_ONLY
class TestReadOnlyFolder:

    @pytest.fixture
    def read_only_project(self, qualcoder_db_path):
        folder = Path(qualcoder_db_path)
        original = stat.S_IMODE(os.stat(folder).st_mode)
        os.chmod(folder, 0o500)
        yield qualcoder_db_path
        os.chmod(folder, original)

    def test_the_setter_refuses_with_no_fallback(self, setup_server_unset,
                                                 read_only_project):
        out = json.loads(server.set_project_ai_coder_name("Nowhere"))
        assert out["error"] == ps.READ_ONLY_FOLDER_MESSAGE
        assert not _sidecar(read_only_project).exists()

    def test_an_existing_setting_is_still_read(self, setup_server,
                                              qualcoder_db_path):
        folder = Path(qualcoder_db_path)
        original = stat.S_IMODE(os.stat(folder).st_mode)
        os.chmod(folder, 0o500)
        try:
            assert read_sidecar(qualcoder_db_path).name == \
                DEFAULT_AI_CODER_NAME
            out = json.loads(server.set_project_ai_coder_name("Changed"))
            assert out["error"] == ps.READ_ONLY_FOLDER_MESSAGE
        finally:
            os.chmod(folder, original)


# =============================================================================
# 10.10 THE OWNER ARGUMENT
# =============================================================================

EXPECTED_OWNER_REFUSAL = (
    "The owner argument no longer chooses the coder name: this project's "
    "AI coder name is \"{name}\" and every row this server writes is "
    "stored under it, so that AI work stays distinguishable from the "
    "researcher's and from other coders'. Omit owner, or, if the user "
    "wants a different attribution, ask them and change the project's AI "
    "coder name with set_project_ai_coder_name, then call this tool again "
    "without owner. A human coder's name is never used for rows this "
    "server writes. Nothing was written and no backup was made.")


class TestOwnerArgument:

    def test_the_exact_refusal_text(self, setup_server, qualcoder_db_path):
        out = json.loads(server.import_text_file(
            "x.txt", "Body.", owner="Someone Else"))
        assert out["error"] == EXPECTED_OWNER_REFUSAL.format(
            name=DEFAULT_AI_CODER_NAME)
        assert out["action_required"] == \
            "omit_owner_or_set_project_ai_coder_name"
        assert out["ai_coder_name"] == DEFAULT_AI_CODER_NAME
        assert "Someone Else" not in json.dumps(out)
        assert _backup_folders(qualcoder_db_path) == []

    def test_equal_to_the_setting_is_accepted(self, setup_server,
                                              qualcoder_db_path):
        out = json.loads(server.import_text_file(
            "same.txt", "Body.", owner=DEFAULT_AI_CODER_NAME,
            create_backup=False))
        assert out["success"] is True

    def test_a_history_name_is_not_accepted(self, setup_server_unset,
                                            qualcoder_db_path):
        server.set_project_ai_coder_name("A")
        server.set_project_ai_coder_name("B")
        out = json.loads(server.import_text_file(
            "hist.txt", "Body.", owner="A", create_backup=False))
        assert out["error"] == EXPECTED_OWNER_REFUSAL.format(name="B")

    @pytest.mark.parametrize("bad", ["Test\nCoder\x01", "x" * 5000,
                                     "AI ##### Agent", "   "])
    def test_validation_still_comes_first(self, setup_server, bad):
        """R1: a hostile owner gets its specific validation message, not
        the restriction refusal, so the S-H3 step 1 pins keep holding."""
        out = json.loads(server.import_text_file(
            "hostile.txt", "Body.", owner=bad, create_backup=False))
        assert "owner" in out["error"]
        assert "no longer chooses" not in out["error"]

    def test_an_unset_project_asks_and_says_the_owner_was_not_applied(
            self, setup_server_unset, qualcoder_db_path):
        out = json.loads(server.import_text_file(
            "askowner.txt", "Body.", owner="Someone Else",
            create_backup=False))
        assert out["error"] == EXPECTED_ASK + (
            " The owner argument you passed was not applied; the name is "
            "the user's to choose.")
        assert "Someone Else" not in json.dumps(out)

    def test_apply_codings_refuses_before_the_backup(self, setup_server,
                                                    qualcoder_db_path):
        session = _approved_session(server, qualcoder_db_path)
        out = json.loads(server.apply_codings(
            session.session_id, owner="Someone Else"))
        assert out["error"] == EXPECTED_OWNER_REFUSAL.format(
            name=DEFAULT_AI_CODER_NAME)
        assert _backup_folders(qualcoder_db_path) == []
        reloaded = server.session_manager.load_session(session.session_id)
        assert len(reloaded.filter_by_status("approved")) == 1

    def test_the_deprecation_is_in_both_docstrings(self):
        for tool in (server.apply_codings, server.import_text_file):
            doc = " ".join((tool.__doc__ or "").split())
            assert "Deprecated since v0.12" in doc
            assert "removal is planned for v1.0" in doc
            assert "A human coder's name is never used." in doc


# =============================================================================
# 10.11 RESTART RESILIENCE
# =============================================================================

class TestRestartResilience:

    def test_the_refusal_repeats_across_a_recycle(self, setup_server_unset,
                                                  qualcoder_db_path,
                                                  tmp_path):
        first = server.create_code("Recycled", create_backup=False)
        # The host recycled the process: globals gone, project re-selected
        server.db = None
        server.current_project_path = None
        json.loads(server.select_project(qualcoder_db_path))
        second = server.create_code("Recycled", create_backup=False)
        assert first == second

    def test_a_name_set_before_the_recycle_is_read_after_it(
            self, setup_server_unset, qualcoder_db_path):
        server.set_project_ai_coder_name("Survivor")
        server.db = None
        server.current_project_path = None
        json.loads(server.select_project(qualcoder_db_path))
        server.create_code("AfterRecycle", create_backup=False)
        assert _row(qualcoder_db_path,
                    "SELECT owner FROM code_name WHERE name='AfterRecycle'"
                    )["owner"] == "Survivor"

    def test_the_module_keeps_no_cache(self):
        """Restart resilience by construction: nothing to invalidate."""
        suspicious = [n for n in dir(ps)
                      if not n.startswith("__")
                      and ("cache" in n.lower() or n.startswith("_CACHED"))]
        assert suspicious == []

    def test_a_change_on_disk_is_seen_without_any_reselect(
            self, setup_server, qualcoder_db_path):
        """Two hosts, one project: the second host's change is read by the
        first on its next write, because nothing is cached."""
        write_ai_coder_name(qualcoder_db_path, "Other Host")
        server.create_code("Coherent", create_backup=False)
        assert _row(qualcoder_db_path,
                    "SELECT owner FROM code_name WHERE name='Coherent'"
                    )["owner"] == "Other Host"

    def test_a_session_recorded_under_another_name_warns_on_apply(
            self, setup_server, qualcoder_db_path):
        from qualcoder_mcp.sessions import AICodingSession, CodingSuggestion
        session = AICodingSession(
            project_path=qualcoder_db_path, description="snapshot",
            file_ids=[1], code_names=["Stress"], instruction="t",
            min_confidence=0.5, ai_coder_name_at_record="Qwen 3.8 6bit")
        session.add_suggestion(CodingSuggestion(
            file_id=1, file_name="interview.txt", code_id=1,
            code_name="Stress", start_pos=0, end_pos=10,
            segment_text="This is in", reasoning="r", confidence=0.9,
            status="approved"))
        server.session_manager.save_session(session)
        out = server.apply_codings(session.session_id, create_backup=False)
        assert ("These suggestions were recorded while the project's AI "
                "coder name was 'Qwen 3.8 6bit'; they are being written "
                f"under '{DEFAULT_AI_CODER_NAME}'.") in out
        assert _row(qualcoder_db_path,
                    "SELECT owner FROM code_text WHERE pos0=0 AND pos1=10"
                    )["owner"] == DEFAULT_AI_CODER_NAME

    def test_no_warning_when_the_name_is_unchanged(self, setup_server,
                                                   qualcoder_db_path):
        session = _approved_session(server, qualcoder_db_path)
        session.ai_coder_name_at_record = DEFAULT_AI_CODER_NAME
        server.session_manager.save_session(session)
        out = server.apply_codings(session.session_id, create_backup=False)
        assert "were recorded while the project's AI coder name" not in out

    def test_a_0_11_session_file_without_the_field_still_applies(
            self, setup_server, qualcoder_db_path):
        session = _approved_session(server, qualcoder_db_path)
        path = (Path(server.session_manager.storage_dir)
                / f"session_{session.session_id}.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        del data["ai_coder_name_at_record"]
        path.write_text(json.dumps(data), encoding="utf-8")
        out = server.apply_codings(session.session_id, create_backup=False)
        assert "CODINGS APPLIED" in out
        assert "were recorded while the project's AI coder name" not in out


# =============================================================================
# 10.12 TRAVEL
# =============================================================================

class TestTravel:

    def test_a_backup_carries_the_sidecar_byte_identically(
            self, setup_server, qualcoder_db_path):
        original = _sidecar(qualcoder_db_path).read_bytes()
        server.create_code("MakesABackup")
        backups = _backup_folders(qualcoder_db_path)
        assert backups, "no backup was made"
        copy = (Path(qualcoder_db_path).parent / backups[-1] / SIDECAR_NAME)
        assert copy.read_bytes() == original

    def test_a_workspace_copy_carries_it_and_stays_separate(
            self, setup_server, qualcoder_db_path, tmp_path):
        # `workspace` is not a tool argument, so the copy goes wherever
        # database.DEFAULT_WORKSPACE points; the autouse _isolate_workspace
        # fixture in conftest points it inside tmp_path. Moving HOME, which
        # this test used to do, redirects nothing: the constant is computed
        # from Path.home() at IMPORT time (database.py:583), so the copy
        # and the "Copy Only" sidecar below landed in the researcher's own
        # workspace on every suite run (QA round 1, F1). Assert the sandbox
        # holds, because a guard that moved nothing is worse than none.
        out = json.loads(server.copy_project_to_workspace(qualcoder_db_path))
        assert out.get("success") is True, out
        copy_path = out["workspace_copy"]
        assert Path(copy_path).is_relative_to(tmp_path), copy_path
        assert read_sidecar(copy_path).name == DEFAULT_AI_CODER_NAME
        # Setting a name on the copy leaves the original alone
        write_ai_coder_name(copy_path, "Copy Only")
        assert read_sidecar(copy_path).name == "Copy Only"
        assert read_sidecar(qualcoder_db_path).name == DEFAULT_AI_CODER_NAME

    def test_restore_reports_the_setting_it_brought_back(
            self, setup_server, qualcoder_db_path):
        server.create_code("BeforeRestore")     # makes a backup carrying A
        backups = _backup_folders(qualcoder_db_path)
        backup = str(Path(qualcoder_db_path).parent / backups[-1])
        write_ai_coder_name(qualcoder_db_path, "Changed Since")
        out = json.loads(H.execute_destructive(server.restore_backup, backup))
        assert out.get("success") is True, out
        assert out["ai_coder_name_note"] == (
            f"The AI coder name setting was restored to "
            f"'{DEFAULT_AI_CODER_NAME}' (it was 'Changed Since' before the "
            f"restore).")
        assert read_sidecar(qualcoder_db_path).name == DEFAULT_AI_CODER_NAME

    def test_restoring_a_backup_without_a_sidecar_unsets_the_project(
            self, setup_server, qualcoder_db_path):
        server.create_code("BeforeRestore")
        backups = _backup_folders(qualcoder_db_path)
        backup = Path(qualcoder_db_path).parent / backups[-1]
        (backup / SIDECAR_NAME).unlink()        # a pre-0.12 backup
        out = json.loads(H.execute_destructive(server.restore_backup, str(backup)))
        assert out.get("success") is True, out
        assert "no longer has one" in out["ai_coder_name_note"]
        assert read_sidecar(qualcoder_db_path).status == "unset"
        out = json.loads(server.create_code("AfterRestore",
                                            create_backup=False))
        assert out["error"] == EXPECTED_ASK


def _sidecar_bytes(folder) -> int:
    return len((Path(folder) / SIDECAR_NAME).read_bytes())

# =============================================================================
# THE SIDECAR SIZE SPIRAL: ONE UNIT FOR BOTH CAPS (fix round 4, S5)
# =============================================================================

class TestTheWriteCapMatchesTheReadCap:
    """Writes were capped at 200 ENTRIES and reads at 64 KiB. The
    formatting and the preserved unknown keys inflate the file between
    the two, so ordinary use produced a file the server had just written
    and would then refuse to read, after which every owner-bearing write
    was refused. No attacker needed."""

    def test_ninety_two_name_changes_stay_readable(self, tmp_path):
        """The measured number, at the documented maxima: an 80-character
        name and a 500-character note, both of which the validator
        accepts, cross 64 KiB on the 92nd write."""
        folder = tmp_path / "p.qda"
        folder.mkdir()
        note = "n" * 500
        for i in range(92):
            name = f"Model {i}".ljust(80, "M")
            assert len(name) == 80
            write_ai_coder_name(folder, name, note=note)
            assert read_sidecar(folder).status == "project", i
        size = _sidecar_bytes(folder)
        assert size <= ps.SIDECAR_WRITE_MAX_BYTES, size
        assert read_sidecar(folder).name == f"Model {91}".ljust(80, "M")

    def test_a_long_life_never_writes_a_file_it_cannot_read(self,
                                                            tmp_path):
        folder = tmp_path / "p.qda"
        folder.mkdir()
        for i in range(400):
            write_ai_coder_name(folder, f"Model {i}", note="x" * 400)
            assert read_sidecar(folder).status == "project", i
            assert _sidecar_bytes(folder) <= ps.SIDECAR_WRITE_MAX_BYTES, i
        state = read_sidecar(folder)
        assert state.name == "Model 399"
        # The history is trimmed oldest first and the current entry is
        # never the one dropped.
        assert state.history, "the history was emptied"
        assert state.history[-1]["name"] == "Model 399"

    def test_the_caps_are_in_the_same_unit(self):
        assert ps.SIDECAR_WRITE_MAX_BYTES < ps.SIDECAR_READ_MAX_BYTES

    def test_preserved_unknown_keys_are_refused_rather_than_written(
            self, tmp_path):
        """When the bulk is not the history, trimming cannot help, so the
        write refuses and the file on disk is left byte-identical."""
        folder = tmp_path / "p.qda"
        folder.mkdir()
        write_ai_coder_name(folder, "First")
        path = folder / SIDECAR_NAME
        data = json.loads(path.read_text(encoding="utf-8"))
        data["someone_elses_key"] = "x" * (ps.SIDECAR_WRITE_MAX_BYTES)
        path.write_text(json.dumps(data), encoding="utf-8")
        before = path.read_bytes()
        with pytest.raises(ps.SidecarWriteError) as e:
            write_ai_coder_name(folder, "Second")
        assert SIDECAR_NAME in str(e.value)
        assert path.read_bytes() == before

    def test_unknown_keys_that_fit_are_still_preserved(self, tmp_path):
        folder = tmp_path / "p.qda"
        folder.mkdir()
        write_ai_coder_name(folder, "First")
        path = folder / SIDECAR_NAME
        data = json.loads(path.read_text(encoding="utf-8"))
        data["a_later_feature"] = {"keep": "me"}
        path.write_text(json.dumps(data), encoding="utf-8")
        write_ai_coder_name(folder, "Second")
        after = json.loads(path.read_text(encoding="utf-8"))
        assert after["a_later_feature"] == {"keep": "me"}

    def test_an_unreadable_sidecar_has_a_route_back(self, setup_server,
                                                    qualcoder_db_path):
        """The recovery half: whatever made the file unreadable, the
        setter gets the project working again without deleting anything
        and without asking the researcher to open a file manager."""
        _sidecar(qualcoder_db_path).write_bytes(b"{" * 30000)
        assert json.loads(server.create_code(
            "Blocked", create_backup=False))["error"] == ps.UNREADABLE_MESSAGE
        out = json.loads(server.set_project_ai_coder_name("Qwen 3.8 6bit"))
        assert out.get("success") is True, out
        assert Path(out["replaced_unreadable_file"]).exists()
        out = json.loads(server.create_code("Now Fine", create_backup=False))
        assert out.get("error") is None, out


# =============================================================================
# A FUNCTION DOCUMENTED AS NEVER RAISING MUST NOT RAISE (fix round 4, S1)
# =============================================================================

class TestReadSidecarNeverRaises:
    """`_read_raw` caught (UnicodeDecodeError, ValueError). CPython's
    JSON scanner raises RecursionError on deep nesting, which is a
    RuntimeError, so it escaped a function whose docstring says "Never
    raises". 64 KiB of '[' is about 30,000 levels and sits under the
    size cap, so the cap did not help."""

    BOMB = b"[" * 30000

    def test_a_deeply_nested_sidecar_classifies_as_unreadable(self,
                                                              tmp_path):
        folder = tmp_path / "p.qda"
        folder.mkdir()
        (folder / SIDECAR_NAME).write_bytes(self.BOMB)
        assert read_sidecar(folder).status == "unreadable"

    def test_the_promise_holds_for_every_shape_the_file_can_take(self,
                                                                 tmp_path):
        folder = tmp_path / "p.qda"
        folder.mkdir()
        target = folder / SIDECAR_NAME
        for raw in (self.BOMB, b"{" * 30000, b"\x00\xff\xfe", b"",
                    b"[1, 2", b"null", b"3", b'"a string"',
                    ("{" * 400 + "}" * 400).encode("utf-8")):
            target.write_bytes(raw)
            state = read_sidecar(folder)          # must not raise
            assert state.status in ("unreadable", "unset"), raw[:12]

    def test_the_message_names_the_file_and_the_repair(self, setup_server,
                                                       qualcoder_db_path):
        _sidecar(qualcoder_db_path).write_bytes(self.BOMB)
        out = json.loads(server.create_code("Blocked"))
        assert "error" in out
        assert SIDECAR_NAME in out["error"]
        assert "RecursionError" not in out["error"]
        assert "maximum recursion" not in out["error"]

    def test_a_restore_that_completed_keeps_its_recovery_pointer(
            self, setup_server, qualcoder_db_path):
        """The damaging call site: restore_backup reads the sidecar
        AGAIN after the folder swap. A bomb in the BACKUP therefore
        reported a completed restore as a bare internal error, with no
        success flag, no restored path and no safety_backup, which is
        the researcher's only route back."""
        server.create_code("BeforeRestore")
        backups = _backup_folders(qualcoder_db_path)
        backup = Path(qualcoder_db_path).parent / backups[-1]
        (backup / SIDECAR_NAME).write_bytes(self.BOMB)
        out = json.loads(H.execute_destructive(server.restore_backup,
                                               str(backup)))
        assert out.get("success") is True, out
        assert out["safety_backup"]
        assert Path(out["safety_backup"]).exists()
        assert out["restored_from"] == str(backup)
        assert read_sidecar(qualcoder_db_path).status == "unreadable"

    def test_the_pointer_survives_a_failure_in_the_reporting_itself(
            self, setup_server, qualcoder_db_path, monkeypatch):
        """Belt and braces on the same line: whatever the post-swap
        reporting raises, the result still carries success and the two
        paths, with a note saying the description is incomplete."""
        server.create_code("BeforeRestore")
        backups = _backup_folders(qualcoder_db_path)
        backup = Path(qualcoder_db_path).parent / backups[-1]

        real = server.read_sidecar
        calls = []

        def _boom_after_the_swap(*a, **k):
            # The PRE-restore read at the top of the tool happens before
            # anything destructive and must keep working; only the read
            # after the swap is faulted here.
            calls.append(1)
            if len(calls) == 1:
                return real(*a, **k)
            raise RuntimeError("anything at all")

        monkeypatch.setattr(server, "read_sidecar", _boom_after_the_swap)
        out = json.loads(H.execute_destructive(server.restore_backup,
                                               str(backup)))
        assert out.get("success") is True, out
        assert out["safety_backup"]
        assert "report_incomplete" in out
        assert "anything at all" not in json.dumps(out)


# =============================================================================
# 10.14 SURFACE PINS
# =============================================================================

class TestSurfacePins:

    def test_the_setter_is_in_core_and_in_the_full_surface(self):
        assert "set_project_ai_coder_name" in server.CORE_TOOLSET
        assert "set_project_ai_coder_name" in server.mcp._tool_manager._tools

    def test_default_owner_has_exactly_two_occurrences(self):
        """The export is its only caller left; every database write goes
        through the resolver (B1.5). Read from the source so a new write
        tool wired to the old helper fails here."""
        source = (Path(server.__file__)).read_text(encoding="utf-8")
        assert source.count("_default_owner(") == 2

    def test_no_write_tool_still_calls_the_export_fallback(self):
        source = (Path(server.__file__)).read_text(encoding="utf-8")
        definition = "def _default_owner() -> str:"
        call = "_default_owner()"
        assert definition in source
        # The one remaining call site is the REFI exporter's ai_user_name
        idx = source.index("ai_user_name=_default_owner()")
        assert idx > 0

    def test_the_resolver_returns_dicts_and_never_raises(self,
                                                         setup_server_unset):
        owner, error = server._resolve_write_owner()
        assert owner is None
        assert isinstance(error, dict)
        assert "error" in error

    def test_british_english_and_no_em_dashes_in_the_new_texts(self):
        """The same helper the batch's other three modules use.

        This module hand-rolled a weaker check: plain substring matching
        over six words, with "color " carrying a trailing space so
        "color." slipped past. Planting "analyze" in UNSET_HINT, a string
        returned to the model, passed the whole suite (QA round 1, F20).
        """
        texts = [EXPECTED_ASK, ps.UNREADABLE_MESSAGE, ps.NEWER_FORMAT_MESSAGE,
                 ps.READ_ONLY_FOLDER_MESSAGE, ps.UNSET_HINT,
                 EXPECTED_OWNER_REFUSAL,
                 server.set_project_ai_coder_name.__doc__ or ""]
        labels = ["EXPECTED_ASK", "UNREADABLE_MESSAGE",
                  "NEWER_FORMAT_MESSAGE", "READ_ONLY_FOLDER_MESSAGE",
                  "UNSET_HINT", "EXPECTED_OWNER_REFUSAL",
                  "set_project_ai_coder_name.__doc__"]
        _house_rules(texts, labels)

    def test_the_setter_docstring_states_the_exact_comparison_rule(self):
        doc = server.set_project_ai_coder_name.__doc__ or ""
        assert "compared exactly" in doc
        assert "never case-insensitively" in doc
