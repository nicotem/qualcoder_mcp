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
- for EU/UK researchers, your **GDPR position**: your institution is
  normally the data *controller* and a provider like Anthropic a
  *processor* — which usually requires an institution-level
  data-processing agreement and a valid transfer safeguard (see the next
  section), not something an individual researcher can arrange alone.

For what Anthropic does with conversation content — retention,
processing, and how terms differ between consumer plans, the API, and
enterprise offerings — consult **Anthropic's own privacy
documentation** for the current terms:
<https://www.anthropic.com/privacy>. Those terms vary by product and
change over time; this document deliberately does not characterize
them.

## Before you use real participant data — check these

These are the questions your ethics committee or Data Protection Officer
will ask, and the summary above depends on them:

- **Your Claude plan's terms differ — and matter.** For *your* account,
  verify whether inputs (a) may be used to train or improve models,
  (b) how long they are retained, and (c) whether they can be reviewed by
  people. These differ materially between consumer plans (Free/Pro) and
  Team/Enterprise/API terms. Inputs being used for model training would
  almost never be covered by existing participant consent — an ethics
  board asks this first.
- **Controller / processor, and a written agreement.** Your institution
  is normally the data controller and Anthropic a processor. UK/EU GDPR
  (Art. 28) then requires a written data-processing agreement, and a
  UK/EU→US transfer needs a valid safeguard (UK IDTA, SCCs, or an
  adequacy/data-bridge mechanism). A personal or consumer account almost
  certainly has **no such agreement** — so this is an institution-level
  decision you cannot clear alone.
- **Special-category data.** Interviews routinely carry health, sexuality,
  religion, ethnicity, political opinion and similar — often disclosed
  incidentally — which has a higher legal bar (GDPR Art. 9).
- **Consent is not compliance.** Participant consent to AI processing
  addresses the ethics limb; it does not by itself provide your lawful
  basis, your transfer safeguard, or the processing agreement.
- **You cannot claw it back.** Content already sent generally cannot be
  retracted — which can make it impossible to honour a participant's
  withdrawal or erasure request, or a retention limit you promised in a
  consent form or ethics application.
- **Secondary use.** Re-analysing data gathered for one study with AI may
  go beyond the original consent and ethics approval, and may itself need
  review.

## Practical mitigations

- **Prefer synthetic or truly anonymised data.** Removing names does *not*
  make a transcript safe to send: pseudonymised (name-stripped)
  qualitative data is **still personal data** and is often re-identifiable
  from context (role, locality, events, relationships, distinctive
  phrasing). Treat pseudonymisation as risk-reduction only — synthetic or
  genuinely anonymised data is the safe path for experimentation.
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
