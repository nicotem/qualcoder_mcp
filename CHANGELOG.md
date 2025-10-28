# Changelog

All notable changes to the Qualcoder MCP Server will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2025-10-28

### Added - AI-Assisted Coding 🤖

This release adds comprehensive AI-assisted coding capabilities, allowing Claude to help code your qualitative data automatically.

#### **10 New MCP Tools**

**Core AI Coding Tools:**
1. **`suggest_coding_for_files(file_ids, code_names, instruction, min_confidence)`**
   - Main AI coding tool that analyzes files and suggests coded segments
   - Uses Claude's native analysis (no API key required)
   - Creates coding session with all suggestions stored separately from database
   - Returns session ID for review and export

2. **`export_coding_suggestions(session_id, output_format, output_path, include_rejected)`**
   - Export suggestions in multiple formats: REFI-QDA, JSON, CSV
   - REFI-QDA format ready for Qualcoder import
   - Automatic validation before export
   - Includes step-by-step import instructions

3. **`update_suggestion_status(session_id, updates)`**
   - Approve or reject specific suggestions before export
   - Batch update support
   - Saves updated session automatically

4. **`get_coding_session_info(session_id)`**
   - View all details of a coding session
   - Shows suggestions, statistics, and metadata

**Session Management Tools:**
5. **`list_coding_sessions(project_path, days_old)`**
   - List all saved coding sessions
   - Filter by project and age

6. **`delete_coding_session(session_id)`**
   - Delete a saved session

7. **`cleanup_old_sessions(days_old)`**
   - Automatically clean up old sessions

**Code Discovery Tools:**
8. **`suggest_new_codes(file_ids, instruction, existing_codes_context)`**
   - AI analyzes files and suggests new codes to add
   - Shows existing codes to avoid duplicates
   - Returns code suggestions with descriptions and examples

9. **`export_new_codes_for_import(codes_json, output_path)`**
   - Export approved codes as REFI-QDA codebook
   - Ready for import into Qualcoder

**Help System:**
10. **`explain_ai_coding_tools(tool_name)`**
    - Comprehensive help for all AI coding tools
    - Examples, tips, and workflow guidance
    - Tool-specific documentation

#### **New Infrastructure**

**Session Management (`sessions.py`):**
- `CodingSuggestion` class - Individual AI coding suggestions
- `AICodingSession` class - Manages batches of suggestions
- `SessionManager` class - Disk persistence to `~/.qualcoder_mcp/sessions/`
- Save, load, list, delete, cleanup operations
- Statistics tracking (approved/rejected/pending)
- JSON-based session storage

**REFI-QDA Export (`refi_export.py`):**
- `RefiQdaExporter` class - Generates compliant REFI-QDA XML
- Creates proper Users, CodeBook, Sources sections
- PlainTextSelection elements for coded segments
- XML prettification and .qdpx ZIP packaging
- Validation before export
- Compatible with Qualcoder's import

**GUID Management (`database.py` additions):**
- Deterministic UUID v5 generation for REFI-QDA compatibility
- `generate_deterministic_guid()` - Consistent GUIDs across exports
- `get_code_guids()`, `get_file_guids()`, `get_case_guids()`
- `get_or_create_user_guid()`
- Uses project path hash as namespace

**Test Infrastructure:**
- `scripts/create_test_project.py` - Generates test .qda database
- Sample interview transcripts about workplace stress
- 10 codes in 3 categories
- 3 cases with demographic attributes

#### **Key Features**

- **Native Claude Analysis**: No API key required, uses Claude's conversational abilities
- **Confidence Scoring**: Claude provides 0.0-1.0 confidence for each suggestion
- **Session Persistence**: Resume work anytime, sessions saved to disk
- **Read-Only Safety**: Original database never modified, suggestions stored separately
- **REFI-QDA Standard**: Industry-standard format for QDA software
- **Comprehensive Validation**: Pre-export checks for codes, files, positions
- **Flexible Export**: REFI-QDA, JSON, or CSV formats
- **Review Workflow**: Approve/reject suggestions before import
- **Full Documentation**: Built-in help system with examples

