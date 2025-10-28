# Qualcoder MCP Research Summary

## What is Model Context Protocol (MCP)?

The Model Context Protocol (MCP) is an open standard introduced by Anthropic in November 2024 that enables secure, two-way connections between AI applications and data sources.

### Key Concepts

**Architecture Components:**
- **Hosts**: AI applications that initiate connections (e.g., Claude Desktop)
- **Clients**: Systems that maintain 1:1 connections with servers within the host
- **Servers**: Systems that provide context, tools, and prompts to clients

**Server Capabilities:**
1. **Resources**: Read-only data exposure (like GET endpoints)
2. **Tools**: Actionable functions that perform operations (like POST endpoints)
3. **Prompts**: Reusable interaction templates for LLMs

### MCP Development

**Python SDK Requirements:**
- Python 3.10 or higher
- `mcp` package with CLI: `pip install "mcp[cli]"`
- FastMCP framework for simplified development

**Basic Server Structure:**
```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(name="ServerName")

@mcp.resource("resource://path/{param}")
def get_resource(param: str) -> str:
    return f"Resource data for {param}"

@mcp.tool()
def perform_action(param: str) -> str:
    # Perform work
    return "Result"

@mcp.prompt()
def template_prompt(param: str) -> str:
    return f"Prompt template for {param}"
```

**Transport:**
- Stdio (standard input/output) - most common for local servers
- SSE (Server-Sent Events) - for HTTP-based connections
- Streamable HTTP - for browser-based clients

**Testing & Deployment:**
- Test with MCP Inspector: `uv run mcp dev server.py`
- Install in Claude Desktop: `uv run mcp install server.py`

### Claude Desktop Configuration (Mac)

**Config File Location:**
`~/Library/Application Support/Claude/claude_desktop_config.json`

**Configuration Format:**
```json
{
  "mcpServers": {
    "server-name": {
      "command": "python",
      "args": ["-m", "package.module"],
      "env": {
        "ENV_VAR": "value"
      }
    }
  }
}
```

**Access via UI:**
Claude Desktop > Settings > Developer > Edit Config

## What is Qualcoder?

Qualcoder is a free, open-source qualitative data analysis (QDA) application written in Python for analyzing text, images, audio, and video data.

### Key Features

- **Multi-format Support**: txt, odt, docx, html, md, epub, PDF
- **Media Coding**: Images, audio, video (requires VLC)
- **Hierarchical Coding**: Tree-like categorization of codes
- **Case Management**: Organize data by cases/participants
- **Annotations & Memos**: Rich note-taking capabilities
- **Reports**: Coding frequencies, coder comparisons, visualizations
- **AI Integration**: GPT-4 and open-source models for assisted coding
- **Cross-platform**: Windows, macOS, Linux
- **Offline-first**: No internet required

### Technical Stack

- **Language**: Python 3.10+
- **GUI**: PyQt6
- **Database**: SQLite
- **Repository**: https://github.com/ccbogel/QualCoder
- **License**: LGPL-3.0

### Database Schema

Qualcoder uses a SQLite database with the following key tables:

**Core Data Tables:**
- `project`: Project metadata (version, date, memo, coder name)
- `source`: Text and media files (id, name, fulltext, mediapath, memo, owner, date)
- `code_name`: Codes/categories (cid, name, memo, catid, owner, date, color)
- `code_cat`: Code categories (catid, name, owner, date, memo, supercatid)

**Coding Assignment Tables:**
- `code_text`: Text segments coded (ctid, cid, fid, seltext, pos0, pos1, owner, date, memo, important)
- `code_image`: Image regions coded (imid, id, x1, y1, width, height, cid, memo, date, pdf_page)
- `code_av`: Audio/video segments coded (avid, id, pos0, pos1, cid, memo, date, important)

**Case Management:**
- `cases`: Cases/participants (caseid, name, memo, owner, date)
- `case_text`: Text segments assigned to cases (id, caseid, fid, pos0, pos1, memo)

**Annotations & Notes:**
- `annotation`: Annotations on text (anid, fid, pos0, pos1, memo, owner, date)
- `journal`: Journal entries (jid, name, jentry, date, owner)

**Attributes:**
- `attribute_type`: Attribute definitions (name, date, owner, memo, caseOrFile, valuetype)
- `attribute`: Attribute values (attrid, name, attr_type, value, id, date, owner)

**Other Tables:**
- `ris`: Reference data (risid, tag, longtag, value)
- `stored_sql`: Saved SQL queries
- `manage_files_display`: File display settings
- `files_filter`: File filter settings
- Graph visualization tables (gr_*)

### Project File Structure

Qualcoder projects are stored as:
- Database: `.qda` SQLite file
- Media files: Stored in project directory alongside database
- Location: User-specified, typically in Documents or designated project folder

### Installation on Mac

**DMG Bundles:**
- `QualCoder_X_arm64.dmg` - Apple Silicon (M1-M4)
- `QualCoder_X_x86_64.dmg` - Intel processors

**From Source:**
```bash
cd Downloads/QualCoder-master
python3 -m venv env
source env/bin/activate
pip3 install -r requirements.txt
cd src
python3 -m qualcoder
```

## MCP Integration Strategy

### Use Cases

The Qualcoder MCP will enable Claude to:
1. **Query coded data**: Search and retrieve coded segments
2. **Analyze coding patterns**: Identify themes and relationships
3. **Access project information**: List codes, categories, files, cases
4. **Read source documents**: Access the original text/data
5. **Generate reports**: Create coding summaries and comparisons
6. **Search annotations**: Find notes and memos

### MCP Server Design

**Resources** (read-only data access):
- `qualcoder://project/info` - Project metadata
- `qualcoder://codes/list` - List all codes with hierarchy
- `qualcoder://codes/{cid}` - Get specific code details and coded segments
- `qualcoder://files/list` - List all source files
- `qualcoder://files/{fid}` - Get file content
- `qualcoder://cases/list` - List all cases
- `qualcoder://cases/{caseid}` - Get case details
- `qualcoder://journal` - Get journal entries

**Tools** (operations):
- `search_coded_text(query, code_name)` - Search coded segments
- `get_coding_frequencies()` - Get coding frequency statistics
- `compare_codes(code1, code2)` - Compare two codes
- `get_cooccurrences(code1, code2)` - Find code co-occurrences
- `search_memos(query)` - Search memos and annotations
- `export_coded_segments(code_name)` - Export all segments for a code

**Prompts** (interaction templates):
- `analyze_theme(theme_name)` - Template for thematic analysis
- `compare_cases(case1, case2)` - Template for case comparison
- `summarize_project()` - Template for project overview

### Implementation Considerations

1. **Database Access**: Read-only access to `.qda` file
2. **File Path Discovery**: Need to locate Qualcoder projects (likely in user's Documents or designated folder)
3. **Project Selection**: Handle multiple projects, perhaps via configuration
4. **Safety**: Read-only operations to prevent data corruption
5. **Performance**: Efficient queries on potentially large datasets
6. **Rich Text**: Handle formatted text and media references

### Technical Approach

1. Use Python MCP SDK with FastMCP
2. Connect to SQLite database using sqlite3
3. Implement read-only connection
4. Provide project path via environment variable or config
5. Use stdio transport for Claude Desktop integration
6. Handle errors gracefully (missing files, corrupt databases, etc.)

## Next Steps

1. Design detailed MCP server architecture
2. Implement basic MCP server with core resources
3. Add tools for searching and analysis
4. Create configuration for Claude Desktop
5. Write documentation and usage examples
6. Test with real Qualcoder projects
