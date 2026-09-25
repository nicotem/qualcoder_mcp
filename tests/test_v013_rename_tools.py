# SPDX-License-Identifier: LGPL-3.0-or-later
"""v0.13: rename_case and rename_file (RENAME_TOOLS_DOSSIER.md, section 3;
the owner's rulings of 2026-09-23).

Parity pins carry QualCoder's own rules transcribed as small pure
functions, from master 9bddf17 and tag 3.8.2 (identical in both where
cited): Manage Cases' name edit (cases.py:706-719; 3.8.2 :637-648) and
Manage Files' "Rename database entry" with its dialog
(manage_files.py:1488-1503 and add_item_name.py:72-82; 3.8.2 :773-788
and :74-84). A fixed list of inputs runs through them and through the
tools, and every difference must be one of the departures the dossier
names. The statements the tools execute are pinned by a dump of every
table before and after.

Windows-safe: no paths beyond the tmp fixtures, no wall-clock waits.
"""

import json
import logging
import sqlite3
import time
import unicodedata
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server
from qualcoder_mcp.database import (QUALCODER_LOCK_FILENAME, name_key,
                                    normalize_name, validate_qda_path)


# ---------------------------------------------------------------------------
# QualCoder's rules, transcribed
# ---------------------------------------------------------------------------

def upstream_manage_cases(cell_text, cases):
    """Manage Cases' cell_modified, name column (master cases.py:706-719;
    3.8.2 :637-648): the value written, or None for the silent revert."""
    value = str(cell_text).strip()
    update = True
    if value == "":
        update = False
    for c in cases:
        if c['name'] == value:
            update = False
    return value if update else None


def upstream_rename_entry(text, sources):
    """Manage Files' rename (master manage_files.py:1488-1503; 3.8.2
    :773-788) through DialogAddItemName.accept (add_item_name.py:72-82;
    3.8.2 :74-84): the name written, or None when the dialog refuses."""
    existing_items = [s['name'] for s in sources]
    this_item = str(text)
    if this_item in existing_items:
        return None
    return this_item


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _con(project):
    con = sqlite3.connect(str(Path(project) / "data.qda"))
    con.row_factory = sqlite3.Row
    return con


def _rows(project, sql, args=()):
    con = _con(project)
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()


def _exec(project, sql, args=()):
    con = _con(project)
    try:
        con.execute(sql, args)
        con.commit()
    finally:
        con.close()


def _reload():
    server.switch_project(server.current_project_path)


def _backups(project):
    project = Path(project)
    return sorted(project.parent.glob(f"{project.stem}_backup_*"))


def _dump(project):
    """Every row of every table, as text, keyed by table."""
    con = _con(project)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "ORDER BY name")]
        return {t: [tuple(r) for r in con.execute(
                    f"SELECT * FROM {t} ORDER BY rowid")] for t in tables}
    finally:
        con.close()


def _disk(project):
    """The project folder's files and their bytes (the database aside)."""
    root = Path(project)
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*"))
            if p.is_file() and not p.name.startswith("data.qda")}


def _case(case_id, name, **kw):
    return json.loads(server.rename_case(case_id, name, **kw))


def _file(file_id, name, **kw):
    return json.loads(server.rename_file(file_id, name, **kw))


def _add_case(project, name, caseid=None):
    _exec(project, "INSERT INTO cases (caseid, name, memo, owner, date) "
                   "VALUES (?, ?, '', 'gui_user', '2024-01-15')",
          (caseid, name))


def _add_file(project, fid, name, mediapath=None, av_text_id=None,
              fulltext="Some text."):
    _exec(project, "INSERT INTO source (id, name, fulltext, mediapath, "
                   "memo, owner, date, av_text_id) VALUES "
                   "(?, ?, ?, ?, '', 'gui_user', '2024-01-15', ?)",
          (fid, name, fulltext, mediapath, av_text_id))


@pytest.fixture
def project(setup_server, qualcoder_db_path):
    """The conftest project: case 1 'Case A'; files 1 'interview.txt' and
    2 'notes.txt', both with no stored path."""
    return Path(qualcoder_db_path)


# ===========================================================================
# rename_case
# ===========================================================================

class TestRenameCase:

    def test_only_the_name_column_changes(self, project):
        before, disk = _dump(project), _disk(project)
        out = _case(1, "P01")
        assert out["success"] is True and out["changed"] is True
        assert (out["case_id"], out["old_name"], out["new_name"]) == \
            (1, "Case A", "P01")
        after = _dump(project)
        assert after["cases"] == [(1, "P01", "First case", "TestCoder",
                                   "2024-01-15")]
        # The date is untouched, by parity with Manage Cases.
        assert {t: v for t, v in after.items() if t != "cases"} == \
            {t: v for t, v in before.items() if t != "cases"}
        assert _disk(project) == disk
        assert out["backup_path"] and Path(out["backup_path"]).exists()

    def test_the_log_carries_the_id_never_a_name(self, project, caplog):
        with caplog.at_level(logging.DEBUG):
            _case(1, "Parity Person", create_backup=False)
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "Renamed case 1" in text
        assert "Case A" not in text and "Parity Person" not in text

    def test_empty_and_unknown_are_answered_without_a_backup(self, project):
        assert _case(1, "  \t ")["error"] == \
            "new_name must be a non-empty string"
        assert _case(99, "x")["error"] == "Case ID 99 does not exist"
        assert _backups(project) == []

    def test_the_identical_name_is_unchanged(self, project):
        out = _case(1, "  Case   A ")
        assert out == {"changed": False, "reason": "unchanged",
                       "message": "Case 'Case A' (id 1) already has that "
                                  "name; nothing was written.",
                       "case": {"id": 1, "name": "Case A"}}
        assert _backups(project) == []

    def test_a_clash_with_another_case_names_its_id(self, project):
        _add_case(project, "Émilie", 5)
        _reload()
        before = _dump(project)
        out = _case(1, "  ÉMILIE ")
        assert out["error"] == "Another case already uses the name " \
                               "'Émilie' (id 5)."
        assert out["candidates"] == [{"id": 5, "name": "Émilie"}]
        assert _dump(project) == before and _backups(project) == []

    def test_a_respelling_of_its_own_name_proceeds(self, project):
        out = _case(1, "CASE a", create_backup=False)
        assert out["changed"] is True and out["new_name"] == "CASE a"

    def test_a_pre_existing_twin_blocks_a_respelling(self, project):
        _add_case(project, "tom", 5)
        _add_case(project, "Tom", 6)
        _reload()
        out = _case(5, "TOM")
        assert out["error"] == "Another case already uses the name " \
                               "'Tom' (id 6)."

    def test_a_stored_name_outside_normal_form_is_rewritten(self, project):
        _add_case(project, "Tom  P01", 5)
        nfd = unicodedata.normalize("NFD", "Zoë")
        _add_case(project, nfd, 6)
        _reload()
        out = _case(5, "Tom  P01", create_backup=False)
        assert out["changed"] is True and out["new_name"] == "Tom P01"
        out = _case(6, unicodedata.normalize("NFC", "Zoë"),
                    create_backup=False)
        # normalize_name does not apply NFC: the NFC spelling is a change.
        assert out["changed"] is True

    def test_a_stored_twin_in_normal_form_blocks_the_rewrite(self, project):
        _add_case(project, "Tom  P01", 5)
        _add_case(project, "Tom P01", 6)
        _reload()
        out = _case(5, "Tom  P01")
        assert out["error"] == "Another case already uses the name " \
                               "'Tom P01' (id 6)."

    def test_the_gate_comes_first(self, project):
        lock = validate_qda_path(str(project)).parent / \
            QUALCODER_LOCK_FILENAME
        lock.write_text(f"gui_user\n{time.time()}", encoding="utf-8")
        try:
            for args in ((1, "Case A"), (99, "x"), (1, "New")):
                out = _case(*args)
                assert "open in QualCoder (user gui_user)" in out["error"]
        finally:
            lock.unlink()
        assert _backups(project) == []


SAVED_PLACES_DDL = (
    # QualCoder's own DDL (master __main__.py:1835-1838, :1861-1862;
    # 3.8.2 :2395-2398, :2416-2417).
    "CREATE TABLE gr_case_text_item (gcaseid integer primary key, grid "
    "integer, x integer, y integer, caseid integer, font_size integer, "
    "bold integer, color text, displaytext text)",
    "CREATE TABLE gr_file_text_item (gfileid integer primary key, grid "
    "integer, x integer, y integer, fid integer, font_size integer, bold "
    "integer, color text, displaytext text)",
    "CREATE TABLE manage_files_display (mfid integer primary key, name "
    "text, tblrows text, tblcolumns text, owner text)",
    "CREATE TABLE files_filter (filterid integer primary key, name text, "
    "filter text, owner text)",
)


def _saved_places(project):
    for ddl in SAVED_PLACES_DDL:
        _exec(project, ddl)


def _save_display(project, name, rows):
    """A saved Manage Files display as QualCoder writes one: its rows
    '<column>\t<operator>\t<value>' joined by two tabs (master
    manage_files.py:752, :1169, :1176, :1183, transcribed)."""
    tblrows = "\t\t".join(f"{col}\t{op}\t{value}" for col, op, value in rows)
    _exec(project, "INSERT INTO manage_files_display (name, tblrows, "
                   "tblcolumns, owner) VALUES (?, ?, 'Name\t100\t\t', "
                   "'gui_user')", (name, tblrows))


def _save_filter(project, name, boolean, conditions):
    """A saved attribute filter as QualCoder writes one: the text of the
    parameter list (master report_attributes.py:143, :268-310,
    transcribed), character values in single quotes."""
    parameters = [[boolean]]
    for attr, case_or_file, type_, op, values in conditions:
        if type_ == "character":
            values = [f"'{v}'" for v in values]
        parameters.append([attr, case_or_file, type_, op, values])
    _exec(project, "INSERT INTO files_filter (name, filter, owner) "
                   "VALUES (?, ?, 'gui_user')", (name, parameters.__str__()))


