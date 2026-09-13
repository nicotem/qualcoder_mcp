"""Guards on the suite itself: where it may write, and what it compiles.

Fix round 1 (QA round 1, F1 and F19). Two defects that a green suite
cannot see: a test whose sandbox redirects nothing and therefore writes
into the researcher's own folders, and a compile-time warning that only
appears on the first import of a fresh checkout. Both get a pin here.
"""

import ast
import os
import pathlib
import shutil
import subprocess
import sys
import warnings

import pytest

import track5_helpers as H
from qualcoder_mcp import database

PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "src" / "qualcoder_mcp"


class TestNothingIsWrittenOutsideTheSandbox:
    """The workspace half of the isolation (QA round 1, F1).

    `copy_project_to_workspace`'s `workspace` is not a tool argument, so
    every tool-level call falls through to `database.DEFAULT_WORKSPACE`,
    which is computed from `Path.home()` at import time. The autouse
    `_isolate_workspace` fixture redirects that object; the session-wide
    guard beside it fails the run if anything reaches the real folder
    anyway.
    """

    def test_the_workspace_constant_is_redirected_into_tmp_path(
            self, tmp_path):
        assert pathlib.Path(database.DEFAULT_WORKSPACE).is_relative_to(
            tmp_path)
        assert not pathlib.Path(
            database.DEFAULT_WORKSPACE).is_relative_to(H.REAL_WORKSPACE)

    def test_a_copy_lands_in_the_sandbox_not_in_the_real_workspace(
            self, qualcoder_db_path):
        copied = database.copy_project_to_workspace(qualcoder_db_path)
        assert copied.exists()
        assert pathlib.Path(copied).is_relative_to(
            database.DEFAULT_WORKSPACE)
        assert not pathlib.Path(copied).is_relative_to(H.REAL_WORKSPACE)

    def test_the_guard_sees_an_entry_that_appears_under_the_real_path(
            self, tmp_path):
        """The guard itself, driven against a stand-in workspace.

        Without this the session fixture could stop reporting (a swapped
        comparison, a swallowed exception) and nothing would notice until
        another hundred project copies had accumulated.
        """
        stand_in = tmp_path / "pretend workspace"
        stand_in.mkdir()
        before = H.real_workspace_entries(stand_in)
        assert before == set()
        (stand_in / "test_project_20260913_133949.qda").mkdir()
        after = H.real_workspace_entries(stand_in)
        assert after - before == {"test_project_20260913_133949.qda"}

    def test_the_guard_reports_a_missing_folder_as_missing(self, tmp_path):
        """A folder that is not there is not an empty folder: creating it
        is itself a write into the researcher's Documents."""
        assert H.real_workspace_entries(tmp_path / "absent") is None

    def test_the_guard_does_not_invent_an_empty_set_when_it_cannot_read(
            self, tmp_path, monkeypatch):
        def _boom(*args, **kwargs):
            raise PermissionError("no")

        # Patched where the walk actually reads: the snapshot is
        # recursive now, because the state home's leak is a session file
        # one directory down and a top-level listing cannot see it.
        monkeypatch.setattr(pathlib.Path, "rglob", _boom)
        assert (H.real_workspace_entries(tmp_path)
                is H.WORKSPACE_UNREADABLE)

    def test_the_snapshot_reaches_below_the_top_level(self, tmp_path):
        """Otherwise widening the guard to the state directory would
        watch a folder whose only interesting contents it cannot see."""
        root = tmp_path / "state"
        (root / "sessions").mkdir(parents=True)
        (root / "sessions" / "session_x.json").write_text("{}",
                                                          encoding="utf-8")
        assert H.real_workspace_entries(root) == {
            "sessions", "sessions/session_x.json"}

    def test_the_baseline_was_taken_before_any_test_module_was_imported(
            self):
        """G7: a session-scoped fixture body runs at the setup of the
        FIRST test, by which time every module has been imported, so an
        import-time write would sit inside `before` and never be
        reported. The baseline is taken in `pytest_sessionstart`, and
        this is the proof of the ordering rather than a claim about it:
        the hook records whether any test module was already in
        sys.modules when it ran."""
        here = str(pathlib.Path(__file__).resolve().parent / "conftest.py")
        loaded = [m for m in list(sys.modules.values())
                  if getattr(m, "__file__", None) == here]
        assert loaded, "the root conftest is not in sys.modules"
        for conftest in loaded:
            assert conftest.BASELINE_PRECEDED_TEST_IMPORTS is True
            assert set(conftest.WORKSPACE_BASELINE) == \
                set(H.GUARDED_REAL_FOLDERS)

    def test_a_test_cannot_undo_the_sandbox(self, monkeypatch, tmp_path):
        """The sandbox holds its own MonkeyPatch.

        Two race tests call `monkeypatch.undo()` part way through to
        stop a fault injection. On the shared instance that undid every
        isolation fixture as well, and the rest of those tests ran
        against the researcher's real MRU file, preview secret,
        workspace and session directory.
        """
        monkeypatch.setattr(pathlib.Path, "cwd", lambda: tmp_path)
        monkeypatch.undo()
        assert pathlib.Path(database.DEFAULT_WORKSPACE).is_relative_to(
            tmp_path.parent)
        assert not pathlib.Path(
            database.DEFAULT_WORKSPACE).is_relative_to(H.REAL_WORKSPACE)
        import qualcoder_mcp.server as _server
        assert not pathlib.Path(
            _server.session_manager.storage_dir).is_relative_to(
                H.REAL_STATE_HOME)
        assert not pathlib.Path(_server._MRU_FILE).is_relative_to(
            H.REAL_STATE_HOME)