#### **Typical Workflow**

```
1. User: "Code files 1-3 with workplace stress codes"
2. Claude analyzes files, creates CodingSuggestions
3. Claude saves session with all suggestions
4. User reviews session statistics
5. User: "Export as REFI-QDA"
6. Claude generates .qdpx file with import instructions
7. User imports into Qualcoder via File > Import > REFI-QDA Project
8. Coded segments appear in Qualcoder!
```

### Technical Details

**Files Added:**
- `src/qualcoder_mcp/sessions.py` (360 lines)
- `src/qualcoder_mcp/refi_export.py` (389 lines)
- `scripts/create_test_project.py` (220 lines)

**Files Modified:**
- `src/qualcoder_mcp/server.py` (+800 lines - 10 new tools)
- `src/qualcoder_mcp/database.py` (+94 lines - GUID methods)

**Total New Code**: ~1,900 lines

**Configuration:**
- Min confidence threshold: 0.6 (configurable per session)
- Overlapping segments: Allowed
- No batch size limits
- Session storage: `~/.qualcoder_mcp/sessions/`
- Automatic cleanup after 30 days (configurable)

### Breaking Changes

None - fully backward compatible with v0.2.0

### Known Limitations

- REFI-QDA import marked as experimental in Qualcoder (as of Dec 2020)
- Large files (>10,000 words) may need chunking (to be added in future release)
- HTML review interface deferred to v0.4.0
- AI coding requires user to guide the analysis process

### Migration Guide

No migration needed - new features are additive.

To use AI coding:
1. Restart Claude Desktop to load new tools
2. Try: "Explain AI coding tools" for overview
3. Try: "Code my interview transcripts"

### Future Enhancements (v0.4.0)

- HTML review interface for visual approval/rejection
- Automatic chunking for large files
- Batch coding optimization
- Code refinement suggestions
- Multi-coder collaboration support

---

## [0.2.0] - 2025-10-28

### Added - Core Three Features + Rich Analysis

This release adds the four most-requested features for advanced qualitative data analysis:

#### 1. Rich Transcript Analysis
- **New Tool**: `analyze_file_with_coding(file_id)`
- Retrieve complete file text WITH all coding information overlaid
- Enables deep contextual analysis beyond just coded segments
- Perfect for questions like "What does Paul say about X?" that require full transcript context
- Returns: full text, coded segments, code usage, annotations, and statistics

#### 2. Attributes & Demographics System
- **New Tool**: `list_attribute_types()` - List all available attributes
- **New Tool**: `get_file_attributes(file_id)` - Get attributes for a file
- **New Tool**: `get_case_attributes(case_id)` - Get attributes for a case
- **New Tool**: `query_by_attribute(attr_name, attr_value, attr_type)` - Query by demographics
- Support for both case and file attributes
- Enables queries like "Show me participants over age 50" or "Find focus group interviews"

#### 3. Co-occurrence Analysis
- **New Tool**: `find_cooccurring_codes(code_id, window_size)`
- Discover which codes appear together in the same segments
- Support for exact overlap or proximity-based co-occurrence (window size)
- Essential for pattern discovery and relationship analysis
- Returns frequency counts and percentages

#### 4. Case-Code Matrix & Comparative Analysis
- **New Tool**: `get_case_code_matrix()` - Full cross-tabulation matrix
- **New Tool**: `get_codes_by_case(case_id)` - Codes used in a specific case
- **New Tool**: `get_cases_by_code(code_id)` - Cases containing a specific code
- Enables comparative analysis across participants
- Perfect for questions like "Which participants mentioned theme X?"

### Enhanced

#### Database Layer (database.py)
- Added 450+ lines of new methods with comprehensive validation
- All new methods include error handling and input validation
- Full documentation with examples

