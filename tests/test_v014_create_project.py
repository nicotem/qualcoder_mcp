# SPDX-License-Identifier: LGPL-3.0-or-later
"""v0.14, creating a project: the tool (brief C, parts 3 to 6).

Where the project goes and what it may be called (part 3), the coder
name (part 4), the selection and the result (part 5), and failures
(part 6). The format itself is tests/test_v014_create_project_format.py.

The name rules also run on a case-sensitive disk: on CI's Linux runners
for real, and on a Mac by pointing pytest's --basetemp at a
case-sensitive disk image; the tests that need it say which half ran.
"""

import json
import os
import sqlite3
import sys
import unicodedata
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server
from qualcoder_mcp import database, new_project
from qualcoder_mcp.database import file_name_problem


@pytest.fixture(autouse=True)
def _restore_selection():
    """Put the server's selected project back after each test, closing
    whatever the test selected (an open handle keeps Windows from
    removing tmp_path)."""
    saved = (server.db, server.current_project_path)
    yield
    if server.db is not None and server.db is not saved[0]:
        try:
            server.db.close()
        except Exception:
            pass
    server.db, server.current_project_path = saved


def create(name, directory=None, coder_name="carol", **kwargs):
    return json.loads(server.create_project(
        name, directory=None if directory is None else str(directory),
        coder_name=coder_name, **kwargs))


def refused(answer) -> str:
    assert answer["success"] is False and answer["created"] is False, answer
    return answer["error"]


def workspace() -> Path:
    return database.default_workspace()


def case_sensitive(folder: Path) -> bool:
    probe = folder / "CaseProbe"
    probe.mkdir()
    try:
        return not (folder / "caseprobe").exists()
    finally:
        probe.rmdir()


# ---------------------------------------------------------------------------
# Part 3: the name
# ---------------------------------------------------------------------------

class TestTheName:

    @pytest.mark.parametrize("name,words", [
        ("", "must not be empty"),
        ("   ", "must not be empty"),
        ("...", "must not be empty"),
        ("a\tb", "control characters"),
        ("a​b", "invisible formatting characters"),
        ("a b", "line or paragraph separator"),
        ("a/b", "'/', '\\', '..' or ':'"),
        ("a\\b", "'/', '\\', '..' or ':'"),
        ("a..b", "'/', '\\', '..' or ':'"),
        ("a:b", "'/', '\\', '..' or ':'"),
        ("a<b", "< > ? * or \""),
        ("a*b", "< > ? * or \""),
        ("a\"b", "< > ? * or \""),
        ("a|b", "QualCoder creates such a project but can never open it"),
        ("Study.", "must not end with a dot or a space"),
        ("Study .qda", "must not end with a dot or a space"),
        ("CON", "Windows device name"),
        ("com1", "Windows device name"),
        ("LPT¹.notes", "Windows device name"),
        ("Study_backup_1", "'_backup_'"),
        ("x_BKUP_pilot", "'_BKUP_'"),
        (".hidden", "must not start with a dot"),
        ("x.qda.qda", "must not end in '.qda' twice"),
    ])
    def test_each_refusal_names_its_reason(self, name, words):
        text = refused(create(name))
        assert words in text, text
        assert text.startswith("A project name must")
        assert not workspace().exists()          # nothing was made

    def test_the_surrogate_refusal(self):
        text = refused(create("a\ud800b"))
        assert "unpaired surrogate" in text

    def test_two_hundred_bytes_and_no_more(self):
        assert create("a" * 200)["created"] is True
        assert "at most 200 bytes" in refused(create("b" * 201))
        # 66 Hangul syllables are 198 bytes; 67 CJK letters are 201
        assert create("한" * 66)["created"] is True
        assert "(this one has 201)" in refused(create("中" * 67))

    def test_one_typed_qda_is_dropped_in_any_letter_case(self):
        answer = create("Typed.QDA")
        assert answer["project_name"] == "Typed"
        assert Path(answer["project_path"]).name == "Typed.qda"

    def test_spaces_trimmed_and_nfc(self):
        decomposed = "Café study"
        answer = create(f"  {decomposed}  ")
        assert answer["project_name"] == unicodedata.normalize(
            "NFC", decomposed)
        assert Path(answer["project_path"]).name == "Café study.qda"

    @settings(max_examples=300, deadline=None)
    @given(st.text(max_size=40))
    def test_every_name_the_file_rule_refuses_is_refused(self, name):
        """v0.13's file-name rule is the project's first rule."""
        stem = new_project.normalise_project_name(name)
        if file_name_problem(stem) is not None:
            assert new_project.project_name_problem(stem) is not None

    def test_backup_markers_are_refused_in_exact_case_only(self):
        """Both programs match the markers in exact case, so a name
        holding '_bkup_' is neither hidden nor deleted as a backup."""
        assert new_project.project_name_problem("x_bkup_y") is None
        assert new_project.project_name_problem("x_Backup_y") is None


# ---------------------------------------------------------------------------
# Part 3: where it goes
# ---------------------------------------------------------------------------