class TestRenameCaseReportsWhereTheOldNameStays:

    def test_each_count_only_when_not_zero(self, project):
        _saved_places(project)
        _exec(project, "INSERT INTO gr_case_text_item (grid, caseid, "
                       "displaytext) VALUES (1, 1, 'CASE A'), "
                       "(1, 1, ''), (2, 1, 'Case A (P)'), (1, 9, 'Case A')")
        _exec(project, "INSERT INTO manage_files_display (name, tblrows) "
                       "VALUES ('mine', 'Case\t=\tcase a'), ('x', 'Name')")
        _add_file(project, 7, "Case A_interview.txt")
        _add_file(project, 8, "survey_case a")
        _reload()
        out = _case(1, "P01", create_backup=False)
        assert out["old_name_left_in"] == {
            "saved_graph_labels": 2, "saved_table_displays": 1,
            "file_ids": [7, 8]}
        assert out["note"] == server.RENAME_CASE_NOTE
        assert "Merge Projects" in out["note"]

    def test_missing_tables_are_skipped_and_nothing_found_is_empty(
            self, project):
        # The conftest schema has none of the four saved-place tables.
        out = _case(1, "P01", create_backup=False)
        assert out["old_name_left_in"] == {}

    def test_the_counts_carry_no_name(self, project):
        _add_file(project, 7, "Case A.txt")
        _reload()
        out = _case(1, "P01", create_backup=False)
        assert "Case A" not in json.dumps(out["old_name_left_in"])


class TestRenameCaseRaces:

    def test_a_clash_between_precheck_and_write_is_refused(
            self, project, monkeypatch):
        """The re-check runs under BEGIN IMMEDIATE, on the write
        connection; a clash found there keeps the text and the backup
        path, and nothing is written."""
        original = server.QualcoderDatabase.begin_immediate

        def racing(self):
            _add_case(project, "p01", 9)
            original(self)
        monkeypatch.setattr(server.QualcoderDatabase, "begin_immediate",
                            racing)
        out = _case(1, "P01")
        assert out["error"] == "Another case already uses the name " \
                               "'p01' (id 9)."
        assert "backup_path" in out and "candidates" not in out
        assert _rows(project, "SELECT name FROM cases WHERE caseid = 1") \
            == [{"name": "Case A"}]

    def test_the_re_check_runs_inside_the_immediate_transaction(
            self, project, monkeypatch):
        calls = []
        original = server.QualcoderDatabase.begin_immediate
        original_rows = server.QualcoderDatabase.case_name_rows

        def spy(self):
            calls.append(("begin", self.conn.in_transaction))
            original(self)

        def rows(self):
            calls.append(("rows", self.conn.in_transaction))
            return original_rows(self)
        monkeypatch.setattr(server.QualcoderDatabase, "begin_immediate", spy)
        monkeypatch.setattr(server.QualcoderDatabase, "case_name_rows", rows)
        assert _case(1, "P01", create_backup=False)["changed"] is True
        # The read-only pre-check, then the lock, then the re-check.
        assert calls == [("rows", False), ("begin", False), ("rows", True)]

    def test_a_unique_failure_at_the_update_is_the_clash_answer(
            self, project):
        _exec(project, "CREATE TRIGGER t BEFORE UPDATE ON cases BEGIN "
                       "SELECT RAISE(ABORT, 'UNIQUE constraint failed: "
                       "cases.name'); END")
        out = _case(1, "P01")
        assert out["error"] == "A case named 'P01' already exists"
        assert "backup_path" in out


class TestRenameCaseSurface:

    def test_full_only(self):
        assert "rename_case" not in server.CORE_TOOLSET
        assert "rename_file" not in server.CORE_TOOLSET
        removed = server._apply_toolset("core")
        try:
            names = {t.name for t in __import__("asyncio").run(
                server.mcp.list_tools())}
            assert "rename_case" not in names and "rename_file" not in names
        finally:
            for name, tool in removed.items():
                server.mcp._tool_manager._tools[name] = tool

    @pytest.mark.parametrize("tool, window", [
        (server.rename_case, "Cases"), (server.rename_file, "Files")])
    def test_the_descriptions_carry_both_qualcoder_paragraphs(
            self, tool, window):
        flat = " ".join(tool.__doc__.split())
        standard = " ".join(server.rename_code.__doc__.split()).split(
            "Refused while QualCoder has the project open")[1].split(
            "Args:")[0]
        assert ("Refused while QualCoder has the project open"
                + standard) in flat
        assert " ".join(server._RENAME_QC40_PARAGRAPH.format(
            window=window).split()) in flat

    def test_the_case_ambiguity_hint_names_rename_case(self, project):
        for name in ("Case  one", "Case one"):
            _add_case(project, name)
        _reload()
        out = json.loads(server.create_case("case one"))
        assert "rename_case takes a case id; cases are merged in " \
               "QualCoder, not here" in out["error"]


# ===========================================================================
# The transcribed interface predicates: every difference is a departure
# the dossier names (3.2 and 3.3, "Departures from ...").
# ===========================================================================

NFD_EMILIE = unicodedata.normalize("NFD", "Émilie")

# (other cases, the renamed case's stored name or None for case 1, typed)
CASE_INPUTS = [
    ([], None, "P01"), ([], None, "  P01  "), ([], None, "\tP01\n"),
    ([], None, "P  01"), ([], None, "A B"), ([], None, ""),
    ([], None, "   "), ([], None, "Case A"), ([], None, "case a"),
    ([], None, "P​01"), (["Tom"], None, "tom"), (["Tom"], None, "Tom"),
    (["Émilie"], None, NFD_EMILIE), (["Straße"], None, "STRASSE"),
    ([], "Tom  P01", "Tom  P01"), (["Tom P01"], "Tom  P01", "Tom  P01"),
]


def _case_departure(up, ours, typed, others):
    """The listed departure a difference is, or None for the same outcome."""
    if up == ours:
        return None
    if ours is not None and ours == normalize_name(typed):
        return "whitespace runs collapse"
    if ours is None and any(name_key(o) == name_key(typed) for o in others):
        return "duplicate-names rule"
    raise AssertionError(f"unlisted departure: {up!r} -> {ours!r}")


@pytest.mark.parametrize("others, stored, typed", CASE_INPUTS)
def test_rename_case_differs_from_manage_cases_only_as_listed(
        project, others, stored, typed):
    for i, name in enumerate(others):
        _add_case(project, name, 20 + i)
    target = 1
    if stored is not None:
        _add_case(project, stored, 50)
        target = 50
    _reload()
    cases = _rows(project, "SELECT caseid, name FROM cases")
    up = upstream_manage_cases(typed, cases)
    out = _case(target, typed, create_backup=False)
    ours = out.get("new_name") if out.get("changed") else None
    if ours is None:
        assert "error" in out or out.get("reason") == "unchanged", out
    departure = _case_departure(up, ours, typed, others)
    assert departure in (None, "whitespace runs collapse",
                         "duplicate-names rule")


def _documents_file(project, name):
    folder = Path(project) / "documents"
    folder.mkdir(exist_ok=True)
    (folder / name).write_text("another file's copy", encoding="utf-8")


# (setup, typed, the refusal's opening words or None): file 1 is
# 'interview.txt', a text with no stored path; file 2 is 'notes.txt'.
FILE_INPUTS = [
    (None, "P01.txt", None), (None, "  P01.txt ", None),
    (None, unicodedata.normalize("NFD", "café.txt"), None),
    (None, "", "A file name must not be empty"),
    (None, "   ", "A file name must not be empty"),
    (None, "...", "A file name must not be empty"),
    (None, "a/b.txt", "A file name must not contain path separators"),
    (None, "..\\x.txt", "A file name must not contain path separators"),
    (None, "D:x.txt", "A file name must not contain path separators"),
    (None, "x" * 197 + ".txt", "A file name must be at most 200 bytes"),
    (None, "\U0001d49c" * 50 + ".txt", "A file name must be at most 200 "
                                       "bytes"),
    (None, "a​b.txt", "A file name must not contain invisible"),
    (None, "a\nb.txt", "A file name must not contain control"),
    (None, "a?b.txt", "A file name must not contain < > | ? *"),
    (None, "P01.txt.", "A file name must not end with a dot"),
    (None, "nul.txt", "A file name must not be a Windows device name"),
    (None, "notes.txt", "Another file already uses the name"),
    (None, "Notes.txt", None), (None, "interview.txt", None),
    (None, "P01", None),
    (None, "P01.docx", "This text has no stored file"),
    (None, "P01.pdf", "The name would gain the ending '.pdf'"),
    (None, "P01.transcribed", "The name would gain the ending "
                              "'.transcribed'"),
    (lambda p: _documents_file(p, "other.txt"), "other.txt",
     "The project's documents folder already holds"),
    (lambda p: _add_file(p, 9, ""), "unnamed_file_9", "File id 9 has an "
                                                      "empty"),
]

FILE_DEPARTURES = {
    "A file name must not be empty": "empty, spaces-only, dots-only",
    "A file name must not contain path separators": "path characters",
    "A file name must be at most": "length",
    "A file name must not contain": "invisible or control characters, "
                                    "or characters Windows cannot store",
    "A file name must not end with a dot": "a name Windows cannot store",
    "A file name must not be a Windows device name":
        "a name Windows cannot store",
    "Another file already uses the name": "collision after NFC",
    "This text has no stored file": "ending rule",
    "The name would gain the ending": "ending rule",
    "The project's documents folder already holds": "documents clash",
    "File id 9 has an empty": "unnamed_file_<n>",
}