class TestNoTestBindsTheResearchersOwnFolders:
    """No test opens a project inside the researcher's real Documents.

    Two modules used to compute `Path.home() / "Documents" / "QDA
    Projects" / "test_project.qda"` at import and skip when it was
    absent. That was 44 of the suite's 46 skips: 44 tests that ran for
    one person and for nobody else, on no CI job and on no platform. It
    showed. When they were pointed at the tmp_path fixture instead they
    failed immediately on two things nobody had noticed: a dataclass
    field renamed long ago (`ai_memo`, now `reasoning`, at five call
    sites) and spans running past the end of the file, which the REFI
    export validator refuses. A third failure was counted with those two
    when this was first written and should not have been: the module
    constant four test bodies still referenced had just been deleted by
    the same change, so it was that change's own fallout rather than rot
    the skips were hiding. And on a machine where such a project DOES
    exist, those tests read the researcher's live data instead.
    """

    TESTS = pathlib.Path(__file__).resolve().parent

    # Read as syntax, not as text: this very module has to be able to
    # DESCRIBE the pattern it forbids, in a docstring and in the sample
    # below, without reporting itself.
    @staticmethod
    def _home_folder_paths(source):
        """Line numbers where code joins `Path.home()` to a named folder."""
        hits = []
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.BinOp) or \
                    not isinstance(node.op, ast.Div):
                continue
            dumped = ast.dump(node)
            if "attr='home'" in dumped and "'Documents'" in dumped:
                hits.append(node.lineno)
        return sorted(set(hits))

    @staticmethod
    def _skip_reasons(source):
        """Every string handed to a `pytest.skip(...)` call."""
        reasons = []
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name != "skip":
                continue
            for argument in node.args:
                for inner in ast.walk(argument):
                    if isinstance(inner, ast.Constant) and \
                            isinstance(inner.value, str):
                        reasons.append(inner.value)
        return reasons

    def test_no_module_builds_a_path_into_the_real_documents_folder(self):
        offenders = []
        for path in sorted(self.TESTS.glob("*.py")):
            for line in self._home_folder_paths(
                    path.read_text(encoding="utf-8")):
                offenders.append(f"{path.name}:{line}")
        assert offenders == [], offenders

    def test_there_are_modules_to_sweep(self):
        assert len(list(self.TESTS.glob("*.py"))) >= 20

    def test_the_sweep_would_notice(self):
        sample = ('TEST_PROJECT_PATH = Path.home() / "Documents" / '
                  '"QDA Projects" / "test_project.qda"')
        assert self._home_folder_paths(sample) == [1]
        assert self._home_folder_paths(
            "REAL_WORKSPACE = Path(_database.DEFAULT_WORKSPACE)") == []

    def test_nothing_skips_for_a_missing_personal_project(self):
        """The reason string those 44 skips carried."""
        offenders = []
        for path in sorted(self.TESTS.glob("*.py")):
            for reason in self._skip_reasons(
                    path.read_text(encoding="utf-8")):
                if "Test project not found" in reason:
                    offenders.append(f"{path.name}: {reason}")
        assert offenders == [], offenders

    def test_that_sweep_would_notice_too(self):
        assert self._skip_reasons(
            'pytest.skip(f"Test project not found at {P}")') == [
                "Test project not found at "]
        assert self._skip_reasons("x = 1") == []


