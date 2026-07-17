# Phase 2 Implementation Plan: MCP Tools & User Interface

> **HISTORICAL DOCUMENT.** This is an early implementation plan; the work
> it describes has long since shipped. Kept for design-history reference.
> See the README and CHANGELOG for what the server actually does today.

**Status**: Ready for Implementation - Awaiting Approval
**Phase 1**: ✅ COMPLETE (Foundation: Test project, Sessions, GUIDs, REFI-QDA export)
**Phase 2**: 📋 PLANNED (MCP Tools, HTML Interface, Testing)
**Phase 3**: 📋 PLANNED (Documentation & Polish)

---

## Phase 1 Completion Summary ✅

### What Has Been Built

**1. Test Qualcoder Project** (`scripts/create_test_project.py`)
- Complete .qda database with proper schema (v8)
- 3 interview transcripts (workplace stress theme)
- 10 codes organized in 3 categories
- 3 cases with demographic attributes
- Sample coded segments for testing
- Located at: `~/Documents/qualcoder_mcp_test/test_project.qda`

**2. Session Management System** (`src/qualcoder_mcp/sessions.py`)
- `CodingSuggestion` class - Individual AI coding suggestions
- `AICodingSession` class - Manages batches of suggestions
- `SessionManager` class - Disk persistence to `~/.qualcoder_mcp/sessions/`
- Features:
  - Save/load sessions as JSON
  - List sessions with filtering
  - Delete sessions
  - Automatic cleanup of old sessions
  - Statistics tracking (approved/rejected/pending counts)

**3. GUID Management** (`src/qualcoder_mcp/database.py`)
- Deterministic UUID v5 generation
- Methods: `generate_deterministic_guid()`, `get_code_guids()`, `get_file_guids()`, `get_case_guids()`, `get_or_create_user_guid()`
- Ensures consistent GUIDs across multiple exports
- Uses project path hash as namespace

**4. REFI-QDA Export** (`src/qualcoder_mcp/refi_export.py`)
- `RefiQdaExporter` class - Generates compliant REFI-QDA XML
- Creates proper Users, CodeBook, Sources sections
- PlainTextSelection elements for each coded segment
- XML prettification and .qdpx ZIP packaging
- Validation before export
- Compatible with Qualcoder import

**Lines of Code Added**: ~1,200 lines across 4 new files + database.py additions

---

## Phase 2: MCP Tools Implementation

### Overview

Add 10 new MCP tools to `server.py` that expose the AI coding functionality to Claude Desktop users. These tools will integrate the foundation components built in Phase 1.

### Architecture

```
User ←→ Claude Desktop ←→ MCP Server (server.py) ←→ Components
                                                    ├─ database.py
                                                    ├─ sessions.py
                                                    └─ refi_export.py
```

---

## Tool Specifications

### Tool 1: `suggest_coding_for_files` ⭐ Core Tool

**Purpose**: Main AI coding tool - analyzes files and generates coded segment suggestions

**Implementation**:
```python
@mcp.tool()
def suggest_coding_for_files(
    file_ids: List[int],
    code_names: Optional[List[str]] = None,
    instruction: str = "Code all relevant segments",
    min_confidence: float = 0.6
) -> str:
    """AI analyzes files and suggests coded segments."""
```

**Logic Flow**:
1. Validate file IDs exist in project
2. Get file content for each file using `analyze_file_with_coding()`
3. If code_names specified, filter to those codes only
4. For each file:
   - Use Claude to analyze text
   - Identify relevant segments for each code
   - Generate confidence scores
   - Create CodingSuggestion objects
5. Create new AICodingSession
6. Add all suggestions to session
7. Save session to disk
8. Return session ID and summary statistics

**Estimated Lines**: 80-100 lines

**Key Considerations**:
- How to invoke Claude's analysis within the tool?
- Need to pass instruction context to Claude
- Confidence scoring algorithm
- Handle large files (chunking?)

---

### Tool 2: `export_coding_suggestions` ⭐ Core Tool

**Purpose**: Export suggestions in various formats for review and import

