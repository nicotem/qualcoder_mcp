# Data Flow & Privacy

This document explains exactly what happens to your research data when
you use the QualCoder MCP server. It is factual and deliberately
sober: this tool makes the data flow explicit precisely so you can make
an informed decision, which many AI integrations do not. It is not legal
advice.

## How your data flows

**The server itself runs entirely on your machine.** It is a local
process started by your MCP client (Claude Desktop, Claude Code, or any
other). It adds **no telemetry, no analytics, and no separate cloud
path** of its own. It opens your QualCoder project database read-only
by default, and nothing in this server ever "phones home".

**But the results of tool calls enter your Claude conversation.** That
is the entire point of an MCP server, and it has a consequence you
must understand:

> Whatever a tool returns (coded segments, interview excerpts, file
> contents, memos, journal entries, code names, frequencies, case and
> attribute data) is delivered into the conversation, and
> conversation content is **transmitted to whichever AI provider your
> host uses and processed like any other chat or API content**. For
> Claude hosts that provider is Anthropic; with a fully local host
> (rung 4 below) there is no external provider at all. Reading a
> transcript through this tool sends the returned portions of that
> transcript to that provider.

What stays local, always:

- your QualCoder project itself (the `.qda` folder and database)
- automatic backups created before writes, and the safety backup a
  confirmed restore_backup takes first: timestamped
  `<project>_backup_<timestamp>.qda` folders placed next to the project
  folder
- exported files (CSV/txt/md reports, REFI-QDA `.qdpx`)
- project copies made by copy_project_to_workspace, in
  `~/Documents/Qualcoder MCP Projects/` by default (each carries the
  same content as a backup, so the `ai_data/` and symlink rules below
  apply to it)
- AI-coding session files (`~/.qualcoder_mcp/sessions/`), written
  atomically and created owner-only on POSIX systems (mode 0600)
- the preview-token secret (`~/.qualcoder_mcp/preview_secret`): 64
  random hex characters, created owner-only on POSIX systems, used to
  sign the tokens that authorise a destructive operation. It never
  leaves your machine, never appears in a result, a log line or an error,
  and holds nothing about your project. Deleting it invalidates
  outstanding preview tokens, which means the next execute asks for a
  fresh preview; nothing else. No export can be written into this
  folder: the export tools refuse paths inside it.
- the last-used project pointer (`~/.qualcoder_mcp/mru_project.json`:
  the path of the project most recently selected under your user
  account, plus a timestamp, written on every successful
  select_project). It has one outward flow: when a tool is called
  before a project is selected, the error message names that path as a
  recovery hint (only while that project still exists on disk), so a
  project path chosen in one MCP host or session
  can appear in another host's conversation on the same account.
  Nothing is ever selected automatically from it; only a path with the
  shape select_project itself records is ever echoed, and deleting the
  file clears it.
- the project's AI coder name setting (`qualcoder_mcp.json` inside the
  `.qda` project folder): the coder name or names you have chosen for
  this project's AI writes, when each was set, an optional note you
  typed (for example the host and model version), and the name declared
  in the host's server configuration at the time. It travels with the
  project folder, its backups and its workspace copies, and a restore
  rolls it back with the rest of the folder. It is returned into the
  conversation by get_current_project, select_project and
  set_project_ai_coder_name, and the current name appears on every write
  result; treat the note like any other project text the model can read.
  QualCoder never reads or writes this file. Deleting it makes the next
  AI write ask for the name again.

Paging cursors (the `c1.` tokens the search and segment tools return)
are not stored anywhere: they are handed to the model in a result and
travel only inside the conversation. What they encode is a position, a
file name, character offsets, a count, a fingerprint of the arguments,
and the modification time and byte size of `data.qda` at the moment the
cursor was minted, which is the heuristic that lets a later page say the
project may have changed under it; never file text, memo text, anything
about a coder, or the project's path. A cursor is base64, not
encryption: anyone the conversation reaches can read those values, and
`list_available_projects` already reports the same project's path, size
and modification time in plain form.

The `returned_so_far` figure in a paged result is carried BY the cursor,
so it is as trustworthy as the cursor the caller handed back and no
more: it counts what earlier pages said they returned, not what this
server has verified. It is bounded on the way in, so a tampered cursor
cannot put an arbitrary number in front of you, and `returned` (this
page) and `has_more` are computed here on every page.

