# Contributing to qualcoder-mcp

qualcoder-mcp is experimental alpha software maintained by one
researcher. Bug reports, questions, feature ideas and code changes are
all welcome. This file says how to send each, and which rules a change
has to meet before it is merged.

## Reporting issues

Everything goes through
[GitHub Issues](https://github.com/nicotem/qualcoder_mcp/issues): bug
reports, questions and feature ideas alike. There is no email support.
The author's email address in the package metadata is an authorship
signature, not a support channel, and support requests sent there will
not receive a reply. Issues are public and searchable, so every answer
helps the next researcher who hits the same thing. [SUPPORT.md](SUPPORT.md)
has the full policy.

When you report a bug, use the issue template. It asks for the
qualcoder-mcp version, the QualCoder version, the project's schema
version (the `databaseversion` value in the `schema` block that
`get_current_project` returns), your MCP host (Claude Desktop, Claude
Code, LM Studio, other), the toolset (`QUALCODER_MCP_TOOLSET`: `core`,
or `full` when the variable is not set) and your operating system.
Never paste participant data, interview text or anything sensitive into
an issue; describe the problem structurally ("a code name with an
apostrophe", "a file of about 40k characters"). Security concerns take
the same route: open an issue, describe the problem structurally, and
say in the title that it concerns data exposure or write safety so it
is looked at first.

## Proposing changes

1. **Open an issue first** for anything beyond a small fix, so the
   scope can be agreed before you write code. The scope rule below
   explains what is likely to be accepted.
2. **Fork and branch.** `main` is never committed to directly; every
   change arrives on a branch and is merged from a pull request against
   `main`.
3. **Install for development** (Python 3.10 or newer):

   ```bash
   git clone https://github.com/<you>/qualcoder_mcp.git
   cd qualcoder_mcp
   python3 -m venv venv
   source venv/bin/activate   # Windows: venv\Scripts\activate
   pip install -e ".[dev]"
   ```

4. **Run the test suite** from the repository root:

   ```bash
   python -m pytest -q
   ```

   The whole suite must pass, on your platform and on the CI. Every new
   behavior needs a test that fails without the change, including the
   edge cases of any upstream behavior you are matching. Do not add
   skips without a stated reason in the test. One scale test (10k+
   codings, a 500k-character document) is opt-in behind
   `TRACK6_GIANT=1` and is not part of a normal run.
5. **Continuous integration** runs on every pushed branch and on pull
   requests to `main`: six jobs, Ubuntu, Windows and macOS, each on
   Python 3.10 and 3.13. All six must be green. The workflow actions are
   pinned by commit SHA with a version comment beside each pin; if you
   add or bump an action, keep that shape (Dependabot maintains the
   pins).
6. **Document the change.** Add an entry under `Unreleased` in
   `CHANGELOG.md` (Keep a Changelog format). Update the docstring of
   every tool whose arguments or behavior changed: the docstrings are
   the documentation the AI model reads. Update `README.md`,
   `INSTALL.md` and `PRIVACY.md` where they describe the behavior you
   changed; `PRIVACY.md` is the contract for what a tool result may
   disclose.

## Review before merge

Nothing is merged on a green CI alone. Every change also goes through a
QA review and a security review. The QA review checks that each new
behavior has a test that fails without it, that the edge cases of the
matched upstream behavior are covered, and that the tests are
Windows-safe and encoding-safe. The security review looks at write
paths, file and symlink handling, what a tool result discloses into the
AI conversation, and what a hostile project folder or a hostile model
input could make the server do. Findings from both are fixed and
re-verified before the merge, and behavior changes that come out of them
are recorded in the CHANGELOG. The maintainer runs both reviews; expect
them to take longer than the CI, and expect requests for more tests
rather than fewer.

## Style rules

- **UTF-8 pinned on text IO.** Every `open()` of a text file passes
  `encoding="utf-8"` (or `utf-8-sig` where QualCoder's own export
  dialect writes a BOM). Never rely on the platform default encoding.
- **Windows-safe tests.** Build paths with `pathlib`, build sqlite URIs
  with `Path.as_uri()`, and never assert on identical timestamps or wait
  on the wall clock: fabricate file ages with `os.utime`. Skip cleanly
  where symlinks or POSIX permission bits are unavailable. Spawn the
  server as a subprocess only when the transport or startup gating is
  the thing under test, and give it an explicit environment. The suite
  has to pass on the Windows runner, not only on your machine.
- **No em dashes in prose.** This applies to documentation, docstrings,
  error strings, CHANGELOG entries and commit messages. Use a colon, a
  comma, parentheses or a new sentence.
- **Capability probes, never version strings.** Behavior keys on the
  existence of tables, columns and views (`SchemaCapabilities` in
  `database.py`), the way QualCoder's own migration ladder does. The
  version string is informational, with one exception: the forward
  guard that refuses writes on a schema newer than the verified ceiling.
- **Parity claims cite the pinned upstream commit.** Any statement that
  "QualCoder does X" in a comment, docstring, CHANGELOG entry or
  document names the upstream file and line at the pinned commit
  (currently QualCoder master `9bddf17`, version string "QualCoder 4.0
  Beta"; see `VERIFIED_MASTER_COMMIT` in `database.py`). When the pin
  moves, the claims are re-verified and re-cited, not carried forward.
- **Heuristics are phrased as heuristics.** A result that reports a
  guess (a project that "appears to be open" in QualCoder) says so.
  Never word a heuristic as a certainty.
- **Disclosure is existence-only.** A tool result may say that
  something exists (a private note on a row, a hidden coder's row, a
  count), never what it contains or whose it is. If a change alters what
  leaves the project into the conversation, `PRIVACY.md` changes in the
  same pull request.
- **No tool argument named `session_id`.** Some MCP middleware strips
  that name before the call reaches the server; the session tools use
  `coding_session_id`. Check new argument names against other
  routing-flavored names (`request_id`, `conversation_id`, `user_id`,
  `context`, `metadata`) as well.
- **Runtime dependencies stay minimal.** The package has one runtime
  dependency (`mcp`). A new one needs a reason in the pull request;
  anything optional must stay optional and import-guarded (the way
  `psutil` is used).
- **Synthetic test data only.** Never commit a `.qda` project, a real
  transcript or any research text. Test fixtures are built in code.
- **Spelling** follows the surrounding file. The codebase has both
  British and American spelling; do not change one to the other in
  passing.

## Scope

The design is for general use, principles first. A feature is justified
on general grounds: parity with QualCoder's own behavior, general
mappings that hold for any project, capability probes rather than
special cases. Requests that fit only one research project, one
researcher's habits or one MCP host are usually declined or generalized
first. The same rule applies to the maintainer's own projects.

## License

qualcoder-mcp is released under the MIT License (see
[LICENSE](LICENSE)). By contributing you agree that your contribution is
licensed under the same terms. QualCoder itself is licensed under the
LGPL-3.0: do not copy its source into this repository. Re-implement the
behavior independently and cite the upstream file and line you matched,
as `memo_privacy.py` does.
