# SPDX-License-Identifier: LGPL-3.0-or-later
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
    every tool-level call falls through to `database.default_workspace()`,
    resolved from the home directory at call time. The autouse
    `_isolate_home` fixture moves the home into tmp_path; the
    session-wide guard beside it fails the run if anything reaches the
    real folder anyway.
    """

    def test_the_default_workspace_is_redirected_into_tmp_path(
            self, tmp_path):
        resolved = database.default_workspace().resolve()
        assert resolved.is_relative_to(tmp_path.resolve())
        assert not resolved.is_relative_to(H.REAL_WORKSPACE)

    def test_a_copy_lands_in_the_sandbox_not_in_the_real_workspace(
            self, qualcoder_db_path):
        copied = database.copy_project_to_workspace(qualcoder_db_path)
        assert copied.exists()
        assert pathlib.Path(copied).resolve().is_relative_to(
            database.default_workspace().resolve())
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
        resolved = database.default_workspace().resolve()
        assert resolved.is_relative_to(tmp_path.resolve())
        assert not resolved.is_relative_to(H.REAL_WORKSPACE)
        import qualcoder_mcp.server as _server
        assert not pathlib.Path(
            _server.session_manager.storage_dir).is_relative_to(
                H.REAL_STATE_HOME)
        assert not pathlib.Path(_server._MRU_FILE).is_relative_to(
            H.REAL_STATE_HOME)


class TestTheDefaultWorkspaceIsResolvedWhenAsked:
    """Release preparation for 0.12, carried defect (a).

    `DEFAULT_WORKSPACE` was a module constant computed from `Path.home()`
    at import, so a test that redirected HOME afterwards moved nothing and
    `copy_project_to_workspace` wrote into the researcher's own
    `~/Documents/Qualcoder MCP Projects`: 93 `test_project_<timestamp>.qda`
    folders had accumulated there by 2026-09-14. The autouse fixture that
    patched the constant closed the leak for the suite; this round makes
    the binding itself late (`database.default_workspace()`), so a
    redirected home is honoured by the code and not only by a fixture
    that knows the constant's name, and pins the three facts that make
    the sandbox real.

    Every comparison is against the SANDBOX, resolved, never against the
    home directory: on GitHub's windows-latest runners `%TEMP%` sits under
    the user profile, so a path can be inside tmp_path AND inside the home
    at once, and "not under the home" would report the redirection it was
    handed (fix round 4, W1, in test_v012_session_id_removed.py).
    """

    def test_the_default_follows_the_home_directory_at_call_time(
            self, tmp_path, monkeypatch):
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.setenv("HOME", str(elsewhere))
        monkeypatch.setenv("USERPROFILE", str(elsewhere))
        expected = elsewhere / "Documents" / "Qualcoder MCP Projects"
        assert database.default_workspace().resolve() == expected.resolve()
        # And it follows the environment back: an answer cached on the
        # first call would fail here, as the import-time constant did.
        monkeypatch.undo()
        sandbox_home = tmp_path / "qc_sandbox_home" / "Documents" / \
            "Qualcoder MCP Projects"
        assert database.default_workspace().resolve() == \
            sandbox_home.resolve()

    def test_a_copy_with_no_workspace_argument_lands_inside_the_sandbox(
            self, setup_server, qualcoder_db_path, tmp_path):
        sandbox = tmp_path.resolve()
        copied = database.copy_project_to_workspace(qualcoder_db_path)
        assert pathlib.Path(copied).resolve().is_relative_to(sandbox)
        assert pathlib.Path(copied).resolve().is_relative_to(
            database.default_workspace().resolve())
        # Through the registered tool as well, which is the route the
        # suite's whole-registry sweeps take with production defaults.
        import json
        import qualcoder_mcp.server as _server
        out = json.loads(_server.copy_project_to_workspace(qualcoder_db_path))
        assert out["success"] is True
        assert pathlib.Path(out["workspace_copy"]).resolve().is_relative_to(
            sandbox)

    def test_every_home_derived_binding_resolves_inside_the_sandbox(
            self, tmp_path):
        """Nothing the suite could write through lies outside tmp_path.

        The file-system guard in conftest watches the two REAL folders
        for entries; this is the other half, on the bindings themselves,
        so a constant redirected somewhere that is neither the sandbox
        nor the real folder is reported too.
        """
        import qualcoder_mcp.server as _server
        from qualcoder_mcp import preview_tokens
        sandbox = tmp_path.resolve()
        bindings = {
            "Path.home()": pathlib.Path.home(),
            "database.default_workspace()": database.default_workspace(),
            "server._MRU_FILE": _server._MRU_FILE,
            "preview_tokens.STATE_HOME": preview_tokens.STATE_HOME,
            "preview_tokens.state_home()": preview_tokens.state_home(),
            "server.session_manager.storage_dir":
                _server.session_manager.storage_dir,
        }
        assert len(bindings) >= 6          # the walk is not empty
        outside = {name: str(path) for name, path in bindings.items()
                   if not pathlib.Path(path).resolve().is_relative_to(
                       sandbox)}
        assert outside == {}, outside


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
            "REAL_WORKSPACE = Path(_database.default_workspace())") == []

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

    def test_an_open_file_cannot_be_unlinked(self, tmp_path):
        """The operation the guard did not cover until fix round 5.

        POSIX unlinks an open file and lets the last descriptor close it
        later; Windows refuses while any handle is open. Every cleanup in
        this package deletes a temp file it has just written, so a
        descriptor that escaped its `with` turns into litter there and
        into nothing at all here."""
        target = tmp_path / "held.tmp"
        target.write_bytes(b"x")
        handle = open(target, "rb")
        try:
            with pytest.raises(PermissionError) as caught:
                os.unlink(str(target))
            assert H.SHARING_VIOLATION in str(caught.value)
            with pytest.raises(PermissionError):
                os.remove(str(target))
            with pytest.raises(PermissionError):
                target.unlink()
        finally:
            handle.close()

    def test_the_same_unlink_succeeds_once_the_handle_is_closed(
            self, tmp_path):
        """The other half, so the guard cannot pass by refusing
        everything: the ordinary delete is untouched."""
        target = tmp_path / "released.tmp"
        with open(target, "wb") as handle:
            handle.write(b"x")
        target.unlink()
        assert not target.exists()
        second = tmp_path / "second.tmp"
        second.write_bytes(b"x")
        os.unlink(str(second))
        assert not second.exists()
        third = tmp_path / "third.tmp"
        third.write_bytes(b"x")
        os.remove(str(third))
        assert not third.exists()

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

    `tests/test_transport.py` had the very same shape (`RUN_DIR`, 1.3 MB
    a run, 938 of them by the v0.12 release review), and the two
    subprocess checks here could not see it because they named one
    module. So the shape is now pinned by syntax over every test module:
    a `tempfile` factory called anywhere a function body does not
    enclose it runs at import, and nothing that runs at import is undone
    by a fixture. The subprocess checks stay, one per module that has
    had the defect, because they prove what the sweep cannot: that the
    replacement creates nothing at import and that what it does create
    does not outlive the interpreter.

    The honest limit of the sweep: it reads the call shape, so it sees
    `tempfile.mkdtemp(...)` (or the name imported from `tempfile`, or
    the module under an alias) outside every function body, decorators
    and default argument values included, because those run at
    definition time; the body of an `if __name__ == "__main__":` guard
    is the one module-level block that does not run at import and is
    the one it skips. It does not see a factory reached through a helper
    of another name called at import, and it does not see a directory
    made INSIDE a function and never removed; that second class belongs
    to the session-wide guards in tests/conftest.py, not here.
    """

    MODULES = {
        "test_scale_media": ("_GEN_DIR", "_gen_dir", "qc_scale_"),
        "test_transport": ("_RUN_DIR", "_run_dir", "qc_transport_"),
    }

    SCRIPT = (
        "import sys\n"
        "sys.path.insert(0, {tests!r})\n"
        "sys.path.insert(0, {src!r})\n"
        "import {module} as module\n"
        "print('AFTER_IMPORT', module.{holder})\n"
        "print('IN_USE', module.{maker}())\n"
    )

    def _run(self, module):
        holder, maker, _ = self.MODULES[module]
        root = pathlib.Path(__file__).resolve().parents[1]
        script = self.SCRIPT.format(tests=str(root / "tests"),
                                    src=str(root / "src"), module=module,
                                    holder=holder, maker=maker)
        done = subprocess.run([sys.executable, "-c", script],
                              capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        lines = dict(line.split(" ", 1) for line in
                     done.stdout.strip().splitlines() if " " in line)
        return lines

    @pytest.mark.parametrize("module", sorted(MODULES))
    def test_importing_the_module_creates_nothing(self, module):
        assert self._run(module)["AFTER_IMPORT"] == "None"

    @pytest.mark.parametrize("module", sorted(MODULES))
    def test_what_it_does_create_does_not_outlive_the_interpreter(
            self, module):
        created = pathlib.Path(self._run(module)["IN_USE"])
        assert created.name.startswith(self.MODULES[module][2])
        assert not created.exists(), (
            f"{created} survived the interpreter that made it")

    # The `tempfile` callables that make a file or a directory the moment
    # they run. `mktemp` only names one and is not in the set.
    FACTORIES = frozenset({"mkdtemp", "mkstemp", "TemporaryDirectory",
                           "NamedTemporaryFile", "TemporaryFile",
                           "SpooledTemporaryFile"})
    TESTS = pathlib.Path(__file__).resolve().parent

    @classmethod
    def _names_bound_to_factories(cls, tree):
        """How a module can spell a factory: `tempfile.X` under the
        module's own name or an alias, and X itself when imported."""
        modules, bare = {"tempfile"}, set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "tempfile":
                        modules.add(alias.asname or "tempfile")
            elif isinstance(node, ast.ImportFrom) \
                    and node.module == "tempfile":
                for alias in node.names:
                    if alias.name in cls.FACTORIES:
                        bare.add(alias.asname or alias.name)
        return modules, bare

    @staticmethod
    def _is_main_guard(node):
        """`if __name__ == "__main__":` and nothing looser."""
        if not isinstance(node, ast.If) \
                or not isinstance(node.test, ast.Compare):
            return False
        test = node.test
        return (isinstance(test.left, ast.Name)
                and test.left.id == "__name__"
                and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq)
                and len(test.comparators) == 1
                and isinstance(test.comparators[0], ast.Constant)
                and test.comparators[0].value == "__main__")

    @classmethod
    def _at_import(cls, source):
        """Line numbers of `tempfile` factory calls the interpreter
        evaluates when it imports the module: everything a function body
        does not enclose, class bodies, module-level `if`, `try` and
        `with`, decorators and default argument values included; the
        body of a `__main__` guard excluded."""
        tree = ast.parse(source)
        modules, bare = cls._names_bound_to_factories(tree)

        def is_factory(func):
            if isinstance(func, ast.Attribute):
                return (func.attr in cls.FACTORIES
                        and isinstance(func.value, ast.Name)
                        and func.value.id in modules)
            return isinstance(func, ast.Name) and func.id in bare

        hits = []

        def visit(node, inside):
            if isinstance(node, ast.Call) and not inside \
                    and is_factory(node.func):
                hits.append(node.lineno)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for child in node.decorator_list:
                    visit(child, inside)
                visit(node.args, inside)
                if node.returns is not None:
                    visit(node.returns, inside)
                for child in node.body:
                    visit(child, True)
                return
            if isinstance(node, ast.Lambda):
                visit(node.args, inside)
                visit(node.body, True)
                return
            if cls._is_main_guard(node):
                visit(node.test, inside)
                for child in node.orelse:
                    visit(child, inside)
                return
            for child in ast.iter_child_nodes(node):
                visit(child, inside)

        visit(tree, False)
        return sorted(set(hits))

    def test_no_test_module_makes_a_temporary_path_at_import(self):
        offenders = []
        for path in sorted(self.TESTS.rglob("*.py")):
            for line in self._at_import(path.read_text(encoding="utf-8")):
                offenders.append(f"{path.relative_to(self.TESTS)}:{line}")
        assert offenders == [], offenders

    def test_there_are_test_modules_to_sweep(self):
        assert len(list(self.TESTS.rglob("*.py"))) >= 20

    def test_the_sweep_would_notice(self):
        """Driven against known-bad and known-good samples, as the other
        sweeps here are. The first bad sample is tests/test_transport.py:58
        as it stood at e194c9f, verbatim."""
        bad = ('RUN_DIR = Path(tempfile.mkdtemp(prefix="qc_transport_"))'
               '  # generated artefacts\n')
        assert self._at_import(bad) == [1]
        assert self._at_import(
            "class T:\n    D = tempfile.mkdtemp()\n") == [2]
        assert self._at_import(
            "from tempfile import mkdtemp as make\nX = make()\n") == [2]
        assert self._at_import(
            "import tempfile as tf\nif True:\n    X = tf.mkstemp()\n") == [3]
        assert self._at_import(
            "def f(where=tempfile.mkdtemp()):\n    return where\n") == [1]
        assert self._at_import(
            "F = tempfile.NamedTemporaryFile(delete=False)\n") == [1]
        assert self._at_import(
            "if __name__ == 'main':\n    X = tempfile.mkdtemp()\n") == [2]

        good = ("_GEN_DIR = None\n\n"
                "def _gen_dir():\n"
                "    global _GEN_DIR\n"
                "    if _GEN_DIR is None:\n"
                "        _GEN_DIR = Path(tempfile.mkdtemp(prefix='x'))\n"
                "        atexit.register(shutil.rmtree, _GEN_DIR, True)\n"
                "    return _GEN_DIR\n")
        assert self._at_import(good) == []
        assert self._at_import(
            "@pytest.fixture\ndef d():\n    t = tempfile.mkdtemp()\n"
            "    yield t\n    shutil.rmtree(t)\n") == []
        assert self._at_import("make = lambda: tempfile.mkdtemp()\n") == []
        assert self._at_import(
            "if __name__ == '__main__':\n    X = tempfile.mkdtemp()\n") == []
        assert self._at_import("NAME = tempfile.mktemp()\n") == []


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


