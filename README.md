# Qualcoder MCP Server

A Model Context Protocol (MCP) server that connects [Claude Desktop](https://claude.ai/download) to [Qualcoder](https://github.com/ccbogel/QualCoder), enabling AI-assisted qualitative data analysis.

## What is this?

This MCP server allows Claude (via Claude Desktop) to directly access and analyze your Qualcoder projects. Claude can:

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
- 🤖 **AI-assisted coding: Conversational approval workflow** (NEW in v0.4.0!)
- ✅ **Review and approve suggestions in chat before applying** (NEW in v0.4.0!)
- 📥 **Direct database writes with automatic backups** (NEW in v0.4.0!)

You can work with read-only analysis OR use write-enabled AI coding. All AI coding operations include automatic backups for safety.

## Prerequisites

- **macOS** (or Linux/Windows with appropriate paths)
- **Python 3.10 or higher**
- **Claude Desktop** installed ([download here](https://claude.ai/download))
- **Qualcoder** with at least one project created ([download here](https://github.com/ccbogel/QualCoder))

## Installation

### Step 1: Clone or Download This Repository

```bash
cd ~/Documents  # or wherever you want to install
git clone https://github.com/nicotem/qualcoder_mcp.git
cd qualcoder_mcp
```

### Step 2: Create a Virtual Environment and Install

```bash
# Create virtual environment
python3 -m venv venv

# Activate it
source venv/bin/activate  # On Mac/Linux
# or on Windows: venv\Scripts\activate

# Install the package
pip install -e .
```

### Step 3: Configure Claude Desktop

You have **two options** for configuring project access:

#### Option A: Dynamic Project Selection (Recommended for Multiple Projects)

If you work with multiple Qualcoder projects, this is the easiest approach - Claude will discover projects and let you switch between them.

**Configuration** (no project path needed):

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

**Replace**: `YOUR_USERNAME` with your actual Mac username

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

See [`PROJECT_SELECTION_GUIDE.md`](PROJECT_SELECTION_GUIDE.md) for full details.

#### Option B: Fixed Project (Simpler for Single Project)

If you work with one main project, you can hardcode the path for instant access.

**Find Your Project Path**:
Your Qualcoder project is a **folder** with a `.qda` extension containing a `data.qda` database file. Typical locations:
- `~/Documents/QualCoder_projects/MyProject/MyProject.qda/` (folder)
- `~/QualCoder/ProjectName/ProjectName.qda/` (folder)

**Configuration**:

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
- `YOUR_USERNAME` - your actual Mac username
- `/Users/YOUR_USERNAME/Documents/qualcoder_mcp` - where you installed this
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

The MCP server should now be connected! You'll see it listed in the MCP section if you look at the settings.

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

**NEW in v0.4.0!** Claude can now help you code your qualitative data with a conversational approval workflow. You chat with Claude, review suggestions together, and directly write approved codings to your database.

### Conversational Workflow

**Important**: AI coding writes directly to the database. Always work on copies in the `~/Documents/Qualcoder MCP Projects/` workspace folder. Automatic backups are created before every write.

### Quick Start Example

**Step 1: Copy Project to Workspace**
```
Copy my project "Interview Study" to the workspace for AI coding
```

**Step 2: Analyze Files**
```
Analyze files 1-3 for WORKPLACE-STRESS and COPING-STRATEGIES codes
```

Claude will:
- Create an analysis session
- Examine the files
- Identify relevant segments
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
- Create automatic backup
- Write approved codings to database
- Report success with coding IDs
- You can immediately open the project in Qualcoder to see results!

### Key Features

- **Conversational Review**: Discuss suggestions with Claude before applying
- **Confidence Scoring**: Each suggestion includes a 0.0-1.0 confidence score with reasoning
- **Session Persistence**: Resume work anytime, all sessions saved to disk
- **Automatic Backups**: Every write creates a timestamped backup first
- **Workspace Isolation**: Work on copies in dedicated workspace folder
- **Direct Database Writes**: No import/export - codings appear instantly in Qualcoder
- **Granular Control**: Approve/reject individual suggestions by GUID
- **Full Context**: See surrounding text for each suggestion

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

For comprehensive workflow documentation, see [AI_CODING_WORKFLOW.md](AI_CODING_WORKFLOW.md).

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

**Project Management:**
- `list_available_projects(search_directories)` - Discover Qualcoder projects on your system
- `select_project(project_path)` - Open/switch to a different project
- `get_current_project()` - Show which project is currently open

**Core Data Analysis:**
- `search_files(pattern, search_filename, search_content, search_memo)` - **NEW**: Find files by name, content, or memo with smart clarification workflow
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

**AI-Assisted Coding (NEW in v0.4.0 - Conversational Workflow):**
- `analyze_for_coding(file_ids, code_names, instruction, min_confidence)` - Prepare analysis session for Claude to perform coding suggestions
- `review_suggestions(session_id, suggestion_guids, show_context)` - Show detailed information about specific suggestions
- `update_suggestion_status(session_id, approve, reject)` - Approve or reject suggestions by GUID
- `apply_codings(session_id, create_backup, owner)` - **WRITES TO DATABASE** - Apply approved suggestions with automatic backup
- `get_coding_session_info(session_id)` - View all details of a coding session
- `list_coding_sessions(project_path, days_old)` - List all saved coding sessions
- `delete_coding_session(session_id)` - Delete a saved session

**Project Management (Write Operations):**
- `copy_project_to_workspace(source_path, new_name)` - Copy a project to the safe workspace for AI coding
- `backup_project(project_path)` - Create manual timestamped backup

**Legacy Tools (Deprecated - use conversational workflow above):**
- `suggest_coding_for_files()` - Old REFI-QDA export approach
- `export_coding_suggestions()` - Old export tool
- `suggest_new_codes()` - Old code discovery
- `export_new_codes_for_import()` - Old code export

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
- 🔒 Data stays local - never sent anywhere except to Claude via MCP
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
         │ Read-only SQLite connection
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
│       ├── sessions.py      # AI coding session management (NEW)
│       └── refi_export.py   # REFI-QDA XML export (NEW)
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

**Future Enhancements (v0.5.0+):**
- [ ] HTML review interface for visual approval/rejection of suggestions
- [ ] Batch operations for multiple files at once
- [ ] Automatic chunking for large files
- [ ] Support for code-code relationships/links (graph data)
- [ ] Network visualization data export
- [ ] More statistical reports (Cohen's Kappa, inter-rater reliability)
- [ ] Media segment access (images, audio, video)
- [ ] Timeline analysis
- [ ] Saved queries execution
- [ ] Batch export functionality

## License

This project is licensed under the MIT License. See LICENSE file for details.

## Acknowledgments

- [Qualcoder](https://github.com/ccbogel/QualCoder) by Dr. Colin Curtain and Dr. Kai Dröge
- [Model Context Protocol](https://modelcontextprotocol.io/) by Anthropic
- [Claude Desktop](https://claude.ai/download) by Anthropic

## Support

If you encounter issues:
1. Check the Troubleshooting section above
2. Review the [MCP documentation](https://modelcontextprotocol.io/)
3. Check [Qualcoder documentation](https://github.com/ccbogel/QualCoder/wiki)
4. Open an issue on GitHub

## See Also

- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [FastMCP Documentation](https://github.com/jlowin/fastmcp)
- [Qualcoder Homepage](https://qualcoder.wordpress.com/)
