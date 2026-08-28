# Qualcoder MCP Server

A Model Context Protocol (MCP) server that connects an MCP host (Claude Desktop, Claude Code, LM Studio, and others) to [Qualcoder](https://github.com/ccbogel/QualCoder), enabling AI-assisted qualitative data analysis.

## What is this?

This MCP server lets an AI assistant directly access and analyze your Qualcoder projects. Claude Desktop is the primary worked example throughout these docs, but any MCP host works (see "Choosing your AI host" below). The assistant can:

- 📊 Read your codes, categories, and coding structure
- 📝 Access coded text segments and original source documents
- 📖 **Analyze complete transcripts with coding context**
- 🔍 Search through your qualitative data
- 📈 Generate coding frequency reports
- 💭 Analyze themes and patterns
- 🔗 **Discover co-occurrence patterns between codes**
- 📋 Compare codes and cases
- 👥 **Query by demographics/attributes** (age, gender, etc.)
- 🎯 **Create case-code matrices for comparative analysis**
- 🗒️ Search through memos and annotations
- 🤖 **AI-assisted coding**: suggest → review → approve → apply, so nothing is written until you say so
- 🏷️ **Codebook editing**: create, rename, recolor, merge, move, and delete codes and categories
- 💾 **Memo & journal writing**: annotate codes, files, codings, and cases; keep a research journal
- ↩️ **Undo & restore**: delete a coding, list backups, and restore a whole project to an earlier state
- 📥 **Import transcripts** and link files to cases
- 🔄 **REFI-QDA export** (.qdpx) for interchange with NVivo, ATLAS.ti, and MAXQDA

You can work with read-only analysis OR use write-enabled tools. The database is opened read-only by default; every write is preceded by an automatic backup, verified against QualCoder's format, and refused while QualCoder has the project open — so the two can't corrupt each other.

## Support & Feedback

This is an experimental alpha built by one researcher — feedback, bug
reports, and feature ideas are genuinely wanted and actively shape what
gets built next.

**Everything goes through [GitHub Issues](https://github.com/nicotem/qualcoder_mcp/issues)**:
bug reports, questions, and feature requests alike. Issues are public
and searchable, so every answer helps the next researcher who hits the
same thing.

**Please don't email support requests.** The author's email address in
the package metadata and LICENSE is an authorship signature, not a
support channel — support requests sent by email will not receive a
reply. GitHub Issues is where everything is read and tracked.

See [SUPPORT.md](https://github.com/nicotem/qualcoder_mcp/blob/main/SUPPORT.md) for the full policy.

## Data Flow & Privacy — read this before using research data

The server runs entirely on your machine and adds no telemetry, no
analytics, and no cloud path of its own. **But everything a tool
returns — coded segments, interview excerpts, file contents, memos,
frequencies — enters your AI conversation, and conversation
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
explicit precisely so you can make that decision — many AI
integrations don't.

**Read [PRIVACY.md](https://github.com/nicotem/qualcoder_mcp/blob/main/PRIVACY.md)** for the full disclosure — what to
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
| **Anthropic commercial-terms routes** (Claude Code with a Console API key; Team/Enterprise accounts) | Same Claude capability; different terms attach to the traffic. Institutions should prefer organizational accounts. | [PRIVACY.md](https://github.com/nicotem/qualcoder_mcp/blob/main/PRIVACY.md), rungs 2 and 3; [INSTALL.md API-key recipe](https://github.com/nicotem/qualcoder_mcp/blob/main/INSTALL.md#claude-code-with-an-anthropic-api-key-experimental) |
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

> qualcoder-mcp is ground-truthed against QualCoder 3.8.2, the latest released
> version (project schema v14), and additionally verified against the unreleased
> QualCoder development tree (version string "QualCoder 4.0 Beta") at commit
> `7b074d2`, whose projects use schema v17. Project schemas v14 through v17 are
> supported for reading and writing. Support is determined by inspecting the
> project database itself (capability probes), not by version numbers, so
> projects migrated by either QualCoder version work interchangeably.
>
> Because QualCoder 4.0 is not yet released, its behavior may change before
> release. Claims about 4.0 compatibility are valid as of commit `7b074d2` and
> will be re-verified against the final release. One known limitation: released
> QualCoder versions signal "project open" through a lock file, which qualcoder-mcp
> honors; the 4.0 development builds no longer use a lock file, so qualcoder-mcp
> cannot detect that a 4.0 build has the project open. Do not run qualcoder-mcp
> writes while any QualCoder window has the same project open.

Sub-codes (a code nested under another code, schema v16 and newer) are
fully supported: creating them, moving and merging without hierarchy
loss, branch-aware deletion, and nesting-aware listings, reports,
codebook and REFI-QDA exports. Projects newer than schema v17 refuse
writes until this server has been verified against them (an explicit
override exists for the adventurous; the tool output explains it).

## Installation

### Recommended: install from PyPI

The simplest install is a plain pip install into a virtual
environment — no git, no source tree:

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

Either way you end up with a **`qualcoder-mcp` console command** — find
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
installed `qualcoder-mcp` command — use the absolute path from
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
absolute path — Claude Desktop does not inherit your shell's PATH)

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

**Configuration** (PyPI install — for a source install use the
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

### Using with Claude Code (and other MCP clients)

The server speaks standard MCP over stdio, so **any MCP client works** —
Claude Desktop is simply the most common host. Researchers also run it
under Claude Code (including in editor side panels such as Obsidian's).

**Claude Code — one command** (PyPI install; for a source install
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

Both accept the same optional `env` block
(`QUALCODER_PROJECT_PATH`) as the Desktop configurations above.
Everything in this guide — the tools, the review-first workflow, the
safety gates — behaves identically in any client.

### Choosing the AI coder name (attribution)

Every row this server writes (codings, memos with provenance,
annotations, journal entries, imports, cases, attributes) is
attributed to one coder name so AI work stays distinguishable from
yours in QualCoder. The default is `AI Coding Assistant`. To change
it, set `QUALCODER_MCP_AI_CODER_NAME` in the server's `env` block:

```json
"env": {
  "QUALCODER_MCP_AI_CODER_NAME": "AI Agent"
}
```

`AI Agent` is the exact name QualCoder 4.0's built-in assistant writes
under. Opting into it groups this server's work and the built-in
assistant's under one coder in 4.0's per-coder visibility toggle,
undo, and reports, which is the coherent choice for projects worked on
by both. The default stays distinct so existing projects keep one
consistent history. Invalid values (empty, longer than 80 characters,
or containing control characters) stop the server at startup with a
clear error.

The MCP server should now be connected! You'll see it listed in the MCP section if you look at the settings.

## Updating to a new version

Updates are manual — a new release does not install itself.

> **Upgrading from a pre-0.9 git install?** See
> ["Upgrading from an earlier (git) install"](https://github.com/nicotem/qualcoder_mcp/blob/main/INSTALL.md#upgrading-from-an-earlier-git-install)
> in INSTALL.md — how to move to the PyPI install (or stay on git),
> with the exact client-config change. Your projects and session
> files are untouched either way.

**PyPI install** (recommended path) — one command, into the same
environment you installed with:

```bash
~/qualcoder-mcp-venv/bin/pip install --upgrade qualcoder-mcp
# pipx:  pipx upgrade qualcoder-mcp
# uv:    uv tool upgrade qualcoder-mcp
```

**Source (git) install** — update in three steps:

```bash
cd ~/Documents/qualcoder_mcp   # wherever you installed it
git pull                        # fetch the latest code
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -e .                # picks up any new dependencies
```

Then **fully quit and reopen your Claude client** (Claude Desktop: Cmd/Ctrl+Q then reopen; Claude Code: restart the session) so it relaunches the server with the new code. **New tools only appear after the client restart** — the client starts the server once per session, so an update takes effect on the next launch, not mid-conversation.

To check which version is running, ask Claude: *"What version of the QualCoder server is running?"* — it will report the version (e.g. `0.8.0a0`). You can also see the latest release and what changed on the [Releases page](https://github.com/nicotem/qualcoder_mcp/releases) and in [CHANGELOG.md](https://github.com/nicotem/qualcoder_mcp/blob/main/CHANGELOG.md).

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

### Analyzing Themes

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
Analyze the theme "workplace culture" and identify key patterns
```

```
Compare the codes "job satisfaction" and "work-life balance"
```

```
What are the main themes in case "Participant 5"?
```

### NEW: Rich Transcript Analysis

```
Analyze the interview transcript for participant 3, showing me both the coded segments and the full context. What does this participant say that relates to the Wisdom of the Crowds argument?
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

**Important**: AI coding writes directly to the database. Always work on copies in the `~/Documents/Qualcoder MCP Projects/` workspace folder. Automatic backups are created before every write, and **writes are refused while the project is open in QualCoder** — close it there first.

### Quick Start Example

**Step 1: Copy Project to Workspace**
```
Copy my project "Interview Study" to the workspace for AI coding
```

(the `copy_project_to_workspace` tool does this; then open the copy with `select_project`)

**Step 2: Analyze Files**
```
Analyze files 1-3 for WORKPLACE-STRESS and COPING-STRATEGIES codes
```

Claude will:
- Create an analysis session (`analyze_for_coding`)
- Examine the files
- Record its suggestions into the session (`record_suggestions` — every
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
- You can immediately open the project in Qualcoder to see results!

**If something went wrong**: `delete_coding(ctid)` removes a single coding;
`list_backups` + `restore_backup` roll the whole project back to a snapshot.

### Key Features

- **Conversational Review**: Discuss suggestions with Claude before applying
- **Confidence Scoring**: Each suggestion includes a 0.0-1.0 confidence score with reasoning
- **Session Persistence**: Resume work anytime, all sessions saved to disk
- **Automatic Backups**: Every write creates a timestamped backup first
- **Workspace Isolation**: Work on copies in dedicated workspace folder
- **Direct Database Writes**: No import/export - codings appear instantly in Qualcoder
- **Granular Control**: Approve/reject individual suggestions by GUID
- **Full Context**: See surrounding text for each suggestion
- **Verified Writes**: Suggestions are checked against the file text when
  recorded AND before writing; sessions only apply to the project they
  were created in
- **QualCoder-Aware**: Writes are refused while QualCoder has the project
  open (its `project_in_use.lock` heartbeat is respected)
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

## Available Tools

Claude can use these tools to analyze your data:

> **Reduced toolset for local models (Experimental):** with
> `QUALCODER_MCP_TOOLSET=core` in the server's environment, only the
> 20-tool supervised coding set is registered: list_available_projects,
> select_project, get_current_project, get_project_summary,
> search_files, analyze_file_with_coding, search_coded_text,
> get_coded_segments, get_coding_frequencies, analyze_for_coding,
> record_suggestions, review_suggestions, edit_suggestion,
> update_suggestion_status, apply_codings, create_code, set_memo,
> copy_project_to_workspace, delete_coding, list_backups.
> Required for local models, optional elsewhere; unknown values fail
> loudly at startup. The default (`full`) is everything below.

**Project Management:**
- `list_available_projects(search_directories)` - Discover Qualcoder projects on your system
- `select_project(project_path)` - Open/switch to a different project
- `get_current_project()` - Show which project is currently open

**Core Data Analysis:**
- `search_files(pattern, search_filename, search_content, search_memo)` - Find files by name, content, or memo with smart clarification workflow
- `search_coded_text(query, code_name, limit)` - Search coded segments
- `get_coded_segments(code_id, limit)` - Get all segments for a code
- `get_coding_frequencies()` - Coding statistics
- `search_memos(query, limit)` - Search memos and annotations
- `export_code_report(code_name)` - Generate detailed code report
- `get_project_summary()` - Comprehensive project overview

**Rich Transcript Analysis:**
- `analyze_file_with_coding(file_id)` - Get complete file text with all coding context for deep analysis

**Attributes & Demographics:**
- `list_attribute_types()` - List all available attributes (age, gender, etc.)
- `get_file_attributes(file_id)` - Get attributes for a specific file
- `get_case_attributes(case_id)` - Get attributes for a specific case
- `query_by_attribute(attr_name, attr_value, attr_type)` - Find cases/files by attribute values

**Co-occurrence Analysis:**
- `find_cooccurring_codes(code_id, window_size)` - Discover which codes appear together

**Case-Code Matrix & Comparative Analysis:**
- `get_case_code_matrix()` - Create cross-tabulation of cases vs codes
- `get_codes_by_case(case_id)` - Get all codes used in a specific case
- `get_cases_by_code(code_id)` - Get all cases containing a specific code

**AI-Assisted Coding (Conversational Workflow):**
- `analyze_for_coding(file_ids, code_names, instruction, min_confidence)` - Create an analysis session for Claude to perform coding suggestions
- `record_suggestions(session_id, suggestions, replace)` - Record Claude's suggestions into the session (each verified against the file text; positions auto-corrected when the excerpt is unique)
- `review_suggestions(session_id, suggestion_guids, show_context)` - Show detailed information about specific suggestions
- `update_suggestion_status(session_id, approve, reject)` - Approve or reject suggestions by GUID
- `apply_codings(session_id, create_backup, owner)` - **WRITES TO DATABASE** - Apply approved suggestions (bound to the session's project, validated before backup, all-or-nothing)
- `get_coding_session_info(session_id)` - View all details of a coding session
- `list_coding_sessions(project_path, days_old)` - List all saved coding sessions
- `delete_coding_session(session_id)` - Delete a saved session file (not the codings)
- `cleanup_old_sessions(days_old)` - Delete session files older than N days (N >= 1)
- `explain_ai_coding_tools(tool_name)` - Built-in help for this workflow

**Data Import & Cases (Write Operations):**
- `import_text_file(filename, content, memo, owner, create_backup, case_name)` - **WRITES TO DATABASE** - Add a new text source, optionally linked to a case
- `link_file_to_case(file_id, case_id, case_name, create_backup)` - **WRITES TO DATABASE** - Make a file visible to case-based analyses

**Recovery & Safety:**
- `copy_project_to_workspace(source_path, new_name)` - Copy a project to the safe workspace for AI coding
- `delete_coding(coding_id, create_backup)` - **WRITES TO DATABASE** - Remove one coded segment
- `list_backups()` - List this project's backup snapshots (both this server's `_backup_` and QualCoder's `_BKUP_` families)
- `restore_backup(backup_path, confirm)` - Guarded project restore (previews first; safety backup of the current state)

**Interchange:**
- `export_refi_qda(output_path, session_id, overwrite)` - Export codings (or a session's suggestions) as a REFI-QDA .qdpx for QualCoder/NVivo/ATLAS.ti/MAXQDA

**Memos & Journal (Write Operations):**
- `set_memo(target_type, target_id, memo)` - **WRITES TO DATABASE** - Write or clear a memo on a code, category, file, coding, or case (content-only, matching QualCoder — never rewrites date/owner)
- `add_journal_entry(name, entry)` - **WRITES TO DATABASE** - Add or update a research journal entry

**Codebook Editing (Write Operations):**
- `create_code(name, category, color, memo)` - **WRITES TO DATABASE** - Create a new code (colour from QualCoder's palette)
- `rename_code(code_id, new_name)` - **WRITES TO DATABASE** - Rename a code
- `recolor_code(code_id, color)` - **WRITES TO DATABASE** - Change a code's colour
- `move_code_to_category(code_id, category)` - **WRITES TO DATABASE** - Move a code into a category (omit `category` for top level)
- `create_category(name, parent_category, memo)` - **WRITES TO DATABASE** - Create a category
- `rename_category(category_id, new_name)` - **WRITES TO DATABASE** - Rename a category
- `move_category(category_id, parent_category)` - **WRITES TO DATABASE** - Reparent a category (refuses moves that would create a cycle)

**Codebook — Destructive (preview → confirm → safety backup):**
- `merge_codes(from_code_id, into_code_id, confirm)` - **WRITES TO DATABASE** - Merge one code into another (lossy on overlaps, exactly matching QualCoder; previews before confirming)
- `delete_code(code_id, confirm)` - **WRITES TO DATABASE** - Delete a code and all its coded segments (preview shows how many codings will be removed)
- `delete_category(category_id, confirm)` - **WRITES TO DATABASE** - Delete a category; its codes and sub-categories move to the top level (no cascade to coded data)

## Available Prompts

Built-in prompt templates for common analysis tasks:

- `analyze_theme(theme_name)` - Deep dive into a specific theme
- `compare_codes(code1, code2)` - Compare two codes
- `summarize_project()` - Create project overview
- `explore_case(case_name)` - Analyze a specific case

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

### "QUALCODER_PROJECT_PATH not set" Error

Make sure the `env` section in your configuration includes the `QUALCODER_PROJECT_PATH` variable with the full path to your `.qda` file.

### Testing the Server Manually

You can test the server independently:

```bash
# Activate your virtual environment
source venv/bin/activate

# Set the project path
export QUALCODER_PROJECT_PATH="/path/to/your/project.qda"

# Run the server (it will use stdio)
python -m qualcoder_mcp.server
```

The server should start without errors. Press Ctrl+C to stop.

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
- ✅ **Session tracking** - All changes logged in `~/.qualcoder_mcp/sessions/`
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
- 🔒 Workspace directory: `~/Documents/Qualcoder MCP Projects/`
- 🔒 Session logs: `~/.qualcoder_mcp/sessions/`

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
         │  guarded writes with backup + lock checks)
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
│       ├── server.py        # Main MCP server with resources, tools, prompts
│       ├── database.py      # SQLite database interface
│       ├── sessions.py      # AI coding session management
│       └── refi_export.py   # REFI-QDA XML export
├── scripts/
│   └── create_test_project.py  # Test project generator (NEW)
├── pyproject.toml           # Package configuration
├── README.md               # This file
├── CHANGELOG.md            # Version history
└── INSTALL.md              # Detailed installation guide
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
- ✅ `record_suggestions` — the AI coding loop works end-to-end via MCP tools
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
- ✅ Inductive/open coding — AI proposes brand-new codes; you refine, approve, and create them
- ✅ Review-time span editing (`edit_suggestion`) with server-computed shorter/longer alternatives
- ✅ Report exports: codebook, coded segments, frequencies, case×code matrix (CSV/txt/md, QualCoder-parity numbers)
- ✅ Annotations, category merge, case creation, attribute schema and values
- ✅ Backup retention (`prune_backups`) and opt-in CSV formula sanitization

**Completed in v0.9.0:**
- ✅ **PyPI packaging** — `pip install qualcoder-mcp` (or a one-command `pipx`/`uvx` install): no git clone, no manual venv, and updates via `pip install --upgrade`
- ✅ Whole-codebase security audit with three fixes; SHA-pinned CI actions
- ✅ Upgrade guide for existing (git-install) testers

**Completed in v0.10.0 (this release):**
- ✅ **QualCoder schema v14 through v17 support**, including full sub-code handling, determined by capability probes rather than version numbers
- ✅ Write protection for lockless QualCoder 4.0 development builds (text re-verified inside the write transaction)
- ✅ **Multi-host support (Experimental)**: the reduced core toolset (`QUALCODER_MCP_TOOLSET=core`) plus setup recipes for [LM Studio (fully local)](https://github.com/nicotem/qualcoder_mcp/blob/main/INSTALL.md#lm-studio-fully-local-experimental) and [Claude Code with an Anthropic API key](https://github.com/nicotem/qualcoder_mcp/blob/main/INSTALL.md#claude-code-with-an-anthropic-api-key-experimental)

**Planned for v0.11:**
- 🤝 QualCoder 4.0 interoperability conventions (memo privacy, attribution, coexistence with QualCoder's own built-in AI)
- 🕵️ Pseudonymisation tooling (retroactive, position-preserving)
- 🤝 Inter-coder agreement / multi-coder comparison (Cohen's Kappa)
- 🖼️ Media region coding (images, audio/video, PDF)
- 🔭 Further refinements driven by tester feedback ([file yours](https://github.com/nicotem/qualcoder_mcp/issues))

## Disclaimer

This software is provided "as is", without warranty of any kind, express or implied. The authors accept no responsibility or liability for any damage, data loss, or other issues arising from the use of this software. Users are solely responsible for ensuring the integrity and backup of their QualCoder projects. Always work on copies of your data, not originals.

## License

This project is licensed under the MIT License. See [LICENSE](https://github.com/nicotem/qualcoder_mcp/blob/main/LICENSE) file for details.

## Acknowledgments

- [Qualcoder](https://github.com/ccbogel/QualCoder) by Dr. Colin Curtain and Dr. Kai Dröge
- [Model Context Protocol](https://modelcontextprotocol.io/) by Anthropic
- [Claude Desktop](https://claude.ai/download) by Anthropic

## Support

If you encounter issues:
1. Check the Troubleshooting section above
2. Review the [MCP documentation](https://modelcontextprotocol.io/)
3. Check [Qualcoder documentation](https://github.com/ccbogel/QualCoder/wiki)
4. Open an issue on [GitHub Issues](https://github.com/nicotem/qualcoder_mcp/issues)

All support goes through GitHub Issues — not email. See
[Support & Feedback](#support--feedback) above and [SUPPORT.md](https://github.com/nicotem/qualcoder_mcp/blob/main/SUPPORT.md).

## See Also

- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [FastMCP Documentation](https://github.com/jlowin/fastmcp)
- [Qualcoder Homepage](https://qualcoder.wordpress.com/)