class TestWhere:

    def test_the_workspace_by_default_made_with_its_parents(self):
        assert not workspace().exists()
        answer = create("First")
        folder = Path(answer["project_path"])
        assert folder.parent.resolve() == workspace().resolve()
        assert folder.is_dir() and (folder / "data.qda").is_file()

    def test_an_existing_folder_the_researcher_names(self, tmp_path):
        target = tmp_path / "my projects"
        target.mkdir()
        answer = create("Named", target)
        assert Path(answer["project_path"]).parent == target.resolve()
        assert not workspace().exists()

    def test_a_home_relative_folder(self):
        target = Path.home() / "Studies"
        target.mkdir()
        answer = create("Tilde", "~/Studies")
        assert Path(answer["project_path"]).parent == target.resolve()

    def test_a_missing_folder_is_not_created(self, tmp_path):
        text = refused(create("X", tmp_path / "nowhere"))
        assert "does not exist" in text
        assert not (tmp_path / "nowhere").exists()

    def test_a_file_is_not_a_folder(self, tmp_path):
        (tmp_path / "file.txt").write_text("x")
        assert "is not a folder" in refused(create("X", tmp_path / "file.txt"))

    def test_an_empty_directory_argument(self):
        assert "`directory` is empty" in refused(create("X", "  "))

    def test_not_inside_the_state_folder(self):
        from qualcoder_mcp import preview_tokens
        state = Path(preview_tokens.STATE_HOME)
        state.mkdir(parents=True, exist_ok=True)
        text = refused(create("X", state))
        assert "state folder" in text
        (state / "deeper").mkdir()
        assert "state folder" in refused(create("X", state / "deeper"))

    @pytest.mark.parametrize("inner", ["", "sub/deeper"])
    def test_not_inside_a_project_folder(self, tmp_path, inner):
        outer = tmp_path / "Outer.QDA" / inner
        outer.mkdir(parents=True)
        text = refused(create("Nested", outer))
        assert "inside the project folder 'Outer.QDA'" in text
        assert not (outer / "Nested.qda").exists()

    def test_not_on_a_path_with_a_pipe(self, tmp_path):
        if os.name == "nt":
            pytest.skip("Windows cannot hold '|' in a folder name")
        target = tmp_path / "a|b"
        target.mkdir()
        text = refused(create("X", target))
        assert "'|' in its path" in text

    def test_through_a_link_the_real_folder_is_checked(self, tmp_path):
        outer = tmp_path / "Real.qda"
        outer.mkdir()
        link = tmp_path / "innocent"
        try:
            link.symlink_to(outer, target_is_directory=True)
        except OSError:
            pytest.skip("this system cannot make a symbolic link here")
        assert "inside the project folder" in refused(create("X", link))


# ---------------------------------------------------------------------------
# Part 3: what is already there
# ---------------------------------------------------------------------------

def _real_project(folder: Path) -> None:
    new_project.write_project(folder, new_project.creation_statements(
        "dave", "x (QualCoder)", "2026-09-26 10:00:00"))


class TestExistingNames:

    def test_a_real_project_is_named_and_left_alone(self, tmp_path):
        _real_project(tmp_path / "Study.qda")
        before = (tmp_path / "Study.qda" / "data.qda").read_bytes()
        text = refused(create("Study", tmp_path))
        assert "A project called 'Study.qda' already exists" in text
        assert "select_project" in text
        assert (tmp_path / "Study.qda" / "data.qda").read_bytes() == before

    @pytest.mark.parametrize("state", ["no database", "empty database"])
    def test_an_orphan_gets_the_orphan_wording(self, tmp_path, state):
        folder = tmp_path / "Half.qda"
        folder.mkdir()
        if state == "empty database":
            (folder / "data.qda").write_bytes(b"")
            (folder / "data.qda-journal").write_bytes(b"\0" * 512)
        text = refused(create("Half", tmp_path))
        assert "remains of a project creation that did not finish" in text
        assert "never deletes" in text
        assert "select_project" not in text
        assert folder.is_dir()

    def test_an_unreadable_database_is_not_called_an_orphan(self, tmp_path):
        folder = tmp_path / "Odd.qda"
        folder.mkdir()
        (folder / "data.qda").write_bytes(b"not a database at all" * 50)
        text = refused(create("Odd", tmp_path))
        assert "its database could not be read" in text
        assert "delete" not in text and "remains" not in text

    def test_a_file_or_a_link_of_that_name(self, tmp_path):
        (tmp_path / "File.qda").write_text("x")
        assert "not a project folder" in refused(create("File", tmp_path))
        try:
            (tmp_path / "Link.qda").symlink_to(tmp_path / "nowhere")
        except OSError:
            return
        assert "not a project folder" in refused(create("Link", tmp_path))

    @pytest.mark.parametrize("existing,new", [
        ("study.qda", "Study"),
        ("STUDY.QDA", "Study"),
        ("Stra\u00dfe.qda", "Strasse"),
        ("\u01c4emal.qda", "\u01c5emal"),
    ])
    def test_letter_case_is_the_same_name(self, tmp_path, existing, new):
        work = tmp_path / "work"
        work.mkdir()
        (work / existing).mkdir()
        text = refused(create(new, work))
        assert f"already holds '{existing}'" in text
        assert "ignores letter case" in text
        assert [p.name for p in work.iterdir()] == [existing]
        # On a case-sensitive disk the mkdir alone would have allowed it:
        # the listing check is what refused (CI's Linux jobs, or a Mac
        # with --basetemp on a case-sensitive disk image).
        if case_sensitive(work):
            probe = work / f"{new}.qda"
            probe.mkdir()
            probe.rmdir()

    def test_a_decomposed_accent_is_the_same_name(self, tmp_path):
        work = tmp_path / "work"
        work.mkdir()
        decomposed = unicodedata.normalize("NFD", "Caf\u00e9.qda")
        try:
            (work / decomposed).mkdir()
        except OSError:
            pytest.skip("this disk refuses the decomposed spelling")
        entry = os.listdir(work)[0]
        answer = create("Caf\u00e9", work)
        if entry == "Caf\u00e9.qda":
            # APFS and HFS+ hand the name back composed: it is simply the
            # same name, reported as the existing folder
            assert "already exists" in refused(answer) or \
                "no usable project database" in refused(answer)
        else:
            assert "already holds" in refused(answer)

    def test_older_backups_beside_the_target_are_refused(self, tmp_path):
        work = tmp_path / "work"
        work.mkdir()
        for name in ("Study_backup_20190101_000000.qda",
                     "Study_BKUP_2019_pilot.qda"):
            (work / name).mkdir()
        (work / "Study_backup_notes.txt").write_text("x")
        text = refused(create("Study", work))
        assert "'Study_BKUP_2019_pilot.qda'" in text
        assert "'Study_backup_20190101_000000.qda'" in text
        assert "notes.txt" not in text
        assert "deletes '_BKUP_' folders" in text
        assert not (work / "Study.qda").exists()

    def test_a_backup_named_in_another_letter_case_is_refused(self, tmp_path):
        work = tmp_path / "work"
        work.mkdir()
        (work / "study_bkup_x.qda").mkdir()
        assert "named like backups" in refused(create("Study", work))

    def test_a_name_made_between_the_look_and_the_claim(self, tmp_path,
                                                         monkeypatch):
        work = tmp_path / "work"
        work.mkdir()
        real_scan = new_project.scan_parent
        calls = []

        def scan(parent, folder_name, stem):
            calls.append(1)
            if len(calls) == 1:
                (parent / folder_name).mkdir()     # someone else, now
                return None
            return real_scan(parent, folder_name, stem)

        monkeypatch.setattr(new_project, "scan_parent", scan)
        text = refused(create("Raced", work))
        assert "remains of a project creation" in text
        assert len(calls) == 2