@pytest.mark.parametrize("setup, typed, refusal", FILE_INPUTS)
def test_rename_file_differs_from_rename_entry_only_as_listed(
        project, setup, typed, refusal):
    if setup is not None:
        setup(project)
    _reload()
    sources = _rows(project, "SELECT name FROM source")
    up = upstream_rename_entry(typed, sources)
    out = _file(1, typed, create_backup=False)
    if refusal is not None:
        assert out["error"].startswith(refusal), out
        assert any(refusal.startswith(k) for k in FILE_DEPARTURES)
        return
    ours = out.get("new_name") if out.get("changed") else None
    if up == ours:
        return
    if ours is None:
        # Nothing written on either side: QualCoder's "This already
        # exists" and our unchanged answer.
        assert up is None and out["reason"] == "unchanged"
    else:
        # The one other departure: ends trimmed, NFC applied.
        assert ours == unicodedata.normalize("NFC", typed.strip()) != up


# ===========================================================================
# rename_file
# ===========================================================================

class TestRenameFile:

    def test_only_the_name_column_changes_and_nothing_on_disk(self, project):
        _add_file(project, 5, "Thomas_interview.docx",
                  mediapath="/docs/Thomas_interview.docx")
        _documents_file(project, "Thomas_interview.docx")
        _reload()
        before, disk = _dump(project), _disk(project)
        out = _file(5, "P01_interview.docx")
        assert out["success"] is True and out["changed"] is True
        assert (out["file_id"], out["old_name"], out["new_name"],
                out["file_type"]) == (5, "Thomas_interview.docx",
                                      "P01_interview.docx", "text")
        after = _dump(project)
        changed = [(b, a) for b, a in zip(before["source"], after["source"])
                   if b != a]
        assert changed == [(
            (5, "Thomas_interview.docx", "Some text.",
             "/docs/Thomas_interview.docx", "", "gui_user", "2024-01-15",
             None, None),
            (5, "P01_interview.docx", "Some text.",
             "/docs/Thomas_interview.docx", "", "gui_user", "2024-01-15",
             None, None))]
        assert {t: v for t, v in after.items() if t != "source"} == \
            {t: v for t, v in before.items() if t != "source"}
        assert _disk(project) == disk

    def test_the_log_carries_the_id_never_a_name(self, project, caplog):
        with caplog.at_level(logging.DEBUG):
            _file(1, "Parity_person.txt", create_backup=False)
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "Renamed file 1" in text
        assert "interview" not in text and "Parity_person" not in text

    def test_unchanged_comes_before_every_rule(self, project):
        for fid, stored in ((5, "no extension"), (6, "a..b.txt"),
                            (7, "x" * 250 + ".txt")):
            _add_file(project, fid, stored)
        _reload()
        for fid, typed in ((5, " no extension "), (6, "a..b.txt"),
                           (7, "x" * 250 + ".txt")):
            out = _file(fid, typed)
            assert out["changed"] is False and out["reason"] == "unchanged"
            assert out["file"]["id"] == fid
        assert _backups(project) == []

    @pytest.mark.parametrize("typed, error", [
        # No limit in characters (fix round 1, QA-1): 101 and 196 ASCII
        # characters, and 98 two-byte letters, are all within 200 bytes.
        ("x" * 97 + ".txt", None),
        ("x" * 196 + ".txt", None),                        # 200 bytes
        ("x" * 197 + ".txt", "A file name must be at most 200 bytes in "
                             "UTF-8 (this one has 201)."),
        ("\u00e9" * 98 + ".txt", None),                   # 200 bytes
        ("\u00e9" * 99 + ".txt", "A file name must be at most 200 bytes "
                                 "in UTF-8 (this one has 202)."),
        ("\U0001d49c" * 49 + ".txt", None),                # 200 bytes
        ("\U0001d49c" * 49 + "a.txt", "A file name must be at most 200 "
                                      "bytes in UTF-8 (this one has 201)."),
    ])
    def test_the_length_limits_at_and_over_each_boundary(
            self, project, typed, error):
        out = _file(1, typed, create_backup=False)
        if error is None:
            assert out["changed"] is True, out
        else:
            assert out == {"error": error}

    def test_every_refusal_is_made_before_the_backup(self, project):
        _add_file(project, 9, "")
        _documents_file(project, "other.txt")
        _reload()
        before = _dump(project)
        for typed in ("", " . ", "a:b.txt", "a b.txt", "\ud800.txt",
                      "other.txt", "unnamed_file_9", "notes.txt",
                      "x.pdf"):
            assert "error" in _file(1, typed), typed
        assert _file(99, "x.txt")["error"] == "File ID 99 does not exist"
        assert _backups(project) == [] and _dump(project) == before

    def test_unnamed_file_is_refused_only_while_that_file_is_invalid(
            self, project):
        _add_file(project, 9, "  ")
        _add_file(project, 10, "fine.txt")
        _reload()
        assert "File id 9 has an empty" in _file(1, "unnamed_file_9")["error"]
        # A valid file 10, and file 9 renaming itself, are both fine.
        assert _file(1, "unnamed_file_10",
                     create_backup=False)["changed"] is True
        assert _file(9, "unnamed_file_9",
                     create_backup=False)["changed"] is True

    def test_collisions_are_exact_after_nfc_on_both_sides(self, project):
        nfd = unicodedata.normalize("NFD", "café.txt")
        _add_file(project, 5, nfd)
        _reload()
        out = _file(1, unicodedata.normalize("NFC", "café.txt"))
        assert out["error"] == f"Another file already uses the name " \
                               f"'{nfd}' (id 5)."
        assert out["candidates"] == [{"id": 5, "name": nfd}]
        # A letter-case twin of ANOTHER file is QualCoder's own rule.
        assert _file(1, "NOTES.txt", create_backup=False)["changed"] is True
        # A respelling of its own name.
        assert _file(2, "Notes.TXT", create_backup=False)["changed"] is True

    def test_a_race_is_refused_with_the_backup_path(self, project,
                                                    monkeypatch):
        original = server.QualcoderDatabase.begin_immediate

        def racing(self):
            _add_file(project, 9, "P01.txt")
            original(self)
        monkeypatch.setattr(server.QualcoderDatabase, "begin_immediate",
                            racing)
        out = _file(1, "P01.txt")
        assert out["error"] == "Another file already uses the name " \
                               "'P01.txt' (id 9)."
        assert "backup_path" in out
        assert _rows(project, "SELECT name FROM source WHERE id = 1") == \
            [{"name": "interview.txt"}]


STILL_POSSIBLE = ("QualCoder's own Rename (Manage Files, the name's "
                  "right-click menu, \"Rename database entry\") can still "
                  "make this change if it is wanted.")


class TestTheEndingRule:
    """The owner's ruling of 2026-09-23 (option b), case by case: refuse
    only a change QualCoder acts on, say why and that QualCoder's own
    Rename can still do it; any other name changes freely."""

    @pytest.fixture
    def media(self, project):
        _add_file(project, 10, "P01.mp3", mediapath="/audio/P01.mp3",
                  av_text_id=11, fulltext=None)
        _add_file(project, 11, "P01.mp3.txt")
        _add_file(project, 12, "P02.mp4", mediapath="video:/data/P02.mp4",
                  av_text_id=13, fulltext=None)
        _add_file(project, 13, "P02.mp4.transcribed")
        _add_file(project, 14, "photo.JPG", mediapath="/images/photo.jpg",
                  fulltext=None)
        _add_file(project, 15, "report.pdf", mediapath="/docs/report.pdf")
        _add_file(project, 16, "Thomas.Jones")
        _add_file(project, 17, "notes of Dr. Thomas",
                  mediapath="/docs/notes of Dr. Thomas")
        _reload()
        return project

    @pytest.mark.parametrize("fid, typed, opening", [
        # A transcript keeps '.txt' or '.transcribed' exactly.
        (11, "P01.mp3.TXT", "This file is the transcript of 'P01.mp3', "
                            "and its name must keep the ending '.txt'"),
        (11, "P01 transcript", "This file is the transcript of"),
        (11, "P01.mp3.transcribed", "This file is the transcript of"),
        (13, "P02.mp4.txt", "This file is the transcript of 'P02.mp4', "
                            "and its name must keep the ending "
                            "'.transcribed'"),
        # '.pdf' neither gained nor lost, in any letter case.
        (15, "report", "The name would lose the ending '.pdf'"),
        (15, "report.PDF.txt", "The name would lose the ending '.pdf'"),
        (2, "notes.Pdf", "The name would gain the ending '.pdf'"),
        # '.transcribed' not gained.
        (16, "P01.mp3.transcribed", "The name would gain the ending "
                                    "'.transcribed'"),
        # A media file keeps its stored file's extension.
        (10, "P01.wav", "This audio file's name must keep its ending '.mp3'"),
        (12, "P02", "This video file's name must keep its ending '.mp4'"),
        (14, "photo.png", "This image file's name must keep its ending "
                          "'.jpg'"),
        # A text with no stored file keeps a plain-text declared type.
        (2, "notes.docx", "This text has no stored file"),
        (2, "Dr. P notes", "This text has no stored file"),
        (2, "notes. .x", "This text has no stored file"),
    ])
    def test_refused_with_why_and_that_qualcoder_can(self, media, fid,
                                                      typed, opening):
        out = _file(fid, typed)
        assert out["error"].startswith(opening), out
        assert out["error"].endswith(STILL_POSSIBLE)
        assert _backups(media) == []

    @pytest.mark.parametrize("fid, typed", [
        (11, "P09.mp3.txt"), (13, "P09.transcribed"),   # ending kept
        (15, "P01 report.PDF"),                          # '.pdf' kept
        (10, "P09.MP3"), (14, "P09.jpg"),                # extension kept
        (16, "P01"), (16, "Alex.Brown"),                 # no ending known
        (17, "notes of Dr. P"), (17, "P01"),             # a document
        (2, "field notes"), (2, "notes.TXT"),            # still plain
        (1, "interview.txt.txt"),
    ])
    def test_any_other_name_changes_freely(self, media, fid, typed):
        out = _file(fid, typed, create_backup=False)
        assert out.get("changed") is True, out

    def test_the_rule_is_a_transcribed_copy_of_the_refi_type(self):
        from qualcoder_mcp.database import refi_declared_text_type as t
        # refi.py:3160-3168 at 9bddf17, transcribed.
        assert [t(n) for n in ("a.txt", "a", "a.b.docx", "a.transcribed",
                               "D. Thomas notes", "a.", "a.Transcribed")] \
            == ["txt", "txt", "docx", "txt", " Thomas notes", "",
                "Transcribed"]


