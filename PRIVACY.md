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
> conversation content is **transmitted to whichever AI provider your
> host uses and processed like any other chat or API content**. For
> Claude hosts that provider is Anthropic; with a fully local host
> (rung 4 below) there is no external provider at all. Reading a
> transcript through this tool sends the returned portions of that
> transcript to that provider.

What stays local, always:

- your QualCoder project itself (the `.qda` folder and database)
- automatic backups created before writes
- exported files (CSV/txt/md reports, REFI-QDA `.qdpx`)
- AI-coding session files (`~/.qualcoder_mcp/sessions/`)

What leaves your machine: **only what tools return into the
conversation** — but for qualitative research, that can be the most
sensitive content you hold.

## Your governance options, from default to fully local (Experimental)

Which terms govern the AI processing is decided by the host you run and
the account you sign into, not by this server. Four rungs, each with
what changes and what to check. Discipline note: we quote official
pages verbatim with their URLs and never characterize terms in our own
voice; every quote below was pulled on 2026-08-17, terms change, and
the linked pages govern. (The multi-host support itself is Experimental
and not yet capability-evaluated; see the INSTALL.md recipes.)

### Rung 1: Claude consumer plans (Free/Pro/Max, including Claude Code signed in with them)

Do not assume what your account's training default is. Open
<https://claude.ai/settings/data-privacy-controls> and check the Model
Improvement setting yourself. The governing documents:

- Consumer Terms of Service (effective date shown: October 8, 2025):
  <https://www.anthropic.com/legal/consumer-terms>, which state:
  > "We may use Materials to provide, maintain, and improve the
  > Services, including training our models, unless you opt out"
- Privacy Policy (effective date shown: July 8, 2026):
  <https://www.anthropic.com/legal/privacy>
- Privacy Center article "Is my data used for model training?":
  <https://privacy.claude.com/en/articles/10023580-is-my-data-used-for-model-training>

Exceptions that apply regardless of the setting (Consumer Terms,
quoted 2026-08-17):

> "Even if you opt out, we will use Materials for model training when:
> (1) you provide Feedback to us regarding any Materials, or (2) your
> Materials are flagged for safety review"

### Rung 2: Anthropic API key (commercial-terms route)

Using Claude Code with a Console API key routes traffic under the
Commercial Terms (<https://www.anthropic.com/legal/commercial-terms>,
effective date shown: June 17, 2025), which state (quoted 2026-08-17):

> "Anthropic may not train models on Customer Content from Services."

The commercial-products Privacy Center article
(<https://privacy.claude.com/en/articles/7996885-how-do-you-use-personal-data-in-model-training>)
states: "We will not use your chats or coding sessions to train our
models, unless you choose to participate in our Development Partner
Program." A Data Processing Addendum exists on the commercial side
(<https://www.anthropic.com/legal/data-processing-addendum>, effective
date shown: February 24, 2025); it is the instrument an institution's
DPO will ask about.

**The individual-account wrinkle, presented without resolving it.**
The Consumer Terms' scope clause includes:

> "Claude.ai, Claude Pro, and other products and services that we may
> offer for individuals (including any Anthropic API key and the
> Anthropic Console, when used by individuals)"

while the Commercial Terms state "Services under these Terms are not
for consumer use." For unambiguous commercial-terms coverage, use a
Console account created for the institution or research group, and let
your DPO read the current versions of both pages. Mechanics: the
INSTALL.md recipe "Claude Code with an Anthropic API key".

### Rung 3: Team/Enterprise (Claude for Work)

Same commercial-terms footing. The August 2025 consumer announcement
(<https://www.anthropic.com/news/updates-to-our-consumer-terms>,
quoted 2026-08-17) lists what the consumer training changes do NOT
touch:

> "These updates do not apply to services under our Commercial Terms,
> including: Claude for Work, which includes our Team and Enterprise
> plans; Our API, Amazon Bedrock, or Google Cloud's Vertex API; Claude
> Gov and Claude for Education"

If your institution already has a Team or Enterprise deployment, using
this server through Claude Desktop or Claude Code under that account
is already commercial-terms coverage; no API key is needed.

### Rung 4: fully local models (Experimental)

The rung where the third-party-processor question disappears: model
inference and every qualcoder-mcp operation happen on your machine. LM
Studio's documentation states (quoted 2026-08-17,
<https://lmstudio.ai/docs/app/offline>) that LM Studio "can operate
entirely offline" and that "Nothing you enter into LM Studio when
chatting with LLMs leaves your device". That is the vendor's statement,
not our certification: verify offline operation yourself (disconnect
and work) and record it as a data-management-plan evidence point.

The trade is stated plainly: a narrower workflow with more supervision,
the reduced core toolset required (`QUALCODER_MCP_TOOLSET=core`), and,
importantly, **we have not yet evaluated how well any local model
performs with this server**. That evaluation is pending; until then
local-model behaviour is unverified, which is why this rung is marked
Experimental. Mechanics: the INSTALL.md recipe "LM Studio (fully
local)".

### Cross-rung cautions

- Feedback mechanisms, safety flagging, and opt-in programs can pierce
  every Anthropic route. Never use feedback features (thumbs,
  /feedback, /bug) in sessions containing participant data.
- Claude Code has side channels: error reporting, session surveys,
  /feedback retention, and local plaintext transcripts under
  `~/.claude/projects/`. Mitigations:
  `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` and `cleanupPeriodDays`
  (see <https://code.claude.com/docs/en/data-usage>).
- Commercial-terms coverage is not GDPR compliance. The DPA exists;
  controller/processor analysis and executing or relying on the DPA
  remain institution-level work.
- All quotes above were pulled 2026-08-17. Terms change; the linked
  pages govern. The Privacy Center now lives at privacy.claude.com
  (older privacy.anthropic.com links redirect there).

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
