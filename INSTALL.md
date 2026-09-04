# Installation Guide for Qualcoder MCP Server

This guide will walk you through installing the Qualcoder MCP server step-by-step. No prior technical knowledge required!

## What You'll Need

Before starting, make sure you have:

- ✅ **A Mac computer** (or Linux/Windows - paths will be slightly different)
- ✅ **Qualcoder installed** with at least one project created
  - Download from: https://github.com/ccbogel/QualCoder
  - Make sure you know where your `.qda` project folder is located
    (QualCoder projects are folders ending in `.qda`, with a `data.qda`
    database file inside)
  - Supported: projects from QualCoder 3.8.x and from the QualCoder
    4.0-Beta pre-release (project schemas v14 through v17); see
    "Supported QualCoder versions" in the README
- ✅ **An MCP host**: the step-by-step guide below uses Claude Desktop
  (download from: https://claude.ai/download); recipes for Claude Code
  and LM Studio follow further down
- ✅ **Python 3.10 or newer**
  - Check by opening Terminal and typing: `python3 --version`
  - If not installed, get it from: https://www.python.org/downloads/

---

## Recommended: Install from PyPI

If you just want to USE the server (no code changes), you don't need
git or this repository at all:

```bash
# Plain pip, in its own virtual environment:
python3 -m venv ~/qualcoder-mcp-venv
~/qualcoder-mcp-venv/bin/pip install qualcoder-mcp

# Or one command with pipx / uv:
pipx install qualcoder-mcp
uv tool install qualcoder-mcp
```

This gives you a `qualcoder-mcp` command; get its absolute path with
`which qualcoder-mcp` and use THAT as the `command` in the Claude
configuration of Step 6 (no `args` needed). Everything else in this
guide (project configuration, testing, updating) applies unchanged.

The step-by-step install below is the **contributor path**: use it if
you want to read or modify the source, or run the test suite.

---

## Step-by-Step Installation

### Step 1: Open Terminal

On Mac:
1. Press `Cmd + Space` to open Spotlight
2. Type "Terminal" and press Enter

### Step 2: Download the Qualcoder MCP Server

Copy and paste these commands into Terminal, one at a time:

```bash
# Go to your Documents folder
cd ~/Documents

# Download the repository
git clone https://github.com/nicotem/qualcoder_mcp.git

# Go into the folder
cd qualcoder_mcp
```

**Don't have git?** You can also:
- Download the ZIP file from GitHub
- Unzip it to your Documents folder
- Rename the folder to `qualcoder_mcp`

### Step 3: Create a Virtual Environment

A virtual environment keeps this installation separate from other Python programs on your computer.

```bash
# Create the virtual environment (this takes a minute)
python3 -m venv venv
```

Wait for it to complete (no output is normal).

### Step 4: Activate the Virtual Environment

```bash
# On Mac/Linux:
source venv/bin/activate
```

You should see `(venv)` appear at the start of your command line.

**On Windows?** Use this instead:
```bash
venv\Scripts\activate
```

### Step 5: Install the Package

```bash
# Install the Qualcoder MCP server
pip install -e .
```

This will install all necessary components. You'll see several lines of output - this is normal!

To run the test suite as well, install the development extras and run
pytest from the repository root:

```bash
pip install -e ".[dev]"
python -m pytest
```

---

## Step 6: Configure Claude Desktop

Now we need to tell Claude Desktop about the MCP server. You have two options:

### Option A: Dynamic Project Selection (Recommended)

**Best for**: People with multiple Qualcoder projects

1. **Find your username**:
   - In Terminal, type: `whoami` and press Enter
   - Remember this username - you'll need it in a moment

2. **Find the full path to your installation**:
   ```bash
   pwd
   ```
   This shows where you installed it (usually `/Users/YOUR_USERNAME/Documents/qualcoder_mcp`)

3. **Open Claude Desktop Configuration**:
   - Open Claude Desktop
   - Go to **Claude > Settings** (or just Settings)
   - Click the **Developer** tab
   - Click **Edit Config**

4. **Add this configuration** (replace YOUR_USERNAME with your actual username):

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

**Important**: If you already have other MCP servers configured, add the "qualcoder" section inside the existing `mcpServers` block, separated by a comma.

5. **Save and Close** the configuration file

### Option B: Fixed Project Path (Simpler)

**Best for**: People with one main Qualcoder project

1. **Find your .qda project folder**:
   - Open Qualcoder
   - Look at your project - note its location
   - **Important**: Qualcoder projects are **folders** with `.qda` extension, not single files
   - Each project folder contains a `data.qda` database file inside
   - Common locations:
     - `~/Documents/QualCoder_projects/MyProject/MyProject.qda/` (folder)
     - `~/QualCoder/ProjectName/ProjectName.qda/` (folder)

   Or in Terminal:
   ```bash
   # Search for .qda project folders
   find ~/Documents -name "*.qda" -type d 2>/dev/null
   ```

2. **Open Claude Desktop Configuration** (same as Option A, step 3)

3. **Add this configuration**:

```json
{
  "mcpServers": {
    "qualcoder": {
      "command": "/Users/YOUR_USERNAME/Documents/qualcoder_mcp/venv/bin/python",
      "args": ["-m", "qualcoder_mcp.server"],
      "env": {
        "QUALCODER_PROJECT_PATH": "/Users/YOUR_USERNAME/Documents/QualCoder_projects/MyProject/MyProject.qda"
      }
    }
  }
}
```

**Replace**:
- `YOUR_USERNAME` with your Mac username
- The path in `QUALCODER_PROJECT_PATH` with your actual `.qda` project
  folder (the path to the `data.qda` file inside it is accepted too).
  If the path does not exist the server refuses to start and prints
  "Error: Database file not found: <path>" to the host's log

4. **Save and Close** the configuration file

---

## Step 7: Restart Claude Desktop

1. **Completely quit** Claude Desktop:
   - Right-click the Claude icon in the Dock
   - Choose "Quit" (or press `Cmd + Q`)

2. **Reopen** Claude Desktop

3. **Verify it's working**:
   - Open a new conversation
   - Type: "List my available Qualcoder projects" (Option A) or "Give
     me a summary of my Qualcoder project" (Option B)
   - If configured correctly, Claude calls the qualcoder tools and
     answers from your project. If it says it has no such tool, the
     server is not connected: see Troubleshooting below

---

## Alternative: Claude Code and other MCP clients

Claude Desktop is not required: the server speaks standard MCP over
stdio, so **any MCP client can host it** (researchers run it under
Claude Code, including in editor side panels such as Obsidian's).

**Claude Code**: register it with one command (use the venv Python
path from Step 5):

```bash
claude mcp add qualcoder -- ~/Documents/qualcoder_mcp/venv/bin/python -m qualcoder_mcp.server
```

Or add a `.mcp.json` to the folder you run Claude Code from:

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

The optional `env` block with `QUALCODER_PROJECT_PATH` (Option B above)
works the same way in `.mcp.json`; on the command line pass it with
`-e`:

```bash
claude mcp add qualcoder -e QUALCODER_PROJECT_PATH=/path/to/MyProject.qda -- ~/Documents/qualcoder_mcp/venv/bin/python -m qualcoder_mcp.server
```

The server behaves the same under any client; which tools are
registered is decided by `QUALCODER_MCP_TOOLSET` (see "Environment
variables the server reads" below), not by the client.

---

## Environment variables the server reads

All configuration is by environment variables in the `env` block of the
server entry (Claude Desktop config, `.mcp.json`, LM Studio's mcp.json),
or with `claude mcp add -e NAME=value ...` for Claude Code. Every
variable is optional.

- `QUALCODER_PROJECT_PATH`: a project to open at start-up (Option B
  above): the folder ending in `.qda`, or the `data.qda` file inside it.
  If the path does not exist the server refuses to start and prints
  "Error: Database file not found: <path>" to stderr. Without it, select
  a project with the tools (Option A).
- `QUALCODER_MCP_TOOLSET`: `full` (default) registers all 67 tools;
  `core` registers the 20-tool supervised coding set for local models
  (see the LM Studio recipe). Any other value stops the server at
  start-up with an error naming the valid values. Resources and prompts
  are not affected.
- `QUALCODER_MCP_AI_CODER_NAME`: the coder name written on every row
  this server creates (codings, annotations, journal entries, imports,
  cases, codes, categories, attributes, and the Users entry of a REFI-QDA
  export). Default `AI Coding Assistant`. Set it to `AI Agent` to use the
  exact name QualCoder 4.0's built-in assistant writes under; that
  groups this server's work with the assistant's in 4.0's per-coder
  visibility toggle, undo and reports, which is the coherent choice for
  a project worked on by both tools. The value is trimmed and must be
  non-empty, at most 80 characters, single-line plain text (no control
  characters, no line or paragraph separators, no bidirectional
  formatting characters) and must not contain `#####`, the QualCoder 4.0
  private-memo marker. An invalid value stops the server at start-up
  with "Error: QUALCODER_MCP_AI_CODER_NAME ..." on stderr. Do not set it
  to your own QualCoder coder name: AI rows would then be
  indistinguishable from yours in QualCoder.

  ```json
  "env": {
    "QUALCODER_MCP_AI_CODER_NAME": "AI Agent"
  }
  ```

- `QUALCODER_MCP_ALLOW_UNKNOWN_SCHEMA`: expert override. Writes to a
  project whose database schema is newer than the schemas this release
  is verified against (v14 through v17, QualCoder master commit
  `9bddf17`) are refused to protect the data, and the refusal names this
  variable. Setting it to `1` lets those writes proceed; every write
  result then carries a warning. Use it only with backups you trust, and
  verify the results in QualCoder.

---

## Claude Code with an Anthropic API key (Experimental)

> **Status: Experimental.** Written from Claude Code's official
> documentation (pages verified 2026-08-17). The end-to-end run of this
> recipe is pending verification; steps may be adjusted after that pass.

Running Claude Code with an API key from the Anthropic Console, instead
of a Free/Pro/Max login, routes your usage through a different set of
terms. What that means for research data is laid out in
[PRIVACY.md](PRIVACY.md) (see "Your governance options"); this section
is only the mechanics.

**1. Install qualcoder-mcp** as described above (PyPI install
recommended).

**2. Authenticate with the API key.** Get a key from the Console at
<https://platform.claude.com/settings/keys>, then:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
claude
```

Approve the key when prompted (Claude Code asks once and remembers the
choice). If you ALSO have a Pro/Max subscription login, the
[authentication docs](https://code.claude.com/docs/en/authentication)
state that the API key takes precedence once approved; run `unset
ANTHROPIC_API_KEY` to switch back to the subscription. Verify which
credential is active with `/status`: an "API key" row appears when an
API key is in use.

**3. Register the server** (same as any Claude Code setup):

```bash
claude mcp add qualcoder -- qualcoder-mcp
```

Verify with `claude mcp list` (the server should show as Connected) and
`/mcp` inside a session. See <https://code.claude.com/docs/en/mcp>.

**4. Strict posture (optional, recommended for participant data).**
Claude Code has side channels documented on its
[data-usage page](https://code.claude.com/docs/en/data-usage): error
reporting, session surveys, `/feedback` retention, and local plaintext
transcripts under `~/.claude/projects/`. Mitigations:

```bash
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
```

and set `cleanupPeriodDays` in your Claude Code settings to shorten the
local transcript cache. Never use feedback features (thumbs, /feedback,
/bug) in sessions containing participant data.

**5. Governance note.** For unambiguous commercial-terms coverage, use
an organizational Console account rather than a personal one;
[PRIVACY.md](PRIVACY.md) quotes the two scope clauses that make the
difference and deliberately does not resolve them for you.

Caveats: the terminal interface is a real usability step down from
Claude Desktop; claude.ai connectors and the `/schedule` feature are
unavailable with a non-login credential; locally configured MCP servers
like this one work identically.

---

## LM Studio (fully local) (Experimental)

> **Status: Experimental.** Written from LM Studio's official
> documentation (pages verified 2026-08-17; instructions written against
> LM Studio 0.4.21, which requires 0.3.17 or newer for MCP). Hands-on
> verification of this recipe is pending, and we have not yet evaluated
> how well any local model performs with this server. Expect to
> supervise closely and report what you find.

[LM Studio](https://lmstudio.ai) runs open-weight models entirely on
your machine and can host MCP servers. With this setup your interview
data, your codings, and the model itself all stay on your computer. LM
Studio's own documentation states that it "can operate entirely
offline" and that "Nothing you enter into LM Studio when chatting with
LLMs leaves your device" (<https://lmstudio.ai/docs/app/offline>,
quoted 2026-08-17). That is their statement, not our certification:
verify offline operation yourself (Step 7) if your data-management plan
depends on it.

Requirements: a machine that can run a mid-size local model (16 GB RAM
is a realistic minimum), LM Studio installed, Python 3.10+.

**Step 1. Install qualcoder-mcp** (same as for any host):

```bash
pipx install qualcoder-mcp
# or: python3 -m venv ~/qualcoder-mcp-venv && ~/qualcoder-mcp-venv/bin/pip install qualcoder-mcp
```

Find the absolute path of the command (`which qualcoder-mcp`). LM
Studio launches MCP servers itself and may not see your shell's PATH,
so the config below must use the absolute path.

**Step 2. Download a tool-capable model.** In LM Studio's Discover tab,
pick a model with the hammer badge (native tool use). LM Studio's docs
list Qwen2.5-7B-Instruct, Llama-3.1-8B-Instruct, and Ministral-8B as
examples and warn that "Smaller models and models that were not trained
for tool use may output improperly formatted tool calls"
(<https://lmstudio.ai/docs/developer/openai-compat/tools>). Community
reports place the practical minimum for many-tool MCP work around 14B
parameters. We have not evaluated specific models with this server;
that evaluation is planned, which is one reason this recipe is marked
Experimental.

**Step 3. Use the core toolset.** This server exposes 67 tools by
default, and the serialized tool definitions alone measure about
118,000 characters, roughly 29k tokens (measured for v0.11.0-alpha).
That exceeds LM Studio's 8k default context several times over before
you type a word, and tool counts this size are far past where
small-model tool selection degrades. Set `QUALCODER_MCP_TOOLSET=core`
(in the config of Step 5) to register only the 20-tool supervised
coding set, measured at about 44,000 characters, roughly 11k tokens.

**Step 4. Raise the context length.** Even the core toolset's roughly
11k tokens of schema exceed the 8k default context. When loading the
model, set the context length to at least 16k for the core toolset
(that leaves about 5k tokens for your transcript excerpts and
conversation; 32k is more comfortable), or 64k if you must run the
full surface (its schema alone is about 29k tokens). Use the model
load settings dialog or a per-model default
(<https://lmstudio.ai/docs/app/advanced/per-model>).

**Step 5. Add the server.** Open the Program tab in the right sidebar,
click Install > Edit mcp.json, and add (LM Studio follows Cursor's
mcp.json notation, per <https://lmstudio.ai/docs/app/mcp>):

```json
{
  "mcpServers": {
    "qualcoder": {
      "command": "/Users/YOUR_USERNAME/qualcoder-mcp-venv/bin/qualcoder-mcp",
      "env": {
        "QUALCODER_PROJECT_PATH": "/Users/YOUR_USERNAME/Documents/QualCoder_projects/MyProject/MyProject.qda",
        "QUALCODER_MCP_TOOLSET": "core"
      }
    }
  }
}
```

With a source (git) install, use `"command":
"/path/to/qualcoder_mcp/venv/bin/python"` with `"args": ["-m",
"qualcoder_mcp.server"]` and the same `env` block. Replace the paths
with your own; if the file already has other entries under
`mcpServers`, add only the `"qualcoder"` block. LM Studio loads the
server when you save.

**Step 6. Keep tool confirmations on.** When the model calls a tool, LM
Studio shows a confirmation dialog where you can inspect and edit the
arguments and allow the call once or always. Keep confirmations on:
they are your audit point for what the model is doing to your project.
If your LM Studio build offers per-chat or per-tool toggles for MCP
servers, disable the server in chats that do not need it (we have not
yet click-verified the exact toggle granularity in the current build;
this sentence will be updated after the hands-on pass).

**Step 7. Verify offline (recommended for data-governance records).**
Disconnect from the network and work. Model inference, chats, and all
qualcoder-mcp operations are local; LM Studio states it needs the
internet only for model search/downloads, runtime downloads, and update
checks (<https://lmstudio.ai/docs/app/offline>). A note that you
verified this yourself is good evidence for a data-management plan.

**What to expect (honest, unverified).** We have not yet evaluated
local models with this server, which is why the feature is
Experimental. From the published evidence on many-tool MCP use, expect
a narrower workflow than with Claude: use the core toolset, work one
document or one code at a time, and verify codings as you go. Long
transcripts should be worked in sections. Multi-step batch operations
(recode across a project, cross-case reports) are not realistic
targets for local models today. Nothing leaves your machine; the
tradeoff is that you supervise more, and until an evaluation exists,
treat every result as needing review.

Troubleshooting: a context overflow typically appears as the model
ignoring tools, emitting malformed tool calls, or the host reporting an
overflow; lower the tool surface (core mode), raise the context, or
shorten the chat. After editing mcp.json or upgrading the package,
toggle the server off and on or restart LM Studio (community-reported
stdio lifecycle rough edges: lmstudio-bug-tracker issues #731, #732).

---

## Other MCP hosts

Any MCP host that can run local stdio servers can host qualcoder-mcp
with the same command/env pattern shown above. Recipes for other
open-source hosts are planned once they have been tested hands-on;
technically comfortable users can adapt the pattern today.

---

## Testing Your Installation

### If you used Option A (Dynamic):

In Claude Desktop, try:
```
List my available Qualcoder projects
```

Claude should show you all `.qda` files it found. Then:
```
Select the "MyProject" project
```

### If you used Option B (Fixed):

In Claude Desktop, try:
```
Give me a summary of my Qualcoder project
```

Claude should respond with information about your project!

### Try Some Queries

```
What codes do I have in my project?

Show me all files in my project

What are the most frequently used codes?

Analyze the transcript for file 1 with all its coding
```

---

## Troubleshooting

### "The server isn't responding"

1. **Check your paths**:
   - Make sure the Python path is correct in your config
   - In Terminal with venv activated, type: `which python`
   - Use that full path in your Claude config

2. **Check your .qda project path** (Option B only):
   - Make sure the folder exists: `ls -ld /path/to/your/project.qda`
   - Make sure the database file exists inside: `ls /path/to/your/project.qda/data.qda`
   - Make sure the path is absolute (starts with `/Users/...`)

3. **Check Claude Desktop logs**:
   - Settings > Developer > Show Logs
   - Look for errors related to "qualcoder"

### "No Qualcoder projects found" (Option A)

The server searches these locations by default:
- `~/Documents/QualCoder_projects`
- `~/Documents/QualCoder`
- `~/QualCoder`
- `~/Documents`

Make sure your `.qda` file is in one of these locations, or tell Claude to search elsewhere:
```
List available projects in ["/path/to/your/projects"]
```

### "No Qualcoder project selected" (Option A)

With dynamic project selection this is normal at the start of a
session: the server has no project open until one is selected. The
error reads "No Qualcoder project selected. Use 'list_available_projects'
to discover projects, then 'select_project' to choose one. Or set
QUALCODER_PROJECT_PATH environment variable." (`get_current_project`
says "No project currently open" instead.) Just select a project:
```
List my available Qualcoder projects
Select the "ProjectName" project
```

When a project was selected before on this machine and still exists,
the same error ends with "The last project used on this machine was
<path>. Use select_project with that path to continue with it." That
pointer is read from `~/.qualcoder_mcp/mru_project.json`, which
`select_project` writes on every successful selection; the selection is
never restored automatically, so one `select_project` call is still
needed. If the error comes back in the middle of a conversation, the
host has restarted the server process between turns and the in-memory
selection was lost; the hint gets you back with one call. For
single-project work, pinning `QUALCODER_PROJECT_PATH` in the server's
`env` block (Option B) avoids that round trip.

### Python Not Found

If you get "python command not found":

1. Install Python from https://www.python.org/downloads/
2. Or install via Homebrew (any Python 3.10 or newer works; the test
   matrix runs 3.10 and 3.13):
   ```bash
   brew install python
   ```

### Permission Denied Errors

If you get permission errors:

```bash
# The project is a folder: reading needs read and traverse permission,
# and the coding tools need write permission on the folder and on its
# parent (backups are created next to the project)
chmod -R u+rwX /path/to/your/project.qda

# Or check who owns it
ls -ld /path/to/your/project.qda
```

### Tools missing or unchanged after an upgrade

The client starts the server once per session and reads the tool list
at that moment. After `pip install --upgrade` (or `git pull` and
reinstall), fully quit the client and reopen it (Claude Desktop: Cmd+Q,
not just closing the window; Claude Code: end the session and start a
new one; LM Studio: toggle the server off and on in mcp.json, or
restart LM Studio). Until then the old process, with the old tool list,
keeps running.

### Reading the server log

The server writes its log lines (INFO and above) to standard error; the
host decides where that goes. Claude Desktop shows it under Settings >
Developer > Show Logs. LM Studio on macOS persists it into
`~/Library/Logs/LM Studio/main.log`; search that file for
`qualcoder_mcp` to find the server's start-up lines (which report the
toolset mode and the number of tools registered) and any errors. The
log never contains memo text; it does carry project file names, code
and case names and, for a database that will not open, the SQLite
error text.

---

## What to Do After Installation

### Learn What You Can Do

Check out the main README.md for:
- Example queries and prompts
- Full list of available tools
- Advanced features (co-occurrence analysis, demographics, etc.)

### Try some richer queries

**Rich Transcript Analysis**:
```
Analyze file 3 with all its coding. What does this participant say about motivation?
```

**Demographic Queries**:
```
Show me all participants over age 50
Which cases have education_level "graduate"?
```

**Pattern Discovery**:
```
What codes appear together with "workplace stress"?
Create a case-code matrix
```

---

## Updating the MCP Server

Updates are manual (a new release does not install itself).

**PyPI install**, one command:

```bash
~/qualcoder-mcp-venv/bin/pip install --upgrade qualcoder-mcp
# pipx:  pipx upgrade qualcoder-mcp
# uv:    uv tool upgrade qualcoder-mcp
```

**Git (contributor) install**, when new versions are released:

```bash
# Go to the installation folder
cd ~/Documents/qualcoder_mcp

# Activate the virtual environment
source venv/bin/activate

# Pull the latest changes
git pull

# Reinstall
pip install -e .
```

Then **fully quit and relaunch your MCP client** (Claude Desktop:
Cmd+Q, then reopen; Claude Code: restart the session; LM Studio: toggle
the server off and on in mcp.json, or restart LM Studio). New tools
only appear after the restart; the client launches the server once per
session and reads its tool list then.

To confirm the update took, check the installed version from the
terminal:

```bash
~/qualcoder-mcp-venv/bin/pip show qualcoder-mcp          # PyPI venv
# pipx:  pipx list
# uv:    uv tool list
# git:   ~/Documents/qualcoder_mcp/venv/bin/pip show qualcoder-mcp
```

The server also reports its version to the host in the MCP handshake
(`serverInfo.version`); whether the assistant can see and repeat it
depends on the host, so asking Claude "what version is running?" is a
convenience, not proof.

Updating never touches your data: the server is code-only, and your
QualCoder projects and backups stay exactly where they are.

---

## Upgrading from an earlier (git) install

*For everyone who installed a pre-0.9 version with `git clone` +
`pip install -e .` and configured their Claude client with
`venv/bin/python` + `"args": ["-m", "qualcoder_mcp.server"]`.*

**First, the reassurance: upgrading only replaces the SERVER code.**
It never touches your QualCoder projects (the `.qda` folders) or your
files under `~/.qualcoder_mcp/` (the AI-coding session files in
`sessions/`, and the last-used project pointer `mru_project.json`); all
live outside the install, and the projects and session files were
verified untouched across every install/upgrade path below. Jumping
from 0.6, 0.7 or 0.8 straight to
0.9 in one step is fine: there is no data or session migration step,
and pre-0.9 session files load unchanged (verified end-to-end, a
0.6-era session file drives the full current workflow).

You have two paths. Both work; pick one.

### Path A: stay on the git install (simplest, no config change)

```bash
cd ~/Documents/qualcoder_mcp   # your clone
git pull
venv/bin/pip install -e .
```

Then fully quit and relaunch your Claude client. Your existing
configuration keeps working unchanged, forever. Good if you don't want
to touch your setup.

### Path B: switch to the PyPI install (recommended going forward)

*Available from v0.9.0 (the first release published to PyPI).*

**Use a FRESH environment. Do not install into the old clone's venv.**
(If you run `pip install qualcoder-mcp` inside the old venv, pip sees
the editable install, reports "Requirement already satisfied", and
silently does nothing, so you would still be running the old code.
Verified behaviour, and the reason these instructions exist.)

**1. Install into a fresh venv (or pipx/uv):**

```bash
python3 -m venv ~/qualcoder-mcp-venv
~/qualcoder-mcp-venv/bin/pip install qualcoder-mcp
# or:  pipx install qualcoder-mcp
# or:  uv tool install qualcoder-mcp
```

**2. Find the command path:**

```bash
ls ~/qualcoder-mcp-venv/bin/qualcoder-mcp   # plain venv
which qualcoder-mcp                          # pipx / uv
```

**3. Update your Claude client config**: change `command` to that
path and REMOVE the `args` line.

Claude Desktop, before:

```json
{
  "mcpServers": {
    "qualcoder": {
      "command": "/Users/YOU/Documents/qualcoder_mcp/venv/bin/python",
      "args": ["-m", "qualcoder_mcp.server"]
    }
  }
}
```

Claude Desktop, after:

```json
{
  "mcpServers": {
    "qualcoder": {
      "command": "/Users/YOU/qualcoder-mcp-venv/bin/qualcoder-mcp"
    }
  }
}
```

(Keep your `env` block with `QUALCODER_PROJECT_PATH`, if you had one;
it works the same.)

Claude Code: re-register once:

```bash
claude mcp remove qualcoder
claude mcp add qualcoder -- ~/qualcoder-mcp-venv/bin/qualcoder-mcp
```

(or edit `.mcp.json` the same way as the Desktop config above).

**4. Fully quit and relaunch the client**, then confirm the installed
version with `~/qualcoder-mcp-venv/bin/pip show qualcoder-mcp` (or
`pipx list` / `uv tool list`). Whether the assistant can also tell you
the running version depends on the host (see "Updating the MCP
Server" above).

**5. Optional cleanup, once the new install is confirmed working:**
delete the old clone and its venv. Keeping them around breaks nothing.

<details>
<summary>Insisting on reusing the old venv? (works, but read this)</summary>

The plain install silently no-ops (above), so you must either upgrade
explicitly:

```bash
~/Documents/qualcoder_mcp/venv/bin/pip install --upgrade qualcoder-mcp
```

or uninstall the editable first:

```bash
~/Documents/qualcoder_mcp/venv/bin/pip uninstall qualcoder-mcp
~/Documents/qualcoder_mcp/venv/bin/pip install qualcoder-mcp
```

Both verified: pip cleanly removes the editable hooks and the wheel
takes over (your existing `venv/bin/python -m qualcoder_mcp.server`
config even keeps working). The catch, and why the fresh venv is
recommended instead: from that moment `git pull` in the clone no
longer affects what runs, which is a confusing state to leave lying
around.
</details>

---

## Getting Help

- **Problems with this server**: check the Troubleshooting section
  above and the README's troubleshooting section, then open an issue on
  [GitHub Issues](https://github.com/nicotem/qualcoder_mcp/issues).
  That is the only support channel (email requests receive no reply);
  see [SUPPORT.md](SUPPORT.md). Never paste research data into an
  issue; a redacted or synthetic example is enough. Include your
  QualCoder version, the server version, your host (Claude Desktop,
  Claude Code, LM Studio) and the toolset mode (full or core).
- **MCP Documentation**: https://modelcontextprotocol.io/
- **Qualcoder Help**: https://github.com/ccbogel/QualCoder/wiki
- **Claude Desktop**: https://claude.ai/help

---

## Uninstalling

If you want to remove the MCP server:

1. **Remove it from your client**:
   - Claude Desktop: Settings > Developer > Edit Config, delete the
     "qualcoder" section, save, then fully quit and reopen Claude Desktop
   - Claude Code: `claude mcp remove qualcoder`
   - LM Studio: delete the "qualcoder" block from mcp.json

2. **Remove the package**:
   ```bash
   # PyPI install in its own venv: delete the venv
   rm -rf ~/qualcoder-mcp-venv
   # pipx:  pipx uninstall qualcoder-mcp
   # uv:    uv tool uninstall qualcoder-mcp
   # Git (contributor) install: delete the clone (its venv is inside it)
   rm -rf ~/Documents/qualcoder_mcp
   ```

3. **Optionally remove the server's own state**: `~/.qualcoder_mcp/`
   holds the AI-coding session files (`sessions/`) and the last-used
   project pointer (`mru_project.json`). Nothing else is stored there.

Uninstalling does not touch your QualCoder projects. Note that the
server does write to projects when you use its coding tools (always
after taking a backup), so the changes you approved during use, and the
backup folders it created next to each project
(`<project>_backup_<timestamp>.qda`), stay where they are. Remove
backups you no longer need with the `prune_backups` tool before
uninstalling, or by hand afterwards. Workspace copies made with
`copy_project_to_workspace` live in `~/Documents/Qualcoder MCP
Projects/`.

---

## Next Steps

Now that you're installed, you can:

1. ✅ Explore your Qualcoder data with natural language queries
2. ✅ Get AI-assisted thematic analysis
3. ✅ Discover patterns and relationships in your coding
4. ✅ Query by demographics and attributes
5. ✅ Analyze complete transcripts with coding context

**Happy analyzing!** 🎉
