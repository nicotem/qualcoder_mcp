# SPDX-License-Identifier: LGPL-3.0-or-later
"""v0.14, creating a project: the format (brief C, part 1).

A project this server creates must be, in everything QualCoder reads,
the project QualCoder 4.0's own New Project creates (the owner's ruling
of 2026-09-25: 4.0's format only). The oracle is a committed fixture
taken from a project 4.0 created at the verified commit
(`tests/fixtures/qc40_new_project.json`, made by `qc40_format_facts.py`);
the comparison runs on every CI job, so on every platform's SQLite.

The creation is one explicit transaction: a failure injected after any
statement, or a process killed there, leaves nothing committed.
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from qualcoder_mcp import new_project
from qualcoder_mcp.database import QualcoderDatabase
from qc40_format_facts import facts, structure

ORACLE = json.loads(
    (Path(__file__).parent / "fixtures" / "qc40_new_project.json")
    .read_text(encoding="utf-8"))
ABOUT = new_project.about_line("0.14.0")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
SRC = str(Path(__file__).resolve().parent.parent / "src")


def _make(parent: Path, coder: str, name: str = "Study") -> Path:
    folder = parent / f"{name}.qda"
    new_project.write_project(folder, new_project.creation_statements(
        coder, ABOUT, new_project.creation_date()))
    return folder


class TestTheOracleItself:
    """The fixture says what the study measured, so it cannot rot into
    agreeing with whatever the code writes."""

    def test_the_fixture_is_four_point_zero_s_project(self):
        order = ORACLE["structure"]["order"]
        kinds = [kind for kind, _, _ in order]
        assert kinds.count("table") == 28
        assert kinds.count("index") == 12
        assert kinds.count("view") == 4
        assert ORACLE["structure"]["triggers"] == []
        assert ORACLE["rows"]["project"][0][0] == "v17"
        assert ORACLE["rows"]["project"][0][3] == "QualCoder 4.0 Beta"
        assert ORACLE["source"]["commit"] == new_project.FORMAT_COMMIT
        assert ORACLE["subfolders"] == sorted(new_project.SUBFOLDERS)


class TestStructureMatchesFourPointZero:

    @pytest.mark.parametrize("coder", ["carol", ""])
    def test_every_structural_fact_is_identical(self, tmp_path, coder):
        ours = structure(_make(tmp_path, coder))
        for key in ("order", "columns", "foreign_keys", "indexes",
                    "triggers", "journal_mode", "header"):
            assert ours[key] == ORACLE["structure"][key], key

    def test_table_by_table(self, tmp_path):
        """The same comparison, one table at a time, so a difference is
        named by its table rather than by one large inequality."""
        ours = structure(_make(tmp_path, "carol"))["columns"]
        oracle = ORACLE["structure"]["columns"]
        assert list(ours) == list(oracle)
        for table in oracle:
            assert ours[table] == oracle[table], table

    def test_the_views_behave_the_same(self, tmp_path):
        ours = facts(_make(tmp_path, "carol"))["views"]
        assert ours == ORACLE["views"]
        assert ours["check"] == "refused"
        assert "hid" not in ours["code_text"]

    def test_the_four_subfolders_and_nothing_else(self, tmp_path):
        folder = _make(tmp_path, "carol")
        assert sorted(p.name for p in folder.iterdir()) == sorted(
            ORACLE["subfolders"] + ["data.qda"])
        assert all(not any((folder / s).iterdir())
                   for s in new_project.SUBFOLDERS)


class TestFirstRows:

    def test_with_a_coder_name(self, tmp_path):
        rows = facts(_make(tmp_path, "carol"))["rows"]
        oracle = ORACLE["rows"]
        (row,) = rows["project"]
        (expected,) = oracle["project"]
        assert DATE_RE.match(row[1])
        assert row[3] == ABOUT and "QualCoder" in row[3]
        assert row[6] == "carol"
        keep = [0, 2, 4, 5, 7, 8, 9, 10]
        assert [row[i] for i in keep] == [expected[i] for i in keep]
        assert rows["coder_names"] == [
            [1, "carol", 1], [2, new_project.SPEAKER_CODER_NAME, 1]]
        assert [n[1:] for n in oracle["coder_names"]][1] == [
            new_project.SPEAKER_CODER_NAME, 1]
        assert rows["row_counts"] == oracle["row_counts"]

    def test_when_the_coder_name_is_not_known(self, tmp_path):
        rows = facts(_make(tmp_path, ""))["rows"]
        (row,) = rows["project"]
        assert row[6] == ""          # never NULL
        assert rows["coder_names"] == [
            [1, new_project.SPEAKER_CODER_NAME, 1]]
        counts = dict(ORACLE["rows"]["row_counts"], coder_names=1)
        assert rows["row_counts"] == counts

    def test_the_about_line_passes_qualcoders_check(self):
        """QualCoder's open check: 'QualCoder' in about, case-sensitive."""
        assert "QualCoder" in ABOUT
        assert ABOUT == "qualcoder-mcp 0.14.0 (QualCoder schema v17)"
        assert "QualCoder" not in "qualcoder-mcp 0.14.0"

    def test_the_date_is_local_time_to_the_second(self):
        assert DATE_RE.match(new_project.creation_date())

    def test_this_server_opens_it_with_every_capability(self, tmp_path):
        db = QualcoderDatabase(str(_make(tmp_path, "carol")))
        try:
            assert db.db_version == "v17"
            assert db.qualcoder_about_ok
            assert all(db.capabilities.to_dict().values())
            assert db.write_support()[0] is True
            assert db.get_project_info()["coder_name"] == "carol"
        finally:
            db.close()


