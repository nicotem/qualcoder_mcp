# SPDX-License-Identifier: LGPL-3.0-or-later
"""v0.14 privacy: what the run record, the error answers and the log keep.

Each class is one item of the v0.14 privacy brief:

1. The run's fingerprints. The run's result no longer carries a digest of
   each file's text before the run, and the run record fingerprints the
   text before and after the run with a digest keyed with the token
   secret (format 3), so neither, beside the pseudonymised text,
   confirms a guessed name. The tool's list of what it does not rewrite
   names the stored copy in `documents/`.
2. One rule for every error answer and log line: the kind of error and
   SQLite's short error name, never SQLite's message, which a trigger in
   the project, or a note or a name stored as bytes that are not UTF-8,
   can fill with a note's text, private part included.
3. No names or paths in the log file: creating things logs ids; the
   selection, start-up and connection lines name no project folder; the
   backup, lock-file, prune and copy lines carry no path.
4. Every read re-checks whether the project hides coders, so a read
   cannot return a hidden coder's row after the project gains that
   setting while this server is connected.

The fixture and helpers are the flagship's own
(`test_v012_pseudonymise_tool.py`), imported so this file drives exactly
the project every other pin drives.
"""

import hashlib
import hmac
import itertools
import json
import logging
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server
from qualcoder_mcp import preview_tokens as pt
from qualcoder_mcp import database as dbmod
from qualcoder_mcp.database import QualcoderDatabase
from test_v012_pseudonymise_tool import (  # noqa: F401  (`project` is a fixture)
    TEXT, add_coding, execute_from, preview_of, project, query)

# The label a text digest in the run record is keyed over, written out
# here rather than imported, so a change to it is a change a test sees.
TEXT_LABEL = b"qualcoder-mcp run record text\n"

# No variants, so one guessed name per entry rebuilds the text before
# the run exactly (the flagship's MAPPING also maps "Tom").
PLAIN_MAPPING = [{"original": "Thomas", "pseudonym": "Alex"},
                 {"original": "Mary Ann", "pseudonym": "Sam"}]


def _run(project):
    result = execute_from(preview_of(mapping=PLAIN_MAPPING),
                          mapping=PLAIN_MAPPING)
    assert result.get("success") is True, result
    record = json.loads(Path(result["manifest_path"]).read_text(
        encoding="utf-8"))
    new_text = query(project, "SELECT fulltext FROM source WHERE id=1"
                     )[0]["fulltext"]
    return result, record, new_text


def _put_back(new_text, spans, by_entry):
    """The text before the run as a guess rebuilds it: each pseudonym's
    span in the new text replaced by the guessed name for its entry."""
    text = new_text
    for span in sorted(spans, key=lambda s: -s["new_span"][0]):
        start, end = span["new_span"]
        text = text[:start] + by_entry[span["entry"]] + text[end:]
    return text


