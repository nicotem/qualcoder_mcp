"""v0.12 Batch A, fix round 1 (F5): the version comments beside the
SHA-pinned GitHub Actions have to stay truthful.

Dependabot rewrites the SHA and, when the trailing comment carries a
suffix such as "(release/v1)", it does not always rewrite the version
next to it. That is how `pypa/gh-action-pypi-publish` came to sit at the
v1.14.2 SHA under a "# v1.14.1" comment while the CHANGELOG repeated the
stale number. Nothing in the suite noticed.

Fix round 2, R5: the round-1 version of this file checked internal
consistency only, and the incident was internally CONSISTENT (both
`uses:` lines carried the stale number and so did the CHANGELOG), so it
would have passed on the pre-fix tree. The three consistency tests are
kept, because they catch a half-rewritten bump cheaply, but the check
that catches the incident itself is the ledger below: every pinned SHA is
recorded against the tag it really dereferences to upstream, so a comment
that disagrees with the SHA fails here however consistent the rest of the
tree is.

The ledger is the offline record of an online check, and a SHA that is
not in it is a failure rather than a pass: bumping a pin therefore means
dereferencing the new tag and recording the pair, which is exactly the
step that was skipped. For owner/action at tag vX.Y.Z:

    curl -s https://api.github.com/repos/<owner>/<action>/git/ref/tags/vX.Y.Z

reading `.object.sha`, and when `.object.type` is "tag" (an annotated
tag, as pypa/gh-action-pypi-publish uses) the commit is one hop further:

    curl -s https://api.github.com/repos/<owner>/<action>/git/tags/<sha>

No test here reaches the network: CI cannot assume it, and a suite that
silently skipped the check offline would be the same hole again.

Fix round 3, S2: the ledger read `*.yml` only. A workflow added as
`*.yaml` is equally valid to GitHub Actions and was scanned by nobody,
while every test here kept passing because the two existing files supply
the ten pins the anti-vacuity guard asks for. The scan is now
`_scanned_files()`, which reads both spellings plus any composite action
under .github/actions, refuses a file in .github/workflows it cannot
read, and is itself pinned against the directory listing.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
ACTIONS = REPO_ROOT / ".github" / "actions"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

# (action, commit SHA) -> the tag whose tip that commit is, each pair
# dereferenced against the public GitHub API on 2026-09-13 with the two
# commands in the module docstring. The superseded gh-action-pypi-publish
# pin is kept as the F5 incident's ground truth: the old SHA belongs to
# v1.14.1, the current one to v1.14.2, which is what the stale comment
# got wrong.
VERIFIED_TAGS = {
    ("actions/checkout",
     "3d3c42e5aac5ba805825da76410c181273ba90b1"): "v7.0.1",
    ("actions/setup-python",
     "5fda3b95a4ea91299a34e894583c3862153e4b97"): "v7.0.0",
    ("actions/upload-artifact",
     "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"): "v7.0.1",
    ("actions/download-artifact",
     "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"): "v8.0.1",
    ("pypa/gh-action-pypi-publish",
     "dc37677b2e1c63e2034f94d8a5b11f265b73ba33"): "v1.14.2",
    ("pypa/gh-action-pypi-publish",
     "ba38be9e461d3875417946c167d0b5f3d385a247"): "v1.14.1",
}

# owner/action@sha  # vX.Y.Z[ (suffix)]
PIN_RE = re.compile(
    r"uses:\s*(?P<action>[\w.-]+/[\w.-]+)@(?P<sha>[0-9a-f]{40})"
    r"\s*#\s*(?P<version>v[\w.]+)(?P<suffix>\s*\([^)]*\))?"
)


def _scanned_files():
    """Every file the ledger reads, and a refusal to leave one unread.

    Fix round 3, S2: the glob was `*.yml`, and GitHub Actions treats
    `.yaml` as equally valid, so a workflow added under that spelling was
    scanned by nobody while all four tests here still passed (the
    anti-vacuity guard is satisfied by the two existing files on its
    own). Composite actions under .github/actions are read for the same
    reason; rglob over a directory that does not exist yields nothing, so
    this is safe while the repository has none.

    The `stray` refusal is what stops the next spelling surprise rather
    than only the one known spelling: a workflow committed as .yamlx, or
    with no suffix at all, fails here instead of escaping the ledger.
    Names beginning with a dot are not workflows and are skipped, so a
    Finder-planted .DS_Store does not fail the suite.
    """
    stray = sorted(path.name for path in WORKFLOWS.iterdir()
                   if path.is_file() and not path.name.startswith(".")
                   and path.suffix not in {".yml", ".yaml"})
    assert not stray, (
        f"files in .github/workflows that this ledger cannot scan: {stray}. "
        f"GitHub Actions reads .yml and .yaml; anything else here is either "
        f"a mistake or a change this test has to learn about.")
    return sorted([*WORKFLOWS.glob("*.y*ml"), *ACTIONS.rglob("action.y*ml")])


def test_the_ledger_scans_every_workflow_file_on_disk():
    """Non-vacuity for the scan itself, not only for the pins it found.

    A glob that matches nothing, or matches less than the directory
    holds, makes every other test in this file pass by default.
    """
    scanned = _scanned_files()
    assert scanned, f"nothing scanned under {WORKFLOWS}"
    on_disk = sorted(path for path in WORKFLOWS.iterdir()
                     if path.is_file() and not path.name.startswith("."))
    assert [path.name for path in scanned if path.parent == WORKFLOWS] == \
        [path.name for path in on_disk]
    # And the two that carry the pins are among them, so a rename cannot
    # empty the scan quietly.
    assert {"ci.yml", "publish.yml"} <= {path.name for path in scanned}


def _pins():
    found = []
    for path in _scanned_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        for line_no, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if "uses:" not in line:
                continue
            match = PIN_RE.search(line)
            # The path is relative to the repository root, not the bare
            # file name: two composite actions are both `action.yml`
            # (fix round 3, S2).
            assert match is not None, f"{rel}:{line_no} {line.strip()}"
            found.append({
                "where": f"{rel}:{line_no}",
                "action": match.group("action"),
                "sha": match.group("sha"),
                "version": match.group("version"),
                "suffix": (match.group("suffix") or "").strip(),
            })
    return found


def test_every_pin_carries_the_version_its_sha_really_is():
    """The check the F5 incident needed: SHA against the tag upstream.

    A globally consistent stale comment, which is the shape Dependabot
    produced, passes every internal-consistency test in this file and
    fails this one.
    """
    pins = _pins()
    assert len(pins) >= 8, pins
    for pin in pins:
        key = (pin["action"], pin["sha"])
        assert key in VERIFIED_TAGS, (
            f"{pin['where']}: {pin['action']}@{pin['sha']} is not in "
            f"VERIFIED_TAGS. Dereference the tag against the GitHub API "
            f"(see this module's docstring) and record the pair, rather "
            f"than trusting the comment beside the pin.")
        assert VERIFIED_TAGS[key] == pin["version"], (
            f"{pin['where']}: the comment says {pin['version']} but "
            f"{pin['sha'][:12]} is the tip of "
            f"{VERIFIED_TAGS[key]} upstream.")


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