class _Faulty:
    """A connection that fails after its `after`-th statement (BEGIN not
    counted), or at COMMIT, and otherwise behaves as the real one."""

    def __init__(self, conn, after=None, at_commit=False, before_raise=None):
        self._conn, self._after, self._count = conn, after, 0
        self._at_commit, self._before_raise = at_commit, before_raise

    @property
    def in_transaction(self):
        return self._conn.in_transaction

    def close(self):
        self._conn.close()

    def execute(self, sql, args=()):
        if sql == "COMMIT" and self._at_commit:
            self._fail()
        result = self._conn.execute(sql, args)
        if sql not in ("BEGIN", "COMMIT", "ROLLBACK"):
            self._count += 1
            if self._count == self._after:
                self._fail()
        return result

    def _fail(self):
        if self._before_raise is not None:
            self._before_raise()
        raise sqlite3.OperationalError("injected failure")


def _statements(coder):
    return new_project.creation_statements(coder, ABOUT, "2026-09-26 10:00:00")


class TestOneTransaction:

    @pytest.mark.parametrize("coder", ["carol", ""])
    def test_a_failure_after_any_statement_leaves_nothing(
            self, tmp_path, monkeypatch, coder):
        work = tmp_path / "work"
        work.mkdir()
        real = new_project._connect
        total = len(_statements(coder))
        assert total == (35 if coder else 34)
        for after in range(1, total + 1):
            monkeypatch.setattr(new_project, "_connect",
                                lambda p, a=after: _Faulty(real(p), a))
            folder = work / f"f{after}.qda"
            with pytest.raises(new_project.ProjectWriteFailed) as caught:
                new_project.write_project(folder, _statements(coder))
            assert caught.value.stage == "database"
            assert caught.value.leftovers.nothing_left
            assert not os.path.lexists(folder), after
        assert list(work.iterdir()) == []

    def test_a_failure_at_commit_leaves_nothing(self, tmp_path, monkeypatch):
        work = tmp_path / "work"
        work.mkdir()
        real = new_project._connect
        monkeypatch.setattr(new_project, "_connect",
                            lambda p: _Faulty(real(p), at_commit=True))
        with pytest.raises(new_project.ProjectWriteFailed):
            new_project.write_project(work / "c.qda", _statements("x"))
        assert list(work.iterdir()) == []

    def test_a_failure_after_each_subfolder_leaves_nothing(
            self, tmp_path, monkeypatch):
        real_mkdir = Path.mkdir
        for fail_at in range(2, len(new_project.SUBFOLDERS) + 2):
            calls = []

            def mkdir(self, *args, **kwargs):
                calls.append(self)
                if len(calls) == fail_at:
                    raise OSError(28, "No space left on device")
                return real_mkdir(self, *args, **kwargs)

            monkeypatch.setattr(Path, "mkdir", mkdir)
            folder = tmp_path / f"s{fail_at}.qda"
            with pytest.raises(new_project.ProjectWriteFailed) as caught:
                new_project.write_project(folder, _statements("x"))
            monkeypatch.setattr(Path, "mkdir", real_mkdir)
            assert caught.value.stage == "subfolder"
            assert caught.value.leftovers.nothing_left
            assert not folder.exists()

    def test_clean_up_keeps_what_another_program_put_there(
            self, tmp_path, monkeypatch):
        """Removal is by name, never recursive: a file dropped into the
        new folder during creation survives, and the folder is reported
        as left rather than deleted with it."""
        folder = tmp_path / "shared.qda"
        real = new_project._connect

        def drop():
            (folder / "someone_elses.txt").write_text("keep me")

        monkeypatch.setattr(new_project, "_connect",
                            lambda p: _Faulty(real(p), 3, before_raise=drop))
        with pytest.raises(new_project.ProjectWriteFailed) as caught:
            new_project.write_project(folder, _statements("x"))
        leftovers = caught.value.leftovers
        assert leftovers.kept == ["."] and not leftovers.failed
        assert not leftovers.replaced
        assert (folder / "someone_elses.txt").read_text() == "keep me"
        assert sorted(p.name for p in folder.iterdir()) == [
            "someone_elses.txt"]

    def test_an_existing_name_is_never_touched(self, tmp_path):
        for kind in ("folder", "file", "link"):
            target = tmp_path / f"{kind}.qda"
            if kind == "folder":
                target.mkdir()
                (target / "data.qda").write_bytes(b"theirs")
            elif kind == "file":
                target.write_bytes(b"theirs")
            else:
                target.symlink_to(tmp_path / "nowhere")
            with pytest.raises(FileExistsError):
                new_project.write_project(target, _statements("x"))
        assert (tmp_path / "folder.qda" / "data.qda").read_bytes() == b"theirs"
        assert (tmp_path / "file.qda").read_bytes() == b"theirs"
        assert os.path.islink(tmp_path / "link.qda")