What leaves your machine: **only what tools return into the
conversation**, but for qualitative research, that can be the most
sensitive content you hold.

## Keeping notes private from the AI: the '#####' memo convention

QualCoder 4.0 introduces a marker for memos: everything from the first
`#####` onward is a private zone that its built-in AI never sees. This
server honours the same convention, so a project touched by both tools
keeps the same promise:

- **Reads**: every tool and resource that returns memo content (code,
  category, file, case, attribute-type and coding memos, annotations,
  journal entries, the project memo) returns only the text before the first
  `#####`. The strip is silent: results do not flag that anything was
  held back, and memo searches neither match nor preview the private
  zone.
- **Writes**: memo-writing tools (set_memo, update_annotation, and the
  provenance notes merge_codes and merge_category append) replace only
  the public text. An existing private zone survives every memo write
  verbatim, and a `#####` in AI-supplied text is not written (code and
  category names and coder names copied into provenance notes are
  neutralised too), so the AI can never create, read, replace, or
  delete a private zone through a memo write.
- **Whole-row deletes** are the one qualification to that sentence.
  Tools that remove an entire row remove any private note on it
  together with the row: delete_coding and delete_annotation (single
  rows), and the cascades of delete_code, delete_category, merge_codes
  (duplicate codings it discards; on pre-v16 schemas the merged code's
  own memo) and merge_category (when merging to the top level, or on
  pre-v16 schemas). By owner ruling, three rules limit that:
  - delete_coding and delete_annotation refuse a row whose memo carries
    a private note unless the caller passes
    `confirm_private_note_deletion=true`, and for such a row a backup
    is always taken first, even when `create_backup=false` was asked.
    The refusal says only that a private note exists on that row; it
    never quotes, counts or characterises it.
  - The cascades (delete_code, delete_category, merge_codes,
    merge_category) require a preview token, always back up first, and
    their preview reports how many rows carrying a private note the
    operation would remove, as a count only.
  - Deliberate disclosure: because the refusal and the forced backup
    trigger only on rows that carry a private note, they reveal that a
    private note EXISTS on that row (never its content). The owner
    accepts this trade so that a private note is never destroyed
    without an explicit decision, and never without a backup.
- **The exception, deliberately**: exported FILES (the REFI-QDA
  `.qdpx` and codebook files, and the coded-segments report file
  written by export_coded_segments_report) keep memos in full, private
  zone included, because QualCoder's own exports do and export parity
  governs. The export tools say so in their descriptions.
  export_code_report, despite its name, returns JSON into the
  conversation rather than writing a file, so it strips like every
  other read. Treat exported files with the same care as the project
  itself.

The private zone stays in your project database on disk; this
convention controls only what enters the AI conversation. The QualCoder
4.0 behaviour described in this section and the next two was verified
against QualCoder master at commit 9bddf17 (pulled 2026-08-25, when 4.0
was in beta); README.md and CHANGELOG.md carry the same pin. The coder
visibility section was also verified against the 3.8.2 tag, which
already creates the `coder_names` table, its `visibility` column and
the four views (schema v14).

## Attribution: the AI coder name is yours to choose

Every row this server writes carries one coder name, so AI work stays
distinguishable from yours in QualCoder. AI rows are never written under
a name the model chose by itself: the name is set per project by you,
and the model can only ask. The first write that needs a name stops and
asks; your answer is stored with the project and reported back by the
project reads. The host's `QUALCODER_MCP_AI_CODER_NAME` setting declares
a name, which is offered as a quick pick and checked for conflicts; it
never attributes a row on its own. A name that belongs to a person (the
project's own coder name) is refused, and the `owner` argument of
`apply_codings` and `import_text_file` can no longer be used to write
rows under someone else's name.

## Comparing coders

`compare_coders` reports how much two named coders' text coding agrees.
Its result carries counts, percentages and two agreement coefficients,
and nothing else about the coding: no coded text, no memo, no character
positions, no file paths. On a project that hides coders, naming a hidden
coder is refused unless you pass `allow_hidden_coder=true`, and the
refusal says only that a named coder is hidden, never which of the two,
and never how many coders are hidden; the lists of coders in its other
error messages name visible coders only and disclose the rest as a
count. With `allow_hidden_coder=true` those lists do name hidden coders,
because the override is what makes them eligible, and the list then says
so instead of calling them visible. If the project's coder-visibility
table cannot be read at all, the tool computes nothing and says so,
rather than treating every coder as visible.
The log lines it writes carry the number of codes and files, never a
coder's name.

## Coder visibility: reading and writing what the user sees

QualCoder lets a project hide individual coders' work (a per-coder
visibility setting stored in the project database). It is not a 4.0
feature: QualCoder 3.8.2 and 4.0, schema v14 and later, create the
table, the column and the views, and this server detects them by
probing the project database rather than by any version string, so the
behaviour below follows the capability wherever it is present. When a
project has the coder-visibility capability:

