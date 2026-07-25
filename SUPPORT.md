# Support

Thanks for using the QualCoder MCP server! This is an **experimental
alpha** built by one researcher. Feedback, bug reports, and feature
ideas are genuinely wanted — they directly shape what gets built next.

## Where to get help

**Everything goes through [GitHub Issues](https://github.com/nicotem/qualcoder_mcp/issues):**

- **Bug reports** — please include what you did (the tool calls or the
  conversation step), what you expected, and what happened instead.
  Never paste sensitive research data into an issue; a synthetic or
  redacted example is perfect. (For what happens to research data when
  you USE the tool — what is transmitted to Anthropic and what stays
  local — see [PRIVACY.md](PRIVACY.md).)
- **Questions** — check the [README](README.md) troubleshooting section
  and [AI_CODING_WORKFLOW.md](AI_CODING_WORKFLOW.md) first, then open an
  issue. Questions are welcome; yours is probably the next person's too.
- **Feature requests and ideas** — very welcome. The release philosophy
  is to develop the next capabilities together with early users, so
  "it would help my analysis if…" issues are exactly what's wanted.

Issues are public and searchable: every answered question helps the next
researcher who hits the same thing, and others can confirm a bug or add
detail. That's why there is one channel, and why it isn't email.

## Please don't email support requests

The author's email address in `pyproject.toml` and the `LICENSE` file is
an **authorship signature** — it identifies who wrote this software. It
is **not a support channel**, and support requests sent by email will
not receive a reply. This isn't unfriendliness: answers buried in a
private inbox help exactly one person once, while answers on GitHub
Issues help everyone, permanently. Please use
[GitHub Issues](https://github.com/nicotem/qualcoder_mcp/issues) instead.

## Before you report

- This is alpha software: **always work on copies of your projects**
  (the `copy_project_to_workspace` tool exists for exactly this), and
  keep your own backups of important data.
- Make sure QualCoder is closed for the project you're working with —
  writes are refused while it has the project open, by design.
- Include your QualCoder version (3.8.x is the supported line) and the
  server version from `pyproject.toml` in bug reports.