#### Server Layer (server.py)
- Added 8 new MCP tools with detailed docstrings
- Each tool includes usage examples and clear parameter descriptions
- Maintains read-only safety guarantees

#### Documentation
- Updated README.md with new feature examples
- Added usage examples for all new features
- Updated contributing section to reflect completed work

### Technical Details

**New Database Methods** (database.py):
- `list_attribute_types()` - Query attribute type definitions
- `get_file_attributes(file_id)` - File attribute values
- `get_case_attributes(case_id)` - Case attribute values
- `query_by_attribute(attr_name, attr_value, attr_type)` - Attribute-based search
- `find_code_cooccurrences(code_id, window_size)` - Co-occurrence detection
- `get_case_code_matrix()` - Matrix generation
- `get_codes_by_case(case_id)` - Per-case code usage
- `get_cases_by_code(code_id)` - Per-code case coverage
- `get_file_with_coding(file_id)` - Rich file analysis

**Lines of Code**: ~500 lines added across database.py and server.py

### Use Cases Enabled

This release enables several critical research workflows:

**Demographic Analysis:**
```
"Show me coding patterns for participants over 50"
"Compare themes by gender"
"Which urban participants discussed remote work?"
```

**Pattern Discovery:**
```
"What themes appear together with workplace stress?"
"Find codes that co-occur with job satisfaction"
"Show me the co-occurrence network"
```

**Comparative Analysis:**
```
"Which participants mentioned work-life balance?"
"Create a table of themes by case"
"Find cases discussing both theme X and theme Y"
```

**Rich Contextual Analysis:**
```
"What does Paul say about Wisdom of the Crowds? Consider both coded segments and the full transcript."
"Analyze how this participant discusses motivation throughout the entire interview"
```

## [0.1.0] - 2025-10-27

### Added - Initial Release

#### Core Features
- **9 Resources**: Read-only data access to projects, codes, files, cases, journal
- **6 Core Tools**: Search, frequency analysis, code reports, project summaries
- **4 Prompts**: Analysis templates for themes, comparisons, and case exploration
- **3 Project Management Tools**: List, select, and switch between projects

#### Security
- Comprehensive security review and hardening
- Path validation for .qda files
- Input validation and sanitization
- LIKE wildcard escaping
- Error message sanitization
- Context manager for database cleanup
- Read-only database access enforcement

#### Project Management
- Dynamic project discovery
- Project switching without restart
- Two configuration modes (dynamic vs fixed)

#### Documentation
- Complete README with setup instructions
- Project selection guide
- Security review documentation
- Feature analysis and roadmap

### Initial Database Schema Support
- Qualcoder database versions v6-v13
- Schema validation on connection
- Version compatibility checking

### Architecture
- MCP server using FastMCP framework
- SQLite read-only connection
- stdio transport for Claude Desktop
- Modular design: database.py + server.py

---

## Release Philosophy

### Version Numbers
- **0.x.y**: Pre-1.0 releases during active development
- **x.0.0**: Major feature additions or breaking changes
- **0.x.0**: New features, no breaking changes
- **0.0.x**: Bug fixes and minor improvements

### Feature Prioritization
Based on qualitative research needs:
1. ⭐⭐⭐⭐⭐ Essential features (attributes, co-occurrence, case-code matrix, rich analysis)
2. ⭐⭐⭐⭐ Important features (coder comparison, code relationships)
3. ⭐⭐⭐ Useful features (media segments, statistics)
4. ⭐⭐ Nice-to-have features (saved queries, batch operations)

### Future Roadmap

**Phase 2 - Advanced Analysis** (v0.3.0):
- Coder comparison and inter-rater reliability
- Code relationships and network data
- Enhanced statistics

**Phase 3 - Specialized Features** (v0.4.0):
- Media segment access (images, audio, video)
- Timeline analysis
- Saved queries execution
- Text mining integration

---

[0.2.0]: https://github.com/YOUR_USERNAME/qualcoder_mcp/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/YOUR_USERNAME/qualcoder_mcp/releases/tag/v0.1.0