- **Reads** go through QualCoder's own visibility views by default, so
  coded segments, coded-text searches, the annotation matches of memo
  searches, the file view with its codings and annotations, code
  detail counts, frequencies, co-occurrence, matrices and the
  codes-by-case and cases-by-code listings reflect what the user sees
  in QualCoder.
  Results disclose when hidden-coder filtering shaped them as a COUNT
  of hidden coders, never their names. An explicit `coder` argument
  reads that coder's rows from the full data instead, the same
  override QualCoder's own AI uses.
- **Writes that target an existing row by id** (delete_coding,
  update_annotation, delete_annotation, and set_memo on a coding) can
  reach a hidden coder's row, as QualCoder's own AI server can. They
  REFUSE unless the caller passes `allow_hidden_coder=true`; the
  refusal says only that the row belongs to a coder currently hidden
  in QualCoder, never who or how many. With the override, the result
  echoes ids only (as QualCoder's AI server does; update_annotation
  also echoes back the public note text the AI itself just supplied),
  never the hidden coder's name, code, span or text. The token-gated
  cascades (delete_code, delete_category, merge_codes, merge_category)
  report in their preview how many affected codings belong to hidden
  coders, as a count, and name every OTHER owner whose codings the
  operation would remove, so the researcher can see whose work is at
  stake; a hidden coder is never named there, and the owner of a code or
  category row being removed is reported as "(hidden coder)" when that
  coder is hidden. Executing a cascade that would remove a hidden
  coder's codings requires an explicit allow_hidden_coder=true. If the visibility state cannot be read (the view exists
  but does not answer), these tools return an error and change nothing,
  with or without the override; they never assume a row is visible, and
  the cascade previews return an error rather than an undercount.
  - Deliberate disclosure: because the refusal fires only on a hidden
    coder's row and changes nothing, it confirms that a given coding or
    annotation id belongs to a hidden coder (never whose, and never how
    many coders are hidden), and an id can be tested this way without a
    write or a backup. The owner accepts this trade so that a hidden
    coder's work is never changed without an explicit decision.
  - `apply_codings` skips an approved suggestion whose identical coding
    (same code, file, span and coder) already exists and reports its
    coding id. The check reads the base table, because the unique
    constraint lives there; so if the AI coder name itself is hidden
    in QualCoder, the result reveals that one such row exists (its id
    only, never its memo or anything about other hidden coders).
- **`pseudonymise_source` and hidden coders.** Rewriting a file moves
  every coder's rows with the text, hidden coders' included, and the
  preview reports what the run would do to a hidden coder's rows as
  counts (`shifted`, `substituted`, `resized`, `snapped`, `deleted`),
  never names. Which of them need `allow_hidden_coder=true` is the
  owner's ruling X1 as refined on 2026-09-15: a coding that covered the
  name and now covers the pseudonym needs no override, whatever the two
  lengths, and neither does a pure position shift, because neither
  changes a coding decision; a coding that grew to swallow a pseudonym,
  one that contained a name and changed length with it, or one that
  would be deleted (which only the `qualcoder_edit_parity` policy does)
  requires the override. The refusal names neither the coder nor a
  count. A row whose stored end lies past the end of the text is
  clamped first and counted as a resize, never carried through the
  exemption.
- Codes, categories, files, cases and journal entries have no
  per-coder visibility in QualCoder; their owner columns are read as
  before.
- **When who is hidden cannot be determined at all**, no coder is
  named. On a project whose visibility capability is present but whose
  `coder_names` table does not answer (schema drift, damaged pages, a
  concurrent QualCoder rebuilding it), a decision about who is hidden
  cannot be made, and every tool that would have made one treats
  unknown as hidden rather than as visible: the coder comparison and
  the AI coder name setter refuse and change nothing, the frequencies
  export names no coder in its result and says why, the case-only
  warning drops the other spelling, and the owner of a code or
  category row being removed is reported as "(hidden coder)". The
  exported FILE is never affected by this: it carries every coder's
  counts, for parity with QualCoder's own report.