class TestTheDocumentsFolderRule:

    @pytest.mark.parametrize("mediapath", [None, "/docs/Own.docx",
                                           "docs:/elsewhere/Own.docx"])
    def test_refused_for_each_text_kind(self, project, mediapath):
        _add_file(project, 5, "Own.docx", mediapath=mediapath)
        _documents_file(project, "Taken.docx")
        _reload()
        out = _file(5, "Taken.docx")
        assert out["error"].startswith("The project's documents folder "
                                       "already holds a file called "
                                       "'Taken.docx'.")

    def test_its_own_stored_copy_is_allowed(self, project):
        _add_file(project, 5, "P01.docx", mediapath="/docs/Thomas.docx")
        _documents_file(project, "Thomas.docx")
        _reload()
        assert _file(5, "Thomas.docx", create_backup=False)["changed"]

    def test_media_is_not_checked(self, project):
        _add_file(project, 5, "a.png", mediapath="/images/a.png",
                  fulltext=None)
        _documents_file(project, "b.png")
        _reload()
        assert _file(5, "b.png", create_backup=False)["changed"] is True

    def test_the_import_shares_the_rules(self, project):
        _documents_file(project, "orphan.txt")
        _reload()
        for name, opening in (
                ("orphan.txt", "The project's documents folder"),
                ("a:b.txt", "A file name must not contain path"),
                ("x" * 197 + ".txt", "A file name must be at most 200"),
                ("a\u200bb.txt", "A file name must not contain invisible"),
                (".", "A file name must not be empty")):
            out = json.loads(server.import_text_file(name, "Some text."))
            assert out["error"].startswith(opening), (name, out)
        assert _backups(project) == []


class TestWhatKeepsTheOldName:

    @pytest.mark.parametrize("mediapath, kind, stored", [
        ("/docs/Thomas.docx", "in_project_folder", "Thomas.docx"),
        ("/audio/Thomas.mp3", "in_project_folder", "Thomas.mp3"),
        ("docs:/home/r/Thomas.docx", "linked_outside_project", "Thomas.docx"),
        ("video:C:\\data\\Thomas.mp4", "linked_outside_project",
         "Thomas.mp4"),
    ])
    def test_stored_copy_kinds(self, project, mediapath, kind, stored):
        ext = stored.rsplit(".", 1)[1]
        _add_file(project, 5, f"Thomas.{ext}", mediapath=mediapath)
        _reload()
        out = _file(5, f"P01.{ext}", create_backup=False)
        assert out["stored_copy"]["kind"] == kind
        assert out["stored_copy"]["stored_name"] == stored
        if kind == "in_project_folder":
            assert "A later QualCoder import of a file called " \
                   f"'{stored}' will overwrite" in out["stored_copy"]["note"]
            assert ("original text" in out["stored_copy"]["note"]) == \
                (ext == "docx")

    @pytest.mark.parametrize("on_disk, kind, found", [
        ([], "none", None),
        (["interview.txt"], "found_by_name", "interview.txt"),
        # Letter case and a trailing space, whatever this disk does
        # (the listing is compared under the strictest disk's rules).
        (["INTERVIEW.TXT "], "found_by_name", "INTERVIEW.TXT "),
        (["interview.txt.txt"], "named_after_it", "interview.txt.txt")])
    def test_a_text_with_no_stored_path(self, project, monkeypatch,
                                        on_disk, kind, found):
        monkeypatch.setattr(server.QualcoderDatabase, "documents_listing",
                            lambda self: list(on_disk))
        out = _file(1, "P01.txt", create_backup=False)
        block = out["stored_copy"]
        assert block["kind"] == kind
        assert block.get("stored_name") == found
        if kind == "found_by_name":
            # Fix round 1, QA-3: a document found by name holds the
            # original text too, and a later import may delete it.
            assert "the original document, whatever has been rewritten " \
                   "here since." in block["note"]
            assert "may then delete it." in block["note"]
        if kind == "named_after_it":
            # No QualCoder code reads documents/<name>.txt back by the
            # entry's name (QA-3): no "finds it" for this one.
            assert "finds" not in block["note"]
            assert "QualCoder does not link it to this entry" in \
                block["note"]

    @pytest.mark.parametrize("mediapath, deletes", [
        ("/docs/Thomas.docx", True), ("/docs/Thomas.pdf", True),
        ("/images/Thomas.jpg", False), ("/audio/Thomas.mp3", False),
        ("/video/Thomas.mp4", False)])
    def test_the_later_import_sentence_by_kind(self, project, mediapath,
                                               deletes):
        """Master's media import overwrites and never unlinks; only a
        document or PDF import deletes its rejected copy (QA-3)."""
        ext = mediapath.rsplit(".", 1)[1]
        _add_file(project, 5, f"Thomas.{ext}", mediapath=mediapath)
        _reload()
        note = _file(5, f"P01.{ext}", create_backup=False)[
            "stored_copy"]["note"]
        assert "will overwrite this stored copy" in note
        assert ("may then delete it" in note) is deletes

    def test_the_notes_and_the_counts(self, project):
        _saved_places(project)
        _exec(project, "INSERT INTO gr_file_text_item (grid, fid, "
                       "displaytext) VALUES (1, 1, 'Interview.TXT'), "
                       "(1, 2, 'interview.txt')")
        _save_filter(project, "f", "BOOLEAN_OR",
                     [("file name", "file", "character", "like",
                       ["interview.txt"])])
        _add_file(project, 7, "interview_p1.jpg", mediapath="/images/x.jpg")
        _add_file(project, 8, "interview.txt_summary")
        _reload()
        out = _file(1, "P01.txt", create_backup=False)
        # A text with no stored file is looked for whole: '.txt' is part
        # of its name, so 'interview_p1.jpg' is not named after it.
        assert out["old_name_left_in"] == {
            "saved_graph_labels": 1, "saved_filters": 1, "file_ids": [8]}
        assert out["note"] == server.RENAME_FILE_NOTE
        assert out["search_index_note"] == \
            server.RENAME_FILE_SEARCH_INDEX_NOTE
        assert "transcript_of" not in out and "linked_transcript" not in out


class TestTranscripts:

    @pytest.fixture
    def av(self, project):
        _add_file(project, 10, "Thomas.mp3", mediapath="/audio/Thomas.mp3",
                  av_text_id=11, fulltext=None)
        _add_file(project, 11, "Thomas.mp3.txt")
        _add_file(project, 12, "Anna.mp4", mediapath="/video/Anna.mp4",
                  av_text_id=13, fulltext=None)
        _add_file(project, 13, "Anna.mp4.transcribed")
        _reload()
        return project

    def test_a_recording_reports_its_transcript(self, av):
        out = _file(10, "P01.mp3", create_backup=False)
        block = out["linked_transcript"]
        assert (block["file_id"], block["name"]) == (11, "Thomas.mp3.txt")
        assert "A later QualCoder import of a recording called " \
               "'Thomas.mp3' would stop" in block["note"]
        assert "transcript_pairing" not in out

    def test_a_transcribed_pairing_asks_for_the_matching_rename(self, av):
        out = _file(12, "P02.mp4", create_backup=False)
        assert "rename the transcript 'P02.mp4.transcribed' to keep that " \
               "pairing" in out["linked_transcript"]["note"]

    def test_a_transcript_reports_its_recording(self, av):
        out = _file(11, "P01.mp3.txt", create_backup=False)
        assert out["transcript_of"]["file_id"] == 10
        assert out["transcript_of"]["name"] == "Thomas.mp3"
        assert "other_recording_ids" not in out["transcript_of"]

    def test_a_transcribed_entry_that_loses_its_pairing_says_so(self, av):
        out = _file(13, "P02.mp4.transcribed", create_backup=False)
        assert out["transcript_pairing"] == [
            "QualCoder's REFI-QDA export and file summary paired this entry "
            "with recording id 12 by name; they no longer do, and unless "
            "the name extends a recording's name, the REFI-QDA export "
            "leaves this entry out."]

    def test_a_name_that_makes_qualcoder_adopt_a_transcript(self, av):
        _add_file(av, 20, "Lost.mp3", mediapath="/audio/Lost.mp3",
                  fulltext=None)                         # no link at all
        _add_file(av, 21, "Gone.wav", mediapath="/audio/Gone.wav",
                  av_text_id=999, fulltext=None)         # link to nowhere
        _reload()
        out = _file(2, "Lost.mp3.txt", create_backup=False)
        assert out["transcript_pairing"] == [
            "QualCoder may adopt file id 2 ('Lost.mp3.txt') as the "
            "transcript of recording id 20 ('Lost.mp3') the next time that "
            "recording is opened: its transcript link is missing, and "
            "QualCoder then looks for an entry called '<recording>.txt' or "
            "'<recording>.transcribed'."]
        _add_file(av, 22, "Gone2.wav.txt")
        _reload()
        out = _file(21, "Gone2.wav", create_backup=False)
        assert out["transcript_pairing"] == [
            "QualCoder may adopt file id 22 ('Gone2.wav.txt') as the "
            "transcript of recording id 21 ('Gone2.wav') the next time that "
            "recording is opened: its transcript link is broken, and "
            "QualCoder then looks for an entry called '<recording>.txt' or "
            "'<recording>.transcribed'."]

    def test_a_recording_renamed_onto_a_transcribed_entry(self, av):
        _add_file(av, 30, "P09.mp3.transcribed")
        _reload()
        out = _file(10, "P09.mp3", create_backup=False)
        assert out["transcript_pairing"] == [
            "QualCoder's REFI-QDA export and file summary pair a recording "
            "with '<name>.transcribed' by name, so file id 30 will be "
            "exported as this recording's transcript."]