class TestNoFixtureBuildsAProjectQualCoderCannotMake:
    """The shape that hid the fix-round-5 defect for a whole batch.

    Every fixture in this suite built `coder_names` WITH the visibility
    column and none of the four views. QualCoder makes that combination
    nowhere: `update_coder_names` adds the column and creates the views
    in one routine that runs on every project open, at 3.8.2 and at
    master alike. The suite was therefore driving thousands of
    assertions through a project shape that exists only when someone has
    removed the views, while the probe read it as "no capability at
    all", so the state that had to fail closed was the state everything
    was tested on. The fixtures now build a project that declares
    nothing, and the declared-but-damaged state has its own fixture.

    Pinned at both ends: the built projects, and the DDL that builds
    them."""

    REPO = pathlib.Path(__file__).resolve().parents[1]
    # Assembled rather than written out, for the reason the sweep above
    # reads syntax: this module has to be able to name the shape it
    # forbids without reporting itself.
    COLUMN = "visibility INTEGER NOT NULL " + "DEFAULT 1"
    # The two places a visibility column may legitimately be spelled:
    # the migration the 4.0 fixture applies, and the verbatim upstream
    # CREATE the parity pins run. Both create the views alongside it.
    ALLOWED = {"test_qc40_visibility.py", "test_v012_ai_coder_setting.py"}

    def test_the_stock_fixture_declares_nothing(self, setup_server):
        import qualcoder_mcp.server as server_module
        caps = server_module.db.capabilities
        assert caps.visibility_declared() is False
        assert caps.visibility_incomplete is False

    def test_the_shared_builder_declares_nothing(self, tmp_path):
        path = H.build_project({}, parent=tmp_path)
        db = database.QualcoderDatabase(path)
        try:
            assert db.capabilities.visibility_declared() is False
        finally:
            db.close()

    def test_only_the_two_visibility_modules_spell_the_column(self):
        offenders = []
        for module in sorted((self.REPO / "tests").glob("*.py")):
            if module.name in self.ALLOWED:
                continue
            if self.COLUMN in module.read_text(encoding="utf-8"):
                offenders.append(module.name)
        assert offenders == [], offenders

    def test_the_sweep_would_notice(self):
        """Otherwise it could pass because the spelling drifted."""
        allowed = sorted(self.ALLOWED)
        found = [name for name in allowed
                 if self.COLUMN in (self.REPO / "tests" / name).read_text(
                     encoding="utf-8")]
        assert found == allowed, found


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
