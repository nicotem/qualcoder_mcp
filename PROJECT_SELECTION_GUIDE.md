# Project Selection Guide

The Qualcoder MCP server now supports **two ways** to work with projects:

## Option 1: Auto-Discovery (Recommended for Multiple Projects)

If you work with multiple Qualcoder projects, you can skip hardcoding the path and let Claude discover and switch between projects dynamically.

### Setup

In your `claude_desktop_config.json`, **don't set** the `QUALCODER_PROJECT_PATH`:

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

Note: No `env` section!

### Usage

When you start Claude Desktop, ask:

```
List my available Qualcoder projects
```

Claude will search these locations automatically:
- `~/Documents/QualCoder_projects/`
- `~/Documents/QualCoder/`
- `~/QualCoder/`
- `~/Documents/`

You'll get a list like:

```json
{
  "project_count": 3,
  "projects": [
    {
      "name": "Interview Study 2024",
      "path": "/Users/you/Documents/QualCoder_projects/InterviewStudy2024/InterviewStudy2024.qda",
      "directory": "/Users/you/Documents/QualCoder_projects/InterviewStudy2024",
      "size_mb": 15.3,
      "modified": 1698765432.0
    },
    {
      "name": "Focus Groups",
      "path": "/Users/you/Documents/QualCoder/FocusGroups/FocusGroups.qda",
      "directory": "/Users/you/Documents/QualCoder/FocusGroups",
      "size_mb": 8.7,
      "modified": 1698654321.0
    }
  ]
}
```

Then select a project:

```
Select the "Interview Study 2024" project
```

Or use the full path:

```
Select the project at /Users/you/Documents/QualCoder_projects/InterviewStudy2024/InterviewStudy2024.qda
```

### Switch Projects Anytime

```
Switch to "Focus Groups" project
```

```
What project am I currently working with?
```

### Custom Search Locations

If your projects are in a non-standard location:

```
Search for projects in /Volumes/External/Research/QualcoderProjects
```

## Option 2: Fixed Project (Simpler Setup)

If you only work with **one project**, you can hardcode it in the config for convenience.

### Setup

In your `claude_desktop_config.json`:

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

### Usage

Claude will automatically connect to your project on startup. No need to select anything - just start asking questions:

```
Give me a summary of my project
```

```
What are my most used codes?
```

## Comparison

| Feature | Auto-Discovery | Fixed Project |
|---------|---------------|---------------|
| **Setup** | Easier (no paths to configure) | Requires full path |
| **Multiple Projects** | ✅ Switch anytime | ✅ Switch anytime too (`select_project` works; the variable only sets the start-up project) |
| **First Use** | Need to select project | Immediate access |
| **Best For** | Researchers with multiple projects | Single project workflows |

## Troubleshooting

### "No Qualcoder projects found"

If auto-discovery doesn't find your projects:

1. **Check your Qualcoder installation:**
   - Open Qualcoder
   - Look at recent projects to see where they're stored
   - Note the full path to a `.qda` project folder

2. **Tell Claude to search there:**
   ```
   Search for Qualcoder projects in /path/to/my/projects
   ```

3. **Or switch to Fixed Project mode** (Option 2 above)

### "No project currently open"

If you're using auto-discovery and haven't selected a project:

```
List available projects
Select the first project
```

When a project was selected before on this machine and still exists, the
error also names it ("The last project used on this machine was
<path>"), so one `select_project` call with that path recovers. The
pointer lives in `~/.qualcoder_mcp/mru_project.json`; nothing is
selected automatically.

### Can't find a specific project

List all projects to see what Claude can find:

```
List all my Qualcoder projects
```

Then select by name or path:

```
Select "MyProjectName"
```

Or if there are multiple with similar names:

```
Select the project in /full/path/to/project.qda
```

## New MCP Tools

The project selection feature adds three new tools:

### 1. `list_available_projects`
Discovers `.qda` project folders in common locations

**Optional parameter:**
- `search_directories`: List of custom paths to search

### 2. `select_project`
Opens a specific Qualcoder project

**Required parameter:**
- `project_path`: Path to the `.qda` project folder (or to the `data.qda` file inside it)

The result reports `qualcoder_gui_signals` (best-effort heuristics for an
open QualCoder 4.0 window; a released QualCoder 3.x is detected through
its lock file) and remembers the selection as the last-used project.

### 3. `get_current_project`
Shows which project is currently open, whether a released QualCoder has
it open (`qualcoder_open`, from its lock file) and the QualCoder 4.0
heuristics (`qualcoder_gui_signals`)

**No parameters**

## Example Workflow

```
# Start fresh
You: List my Qualcoder projects

Claude: I found 3 projects:
1. Interview Study 2024 (15.3 MB, last modified today)
2. Focus Groups (8.7 MB, last modified yesterday)
3. Pilot Study (3.2 MB, last modified last week)

Which would you like to work with?

# Select project
You: Open the Interview Study project

Claude: ✓ Switched to "Interview Study 2024"

This project contains:
- 15 source files
- 47 codes in 8 categories
- 342 coded segments
- Last updated: 2024-10-28

What would you like to analyze?

# Now you can work normally
You: Show me the most frequently used codes

Claude: Here are your top codes:
1. "participant motivation" - 67 segments
2. "workplace culture" - 54 segments
...

# Switch projects without restarting Claude
You: Switch to the Focus Groups project

Claude: ✓ Switched to "Focus Groups"
...
```

## Recommendation

- **If you have multiple projects**: Use Option 1 (Auto-Discovery)
- **If you have one main project**: Use Option 2 (Fixed Project)
- **If unsure**: Start with Option 1 - it's more flexible!

You can always change your config later by editing `claude_desktop_config.json` and restarting Claude Desktop.