class TestPathLength:

    def test_windows_refuses_what_it_could_not_make(self, tmp_path):
        short = tmp_path / "S.qda"
        assert new_project.windows_path_refusal(short, True, False) is None
        deep = Path("C:\\" + "d" * 230) / "Study.qda"
        text = new_project.windows_path_refusal(deep, True, False)
        assert "too long for Windows" in text and "247" in text
        assert new_project.windows_path_refusal(deep, True, True) is None
        assert new_project.windows_path_refusal(deep, False, False) is None

    def test_a_long_path_draws_a_warning_everywhere(self, tmp_path):
        target = tmp_path / ("d" * 150)
        target.mkdir()
        answer = create("Long", target)
        assert answer["created"] is True
        assert any("travel better" in w for w in answer["warnings"])
        typical = Path("/Users/researcher/Documents/Qualcoder MCP Projects"
                       "/Interview study.qda")
        assert new_project.long_path_warning(typical) is None
        assert new_project.file_name_room(typical) >= \
            new_project.FILE_NAME_ROOM


# ---------------------------------------------------------------------------
# Part 4: the researcher's coder name
# ---------------------------------------------------------------------------

def _stored_coder(answer):
    conn = sqlite3.connect(str(Path(answer["project_path"]) / "data.qda"))
    try:
        row = conn.execute("SELECT codername FROM project").fetchone()
        names = [r[0] for r in conn.execute(
            "SELECT name FROM coder_names ORDER BY rowid")]
    finally:
        conn.close()
    return row[0], names


class TestTheCoderName:

    def test_missing_asks_and_makes_nothing(self):
        answer = json.loads(server.create_project("Study"))
        text = refused(answer)
        assert answer["action_required"] == "ask_researcher_coder_name"
        assert "Settings, Coder name" in text and "never be guessed" in text
        assert "coder_name_not_known=true" in text
        assert not workspace().exists()

    def test_a_name_is_stored(self):
        answer = create("Study", coder_name="  Carol Smith ")
        assert answer["coder_name"] == "Carol Smith"
        assert answer["coder_name_known"] is True
        assert _stored_coder(answer) == (
            "Carol Smith", ["Carol Smith", server.SPEAKER_SYSTEM_CODER])
        assert server.CODER_NAME_NOT_KNOWN_WARNING not in answer["warnings"]

    def test_not_known_stores_an_empty_name_with_the_warning(self):
        answer = json.loads(server.create_project(
            "Study", coder_name_not_known=True))
        assert answer["created"] is True
        assert answer["coder_name"] is None
        assert answer["coder_name_known"] is False
        assert server.CODER_NAME_NOT_KNOWN_WARNING in answer["warnings"]
        assert "first opens this project in QualCoder" in \
            server.CODER_NAME_NOT_KNOWN_WARNING
        assert _stored_coder(answer) == ("", [server.SPEAKER_SYSTEM_CODER])

    @pytest.mark.parametrize("value", ["", "   "])
    def test_an_empty_name_is_not_a_not_known(self, value):
        """A model filling optional arguments often sends '': that is
        not the researcher saying they do not know."""
        text = refused(create("Study", coder_name=value))
        assert "coder_name is empty" in text
        assert "coder_name_not_known=true" in text
        assert not workspace().exists()

    def test_a_name_and_the_flag_together(self):
        text = refused(create("Study", coder_name="carol",
                              coder_name_not_known=True))
        assert "not both" in text

    def test_the_speaker_coder_is_refused(self):
        text = refused(create("Study",
                              coder_name=server.SPEAKER_SYSTEM_CODER))
        assert "speaker coder" in text

    def test_default_is_accepted(self):
        """QualCoder's own name for anyone who never set one."""
        answer = create("Study", coder_name="default")
        assert _stored_coder(answer)[0] == "default"

    @pytest.mark.parametrize("value,words", [
        ("a" * 81, "the maximum is 80"),
        ("a\nb", "control characters"),
        ("x ##### y", "'#####'"),
    ])
    def test_the_coder_name_rule(self, value, words):
        assert words in refused(create("Study", coder_name=value))

    def test_asked_last_in_the_same_answer(self, tmp_path):
        """A refused name and a missing coder name: both in one answer,
        the name first, so the researcher is asked once."""
        answer = json.loads(server.create_project("bad|name"))
        text = refused(answer)
        assert text.startswith(new_project.PIPE_REFUSAL)
        assert "Before calling again, also: Ask the researcher" in text
        assert answer["action_required"] == "ask_researcher_coder_name"
        (tmp_path / "Taken.qda").mkdir()
        answer = json.loads(server.create_project(
            "Taken", directory=str(tmp_path)))
        assert "remains of a project creation" in refused(answer)
        assert "also: Ask the researcher" in refused(answer)
        # and a coder-name refusal rides along the same way
        text = refused(create("bad|name", coder_name=""))
        assert "also: coder_name is empty" in text

    def test_qualcoders_settings_file_is_never_read(self, tmp_path):
        """An audit hook records every file opened, folder listed and
        database connected while the tool runs in a home that holds
        QualCoder's settings folder, with its config.ini: nothing under
        it is touched (the settings file holds API keys)."""
        settings = Path.home() / ".qualcoder"
        settings.mkdir()
        (settings / "config.ini").write_text(
            "[DEFAULT]\ncodername = alice\n", encoding="utf-8")
        seen = []
        _AUDIT["sink"] = seen
        try:
            json.loads(server.create_project("Audited",
                                             coder_name_not_known=True))
            create("Audited2", coder_name="carol")
            json.loads(server.create_project("Asked"))
        finally:
            _AUDIT["sink"] = None
        assert seen, "the hook saw nothing: it is not recording"
        touched = [p for p in seen if _under(p, settings)]
        assert touched == []


_AUDIT = {"sink": None}
_AUDIT_EVENTS = {"open", "os.listdir", "os.scandir", "sqlite3.connect"}


