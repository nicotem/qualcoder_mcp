# Data Flow & Privacy

This document explains exactly what happens to your research data when
you use the QualCoder MCP server. It is factual and deliberately
sober: this tool makes the data flow explicit precisely so you can make
an informed decision — many AI integrations don't. It is not legal
advice.

## How your data flows

**The server itself runs entirely on your machine.** It is a local
process started by your MCP client (Claude Desktop, Claude Code, or any
other). It adds **no telemetry, no analytics, and no separate cloud
path** of its own. It opens your QualCoder project database read-only
by default, and nothing in this server ever "phones home".

**But the results of tool calls enter your Claude conversation.** That
is the entire point of an MCP server — and it has a consequence you
must understand:

> Whatever a tool returns — coded segments, interview excerpts, file
> contents, memos, journal entries, code names, frequencies, case and
> attribute data — is delivered into the conversation, and
> conversation content is **transmitted to Anthropic and processed
> like any other chat or API content**. Reading a transcript through
> this tool sends the returned portions of that transcript to
> Anthropic.

What stays local, always:

- your QualCoder project itself (the `.qda` folder and database)
- automatic backups created before writes
- exported files (CSV/txt/md reports, REFI-QDA `.qdpx`)
- AI-coding session files (`~/.qualcoder_mcp/sessions/`)

What leaves your machine: **only what tools return into the
conversation** — but for qualitative research, that can be the most
sensitive content you hold.

## What this means for research data

Your participants may have consented to *you* analyzing their data —
that is not the same as consenting to their data being processed by a
third-party AI provider. Whether this flow is acceptable for a given
project is **the researcher's responsibility to determine**, and the
answer belongs in:

- your **informed-consent language** (does it cover third-party
  processing by an AI service?)
- your **data-management plan**
- your **ethics / IRB approvals**
- for EU/UK researchers, your **GDPR position**: you remain the data
  controller for your research data; Anthropic's processing terms
  differ by plan and product.

For what Anthropic does with conversation content — retention,
processing, and how terms differ between consumer plans, the API, and
enterprise offerings — consult **Anthropic's own privacy
documentation** for the current terms:
<https://www.anthropic.com/privacy>. Those terms vary by product and
change over time; this document deliberately does not characterize
them.

## Practical mitigations

- **Work on pseudonymized or de-identified copies** of your projects
  where possible.
- **Only open projects whose consent covers third-party processing.**
- **Consider which files you let the AI read.** Tools read only what is
  asked for — a session that never touches file 7 never transmits
  file 7's text.
- **Consult your institution's DPO or ethics board** if you are unsure
  — before the analysis, not after.
- Remember that the server's safety features (read-only default,
  automatic local backups, refuse-while-QualCoder-is-open) protect your
  project's **integrity on disk** — they do not change what leaves the
  machine through the conversation.

## Questions

Questions about this document belong in
[GitHub Issues](https://github.com/nicotem/qualcoder_mcp/issues) like
everything else (see [SUPPORT.md](SUPPORT.md)) — and please don't paste
participant data into an issue either.