**Implementation**:
```python
@mcp.tool()
def export_coding_suggestions(
    session_id: str,
    output_format: str = "refi-qda",
    output_path: Optional[str] = None,
    include_rejected: bool = False
) -> str:
    """Export AI coding suggestions for review and import."""
```

**Logic Flow**:
1. Load session from disk using SessionManager
2. Filter suggestions by status (exclude rejected if include_rejected=False)
3. Generate default output path if not specified:
   - refi-qda: `~/Documents/coding_suggestions_YYYY-MM-DD.qdpx`
   - html-report: `~/Documents/coding_review_YYYY-MM-DD.html`
   - json: `~/Documents/coding_suggestions_YYYY-MM-DD.json`
   - csv: `~/Documents/coding_suggestions_YYYY-MM-DD.csv`
4. Call appropriate export function:
   - `refi-qda`: Use RefiQdaExporter.export_to_qdpx()
   - `html-report`: Use HtmlReportGenerator.generate_report()
   - `json`: Session.to_dict()
   - `csv`: Generate CSV with pandas or csv module
5. Return output file path and statistics

**Estimated Lines**: 60-80 lines

---

### Tool 3: `update_suggestion_status`

**Purpose**: Mark suggestions as approved or rejected

**Implementation**:
```python
@mcp.tool()
def update_suggestion_status(
    session_id: str,
    updates: str  # JSON array of {index, status}
) -> str:
    """Update the status of coding suggestions."""
```

**Logic Flow**:
1. Load session from disk
2. Parse updates JSON
3. For each update, call session.update_suggestion_status()
4. Save updated session to disk
5. Return updated statistics

**Estimated Lines**: 30-40 lines

---

### Tool 4: `get_coding_session_info`

**Purpose**: Get detailed information about a session

**Implementation**:
```python
@mcp.tool()
def get_coding_session_info(session_id: str) -> str:
    """Get detailed information about a coding session."""
```

**Logic Flow**:
1. Load session from disk
2. Return session.to_dict() with all details

**Estimated Lines**: 20-30 lines

---

### Tool 5: `list_coding_sessions`

**Purpose**: List all saved sessions

**Implementation**:
```python
@mcp.tool()
def list_coding_sessions(
    project_path: Optional[str] = None,
    days_old: int = 30
) -> str:
    """List all saved AI coding sessions."""
```

**Logic Flow**:
1. Create SessionManager instance
2. Call session_manager.list_sessions()
3. Return formatted list

**Estimated Lines**: 20-30 lines

---

### Tool 6: `delete_coding_session`

**Purpose**: Delete a saved session

**Implementation**:
```python
@mcp.tool()
def delete_coding_session(session_id: str) -> str:
    """Delete a saved coding session."""
```

**Logic Flow**:
1. Create SessionManager instance
2. Call session_manager.delete_session()
3. Return success/failure

**Estimated Lines**: 20-30 lines

---

### Tool 7: `cleanup_old_sessions`

**Purpose**: Clean up old sessions

**Implementation**:
```python
@mcp.tool()
def cleanup_old_sessions(days_old: int = 30) -> str:
    """Clean up old coding sessions."""
```

**Logic Flow**:
1. Create SessionManager instance
2. Call session_manager.cleanup_old_sessions()
3. Return count deleted

**Estimated Lines**: 20-30 lines

---

### Tool 8: `suggest_new_codes`

**Purpose**: AI analyzes files and suggests new codes to add

**Implementation**:
```python
@mcp.tool()
def suggest_new_codes(
    file_ids: List[int],
    instruction: str = "Analyze and suggest relevant codes",
    existing_codes_context: bool = True
) -> str:
    """AI analyzes files and suggests new codes."""
```

**Logic Flow**:
1. Get file content for each file
2. If existing_codes_context, include current codebook
3. Use Claude to analyze and suggest new codes
4. For each suggested code:
   - Generate name, description, category
   - Find example segments
   - Estimate frequency
   - Calculate confidence
5. Return structured suggestions

**Estimated Lines**: 60-80 lines