def _audit(event, args):
    sink = _AUDIT["sink"]
    if sink is None or event not in _AUDIT_EVENTS or not args:
        return
    target = args[0]
    if isinstance(target, (str, bytes, os.PathLike)):
        sink.append(os.fsdecode(target))


sys.addaudithook(_audit)


def _under(path: str, folder: Path) -> bool:
    try:
        return Path(path).resolve().is_relative_to(folder.resolve())
    except (OSError, ValueError):
        return False


class TestTheAiCoderNameSetter:

    def test_the_speaker_coder_is_refused_as_the_ai_name(self):
        create("Study")
        answer = json.loads(server.set_project_ai_coder_name(
            server.SPEAKER_SYSTEM_CODER))
        assert "speaker coder" in answer["error"]
        assert "Nothing was changed" in answer["error"]
        assert not (Path(server.current_project_path) /
                    "qualcoder_mcp.json").exists()


# ---------------------------------------------------------------------------
# Part 5: after creation
# ---------------------------------------------------------------------------

class TestAfterCreation:

    def test_the_new_project_is_selected(self):
        answer = create("Selected")
        assert answer["selected"] is True
        folder = Path(answer["project_path"])
        assert Path(server.current_project_path) == folder
        assert server.db is not None and server.db.read_only
        current = json.loads(server.get_current_project())
        assert Path(current["current_project"]) == folder
        mru = json.loads(Path(server._MRU_FILE).read_text(encoding="utf-8"))
        assert Path(mru["project_path"]) == folder / "data.qda"

    def test_no_process_scan_so_no_false_alarm(self, monkeypatch):
        """A machine where the scan would match: select_project warns
        that the project APPEARS to be open; create_project never runs
        the scan for the project it has just made."""
        calls = []

        def hits():
            calls.append(1)
            return ["/usr/bin/python3 -m qualcoder"]

        monkeypatch.setattr(database, "_qualcoder_process_hits", hits)
        answer = create("Quiet")
        assert calls == []
        assert answer["qualcoder_gui_signals"] == []
        assert "APPEARS" not in json.dumps(answer)
        selected = json.loads(server.select_project(answer["project_path"]))
        assert "APPEARS to be open" in selected["warning"]

    def test_the_previous_project_is_named(self):
        server.db, server.current_project_path = None, None
        first = create("First")
        assert first["previous_project"] is None
        assert not any("previous_project" in step
                       for step in first["next_steps"])
        second = create("Second")
        assert second["previous_project"] == first["project_path"]
        assert any("previous_project" in step
                   for step in second["next_steps"])

    def test_what_each_build_does_on_first_open(self):
        known = create("Known")
        opening = known["opening_in_qualcoder"]
        assert "without a message and without changing its format" in \
            opening["4.0"]
        assert known["project_path"] in opening["4.0"]
        assert "keep it or switch" in opening["4.0"]
        assert opening["3.8.2"] == server.OPENING_IN_QUALCODER_382
        for words in ("opens it without any warning",
                      "sub-codes appear there as ordinary codes",
                      "labels, arrows and memo notes on graphs",
                      "goes back under its parent code",
                      "become ordinary codes with no category",
                      "Work on this project in QualCoder 4.0"):
            assert words in opening["3.8.2"]
        unknown = json.loads(server.create_project(
            "Unknown", coder_name_not_known=True))
        assert "records the researcher's own on that first open" in \
            unknown["opening_in_qualcoder"]["4.0"]
        assert "one backup copy" in unknown["opening_in_qualcoder"]["4.0"]

    def test_the_next_steps(self):
        steps = " ".join(create("Steps", coder_name="carol")["next_steps"])
        assert "set_project_ai_coder_name" in steps
        assert "must differ from the researcher's own QualCoder coder " \
               "name, \"carol\"" in steps
        assert "import_text_file" in steps and "parent_code_id" in steps
        steps = " ".join(json.loads(server.create_project(
            "Steps2", coder_name_not_known=True))["next_steps"])
        assert "is not known" in steps

    def test_created_but_not_selected_is_said(self, monkeypatch):
        def refuse(path, read_only=True):
            raise database.DatabaseLockedError("locked")

        monkeypatch.setattr(server, "switch_project", refuse)
        answer = create("Unselected")
        assert answer["created"] is True and answer["selected"] is False
        assert "created but could not be selected" in \
            answer["selection_error"]
        assert "DatabaseLockedError" in answer["selection_error"]
        assert (Path(answer["project_path"]) / "data.qda").is_file()

    def test_nothing_else_is_written(self):
        """No ai_data, lock file, sidecar, backup or journal entry; the
        workspace holds the one folder."""
        answer = create("Bare")
        folder = Path(answer["project_path"])
        assert sorted(p.name for p in folder.iterdir()) == [
            "audio", "data.qda", "documents", "images", "video"]
        assert [p.name for p in folder.parent.iterdir()] == ["Bare.qda"]
        conn = sqlite3.connect(str(folder / "data.qda"))
        try:
            assert conn.execute("SELECT count(*) FROM journal"
                                ).fetchone()[0] == 0
            assert conn.execute("SELECT memo FROM project").fetchone() == (
                "",)
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Part 6: failures
# ---------------------------------------------------------------------------

class _FailAt:
    """A connection that raises `error` after its `after`-th statement,
    or at COMMIT when `after` is None."""

    def __init__(self, conn, error, after=None):
        self._conn, self._error, self._after, self._count = \
            conn, error, after, 0

    in_transaction = property(lambda self: self._conn.in_transaction)

    def close(self):
        self._conn.close()

    def execute(self, sql, args=()):
        if sql == "COMMIT" and self._after is None:
            raise self._error
        result = self._conn.execute(sql, args)
        if sql not in ("BEGIN", "COMMIT", "ROLLBACK"):
            self._count += 1
            if self._count == self._after:
                raise self._error
        return result


def _fail_with(monkeypatch, error, after=None):
    real = new_project._connect
    monkeypatch.setattr(new_project, "_connect",
                        lambda path: _FailAt(real(path), error, after))


