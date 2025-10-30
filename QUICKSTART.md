# Quick Start Guide

This guide will get you up and running with the Qualcoder MCP server in 10 minutes.

## Prerequisites Checklist

- [ ] Python 3.10 or higher installed
- [ ] Claude Desktop installed
- [ ] At least one Qualcoder project created (`.qda` file)

## Installation Steps

### 1. Install the MCP Server

```bash
# Navigate to where you want to install (e.g., Documents)
cd ~/Documents

# Clone or download this repository
git clone https://github.com/YOUR_USERNAME/qualcoder_mcp.git
cd qualcoder_mcp

# Create virtual environment
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate

# Install dependencies
pip install -e .
```

### 2. Find Your Qualcoder Project

Locate your `.qda` project folder (it's a folder with `.qda` extension, not a single file). Common locations:
- `~/Documents/QualCoder_projects/MyProject/MyProject.qda/`
- `~/QualCoder/MyProject/MyProject.qda/`

You can find it by:
- Opening Qualcoder and checking the recent projects list
- Searching for `.qda` folders: `find ~ -name "*.qda" -type d 2>/dev/null`

### 3. Configure Claude Desktop

**Get the paths you'll need:**

```bash
# Get Python path (while virtual environment is active)
which python
# Example output: /Users/yourname/Documents/qualcoder_mcp/venv/bin/python

# Get your username
whoami
# Example output: yourname
```

**Edit the configuration:**

On Mac, open:
```bash
open ~/Library/Application\ Support/Claude/
```

Or via Claude Desktop: Settings > Developer > Edit Config

**Add this to `claude_desktop_config.json`:**

```json
{
  "mcpServers": {
    "qualcoder": {
      "command": "/Users/yourname/Documents/qualcoder_mcp/venv/bin/python",
      "args": ["-m", "qualcoder_mcp.server"],
      "env": {
        "QUALCODER_PROJECT_PATH": "/Users/yourname/Documents/QualCoder_projects/MyProject/MyProject.qda"
      }
    }
  }
}
```

Replace:
- `yourname` with your actual username
- The paths with your actual paths from steps above

### 4. Restart Claude Desktop

1. Quit Claude Desktop completely (Cmd+Q)
2. Reopen Claude Desktop
3. The MCP should now be connected!

### 5. Test It Out

In Claude Desktop, try these prompts:

```
Can you give me a summary of my Qualcoder project?
```

```
What codes do I have?
```

```
Show me the most frequently used codes
```

## Troubleshooting

### Connection Issues

If Claude can't connect:

1. **Check the config file syntax** - make sure JSON is valid (commas, quotes, brackets)
2. **Verify paths** - make sure all paths are absolute and correct
3. **Check Python path** - activate venv and run `which python`
4. **Check .qda project** - make sure the folder exists: `ls -ld /path/to/your/project.qda`
5. **Restart Claude** - always restart after config changes

### Test the Server Manually

```bash
cd ~/Documents/qualcoder_mcp
source venv/bin/activate
export QUALCODER_PROJECT_PATH="/path/to/your/project.qda"
python -m qualcoder_mcp.server
```

You should see it start without errors. Press Ctrl+C to stop.

### Common Errors

**"QUALCODER_PROJECT_PATH not set"**
- Make sure the `env` section in config has the path
- Check for typos in the environment variable name

**"Database file not found"**
- Verify the `.qda` project folder path is correct
- Make sure you're using the full absolute path to the folder
- Ensure the folder contains a `data.qda` file inside

**"Module not found"**
- Make sure you ran `pip install -e .` in the virtual environment
- Check that the Python path in config points to the venv Python

## Next Steps

Once it's working:

1. Read the [full README](README.md) for all features
2. Try the example prompts in the Usage section
3. Explore the available tools and resources
4. Check out the prompt templates for analysis tasks

## Getting Help

- Check the [README troubleshooting section](README.md#troubleshooting)
- Review [MCP documentation](https://modelcontextprotocol.io/)
- Check [Qualcoder documentation](https://github.com/ccbogel/QualCoder/wiki)

Happy analyzing! 🎉