**Key Consideration**: How to invoke Claude's analysis?

---

### Tool 9: `export_new_codes_for_import`

**Purpose**: Export suggested codes as REFI-QDA codebook

**Implementation**:
```python
@mcp.tool()
def export_new_codes_for_import(
    codes_json: str,
    output_path: str = "~/Documents/new_codes.qdpx"
) -> str:
    """Export approved new codes as REFI-QDA Codebook."""
```

**Logic Flow**:
1. Parse codes_json
2. Create minimal REFI-QDA XML with just CodeBook section
3. Package as .qdpx
4. Return path and import instructions

**Estimated Lines**: 40-50 lines

**Note**: Simplified version of RefiQdaExporter focusing only on codes

---

### Tool 10: `explain_ai_coding_tools` ⭐ Help Tool

**Purpose**: Comprehensive help system for all AI coding tools

**Implementation**:
```python
@mcp.tool()
def explain_ai_coding_tools(tool_name: Optional[str] = None) -> str:
    """Get help and examples for AI coding tools."""
```

**Logic Flow**:
1. If tool_name is None, return overview of all tools
2. If tool_name specified, return detailed help for that tool
3. Include:
   - Tool description
   - Parameter explanations
   - Usage examples
   - Workflow context
   - Tips and best practices
   - Related tools

**Estimated Lines**: 150-200 lines (mostly documentation content)

**Data Structure**:
```python
TOOL_HELP = {
    "suggest_coding_for_files": {
        "description": "...",
        "parameters": {...},
        "examples": [...],
        "tips": [...],
        "related_tools": [...]
    },
    # ... for each tool
}
```

---

## Total Estimated Lines for Phase 2: ~500-650 lines

---

## Phase 3: HTML Review Interface

### Overview

Create an interactive HTML report generator that allows users to review AI coding suggestions in a web browser before importing.

### Component: `html_report.py`

**Purpose**: Generate beautiful, interactive HTML reports for reviewing suggestions

**Features**:
1. **Summary Dashboard**
   - Total suggestions count
   - Breakdown by file
   - Breakdown by code
   - Confidence distribution
   - Status counts (pending/approved/rejected)

2. **Interactive Filtering**
   - Filter by file
   - Filter by code
   - Filter by confidence threshold
   - Filter by status

3. **Suggestion Cards**
   - File name and position
   - Coded text (highlighted)
   - Code name with color badge
   - AI memo
   - Confidence score (visual bar)
   - Approve/Reject buttons
   - Edit memo field

4. **JavaScript Functionality**
   - Update statistics on filter changes
   - Toggle approve/reject status
   - Edit memos inline
   - Export decisions to JSON
   - Search/filter functionality

5. **Export Functionality**
   - Save approval decisions
   - Generate updated session JSON
   - Download functionality

### HTML Template Structure

```html
<!DOCTYPE html>
<html>
<head>
    <title>AI Coding Suggestions Review</title>
    <style>
        /* Modern, clean styling */
        /* Color-coded confidence levels */
        /* Responsive layout */
    </style>
</head>
<body>
    <header>
        <h1>AI Coding Suggestions Review</h1>
        <div id="summary"><!-- Stats --></div>
    </header>

    <div id="filters">
        <!-- Filter controls -->
    </div>

    <div id="suggestions">
        <!-- Suggestion cards -->
    </div>

    <footer>
        <button id="export">Export Decisions</button>
    </footer>

    <script>
        /* Interactivity logic */
    </script>
</body>
</html>
```

### Implementation Class

```python
class HtmlReportGenerator:
    """Generate interactive HTML reports for reviewing AI coding suggestions."""

    def __init__(self, db: QualcoderDatabase):
        self.db = db

    def generate_report(
        self,
        session: AICodingSession,
        output_path: str
    ) -> str:
        """Generate HTML report."""
        # Load template
        # Inject data
        # Generate HTML
        # Save to file
        # Return path
```

**Estimated Lines**: ~300-400 lines (including HTML template as string)

---

## Testing Plan

### Unit Tests

