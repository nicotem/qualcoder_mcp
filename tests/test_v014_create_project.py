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