- **When the capability arrives while this server is connected.**
  QualCoder creates the visibility column and its views when it opens a
  project, which can be after this server connected to it. Every
  decision that puts a coder's NAME into a result re-reads the
  declaration from the project at the time it is made: the
  pseudonymisation preview's owner breakdown and hidden-row counts, the
  cascade previews' `by_owner` and `discarded_by_owner` lists and their
  masked row owner, the coder comparison's refusal and its hidden
  count, the frequencies export's coder list and the AI coder name
  setter. So a coder hidden after this server connected is treated as
  hidden by all of them. That re-read is one way: a declaration that
  was there when the connection opened is never withdrawn by it,
  because a column that disappears under a live connection is damage
  or a concurrent rebuild, and the answer to those is the "cannot be
  determined" posture above; and if the declaration itself cannot be
  read, the answer is the same posture rather than "nobody is hidden".
  What is NOT re-read is which table each READ goes to. That is settled
  when the connection opens, so on a project that gained the capability
  afterwards the read tools (coded segments, searches, the file view,
  frequencies and the rest of the list above) go to the base tables
  until the project is selected again, and can return a hidden coder's
  row with its owner; the hidden-coder count those results disclose
  keys on the same connect-time answer, so it does not claim a filter
  that was not applied. Reopening the project (`select_project`, or
  restarting the server) settles it, and so does any write that opens
  the database, because such a write opens a fresh connection
  (`prune_backups` opens no fresh project connection and settles nothing).
  If you hide a coder in QualCoder
  while a conversation is in progress, re-select the project before
  relying on what the read tools return.

Projects without the coder-visibility capability (schemas older than
v14) are unaffected.

## Backups, project copies, and the `ai_data/` folder

QualCoder 4.0 keeps its AI state in `<project>/ai_data/`: the prompt
library (`ai_prompts/`, `ai_prompts.yaml`) and the AI chat history
(`chat_history.sqlite`) are user data that cannot be regenerated,
while `search.sqlite` is a rebuildable search index. Be aware that
**`ai_data/search.sqlite` contains a full plaintext copy of every text
source in the project** (QualCoder chunks source fulltext into it for
retrieval), which matters to anyone sharing or syncing project
folders.

This server never writes into `ai_data/` (it is QualCoder's own
territory). Its backups and workspace copies include `ai_data/` whole,
minus exactly the files QualCoder 4.0's own backups skip
(`search.sqlite`, `search.sqlite-*`, `*.sqlite-shm`, `*.sqlite-wal` and
`*.sqlite-journal`) and, in addition, any `*.lock` file (QualCoder 3.8's
backups skipped those too, and a copied lock file would make QualCoder
report the copy as not properly closed). That mirrors upstream behaviour, keeps the
non-regenerable prompt library and chat history safe in every backup,
and avoids multiplying plaintext copies of your sources across backup
folders. A restored or copied project without `search.sqlite` is
normal: QualCoder rebuilds it on project open.

Two further rules touch files on your disk:

- **Symlinks.** Unlike QualCoder's own backups, this server's backups
  and workspace copies do not follow a symlink that points outside the
  project folder, or that dangles: such entries are skipped, and the
  result reports how many (and which, up to twenty names) were
  skipped, so a shared or
  untrusted project folder cannot pull files from elsewhere on your
  disk into a backup. Symlinks that resolve inside the project are
  copied as before, with one exception: a symlink loop (a link that
  points back into a folder the copy is already inside, such as
  `documents/up -> ..` or two folders linking to each other) is
  skipped and reported the same way, because following it would nest
  the whole project into itself many times over; QualCoder's own backup
  fails on such a project. This is a deliberate, owner-approved
  deviation from QualCoder's save_backup, which copies whatever a link
  points to. A copy that fails part-way is removed rather than left
  behind as a half-complete "backup". On `pseudonymise_source`'s result
  the name of a skipped symlink is withheld where a reader of it would
  see a name from the mapping, the count kept; and the log line that
  reports a skipped symlink carries the count and the reason, never the
  path, because MCP hosts keep the server's log on disk.