class TestLimitsHoldElsewhere:

    @pytest.mark.parametrize("name", ["é" * 96 + ".txt",
                                      "\U0001d49c" * 49 + ".txt"])
    def test_a_name_at_the_limit_pages_in_search_coded_text(self, project,
                                                            name):
        """The limits keep a cursor carrying the name under the cursor's
        1,024-character cap (rename dossier 3.3, item 4)."""
        assert _file(1, name, create_backup=False)["changed"] is True
        first = json.loads(server.search_coded_text("I", limit=1))
        cursor = first["page"]["next_cursor"]
        assert cursor and len(cursor) <= 1024
        second = json.loads(server.search_coded_text("I", limit=1,
                                                     cursor=cursor))
        assert "error" not in second, second
        assert second["results"][0]["file_name"] == name

    def test_a_link_stale_in_qualcoder_4_is_reported(self, project):
        _add_file(project, 20, "R.mp3", mediapath="/audio/R.mp3",
                  av_text_id=21, fulltext=None)
        _add_file(project, 21, "R transcript")     # no longer '.txt'
        _reload()
        out = _file(1, "R.mp3.txt", create_backup=False)
        assert "its transcript link is stale in QualCoder 4.0" in \
            out["transcript_pairing"][0]


class TestTheCodebookRenamesLogIdsToo:
    """The carried housekeeping item (RULINGS_2026-09-22.md, 2026-09-23):
    rename_code and rename_category logged both names at INFO to the log
    the host keeps on disk. They log the id only now."""

    def test_rename_code(self, project, caplog):
        with caplog.at_level(logging.DEBUG):
            out = json.loads(server.rename_code(1, "Parity Strain",
                                                create_backup=False))
        assert out["changed"] is True
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "Renamed code 1" in text
        assert "Stress" not in text and "Parity Strain" not in text

    def test_rename_category(self, project, caplog):
        with caplog.at_level(logging.DEBUG):
            out = json.loads(server.rename_category(1, "Parity Group",
                                                    create_backup=False))
        assert out["changed"] is True
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "Renamed category 1" in text
        assert "Category A" not in text and "Parity Group" not in text


class TestTheDatabaseHalf:
    """The database methods keep their own backstops: an exact duplicate
    is refused before the UPDATE, and rename_file applies the name rules
    again (defence in depth behind the tool's pre-check)."""

    @pytest.fixture
    def wdb(self, project):
        from qualcoder_mcp.database import QualcoderDatabase
        db = QualcoderDatabase(str(project), read_only=False)
        yield db
        db.close()

    def test_an_exact_duplicate_case_is_refused(self, project, wdb):
        _add_case(project, "Tom", 5)
        with pytest.raises(ValueError, match=r"^A case named 'Tom' already "
                                             r"exists \(id 5\)$"):
            wdb.rename_case(1, "Tom")
        with pytest.raises(ValueError, match="Case ID 99 does not exist"):
            wdb.rename_case(99, "x")

    def test_an_exact_duplicate_file_is_refused(self, project, wdb):
        with pytest.raises(ValueError, match=r"^A file named 'notes.txt' "
                                             r"already exists \(id 2\)$"):
            wdb.rename_file(1, "notes.txt")

    @pytest.mark.parametrize("name", ["", "a/b.txt", "a​b.txt",
                                      "x" * 201])
    def test_rename_file_applies_the_name_rules_itself(self, wdb, name):
        with pytest.raises(ValueError, match="^A file name must"):
            wdb.rename_file(1, name)

    def test_the_date_owner_and_stored_path_are_not_written(self, project,
                                                             wdb):
        _add_file(project, 5, "a.png", mediapath="/images/a.png")
        assert wdb.rename_file(5, " b.png ") == {
            "file_id": 5, "old_name": "a.png", "new_name": "b.png"}
        assert _rows(project, "SELECT name, mediapath, owner, date FROM "
                              "source WHERE id = 5") == [
            {"name": "b.png", "mediapath": "/images/a.png",
             "owner": "gui_user", "date": "2024-01-15"}]


class TestNamesWindowsCannotStore:
    """Fix round 1, S-2 (and S-1's trailing dot): both tools refuse a
    name Windows cannot store, a named departure from QualCoder's own
    Rename, whose export opens the name as a file with no handler."""

    @pytest.mark.parametrize("name, opening", [
        ("a<b.txt", "A file name must not contain < > | ? * or \""),
        ("a>b.txt", "A file name must not contain < > | ? * or \""),
        ("a|b.txt", "A file name must not contain < > | ? * or \""),
        ("a?.txt", "A file name must not contain < > | ? * or \""),
        ("a*.txt", "A file name must not contain < > | ? * or \""),
        ("a\"b.txt", "A file name must not contain < > | ? * or \""),
        ("P01.txt.", "A file name must not end with a dot or a space"),
        ("P01.txt. .", "A file name must not end with a dot or a space"),
        ("CON", "A file name must not be a Windows device name"),
        ("con.txt", "A file name must not be a Windows device name"),
        ("NUL.tar.gz", "A file name must not be a Windows device name"),
        ("Aux .txt", "A file name must not be a Windows device name"),
        ("COM1.txt", "A file name must not be a Windows device name"),
        ("com0.txt", "A file name must not be a Windows device name"),
        ("COM¹.txt", "A file name must not be a Windows device name"),
        ("LPT9.docx", "A file name must not be a Windows device name"),
        ("prn.txt", "A file name must not be a Windows device name"),
        ("CONIN$", "A file name must not be a Windows device name"),
        ("conout$.txt", "A file name must not be a Windows device name"),
    ])
    def test_refused_by_both_tools(self, project, name, opening):
        out = _file(1, name)
        assert out["error"].startswith(opening), out
        out = json.loads(server.import_text_file(name, "Some text."))
        assert out["error"].startswith(opening), out
        assert _backups(project) == []

    @pytest.mark.parametrize("name", [
        "CONSENT.txt", "console notes.txt", "COM10.txt", "LPT.txt",
        "my NUL.txt", "a.CON.txt", "P01 notes.txt"])
    def test_names_that_merely_resemble_them_pass(self, project, name):
        assert _file(1, name, create_backup=False)["changed"] is True
        assert _file(1, "interview.txt", create_backup=False)["changed"]

    def test_the_refusal_names_every_kind_it_refuses(self, project):
        """R1-6: the superscript digits and the console names are named."""
        out = _file(1, "COM\u00b2.txt")
        assert out["error"] == (
            "A file name must not be a Windows device name (CON, PRN, AUX, "
            "NUL, CONIN$, CONOUT$, COM0 to COM9, LPT0 to LPT9, or COM or "
            "LPT followed by a superscript 1, 2 or 3, with any extension): "
            "Windows cannot store it as a file.")

    def test_a_trailing_space_is_refused_too(self):
        from qualcoder_mcp.database import file_name_problem
        assert file_name_problem("P01.txt ").startswith(
            "A file name must not end with a dot or a space")


class TestTheDocumentsRuleIsTheStrictestDisks:
    """Fix round 1, S-1. The documents/ rule compares the new name with
    the folder's listing under the strictest disk QualCoder may open the
    project on (NFC, letter case folded, trailing dots and spaces
    dropped), never this server's own disk: the listing is stood in here,
    so these pins say the same on a disk that keeps letter case (Linux)
    and on one that folds it (this Mac, Windows)."""

    @pytest.fixture
    def listing(self, project, monkeypatch):
        names = []
        monkeypatch.setattr(server.QualcoderDatabase, "documents_listing",
                            lambda self: list(names))
        _add_file(project, 20, "Alpha_interview.docx",
                  mediapath="/docs/Alpha_interview.docx")
        _add_file(project, 21, "Beta_interview.docx",
                  mediapath="/docs/Beta_interview.docx")
        _reload()
        names.extend(["Alpha_interview.docx", "Beta_interview.docx"])
        return names

    @pytest.mark.parametrize("typed", [
        "beta_interview.docx", "BETA_INTERVIEW.DOCX", "Beta_Interview.docx"])
    def test_another_files_copy_in_any_letter_case_is_refused(
            self, listing, typed):
        out = _file(20, typed)
        assert out["error"].startswith(
            "The project's documents folder already holds "
            "'Beta_interview.docx', which is the same file as"), out

    def test_a_listing_entry_with_a_trailing_space_or_dot(self, listing):
        listing.append("Gamma notes.docx ")         # a Linux-only name
        listing.append("Delta.docx.")
        for typed in ("Gamma notes.docx", "delta.docx"):
            out = _file(20, typed)
            assert out["error"].startswith(
                "The project's documents folder already holds"), out

    def test_its_own_copy_in_another_letter_case_is_allowed(self, listing):
        out = _file(20, "ALPHA_interview.docx", create_backup=False)
        assert out["changed"] is True

    def test_its_own_copy_does_not_excuse_another_files_twin(self, listing):
        # On a disk that keeps letter case both files exist; on one that
        # folds it they are one, so the new name is refused.
        listing.append("ALPHA_INTERVIEW.docx")
        out = _file(20, "ALPHA_INTERVIEW.docx")
        assert "already holds a file called 'ALPHA_INTERVIEW.docx'" in \
            out["error"]

    def test_unicode_form_is_folded_too(self, listing):
        listing.append(unicodedata.normalize("NFD", "Zo\u00eb.docx"))
        out = _file(20, "ZO\u00cb.docx")
        assert out["error"].startswith(
            "The project's documents folder already holds"), out

    def test_the_import_asks_the_same_question(self, listing):
        out = json.loads(server.import_text_file("beta_INTERVIEW.docx",
                                                 "Some text."))
        assert out["error"].startswith(
            "The project's documents folder already holds "
            "'Beta_interview.docx'"), out

    def test_a_legacy_name_with_a_nul_is_compared_not_stat_ed(
            self, project, listing):
        """S-7: the own-copy exemption used to stat documents/<stored
        name>, and a stored name with a NUL raised instead of answering."""
        _add_file(project, 22, "legacy\x00name.txt")
        _reload()
        listing.append("legacy2.txt")
        out = _file(22, "Legacy2.txt")
        assert out["error"].startswith(
            "The project's documents folder already holds 'legacy2.txt'")
        assert _file(22, "P22.txt", create_backup=False)["changed"] is True