Create `tests/test_ai_coding.py`:

1. **Test Session Management**
   - Create session
   - Save session
   - Load session
   - Update suggestion status
   - Session statistics

2. **Test REFI-QDA Export**
   - Generate XML structure
   - Validate XML against schema
   - Create .qdpx file
   - Validate suggestions

3. **Test GUID Generation**
   - Consistency across multiple calls
   - Uniqueness for different entities

### Integration Tests

1. **End-to-End Workflow**
   - Create session with suggestions
   - Export to REFI-QDA
   - Validate .qdpx structure
   - (Manual) Import into Qualcoder
   - Verify segments appear correctly

2. **HTML Report**
   - Generate report
   - Validate HTML structure
   - Test JavaScript functionality (manual)

### Manual Testing Checklist

- [ ] Create test suggestions
- [ ] Export to HTML, review in browser
- [ ] Export to REFI-QDA
- [ ] Import into Qualcoder
- [ ] Verify coded segments appear
- [ ] Test with various file sizes
- [ ] Test with overlapping segments
- [ ] Test error handling

**Estimated Lines**: ~200-300 lines of test code

---

## Documentation Plan

### 1. AI_CODING_GUIDE.md

**User-facing guide for AI coding features**

**Contents**:
- Introduction to AI-assisted coding
- Prerequisites and setup
- Quick start guide
- Complete workflow walkthroughs
- Tool reference
- Tips and best practices
- Troubleshooting
- FAQ

**Estimated Length**: 1000-1500 lines

### 2. Update README.md

**Add AI coding section**:
- Feature overview
- Quick example
- Link to full guide

**Estimated Additions**: ~100 lines

### 3. Update CHANGELOG.md

**v0.3.0 Entry**:
- List all new features
- Breaking changes (if any)
- Migration guide
- Known limitations

**Estimated Additions**: ~50 lines

### 4. IMPORT_INSTRUCTIONS.md

**Step-by-step import guide**:
- How to import REFI-QDA into Qualcoder
- Screenshots (if possible)
- Troubleshooting import issues
- What to expect

**Estimated Length**: 200-300 lines

---

## Implementation Order

### Session 1: Core AI Coding Tools (2-3 hours)
1. ✅ Foundation (COMPLETE)
2. Add `suggest_coding_for_files` tool
3. Add `export_coding_suggestions` tool
4. Add `update_suggestion_status` tool
5. Add `get_coding_session_info` tool
6. Test basic workflow

### Session 2: Session Management Tools (1 hour)
1. Add `list_coding_sessions` tool
2. Add `delete_coding_session` tool
3. Add `cleanup_old_sessions` tool
4. Test session operations

### Session 3: Code Discovery Tools (1-2 hours)
1. Add `suggest_new_codes` tool
2. Add `export_new_codes_for_import` tool
3. Test code suggestion workflow

### Session 4: Help System (1 hour)
1. Add `explain_ai_coding_tools` tool
2. Write comprehensive help content
3. Test help responses

### Session 5: HTML Interface (2-3 hours)
1. Create `html_report.py`
2. Design HTML template
3. Implement JavaScript interactivity
4. Test in browser

### Session 6: Testing & QA (2-3 hours)
1. Write unit tests
2. Write integration tests
3. Manual end-to-end testing
4. Bug fixes

### Session 7: Documentation (2-3 hours)
1. Write AI_CODING_GUIDE.md
2. Update README.md
3. Update CHANGELOG.md
4. Write IMPORT_INSTRUCTIONS.md

**Total Estimated Time**: 12-18 hours of focused work

---

## Critical Design Questions to Resolve

### Question 1: AI Analysis Invocation

**Challenge**: How do the MCP tools invoke Claude's analysis capabilities?

**Options**:

**Option A**: Use Claude's native capabilities within the conversation
- Tools return prompts/context
- Claude analyzes and structures results
- Tools just format the output

**Option B**: Use Claude API directly (requires API key)
- Tools make API calls to Claude
- More automated but requires configuration
- Additional dependency

