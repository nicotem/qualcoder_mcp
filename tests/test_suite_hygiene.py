"""Guards on the suite itself: where it may write.

Fix round 1 (QA round 1, F1). A test whose sandbox redirects nothing,
and therefore writes into the researcher's own folders, is invisible to
a green suite: this batch shipped one, and Batch A shipped the same
class of defect under a different constant. So the sandbox gets a pin.
"""

import pathlib

import track5_helpers as H
from qualcoder_mcp import database


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