class TestFailures:

    def test_a_failure_at_commit_is_the_tools_own_words(self, monkeypatch):
        _fail_with(monkeypatch, sqlite3.OperationalError("database is locked"))
        text = refused(create("AtCommit"))
        assert text.startswith("The project could not be created:")
        assert "Nothing was left behind." in text
        assert server.DB_UNAVAILABLE_ERROR not in text
        assert "close QualCoder" not in text and "QualCoder is open" not in text
        assert list(workspace().iterdir()) == []

    def test_a_full_disk_is_named(self, monkeypatch):
        _fail_with(monkeypatch,
                   sqlite3.OperationalError("database or disk is full"), 20)
        text = refused(create("Full"))
        assert "the disk is full" in text
        assert list(workspace().iterdir()) == []

    def test_a_full_disk_at_a_subfolder(self, monkeypatch):
        real_mkdir = Path.mkdir

        def mkdir(self, *args, **kwargs):
            if self.name == "video":
                raise OSError(28, "No space left on device")
            return real_mkdir(self, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", mkdir)
        text = refused(create("FullAgain"))
        assert "the disk is full" in text and "Nothing was left" in text
        assert list(workspace().iterdir()) == []

    @pytest.mark.skipif(os.name == "nt" or getattr(os, "geteuid", lambda: 1)()
                        == 0, reason="POSIX permissions, not as root")
    def test_no_permission_to_write_there(self, tmp_path):
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0o500)
        try:
            text = refused(create("NoWrite", locked))
        finally:
            locked.chmod(0o700)
        assert "may not write there" in text
        assert list(locked.iterdir()) == []

    def test_the_workspace_cannot_be_made(self):
        # the sandbox's Documents (the workspace's parent) as a file
        workspace().parent.write_text("a file, not a folder")
        text = refused(create("NoWorkspace"))
        assert text.startswith("The workspace folder")
        assert "Nothing was created" in text

    def test_a_folder_holding_someone_elses_file_is_left(self, tmp_path,
                                                        monkeypatch):
        work = tmp_path / "work"
        work.mkdir()

        class Drop(_FailAt):
            def execute(self, sql, args=()):
                if sql == "COMMIT":
                    (work / "Shared.qda" / "theirs.txt").write_text("keep")
                return super().execute(sql, args)

        real = new_project._connect
        monkeypatch.setattr(
            new_project, "_connect",
            lambda path: Drop(real(path), sqlite3.OperationalError("x")))
        text = refused(create("Shared", work))
        assert "was kept, because it holds something this tool did not " \
               "make" in text
        assert "delete" not in text
        assert (work / "Shared.qda" / "theirs.txt").read_text() == "keep"
        assert sorted(p.name for p in (work / "Shared.qda").iterdir()) == [
            "theirs.txt"]

    def test_what_could_not_be_removed_is_named(self, tmp_path,
                                                monkeypatch):
        work = tmp_path / "work"
        work.mkdir()
        _fail_with(monkeypatch, sqlite3.OperationalError("x"), 5)
        real_unlink = Path.unlink

        def unlink(self, *args, **kwargs):
            if self.name == "data.qda":
                raise PermissionError(13, "in use")
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", unlink)
        text = refused(create("Stuck", work))
        assert "could not be removed (data.qda, in" in text
        assert "may delete what this call made by hand" in text


_SPILL = """
import os, sqlite3, sys
sys.path.insert(0, {src!r})
from qualcoder_mcp import new_project as n
target = sys.argv[1]
os.mkdir(target)
for name in n.SUBFOLDERS:
    os.mkdir(os.path.join(target, name))
conn = sqlite3.connect(os.path.join(target, "data.qda"), isolation_level=None)
conn.execute("PRAGMA cache_size=1")       # pages reach the file before COMMIT
conn.execute("BEGIN")
for sql, args in n.creation_statements("carol", "x QualCoder", "2026-09-26"):
    conn.execute(sql, args)
os._exit(9)                                # killed during the commit's work
"""


class TestWhatACrashLeaves:
    """A crash before COMMIT leaves an empty database and a journal (an
    orphan); a crash while COMMIT writes the pages leaves a full database
    with a hot journal, which is also what a real project looks like
    after QualCoder crashed mid-write. Only the first gets the orphan
    wording, and neither is opened for writing (a read-write open would
    roll a stranger's journal back)."""

    def _hot(self, tmp_path) -> Path:
        import subprocess
        script = tmp_path / "spill.py"
        src = str(Path(__file__).resolve().parent.parent / "src")
        script.write_text(_SPILL.format(src=src), encoding="utf-8")
        target = tmp_path / "work" / "Hot.qda"
        target.parent.mkdir()
        proc = subprocess.run([sys.executable, str(script), str(target)],
                              capture_output=True, timeout=120)
        assert proc.returncode == 9, proc.stderr
        assert (target / "data.qda").stat().st_size > 0
        assert (target / "data.qda-journal").stat().st_size > 0
        return target

    @staticmethod
    def _bytes(folder):
        return {p.name: p.read_bytes() for p in folder.iterdir()
                if p.is_file()}

    def test_a_hot_journal_is_not_an_orphan_and_is_not_touched(self,
                                                               tmp_path):
        folder = self._hot(tmp_path)
        before = self._bytes(folder)
        text = refused(create("Hot", folder.parent))
        assert "its database could not be read" in text
        assert "remains" not in text and "delete" not in text
        assert self._bytes(folder) == before     # the journal is intact

    def test_an_empty_database_with_a_journal_is_an_orphan(self, tmp_path):
        folder = tmp_path / "work" / "Cold.qda"
        folder.mkdir(parents=True)
        (folder / "data.qda").write_bytes(b"")
        (folder / "data.qda-journal").write_bytes(b"\xd9\xd5\x05\xf9" * 128)
        before = self._bytes(folder)
        text = refused(create("Cold", folder.parent))
        assert "remains of a project creation that did not finish" in text
        assert self._bytes(folder) == before


# ---------------------------------------------------------------------------
# Part 8: a round trip from creation to coding
# ---------------------------------------------------------------------------

INTERVIEW = ("I: How do you cope?\n\nP: I go for a run after work, and I "
             "talk to colleagues who understand. Deadlines still keep me "
             "awake some nights.")


