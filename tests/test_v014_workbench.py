# SPDX-License-Identifier: LGPL-3.0-or-later
"""v0.14, the workbench: the limits that stop a hang holding CI.

- Every job in `.github/workflows/ci.yml` carries a time limit. GitHub's
  default is six hours, so a hung test used to hold a runner that long
  before anyone heard of it.
- A test that runs past the per-test limit is named in the run's output:
  pytest's own `faulthandler_timeout` (the standard library's
  `faulthandler`, built into pytest, so no new dependency) writes every
  thread's stack, the test's own frame included, once one test has run
  longer than the limit. It changes no verdict: a slow test on a slow
  runner still passes, and a hung one is then ended by the job's limit
  with its name already in the log.

The reasons for the two numbers are beside them, in ci.yml and in
pyproject.toml.
"""

import re
import subprocess
import sys
from pathlib import Path

from test_v012_workflow_pins import WORKFLOWS, _jobs

REPO = Path(__file__).resolve().parents[1]

JOB_TIME_LIMIT_MINUTES = 90
PER_TEST_LIMIT_SECONDS = 600


def test_every_ci_job_has_a_time_limit():
    jobs = _jobs(WORKFLOWS / "ci.yml")
    assert [job["name"] for job in jobs] == ["test"]
    for job in jobs:
        limits = [line for line in job["lines"]
                  if re.match(r"^\s*timeout-minutes:", line)]
        assert limits == [f"    timeout-minutes: {JOB_TIME_LIMIT_MINUTES}"], (
            f"{job['where']}: job '{job['name']}' should carry one "
            f"job-level time limit of {JOB_TIME_LIMIT_MINUTES} minutes; "
            f"found {limits}")


def test_the_per_test_limit_is_live_in_this_run(pytestconfig):
    """The value from pyproject, and the plugin that acts on it loaded: a
    `-p no:faulthandler` on CI's command line would leave the value in
    place and nothing reading it."""
    assert pytestconfig.pluginmanager.has_plugin("faulthandler")
    assert float(pytestconfig.getini("faulthandler_timeout")) == \
        PER_TEST_LIMIT_SECONDS


def test_a_test_past_the_limit_is_named(tmp_path):
    """In its own interpreter, under this repository's configuration with
    the limit lowered to one second: the output names the file, the line
    and the test that ran past it, and the test still passes."""
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_slow.py").write_text(
        "import time\n"
        "\n"
        "def test_runs_past_the_limit():\n"
        "    time.sleep(3)\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "-c", str(REPO / "pyproject.toml"), "--rootdir", str(tests),
         f"--basetemp={tmp_path / 'basetemp'}",
         "-o", "faulthandler_timeout=1", str(tests / "test_slow.py")],
        cwd=str(tests), capture_output=True, text=True, timeout=120)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output[-2000:]
    assert "1 passed" in output, output[-2000:]
    assert "Timeout (0:00:01)!" in output, output[-2000:]
    assert re.search(r'test_slow\.py", line 4 in test_runs_past_the_limit',
                     output), output[-2000:]