class TestARenameBack:
    """Fix round 1, QA-4 (the lead's ruling): renaming back to a name
    whose documents/ file is this entry's own copy is allowed, and
    restoring an ending the entry had is not refused. The evidence is the
    project's backups (every earlier name is in one) and the stored
    file's own name; without it, the refusal says what can do it."""

    @pytest.fixture
    def listing(self, monkeypatch):
        names = []
        monkeypatch.setattr(server.QualcoderDatabase, "documents_listing",
                            lambda self: list(names))
        return names

    def test_a_legacy_text_takes_back_its_own_found_copy(self, project,
                                                          listing):
        _add_file(project, 5, "legacy.txt")
        _reload()
        listing.append("legacy.txt")
        assert _file(5, "legacy2.txt")["changed"] is True   # with a backup
        out = _file(5, "legacy.txt", create_backup=False)
        assert out["changed"] is True, out

    def test_without_a_backup_the_refusal_says_what_can(self, project,
                                                        listing):
        _add_file(project, 5, "legacy.txt")
        _reload()
        listing.append("legacy.txt")
        assert _file(5, "legacy2.txt", create_backup=False)["changed"]
        out = _file(5, "legacy.txt")
        assert out["error"].startswith(
            "The project's documents folder already holds a file called "
            "'legacy.txt'.")
        assert out["error"].endswith(
            "If it was this entry's own copy under a name it had before, "
            "restore_backup or QualCoder's own Rename can put that name "
            "back (restoring an earlier backup undoes everything done after "
            "it, any pseudonymisation run included); this server recognises "
            "a rename back only from a "
            "backup that shows this entry with that name and, for its "
            "documents copy, the same text.")

    def test_not_when_another_entry_claims_that_file_now(self, project,
                                                          listing):
        _add_file(project, 5, "legacy.txt")
        _reload()
        listing.append("legacy.txt")
        assert _file(5, "legacy2.txt")["changed"] is True
        _add_file(project, 6, "Other.txt", mediapath="/docs/LEGACY.txt")
        _reload()
        assert "already holds" in _file(5, "legacy.txt")["error"]

    def test_not_when_two_files_there_match(self, project, listing):
        _add_file(project, 5, "legacy.txt")
        _reload()
        listing.extend(["legacy.txt", "LEGACY.txt"])
        assert _file(5, "legacy2.txt")["changed"] is True
        assert "already holds" in _file(5, "legacy.txt")["error"]

    @pytest.mark.parametrize("first, second", [
        ("Thomas.Jones", "P01 notes"),               # a declared type
        ("stray.transcribed", "stray.txt"),          # '.transcribed'
    ])
    def test_an_ending_it_had_is_restored(self, project, first, second):
        _add_file(project, 5, first)
        _reload()
        assert _file(5, second)["changed"] is True          # a backup
        assert _file(5, first, create_backup=False)["changed"] is True

    @pytest.mark.parametrize("first, second, opening", [
        ("Thomas.Jones", "P01 notes", "This text has no stored file"),
        ("stray.transcribed", "stray.txt",
         "The name would gain the ending '.transcribed'"),
    ])
    def test_not_without_the_evidence(self, project, first, second,
                                      opening):
        _add_file(project, 5, first)
        _reload()
        assert _file(5, second, create_backup=False)["changed"] is True
        assert _file(5, first)["error"].startswith(opening)

    @pytest.mark.parametrize("name, mediapath, restored", [
        # QA-13: a PDF whose entry lost '.pdf' in QualCoder, and a
        # document whose entry gained it, go back to the stored file's.
        ("report", "/docs/report.pdf", "report.pdf"),
        ("notes.pdf", "/docs/notes.docx", "notes.docx"),
    ])
    def test_the_stored_files_own_name_is_evidence_too(
            self, project, name, mediapath, restored):
        _add_file(project, 5, name, mediapath=mediapath)
        _reload()
        assert _file(5, restored, create_backup=False)["changed"] is True


class TestTheTranscriptRefusalByCase:
    """Fix round 1, QA-2: a swap of '.txt' and '.transcribed' keeps
    QualCoder 4.0's link, so its refusal names what QualCoder does act on,
    the by-name '.transcribed' pairing; losing both endings keeps the
    link text. And a swap back to an ending the transcript had is a
    restore (QA-4); losing both endings never is."""

    @pytest.fixture
    def av(self, project):
        _add_file(project, 10, "P01.mp3", mediapath="/audio/P01.mp3",
                  av_text_id=11, fulltext=None)
        _add_file(project, 11, "P01.mp3.transcribed")
        _reload()
        return project

    @pytest.mark.parametrize("typed, ending, other", [
        ("P01.mp3.txt", ".transcribed", ".txt")])
    def test_a_swap_names_the_pairing(self, av, typed, ending, other):
        out = _file(11, typed)
        assert out["error"].startswith(
            f"This file is the transcript of 'P01.mp3', and its name must "
            f"keep the ending '{ending}': QualCoder's REFI-QDA export and "
            f"file summary pair a recording with an entry ending in "
            f"'.transcribed' by name"), out
        assert f"swapping '{ending}' for '{other}'" in out["error"]
        assert "broken link" not in out["error"]
        assert out["error"].endswith(STILL_POSSIBLE)

    def test_the_other_swap_too(self, project):
        _add_file(project, 10, "P01.mp3", mediapath="/audio/P01.mp3",
                  av_text_id=11, fulltext=None)
        _add_file(project, 11, "P01.mp3.txt")
        _reload()
        out = _file(11, "P01.mp3.transcribed")
        assert "swapping '.txt' for '.transcribed'" in out["error"]
        assert "broken link" not in out["error"]

    def test_losing_both_keeps_the_link_text(self, av):
        out = _file(11, "P01 transcript")
        assert "treats a transcript whose name does not end in '.txt' or " \
               "'.transcribed' as a broken link" in out["error"]

    def test_a_swap_back_is_a_restore(self, av):
        # QualCoder's own Rename made the swap; a backup shows the
        # transcript's earlier '.txt'.
        _exec(av, "UPDATE source SET name = 'P01.mp3.txt' WHERE id = 11")
        _reload()
        assert _file(11, "P09.mp3.txt")["changed"] is True   # a backup
        _exec(av, "UPDATE source SET name = 'P01.mp3.transcribed' "
                  "WHERE id = 11")
        _reload()
        out = _file(11, "P01.mp3.txt", create_backup=False)
        assert out["changed"] is True, out

    def test_losing_both_is_never_a_restore(self, av):
        _exec(av, "UPDATE source SET name = 'P01 transcript' WHERE id = 11")
        _reload()
        assert _file(11, "P01.mp3.txt")["changed"] is True   # a backup
        out = _file(11, "P01 transcript")
        assert "as a broken link" in out["error"], out


class TestWhereTheOldNameStaysByWholeWords:
    """Fix round 1, QA-6 (the lead's ruling): the old name is looked for
    as a whole word, ignoring letter case, where letters and digits make
    a word, so a short label does not over-report and a file named after
    a case with '_' around the label is still found. A heuristic, and
    both notes say so."""

    def test_a_short_label_is_not_found_inside_longer_words(self, project):
        _saved_places(project)
        _add_case(project, "AS", 5)
        _save_display(project, "d1", [("Case", "=", "Thomas")])
        _save_display(project, "d2", [("Case", "=", "AS")])
        _save_filter(project, "f1", "BOOLEAN_OR",
                     [("case name", "case", "character", "like", ["Thomas"])])
        _save_filter(project, "f2", "BOOLEAN_AND",
                     [("case name", "case", "character", "=", ["AS"])])
        _add_file(project, 7, "Thomas.txt")
        _add_file(project, 8, "AS_interview.txt")
        _add_file(project, 9, "Survey_as")
        _reload()
        out = _case(5, "P05", create_backup=False)
        assert out["old_name_left_in"] == {
            "saved_table_displays": 1, "saved_filters": 1,
            "file_ids": [8, 9]}

    def test_files_named_after_a_case_with_underscores(self, project):
        _add_case(project, "Thomas_P01", 5)
        for fid, name in ((7, "Thomas_P01_interview.txt"),
                          (8, "Survey_Thomas_P01"),
                          (9, "Thomas_P010.txt"),        # another label
                          (10, "Thomas_P01.mp3.txt")):
            _add_file(project, fid, name)
        _reload()
        out = _case(5, "P01", create_backup=False)
        assert out["old_name_left_in"] == {"file_ids": [7, 8, 10]}

    def test_a_dotted_name_is_not_cut_at_its_dot(self, project):
        _add_file(project, 5, "Thomas.Jones")
        _add_file(project, 6, "Thomas_notes.txt")
        _add_file(project, 7, "Thomas.Jones.txt")
        _reload()
        out = _file(5, "P05", create_backup=False)
        assert out["old_name_left_in"] == {"file_ids": [7]}

    def test_a_stored_file_is_looked_for_without_its_extension(self,
                                                               project):
        _add_file(project, 5, "Thomas.pdf", mediapath="/docs/Thomas.pdf")
        _add_file(project, 6, "Thomas_p1.jpg", mediapath="/images/t.jpg")
        _add_file(project, 7, "Thomasina.txt")
        _reload()
        out = _file(5, "P05.pdf", create_backup=False)
        assert out["old_name_left_in"] == {"file_ids": [6]}

    def test_both_notes_call_it_a_heuristic(self):
        for note in (server.RENAME_CASE_NOTE, server.RENAME_FILE_NOTE):
            assert note.endswith(server.OLD_NAME_LEFT_IN_NOTE)
        assert server.OLD_NAME_LEFT_IN_NOTE.startswith(
            "old_name_left_in is a heuristic: it looks for the old name as "
            "a whole word, ignoring letter case")