class TestRoundTrip:

    def test_from_creation_to_coding(self, monkeypatch):
        """Create, select (no process-scan warning), set the AI coder
        name, import, a category, a code in it and a sub-code under it,
        suggest, approve and apply two codings, read them back; the
        project is still 4.0's format in everything QualCoder reads."""
        monkeypatch.setattr(database, "_qualcoder_process_hits",
                            lambda: ["/usr/bin/python3 -m qualcoder"])
        made = create("Round trip", coder_name="carol")
        assert made["selected"] and "APPEARS" not in json.dumps(made)
        folder = Path(made["project_path"])
        # the first write asks for the AI coder name, as on any project
        asked = json.loads(server.create_code("Coping"))
        assert asked.get("action_required") == "set_project_ai_coder_name"
        assert json.loads(server.set_project_ai_coder_name(
            "AI Coding Assistant"))["success"] is True
        imported = json.loads(server.import_text_file(
            "interview.txt", INTERVIEW))
        assert imported["success"] is True, imported
        file_id = imported["file"]["id"] if "file" in imported else \
            imported["file_id"]
        assert json.loads(server.create_category("Responses"))["created"]
        parent = json.loads(server.create_code("Coping",
                                               category="Responses"))
        child = json.loads(server.create_code(
            "Exercise", parent_code_id=parent["code"]["id"]))
        assert child["created"] is True, child
        session = server.analyze_for_coding([file_id])
        assert session.lstrip().startswith("{"), session[:300]
        sid = json.loads(session)["coding_session_id"]
        recorded = json.loads(server.record_suggestions(sid, [
            {"file_id": file_id, "code_name": "Coping",
             "segment_text": "talk to colleagues who understand",
             "reasoning": "social support", "confidence": 0.9},
            {"file_id": file_id, "code_name": "Exercise",
             "segment_text": "I go for a run after work",
             "reasoning": "exercise", "confidence": 0.9}]))
        guids = [r["guid"] for r in recorded["recorded"]]
        assert "error" not in server.update_suggestion_status(
            sid, approve=guids).lower()
        applied = server.apply_codings(sid)
        assert "CODINGS APPLIED" in applied, applied
        for code in (parent, child):
            segments = json.loads(server.get_coded_segments(
                code["code"]["id"]))
            assert segments["segments"], segments
        conn = sqlite3.connect(str(folder / "data.qda"))
        try:
            rows = conn.execute(
                "SELECT c.name, t.owner, t.seltext FROM code_text t "
                "JOIN code_name c ON c.cid = t.cid ORDER BY c.name"
            ).fetchall()
            sub = conn.execute("SELECT supercid, catid FROM code_name "
                               "WHERE name = 'Exercise'").fetchone()
            coders = [r[0] for r in conn.execute(
                "SELECT name FROM coder_names ORDER BY rowid")]
        finally:
            conn.close()
        assert rows == [
            ("Coping", "AI Coding Assistant",
             "talk to colleagues who understand"),
            ("Exercise", "AI Coding Assistant", "I go for a run after work")]
        assert sub == (parent["code"]["id"], None)
        assert coders[:2] == ["carol", server.SPEAKER_SYSTEM_CODER]
        # still 4.0's format in everything QualCoder reads
        from qc40_format_facts import structure
        oracle = json.loads((Path(__file__).parent / "fixtures" /
                             "qc40_new_project.json").read_text("utf-8"))
        facts = structure(folder)
        for key in ("order", "columns", "indexes", "triggers", "header"):
            assert facts[key] == oracle["structure"][key], key


# ---------------------------------------------------------------------------
# Part 8: Windows' path limits, on the Windows CI jobs
# ---------------------------------------------------------------------------

def _extended(path: Path) -> str:
    """The extended-length form Windows accepts past 260 characters."""
    return "\\\\?\\" + str(path)


def _deep_folder(base: Path, length: int) -> Path:
    """A folder under `base` whose own path is `length` characters long,
    made with the extended-length prefix so the test does not depend on
    the limit it is about."""
    folder = base
    while len(str(folder)) < length:
        room = length - len(str(folder)) - 1
        folder = folder / ("d" * max(1, min(50, room)))
    os.makedirs(_extended(folder), exist_ok=True)
    return folder


@pytest.mark.skipif(os.name != "nt", reason="Windows' own path limits")
class TestWindowsPathLimits:
    """Creation where data.qda's path is about 250 and about 270
    characters long (the study's check asked for both, to settle what
    Windows and SQLite do before the wording is fixed). Whatever the
    runner's long-path setting, the answer is either a created project
    this server opens, or the tool's own refusal or failure with nothing
    left behind; the outcome is written into the CI log."""

    @pytest.mark.parametrize("data_path_length", [250, 270])
    def test_near_the_limit(self, tmp_path, data_path_length,
                            record_property):
        tail = len("\\Study.qda\\data.qda")
        parent = _deep_folder(tmp_path.resolve(),
                              data_path_length - tail)
        answer = json.loads(server.create_project(
            "Study", directory=str(parent), coder_name="carol"))
        long_paths = new_project.windows_long_paths_enabled()
        folder = parent / "Study.qda"
        outcome = ("created" if answer.get("created") else
                   "refused: " + answer.get("error", "")[:160])
        record_property("windows_path_probe", (
            f"data.qda at {len(str(folder / 'data.qda'))} characters, long "
            f"paths {'on' if long_paths else 'off'}: {outcome}"))
        if answer.get("created"):
            db = database.QualcoderDatabase(str(folder))
            try:
                assert db.db_version == "v17"
            finally:
                db.close()
            return
        text = answer["error"]
        assert ("too long for Windows" in text
                or text.startswith("The project could not be created"))
        assert server.DB_UNAVAILABLE_ERROR not in text
        assert not os.path.lexists(_extended(folder))