- **Process listing.** To warn when a QualCoder 4.0 window appears to
  have a project open (4.0 writes no lock file), every tool that
  reports a `qualcoder_gui_signals` field (today: select_project,
  get_current_project, analyze_for_coding and the restore_backup
  preview, which is the call without a preview_token) also looks at the
  list of processes running on this machine (`ps` or `tasklist`, or
  psutil when installed). The listing is filtered in memory for
  process names and command lines that mention QualCoder (this
  server's own name is blanked out first) and only the NUMBER of
  matches is reported into the conversation; process names, command
  lines and other users' processes never leave the server, the
  filtered matches are held in memory for at most five seconds so that
  back-to-back calls do not rescan, and nothing from the list is
  stored on disk. The other signals in that field come from the
  project folder alone: whether `data.qda` has a write sidecar
  (`-journal`, `-wal` or `-shm`), and whether
  `ai_data/search.sqlite-wal`, `ai_data/search.sqlite-shm` or
  `ai_data/chat_history.sqlite` exist and how recently they were
  modified; only presence and timestamps are read, never contents.
  This is a heuristic: it can miss an open window (an idle 4.0 window
  with no recent AI activity leaves no file trace, so only the process
  scan can see it) and it can count an unrelated process whose command
  line mentions QualCoder.

## Your governance options, from default to fully local (Experimental)

Which terms govern the AI processing is decided by the host you run and
the account you sign into, not by this server. Four rungs, each with
what changes and what to check. Discipline note: we quote official
pages verbatim with their URLs and never characterise terms in our own
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
  > Services, including training our models, unless you opt out of
  > training through your account settings"
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
quoted 2026-08-17; the page renders the items as a bulleted list,
joined here with semicolons) lists what the consumer training changes
do NOT touch:

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

- Feedback mechanisms, safety flagging, and opt-in programmes can pierce
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

Your participants may have consented to *you* analysing their data;
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
  *processor*, which usually requires an institution-level
  data-processing agreement and a valid transfer safeguard (see the next
  section), not something an individual researcher can arrange alone.

For what Anthropic does with conversation content (retention,
processing, and how terms differ between consumer plans, the API, and
enterprise offerings), consult **Anthropic's own privacy
documentation** for the current terms:
<https://www.anthropic.com/privacy>. Those terms vary by product and
change over time; this document deliberately does not characterise
them.

## Before you use real participant data, check these

These are the questions your ethics committee or Data Protection Officer
will ask, and the summary above depends on them:

- **Your Claude plan's terms differ, and they matter.** For *your* account,
  verify whether inputs (a) may be used to train or improve models,
  (b) how long they are retained, and (c) whether they can be reviewed by
  people. These differ materially between consumer plans (Free/Pro) and
  Team/Enterprise/API terms. Inputs being used for model training would
  almost never be covered by existing participant consent; an ethics
  board asks this first.
- **Controller / processor, and a written agreement.** Your institution
  is normally the data controller and Anthropic a processor. UK/EU GDPR
  (Art. 28) then requires a written data-processing agreement, and a
  UK/EU→US transfer needs a valid safeguard (UK IDTA, SCCs, or an
  adequacy/data-bridge mechanism). A personal or consumer account almost
  certainly has **no such agreement**, so this is an institution-level
  decision you cannot clear alone.
- **Special-category data.** Interviews routinely carry health, sexuality,
  religion, ethnicity, political opinion and similar (often disclosed
  incidentally), which has a higher legal bar (GDPR Art. 9).
- **Consent is not compliance.** Participant consent to AI processing
  addresses the ethics limb; it does not by itself provide your lawful
  basis, your transfer safeguard, or the processing agreement.
- **You cannot claw it back.** Content already sent generally cannot be
  retracted, which can make it impossible to honour a participant's
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
  phrasing). Treat pseudonymisation as risk-reduction only; synthetic or
  genuinely anonymised data is the safe path for experimentation.
