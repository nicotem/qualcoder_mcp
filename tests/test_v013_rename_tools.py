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
    "A file name must not contain": "invisible or control characters",
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
        (2, "notes.", "This text has no stored file"),
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

    @pytest.mark.parametrize("on_disk, found", [
        ([], None), (["interview.txt"], "interview.txt"),
        (["interview.txt.txt"], "interview.txt.txt")])
    def test_a_text_with_no_stored_path(self, project, on_disk, found):
        for name in on_disk:
            _documents_file(project, name)
        out = _file(1, "P01.txt", create_backup=False)
        if found is None:
            assert out["stored_copy"]["kind"] == "none"
            assert "stored_name" not in out["stored_copy"]
        else:
            assert out["stored_copy"] == {
                "kind": "found_by_name", "stored_name": found,
                "note": out["stored_copy"]["note"]}

    def test_the_notes_and_the_counts(self, project):
        _saved_places(project)
        _exec(project, "INSERT INTO gr_file_text_item (grid, fid, "
                       "displaytext) VALUES (1, 1, 'Interview.TXT'), "
                       "(1, 2, 'interview.txt')")
        _exec(project, "INSERT INTO files_filter (name, filter) VALUES "
                       "('f', 'Name like interview.txt')")
        _add_file(project, 7, "interview_p1.jpg", mediapath="/images/x.jpg")
        _reload()
        out = _file(1, "P01.txt", create_backup=False)
        assert out["old_name_left_in"] == {
            "saved_graph_labels": 1, "saved_filters": 1, "file_ids": [7]}
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