class TestTheLogNamesNothing:
    """The tool's own log lines carry no name, path or error text: a
    project is often named after its participant, and the host keeps
    the log (the privacy rules of v0.14). The failure lines carry the
    stage and the error's kind, even when the error's own text holds
    the path."""

    MARK = "Zebedee"

    def _mine(self, caplog):
        return [r.getMessage() for r in caplog.records
                if r.getMessage().startswith((
                    "Created a new project", "Creating a project failed",
                    "A created project could not be selected",
                    "The workspace could not be made"))]

    def test_success_and_each_failure(self, tmp_path, monkeypatch, caplog):
        import logging
        caplog.set_level(logging.DEBUG)
        work = tmp_path / f"{self.MARK} folder"
        work.mkdir()
        assert create(f"{self.MARK} study", work,
                      coder_name=f"{self.MARK} coder")["created"]
        _fail_with(monkeypatch, sqlite3.OperationalError(
            f"disk full at {work}/{self.MARK}.qda"), 3)
        refused(create(f"{self.MARK} two", work))
        monkeypatch.undo()

        def refuse(path, read_only=True):
            raise RuntimeError(f"cannot open {path}")

        monkeypatch.setattr(server, "switch_project", refuse)
        assert create(f"{self.MARK} three", work)["selected"] is False
        lines = self._mine(caplog)
        assert len(lines) == 4, lines
        assert not [line for line in lines if self.MARK in line]
        assert "Creating a project failed at the database stage: " \
               "OperationalError" in lines


class TestCreatingLogsNoNameOrPath:
    """With v0.14's privacy rules merged: creating a project named after
    its participant, in a folder so named, with the researcher's coder
    name, then setting the AI coder name and the project memo, and
    refusing the same name again, leaves no log record, from any module,
    that holds the name (the host keeps the log on disk)."""

    MARK = "Zebedee"

    def test_no_record_names_the_project(self, tmp_path, caplog):
        import logging
        caplog.set_level(logging.DEBUG)
        work = tmp_path / f"{self.MARK} folder"
        work.mkdir()
        made = create(f"{self.MARK} study", work,
                      coder_name=f"{self.MARK} Smith")
        assert made["created"] and made["selected"]
        assert json.loads(server.set_project_ai_coder_name(
            f"{self.MARK} AI"))["success"]
        assert json.loads(server.set_memo(
            "project", None, f"About {self.MARK}"))["success"]
        refused(create(f"{self.MARK} study", work))
        records = [r.getMessage() for r in caplog.records]
        assert records, "nothing was logged: the check sees nothing"
        assert "Created a new project (schema v17)" in records
        assert [line for line in records if self.MARK in line] == []


# ---------------------------------------------------------------------------
# Fix round 1 (QA M2, Security 1): only a true leftover is "unfinished"
# ---------------------------------------------------------------------------

def _folder(parent: Path, name: str, files=(), database=None,
            subfolders=True, journal=False) -> Path:
    folder = parent / f"{name}.qda"
    folder.mkdir()
    if subfolders:
        for sub in new_project.SUBFOLDERS:
            (folder / sub).mkdir()
    if database is not None:
        (folder / "data.qda").write_bytes(database)
    if journal:
        (folder / "data.qda-journal").write_bytes(b"\xd9\xd5\x05\xf9" * 128)
    for relative in files:
        target = folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"the researcher's own bytes")
    return folder


def _snapshot(folder: Path):
    return sorted((str(p.relative_to(folder)), p.is_dir(),
                   None if p.is_dir() else p.read_bytes())
                  for p in folder.rglob("*"))


class TestOnlyATrueLeftoverIsUnfinished:
    """The refuters' folders: each holds no usable database, and each
    holds more than a creation leaves. None may be called an unfinished
    creation or offered for deletion, in any of the three places."""

    CASES = {
        # QA: a project whose data.qda is empty, with its files, and a
        # QualCoder backup beside it
        "Fieldwork": dict(database=b"", files=(
            "audio/interview_01.m4a", "documents/consent_forms.pdf")),
        # QA and Security: iCloud's placeholders (macOS 13 and earlier)
        "Interviews": dict(files=(".data.qda.icloud",
                                  "documents/.transcript_07.docx.icloud")),
        # Security: files and no data.qda
        "Real Study": dict(files=("audio/interview-jane.m4a",
                                  "documents/consent-jane.pdf")),
        # the refuter: what QualCoder's own open leaves (an empty
        # data.qda created beside a recording)
        "Reopened": dict(database=b"", files=("audio/interview.m4a",)),
        # the refuter: the four empty subfolders and a note of one's own
        "Notes": dict(files=("memo.txt",)),
    }

    def _check_neutral(self, text, name):
        assert "no usable project database" in text, text
        assert "check what is in it before removing anything" in text
        assert "delete" not in text and "remains" not in text
        assert "did not finish" not in text
        assert "Nothing in it can be opened" not in text

    @pytest.mark.parametrize("name", sorted(CASES))
    def test_in_all_three_places(self, tmp_path, name):
        work = tmp_path / "work"
        work.mkdir()
        folder = _folder(work, name, **self.CASES[name])
        if name == "Fieldwork":
            (work / "Fieldwork_BKUP_20260901_10.qda").mkdir()
        before = _snapshot(folder)
        refusal = refused(create(name, work))
        self._check_neutral(refusal, name)
        assert refusal.startswith(f"A folder called '{name}.qda' exists")
        selected = json.loads(server.select_project(str(folder)))
        assert selected["success"] is False
        self._check_neutral(selected["error"], name)
        listing = json.loads(server.list_available_projects([str(work)]))
        (entry,) = [p for p in listing["projects"]
                    if p["name"] == name]
        assert entry["usable"] is False
        self._check_neutral(entry["note"], name)
        assert _snapshot(folder) == before
        if name == "Fieldwork":
            assert "'Fieldwork_BKUP_20260901_10.qda'" in refusal
        if name == "Interviews":
            assert "iCloud placeholders" in refusal
            assert "'.data.qda.icloud'" in refusal

    @pytest.mark.parametrize("layout", [
        dict(database=b"", journal=True),       # killed before COMMIT
        dict(subfolders=True),                  # killed before the database
        dict(subfolders=False),                 # killed after the claim
    ])
    def test_a_true_leftover_still_gets_the_leftover_words(self, tmp_path,
                                                           layout):
        work = tmp_path / "work"
        work.mkdir()
        folder = _folder(work, "Left", **layout)
        assert "remains of a project creation that did not finish" in \
            refused(create("Left", work))
        selected = json.loads(server.select_project(str(folder)))
        assert selected["error"].startswith(server.NO_USABLE_DATABASE)
        listing = json.loads(server.list_available_projects([str(work)]))
        assert listing["projects"][0]["note"] == server.NO_USABLE_DATABASE

    def test_a_linked_subfolder_is_not_a_leftover(self, tmp_path):
        work = tmp_path / "work"
        work.mkdir()
        folder = _folder(work, "Linked", subfolders=False)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        try:
            (folder / "audio").symlink_to(elsewhere,
                                          target_is_directory=True)
        except OSError:
            pytest.skip("this system cannot make a symbolic link here")
        assert not new_project.is_unfinished(folder)
        self._check_neutral(refused(create("Linked", work)), "Linked")

    def test_a_linked_database_is_never_opened(self, tmp_path):
        """Security 4: a `data.qda` that is a link counts as unreadable
        and is not followed, here to a stand-in for QualCoder's
        settings file."""
        work = tmp_path / "work"
        work.mkdir()
        folder = _folder(work, "Lure")
        target = tmp_path / "config.ini"
        target.write_text("[DEFAULT]\ncodername = alice\n")
        try:
            (folder / "data.qda").symlink_to(target)
        except OSError:
            pytest.skip("this system cannot make a symbolic link here")
        seen = []
        _AUDIT["sink"] = seen
        try:
            text = refused(create("Lure", work))
        finally:
            _AUDIT["sink"] = None
        assert "its database could not be read" in text
        assert not [p for p in seen if _under(p, target)
                    or Path(p).name.startswith("config.ini")]