def _every_string(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _every_string(item)
    elif isinstance(value, list):
        for item in value:
            yield from _every_string(item)
    elif isinstance(value, str):
        yield value


def _keyed(text):
    return hmac.new(pt.load_secret().encode("ascii"),
                    TEXT_LABEL + text.encode("utf-8"),
                    hashlib.sha256).hexdigest()


# =============================================================================
# 1. THE RUN'S FINGERPRINTS
# =============================================================================

class TestTheRunsFingerprints:

    # The v0.13 release's security gate ran the same attack with 3,000
    # names and recovered both in a fifth of a second.
    GUESSES = ["Anna", "Tom", "Thomas", "Mary", "Mary Ann", "Peter", "Sue"]

    def _confirmed(self, record, new_text):
        """Every guess the record confirms: a name put back where each
        pseudonym sits, the text's plain SHA-256 compared with every
        string anywhere in the record."""
        item = record["files"][0]
        held = set(_every_string(record))
        entries = sorted({s["entry"] for s in item["replacements"]})
        found = []
        for guess in itertools.product(self.GUESSES, repeat=len(entries)):
            by_entry = dict(zip(entries, guess))
            text = _put_back(new_text, item["replacements"], by_entry)
            if hashlib.sha256(text.encode("utf-8")).hexdigest() in held:
                found.append(by_entry)
        return found

    def test_the_attack_rebuilds_the_text_before_the_run(self, project):
        """The control half: the right guess rebuilds the old text
        exactly, so the pin below cannot pass because the attack is
        broken."""
        _, record, new_text = _run(project)
        assert new_text != TEXT
        assert _put_back(new_text, record["files"][0]["replacements"],
                         {0: "Thomas", 1: "Mary Ann"}) == TEXT

    def test_the_record_confirms_no_guessed_name(self, project):
        _, record, new_text = _run(project)
        assert self._confirmed(record, new_text) == []

    def test_the_record_keys_both_texts_with_the_secret(self, project):
        _, record, new_text = _run(project)
        assert record["format"] == 3
        item = record["files"][0]
        assert "old_fingerprint" not in item
        assert "new_fingerprint" not in item
        assert item["old_length"] == len(TEXT)
        assert item["new_length"] == len(new_text)
        assert item["old_text_hmac_sha256"] == _keyed(TEXT)
        assert item["new_text_hmac_sha256"] == _keyed(new_text)

    def test_neither_digest_is_the_plain_or_unlabelled_one(self, project):
        """Keyed over the label as well as the text: without it the
        digest of a text would be the digest of any other value keyed
        with the same secret that the text was built to spell."""
        _, record, new_text = _run(project)
        item = record["files"][0]
        secret = pt.load_secret().encode("ascii")
        for text, key in ((TEXT, "old_text_hmac_sha256"),
                          (new_text, "new_text_hmac_sha256")):
            data = text.encode("utf-8")
            assert item[key] != hashlib.sha256(data).hexdigest()
            assert item[key] != hmac.new(secret, data,
                                         hashlib.sha256).hexdigest()

    def test_the_result_carries_no_digest_of_the_text_before_the_run(
            self, project):
        result, _, new_text = _run(project)
        item = result["files"][0]
        assert "old_sha256" not in item
        old = hashlib.sha256(TEXT.encode("utf-8")).hexdigest()
        assert not [s for s in _every_string(result) if old in s]
        # What stays: the lengths, which the preview already gives, and
        # the digest of the new text, which the reader can read.
        assert item["old_length"] == len(TEXT)
        assert item["new_length"] == len(new_text)
        assert item["new_sha256"] == hashlib.sha256(
            new_text.encode("utf-8")).hexdigest()

    def test_the_preview_gives_the_same_two_lengths(self, project):
        """PRIVACY.md says the lengths the record and the result keep
        plain are the ones the preview already gives the conversation."""
        out = preview_of(mapping=PLAIN_MAPPING)
        item = out["preview"]["files"][0]
        result = execute_from(out, mapping=PLAIN_MAPPING)
        assert item["text_length"] == result["files"][0]["old_length"]
        assert item["new_text_length"] == result["files"][0]["new_length"]

    def test_the_documents_say_what_an_old_record_holds(self):
        privacy = " ".join((Path(__file__).parent.parent / "PRIVACY.md"
                            ).read_text(encoding="utf-8").split())
        assert ("Records written before v0.14 (format 1 by v0.12, format 2 "
                "by v0.13) carry each file's length and plain SHA-256 "
                "before and after the run instead") in privacy
        assert ("the keyed digests in the run manifests already written "
                "can then no longer be checked") in privacy
        assert "Deleting it invalidates outstanding preview tokens, which " \
               "means the next execute asks for a fresh preview; nothing " \
               "else." not in privacy

    def test_the_does_not_rewrite_list_names_the_stored_copy(self):
        doc = " ".join(server.pseudonymise_source.__doc__.split())
        start = doc.index("What this does NOT rewrite")
        listed = doc[start:doc.index("Case and file names are changed",
                                     start)]
        assert "stored copy" in listed
        assert "documents/" in listed


# =============================================================================
# 2. ONE RULE FOR EVERY ERROR ANSWER AND LOG LINE
# =============================================================================

SENTINEL = "PRIVATE-SENTINEL"
NOTE = f"Met Thomas.\n#####{SENTINEL} words"
LEAKS = (SENTINEL, "Met Thomas")


def _sqlite_names_its_errors():
    """Whether this Python's sqlite3 errors carry SQLite's error name
    (`sqlite_errorname`, from Python 3.11), asked of a real error."""
    con = sqlite3.connect(":memory:")
    try:
        con.execute("SELECT * FROM no_such_table")
    except sqlite3.Error as e:
        return bool(getattr(e, "sqlite_errorname", None))
    finally:
        con.close()
    return False


def _json_path_error_quotes_its_argument():
    """Whether this SQLite has json_extract and names a bad path in its
    error, which is what lets a trigger put a note in SQLite's message
    without RAISE: "bad JSON path: '<note>'" on 3.49 and 3.50, "JSON path
    error near '<note>'" on 3.43. Asked of SQLite, never of a version."""
    con = sqlite3.connect(":memory:")
    try:
        con.execute("SELECT json_extract('{}', 'quoted-probe')")
    except sqlite3.Error as e:
        return "quoted-probe" in str(e)
    finally:
        con.close()
    return False


NAMED = _sqlite_names_its_errors()
TRIGGER_LABEL = ("IntegrityError SQLITE_CONSTRAINT_TRIGGER" if NAMED
                 else "IntegrityError")
JSON_LABEL = "OperationalError SQLITE_ERROR" if NAMED else "OperationalError"
NEEDS_JSON_PATH_ERROR = pytest.mark.skipif(
    not _json_path_error_quotes_its_argument(),
    reason=f"SQLite {sqlite3.sqlite_version} has no json_extract, or does "
           f"not name a bad path in its error; the RAISE form runs instead")

# A trigger body that fails with SQLite's message set to annotation 1's
# note, two ways: RAISE with a literal (an IntegrityError, on every
# SQLite: only an expression as RAISE's argument needs a newer one), and
# json_extract with the note as the path (an OperationalError, where
# json_extract names a bad path).
RAISES = {
    "raise": (f"SELECT RAISE(ABORT, '{NOTE}');", TRIGGER_LABEL),
    "json": ("SELECT json_extract('{}', (SELECT memo FROM annotation "
             "WHERE anid = 1));", JSON_LABEL),
}
FORMS = [pytest.param("raise", id="raise"),
         pytest.param("json", id="json", marks=NEEDS_JSON_PATH_ERROR)]


def _ddl(project, *statements):
    con = sqlite3.connect(str(project / "data.qda"))
    for statement in statements:
        con.execute(statement)
    con.commit()
    con.close()
    server.db.close()
    server.db = QualcoderDatabase(str(project))


def _plant_trigger(project, event, table, form):
    body, _ = RAISES[form]
    _ddl(project,
         "UPDATE annotation SET memo = '" + NOTE + "' WHERE anid = 1",
         f"CREATE TRIGGER leak BEFORE {event} ON {table} BEGIN {body} END")


def _logged(caplog):
    return [r.getMessage() for r in caplog.records]


def _assert_no_leak(caplog, *answers):
    """Neither the note's public part nor its private part in any answer,
    in any log record's message, or in the formatted log, tracebacks
    included (a library that logs an exception prints its chain)."""
    text = "\n".join(_logged(caplog)) + "\n" + caplog.text + "\n" + \
        "\n".join(json.dumps(a) if not isinstance(a, str) else a
                  for a in answers)
    for leaked in LEAKS:
        assert leaked not in text, leaked


def _category(project):
    _ddl(project, "INSERT INTO code_cat (catid, name, memo, owner, date, "
                  "supercatid) VALUES (1, 'Feelings', '', 'TestCoder', 'd', "
                  "NULL)")


# The older write tools, each with the statement a trigger fires on and
# the words its answer opens with (the kind of error follows them).
WRITE_TOOLS = {
    "create_code": ("INSERT", "code_name", "Failed to add code",
                    lambda: server.create_code(name="Fresh",
                                               create_backup=False)),
    "rename_code": ("UPDATE", "code_name", "Failed to rename code",
                    lambda: server.rename_code(code_id=1, new_name="Strain",
                                               create_backup=False)),
    "create_category": ("INSERT", "code_cat", "Failed to add category",
                        lambda: server.create_category(
                            name="Fresh", create_backup=False)),
    "rename_category": ("UPDATE", "code_cat", "Failed to rename category",
                        lambda: server.rename_category(
                            category_id=1, new_name="Moods",
                            create_backup=False)),
    "add_journal_entry": ("INSERT", "journal", "Failed to add journal entry",
                          lambda: server.add_journal_entry(
                              name="j1", entry="x", create_backup=False)),
    "import_text_file": ("INSERT", "source", "Failed to import text file",
                         lambda: server.import_text_file(
                             filename="fresh.txt", content="Some words.",
                             create_backup=False)),
}


# The tools whose non-constraint branch is `_raise_query_error`, and the
# name its log line gives.
QUERY_ERROR_ROUTE = {"add_journal_entry": "add_journal_entry",
                     "rename_code": "rename_code",
                     "create_category": "add_category",
                     "rename_category": "rename_category"}


class TestOneRuleForErrors:

    @pytest.mark.parametrize("form", FORMS)
    @pytest.mark.parametrize("tool", sorted(WRITE_TOOLS))
    def test_a_write_tool_answers_and_logs_the_kind_only(
            self, project, caplog, tool, form):
        event, table, opening, call = WRITE_TOOLS[tool]
        if table == "code_cat":
            _category(project)
        _plant_trigger(project, event, table, form)
        caplog.set_level(logging.DEBUG)
        answer = json.loads(call())
        _assert_no_leak(caplog, answer)
        label = RAISES[form][1]
        if form == "json" and tool in QUERY_ERROR_ROUTE:
            # The non-constraint branch goes through `_raise_query_error`,
            # which answers its fixed text and logs the kind.
            assert answer["error"] == opening
            assert f"Database error in {QUERY_ERROR_ROUTE[tool]}: " \
                   f"{label}" in _logged(caplog)
        elif tool == "import_text_file":
            assert answer["error"] == f"Database error: {opening}: {label}"
        else:
            assert answer["error"] == f"{opening}: {label}"

    @pytest.mark.parametrize("form", FORMS)
    def test_add_coding_raises_and_logs_the_kind_only(
            self, project, caplog, form):
        _plant_trigger(project, "INSERT", "code_text", form)
        write_db = QualcoderDatabase(str(project), read_only=False)
        caplog.set_level(logging.DEBUG)
        try:
            with pytest.raises(RuntimeError) as raised:
                write_db.add_coding(file_id=1, code_id=1, start_pos=7,
                                    end_pos=11, selected_text="said",
                                    owner="TestCoder")
        finally:
            write_db.close()
        _assert_no_leak(caplog, str(raised.value))
        assert str(raised.value) == f"Failed to add coding: " \
                                    f"{RAISES[form][1]}"

    @pytest.mark.parametrize("form", FORMS)
    def test_add_memo_to_coding_raises_and_logs_the_kind_only(
            self, project, caplog, form):
        add_coding(project, 9, 1, 11, 15)
        _plant_trigger(project, "UPDATE", "code_text", form)
        write_db = QualcoderDatabase(str(project), read_only=False)
        caplog.set_level(logging.DEBUG)
        try:
            with pytest.raises(RuntimeError) as raised:
                write_db.add_memo_to_coding(9, "a new note", "TestCoder")
        finally:
            write_db.close()
        _assert_no_leak(caplog, str(raised.value))
        assert str(raised.value) == f"Failed to update memo: " \
                                    f"{RAISES[form][1]}"
        assert f"Database error in add_memo_to_coding: {RAISES[form][1]}" \
            in _logged(caplog)


def _not_utf8(table, column, where):
    """A value stored as bytes that are not UTF-8, holding the note: any
    read of it makes Python's sqlite3 raise an OperationalError whose
    message quotes the whole value. No trigger; every SQLite."""
    raw = NOTE.replace("Thomas", "Thömas").encode("latin-1")
    return (f"UPDATE {table} SET {column} = CAST(X'{raw.hex()}' AS TEXT) "
            f"WHERE {where}")


def _decode_label():
    """The label of Python's own decode error, asked of a real one: it is
    raised by Python's sqlite3, not by SQLite, and carries no SQLite
    name on any version."""
    con = sqlite3.connect(":memory:")
    try:
        con.execute("SELECT CAST(X'ff' AS TEXT)").fetchone()
    except sqlite3.Error as e:
        return dbmod.sqlite_error_label(e)
    finally:
        con.close()
    raise AssertionError("a value that is not UTF-8 was read")


DECODE_LABEL = _decode_label()


class TestTheQueryHelperAndTheRoutesWithNoTrigger:

    @pytest.mark.parametrize("named", [True, False],
                             ids=["named", "nameless"])
    def test_the_query_helper_logs_the_kind_only(self, caplog, named):
        """Every platform: the error is built here, its message the note,
        with and without SQLite's name (Python 3.10 has none)."""
        error = sqlite3.OperationalError(f"bad JSON path: '{NOTE}'")
        if named:
            error.sqlite_errorname = "SQLITE_ERROR"
        caplog.set_level(logging.DEBUG)
        with pytest.raises(RuntimeError) as raised:
            dbmod._raise_query_error(error, "somewhere", "Fixed text")
        assert str(raised.value) == "Fixed text"
        assert raised.value.__cause__ is None
        assert _logged(caplog) == [
            "Database error in somewhere: OperationalError"
            + (" SQLITE_ERROR" if named else "")]

    def test_a_coded_segments_read_of_a_note_not_utf8(self, project, caplog):
        add_coding(project, 9, 1, 7, 11)
        _ddl(project, _not_utf8("code_text", "memo", "ctid = 9"))
        caplog.set_level(logging.DEBUG)
        answer = json.loads(server.get_coded_segments(code_id=1))
        _assert_no_leak(caplog, answer)
        assert "error" in answer

    def test_a_file_search_over_text_not_utf8(self, project, caplog):
        _ddl(project, _not_utf8("source", "fulltext", "id = 4"))
        caplog.set_level(logging.DEBUG)
        answer = json.loads(server.search_files(pattern="nothing",
                                                search_content=True))
        _assert_no_leak(caplog, answer)
        assert answer["error"].startswith("Failed to search files")
        assert [line for line in _logged(caplog)
                if line.startswith("Database error in")] == [
            "Database error in search_files: " + DECODE_LABEL]

    def test_a_file_search_meeting_a_sqlite_error_of_its_own(
            self, project, caplog, monkeypatch):
        """The tool's own catch-all, reached by a SQLite error that no
        database method wrapped: it answered and logged the message."""
        def fails(*args, **kwargs):
            raise sqlite3.OperationalError(f"bad JSON path: '{NOTE}'")
        monkeypatch.setattr(QualcoderDatabase, "search_files", fails)
        caplog.set_level(logging.DEBUG)
        answer = json.loads(server.search_files(pattern="nothing",
                                                search_content=True))
        _assert_no_leak(caplog, answer)
        assert answer["error"] == "Failed to search files: OperationalError"
        assert "Error in search_files: OperationalError" in _logged(caplog)

    @pytest.mark.parametrize("read", ["codes", "files", "cases", "journal"])
    def test_a_resource_read_of_a_note_not_utf8(self, project, caplog, read):
        """Resources are read by the MCP library, which answered with the
        message and logged the traceback; since fix round 1 they answer
        the fixed text as their content and raise nothing, so the
        library logs nothing."""
        table, column, where, fn = {
            "codes": ("code_name", "memo", "cid = 1", server.list_all_codes),
            "files": ("source", "memo", "id = 4", server.list_all_files),
            "cases": ("cases", "memo", "caseid = 1", server.list_all_cases),
            "journal": ("journal", "jentry", "jid = 1",
                        server.get_journal_entries),
        }[read]
        if read == "journal":
            _ddl(project, "INSERT INTO journal (jid, name, jentry, date, "
                          "owner) VALUES (1, 'j', 'x', 'd', 'TestCoder')")
        _ddl(project, _not_utf8(table, column, where))
        caplog.set_level(logging.DEBUG)
        answer = json.loads(fn())
        assert answer == {"error": server.DB_UNAVAILABLE_ERROR}
        _assert_no_leak(caplog, answer)

    def test_a_resource_read_over_the_wire(self, project, caplog):
        """What the host receives, through the MCP library itself."""
        import asyncio
        _ddl(project, _not_utf8("code_name", "memo", "cid = 1"))
        caplog.set_level(logging.DEBUG)
        contents = asyncio.run(server.mcp.read_resource(
            "qualcoder://codes/list"))
        text = "".join(item.content for item in contents)
        _assert_no_leak(caplog, text)
        assert json.loads(text) == {"error": server.DB_UNAVAILABLE_ERROR}
        # The library logged nothing: it logs only what a resource raises.
        assert "Error reading resource" not in caplog.text

    @pytest.mark.parametrize("tool", ["rename_file", "rename_case"])
    def test_a_rename_beside_a_file_name_not_utf8(self, project, caplog,
                                                  tool):
        """A file name stored as bytes that are not UTF-8 refuses every
        rename (v0.13's final check, H-6); the log line that refusal
        wrote carried the name. The kind only now."""
        _ddl(project, _not_utf8("source", "name", "id = 4"))
        caplog.set_level(logging.DEBUG)
        call = (server.rename_file(file_id=1, new_name="renamed.txt",
                                   create_backup=False)
                if tool == "rename_file" else
                server.rename_case(case_id=1, new_name="Participant Z",
                                   create_backup=False))
        answer = json.loads(call)
        _assert_no_leak(caplog, answer)
        assert "error" in answer


class TestTheTwoHelpers:
    """`error_label` for log lines, `error_text` for answers, each with
    every kind of error they meet, on every platform."""

    def _sqlite_error(self, named=True):
        error = sqlite3.OperationalError(f"bad JSON path: '{NOTE}'")
        if named:
            error.sqlite_errorname = "SQLITE_ERROR"
        return error

    @pytest.mark.parametrize("helper", [dbmod.error_label, dbmod.error_text],
                             ids=["label", "text"])
    def test_a_sqlite_error_is_its_kind_and_name(self, helper):
        assert helper(self._sqlite_error()) == \
            "OperationalError SQLITE_ERROR"
        assert helper(self._sqlite_error(named=False)) == "OperationalError"

    @pytest.mark.parametrize("helper", [dbmod.error_label, dbmod.error_text],
                             ids=["label", "text"])
    def test_a_file_system_error_is_its_kind_and_errno_name(self, helper):
        error = PermissionError(13, "Permission denied",
                                "/Users/someone/Thomas study.qda/data.qda")
        assert helper(error) == "PermissionError EACCES"
        assert helper(FileNotFoundError("Path not found: /x/Thomas.qda")) \
            == "FileNotFoundError"

    def test_a_wrapper_raised_while_handling_one_is_labelled_in_a_log(self):
        try:
            try:
                raise self._sqlite_error()
            except sqlite3.Error:
                raise RuntimeError("Fixed text") from None
        except RuntimeError as wrapper:
            assert dbmod.error_label(wrapper) == \
                "OperationalError SQLITE_ERROR"
            # An answer keeps this server's own words.
            assert dbmod.error_text(wrapper) == "Fixed text"

    def test_this_servers_own_errors_keep_their_words_in_an_answer(self):
        for error in (ValueError("A code named 'Stress' already exists"),
                      TypeError("Pattern must be a string, got int"),
                      RuntimeError("Could not read the rows")):
            assert dbmod.error_text(error) == str(error)
            assert dbmod.error_label(error) == type(error).__name__

    def test_an_error_of_another_kind_is_its_kind_only(self):
        assert dbmod.error_text(KeyError(NOTE)) == "KeyError"
        assert dbmod.error_label(KeyError(NOTE)) == "KeyError"


class TestTheRunsJournalWriteAndTheLastRoutes:

    @NEEDS_JSON_PATH_ERROR
    def test_the_runs_journal_write_logs_the_kind_only(self, project,
                                                       caplog):
        """v0.13's final check (RF2-1): a trigger on the journal insert
        that fails with a non-constraint error reached
        `_raise_query_error` through `add_journal_entry`, which logged the
        note's public and private parts. json_extract needs no RAISE."""
        _ddl(project,
             "UPDATE annotation SET memo = '" + NOTE + "' WHERE anid = 1",
             "CREATE TRIGGER leak BEFORE INSERT ON journal BEGIN "
             + RAISES["json"][0] + " END")
        out = preview_of()
        caplog.set_level(logging.DEBUG)
        result = execute_from(out)
        assert result.get("success") is True, result
        assert result.get("journal_entry") is None
        _assert_no_leak(caplog, result)
        logged = _logged(caplog)
        assert f"Database error in add_journal_entry: {JSON_LABEL}" in logged
        assert ("Could not write the pseudonymisation journal entry: "
                + JSON_LABEL) in logged

    def test_an_unexpected_error_in_a_tool_answers_its_kind_only(
            self, project, caplog, monkeypatch):
        """The guard's last arm: an error of a kind none of its arms
        names no longer leaves for the MCP library with its message."""
        def fails(*args, **kwargs):
            raise KeyError(NOTE)
        monkeypatch.setattr(QualcoderDatabase, "list_categories", fails)
        caplog.set_level(logging.DEBUG)
        answer = json.loads(server.get_project_summary())
        _assert_no_leak(caplog, answer)
        assert answer == {"error": server.UNEXPECTED_ERROR.format(
            kind="KeyError")}
        assert "Unexpected error in get_project_summary: KeyError" in \
            _logged(caplog)

    def test_an_unexpected_error_in_a_resource_is_its_kind_only(
            self, project, caplog, monkeypatch):
        def fails(*args, **kwargs):
            raise KeyError(NOTE)
        monkeypatch.setattr(QualcoderDatabase, "list_codes", fails)
        caplog.set_level(logging.DEBUG)
        answer = json.loads(server.list_all_codes())
        assert answer == {"error": server.UNEXPECTED_RESOURCE_ERROR.format(
            kind="KeyError")}
        _assert_no_leak(caplog, answer)

    def test_this_servers_own_errors_still_reach_a_resource_reader(self):
        """The resource guard answers this server's own messages as a tool
        would, "No Qualcoder project selected" among them, as content."""
        original = (server.db, server.current_project_path)
        server.db, server.current_project_path = None, None
        try:
            answer = json.loads(server.list_all_codes())
            assert answer["error"].startswith("No Qualcoder project selected")
        finally:
            server.db, server.current_project_path = original


# The ways a handler may use the error it caught without its message
# reaching an answer or a log line: the helpers that give its kind, the
# type checks, the attributes that are names or numbers, a comparison
# (`"unique" in str(e).lower()` reads the message to decide, and says
# nothing), a re-raise, and a chained cause.
SAFE_CALLS = {"sqlite_error_label", "error_label", "error_text",
              "_error_answer",
              "_raise_query_error", "isinstance", "type", "_is_locked_error",
              "_is_transient_sqlite_error", "_write_failed_text"}
SAFE_ATTRIBUTES = {"sqlite_errorname", "sqlite_errorcode", "errno",
                   "__class__"}
SQLITE_CAPABLE = ("sqlite3.", "Exception", "BaseException")
SOURCE = Path(__file__).resolve().parent.parent / "src" / "qualcoder_mcp"


def _unsafe_uses(tree, capable=SQLITE_CAPABLE):
    """Every use of a caught error, in a handler that can catch a SQLite
    error, that is none of the safe ways above."""
    import ast
    parents = {child: node for node in ast.walk(tree)
               for child in ast.iter_child_nodes(node)}
    found = []
    for handler in ast.walk(tree):
        if not isinstance(handler, ast.ExceptHandler) or not handler.name:
            continue
        caught = ast.unparse(handler.type) if handler.type else "BARE"
        if caught != "BARE" and not any(c in caught for c in capable):
            continue
        for statement in handler.body:
            for node in ast.walk(statement):
                if not (isinstance(node, ast.Name) and node.id == handler.name
                        and isinstance(node.ctx, ast.Load)):
                    continue
                parent = parents[node]
                if isinstance(parent, ast.Call) and \
                        ast.unparse(parent.func) in SAFE_CALLS:
                    continue
                if isinstance(parent, ast.Attribute) and \
                        parent.attr in SAFE_ATTRIBUTES:
                    continue
                if isinstance(parent, ast.Raise):
                    continue            # `raise e` or `raise X from e`
                up, compared = parent, False
                while up is not handler:
                    if isinstance(up, ast.Compare):
                        compared = True
                        break
                    up = parents[up]
                if not compared:
                    found.append(f"line {node.lineno}: {ast.unparse(parent)}")
    return found


class TestTheRuleHoldsInTheSource:

    def test_the_checker_finds_a_message_used(self):
        """The control half: the checker is shown what it looks for."""
        import ast
        tree = ast.parse(
            "try:\n    pass\nexcept sqlite3.Error as e:\n"
            "    logger.error(f'Database error: {e}')\n"
            "try:\n    pass\nexcept Exception as e:\n"
            "    answer = {'error': str(e)}\n"
            "try:\n    pass\nexcept sqlite3.IntegrityError as e:\n"
            "    if 'unique' in str(e).lower():\n"
            "        raise ValueError('taken') from None\n"
            "    raise RuntimeError(sqlite_error_label(e)) from e\n")
        assert len(_unsafe_uses(tree)) == 2

    @pytest.mark.parametrize("module", sorted(
        p.name for p in SOURCE.glob("*.py")))
    def test_no_handler_that_can_catch_a_sqlite_error_uses_its_message(
            self, module):
        import ast
        tree = ast.parse((SOURCE / module).read_text(encoding="utf-8"))
        assert _unsafe_uses(tree) == []


def _flat(name):
    return " ".join((Path(__file__).resolve().parent.parent / name
                     ).read_text(encoding="utf-8").split())


class TestTheDocumentsSayTheRule:

    def test_privacy_says_the_rule(self):
        privacy = _flat("PRIVACY.md")
        assert ("Since v0.14 neither carries SQLite's message: an error "
                "from the database is reported by its kind and SQLite's "
                "short name for it") in privacy
        assert ("The same rule holds for the resources (the `qualcoder://` "
                "addresses)") in privacy

    def test_install_no_longer_says_the_log_quotes_a_note(self):
        install = _flat("INSTALL.md")
        assert "closing that is on the list for v0.14" not in install
        assert "the SQLite error text" not in install
        assert ("The lines this server writes carry no memo text, and "
                "since v0.14 no SQLite message") in install


# =============================================================================
# 3. NO NAMES OR PATHS IN THE LOG FILE
# =============================================================================

# Everything the flow below names starts with this, the project folder
# included, so one search finds any of them in any log record.
MARK = "Zebedee"


@pytest.fixture
def marked(tmp_path, monkeypatch):
    """A project whose folder is named after a participant, selected the
    way a researcher selects one, with every log record kept."""
    from test_v012_pseudonymise_tool import build_project
    from track5_helpers import write_fixture_sidecar
    folder = build_project(tmp_path / f"{MARK} study.qda")
    write_fixture_sidecar(str(folder))
    original = (server.db, server.current_project_path)
    yield folder
    if server.db is not None:
        try:
            server.db.close()
        except Exception:
            pass
    server.db, server.current_project_path = original


def _log_names(caplog):
    return [line for line in _logged(caplog) if MARK in line]


class TestNoNamesOrPathsInTheLog:

    def test_selecting_creating_and_connecting_log_no_name(
            self, marked, caplog, monkeypatch):
        caplog.set_level(logging.DEBUG)
        assert json.loads(server.select_project(str(marked))).get(
            "success") is not False
        answers = [
            server.create_code(name=f"{MARK} code", create_backup=False),
            server.create_category(name=f"{MARK} category",
                                   create_backup=False),
            server.create_case(name=f"{MARK} case", create_backup=False),
            server.add_journal_entry(name=f"{MARK} journal", entry="x",
                                     create_backup=False),
            server.import_text_file(filename=f"{MARK}_interview.txt",
                                    content="Some words.",
                                    create_backup=False),
            server.create_attribute_type(name=f"{MARK} attribute",
                                         applies_to="case",
                                         create_backup=False),
            server.set_attribute(target_type="case", target_id=1,
                                 attribute_name=f"{MARK} attribute",
                                 value="yes", create_backup=False),
        ]
        for answer in answers:
            assert "error" not in json.loads(answer), answer
        # The connection lines: a lost connection, and a project set in
        # the host's configuration.
        server.db.close()
        server.db = None
        assert "error" not in json.loads(server.get_current_project())
        server.db.close()
        server.db, server.current_project_path = None, None
        monkeypatch.setenv("QUALCODER_PROJECT_PATH", str(marked))
        assert "error" not in json.loads(server.get_project_summary())
        assert _log_names(caplog) == []
        logged = _logged(caplog)
        assert "Added code: cid=3, category=None" in logged
        assert "Added case: caseid=2" in logged
        assert "Connected to the project set in QUALCODER_PROJECT_PATH" \
            in logged

    def test_a_failed_selection_logs_the_kind_only(self, marked, caplog):
        caplog.set_level(logging.DEBUG)
        answer = json.loads(server.select_project(
            str(marked.parent / f"{MARK} missing.qda")))
        assert answer["success"] is False
        assert _log_names(caplog) == []
        assert "Failed to select project: FileNotFoundError" in \
            _logged(caplog)

    def test_the_start_up_lines_name_no_project(self, marked, caplog,
                                                monkeypatch, capsys):
        monkeypatch.setattr(server.mcp, "run", lambda **kwargs: None)
        monkeypatch.setattr(server, "_print_tty_notice_if_interactive",
                            lambda: None)
        monkeypatch.setattr(server, "_apply_toolset", lambda mode: {})
        caplog.set_level(logging.DEBUG)
        monkeypatch.setenv("QUALCODER_PROJECT_PATH", str(marked))
        server.main([])
        monkeypatch.setenv("QUALCODER_PROJECT_PATH",
                           str(marked.parent / f"{MARK} missing.qda"))
        with pytest.raises(SystemExit):
            server.main([])
        printed = capsys.readouterr()
        assert MARK not in printed.err + printed.out
        assert "QUALCODER_PROJECT_PATH was not found" in printed.err
        assert _log_names(caplog) == []
        assert ("Starting Qualcoder MCP server with the project set in "
                "QUALCODER_PROJECT_PATH") in _logged(caplog)

    def _backups(self, marked):
        return sorted(marked.parent.glob(f"{MARK} study_backup_*.qda"))

    def test_the_backup_restore_prune_and_copy_lines_name_no_project(
            self, marked, caplog, monkeypatch):
        import shutil
        assert json.loads(server.select_project(str(marked))).get(
            "success") is not False
        caplog.set_level(logging.DEBUG)
        for name in ("First", "Second"):
            assert "error" not in json.loads(server.create_code(
                name=name, create_backup=True))
        backups = self._backups(marked)
        assert len(backups) == 2
        # Every restore: the swap has already removed the lock file.
        preview = json.loads(server.restore_backup(str(backups[0])))
        restored = json.loads(server.restore_backup(
            str(backups[0]), preview_token=preview["preview_token"]))
        assert restored.get("success") is True, restored
        assert [line for line in _logged(caplog)
                if line.startswith("Could not remove project lock file")] \
            == ["Could not remove project lock file: FileNotFoundError "
                "ENOENT"]
        # The prune's error line, with a removal refused.
        def refuse(path, *args, **kwargs):
            raise PermissionError(13, "Permission denied", str(path))
        preview = json.loads(server.prune_backups(keep_last=0))
        monkeypatch.setattr(shutil, "rmtree", refuse)
        json.loads(server.prune_backups(
            keep_last=0, preview_token=preview["preview_token"]))
        monkeypatch.undo()
        failed = [line for line in _logged(caplog)
                  if line.startswith("Failed to remove backup")]
        assert failed and all(
            line.startswith("Failed to remove backup (project folder name "
                            "withheld)_backup_")
            and line.endswith(": PermissionError EACCES")
            for line in failed), failed
        # A copy into the workspace.
        copied = json.loads(server.copy_project_to_workspace(
            source_path=str(marked)))
        assert "error" not in copied, copied
        assert _log_names(caplog) == []

    @pytest.mark.parametrize("error", [
        FileExistsError(17, "File exists", f"/x/{MARK} study_backup_1.qda"),
        PermissionError(13, "Permission denied", f"/x/{MARK} study.qda")],
        ids=["exists", "refused"])
    def test_the_two_backup_failure_lines_name_no_path(
            self, marked, caplog, monkeypatch, error):
        import shutil
        assert json.loads(server.select_project(str(marked))).get(
            "success") is not False

        def fails(*args, **kwargs):
            raise error
        monkeypatch.setattr(shutil, "copytree", fails)
        caplog.set_level(logging.DEBUG)
        answer = json.loads(server.create_code(name="Third",
                                               create_backup=True))
        assert "error" in answer
        assert _log_names(caplog) == []
        label = dbmod.error_label(error)
        assert f"Failed to create backup: {label}" in _logged(caplog)


def _errors_logged_whole(tree):
    """Every log call, in any handler, that carries the caught error
    other than by its kind (`error_label`, `sqlite_error_label`, or its
    class): an error's text can be a path, a name or a note."""
    import ast
    parents = {child: node for node in ast.walk(tree)
               for child in ast.iter_child_nodes(node)}
    found = []
    for handler in ast.walk(tree):
        if not isinstance(handler, ast.ExceptHandler) or not handler.name:
            continue
        for statement in handler.body:
            for node in ast.walk(statement):
                if not (isinstance(node, ast.Name) and node.id == handler.name
                        and isinstance(node.ctx, ast.Load)):
                    continue
                up, in_log = node, False
                while up is not handler:
                    up = parents[up]
                    if isinstance(up, ast.Call) and \
                            ast.unparse(up.func).startswith("logger."):
                        in_log = True
                        break
                if not in_log:
                    continue
                parent = parents[node]
                if isinstance(parent, ast.Call) and ast.unparse(
                        parent.func) in ("error_label", "sqlite_error_label",
                                         "type"):
                    continue
                found.append(f"line {node.lineno}: {ast.unparse(up)[:80]}")
    return found


class TestNoLogLineCarriesAnErrorsText:

    def test_the_checker_finds_an_error_logged_whole(self):
        import ast
        tree = ast.parse(
            "try:\n    pass\nexcept OSError as e:\n"
            "    logger.warning(f'Could not remove the lock: {e}')\n"
            "try:\n    pass\nexcept OSError as e:\n"
            "    logger.warning('Could not: %s', e)\n"
            "try:\n    pass\nexcept OSError as e:\n"
            "    logger.warning('Could not: %s', error_label(e))\n"
            "    answer = {'error': str(e)}\n")
        assert len(_errors_logged_whole(tree)) == 2

    @pytest.mark.parametrize("module", sorted(
        p.name for p in SOURCE.glob("*.py")))
    def test_no_log_line_carries_an_errors_text(self, module):
        import ast
        tree = ast.parse((SOURCE / module).read_text(encoding="utf-8"))
        assert _errors_logged_whole(tree) == []


class TestTheDocumentsSayWhatTheLogCarries:

    def test_privacy_says_the_log_names_nothing(self):
        privacy = _flat("PRIVACY.md")
        assert ("Since v0.14 the log also names no project, file, code, "
                "category, case, journal entry or attribute, and carries no "
                "path") in privacy

    def test_install_no_longer_says_the_log_carries_names(self):
        install = _flat("INSTALL.md")
        assert "It does carry project file names" not in install
        assert ("They name no project, file, code, category, case, journal "
                "entry or attribute and no path") in install

    def test_both_say_what_a_host_that_records_every_answer_keeps(self):
        """Fix round 1 (the refuter's widening of QA F1): the sentences
        are about the lines this server writes; Claude Desktop's file
        records every request and answer beside them."""
        install = _flat("INSTALL.md")
        privacy = _flat("PRIVACY.md")
        assert ("Claude Desktop's server log (the file Show Logs opens) "
                "also records every request and every answer in full") \
            in install
        assert ("A host may record more in the same file: Claude "
                "Desktop's server log") in privacy
        assert ("The rule closes one channel, SQLite's message; it does "
                "not make a project built to leak safe to open.") in privacy
        assert ("The server replaces the secret by itself, with the same "
                "two effects") in privacy
        assert ("they share one memory of it") in privacy


# =============================================================================
# 4. EVERY READ RE-CHECKS WHETHER THE PROJECT HIDES CODERS
# =============================================================================

HIDDEN = "Hidden Helga"


@pytest.fixture
def arriving(project):
    """Connected before the project could hide a coder; then QualCoder
    opened it and hid one: the column and the four views, as its routine
    makes them, under this server's live connection."""
    add_coding(project, 5, 1, 19, 22, owner=HIDDEN)
    add_coding(project, 6, 2, 63, 68, owner=HIDDEN)
    server.db.close()
    server.db = QualcoderDatabase(str(project))
    assert server.db.capabilities.visibility_declared() is False
    assert server.db.code_text_source() == "code_text"
    return project


def _arrive(project, views=None):
    from test_v012_pseudonymise_tool import (VISIBILITY_COLUMN,
                                             VISIBILITY_VIEWS)
    con = sqlite3.connect(str(project / "data.qda"))
    con.execute(VISIBILITY_COLUMN)
    for ddl in (VISIBILITY_VIEWS if views is None else views):
        con.execute(ddl)
    con.executemany("INSERT OR REPLACE INTO coder_names (name, visibility) "
                    "VALUES (?, ?)", [("TestCoder", 1), (HIDDEN, 0)])
    con.commit()
    con.close()


class TestEveryReadRechecksWhoIsHidden:

    def test_a_read_after_the_setting_arrives_filters_the_hidden_coder(
            self, arriving):
        before = server.get_coded_segments(code_id=1)
        assert HIDDEN in before        # nobody hidden yet: hers is shown
        _arrive(arriving)
        after = json.loads(server.get_coded_segments(code_id=1))
        assert HIDDEN not in json.dumps(after)
        assert after["coder_visibility"]["hidden_coders"] == 1
        assert server.db.code_text_source() == "code_text_visible"

    @pytest.mark.parametrize("read", [
        "get_coded_segments", "search_coded_text", "get_coding_frequencies",
        "find_cooccurring_codes", "analyze_file_with_coding",
        "get_project_summary"])
    def test_every_read_of_codings_answers_as_a_fresh_connection_would(
            self, arriving, read):
        """From the next call on, each read answers exactly what it would
        on a connection opened after the coder was hidden; and the case
        is one where hiding her changes the answer."""
        call = {
            "get_coded_segments": lambda: server.get_coded_segments(
                code_id=1),
            "search_coded_text": lambda: server.search_coded_text(
                query="Tom"),
            "get_coding_frequencies": lambda: server.get_coding_frequencies(),
            "find_cooccurring_codes": lambda: server.find_cooccurring_codes(
                code_id=1),
            "analyze_file_with_coding": lambda:
                server.analyze_file_with_coding(file_id=1),
            "get_project_summary": lambda: server.get_project_summary(),
        }[read]
        before = json.loads(call())
        _arrive(arriving)
        after = json.loads(call())
        server.db.close()
        server.db = QualcoderDatabase(str(arriving))
        assert server.db.capabilities.visibility_declared() is True
        fresh = json.loads(call())
        assert after == fresh
        assert after != before

    def test_the_setting_arriving_without_its_views_is_refused(
            self, arriving):
        from test_v012_pseudonymise_tool import VISIBILITY_VIEWS
        _arrive(arriving, views=VISIBILITY_VIEWS[:2])
        answer = json.loads(server.get_coded_segments(code_id=1))
        assert HIDDEN not in json.dumps(answer)
        assert "coder-visibility views is missing" in answer["error"]

    def test_the_recheck_is_one_way(self, arriving, monkeypatch):
        """Once seen, the setting is kept: a declaration that disappears
        under a live connection is damage, not "nobody is hidden"."""
        _arrive(arriving)
        assert server.db.code_text_source() == "code_text_visible"
        monkeypatch.setattr(QualcoderDatabase, "_visibility_is_declared_now",
                            lambda self, **kwargs: False)
        assert server.db.code_text_source() == "code_text_visible"
        assert HIDDEN not in server.get_coded_segments(code_id=1)

    def test_a_lock_on_the_reread_is_said_as_a_lock(self, arriving,
                                                   monkeypatch):
        """Every read now re-reads the declaration; a locked database met
        there is answered as locked, as it is everywhere else, and
        nothing is read."""
        class Locked:
            def __init__(self, real):
                self._real = real

            def execute(self, sql, *args):
                if "PRAGMA table_info(coder_names)" in sql:
                    raise sqlite3.OperationalError("database is locked")
                return self._real.execute(sql, *args)

            def __getattr__(self, name):
                return getattr(self._real, name)

        server.db.conn = Locked(server.db.conn)
        try:
            answer = json.loads(server.get_coded_segments(code_id=1))
        finally:
            server.db.conn = server.db.conn._real
        assert answer == {"error": dbmod.DB_LOCKED_MESSAGE}

    def test_a_project_declaring_it_at_connect_is_not_reread(
            self, arriving, monkeypatch):
        """The cost falls only where the answer can change: with the
        setting present when the connection opened, no read re-reads it."""
        _arrive(arriving)
        server.db.close()
        server.db = QualcoderDatabase(str(arriving))
        assert server.db.capabilities.visibility_declared() is True
        reread = []
        monkeypatch.setattr(
            QualcoderDatabase, "_visibility_is_declared_now",
            lambda self, **kwargs: reread.append(1) or True)
        assert HIDDEN not in server.get_coded_segments(code_id=1)
        assert reread == []


# Fix round 1 (QA F2 and F3): QualCoder's own order, and one memory.

def _column_only(project):
    """The first half of QualCoder's `update_coder_names`: the table with
    its `visibility` column, committed on its own before the views."""
    from test_v012_pseudonymise_tool import VISIBILITY_COLUMN
    con = sqlite3.connect(str(project / "data.qda"))
    con.execute(VISIBILITY_COLUMN)
    con.commit()
    con.close()


def _views_and_rows(project):
    """The second half: the four views and the rows, committed together."""
    from test_v012_pseudonymise_tool import VISIBILITY_VIEWS
    con = sqlite3.connect(str(project / "data.qda"))
    for ddl in VISIBILITY_VIEWS:
        con.execute(ddl)
    con.executemany("INSERT OR REPLACE INTO coder_names (name, visibility) "
                    "VALUES (?, ?)", [("TestCoder", 1), (HIDDEN, 0)])
    con.commit()
    con.close()


def _lose_the_declaration(project):
    """The declaration and its views gone under the live connection (a
    damaged project, or another tool; never QualCoder). The column is
    dropped by rebuilding the table, which every SQLite can do."""
    con = sqlite3.connect(str(project / "data.qda"))
    for view in ("code_text_visible", "code_image_visible",
                 "code_av_visible", "annotation_visible"):
        con.execute(f"DROP VIEW IF EXISTS {view}")
    con.execute("CREATE TABLE names_kept AS SELECT name FROM coder_names")
    con.execute("DROP TABLE coder_names")
    con.execute("ALTER TABLE names_kept RENAME TO coder_names")
    con.commit()
    con.close()


class TestAnArrivalInQualCodersOwnOrder:

    def test_a_read_between_the_two_commits_does_not_stick(self, arriving):
        """QA F2: a read that lands after the column and before the views
        is refused; once QualCoder has committed the views, the next read
        answers, filtered, without selecting the project again."""
        _column_only(arriving)
        between = json.loads(server.get_coded_segments(code_id=1))
        assert "coder-visibility views is missing" in between["error"]
        _views_and_rows(arriving)
        after = json.loads(server.get_coded_segments(code_id=1))
        assert "error" not in after, after
        assert HIDDEN not in json.dumps(after)
        assert after["coder_visibility"]["hidden_coders"] == 1

    @pytest.mark.parametrize("first", ["a_read", "a_naming_decision"])
    def test_a_declaration_seen_by_either_path_is_never_withdrawn(
            self, arriving, first):
        """QA F3: one memory for the reads and the decisions that name a
        coder. Whichever saw the arrival first, a declaration that then
        disappears under the live connection names nobody hidden: the
        reads refuse and the comparison does not list her."""
        _arrive(arriving)
        if first == "a_read":
            assert HIDDEN not in server.get_coded_segments(code_id=1)
        else:
            assert HIDDEN not in server.compare_coders()
        _lose_the_declaration(arriving)
        for answer in (server.get_coded_segments(code_id=1),
                       server.search_coded_text(query="Tom"),
                       server.get_coding_frequencies(),
                       server.compare_coders()):
            assert HIDDEN not in answer


# =============================================================================
# FIX ROUND 1: RESOURCES OVER THE WIRE, ON A REAL STDIO SERVER
# =============================================================================
#
# The MCP library logs every error a resource raises with its traceback, at
# ERROR, to the server's standard error, which is the host's log. Here the
# server runs as a real subprocess, its standard error kept whole as a host
# keeps it, and the participant's name the project folder carries is looked
# for in every line (QA F1, Security secB-1, and their refuters' scenarios).

CONCRETE_RESOURCES = [
    "qualcoder://project/info", "qualcoder://codes/list",
    "qualcoder://categories/list", "qualcoder://files/list",
    "qualcoder://cases/list", "qualcoder://journal",
    "qualcoder://guidance/methods"]
TEMPLATE_RESOURCES = ["qualcoder://codes/1", "qualcoder://files/1",
                      "qualcoder://cases/1"]


class _Wire:
    """One home, one state folder, a project named after a participant,
    and a way to run a server session and keep its standard error."""

    def __init__(self, root):
        from test_v012_pseudonymise_tool import build_project
        from track5_helpers import write_fixture_sidecar
        self.root = root
        self.home = root / "home"
        self.home.mkdir()
        self.research = root / "Research" / f"{MARK} study"
        self.project = build_project(self.research / f"{MARK} study.qda")
        write_fixture_sidecar(str(self.project))
        self.sessions = 0

    def env(self, configured=None):
        import os
        env = {k: v for k, v in os.environ.items()
               if k != "QUALCODER_PROJECT_PATH"}
        env.update(HOME=str(self.home), USERPROFILE=str(self.home),
                   QUALCODER_MCP_STATE_HOME=str(self.home / ".qualcoder_mcp"),
                   PYTHONPATH=str(Path(server.__file__).parents[1]),
                   PYTHONDONTWRITEBYTECODE="1")
        if configured is not None:
            env["QUALCODER_PROJECT_PATH"] = str(configured)
        return env

    def run(self, steps, configured=None, during=None):
        """Run `steps` (("tool" or "read", name, arguments)) in a fresh
        server; `during(step_index)` may return a context manager held
        around that step. Returns (answers, standard error)."""
        import asyncio
        import contextlib
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        self.sessions += 1
        errfile = self.root / f"stderr-{self.sessions}.log"
        params = StdioServerParameters(
            command=sys.executable, args=["-B", "-m", "qualcoder_mcp.server"],
            env=self.env(configured))

        async def session():
            answers = []
            with open(errfile, "w", encoding="utf-8") as err:
                async with stdio_client(params, errlog=err) as (r, w):
                    async with ClientSession(r, w) as s:
                        await s.initialize()
                        for index, (kind, name, args) in enumerate(steps):
                            held = (during(index) if during
                                    else contextlib.nullcontext())
                            with held:
                                if kind == "tool":
                                    res = await s.call_tool(name, args or {})
                                    answers.append("".join(
                                        getattr(b, "text", "")
                                        for b in res.content))
                                else:
                                    res = await s.read_resource(name)
                                    answers.append("".join(
                                        getattr(c, "text", "")
                                        for c in res.contents))
            return answers

        answers = asyncio.run(asyncio.wait_for(session(), 180))
        return answers, errfile.read_text(encoding="utf-8")


def _every_resource():
    return [("read", uri, None)
            for uri in CONCRETE_RESOURCES + TEMPLATE_RESOURCES]


def _named_lines(stderr):
    return [line for line in stderr.splitlines() if MARK in line]


class TestResourcesLogNothingOverTheWire:

    def test_a_last_used_project_and_nothing_selected(self, tmp_path):
        """A new conversation on a machine that has selected a project
        before: every resource read first. The answers carry the hint to
        the last-used project, as a tool's do; the log names nothing."""
        wire = _Wire(tmp_path)
        answers, _ = wire.run([("tool", "select_project",
                                {"project_path": str(wire.project)})])
        assert json.loads(answers[0])["success"] is True
        answers, stderr = wire.run(
            _every_resource() + [("tool", "get_project_summary", None)])
        assert _named_lines(stderr) == [], stderr
        assert "Error reading resource" not in stderr
        first = json.loads(answers[0])
        assert first["error"].startswith("No Qualcoder project selected")
        assert "The last project used on this machine was" in first["error"]

    def test_a_misconfigured_project_path(self, tmp_path):
        """QUALCODER_PROJECT_PATH set to the folder that holds the project,
        which the start-up check lets through (it exists)."""
        wire = _Wire(tmp_path)
        answers, stderr = wire.run(_every_resource(),
                                   configured=wire.research)
        assert _named_lines(stderr) == [], stderr
        assert "Error reading resource" not in stderr
        # v0.14 brief D, fix round 1: one text for a configured project
        # that cannot be opened, in every tool, without the path (it
        # used to be "Directory must have .qda extension: <path>")
        error = json.loads(answers[0])["error"]
        assert error == server.CONFIGURED_PROJECT_UNAVAILABLE
        assert str(wire.research) not in error

    def test_a_lost_connection(self, tmp_path):
        """Selected; a mistyped second selection drops the connection; the
        next read tries to reconnect while another program holds the
        database, fails, and answers the no-project text with its hint."""
        import contextlib
        wire = _Wire(tmp_path)

        @contextlib.contextmanager
        def held():
            other = sqlite3.connect(str(wire.project / "data.qda"))
            other.execute("BEGIN EXCLUSIVE")
            try:
                yield
            finally:
                other.rollback()
                other.close()

        steps = [("tool", "select_project",
                  {"project_path": str(wire.project)}),
                 ("tool", "select_project",
                  {"project_path": str(wire.project.parent
                                       / f"{MARK} typo.qda")}),
                 ("read", "qualcoder://codes/list", None)]
        answers, stderr = wire.run(
            steps, during=lambda i: held() if i == 2
            else contextlib.nullcontext())
        assert json.loads(answers[1])["success"] is False
        assert "Database connection lost" in stderr
        assert _named_lines(stderr) == [], stderr
        assert "Error reading resource" not in stderr
        assert json.loads(answers[2])["error"].startswith(
            "No Qualcoder project selected")

    def test_a_file_system_error(self, tmp_path):
        """A configured project folder whose data.qda is missing: the
        file-system arm answers its fixed text, the log its kind (QA's
        surviving mutation Q5)."""
        wire = _Wire(tmp_path)
        (wire.project / "data.qda").unlink()
        answers, stderr = wire.run(_every_resource(),
                                   configured=wire.project)
        assert _named_lines(stderr) == [], stderr
        assert "Error reading resource" not in stderr
        # v0.14 brief D, fix round 1: a configured project answers its
        # one text; the log keeps the kind
        assert json.loads(answers[0]) == {
            "error": server.CONFIGURED_PROJECT_UNAVAILABLE}
        assert "Failed to connect to database: FileNotFoundError" in stderr

    def test_the_file_system_arm_answers_its_fixed_text(self):
        """The arm the test above used to reach through a configured
        project (QA's mutation Q5), now reached directly."""
        answer = json.loads(server._error_answer(
            "get_project_info",
            FileNotFoundError("Path not found: /x/Thomas study.qda")))
        assert answer == {"error": "File or project not found."}


# =============================================================================
# FIX ROUND 1: THE SCHEMA VERSION, AND pseudonyms.json'S ERRORS
# =============================================================================

class TestTheSchemaVersionInTheLog:

    @pytest.mark.parametrize("planted", [NOTE, f"v14 {NOTE}"],
                             ids=["a_note", "a_note_after_a_version"])
    def test_a_version_holding_a_note_is_not_logged(self, project, caplog,
                                                    planted):
        """Security secB-2: `databaseversion` is the project's own text;
        a trigger copying a note into it put the note, private part
        included, into the WARNING every connection writes. Set directly
        and then by the trigger QualCoder's own memo edit fires, across a
        selection, a write (whose downgrade reconnects) and a read. The
        second value starts as a version does, so only the whole shape
        lets a value through."""
        _ddl(project, f"UPDATE project SET databaseversion = '{planted}'")
        caplog.set_level(logging.DEBUG)
        server.select_project(str(project))
        server.get_current_project()
        _ddl(project,
             "UPDATE project SET databaseversion = 'v14'",
             "CREATE TRIGGER copy AFTER UPDATE OF memo ON code_name BEGIN "
             "UPDATE project SET databaseversion = new.memo; END")
        server.set_memo(target_type="code", target_id=1, memo=NOTE,
                        create_backup=False)
        server.get_project_summary()
        assert SENTINEL not in caplog.text
        assert "Met Thomas" not in caplog.text
        assert ("Untested database version: (a value not in QualCoder's "
                "form, withheld)") in caplog.text

    def test_a_version_in_qualcoders_form_is_still_logged(self, project,
                                                         caplog):
        _ddl(project, "UPDATE project SET databaseversion = 'v99'")
        caplog.set_level(logging.DEBUG)
        server.get_project_summary()
        assert "Untested database version: v99." in caplog.text


class TestPseudonymsJsonErrorsAreAnsweredByKind:

    def _unreadable(self, project, monkeypatch, error):
        from qualcoder_mcp import database as database_module
        (project / "pseudonyms.json").write_text(
            '[{"original": "Thomas", "pseudonym": "Alex"}]',
            encoding="utf-8")

        def fails(path):
            raise error
        monkeypatch.setattr(database_module, "_read_pseudonyms_json_bytes",
                            fails)

    ERRORS = {
        "permission": PermissionError(
            13, "Permission denied", f"/x/{MARK} study.qda/pseudonyms.json"),
        "loop_before_3_13": RuntimeError(
            f"Symlink loop from '/x/{MARK} study.qda/pseudonyms.json'"),
        "nested_too_deep": RecursionError(
            "maximum recursion depth exceeded while decoding a JSON array"),
    }

    @pytest.mark.parametrize("kind", sorted(ERRORS))
    @pytest.mark.parametrize("route", ["import_text_file",
                                       "pseudonymise_source",
                                       "get_current_project",
                                       "read_pseudonym_list"])
    def test_the_answer_names_the_kind_not_the_path(
            self, project, monkeypatch, route, kind):
        self._unreadable(project, monkeypatch, self.ERRORS[kind])
        raw = {
            "import_text_file": lambda: server.import_text_file(
                filename="fresh.txt", content="Words.",
                apply_project_pseudonyms=True, create_backup=False),
            "pseudonymise_source": lambda: server.pseudonymise_source(
                file_id=1, use_project_pseudonyms=True),
            "get_current_project": lambda: server.get_current_project(),
            "read_pseudonym_list": lambda: server.read_pseudonym_list(),
        }[route]()
        assert MARK not in raw
        answer = json.loads(raw)
        label = dbmod.error_label(self.ERRORS[kind])
        expected = f"pseudonyms.json could not be read ({label})."
        if route in ("get_current_project", "read_pseudonym_list"):
            assert expected in json.dumps(answer), answer
            assert "Failed to get project info" not in raw
        else:
            assert answer["error"] == expected


class TestOpeningADatabaseChainsNothing:
    """The six raises in `QualcoderDatabase.__init__` that wrap a SQLite
    error are `from None` (fix round 1): a chained cause is printed
    whole by any traceback, and a traceback is what a library logs."""

    STAGES = {
        "open": "PRAGMA foreign_keys = ON",
        "schema": "SELECT name FROM sqlite_master WHERE type='table'",
        "required_columns": "PRAGMA table_info(code_text)",
    }

    @pytest.mark.parametrize("kind", ["operational", "other"])
    @pytest.mark.parametrize("stage", sorted(STAGES))
    def test_the_wrapper_carries_no_cause(self, project, monkeypatch,
                                          stage, kind):
        import traceback
        real_connect = sqlite3.connect
        target = self.STAGES[stage]
        error = (sqlite3.OperationalError if kind == "operational"
                 else sqlite3.DatabaseError)

        class Faulty:
            def __init__(self, real):
                object.__setattr__(self, "_real", real)

            def execute(self, sql, *args):
                if sql == target:
                    raise error(NOTE)
                return self._real.execute(sql, *args)

            def __getattr__(self, name):
                return getattr(self._real, name)

            def __setattr__(self, name, value):
                setattr(self._real, name, value)

        monkeypatch.setattr(dbmod.sqlite3, "connect",
                            lambda *a, **k: Faulty(real_connect(*a, **k)))
        with pytest.raises(RuntimeError) as raised:
            QualcoderDatabase(str(project))
        assert raised.value.__cause__ is None
        assert raised.value.__suppress_context__ is True
        printed = "".join(traceback.format_exception(raised.value))
        assert SENTINEL not in printed and "Met Thomas" not in printed