class TestTheFiveBehavioursQAFoundUnpinned:
    """Fix round 1, QA-5: five behaviours the QA's mutations changed with
    the full suite green (Q1, Q2, Q5, Q6, Q10). Q10, the note's backup
    sentence, is pinned in test_v013_rename_beside_pseudonymise.py."""

    def test_q1_rename_files_gate_comes_first(self, project):
        _add_file(project, 5, "no extension")
        _reload()
        lock = validate_qda_path(str(project)).parent / \
            QUALCODER_LOCK_FILENAME
        lock.write_text(f"gui_user\n{time.time()}", encoding="utf-8")
        try:
            # The identical name, an unknown id, a name the rules refuse,
            # an ending the rule refuses and a valid name: each would get
            # a pre-check answer of its own after the gate.
            for args in ((5, "no extension"), (99, "x.txt"), (1, "a/b"),
                         (1, "interview.pdf"), (1, "P01.txt")):
                out = _file(*args)
                assert out == {"error": "This project is open in QualCoder "
                                        "(user gui_user). Close the project "
                                        "in QualCoder, then retry."}, args
        finally:
            lock.unlink()
        assert _backups(project) == []

    def test_q2_the_declared_type_rule_is_for_texts_with_no_stored_file(
            self, project):
        # A /docs/ document's declared type comes from its stored path, so
        # its name may change type freely.
        _add_file(project, 5, "field notes.txt",
                  mediapath="/docs/field notes.txt")
        _reload()
        assert _file(5, "field notes.docx",
                     create_backup=False)["changed"] is True

    def test_q5_a_legacy_text_keeps_its_own_found_copy(self, project,
                                                        monkeypatch):
        monkeypatch.setattr(server.QualcoderDatabase, "documents_listing",
                            lambda self: ["legacy.txt"])
        _add_file(project, 5, "legacy.txt")
        _reload()
        assert _file(5, "LEGACY.txt", create_backup=False)["changed"] is True

    @pytest.mark.parametrize("stored, typed, written", [
        ("loose notes.txt ", "loose notes.txt ", "loose notes.txt"),
        (unicodedata.normalize("NFD", "café.txt"),
         unicodedata.normalize("NFD", "café.txt"),
         unicodedata.normalize("NFC", "café.txt")),
    ])
    def test_q6_a_stored_name_outside_normal_form_is_rewritten(
            self, project, stored, typed, written):
        _add_file(project, 5, stored)
        _reload()
        out = _file(5, typed, create_backup=False)
        assert out["changed"] is True and out["new_name"] == written
        assert _rows(project, "SELECT name FROM source WHERE id = 5") == \
            [{"name": written}]


class TestSavedDisplaysAndFiltersByTheirValues:
    """Fix round 2, R2-1: in QualCoder's saved displays and filters only
    the values they filter on are read, so a label that is one of
    QualCoder's own words is not counted everywhere."""

    @pytest.mark.parametrize("label", ["OR", "AND", "Case", "like",
                                       "character"])
    def test_qualcoders_own_words_are_not_labels(self, project, label):
        _saved_places(project)
        _add_case(project, label, 5)
        _save_display(project, "d1", [("Case", "like", "Thomas"),
                                      ("Name", "hide", "notes")])
        _save_filter(project, "any", "BOOLEAN_OR",
                     [("case name", "case", "character", "like", ["P01"]),
                      ("Age", "case", "numeric", "between", ["20", "30"])])
        _save_filter(project, "all", "BOOLEAN_AND",
                     [("case name", "case", "character", "=", ["P02"])])
        _reload()
        out = _case(5, "P05", create_backup=False)
        assert out["old_name_left_in"] == {}, out

    def test_the_values_are_still_read(self, project):
        _saved_places(project)
        _add_case(project, "OR", 5)
        _save_display(project, "d1", [("Case", "=", "OR"),
                                      ("Name", "like", "Thomas")])
        _save_filter(project, "any", "BOOLEAN_OR",
                     [("case name", "case", "character", "like", ["P01"]),
                      ("case name", "case", "character", "=", ["OR"])])
        _reload()
        out = _case(5, "P05", create_backup=False)
        assert out["old_name_left_in"] == {
            "saved_table_displays": 1, "saved_filters": 1}

    def test_the_note_says_so(self):
        assert "in saved table displays and filters it reads their names, " \
               "and the values they filter on rather than QualCoder's own " \
               "words such as BOOLEAN_OR or like, reading a row whole when " \
               "it is not in QualCoder's exact saved shape." in \
               server.OLD_NAME_LEFT_IN_NOTE

    def test_a_text_in_no_saved_shape_is_read_whole(self):
        from qualcoder_mcp.database import (saved_display_values,
                                            saved_filter_values)
        assert saved_filter_values("case name like OR") == \
            ["case name like OR"]
        assert saved_filter_values(
            "[['BOOLEAN_OR'], ['n', 'case', 'character', '=', [\"'OR'\"]]]"
        ) == ["OR"]
        assert saved_display_values("Case\t=\tOR\t\tName\tlike\tP 1") == \
            ["OR", "P 1"]
        # Fix round 3, B-2: read whole, as the test's name says.
        assert saved_display_values("no tabs here") == ["no tabs here"]
        for other in ("Case\tThomas_P01", "Case = Thomas_P01",
                      "Case\tmaybe\tThomas_P01"):
            assert saved_display_values(other) == [other]
        for other in ("[['Thomas_P01']]",
                      # Fix round 4, F3B-3 and F3B-4 (M1): a condition whose
                      # name, case-or-file or operator slot is not
                      # QualCoder's is read whole, so the label there counts.
                      "[['BOOLEAN_OR'], [['Thomas_P01'], 'case', "
                      "'character', '=', [\"'x'\"]]]",
                      "[['BOOLEAN_OR'], ['n', 'Thomas_P01', 'character', "
                      "'=', [\"'x'\"]]]",
                      "[['BOOLEAN_OR'], ['n', 'case', 'character', "
                      "'Thomas_P01', [\"'x'\"]]]",
                      "[['BOOLEAN_OR'], ['n', 'case', 'character', '=', "
                      "\"'Thomas_P01'\"]]",
                      "[['BOOLEAN_OR'], ['n', 'case', 'character', '=']]",
                      "[['BOOLEAN_OR'], ['n', 'case', 'character', '=', "
                      "[], 'Thomas_P01']]",
                      "['BOOLEAN_OR', 'Thomas_P01']",
                      "[['BOOLEAN_OR'], ['n', 'case', 'character', '=', "
                      "[[\"'Thomas_P01'\"]]]]"):
            assert saved_filter_values(other) == [other], other


class TestTheBehavioursR2FoundUnpinned:
    """Fix round 2, R2-2 (D1 to D5 of REVERIFY_RENAME_R2.md)."""

    @pytest.fixture
    def av(self, project):
        _add_file(project, 10, "V02.mp4", mediapath="/video/V02.mp4",
                  av_text_id=11, fulltext=None)
        _add_file(project, 11, "V02.mp4.transcribed")
        _reload()
        return project

    def test_d1_only_the_other_ending_licenses_a_swap(self, av):
        # A backup shows the transcript under another name with the SAME
        # ending; that is no evidence for the other ending.
        assert _file(11, "V03.mp4.transcribed")["changed"] is True
        out = _file(11, "V03.mp4.txt")
        assert "swapping '.transcribed' for '.txt'" in out["error"], out

    def test_d3_a_letter_case_variant_is_no_swap(self, project):
        _add_file(project, 10, "P01.mp3", mediapath="/audio/P01.mp3",
                  av_text_id=11, fulltext=None)
        _add_file(project, 11, "P01.mp3.txt")
        _reload()
        out = _file(11, "P01.mp3.Transcribed")
        # QualCoder 4.0's test is case-sensitive: this loses both endings.
        assert "as a broken link" in out["error"], out
        assert "swapping" not in out["error"]

    def test_d2_the_renamed_file_is_not_named_after_itself(self, project):
        _add_file(project, 5, "Thomas notes")
        _add_file(project, 6, "Dr. Thomas notes")
        _reload()
        out = _file(5, "Thomas notes v2", create_backup=False)
        assert out["old_name_left_in"] == {"file_ids": [6]}

    def test_d4_the_import_says_the_comparison_folds_letter_case(self):
        flat = " ".join(server.import_text_file.__doc__.split())
        assert "not a name already in the project's documents folder " \
               "(compared ignoring letter case), which QualCoder would " \
               "take for this text's stored copy" in flat

    def test_d5_the_stored_extension_is_cut_in_any_letter_case(self,
                                                               project):
        _add_file(project, 5, "Thomas.PDF", mediapath="/docs/Thomas.pdf")
        _add_file(project, 6, "Thomas_p1.jpg", mediapath="/images/t.jpg")
        _reload()
        out = _file(5, "P05.PDF", create_backup=False)
        assert out["old_name_left_in"] == {"file_ids": [6]}


