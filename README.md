# Qualcoder MCP Server

A Model Context Protocol (MCP) server that connects an MCP host (Claude Desktop, Claude Code, LM Studio, and others) to [Qualcoder](https://github.com/ccbogel/QualCoder), enabling AI-assisted qualitative data analysis.

## What is this?

This MCP server lets an AI assistant directly access and analyse your Qualcoder projects. Claude Desktop is the primary worked example throughout these docs, but any MCP host works (see "Choosing your AI host" below). The assistant can:

- 📊 Read your codes, categories, and coding structure
- 📝 Access coded text segments and original source documents
- 📖 **Analyse complete transcripts with coding context**
- 🔍 Search through your qualitative data
- 📈 Generate coding frequency reports
- 💭 Analyse themes and patterns
- 🔗 **Discover co-occurrence patterns between codes**
- 📋 Compare codes and cases
- 👥 **Query by demographics/attributes** (age, gender, etc.)
- 🎯 **Create case-code matrices for comparative analysis**
- 🗒️ Search through memos and annotations
- 🤖 **AI-assisted coding**: suggest → review → approve → apply, so nothing is written until you say so
- 🏷️ **Codebook editing**: create, rename, recolour, merge, move, and delete codes and categories
- 💾 **Memo & journal writing**: annotate codes, files, codings, and cases; keep a research journal
- ↩️ **Undo & restore**: delete a coding, list backups, and restore a whole project to an earlier state
- 📥 **Import transcripts**, link files to cases, and **rename cases and files** the way QualCoder's Manage Cases and Manage Files do (`rename_case`, `rename_file`)
- 🔄 **REFI-QDA export** (.qdpx) for interchange with NVivo, ATLAS.ti, and MAXQDA
- 📤 **Report exports**: codebook, coded segments, code frequencies and case-code matrix as CSV, txt or Markdown files
- 🕵️ **Pseudonymisation that keeps the coding** (`pseudonymise_source`): replace the names you list, as whole words, in the stored text of one text source per call, moving every coding, annotation and case link with the text; a preview and a residue report come first, a mandatory backup is taken, and what the tool does not rewrite is counted rather than left to be discovered, and the names left in every file's text are counted, both readings; with `rewrite_memos` the public part of every note and journal entry is rewritten too, and a mapping you type must be saved into the project's own `pseudonyms.json` or attested as kept before a run goes ahead
- ⚖️ **Coder comparison** (`compare_coders`): per-code agreement between two coders, with QualCoder's own coefficient and Cohen's kappa side by side
- 🤝 **QualCoder 4.0 conventions**: `#####` private memo sections are never shown to the AI, reads follow QualCoder's per-coder visibility (on projects with that capability: QualCoder 3.8.2 and 4.0, schema v14 and later), and AI work is written under one coder name that you choose per project (see "Working alongside QualCoder 4.0" below)

You can work with read-only analysis OR use write-enabled tools. The database is opened read-only by default; every write is preceded by an automatic backup, verified against QualCoder's format, and refused while a released QualCoder version (3.x) has the project open, which its lock file signals. QualCoder 4.0 (the 4.0-Beta pre-release) writes no lock file, so for it the server can only warn on best-effort heuristics; never write while any QualCoder window has the same project open (see "Supported QualCoder versions" below).

## Support & Feedback

This is an experimental alpha built by one researcher. Feedback, bug
reports, and feature ideas are genuinely wanted and actively shape what
gets built next.

**Everything goes through [GitHub Issues](https://github.com/nicotem/qualcoder_mcp/issues)**:
bug reports, questions, and feature requests alike. Issues are public
and searchable, so every answer helps the next researcher who hits the
same thing.

**Please don't email support requests.** The author's email address in
the package metadata is an authorship signature, not a support
channel; support requests sent by email will not receive a
reply. GitHub Issues is where everything is read and tracked.

See [SUPPORT.md](https://github.com/nicotem/qualcoder_mcp/blob/main/SUPPORT.md) for the full policy.

## Data Flow & Privacy: read this before using research data

The server runs entirely on your machine and adds no telemetry, no
analytics, and no cloud path of its own. **But everything a tool
returns (coded segments, interview excerpts, file contents, memos,
frequencies) enters your AI conversation, and conversation
content is transmitted to whichever AI provider your host uses**
(Anthropic for Claude hosts; no external provider at all with a fully
local host) and processed like any other chat/API content. Reading a
transcript through this tool sends the returned portions of that
transcript to that provider. Backups, exports and session files stay
local. Which provider, and under which terms, is decided by your host
and account, not by this server: see "Choosing your AI host" below.

Your participants may not have consented to third-party AI processing.
It is the researcher's responsibility to check what this flow means
for your consent language, data-management plan, ethics/IRB approvals
and (for EU/UK researchers) GDPR position. This tool makes the flow
explicit precisely so you can make that decision; many AI
integrations don't.

**Read [PRIVACY.md](https://github.com/nicotem/qualcoder_mcp/blob/main/PRIVACY.md)** for the full disclosure: what to
check with your Claude plan and your institution's DPO, why pseudonymised
data is *not* automatically safe to send, and practical mitigations.

## Choosing your AI host: data-governance options (Experimental)

This server is host-agnostic stdio MCP. Which AI processes your data,
and under which terms, is decided by the host you run and the account
you sign into, not by this server. The terms attach to the account and
product line, not to the client application. Three routes, from easiest
to most private:

| Route | What it means | Where to read more |
|---|---|---|
| **Claude consumer plans** (claude.ai, Claude Desktop, Claude Code with a Free/Pro/Max login) | The easiest path. Check your own Model Improvement setting at [claude.ai/settings/data-privacy-controls](https://claude.ai/settings/data-privacy-controls); do not assume a default. | [PRIVACY.md](https://github.com/nicotem/qualcoder_mcp/blob/main/PRIVACY.md), rung 1 |
| **Anthropic commercial-terms routes** (Claude Code with a Console API key; Team/Enterprise accounts) | Same Claude capability; different terms attach to the traffic. Institutions should prefer organisational accounts. | [PRIVACY.md](https://github.com/nicotem/qualcoder_mcp/blob/main/PRIVACY.md), rungs 2 and 3; [INSTALL.md API-key recipe](https://github.com/nicotem/qualcoder_mcp/blob/main/INSTALL.md#claude-code-with-an-anthropic-api-key-experimental) |
| **Fully local models** (LM Studio and similar MCP hosts) | Participant data is never sent to any AI provider. The trade is capability: local models are markedly weaker on many-tool work, and we have not yet evaluated any local model with this server (evaluation pending; that is why this is Experimental). Requires the reduced core toolset. | [PRIVACY.md](https://github.com/nicotem/qualcoder_mcp/blob/main/PRIVACY.md), rung 4; [INSTALL.md LM Studio recipe](https://github.com/nicotem/qualcoder_mcp/blob/main/INSTALL.md#lm-studio-fully-local-experimental) |

The multi-host support (the core toolset and the two recipes) is
**Experimental**: written from official documentation, functionally
tested at the server level, but not yet exercised end to end on every
host and not capability-evaluated on local models.

## Prerequisites

- **macOS** (or Linux/Windows with appropriate paths)
- **Python 3.10 or higher**
- **An MCP host**: Claude Desktop is the most common ([download here](https://claude.ai/download)); see "Choosing your AI host" above for alternatives
- **Qualcoder** with at least one project created ([download here](https://github.com/ccbogel/QualCoder))

## Supported QualCoder versions

> qualcoder-mcp is ground-truthed against QualCoder 3.8.2, the latest stable
> release (project schema v14), and additionally verified against the QualCoder
> 4.0-Beta pre-release (version string "QualCoder 4.0 Beta", built from the
> QualCoder development tree) at commit `9bddf17`, whose projects use schema v17. Project schemas v14 through v17 are
> supported for reading and writing. Support is determined by inspecting the
> project database itself (capability probes), not by version numbers, so
> projects migrated by either QualCoder version work interchangeably.
>
> Because QualCoder 4.0 is a pre-release, its behaviour may change before the
> final release. Claims about 4.0 compatibility are valid as of commit `9bddf17`
> (2026-08-25) and will be re-verified against the final release. One known
> limitation: released
> QualCoder versions signal "project open" through a lock file, which qualcoder-mcp
> honours; the 4.0-Beta pre-release builds no longer use a lock file, so qualcoder-mcp
> falls back to best-effort heuristics there (reported as
> `qualcoder_gui_signals` by `select_project`, `get_current_project`,
> `analyze_for_coding` and the `restore_backup` preview:
> database write sidecars, recent AI search-index and chat-history activity,
> and a best-effort process scan that reports only how many running processes
> look like QualCoder, never their names or command lines). The file-based signals are traces of recent
> activity, never proof of an open window (an idle 4.0 window with no recent AI
> activity leaves no file trace at all), so heuristics can miss an open window:
> do not run qualcoder-mcp writes while any QualCoder window has the same
> project open.
>
> Two facts about how the tools relate. QualCoder 4.0 ships its own embedded
> AI assistant, built on an internal MCP server that serves only the GUI (it
> has no external transport); qualcoder-mcp is the external MCP surface for
> QualCoder projects, for Claude-family and other MCP hosts, automation, and
> headless work. And an open QualCoder 4.0 window will not display changes
> written by external tools (its views refresh through an internal event bus
> only), so changes written by qualcoder-mcp appear after the project is
> closed and reopened in QualCoder.

Sub-codes (a code nested under another code, schema v16 and newer) are
fully supported: creating them, moving and merging without hierarchy
loss, branch-aware deletion, and nesting-aware listings, reports,
codebook and REFI-QDA exports. Projects newer than schema v17 refuse
writes until this server has been verified against them; setting
`QUALCODER_MCP_ALLOW_UNKNOWN_SCHEMA=1` in the server environment lets
writes proceed at your own risk, and every write result then carries a
warning.

## Installation

### Recommended: install from PyPI

The simplest install is a plain pip install into a virtual
environment, with no git and no source tree:

```bash
python3 -m venv ~/qualcoder-mcp-venv
~/qualcoder-mcp-venv/bin/pip install qualcoder-mcp
```

Or, if you use [pipx](https://pipx.pypa.io) or [uv](https://docs.astral.sh/uv/),
one command gives you an isolated install with the `qualcoder-mcp`
command on your PATH:

```bash
pipx install qualcoder-mcp
# or
uv tool install qualcoder-mcp
```

Either way you end up with a **`qualcoder-mcp` console command**. Find
its absolute path with `which qualcoder-mcp` (you'll need it for the
client configuration below).

### Contributor install (from source)

Use this path if you want to modify the code or run the test suite.

**Step 1: Clone or Download This Repository**

```bash
cd ~/Documents  # or wherever you want to install
git clone https://github.com/nicotem/qualcoder_mcp.git
cd qualcoder_mcp
```

**Step 2: Create a Virtual Environment and Install**

```bash
# Create virtual environment
python3 -m venv venv

# Activate it
source venv/bin/activate  # On Mac/Linux
# or on Windows: venv\Scripts\activate

# Install the package (editable, with dev tools)
pip install -e ".[dev]"
```

### Step 3: Configure Claude Desktop

You have **two options** for configuring project access:

#### Option A: Dynamic Project Selection (Recommended for Multiple Projects)

If you work with multiple Qualcoder projects, this is the easiest approach - Claude will discover projects and let you switch between them.

**Configuration** (no project path needed).

With a **PyPI install** (pip/pipx/uv), point the client straight at the
installed `qualcoder-mcp` command, using the absolute path from
`which qualcoder-mcp`:

```json
{
  "mcpServers": {
    "qualcoder": {
      "command": "/Users/YOUR_USERNAME/qualcoder-mcp-venv/bin/qualcoder-mcp"
    }
  }
}
```

With a **source (git) install**:

```json
{
  "mcpServers": {
    "qualcoder": {
      "command": "/Users/YOUR_USERNAME/Documents/qualcoder_mcp/venv/bin/python",
      "args": ["-m", "qualcoder_mcp.server"]
    }
  }
}
```

**Replace**: `YOUR_USERNAME` with your actual Mac username (use an
absolute path; Claude Desktop does not inherit your shell's PATH)

**Usage**: After restarting Claude Desktop:
```
List my available Qualcoder projects
```
Then select one:
```
Select the "My Research Project" project
```

Switch projects anytime:
```
Switch to "Different Project"
```

See [`PROJECT_SELECTION_GUIDE.md`](https://github.com/nicotem/qualcoder_mcp/blob/main/PROJECT_SELECTION_GUIDE.md) for full details.

#### Option B: Fixed Project (Simpler for Single Project)

If you work with one main project, you can hardcode the path for instant access.

**Find Your Project Path**:
Your Qualcoder project is a **folder** with a `.qda` extension containing a `data.qda` database file. Typical locations:
- `~/Documents/QualCoder_projects/MyProject/MyProject.qda/` (folder)
- `~/QualCoder/ProjectName/ProjectName.qda/` (folder)

**Configuration** (PyPI install; for a source install use the
`venv/bin/python` + `-m qualcoder_mcp.server` form from Option A):

```json
{
  "mcpServers": {
    "qualcoder": {
      "command": "/Users/YOUR_USERNAME/qualcoder-mcp-venv/bin/qualcoder-mcp",
      "env": {
        "QUALCODER_PROJECT_PATH": "/Users/YOUR_USERNAME/Documents/QualCoder_projects/MyProject/MyProject.qda"
      }
    }
  }
}
```

**Replace**:
- `YOUR_USERNAME` - your actual Mac username
- the `command` path - the output of `which qualcoder-mcp` (or your source install's venv python)
- `/Users/YOUR_USERNAME/Documents/QualCoder_projects/MyProject/MyProject.qda` - path to your `.qda` project folder

**Editing the Config**:

1. Open Claude Desktop
2. Go to **Settings** (Claude > Settings)
3. Click the **Developer** tab
4. Click **Edit Config**
5. Paste your chosen configuration
6. Save and restart Claude Desktop

### Step 4: Restart Claude Desktop

After editing the configuration:
1. Quit Claude Desktop completely (Cmd+Q)
2. Reopen Claude Desktop

The MCP server should now be connected. You will see it listed in the MCP section of Claude Desktop's settings.

### Using with Claude Code (and other MCP clients)

The server speaks standard MCP over stdio, so **any MCP client works**;
Claude Desktop is simply the most common host. Researchers also run it
under Claude Code (including in editor side panels such as Obsidian's).

**Claude Code, one command** (PyPI install; for a source install
substitute `<repo>/venv/bin/python -m qualcoder_mcp.server`):

```bash
claude mcp add qualcoder -- qualcoder-mcp
```

**Or per-project** with a `.mcp.json` in the folder you run Claude Code
from:

```json
{
  "mcpServers": {
    "qualcoder": {
      "command": "qualcoder-mcp"
    }
  }
}
```

(Claude Code resolves commands on your shell PATH; if in doubt, use the
absolute path from `which qualcoder-mcp`.)

Both accept the same optional `env` block (`QUALCODER_PROJECT_PATH`,
`QUALCODER_MCP_AI_CODER_NAME`, `QUALCODER_MCP_TOOLSET`) as the Desktop
configurations above. Everything in this guide (the tools, the
review-first workflow, the safety gates) behaves identically in any
client.

### Choosing the AI coder name (attribution)

Every row this server writes (codings, annotations, journal entries,
imports, cases, codes, categories, attributes) is attributed to one
coder name, so AI work stays distinguishable from yours in QualCoder.
From v0.12 that name belongs to the PROJECT and it is yours to choose.
The first write that needs it stops and asks: the model relays the
question, you answer, and it calls

```
set_project_ai_coder_name("Qwen 3.8 6bit", note="LM Studio 0.4.22")
```

The answer is stored in `qualcoder_mcp.json` in the project folder,
beside `data.qda`, so it travels with backups, copies and a synced
folder, and two hosts talking to one project agree on it. Reads never
ask. You can change the name at any time with the same tool; earlier
rows keep the name they were written under, and the project remembers
the names it has used, which is what makes a later comparison between
two models possible. A model name is a good answer for exactly that
reason.

Quick picks if you have no preference: `AI Coding Assistant` (this
server's built-in default, and what every project coded with v0.11 and
earlier already holds) and `AI Agent`, the exact name QualCoder 4.0's
built-in assistant writes under, which groups this server's work and
the built-in assistant's under one coder in QualCoder's per-coder
visibility toggle, undo and reports.

`QUALCODER_MCP_AI_CODER_NAME` in the server's `env` block is now a
DECLARATION by this host, not an attribution:

```json
"env": {
  "QUALCODER_MCP_AI_CODER_NAME": "Qwen 3.8 6bit"
}
```

It offers that name as the first quick pick, which is the convenient
way to say "this host runs this model". It never writes a row by
itself: if it differs from the project's current name, the next write
asks which of the two to use rather than re-attributing anything, and
answering either way settles it for that host. Invalid values (empty,
longer than 80 characters, containing control or line-separator
characters, containing any invisible formatting character (Unicode
category Cf: every bidirectional control, every zero-width character;
the two exceptions are ZWNJ and ZWJ, which spell words in Persian and
Indic scripts), or containing the `#####` memo-privacy marker) stop the
server at startup with a clear error. The invisible ones are refused
because a name you cannot see is a name you cannot check: it renders
identically to yours in QualCoder's coder list, its visibility toggle
and its reports, and telling AI rows from yours is what the setting is
for.

The name cannot be your own QualCoder coder name (the project's
codername) or QualCoder's literal `default`: AI rows would then be
indistinguishable from a person's in QualCoder's coder lists,
visibility toggle, undo and reports, and mixed rows cannot be told
apart again later. Both are refused. A name that a QualCoder
visibility setting hides is refused too, unless you pass
`allow_hidden_coder=true`, because rows written under it would be
invisible in QualCoder and in this server's default reads.

Coder names are compared exactly, after trimming spaces, and never
case-insensitively: QualCoder stores them in a column with a binary
unique index, so `AI Agent` and `AI agent` really are two coders
there. (Code, category and case NAMES follow the opposite rule, which
is QualCoder 4.0 parity for those.)

The setting travels with the project folder: our backups and workspace
copies carry it, QualCoder's own `_BKUP_` backups carry it, and
`restore_backup` puts back whatever the backup held (the result says so
when the name changed). One case does not travel: after merging another
project into this one in QualCoder, the source's AI coder names appear as
owners on the rows that arrived, but they are not added to this project's
name history, because the merge copies rows and not settings.

**The `owner` argument is deprecated.** `apply_codings` and
`import_text_file` still accept `owner`, but it no longer chooses the
name: passing exactly the project's AI coder name is a no-op and any
other value is refused before any backup or write. A human coder's
name is never used for rows this server writes. It is kept in the
signature for one release cycle and planned for removal at v1.0. If
you want a file under your own name in QualCoder, import it in
QualCoder rather than through this server.


## Starting a project from the conversation (Experimental)

The server can create a new, empty QualCoder project, so a study can
begin in the conversation. It is off by default: add
`QUALCODER_MCP_TOOLSET=lifecycle` to the server's environment (INSTALL.md
shows where), which registers the full set of tools plus
`create_project`.

- **The format.** The project is made in QualCoder 4.0's format, exactly
  as 4.0's own New Project makes it (the folder `<name>.qda` with its
  four subfolders, and the database at schema v17), in one step: if
  anything fails, nothing committed is left, and what this call made is
  removed; if the process is killed part way, the folder it leaves (empty
  subfolders, an empty database and its journal) is recognised as such
  next time, and the name refused. Its "about" line reads
  `qualcoder-mcp <version> (QualCoder schema v17)`.
- **Where.** In the server's workspace, `~/Documents/Qualcoder MCP
  Projects`, unless you name an existing folder (give its full path, or
  one starting with `~`). Keep projects on a local disk that is not
  synced (iCloud, OneDrive, Dropbox): sync services can copy the
  database and its journal separately. On a Mac with iCloud's "Desktop
  & Documents Folders" switched on, `~/Documents`, and so the default
  workspace, is synced: name a folder outside it. On Windows
  where Documents has been moved (to OneDrive, say), the workspace may
  not be where QualCoder's Open dialog starts; the result gives the full
  path.
- **Your coder name.** The assistant asks for the coder name you use in
  QualCoder (Settings, Coder name), after every other check, so you are
  asked once. If you do not know it or do not use QualCoder yet, say so:
  the project is created, and the check that keeps the AI's codings
  apart from yours stays off until QualCoder records your name, the
  first time you open the project there. The server never reads
  QualCoder's settings file, which holds API keys.
- **Names.** A name already used in that folder is refused, never given
  a "_1", and so is one that differs from it only in letter case or
  accents (a project copied to macOS or Windows would otherwise merge
  with it). Names QualCoder cannot open (with `|`), names Windows cannot
  store, names holding `_backup_` or `_BKUP_`, and a name whose older
  backup folders sit in that folder are refused too, each with the
  reason. Short names, in folders near the top of the disk, travel
  better.
- **Opening it in QualCoder.** QualCoder 4.0 opens it without changing
  its format (Project, Open Project, then the folder), and without a
  message when you open it under the coder name you gave (under another
  name, it asks whether to keep yours or switch). QualCoder 3.8.2 opens it without any warning and keeps
  everything in it, but cannot show what 4.0 added: sub-codes appear
  there as ordinary codes, and the labels, arrows and memo notes on
  graphs do not appear. If the project is edited in 3.8.2, three things
  change without a message the next time 4.0 opens it: a sub-code moved
  into a category goes back under its parent code; the sub-codes of a
  code deleted in 3.8.2 become ordinary codes with no category; and a
  graph saved after another was deleted can show the deleted graph's
  memo notes. Work on such a project in QualCoder 4.0.
- **The project memo** starts empty, as QualCoder leaves it;
  `set_memo` with the target `project` writes it (research questions,
  methodology, participants), and QualCoder 4.0's own assistant reads it
  as the study's context. Text after a `#####` line stays private.

## Working alongside QualCoder 4.0

QualCoder 4.0's AI subsystem defines conventions that live in the
project itself. This server follows them, so a project touched by both
tools behaves coherently. Each feature below is detected by probing the
project database (tables, columns, views), never by version string;
pre-4.0 projects behave as before. Parity claims were verified against
QualCoder master at commit `9bddf17`.

**Private memo sections (`#####`).** Memo text from the first `#####`
marker onward is the researcher's private zone. Every tool and resource
that returns memo content (codes, categories, files, cases, codings,
annotations, journal entries, the project memo, attribute types, search
results) returns only the text before the marker, silently, and
`search_memos` and `search_files` match the public part only. Memo
writes (`set_memo`, `update_annotation`, the provenance notes that
merges add) replace only the public text and keep an existing private
section verbatim; a `#####` in AI-supplied text is never written.
Deleting a coding or annotation whose memo carries a private note is
refused unless `confirm_private_note_deletion=true`, and for such a row
a backup is always taken even with `create_backup=false`; the refusal
says only that a private note exists, never its content. One exception,
chosen for parity with QualCoder's own exports: exported files
(`export_refi_qda`, `export_codebook`, `export_coded_segments_report`)
carry full memos, marker and private section included, and those tools
say so. `export_code_report` returns into the conversation and strips.

**The novelty filter and coder visibility.** `exclude_code_ids` on
`search_files` and `search_coded_text` drops candidates that overlap a
coding of one of those codes in the same file, which is how you ask what
is not yet coded. The spans it excludes are the ones the caller can see:
on a project that hides coders, a hidden coder's codings do not suppress
a passage, so the filter cannot be used to find out that hidden work
exists. QualCoder's own search reads the full table there
(`ai_mcp_server.py:5228` at `9bddf17`); this is a deliberate difference.

**Coder visibility.** When a project with the coder-visibility
capability (QualCoder 3.8.2 and 4.0, schema v14 and later) hides some
coders' work, a setting stored in the project database, coded-segment
reads and
analytics (`get_coded_segments`, `search_coded_text`,
`get_coding_frequencies`, `find_cooccurring_codes`,
`get_case_code_matrix`, `get_codes_by_case`, `get_cases_by_code`, the
codings and annotations in `analyze_file_with_coding`, and the
annotation matches of `search_memos`) read what the user sees in
QualCoder by default and disclose how many coders are hidden, never
their names (with one stated exception, a project that gained the
capability after this server connected to it; PRIVACY.md's "Coder
visibility" section says what is and is not re-read). Passing an
explicit `coder` argument reads that coder's rows from the full data
instead. File exports keep reading the full data, as QualCoder's own
reports do: QualCoder's own coding report does the same in both pinned
builds (Reports > Coding reports in 3.8.2, Reports > Code retrieval in
4.0) and lists a hidden coder's segments, because it reads the base
`code_text` table (`report_codes.py:1712-1724` at `9bddf17`,
`:1504-1515` at the 3.8.2 tag); only the coding screen reads through
the visibility view.

> **QualCoder 3.8.2 and edit mode: an upstream caution, not this server's.**
> On the 3.8.2 line, leaving the coding view's edit mode after any change to
> the text deletes every coding that the edit leaves touching the new end of
> the file, and the undo cannot bring it back. `ed_update_codings` deletes any
> row whose new end is `>= len(text)`, evaluated against the text AFTER the
> edit, so this is wider than it sounds: **trimming the tail of a transcript
> destroys whichever coding is left nearest the cut, even one that was
> nowhere near the end before.** Deleting the last 193 characters of a
> 614-character file in the v0.12.1 acceptance run destroyed a coding at
> 400-421, which had sat 193 characters clear of the end; the 4.0 line kept it.
> Annotations and case links are unaffected, and leaving edit mode without
> changing anything is harmless. This happens whether or not a project has
> ever been through this server: the acceptance run reproduced it on a project
> the server never touched, which is how it is attributed. The QualCoder 4.0
> line has fixed it, clamping such a coding instead of deleting it. If you use
> edit mode on 3.8.2, know this regardless of `pseudonymise_source`.

Writes that target an existing row
by id (`delete_coding`, `update_annotation`, `delete_annotation`,
`set_memo` on a coding) refuse a hidden coder's row unless
`allow_hidden_coder=true`; with the override the result carries ids
only. The refusal confirms that the row belongs to a hidden coder
(never who, never how many), a trade PRIVACY.md states.

**Backups, workspace copies and `ai_data/`.** Backups and
`copy_project_to_workspace` copy the whole project tree including
`ai_data/` (the 4.0 prompt library and chat history are user data),
minus QualCoder's own backup ignore set (`search.sqlite`,
`search.sqlite-*`, `*.sqlite-shm`, `*.sqlite-wal`, `*.sqlite-journal`)
and lock files. `search.sqlite` is the regenerable AI search index and
holds a plaintext copy of every text source; QualCoder rebuilds it on
project open, so a restored project without one is normal. Unlike
QualCoder's backups, symlinks that point outside the project folder,
that dangle, or that loop back into a folder already being copied are
not followed; skipped entries are reported (`backup_skipped_symlinks`
on write results, `skipped_symlinks` on workspace copies,
`safety_backup_skipped_symlinks` on restores).

**Detecting an open 4.0 window.** 4.0 writes no lock file, so detection
is heuristic (see "Supported QualCoder versions" above), and an open
4.0 window will not display external writes until the project is
reopened there.

**Recovery hint.** If a host restarts the server mid-conversation
(observed with LM Studio), every "no project selected" error names the
last project used on this machine when it still exists, so one
`select_project` call recovers. The selection is never restored
automatically. The pointer lives in `~/.qualcoder_mcp/mru_project.json`.

[PRIVACY.md](https://github.com/nicotem/qualcoder_mcp/blob/main/PRIVACY.md)
describes these conventions in full, including what they disclose.

## Updating to a new version

Updates are manual: a new release does not install itself.

> **Upgrading from a pre-0.9 git install?** See
> ["Upgrading from an earlier (git) install"](https://github.com/nicotem/qualcoder_mcp/blob/main/INSTALL.md#upgrading-from-an-earlier-git-install)
> in INSTALL.md for how to move to the PyPI install (or stay on git),
> with the exact client-config change. Your projects and session
> files are untouched either way.

**PyPI install** (recommended path): one command, into the same
environment you installed with:

```bash
~/qualcoder-mcp-venv/bin/pip install --upgrade qualcoder-mcp
# pipx:  pipx upgrade qualcoder-mcp
# uv:    uv tool upgrade qualcoder-mcp
```

**Source (git) install**: update in three steps:

```bash
cd ~/Documents/qualcoder_mcp   # wherever you installed it
git pull                        # fetch the latest code
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -e .                # picks up any new dependencies
```

Then **fully quit and reopen your Claude client** (Claude Desktop: Cmd/Ctrl+Q then reopen; Claude Code: restart the session) so it relaunches the server with the new code. **New tools only appear after the client restart**: the client starts the server once per session, so an update takes effect on the next launch, not mid-conversation.

To check which version is installed, run the server with `--version` in the environment you installed into: `~/qualcoder-mcp-venv/bin/qualcoder-mcp --version` (PyPI venv), `qualcoder-mcp --version` after `pipx` or `uv tool` (both put the command on your PATH), or `venv/bin/python -m qualcoder_mcp.server --version` in a git clone. It prints `qualcoder-mcp` followed by the version and exits; version `0.13.0-alpha` shows as `0.13.0a0`, its normalised form. The server also reports its version to the host in the MCP handshake, but whether Claude can see and repeat it depends on the host, so asking Claude *"What version of the QualCoder server is running?"* is a convenience, not proof. You can also see the latest release and what changed on the [Releases page](https://github.com/nicotem/qualcoder_mcp/releases) and in [CHANGELOG.md](https://github.com/nicotem/qualcoder_mcp/blob/main/CHANGELOG.md).

Updates never touch your data: the server is code-only, so your
QualCoder projects and their backups stay exactly where they are.

## Usage

Once configured, you can interact with your Qualcoder data naturally in Claude Desktop. Here are some example prompts:

### Getting Started

```
Can you give me a summary of my Qualcoder project?
```

```
What codes do I have in my project?
```

```
List all the source files in my project
```

### Finding Files

```
Find files with 'paul' in the name
```

```
Search file content for 'workplace stress'
```

```
Search for files containing motivation (in both filenames and content)
```

### Analysing Themes

```
Show me all the text segments coded with "participant motivation"
```

```
What are the most frequently used codes in my project?
```

```
Search for segments containing the word "education"
```

### Deeper Analysis

```
Analyse the theme "workplace culture" and identify key patterns
```

```
Compare the codes "job satisfaction" and "work-life balance"
```

```
What are the main themes in case "Participant 5"?
```

### NEW: Rich Transcript Analysis

```
Analyse the interview transcript for participant 3, showing me both the coded segments and the full context. What does this participant say that relates to the Wisdom of the Crowds argument?
```

```
Review file ID 5 with all its coding. Help me understand how the participant discusses motivation throughout the entire interview.
```

### NEW: Demographic Analysis

```
Show me all participants over age 50
```

```
Which cases have education level "graduate"?
```

```
Find all interview files where the attribute "interview_type" is "focus_group"
```

### NEW: Co-occurrence & Pattern Discovery

```
What codes appear together with "workplace stress"?
```

```
Find patterns of co-occurring themes in the data
```

```
Which codes never appear with "job satisfaction"?
```

### NEW: Comparative Case Analysis

```
Create a case-code matrix showing which themes appear in which participants
```

```
Which participants mention "work-life balance"?
```

```
Show me all codes that appear in case "Participant 7"
```

### Searching

```
Search through my memos for notes about "methodology"
```

```
Find coded segments that mention "remote work" but only for the code "challenges"
```

## AI-Assisted Coding 🤖

Claude can help you code your qualitative data with a conversational approval workflow. You chat with Claude, review suggestions together, and directly write approved codings to your database.

### Conversational Workflow

**Important**: AI coding writes directly to the database. Always work on copies in the `~/Documents/Qualcoder MCP Projects/` workspace folder. Automatic backups are created before every write, and **writes are refused while a released QualCoder (3.x) has the project open**; close it there first. QualCoder 4.0 builds write no lock file, so for them the server can only warn on heuristics: make sure no QualCoder window has the project open before any write.

### Quick Start Example

**Step 1: Copy Project to Workspace**
```
Copy my project "Interview Study" to the workspace for AI coding
```

(the `copy_project_to_workspace` tool does this; then open the copy with `select_project`)

**Step 2: Analyse Files**
```
Analyse files 1-3 for WORKPLACE-STRESS and COPING-STRATEGIES codes
```

Claude will:
- Create an analysis session (`analyze_for_coding`)
- Examine the files
- Record its suggestions into the session (`record_suggestions`; every
  suggestion is verified against the file text before it is stored)
- Present suggestions with reasoning and confidence scores

**Step 3: Review in Chat**
```
Show me details about suggestion 1
```

Claude shows you:
- The text segment
- Which code and file
- Why it was selected (reasoning)
- Confidence score
- Surrounding context

**Step 4: Approve/Reject**
```
Approve suggestions 1, 2, and 5. Reject 3 and 4.
```

**Step 5: Apply to Database**
```
Apply the approved codings to the project
```

Claude will:
- Verify every approved suggestion against the project (right project,
  files/codes exist, text matches positions)
- Create automatic backup
- Write approved codings to database (all-or-nothing)
- Report success with coding IDs
- Then open the project in QualCoder to see the results (a QualCoder 4.0 window that was already open will not show them until the project is reopened)

**If something went wrong**: `delete_coding(coding_id)` removes a single coding;
`list_backups` + `restore_backup` roll the whole project back to a snapshot.

### Key Features

- **Conversational Review**: Discuss suggestions with Claude before applying
- **Confidence Scoring**: Each suggestion includes a 0.0-1.0 confidence score with reasoning
- **Session Persistence**: Resume work anytime, all sessions saved to disk
- **Automatic Backups**: Every write creates a timestamped backup first
- **Workspace Isolation**: Work on copies in dedicated workspace folder
- **Direct Database Writes**: No import/export step; codings are in the project the next time it is opened in QualCoder (an open QualCoder 4.0 window does not show external changes until the project is reopened)
- **Granular Control**: Approve/reject individual suggestions by GUID
- **Full Context**: See surrounding text for each suggestion
- **Verified Writes**: Suggestions are checked against the file text when
  recorded AND before writing; sessions only apply to the project they
  were created in
- **QualCoder-Aware**: Writes are refused while a released QualCoder (3.x)
  has the project open (its `project_in_use.lock` heartbeat is respected).
  QualCoder 4.0 builds write no lock file, so for them the server reports
  best-effort heuristics (`qualcoder_gui_signals`), re-verifies the file
  text inside the write transaction, and relies on you to make sure no
  window has the project open
- **Recovery Tools**: `delete_coding`, `list_backups`, `restore_backup`

### Workspace Safety

The AI coding workflow uses a workspace directory for safe modifications:

```
~/Documents/Qualcoder MCP Projects/
```

Never work on your original projects with AI coding! Always:
1. Copy project to workspace first
2. Let Claude work on the workspace copy
3. Review results in Qualcoder
4. If good, replace original OR keep both versions

For comprehensive workflow documentation, see [AI_CODING_WORKFLOW.md](https://github.com/nicotem/qualcoder_mcp/blob/main/AI_CODING_WORKFLOW.md).

## Available Resources

The MCP server exposes these resources (read-only data):

- `qualcoder://project/info` - Project metadata
- `qualcoder://codes/list` - All codes
- `qualcoder://categories/list` - Code categories
- `qualcoder://codes/{id}` - Specific code details
- `qualcoder://files/list` - All source files
- `qualcoder://files/{id}` - File content
- `qualcoder://cases/list` - All cases
- `qualcoder://cases/{id}` - Case details
- `qualcoder://journal` - Journal entries
- `qualcoder://guidance/methods` - Static methods notes: the grounding rules, the four-way methodological vocabulary (allow, allow_with_caveat, reframe_and_ask, refuse) and citations to the method literature QualCoder 4.0 ships prompts for; needs no project

## Available Tools

Claude can use these tools to analyse your data. The full toolset
(the default, `QUALCODER_MCP_TOOLSET=full`) registers 73 tools; the
argument lists below are abbreviated, and each tool's own description
carries the complete list.

> **Creating projects (Experimental, opt-in):** with
> `QUALCODER_MCP_TOOLSET=lifecycle` the server registers the full set
> plus `create_project`, 74 tools. Creating projects stays out of the
> default set so that researchers opt in to a tool that makes folders on
> their disk; it is not in `core` either. Measured as below, the
> `lifecycle` definitions run to about 175,000 characters, roughly 44k
> tokens.

> **Reduced toolset for local models (Experimental):** with
> `QUALCODER_MCP_TOOLSET=core` in the server's environment, only the
> 21-tool supervised coding set is registered: list_available_projects,
> select_project, get_current_project, get_project_summary,
> search_files, analyze_file_with_coding, search_coded_text,
> get_coded_segments, get_coding_frequencies, analyze_for_coding,
> record_suggestions, review_suggestions, edit_suggestion,
> update_suggestion_status, apply_codings, create_code, set_memo,
> set_project_ai_coder_name,
> copy_project_to_workspace, delete_coding, list_backups.
> Required for local models, optional elsewhere; unknown values fail
> loudly at startup. Measured for 0.14 (the
> serialised tool definitions: name, description and input schema, the
> same method as the CHANGELOG, under Python 3.13.5 with mcp 1.30.0, in
> the repository's own `venv/`), the
> definitions run to about 172,000 characters for `full`, roughly 43k
> tokens at four characters per token, and about 58,000 characters for
> `core`, roughly 14k tokens. On Python 3.10 to 3.12 the same
> definitions measure about five per cent more, because those
> interpreters keep the docstring indentation that 3.13 strips. See the
> LM Studio recipe in INSTALL.md for what that means for context
> length.

**Project Management:**
- `list_available_projects(search_directories)` - Discover Qualcoder projects on your system
- `select_project(project_path)` - Open/switch to a different project (reports `qualcoder_gui_signals` and remembers the selection for the recovery hint)
- `get_current_project()` - Show which project is open, whether a released QualCoder has it open (`qualcoder_open`), and the 4.0 heuristics (`qualcoder_gui_signals`); `pseudonyms_json` says whether the project's own `pseudonyms.json` is present and how many entries it has, never a name
- `create_project(name, directory, coder_name, coder_name_not_known)` - **Creates a folder and a database** (the `lifecycle` toolset only): a new, empty project in QualCoder 4.0's format, exactly as 4.0's own New Project makes it, in the server's workspace or an existing folder, then selects it. Asks for the researcher's own QualCoder coder name (or an explicit "not known") after every other check; refuses a name already used there in any letter case, names QualCoder cannot open or Windows cannot store, and names whose backups sit beside it; never replaces or deletes anything
- `set_project_ai_coder_name(name, note, allow_hidden_coder)` - Set the coder name this project's AI writes are stored under (stored beside the project in `qualcoder_mcp.json`); refuses the researcher's own coder name, QualCoder's `default` and its speaker coder, and warns when the researcher's name is not known yet
- `read_pseudonym_list()` - **Sends real names to the AI provider**: returns the entries of the project's own `pseudonyms.json` (the researcher's reverse key), for use only when the researcher asks to see or check the list; each call writes one log line with the count and no name. In the full toolset only. QualCoder's Pseudonyms dialog (the button in Manage Files) shows the same list without sending it anywhere

**Core Data Analysis:**
- `search_files(pattern, search_filename, search_content, search_memo, limit, exclude_code_ids, cursor, max_matches_per_file)` - Find files by name, content, or memo with smart clarification workflow; `exclude_code_ids` hides content matches that are already coded under those codes, and `cursor` walks the results page by page
- `search_coded_text(query, code_name, limit, coder, exclude_code_ids, cursor)` - Search coded segments, with the same novelty filter and paging
- `get_coded_segments(code_id, limit, coder, strategy, max_chars, file_ids, cursor)` - Segments for a code, sampled by strategy (`by_document`, `diverse_by_document`, `recent_first`, `sequential`) under an optional character budget
- `get_coding_frequencies(coder)` - Coding statistics
- `search_memos(query, limit)` - Search memos and annotations (public memo text only)
- `export_code_report(code_name)` - Detailed code report returned into the conversation (public memo text only)
- `get_project_summary()` - Comprehensive project overview

On projects with the coder-visibility capability (QualCoder 3.8.2 and
4.0, schema v14 and later) that hide coders, tools with a `coder`
argument read visible coders' work by default and one coder's rows from
the full data when `coder` is given (see "Working alongside QualCoder
4.0").

**Rich Transcript Analysis:**
- `analyze_file_with_coding(file_id)` - Get complete file text with all coding context for deep analysis

**Attributes & Demographics:**
- `list_attribute_types()` - List all available attributes (age, gender, etc.)
- `get_file_attributes(file_id)` - Get attributes for a specific file
- `get_case_attributes(case_id)` - Get attributes for a specific case
- `query_by_attribute(attr_name, attr_value, attr_type, operator)` - Find cases/files by attribute values

**Co-occurrence Analysis:**
- `find_cooccurring_codes(code_id, window_size, coder)` - Discover which codes appear together
- `compare_coders(coder_a, coder_b, code_ids, file_ids, case_ids, include_subcodes, per_file, allow_hidden_coder)` - Compare two coders' text coding per code: agreement, dual-coded and uncoded percentages, and two agreement coefficients (`kappa_qualcoder`, which reproduces QualCoder's own column, and `kappa_cohen`). Read-only; full toolset only

**Case-Code Matrix & Comparative Analysis:**
- `get_case_code_matrix(coder)` - Create cross-tabulation of cases vs codes
- `get_codes_by_case(case_id, coder)` - Get all codes used in a specific case
- `get_cases_by_code(code_id, coder)` - Get all cases containing a specific code

**AI-Assisted Coding (Conversational Workflow):**
- `analyze_for_coding(file_ids, code_names, instruction, min_confidence)` - Create an analysis session for Claude to perform coding suggestions (returns the `coding_session_id` the other session tools take)
- `record_suggestions(coding_session_id, suggestions, replace)` - Record Claude's suggestions into the session (each verified against the file text; positions auto-corrected when the excerpt is unique)
- `review_suggestions(coding_session_id, suggestion_guids, show_context)` - Show detailed information about specific suggestions
- `edit_suggestion(coding_session_id, suggestion_guid, start_pos, end_pos, segment_text, use_alternative, code_id, code_name)` - Adjust a pending suggestion's span or code before approval (session-only; server-computed shorter/longer alternatives)
- `update_suggestion_status(coding_session_id, approve, reject)` - Approve or reject suggestions by GUID
- `apply_codings(coding_session_id, create_backup, owner)` - **WRITES TO DATABASE** - Apply approved suggestions (bound to the session's project, validated before backup, all-or-nothing; a suggestion whose identical coding is already in the project is reported as already existing and skipped, not written twice)
- `get_coding_session_info(coding_session_id)` - View all details of a coding session
- `list_coding_sessions(project_path, days_old)` - List all saved coding sessions
- `delete_coding_session(coding_session_id)` - Delete a saved session file (not the codings)
- `cleanup_old_sessions(days_old)` - Delete session files older than N days (N >= 1)
- `explain_ai_coding_tools(tool_name)` - Built-in help for this workflow, including `grounding_rules`, `methodology_vocabulary` and `methods_notes`

**Inductive Coding (proposing new codes):**
- `propose_codes(coding_session_id, proposals, replace)` - Record brand-new code proposals discovered in the data
- `review_proposals(coding_session_id, proposal_guids, show_examples)` - Review proposed codes in detail before deciding
- `update_proposal(coding_session_id, proposal_guid, name, color, category, memo, example_segments)` - Refine a proposal before it is created
- `merge_proposals(coding_session_id, from_proposal_guid, into_proposal_guid)` - Combine two proposals
- `update_proposal_status(coding_session_id, approve, reject)` - Approve or reject proposals
- `create_proposed_codes(coding_session_id, apply_coded_segments, create_backup)` - **WRITES TO DATABASE** - Create the approved proposals in the codebook, optionally writing their evidence spans as codings

**Data Import, Cases & Attributes (Write Operations):**
- `import_text_file(filename, content, memo, owner, create_backup, case_name, apply_project_pseudonyms)` - **WRITES TO DATABASE** - Add a new text source, optionally linked to a case. The name follows `rename_file`'s rules (at most 200 bytes in UTF-8; no path, control or invisible characters; no name Windows cannot store; not a name already in the project's `documents/` folder). With `apply_project_pseudonyms=true` the project's own `pseudonyms.json` is applied to the text before it is stored, which is what QualCoder does to every text file it imports; default off
- `link_file_to_case(file_id, case_id, case_name, create_backup)` - **WRITES TO DATABASE** - Make a file visible to case-based analyses
- `create_case(name, memo, create_backup)` - **WRITES TO DATABASE** - Create a new case (idempotent: an existing name, case-insensitively, answers `created: false` with the existing case)
- `rename_case(case_id, new_name, create_backup)` - **WRITES TO DATABASE** - Rename a case, as QualCoder's Manage Cases does: the name only, the date untouched. A name another case has, ignoring letter case, spacing and Unicode form, is refused; the result says where the old name stays (saved graph labels, table displays and filters, files named after the case, backups)
- `rename_file(file_id, new_name, create_backup)` - **WRITES TO DATABASE** - Rename a file's entry, as QualCoder's "Rename database entry" does: the name only, nothing on disk. Refuses path characters, names Windows cannot store, names over 200 bytes in UTF-8, a name already in the project's `documents/` folder for a text, and an ending change QualCoder acts on (a transcript's `.txt` or `.transcribed`, `.pdf`, a media file's extension); the result says what keeps the old name (an imported file's stored copy and stored path, and for a document its original text)
- `create_attribute_type(name, applies_to, value_type, memo, create_backup)` - **WRITES TO DATABASE** - Define a new attribute for cases, files or journals
- `set_attribute(target_type, target_id, attribute_name, value, create_backup)` - **WRITES TO DATABASE** - Set or clear an attribute value

**Recovery & Safety:**
- `copy_project_to_workspace(source_path, new_name)` - Copy a project to the safe workspace for AI coding (same exclusions as backups; reports skipped symlinks)
- `delete_coding(coding_id, create_backup, allow_hidden_coder, confirm_private_note_deletion)` - **WRITES TO DATABASE** - Remove one coded segment (refuses a hidden coder's row or a row carrying a private note unless the override is passed)
- `list_backups()` - List this project's backup snapshots (both this server's `_backup_` and QualCoder's `_BKUP_` families)
- `prune_backups(keep_last, older_than_days, preview_token)` - Delete this server's own backups by a retention policy (preview first, then the token the preview returns; QualCoder's `_BKUP_` backups are never removed)
- `restore_backup(backup_path, preview_token)` - Guarded project restore (previews first, then the token the preview returns, reporting `qualcoder_gui_signals`; safety backup of the current state)

**Interchange & Report Exports (exported files keep full memos, private sections included):**
- `export_refi_qda(output_path, coding_session_id, overwrite)` - Export codings (or a session's suggestions) as a REFI-QDA .qdpx for QualCoder/NVivo/ATLAS.ti/MAXQDA
- `export_codebook(output_path, format, include_memos, sanitize_formulas, overwrite)` - Codebook (codes and category tree) as CSV, txt or Markdown, matching QualCoder's Codebook export
- `export_coded_segments_report(output_path, code_names, case_names, coder, file_ids, search_text, important, include_variables, format, sanitize_formulas, overwrite)` - QualCoder's Coding Report as a file
- `export_frequencies_csv(output_path, sanitize_formulas, overwrite)` - Code frequencies table as CSV
- `export_case_code_matrix_csv(output_path, sanitize_formulas, overwrite)` - Case by code cross-tab as CSV

**Memos, Annotations & Journal (Write Operations):**
- `set_memo(target_type, target_id, memo, create_backup, allow_hidden_coder)` - **WRITES TO DATABASE** - Write or clear the public part of a memo on a code, category, file, coding, or case, or the project memo (`target_type` `project`, `target_id` null), which QualCoder 4.0's own assistant reads as the study's context (an existing `#####` private section survives; content-only, matching QualCoder, never rewrites date/owner)
- `add_journal_entry(name, entry, create_backup)` - **WRITES TO DATABASE** - Add or update a research journal entry
- `add_annotation(file_id, start_pos, end_pos, memo, create_backup)` - **WRITES TO DATABASE** - Attach a note to a text span of a file
- `update_annotation(annotation_id, memo, create_backup, allow_hidden_coder)` - **WRITES TO DATABASE** - Edit an annotation's note (an empty note deletes the annotation, as in QualCoder, unless a private section keeps the row)
- `delete_annotation(annotation_id, create_backup, allow_hidden_coder, confirm_private_note_deletion)` - **WRITES TO DATABASE** - Delete an annotation

**Codebook Editing (Write Operations):**
- `create_code(name, category, color, memo, parent_code_id, create_backup)` - **WRITES TO DATABASE** - Create a new code (a supplied colour is snapped onto QualCoder's 120-colour palette and the result says so; `parent_code_id` nests it as a sub-code on v16+ schemas). Idempotent: a name that already exists, ignoring letter case, spacing and Unicode form, answers `created: false, reason: already_exists` with the existing code and makes no backup
- `rename_code(code_id, new_name)` - **WRITES TO DATABASE** - Rename a code (a name another code already uses, case-insensitively, is refused; the identical name answers `changed: false`)
- `recolor_code(code_id, color)` - **WRITES TO DATABASE** - Change a code's colour (snapped onto QualCoder's palette; `changed: false` when the code already has that colour)
- `move_code_to_category(code_id, category)` - **WRITES TO DATABASE** - Move a code into a category (omit `category` for top level; `changed: false` when it is already there). The result names the category it resolved to (`new_category`), since a name matches ignoring letter case, spacing and Unicode form
- `create_category(name, parent_category, memo)` - **WRITES TO DATABASE** - Create a category (idempotent like `create_code`: an existing name, case-insensitively, answers `created: false` with the existing category)
- `rename_category(category_id, new_name)` - **WRITES TO DATABASE** - Rename a category (same collision and no-op rules as `rename_code`)
- `move_category(category_id, parent_category)` - **WRITES TO DATABASE** - Reparent a category (refuses moves that would create a cycle; `changed: false` when it is already under that parent). The result names the new parent (`new_parent`)

**Source Text, Destructive (preview, then token, then safety backup):**
- `pseudonymise_source(mapping, file_id, use_project_pseudonyms, case_mode, overlap_policy, rewrite_memos, save_mapping_to_project, researcher_keeps_mapping, preview_token, allow_hidden_coder, record_in_journal, include_context, context_chars, scan_residue, residue_detail, max_spans_per_entry)` - **WRITES TO DATABASE** - Replace names with pseudonyms in the stored text of one text source per call, moving every coding, annotation and case link with the text. `file_id` is required; to pseudonymise a project, run it file by file, so two people who share a name can be given two pseudonyms. A PDF, a media file or a source with no stored text is refused with the reason (`pdf_source`, `no_fulltext`, `unknown_file_id`). The only tool here that rewrites the text positions are measured against. Deterministic and rule-based: only the names in `mapping` are replaced, as whole words, case-sensitively unless `case_mode` says otherwise; no name detection. `overlap_policy` decides what happens to a coding that cut into a name: `snap_to_pseudonym` (default) grows it to contain the whole pseudonym and never deletes anything, `qualcoder_edit_parity` reproduces the walk QualCoder's coding-view editor applies, fed this tool's exact edit list (the editor's own diff may factor a shared prefix or suffix out of a replacement and keep a coding this policy deletes), which deletes a coding sitting on a name. Notes and journal entries are scanned and counted, and are rewritten, in their public part only and across the whole project, only when `rewrite_memos` is on; case, file, code, category and attribute-type names, journal entry names and attribute values are scanned and counted, never rewritten; and the preview's `residue` block says where names remain, with a third count, `wide_after_rewrite`, on each note field when the notes are rewritten, which does not reach zero because the wide reading is wider than the rewrite. A note's private part is carried across unread, so a name in it is still there and cannot be reported. `rewrite_memos` and `save_mapping_to_project` are bound into the token, six bound arguments in all. On a mapping you type, the execute is refused unless `save_mapping_to_project` (given on the preview, because the token binds it) writes the mapping into the project's own `pseudonyms.json` in QualCoder's own format, merged by QualCoder's rules, with alternative spellings as separate entries and the new entries longest name first (QualCoder's text and transcript imports (not PDFs) apply the file one entry at a time, case-sensitively; the preview warns when an entry already in the file would pre-empt a new one, or when an insensitive case mode means QualCoder will replace only the spellings saved), or `researcher_keeps_mapping` (not bound, and allowed on the execute) attests that the researcher keeps their own record; PDFs are never rewritten, and their stored text is counted with every other file's; media files and `ai_data/` are out of scope and are neither rewritten nor scanned. Writes a run manifest to `~/.qualcoder_mcp/pseudonymisation/`, an audit record of which rows the run changed and not a way back (the backup is), and, by default, a journal entry in the project; neither ever contains an original name, and a file name, folder name or path that carries one is withheld from both in favour of the file id. Every `residue` count is two readings, `{"wide": N, "whole_word": M}`: the wide one reads wider than the rewrite does, any occurrence a person would see, including inside a longer word and in any case, and every whole word the rewrite itself matches, and is a heuristic; the whole-word one is what this run's own rule matches. Notes, labels and attribute values are counted as fields; the `file_text` block counts, as occurrences, the names left in the text of every file with stored text after the run, the one this call rewrites, the files it does not touch and the PDF sources, each split by kind (inside a longer word, case only, joined differently, an invisible character or another normalisation, put back by a pseudonym, whole words in a file this run did not rewrite). A name inside a longer word is reported and never substituted; on a typed mapping the block lists the longer words themselves, so an exact entry can be added. A longer word is listed only when it extends the name by at most eight characters and is not in a script written without spaces (Chinese, Japanese, Thai, Lao, Khmer, Myanmar), and all lists in one preview share 4,000 characters. By default the block gives full detail for the file this call names and one short row (id, name, the two counts) for up to 1,000 other files that still show a name, and their ids past that; `residue_detail="project"` gives full detail for up to 200 files and the short row for up to 1,000 more, and the totals and the warnings are the same either way. The count has fixed budgets for its work and for the number of matches, and everything it spends is charged to them; past them a file is only asked whether a name shows, and past a budget for that question it is not checked, is listed in `files_not_checked` and is never reported clean. The file this call names is read first, with the first claim on the budgets. A count that stops part-way has found a name and is listed in `files_counted_in_part` with a lower bound, a file too large to count with this many names, decided before counting, is listed in `files_too_large_for_this_mapping`, where fewer names is the remedy, one too large for any mapping in `files_too_large_for_any_mapping`, where there is none, and a PDF source, which cannot be named for a preview, is never told to be previewed on its own. With `use_project_pseudonyms` the mapping is the researcher's own `pseudonyms.json`, which the caller never supplied, so no diagnostic and no refusal quotes a name from it, `include_context` returns nothing, no longer word is listed, and a pseudonym that carries one of its names is withheld by entry number (it is withheld from the run manifest and the journal entry on both paths); file names, including every file the residue names, and the project path are still returned as they stand. The mandatory backup does contain the real names. On a project that hides coders, the preview reports what the run would do to their rows as counts (`shifted`, `substituted`, `resized`, `snapped`, `deleted`, `clamped`), never names, and `allow_hidden_coder` is required when `snapped`, `deleted` or `clamped` is non-zero: a pure shift, a substitution and a resize change no coding decision, whatever the two lengths

**Codebook, Destructive (preview, then token, then safety backup):**
- `merge_codes(from_code_id, into_code_id, preview_token, allow_hidden_coder)` - **WRITES TO DATABASE** - Merge one code into another (lossy on overlaps, exactly matching QualCoder)
- `delete_code(code_id, preview_token, cascade, allow_hidden_coder)` - **WRITES TO DATABASE** - Delete a code and all its coded segments (`cascade=true` is required for a code that has sub-codes)
- `delete_category(category_id, preview_token)` - **WRITES TO DATABASE** - Delete a category; its codes and sub-categories move to the top level (no cascade to coded data)
- `merge_category(from_category_id, into_category, preview_token)` - **WRITES TO DATABASE** - Merge a category into another (or into the top level); its codes and sub-categories move to the target

Since v0.12 these four, `pseudonymise_source`, `restore_backup` and
`prune_backups`, are two-step: call without `preview_token` for a preview of exactly what
would change, then call again with the token the preview returned. The
token is bound to that operation, those arguments, that project and the
rows the preview covered, so a preview the user approved cannot
authorise something else, and a project that changed in between is
refused rather than acted on. The `confirm` argument, accepted and
ignored through 0.12, is gone in 0.13: drop it from any call that still
passes it.

Every preview says whose work is at stake: how many of the affected
codings were made under this project's AI coder name(s), a per-owner
breakdown of the rest, how many belong to coders currently hidden in
QualCoder (a count, never a name), and how many rows carry a `#####`
private note. Of these six, `merge_codes` and `delete_code` require
`allow_hidden_coder=true` to execute when hidden coders' codings are
affected (`restore_backup` rolls the whole project back and has no such
gate); `pseudonymise_source` gates a narrower set (see its own entry).

## Available Prompts

Built-in prompt templates for common analysis tasks:

- `analyze_theme(theme_name)` - Deep dive into a specific theme
- `compare_codes(code1, code2)` - Compare two codes
- `summarize_project()` - Describe the state of a project (data, codebook, coding progress), without drawing conclusions from counts
- `explore_case(case_name)` - Analyse a specific case

## Troubleshooting

### Server Not Connecting

1. **Check the configuration file path**: Make sure `claude_desktop_config.json` is in the right location
2. **Verify Python path**: Run `which python` in your virtual environment to get the correct path
3. **Check .qda project path**: Make sure the path to your Qualcoder project folder is correct and exists
4. **Look at logs**: Check Claude Desktop logs for errors

### Claude Can't Access Data

1. **Restart Claude Desktop** after any configuration changes
2. **Check file permissions**: Make sure the `.qda` file is readable
3. **Verify the virtual environment** is activated when testing

### "No Qualcoder project selected" Error

With Option A, ask Claude to list and select a project; the error also names the last project used on this machine when it still exists, so one `select_project` call recovers. With Option B, make sure the `env` section in your configuration includes the `QUALCODER_PROJECT_PATH` variable with the full path to your `.qda` project folder (or its `data.qda` file).

### Testing the Server Manually

You can test the server independently:

```bash
# Activate your virtual environment
source venv/bin/activate

# Set the project path
export QUALCODER_PROJECT_PATH="/path/to/your/project.qda"

# Check the installed version (prints it and exits)
python -m qualcoder_mcp.server --version

# Run the server (it will use stdio)
python -m qualcoder_mcp.server
```

Started by hand like this, the server prints one paragraph explaining that it expects an MCP host on its standard input and output, then waits; that is the expected behaviour, not an error (an MCP host never sees the paragraph). Press Ctrl+C to stop.

### Using MCP Inspector for Development

For development and debugging, you can use the MCP Inspector:

```bash
# Install uv if you haven't already
pip install uv

# Run the inspector
export QUALCODER_PROJECT_PATH="/path/to/your/project.qda"
uv run mcp dev src/qualcoder_mcp/server.py
```

This will open a web interface where you can test resources and tools.

## Data Safety

This MCP server operates in **two modes**:

### Read-Only Mode (Default)
For all standard analysis operations:
- ✅ No writes to your project database
- ✅ Your Qualcoder projects are never modified
- ✅ All operations are queries only
- ✅ Safe to use on original projects

### Write-Enabled Mode (AI Coding)
For AI-assisted coding with direct database writes:
- ⚠️ **WRITES TO DATABASE** - Can modify project files
- ✅ **Automatic backups** created before every write
- ✅ **Workspace isolation** - Work on copies only
- ✅ **Conversational approval** - You control what gets written
- ✅ **Session files** - AI coding sessions (suggestions, proposals and their statuses) are saved in `~/.qualcoder_mcp/sessions/`
- ✅ **Rollback capability** - Backups allow full restoration

**Best Practices for AI Coding:**
1. 🔒 **NEVER work on original projects** - Always copy to workspace first
2. 🔒 **Review backups** - Check backup was created before applying
3. 🔒 **Test on copies** - Try workflow on test projects first
4. 🔒 **Keep originals** - Maintain untouched versions of important projects
5. 🔒 **Verify in Qualcoder** - Open project after AI coding to confirm results

**General Safety:**
- 🔒 The server runs locally and adds no cloud path of its own, but
  tool results enter the conversation and are transmitted to whichever
  AI provider your host uses (none, with a fully local host). **See
  [PRIVACY.md](https://github.com/nicotem/qualcoder_mcp/blob/main/PRIVACY.md)** for what this means for research data.
- 🔒 Regular Qualcoder backups recommended
- 🔒 Automatic backups: `<project>_backup_<timestamp>.qda` folders next to
  the project, one per write (the whole project tree, `ai_data/`
  included, minus QualCoder's backup ignore set and lock files; prune
  them with `prune_backups`)
- 🔒 Workspace directory: `~/Documents/Qualcoder MCP Projects/`
- 🔒 Session files: `~/.qualcoder_mcp/sessions/`
- 🔒 Last-used project pointer: `~/.qualcoder_mcp/mru_project.json` (one
  project path and a timestamp, echoed only into the "no project selected"
  error as a recovery hint; see [PRIVACY.md](https://github.com/nicotem/qualcoder_mcp/blob/main/PRIVACY.md))

## Architecture

```
┌─────────────────┐
│  Claude Desktop │  (MCP Host)
└────────┬────────┘
         │ MCP Protocol (stdio)
         │
┌────────▼────────┐
│   MCP Server    │  (This package)
│  qualcoder_mcp  │
└────────┬────────┘
         │ SQLite connection (read-only by default;
         │  guarded writes with backup, lock and
         │  heuristic open-window checks)
         │
┌────────▼────────┐
│   Qualcoder     │
│  Database (.qda)│
└─────────────────┘
```

## Development

### Project Structure

```
qualcoder_mcp/
├── src/
│   └── qualcoder_mcp/
│       ├── __init__.py
│       ├── server.py            # Main MCP server with resources, tools, prompts
│       ├── database.py          # SQLite database interface
│       ├── memo_privacy.py      # QualCoder's '#####' private-memo convention
│       ├── sessions.py          # AI coding session management
│       ├── project_settings.py  # The project's AI coder name (qualcoder_mcp.json)
│       ├── preview_tokens.py    # Preview tokens for the destructive tools
│       ├── cursors.py           # Paging cursors for the search and segment tools
│       ├── coder_comparison.py  # compare_coders: agreement and the two kappas
│       ├── pseudonymise.py      # pseudonymise_source: matching, remapping, the residue detector
│       └── refi_export.py       # REFI-QDA XML export
├── scripts/
│   ├── create_test_project.py  # Test project generator
│   ├── generate_test_export.py
│   └── test_workflow.py
├── legal/
│   └── GPL-3.0.txt         # The GNU GPL, version 3, which the LGPL incorporates
├── pyproject.toml           # Package configuration
├── README.md               # This file
├── CHANGELOG.md            # Version history
├── INSTALL.md              # Detailed installation guide
├── PRIVACY.md              # Data-flow disclosure
├── CONTRIBUTING.md         # How to report, propose and review changes
├── CITATION.cff            # Citation metadata
├── SUPPORT.md              # Support policy (GitHub Issues only)
├── COPYING.LESSER          # The licence: GNU LGPL, version 3
└── NOTICE                  # Copyright, licence, and the code derived from QualCoder
```

### Contributing

Contributions are welcome! Some ideas for enhancements:

**Completed in v0.2.0:**
- ✅ Co-occurrence analysis
- ✅ Support for attributes and demographic queries
- ✅ Case-code matrix for comparative analysis
- ✅ Rich transcript analysis with full coding context
- ✅ Support for multiple projects switching

**Completed in v0.3.0:**
- ✅ AI-assisted coding with REFI-QDA export (deprecated in v0.4.0)
- ✅ Code discovery and suggestion
- ✅ Session persistence and management
- ✅ Confidence scoring for suggestions
- ✅ Comprehensive help system

**Completed in v0.4.0:**
- ✅ Conversational approval workflow for AI coding
- ✅ Direct database writes with automatic backups
- ✅ Workspace isolation for safe modifications
- ✅ GUID-based suggestion approval/rejection
- ✅ Context-aware suggestions with reasoning

**Completed in v0.6.0:**
- ✅ `record_suggestions`: the AI coding loop works end-to-end via MCP tools
- ✅ Session-project binding and text/position verification on every write
- ✅ QualCoder lock-file protocol: writes refuse while QualCoder is open
- ✅ Recovery tooling: `delete_coding`, `list_backups`, guarded `restore_backup`
- ✅ REFI-QDA export revived and made importable (`export_refi_qda`)
- ✅ Case linkage for imported files (`link_file_to_case`)
- ✅ Attribute queries with operators (contains, gt/gte/lt/lte)
- ✅ Old-schema/corrupt/locked projects fail with clear, actionable errors

**Completed in v0.7.0:**
- ✅ Memo writing (`set_memo`) and research journal (`add_journal_entry`)
- ✅ Codebook editing: create/rename/recolour/move codes and categories
- ✅ Destructive codebook ops with preview → confirm → safety backup (`merge_codes`, `delete_code`, `delete_category`), matching QualCoder's own semantics
- ✅ Category cycle guard (which QualCoder itself lacks)

**Completed in v0.8.0:**
- ✅ Inductive/open coding: AI proposes brand-new codes; you refine, approve, and create them
- ✅ Review-time span editing (`edit_suggestion`) with server-computed shorter/longer alternatives
- ✅ Report exports: codebook, coded segments, frequencies, case×code matrix (CSV/txt/md, QualCoder-parity numbers)
- ✅ Annotations, category merge, case creation, attribute schema and values
- ✅ Backup retention (`prune_backups`) and opt-in CSV formula sanitisation

**Completed in v0.9.0:**
- ✅ **PyPI packaging**: `pip install qualcoder-mcp` (or a one-command `pipx`/`uvx` install): no git clone, no manual venv, and updates via `pip install --upgrade`
- ✅ Whole-codebase security audit with three fixes; SHA-pinned CI actions
- ✅ Upgrade guide for existing (git-install) testers

**Completed in v0.10.x:**
- ✅ **QualCoder schema v14 through v17 support**, including full sub-code handling, determined by capability probes rather than version numbers
- ✅ Write protection for lockless QualCoder 4.0 builds (text re-verified inside the write transaction)
- ✅ **Multi-host support (Experimental)**: the reduced core toolset (`QUALCODER_MCP_TOOLSET=core`) plus setup recipes for [LM Studio (fully local)](https://github.com/nicotem/qualcoder_mcp/blob/main/INSTALL.md#lm-studio-fully-local-experimental) and [Claude Code with an Anthropic API key](https://github.com/nicotem/qualcoder_mcp/blob/main/INSTALL.md#claude-code-with-an-anthropic-api-key-experimental)
- ✅ v0.10.1: the session tools take `coding_session_id` (some MCP middleware strips an argument named `session_id`)

**Completed in v0.11.0: QualCoder 4.0 interop conventions, Phase 1**
- ✅ `#####` private memo sections are never shown to the AI, survive every memo write, and stay in exported files for parity with QualCoder's own exports
- ✅ Configurable AI coder name (`QUALCODER_MCP_AI_CODER_NAME`), with `AI Agent` as the QualCoder 4.0 opt-in
- ✅ Reads follow QualCoder's per-coder visibility wherever the project has that capability (QualCoder 3.8.2 and 4.0, schema v14 and later), with a `coder` override; by-id writes on a hidden coder's row need `allow_hidden_coder`
- ✅ Backups and workspace copies include `ai_data/` minus QualCoder's own ignore set; symlinks pointing outside the project are not followed
- ✅ Best-effort detection of an open QualCoder 4.0 window (`qualcoder_gui_signals`), and the last-used project named in "no project selected" errors

**Completed in v0.12.0: the QualCoder 4.0 ground-truth study, two batches and a flagship** (v0.12.1 adds no code; it records the in-QualCoder acceptance checks, see the CHANGELOG)
- ✅ 🕵️ Pseudonymisation tooling, retroactive and position-preserving
  (`pseudonymise_source`): rewrites the stored text of chosen text
  sources and moves every coding, annotation and case link with it, in
  one transaction, behind a preview, a token bound to that preview and a
  mandatory backup. Rule-based and deterministic: only the names you
  list are replaced, as whole words. Memos, journal entries, case, file,
  code, category and attribute-type names and attribute values are
  scanned and counted, never rewritten, so the preview's residue report
  tells you where names remain; PDFs, media files and `ai_data/` are out
  of scope and are neither rewritten nor scanned. Hidden coders' rows
  move with the text and are reported as counts; the override is needed
  only where a coding's extent changed for a reason the rewrite does not
  explain
- ✅ The AI coder name belongs to the project and you choose it
  (`set_project_ai_coder_name`, stored in `qualcoder_mcp.json` beside
  `data.qda`); the first write asks instead of guessing, and the host's
  `QUALCODER_MCP_AI_CODER_NAME` is a declaration offered as a quick pick
- ✅ Preview tokens on the six destructive tools, replacing `confirm`
  (kept inert for one release), with a `collateral` block that says
  whose work each cascade would remove
- ✅ `compare_coders`: per-code agreement between two coders, with
  QualCoder's own coefficient and Cohen's kappa side by side
- ✅ The novelty filter (`exclude_code_ids`), keyset cursors and sampling
  strategies with a character budget on the paged read tools
- ✅ Colours snapped onto QualCoder's 120-colour palette; idempotent
  creates with case-insensitive duplicate detection (QualCoder 4.0
  parity, reversing the v0.10 rule); no-op writes answered, not backed up
- ✅ Methodology vocabulary and grounding rules in the tool guidance, the
  `qualcoder://guidance/methods` resource and the MCP `initialize` handshake
- ✅ `--version`, a notice when the server is started by hand, the
  `mcp>=1.17.0,<2` floor (the core toolset needs `FastMCP.remove_tool`),
  the deprecated `session_id` duplicate removed, Dependabot on the
  SHA-pinned actions

**Completed in v0.13.0 (this release):**
- ✅ `pseudonymise_source` rewrites one file per call, and its residue
  report counts the names left in the text of every file, each count
  as two readings (wide and whole-word); a name inside a longer word is
  reported and never substituted, and a pseudonym that contains a real
  name is warned about and withheld from the records
- ✅ `pseudonymise_source` rewrites the public part of notes and journal
  entries under `rewrite_memos`, and a mapping you type is kept: saved
  into the project's own `pseudonyms.json` or attested as kept by the
  researcher; `read_pseudonym_list` reads that file back as a tool of
  its own; a restore says when it takes the file out of the project,
  and a prune warns before it removes the only backups that hold it
- ✅ `rename_case` and `rename_file` rename a case or a file's entry the
  way QualCoder does, so a label named after a participant can be
  changed without leaving the conversation
- ✅ The inert `confirm` argument is gone from the six token-gated
  tools, and every failure after a backup names that backup
- ✅ The licence is LGPL-3.0-or-later, QualCoder's own, and NOTICE lists
  every routine, value and fact taken from QualCoder

**Planned for v0.14 and later:**
- 🛡️ Undo for what a session did, on the model of QualCoder 4.0's own
  assistant, and a hardening round (no note text in any error answer or
  log line)
- 🧪 Studies under way: creating a project, coding a PDF's text, and
  graphs
- 🎯 Quote-anchored writes with a fuzzy fallback
- 🖼️ Media region coding (images, audio/video, PDF)
- 🤝 Further QualCoder 4.0 interoperability (later phases)
- 🔭 Further refinements driven by tester feedback ([file yours](https://github.com/nicotem/qualcoder_mcp/issues))

## Disclaimer

This software is provided "as is", without warranty of any kind, express or implied. The authors accept no responsibility or liability for any damage, data loss, or other issues arising from the use of this software. Users are solely responsible for ensuring the integrity and backup of their QualCoder projects. Always work on copies of your data, not originals.

## Licence

From v0.13, qualcoder-mcp is licensed under the GNU Lesser General Public License, version 3 or (at your option) any later version (`LGPL-3.0-or-later`), which is QualCoder's own licence. The licence texts are [COPYING.LESSER](https://github.com/nicotem/qualcoder_mcp/blob/main/COPYING.LESSER) and the GNU General Public License, version 3, which the Lesser licence incorporates: [legal/GPL-3.0.txt](https://github.com/nicotem/qualcoder_mcp/blob/main/legal/GPL-3.0.txt) in this repository, and the same text at [https://www.gnu.org/licenses/gpl-3.0.txt](https://www.gnu.org/licenses/gpl-3.0.txt).

Every release up to and including 0.12.1 was published under the MIT License, and this project's own code in those releases remains available under those terms. Those releases also contained some of the QualCoder-derived items NOTICE lists; those items were always under QualCoder's licence, LGPL-3.0-or-later, whatever those releases declared.

qualcoder-mcp is a separate program that reads and writes QualCoder project files. It does not include QualCoder, but it contains code derived from QualCoder: a small number of routines and values taken from QualCoder so that its results match QualCoder's exactly. [NOTICE](https://github.com/nicotem/qualcoder_mcp/blob/main/NOTICE) lists them, with the QualCoder file and lines each comes from.

Nothing changes for anyone who installs and runs the server. The licence's conditions apply only to someone who distributes it, and in practice they matter for a modified version: whoever distributes one must make its source available under the same licence.

## Acknowledgements

- [Qualcoder](https://github.com/ccbogel/QualCoder) by Dr. Colin Curtain and Dr. Kai Dröge
- [Model Context Protocol](https://modelcontextprotocol.io/) by Anthropic
- [Claude Desktop](https://claude.ai/download) by Anthropic

## Support

If you encounter issues:
1. Check the Troubleshooting section above
2. Review the [MCP documentation](https://modelcontextprotocol.io/)
3. Check [Qualcoder documentation](https://github.com/ccbogel/QualCoder/wiki)
4. Open an issue on [GitHub Issues](https://github.com/nicotem/qualcoder_mcp/issues)

All support goes through GitHub Issues, not email. See
[Support & Feedback](#support--feedback) above and [SUPPORT.md](https://github.com/nicotem/qualcoder_mcp/blob/main/SUPPORT.md).

## See Also

- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [FastMCP Documentation](https://github.com/jlowin/fastmcp)
- [Qualcoder Homepage](https://qualcoder.wordpress.com/)
