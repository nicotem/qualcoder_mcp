# SPDX-License-Identifier: LGPL-3.0-or-later
"""v0.12 Batch A, item A5: `--version` and the terminal notice.

`--version` prints the package version and exits 0 (console script and
`python -m qualcoder_mcp.server`); a start with stdin on a TTY prints one
paragraph to stderr and keeps running (hosts never present a TTY, so the
notice can only appear when a person starts the server by hand). The TTY
tests monkeypatch isatty so CI stays deterministic (no pty).
"""

import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp
import qualcoder_mcp.server as server

REPO = Path(__file__).resolve().parent.parent


# ===========================================================================
# A5: --version and the TTY notice
# ===========================================================================

class _FakeStdin:
    def __init__(self, tty):
        self._tty = tty

    def isatty(self):
        return self._tty


class _RaisingStdin:
    def isatty(self):
        raise ValueError("I/O operation on closed file")


class TestVersionFlag:

    def test_version_flag_in_process_exits_zero_and_prints_version(self, capsys):
        with pytest.raises(SystemExit) as exc:
            server.main(["--version"])
        assert exc.value.code == 0
        out = capsys.readouterr()
        assert out.out.strip() == f"qualcoder-mcp {qualcoder_mcp.__version__}"
        assert qualcoder_mcp.__version__ in out.out
        assert "0.0.0+unknown" not in out.out

    def test_module_invocation_reports_version(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO / "src")
        env.pop("QUALCODER_PROJECT_PATH", None)
        proc = subprocess.run(
            [sys.executable, "-m", "qualcoder_mcp.server", "--version"],
            capture_output=True, text=True, encoding="utf-8", env=env,
            timeout=60)
        assert proc.returncode == 0, proc.stderr
        assert qualcoder_mcp.__version__ in proc.stdout

    def test_console_script_reports_version(self):
        script = shutil.which("qualcoder-mcp", path=str(Path(sys.executable).parent))
        if script is None:
            pytest.skip("qualcoder-mcp console script not installed beside this interpreter")
        proc = subprocess.run([script, "--version"], capture_output=True,
                              text=True, encoding="utf-8", timeout=60)
        assert proc.returncode == 0, proc.stderr
        assert qualcoder_mcp.__version__ in proc.stdout

    def test_version_says_nothing_on_stderr_and_creates_no_state(self, tmp_path):
        """Fix round 1, F16. Importing the module used to call
        logging.basicConfig and build a SessionManager, so `--version`
        printed an INFO line above the version and created
        ~/.qualcoder_mcp/sessions before it had read its own command line.
        A fresh HOME proves neither happens."""
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO / "src")
        env.pop("QUALCODER_PROJECT_PATH", None)
        home = tmp_path / "home"
        home.mkdir()
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)          # Path.home() on Windows

        proc = subprocess.run(
            [sys.executable, "-m", "qualcoder_mcp.server", "--version"],
            capture_output=True, text=True, encoding="utf-8", env=env,
            timeout=120)

        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == f"qualcoder-mcp {qualcoder_mcp.__version__}"
        assert proc.stderr == ""
        assert not (home / ".qualcoder_mcp").exists()

    def test_importing_the_module_writes_nothing_and_touches_nothing(self, tmp_path):
        """The module may configure logging at import (the plain stderr
        format has to win over FastMCP's rich one), but it must not LOG and
        must not create anything, or --version speaks second."""
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO / "src")
        env.pop("QUALCODER_PROJECT_PATH", None)
        home = tmp_path / "home"
        home.mkdir()
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)
        proc = subprocess.run(
            [sys.executable, "-c", "import qualcoder_mcp.server"],
            capture_output=True, text=True, encoding="utf-8", env=env,
            timeout=120)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout == ""
        assert proc.stderr == ""
        assert list(home.iterdir()) == []

    def test_unknown_argument_is_refused_with_usage(self, capsys):
        with pytest.raises(SystemExit) as exc:
            server.main(["--bogus"])
        assert exc.value.code == 2
        assert "usage:" in capsys.readouterr().err


class TestTtyNotice:

    @pytest.fixture
    def stub_run(self, monkeypatch):
        calls = []
        monkeypatch.setattr(server.mcp, "run", lambda **kw: calls.append(kw))
        monkeypatch.delenv("QUALCODER_PROJECT_PATH", raising=False)
        monkeypatch.delenv("QUALCODER_MCP_TOOLSET", raising=False)
        return calls

    def test_notice_when_stdin_is_a_tty(self, monkeypatch, capsys, stub_run):
        monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=True))
        server.main([])                       # returns: no exit, no exception
        err = capsys.readouterr().err
        assert err.count(server.TTY_NOTICE) == 1
        assert "--version" in err and "INSTALL.md" in err
        assert stub_run == [{"transport": "stdio"}]   # the server still starts

    def test_silent_when_stdin_is_a_pipe(self, monkeypatch, capsys, stub_run):
        monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=False))
        server.main([])
        assert server.TTY_NOTICE not in capsys.readouterr().err
        assert stub_run == [{"transport": "stdio"}]

    def test_notice_goes_to_stderr_never_stdout(self, monkeypatch, capsys, stub_run):
        monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=True))
        server.main([])
        out = capsys.readouterr()
        assert out.out == ""                  # stdout is the MCP transport

    def test_helpers_are_defensive(self):
        assert server._stdin_is_tty(None) is False
        assert server._stdin_is_tty(_RaisingStdin()) is False
        assert server._stdin_is_tty(io.StringIO()) is False
        err = io.StringIO()
        assert server._print_tty_notice_if_interactive(_FakeStdin(True), err) is True
        assert err.getvalue().strip() == server.TTY_NOTICE
        err = io.StringIO()
        assert server._print_tty_notice_if_interactive(_FakeStdin(False), err) is False
        assert err.getvalue() == ""

    def test_notice_text_house_rules(self):
        assert "—" not in server.TTY_NOTICE
        assert "qualcoder-mcp --version" in server.TTY_NOTICE
        assert "python -m qualcoder_mcp.server --version" in server.TTY_NOTICE
        assert "\n" not in server.TTY_NOTICE   # one paragraph

    def test_registry_intact_after_main_in_process(self, monkeypatch, capsys, stub_run):
        monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=False))
        server.main([])
        assert len(server.mcp._tool_manager._tools) == 73