**Option C**: Hybrid approach
- Tools prepare context
- Return structured prompts
- Claude responds with analysis
- Tools capture and store results

**Recommendation**: Option A (native) or Option C (hybrid) - avoids API key requirement

---

### Question 2: Confidence Scoring

**Challenge**: How to generate confidence scores for AI suggestions?

**Options**:

**Option A**: Claude provides confidence
- Ask Claude to rate each suggestion 0.0-1.0
- Based on clarity, context, relevance

**Option B**: Heuristic-based
- Keyword density
- Segment length
- Code occurrence frequency

**Option C**: Default confidence
- All suggestions get same confidence (0.6)
- User reviews all equally

**Recommendation**: Option A - let Claude assess confidence

---

### Question 3: Large File Handling

**Challenge**: How to handle large transcript files (>10,000 words)?

**Options**:

**Option A**: Chunk files
- Split into manageable sections
- Process each chunk separately
- Merge results

**Option B**: Full file analysis
- Send entire file to Claude
- Rely on Claude's context window

**Option C**: Hybrid
- Analyze full file for overview
- Chunk for detailed coding

**Recommendation**: Start with Option B, add Option A if needed

---

## Version Bump Plan

**Current**: v0.2.0
**Next**: v0.3.0

**Changes**:
- Major feature addition (AI coding)
- New MCP tools (10 tools)
- New dependencies: None
- Breaking changes: None
- Database changes: None (read-only)

---

## File Structure After Phase 2 & 3

```
qualcoder_mcp/
├── src/
│   └── qualcoder_mcp/
│       ├── __init__.py
│       ├── server.py              # +500 lines (10 new tools)
│       ├── database.py            # +100 lines (GUID methods) ✅
│       ├── sessions.py            # NEW ✅ (360 lines)
│       ├── refi_export.py         # NEW ✅ (389 lines)
│       └── html_report.py         # NEW (300 lines)
├── scripts/
│   └── create_test_project.py    # NEW ✅ (220 lines)
├── templates/
│   └── coding_review.html         # NEW (included in html_report.py)
├── tests/
│   ├── test_sessions.py           # NEW (100 lines)
│   ├── test_refi_export.py        # NEW (100 lines)
│   └── test_ai_coding.py          # NEW (100 lines)
├── docs/
│   ├── AI_CODING_GUIDE.md         # NEW (1500 lines)
│   └── IMPORT_INSTRUCTIONS.md     # NEW (300 lines)
├── README.md                      # +100 lines
├── CHANGELOG.md                   # +50 lines
└── pyproject.toml                 # Version: 0.2.0 → 0.3.0
```

**Total New Lines Estimated**: ~3,500-4,000 lines

---

## Next Steps - When Ready to Proceed

**Option 1**: Implement all at once (full Phase 2 & 3)
- Estimated: 12-18 hours
- Delivers complete feature

**Option 2**: Implement in stages
- Session 1-2: Core tools (4-5 hours)
- Review and test
- Session 3-7: Complete remaining (8-13 hours)

**Option 3**: MVP first
- Just tools 1-4 (core coding workflow)
- Test with real data
- Expand if successful

---

## Questions for Review

1. **AI Analysis**: Which approach for invoking Claude's analysis? (A, B, or C)

2. **Confidence Scoring**: Which method? (Claude-based, heuristic, or default)

3. **Implementation Pace**: All at once, staged, or MVP first?

4. **HTML Interface Priority**: Critical for v0.3.0 or can defer to v0.4.0?

5. **Testing Depth**: Full test suite or focus on integration tests?

---

## Approval Checklist

Before proceeding with Phase 2, please confirm:

- [ ] Phase 1 foundation meets expectations
- [ ] REFI-QDA export approach is correct
- [ ] Session persistence design is acceptable
- [ ] 10 MCP tools cover the needed functionality
- [ ] HTML interface specifications are clear
- [ ] Implementation order makes sense
- [ ] Estimated timeline is acceptable
- [ ] Design questions have been answered

---

**Ready for your review and feedback!**

Once you approve, I'll proceed with implementation following the plan above.
