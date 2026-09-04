# Support

Thanks for using the QualCoder MCP server. This is an **experimental
alpha** built by one researcher. Feedback, bug reports, and feature
ideas are genuinely wanted: they directly shape what gets built next.

## Where to get help

**Everything goes through [GitHub Issues](https://github.com/nicotem/qualcoder_mcp/issues):**

- **Bug reports**: please include what you did (the tool calls or the
  conversation step), what you expected, and what happened instead.
  Never paste sensitive research data into an issue; a synthetic or
  redacted example is perfect. (For what happens to research data when
  you USE the tool, what leaves your machine and what stays local, see
  [PRIVACY.md](PRIVACY.md).)
- **Questions**: check the [README](README.md) troubleshooting section
  and [AI_CODING_WORKFLOW.md](AI_CODING_WORKFLOW.md) first, then open an
  issue. Questions are welcome; yours is probably the next person's too.
- **Feature requests and ideas**: very welcome. The release philosophy
  is to develop the next capabilities together with early users, so
  "it would help my analysis if…" issues are exactly what's wanted.

Issues are public and searchable: every answered question helps the next
researcher who hits the same thing, and others can confirm a bug or add
detail. That's why there is one channel, and why it isn't email.

## Please don't email support requests

The author's email address in the package metadata (`pyproject.toml`)
is an **authorship signature**: it identifies who wrote this software.
It is **not a support channel**, and support requests sent by email will
not receive a reply. This isn't unfriendliness: answers buried in a
private inbox help exactly one person once, while answers on GitHub
Issues help everyone, permanently. Please use
[GitHub Issues](https://github.com/nicotem/qualcoder_mcp/issues) instead.

## Before you report

- This is alpha software: **always work on copies of your projects**
  (the `copy_project_to_workspace` tool exists for exactly this), and
  keep your own backups of important data.
- Remember the data flow: **everything a tool returns, interview text
  included, enters your AI conversation and is sent to whichever AI
  provider your host uses** (Anthropic for Claude hosts; no external
  provider at all with a fully local host). Prefer synthetic or
  consented data, and check your ethics/GDPR position before using real
  participant data; see [PRIVACY.md](PRIVACY.md).
- Close the project in QualCoder before any writing. Writes are refused
  while a released QualCoder (3.x) has the project open, by design,
  through its lock file. QualCoder 4.0 (the 4.0-Beta pre-release)
  writes no lock file, so there the server can only report that the project appears to
  be open (heuristics, which can miss an open window); never write while
  any QualCoder window has the same project open.
- Include in bug reports: your QualCoder version (a 3.8.x release or
  the 4.0-Beta pre-release; see "Supported QualCoder versions" in the
  README for the supported project schemas),
  the project's schema version (the `databaseversion` value in the
  `schema` block that `get_current_project` returns), the server version
  (`pip show qualcoder-mcp`, `pipx list` or `uv tool list` in the
  environment you installed into; no tool reports it), your MCP host (Claude Desktop, Claude
  Code, LM Studio, other), and the toolset (`QUALCODER_MCP_TOOLSET`:
  `core`, or `full` when the variable is not set). The bug report
  template asks for all of these.