# ---------------------------------------------------------------------------
# Fix round 1 (Security 2): the clean-up removes only what this call made
# ---------------------------------------------------------------------------

def _victim(parent: Path) -> Path:
    """A real project of someone else's."""
    folder = parent / "Victim.qda"
    new_project.write_project(folder, new_project.creation_statements(
        "dave", "x (QualCoder)", "2026-09-26 10:00:00"))
    return folder


def _statements():
    return new_project.creation_statements("carol", "x (QualCoder)",
                                           "2026-09-26 10:00:00")


class TestCleanUpRemovesOnlyItsOwn:
    """The Security gate's swaps: each needs another program to act in
    the moment between the claim and the build; the clean-up then
    removes nothing it did not make."""

    def _link_or_skip(self, link: Path, target: Path):
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            pytest.skip("this system cannot make a symbolic link here")

    def test_the_folder_swapped_for_a_link_before_the_database(
            self, tmp_path, monkeypatch):
        victim = _victim(tmp_path)
        before = (victim / "data.qda").read_bytes()
        folder = tmp_path / "New.qda"
        real = new_project._create_database_file

        def swap_then_create(path):
            folder.rename(tmp_path / "moved.qda")
            self._link_or_skip(folder, victim)
            real(path)

        monkeypatch.setattr(new_project, "_create_database_file",
                            swap_then_create)
        with pytest.raises(new_project.ProjectWriteFailed) as caught:
            new_project.write_project(folder, _statements())
        assert caught.value.leftovers.replaced
        assert (victim / "data.qda").read_bytes() == before
        assert sorted(p.name for p in victim.iterdir()) == [
            "audio", "data.qda", "documents", "images", "video"]

    def test_the_folder_swapped_for_a_link_after_the_claim(
            self, tmp_path, monkeypatch):
        victim = _victim(tmp_path)
        before = _snapshot(victim)
        folder = tmp_path / "New.qda"
        real_mkdir = Path.mkdir

        def mkdir(self, *args, **kwargs):
            real_mkdir(self, *args, **kwargs)
            if self == folder:
                folder.rename(tmp_path / "moved.qda")
                try:
                    folder.symlink_to(victim, target_is_directory=True)
                except OSError:
                    pytest.skip("no symbolic links here")

        monkeypatch.setattr(Path, "mkdir", mkdir)
        with pytest.raises(new_project.ProjectWriteFailed) as caught:
            new_project.write_project(folder, _statements())
        monkeypatch.undo()
        assert caught.value.leftovers.replaced
        assert _snapshot(victim) == before

    def test_another_programs_wal_and_shm_are_kept(self, tmp_path,
                                                   monkeypatch):
        folder = tmp_path / "Side.qda"
        real = new_project._connect

        def drop_then_fail(path):
            (folder / "data.qda-wal").write_bytes(b"theirs")
            (folder / "data.qda-shm").write_bytes(b"theirs too")
            conn = real(path)
            conn.close()
            raise sqlite3.OperationalError("injected")

        monkeypatch.setattr(new_project, "_connect", drop_then_fail)
        with pytest.raises(new_project.ProjectWriteFailed) as caught:
            new_project.write_project(folder, _statements())
        leftovers = caught.value.leftovers
        assert leftovers.kept == ["."] and not leftovers.failed
        assert sorted(p.name for p in folder.iterdir()) == [
            "data.qda-shm", "data.qda-wal"]
        assert (folder / "data.qda-wal").read_bytes() == b"theirs"

    def test_another_programs_database_is_never_opened_or_removed(
            self, tmp_path, monkeypatch):
        work = tmp_path / "work"
        work.mkdir()
        real = new_project._create_database_file

        def drop_first(path):
            path.write_bytes(b"another program's database")
            real(path)

        monkeypatch.setattr(new_project, "_create_database_file",
                            drop_first)
        text = refused(create("Dropped", work))
        assert "Nothing was left behind" not in text
        assert "was kept, because it holds something this tool did " \
               "not make" in text and "delete" not in text
        folder = work / "Dropped.qda"
        assert (folder / "data.qda").read_bytes() == \
            b"another program's database"
        assert [p.name for p in folder.iterdir()] == ["data.qda"]

    def test_a_subfolder_holding_their_file_is_kept_not_failed(
            self, tmp_path, monkeypatch):
        work = tmp_path / "work"
        work.mkdir()
        _fail_with(monkeypatch, sqlite3.OperationalError("x"), 4)
        real = new_project._create_database_file

        def drop_in_documents(path):
            (path.parent / "documents" / "theirs.txt").write_text("keep")
            real(path)

        monkeypatch.setattr(new_project, "_create_database_file",
                            drop_in_documents)
        text = refused(create("Kept", work))
        assert "was kept, because it holds something this tool did " \
               "not make" in text
        assert "could not be removed" not in text and "delete" not in text
        folder = work / "Kept.qda"
        assert sorted(str(p.relative_to(folder))
                      for p in folder.rglob("*")) == [
            "documents", os.path.join("documents", "theirs.txt")]