- **Pseudonymising a project does not empty it of names.** The v0.12
  `pseudonymise_source` tool rewrites the stored text of the text
  sources you choose and moves every coding with it. What it does NOT
  touch, and where the names therefore stay, is stated by the tool's own
  preview as counts, and repeated here because it decides what you may
  send afterwards:
  - **The backup.** Every run copies the whole project first, and that
    copy holds the text as it was, real names included. Backups sit
    beside the project until you remove them; `list_backups` shows them
    and `prune_backups` removes this server's own. A project you are
    about to share is not pseudonymised while its backups are beside it.
  - **`pseudonyms.json`**, if you keep one. It is QualCoder's own
    import-time list and it is the reverse key in plain text at the
    project root, so it travels into every backup either tool makes.
    This server never writes it and never deletes it. QualCoder's own
    guidance is to remove it and store it securely once the import is
    done (`manage_files.py` at the 9bddf17 pin), and that applies here
    too. `speakers.json` and `speaker_regex.json` can hold names as
    well; the preview reports whether they are present and never reads
    them.
  - **Memos (twelve fields, the audio/video and image coding memos
    included), journal entries and their names, case names, file names,
    code names, category names, attribute-type names and attribute
    values.** Scanned and counted, never rewritten. The count is in the
    preview's `residue` block, and a name that occurs only in a
    `#####` private note is neither read nor counted. Those counts are a
    heuristic that reads wider than the rewrite does: the rewrite
    replaces whole words only, as QualCoder's own import does, while
    the count is of anything a person reading the label or the memo
    would see, including inside a longer word (`Thomas_P01`) and in any
    letter case, compared after Unicode compatibility normalisation with
    invisible characters (a soft hyphen, a zero-width space) removed.
    So a label the count reports is not always one the rewrite would
    have changed, and that is the safe direction for a report whose
    job is to tell you where the names remain. What the count does not
    reach, and the preview says so: a look-alike letter from another
    script (a Cyrillic "о" for a Latin "o") is a different letter to
    the comparison, and is out of scope.
  - **QualCoder 4.0's `ai_data/` folder.** Its chat history may quote the
    previous text and its search index still holds it until QualCoder
    reopens the project and re-indexes. This server never reads or
    writes anything in there.
  - **This server's own session files** in `~/.qualcoder_mcp/sessions/`.
    A coding session records the excerpt each suggestion refers to, so a
    session made before a run keeps the pre-pseudonymisation text on
    disk. The run lists the affected sessions and never deletes one;
    `delete_coding_session` is yours to call.
  - The run manifest in `~/.qualcoder_mcp/pseudonymisation/` and the
    journal entry inside the project carry pseudonyms, counts and row
    ids only, never an original name. That covers the names of things
    as well as the names in the mapping: a file called
    `Thomas_interview.txt`, a project folder called `Thomas study.qda`
    and the backup folder derived from it are all withheld from those
    two records, which then identify the file by its id and the run by
    its token binding. The test is applied to the whole path, so if any
    folder above the project happens to contain one of the names, the
    manifest records `paths_withheld` instead of the project path and
    the backup path. That errs towards recording less, and `token_bind`
    still identifies the run and the project. The same test is
    normalised to Unicode NFKC with invisible characters removed, so a
    fullwidth spelling, or a soft hyphen or zero-width space inside a
    name, is detected by it; a look-alike letter from another script is
    not, and neither the test nor the rewrite matches such a spelling.
    The manifest's `token_bind` and its `mapping_hmac_sha256` are both
    keyed with the per-user token secret rather than plain digests, so
    neither confirms a guessed name to anyone who holds the manifest or
    the preview without also holding that secret.
  - **The preview's own reply.** On the `use_project_pseudonyms` path
    the mapping is the researcher's own reverse key and the model never
    supplied it, so no diagnostic and no refusal quotes a name from it
    and `include_context` returns nothing at all. Two things are still
    returned as they stand, because a preview whose files cannot be
    named cannot be relayed: the project path and each file's own name,
    either of which can itself contain one of those names. The tool's
    description says so.
- **Only open projects whose consent covers third-party processing.**
- **Consider which files you let the AI read.** Tools read only what is
  asked for: a session that never touches file 7 never transmits
  file 7's text.
- **Consult your institution's DPO or ethics board** if you are unsure,
  before the analysis, not after.
- Remember that the server's safety features (read-only default,
  automatic local backups, refuse-while-QualCoder-is-open through the
  lock file QualCoder 3.x writes, and for
  QualCoder 4.0 a best-effort check of this machine's process list that
  reports only a count, never names or command lines) protect your
  project's **integrity on disk**; they do not change what leaves the
  machine through the conversation.

## Questions

Questions about this document belong in
[GitHub Issues](https://github.com/nicotem/qualcoder_mcp/issues) like
everything else (see [SUPPORT.md](SUPPORT.md)), and please do not paste
participant data into an issue either.