# Saved displays and filters exactly as QualCoder 4.0 and 3.8.2 wrote
# them through their own code (REVERIFY_RENAME_F2_B.md, section 3, its
# evidence logs/saved40.json; byte for byte the same from 3.8.2).
QUALCODER_DISPLAYS = [
    ("saved 1", "Case\t=\tThomas_P01"),
    ("saved 2", "Name\tlike\tThomas_P01\t\tName\thide\tOR"),
    ("saved 3", "Case\t=\tZoë;O'Brien"),
    ("saved 4", "Name\t=\tOR notes.txt"),
    ("saved 5", "Case\tlike\t\tThomas_P01"),
    ("saved 6", "Participant\t=\t\tP02"),
    ("saved 7", "Participant\t=\tAnne\t\tP02"),
    ("saved 8", "Case\t=\tback\\slash"),
    ("Thomas_P01 files", "Name\tlike\tThomas_P01"),
    ("saved 10", "Participant\t=\tThomas_P01"),
]
QUALCODER_FILTERS = [
    ("filter 1", "[['BOOLEAN_OR'], ['case name', 'case', 'character', 'like',"
               " [\"'Thomas_P01'\"]]]"),
    ("filter 2", "[['BOOLEAN_AND'], ['case name', 'case', 'character', '=', "
               "[\"'OR'\"]]]"),
    ("filter 5", "[['BOOLEAN_OR'], ['Age', 'case', 'numeric', '>', "
                        "['18']]]"),
    ("filter 7", "[['BOOLEAN_AND'], ['case name', 'case', "
                             "'character', 'in', [\"'AND'\", \"'character'\","
                             " \"'like'\", \"'Case'\"]]]"),
    ("P02 only", "[['BOOLEAN_OR'], ['Age', 'case', 'numeric', '<', "
                 "['99']]]"),
]


class TestSavedRowsQualCoderWrote:
    """Fix round 3, B-1, B-3, B-6, B-8, on rows QualCoder itself wrote."""

    @pytest.fixture
    def saved(self, project):
        _saved_places(project)
        for name, rows in QUALCODER_DISPLAYS:
            _exec(project, "INSERT INTO manage_files_display (name, "
                           "tblrows, tblcolumns, owner) VALUES (?, ?, "
                           "'Name\t220\t\t', 'gui_user')", (name, rows))
        for name, text in QUALCODER_FILTERS:
            _exec(project, "INSERT INTO files_filter (name, filter, owner) "
                           "VALUES (?, ?, 'gui_user')", (name, text))
        _reload()
        return project

    @pytest.mark.parametrize("label, expected", [
        # D5 is counted again (B-1), with D1, D2, D9 (also by its name,
        # B-3) and D10; F1.
        ("Thomas_P01", {"saved_table_displays": 5, "saved_filters": 1}),
        # D6 and D7 (B-1) and the filter named 'P02 only' (B-3).
        ("P02", {"saved_table_displays": 2, "saved_filters": 1}),
        ("OR", {"saved_table_displays": 2, "saved_filters": 1}),
        ("AND", {"saved_filters": 1}),
        # 'saved 5' is read whole, so 'like' is counted there too: the
        # conservative direction, for a row QualCoder cannot load back.
        ("like", {"saved_table_displays": 1, "saved_filters": 1}),
    ])
    def test_counted_where_the_label_is(self, saved, label, expected):
        _add_case(saved, label, 5)
        _reload()
        out = _case(5, "P05 new", create_backup=False)
        assert out["old_name_left_in"] == expected, out

    def test_a_cancelled_like_row_is_read_whole(self, project):
        """B-8: 3.8.2 writes a cancelled 'Show values like' row with an
        empty value; the display is then read whole, so a case called
        'like' is counted there (the conservative direction)."""
        _saved_places(project)
        _exec(project, "INSERT INTO manage_files_display (name, tblrows) "
                       "VALUES ('cancelled', 'Name\tlike\t\t\tName\tlike\t')")
        _add_case(project, "like", 5)
        _reload()
        out = _case(5, "P05 new", create_backup=False)
        assert out["old_name_left_in"].get("saved_table_displays") == 1

    def test_a_saved_row_that_is_not_utf8_does_not_block_a_rename(
            self, saved):
        """B-6: a row stored as text that is not UTF-8 is decoded
        tolerantly for the count; the rename goes through."""
        _exec(saved, "INSERT INTO files_filter (name, filter) VALUES "
                     "(CAST(X'5468FF6F6D61735F503031' AS TEXT), "
                     "CAST(X'5B27FF546865' AS TEXT))")
        _exec(saved, "INSERT INTO manage_files_display (name, tblrows) "
                     "VALUES ('bad', CAST(X'436173650920093D09FF' AS TEXT))")
        _add_case(saved, "P03", 5)
        _reload()
        out = _case(5, "P05 new", create_backup=False)
        assert out.get("changed") is True, out


class TestTheSavedPlacesOwnNames:
    """Fix round 3, B-3: a saved display's or filter's own name is read,
    also when nothing in its rows names the label."""

    def test_only_the_names_hold_the_label(self, project):
        _saved_places(project)
        _save_display(project, "P03 view", [("Case", "=", "someone else")])
        _save_filter(project, "P03 filter", "BOOLEAN_OR",
                     [("Age", "case", "numeric", ">", ["18"])])
        _add_case(project, "P03", 5)
        _reload()
        out = _case(5, "P05 new", create_backup=False)
        assert out["old_name_left_in"] == {
            "saved_table_displays": 1, "saved_filters": 1}, out


def _to_utf16(project):
    """Rewrite the project's database with the text encoding UTF-16le, as
    checker B's probe p4 made a UTF-16 copy of QualCoder's saved40.qda
    (QualCoder never sets an encoding; another tool could)."""
    path = Path(project) / "data.qda"
    old_path = Path(project) / "data_utf8.qda"
    path.rename(old_path)
    new = sqlite3.connect(str(path))
    old = sqlite3.connect(str(old_path))
    try:
        new.execute("PRAGMA encoding = 'UTF-16le'")
        for name, sql in old.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'table' "
                "AND sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"):
            new.execute(sql)
            rows = old.execute(f"SELECT * FROM {name}").fetchall()
            if rows:
                marks = ",".join("?" * len(rows[0]))
                new.executemany(f"INSERT INTO {name} VALUES ({marks})", rows)
        new.commit()
        assert new.execute("PRAGMA encoding").fetchone()[0] == "UTF-16le"
    finally:
        new.close()
        old.close()
    old_path.unlink()


class TestAUtf16Database:
    """Fix round 4, F3B-1: in a UTF-16 database the saved rows are read as
    text (SQLite hands Python UTF-8 whatever the encoding), and only a
    read that raises falls back to the database's own bytes, decoded with
    its codec."""

    @pytest.mark.parametrize("label, expected", [
        ("Thomas_P01", {"saved_table_displays": 5, "saved_filters": 1}),
        ("P02", {"saved_table_displays": 2, "saved_filters": 1}),
    ])
    def test_counted_as_in_a_utf8_database(self, project, label, expected):
        _saved_places(project)
        for name, rows in QUALCODER_DISPLAYS:
            _exec(project, "INSERT INTO manage_files_display (name, "
                           "tblrows) VALUES (?, ?)", (name, rows))
        for name, text in QUALCODER_FILTERS:
            _exec(project, "INSERT INTO files_filter (name, filter) "
                           "VALUES (?, ?)", (name, text))
        _add_case(project, label, 5)
        server.db.close()
        _to_utf16(project)
        _reload()
        out = _case(5, "P05 new", create_backup=False)
        assert out["old_name_left_in"] == expected, out

    def test_the_fallback_decodes_with_the_databases_codec(self):
        """SQLite hands Python a UTF-16 database's text as valid UTF-8, so
        the fallback is reached only by bytes no encoding can read, which
        in a UTF-8 database is the B-6 pin below; here, the codec it
        would use for each encoding SQLite names."""
        from qualcoder_mcp.database import sqlite_text_codec
        assert [sqlite_text_codec(e) for e in
                ("UTF-8", "UTF-16le", "UTF-16be", "UTF-16")] == \
            ["utf-8", "utf-16-le", "utf-16-be", "utf-16"]
        text = "Thomas_P01 note"
        assert (text.encode("utf-16-le")
                .decode(sqlite_text_codec("UTF-16le"))) == text


class TestAGraphLabelIsReadTolerantly:
    """Fix round 4, F3B-4 (M2): a saved graph label stored as text that is
    not UTF-8 is read, decoded tolerantly, and the rename goes through."""

    def test_counted_and_not_blocking(self, project):
        _saved_places(project)
        _add_case(project, "Thomas_P01", 5)
        _exec(project, "INSERT INTO gr_case_text_item (grid, caseid, "
                       "displaytext) VALUES (1, 5, CAST(? AS TEXT))",
              (b"Thomas_P01 \xff",))
        _reload()
        out = _case(5, "P05 new", create_backup=False)
        assert out.get("changed") is True, out
        assert out["old_name_left_in"] == {"saved_graph_labels": 1}, out