_KILL_SCRIPT = textwrap.dedent("""
    import os, sys
    sys.path.insert(0, {src!r})
    from qualcoder_mcp import new_project as n
    target, coder, after = sys.argv[1], sys.argv[2], int(sys.argv[3])
    real = n._connect

    class Kill:
        def __init__(self, conn):
            self.conn, self.count = conn, 0
        in_transaction = property(lambda self: self.conn.in_transaction)
        def close(self):
            self.conn.close()
        def execute(self, sql, args=()):
            result = self.conn.execute(sql, args)
            if sql not in ("BEGIN", "COMMIT"):
                self.count += 1
                if self.count == after:
                    os._exit(9)       # no rollback, no close
            return result

    n._connect = lambda p: Kill(real(p))
    n.write_project(target, n.creation_statements(
        coder, "about QualCoder", "2026-09-26 10:00:00"))
""")


class TestAKilledProcessCommitsNothing:
    """A process killed after a statement, before COMMIT: the folder,
    an empty `data.qda` and its journal remain, and no table is
    committed (a fresh reader rolls the journal back and sees none)."""

    @pytest.mark.parametrize("coder,after", [
        ("carol", 1), ("carol", 12), ("carol", 28), ("carol", 30),
        ("carol", 35), ("", 1), ("", 29), ("", 34)])
    def test_no_table_survives(self, tmp_path, coder, after):
        script = tmp_path / "kill.py"
        script.write_text(_KILL_SCRIPT.format(src=SRC), encoding="utf-8")
        target = tmp_path / "killed.qda"
        proc = subprocess.run(
            [sys.executable, str(script), str(target), coder, str(after)],
            capture_output=True, timeout=120)
        assert proc.returncode == 9, proc.stderr
        db = target / "data.qda"
        assert db.stat().st_size == 0
        assert (target / "data.qda-journal").exists()
        conn = sqlite3.connect(str(db))
        try:
            objects = conn.execute(
                "SELECT count(*) FROM sqlite_master").fetchone()[0]
        finally:
            conn.close()
        assert objects == 0