class TestTheSourceCompilesWithoutWarnings:
    r"""Every shipped module compiles silently (QA round 1, F19).

    `\_` in a plain docstring is an invalid escape sequence: a
    SyntaxWarning on 3.12 and 3.13, printed above the version string on
    the very sanity check INSTALL tells a user to run; a
    DeprecationWarning on 3.10 and 3.11; and under `-W error` an import
    failure, which collects zero tests. It is emitted at COMPILE time,
    so a warm __pycache__ hides it and a rerun does not reproduce it,
    which is what made it look like a flake. These pins compile and
    import the source itself, so a warm cache cannot hide the next one.

    Scoped to our own package deliberately: a dependency's
    DeprecationWarning is not ours to fix, and pinning the whole run
    warning-free would pin third-party release notes into this suite.
    """

    @staticmethod
    def _modules():
        return sorted(PACKAGE.rglob("*.py"))

    def test_there_are_modules_to_check(self):
        # Otherwise an empty glob would make the sweep below pass
        # whatever the source says.
        assert len(self._modules()) >= 5

    def test_no_module_compiles_with_a_warning(self):
        offenders = []
        for path in self._modules():
            source = path.read_text(encoding="utf-8")
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                compile(source, str(path), "exec")
            offenders.extend(
                f"{path.name}: {w.category.__name__}: {w.message}"
                for w in caught)
        assert offenders == [], offenders

    def test_a_cold_import_is_silent_under_warnings_as_errors(self,
                                                             tmp_path):
        """The failure mode F19 describes, reproduced end to end.

        A separate interpreter with an empty bytecode cache, so every
        module is compiled from source, and with warnings from this
        package raised as errors: that is what a fresh checkout, a clone,
        a CI runner and `pip install` all do on first import. In process
        this cannot be tested, because re-importing the package rebinds
        the classes the rest of the suite is holding.
        """
        script = (
            "import importlib, pkgutil, warnings\n"
            # Third-party deprecations are not ours to fix, so start from
            # ignore and raise only what is: anything our own modules
            # emit, every SyntaxWarning (3.12 and 3.13's class for an
            # invalid escape), and the same defect's 3.10/3.11 spelling,
            # which arrives as a DeprecationWarning the import machinery
            # owns rather than our module.
            "warnings.simplefilter('ignore')\n"
            "warnings.filterwarnings('error', module=r'qualcoder_mcp.*')\n"
            "warnings.filterwarnings('error', category=SyntaxWarning)\n"
            "warnings.filterwarnings('error', "
            "message='invalid escape sequence')\n"
            "import qualcoder_mcp\n"
            "for info in pkgutil.iter_modules(qualcoder_mcp.__path__):\n"
            "    importlib.import_module('qualcoder_mcp.' + info.name)\n"
        )
        env = dict(os.environ)
        env["PYTHONPYCACHEPREFIX"] = str(tmp_path / "pycache")
        env["PYTHONPATH"] = str(PACKAGE.parents[1] / "src")
        done = subprocess.run([sys.executable, "-c", script], env=env,
                              capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        assert done.stderr == "", done.stderr


NOT_ON_WINDOWS = pytest.mark.skipif(
    sys.platform == "win32",
    reason="the emulation stands aside on Windows, where the operating "
           "system enforces the rule itself")


@NOT_ON_WINDOWS
class TestWindowsSharingSemanticsAreEmulated:
    """The `_windows_sharing_semantics` guard, driven against itself.

    The guard exists because a whole class of defect is invisible on the
    machines this suite is written on: Windows refuses to rename or
    remove a directory holding an OPEN file and POSIX allows it, so a
    test that moves a project while the server still has its connection
    is green on ubuntu and macOS and fails on BOTH Windows jobs. That is
    exactly the failure this round diagnosed. A guard nobody drives can
    stop reporting without anyone noticing, so these pin both answers:
    it fires when a handle is open, and it stands aside when none is.
    """

    def test_a_directory_holding_an_open_file_cannot_be_renamed(
            self, tmp_path):
        folder = tmp_path / "project.qda"
        folder.mkdir()
        handle = open(folder / "data.qda", "wb")
        try:
            with pytest.raises(PermissionError) as caught:
                folder.rename(tmp_path / "moved.qda")
            assert H.SHARING_VIOLATION in str(caught.value)
            assert "data.qda" in str(caught.value)
        finally:
            handle.close()

    def test_the_same_rename_succeeds_once_the_handle_is_closed(
            self, tmp_path):
        """The other half: the guard must not refuse everything."""
        folder = tmp_path / "project.qda"
        folder.mkdir()
        with open(folder / "data.qda", "wb") as handle:
            handle.write(b"x")
        moved = tmp_path / "moved.qda"
        folder.rename(moved)
        assert moved.is_dir()
        assert (moved / "data.qda").exists()

    def test_an_open_file_cannot_be_replaced_in_place(self, tmp_path):
        target = tmp_path / "settings.json"
        target.write_text("{}", encoding="utf-8")
        source = tmp_path / "settings.json.tmp"
        source.write_text("{}", encoding="utf-8")
        handle = open(target, "rb")
        try:
            with pytest.raises(PermissionError):
                os.replace(str(source), str(target))
        finally:
            handle.close()

    def test_rmtree_refuses_but_ignore_errors_still_does_not(self, tmp_path):
        """`ignore_errors=True` does not raise on Windows either, so the
        emulation must not raise where Windows would stay silent."""
        folder = tmp_path / "tree"
        folder.mkdir()
        handle = open(folder / "held.bin", "wb")
        try:
            with pytest.raises(PermissionError):
                shutil.rmtree(folder)
            shutil.rmtree(folder, ignore_errors=True)
        finally:
            handle.close()

    def test_the_detector_reports_nothing_when_nothing_is_open(
            self, tmp_path):
        """Otherwise every assertion above could pass for the wrong
        reason, with the detector simply answering "held" always."""
        folder = tmp_path / "quiet"
        folder.mkdir()
        (folder / "a.bin").write_bytes(b"a")
        assert H.open_paths_under(folder) == []
        with open(folder / "a.bin", "rb"):
            assert H.open_paths_under(folder) == [str(folder / "a.bin")]


class TestTheSuiteLeavesNoTemporaryDirectoryBehind:
    """What a run leaves in the system temporary directory.

    `tests/test_scale_media.py` evaluated `tempfile.mkdtemp` at module
    IMPORT and never removed the result, so every run of the suite left
    one directory behind whether or not a test in that module ran; 402
    of them had accumulated on the machine this was found on. It is
    pre-existing rather than a defect of this batch, and it is also the
    import-time filesystem work that made the guard's baseline move to
    `pytest_sessionstart`.
    """

    SCRIPT = (
        "import sys\n"
        "sys.path.insert(0, {tests!r})\n"
        "sys.path.insert(0, {src!r})\n"
        "import test_scale_media as module\n"
        "print('AFTER_IMPORT', module._GEN_DIR)\n"
        "print('IN_USE', module._gen_dir())\n"
    )

    def _run(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        script = self.SCRIPT.format(tests=str(root / "tests"),
                                    src=str(root / "src"))
        done = subprocess.run([sys.executable, "-c", script],
                              capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        lines = dict(line.split(" ", 1) for line in
                     done.stdout.strip().splitlines() if " " in line)
        return lines

    def test_importing_the_module_creates_nothing(self):
        assert self._run()["AFTER_IMPORT"] == "None"

    def test_what_it_does_create_does_not_outlive_the_interpreter(self):
        created = pathlib.Path(self._run()["IN_USE"])
        assert created.name.startswith("qc_scale_")
        assert not created.exists(), (
            f"{created} survived the interpreter that made it")


class TestNoConnectionIsOpenedWithNothingToCloseIt:
    """The unclosed-connection class, pinned by syntax rather than by luck.

    The shape: `sqlite3.connect(...)` whose result is never bound to a
    name, so no `close()` and no `with` can ever reach it, and the
    handle stays open until the garbage collector happens to take it.
    Five of them were in this suite. Four were found by running the
    whole suite on Python 3.13 under
    `-W error::pytest.PytestUnraisableExceptionWarning`, which is the
    only interpreter in the matrix whose sqlite3 emits
    `ResourceWarning: unclosed database` at finalisation, and fix round 2
    concluded from that hunt that the next instance would have to be
    found the same way, by running under the flag until it fired.

    That conclusion was wrong and this class is the correction. The
    runtime hunt needs an interpreter that emits the warning, a run long
    enough for a collection to happen (a single-module run stays green
    over a real leak), and it names a different test each time, because
    the warning lands on whoever is running when the collector next
    runs. Reading the syntax needs none of those: it costs milliseconds,
    it behaves identically on every platform and every interpreter, and
    it names the line. Run against the fix-round-1 tip `cda4eeb` it
    returns exactly the five sites that were there, and against this
    tree it returns none.

    The honest limit, because a sweep that overclaims is worse than no
    sweep: this reads the CALL shape, so it sees a connection nothing
    could close, and it does not see a connection bound to a name and
    then dropped without `close()`, nor one opened through an alias for
    the `sqlite3` module. Neither exists in this tree, and both are
    shapes where a reader can at least see the handle. The complementary
    runtime pin is in tests/conftest.py: nothing may be left OPEN at the
    end of the run.
    """

    ROOTS = (pathlib.Path(__file__).resolve().parent, PACKAGE)

    @staticmethod
    def _is_connect(node):
        return (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "connect"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "sqlite3")

    @classmethod
    def _unclosable(cls, source):
        """Line numbers where a connection is opened with nothing that
        could ever close it: chained straight into a method call, or
        evaluated as a statement and dropped."""
        hits = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Attribute) and cls._is_connect(node.value):
                hits.append(node.lineno)
            elif isinstance(node, ast.Expr) and cls._is_connect(node.value):
                hits.append(node.lineno)
        return sorted(set(hits))

    def test_nothing_opens_a_connection_it_cannot_close(self):
        offenders = []
        for root in self.ROOTS:
            for path in sorted(root.rglob("*.py")):
                for line in self._unclosable(
                        path.read_text(encoding="utf-8")):
                    offenders.append(f"{path.name}:{line}")
        assert offenders == [], offenders

    def test_there_are_modules_to_sweep(self):
        assert sum(len(list(root.rglob("*.py"))) for root in self.ROOTS) >= 20

    def test_the_sweep_would_notice(self):
        """Driven against a known-bad and a known-good sample, as the
        other sweeps here are: the bad one is the fifth instance,
        verbatim from tests/test_qa_v08_d1d2_attack.py before it was
        closed; the good ones are how the same work is written now."""
        bad = ('b = [l for l in list(sqlite3.connect(str(twin / "data.qda"))'
               '.iterdump()) if "code_cat" in l]')
        assert self._unclosable(bad) == [1]
        assert self._unclosable(
            "sqlite3.connect(str(p)).execute('PRAGMA writable_schema = ON')"
        ) == [1]

        good = ("with closing(sqlite3.connect(str(p))) as conn:\n"
                "    lines = list(conn.iterdump())\n")
        assert self._unclosable(good) == []
        assert self._unclosable(
            "conn = sqlite3.connect(str(p))\nconn.close()\n") == []


class TestTheSourceDistributionCarriesNoSuite:
    """The sdist shipped `tests/test_*.py` without `tests/conftest.py`
    or `tests/track5_helpers.py`, which every one of those modules
    needs: conftest holds the sandbox fixtures that keep a run out of
    ~/.qualcoder_mcp and the real workspace, track5_helpers the project
    builders and the REAL_WORKSPACE guards. A packager who downloaded it
    to verify a release got a collection error, and anyone who then
    reconstructed a runner had the test bodies WITHOUT the isolation.

    Pinned by reading the manifest rather than by building, so it runs
    everywhere in milliseconds; the build itself was run by hand in fix
    round 4, before (61 test entries, no conftest) and after (none).
    """

    REPO = pathlib.Path(__file__).resolve().parents[1]

    def test_the_manifest_prunes_the_tests(self):
        manifest = self.REPO / "MANIFEST.in"
        assert manifest.exists(), "MANIFEST.in is what keeps them out"
        directives = [line.split("#", 1)[0].strip()
                      for line in manifest.read_text(encoding="utf-8")
                      .splitlines()]
        assert "prune tests" in directives, directives

    def test_the_tests_still_need_the_files_the_sdist_omitted(self):
        """The reason the half-suite is useless, as a fact rather than a
        claim: if these ever stop being needed, this pin is wrong and
        should be revisited rather than deleted."""
        tests_dir = self.REPO / "tests"
        assert (tests_dir / "conftest.py").exists()
        assert (tests_dir / "track5_helpers.py").exists()
        users = [p.name for p in tests_dir.glob("test_*.py")
                 if "track5_helpers" in p.read_text(encoding="utf-8")]
        assert len(users) >= 10, users

    def test_no_package_data_puts_them_back(self):
        """A graft or a package-data glob added later would undo this
        quietly, so the other half of the shape is pinned too."""
        text = (self.REPO / "pyproject.toml").read_text(encoding="utf-8")
        assert "graft tests" not in text
        manifest = (self.REPO / "MANIFEST.in").read_text(encoding="utf-8")
        assert "graft tests" not in manifest
        assert "recursive-include tests" not in manifest
