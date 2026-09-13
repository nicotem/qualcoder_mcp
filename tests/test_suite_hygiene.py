"""Guards on the suite itself: where it may write, and what it compiles.

Fix round 1 (QA round 1, F1 and F19). Two defects that a green suite
cannot see: a test whose sandbox redirects nothing and therefore writes
into the researcher's own folders, and a compile-time warning that only
appears on the first import of a fresh checkout. Both get a pin here.
"""

import os
import pathlib
import subprocess
import sys
import warnings

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

        monkeypatch.setattr(pathlib.Path, "iterdir", _boom)
        assert (H.real_workspace_entries(tmp_path)
                is H.WORKSPACE_UNREADABLE)


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
