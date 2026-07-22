# Installation Guide for Qualcoder MCP Server

This guide will walk you through installing the Qualcoder MCP server step-by-step. No prior technical knowledge required!

## What You'll Need

Before starting, make sure you have:

- ✅ **A Mac computer** (or Linux/Windows - paths will be slightly different)
- ✅ **Qualcoder installed** with at least one project created
  - Download from: https://github.com/ccbogel/QualCoder
  - Make sure you know where your `.qda` project file is located
- ✅ **Claude Desktop installed**
  - Download from: https://claude.ai/download
- ✅ **Python 3.10 or newer**
  - Check by opening Terminal and typing: `python3 --version`
  - If not installed, get it from: https://www.python.org/downloads/

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
- The path in `QUALCODER_PROJECT_PATH` with your actual `.qda` file location

4. **Save and Close** the configuration file

---

## Step 7: Restart Claude Desktop

1. **Completely quit** Claude Desktop:
   - Right-click the Claude icon in the Dock
   - Choose "Quit" (or press `Cmd + Q`)

2. **Reopen** Claude Desktop

3. **Verify it's working**:
   - Open a new conversation
   - Type: "Can you see my Qualcoder project?"
   - If configured correctly, Claude should be able to access your data!

---

## Alternative: Claude Code and other MCP clients

Claude Desktop is not required — the server speaks standard MCP over
stdio, so **any MCP client can host it** (researchers run it under
Claude Code, including in editor side panels such as Obsidian's).

**Claude Code** — register it with one command (use the venv Python
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
works the same way. Every feature behaves identically in any client.

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

### "QUALCODER_PROJECT_PATH not set" (Option A)

This is normal! Just use the tools to select a project:
```
List my available Qualcoder projects
Select the "ProjectName" project
```

### Python Not Found

If you get "python command not found":

1. Install Python from https://www.python.org/downloads/
2. Or install via Homebrew:
   ```bash
   brew install python@3.10
   ```

### Permission Denied Errors

If you get permission errors:

```bash
# Make sure the file is readable
chmod +r /path/to/your/project.qda

# Or check who owns it
ls -l /path/to/your/project.qda
```

---

## What to Do After Installation

### Learn What You Can Do

Check out the main README.md for:
- Example queries and prompts
- Full list of available tools
- Advanced features (co-occurrence analysis, demographics, etc.)

### Try the New Features (v0.2.0)

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

When new versions are released:

```bash
# Go to the installation folder
cd ~/Documents/qualcoder_mcp

# Activate the virtual environment
source venv/bin/activate

# Pull the latest changes
git pull

# Reinstall
pip install -e .

# Restart Claude Desktop
```

---

## Getting Help

- **Technical Issues**: Check the main README.md troubleshooting section
- **MCP Documentation**: https://modelcontextprotocol.io/
- **Qualcoder Help**: https://github.com/ccbogel/QualCoder/wiki
- **Claude Desktop**: https://claude.ai/help

---

## Uninstalling

If you want to remove the MCP server:

1. **Remove from Claude Desktop**:
   - Settings > Developer > Edit Config
   - Delete the "qualcoder" section
   - Save and restart

2. **Delete the files**:
   ```bash
   rm -rf ~/Documents/qualcoder_mcp
   ```

Your Qualcoder projects are **never modified** by this server, so they'll remain intact!

---

## Next Steps

Now that you're installed, you can:

1. ✅ Explore your Qualcoder data with natural language queries
2. ✅ Get AI-assisted thematic analysis
3. ✅ Discover patterns and relationships in your coding
4. ✅ Query by demographics and attributes
5. ✅ Analyze complete transcripts with coding context

**Happy analyzing!** 🎉
