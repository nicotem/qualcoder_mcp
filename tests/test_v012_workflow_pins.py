"""v0.12 Batch A, fix round 1 (F5): the version comments beside the
SHA-pinned GitHub Actions have to stay truthful.

Dependabot rewrites the SHA and, when the trailing comment carries a
suffix such as "(release/v1)", it does not always rewrite the version
next to it. That is how `pypa/gh-action-pypi-publish` came to sit at the
v1.14.2 SHA under a "# v1.14.1" comment while the CHANGELOG repeated the
stale number. Nothing in the suite noticed.

These are offline consistency pins: every pinned action carries a full
40-character SHA and a version comment, the same action is pinned to the
same SHA and version in every workflow, and the CHANGELOG CI entry names
the same versions the comments do. Whether a SHA really is that tag is
checked against the GitHub API by hand at bump time; it cannot be
checked without network access.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

# owner/action@sha  # vX.Y.Z[ (suffix)]
PIN_RE = re.compile(
    r"uses:\s*(?P<action>[\w.-]+/[\w.-]+)@(?P<sha>[0-9a-f]{40})"
    r"\s*#\s*(?P<version>v[\w.]+)(?P<suffix>\s*\([^)]*\))?"
)


def _pins():
    found = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for line_no, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if "uses:" not in line:
                continue
            match = PIN_RE.search(line)
            assert match is not None, f"{path.name}:{line_no} {line.strip()}"
            found.append({
                "where": f"{path.name}:{line_no}",
                "action": match.group("action"),
                "sha": match.group("sha"),
                "version": match.group("version"),
                "suffix": (match.group("suffix") or "").strip(),
            })
    return found


def test_every_action_is_sha_pinned_with_a_version_comment():
    pins = _pins()
    # Guard against a glob that matched nothing.
    assert len(pins) >= 8, pins
    assert {pin["action"] for pin in pins} == {
        "actions/checkout",
        "actions/setup-python",
        "actions/upload-artifact",
        "actions/download-artifact",
        "pypa/gh-action-pypi-publish",
    }


def test_the_same_action_is_pinned_the_same_way_everywhere():
    by_action = {}
    for pin in _pins():
        by_action.setdefault(pin["action"], []).append(pin)
    for action, pins in by_action.items():
        shas = {pin["sha"] for pin in pins}
        versions = {pin["version"] for pin in pins}
        assert len(shas) == 1, (action, [p["where"] for p in pins], shas)
        assert len(versions) == 1, (action, [p["where"] for p in pins], versions)


def test_changelog_ci_entry_names_the_pinned_versions():
    text = CHANGELOG.read_text(encoding="utf-8")
    start = text.index("### CI")
    entry = text[start:text.index("\n### ", start + 1)
                 if "\n### " in text[start + 1:] else len(text)]
    for pin in _pins():
        short = pin["action"].split("/")[-1]
        claim = f"{short} {pin['version']}"
        assert claim in entry, (pin["where"], claim)
