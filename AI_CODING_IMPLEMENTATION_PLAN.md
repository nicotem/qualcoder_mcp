# AI-Assisted Coding Implementation Plan
## REFI-QDA XML Approach

**Status**: Design Phase - Awaiting Approval to Proceed

**Approach**: Option A - Generate REFI-QDA XML format for standard import into Qualcoder

---

## Overview

This implementation will add AI-assisted coding capabilities to the Qualcoder MCP server while maintaining read-only safety for the main database. The AI will analyze files, suggest coded segments, and export them in REFI-QDA XML format for review and import into Qualcoder.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ Phase 1: Code Discovery (Optional - if new codes needed)   │
├─────────────────────────────────────────────────────────────┤
│ 1. AI analyzes files → suggests new codes                  │
│ 2. User reviews & approves code suggestions                │
│ 3. Export new codes as REFI-QDA Codebook XML               │
│ 4. User imports into Qualcoder (File > Import > Codebook)  │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ Phase 2: Automated Coding                                   │
├─────────────────────────────────────────────────────────────┤
│ 1. AI codes files using existing/approved codes            │
│ 2. Suggestions stored in memory (NOT written to database)  │
│ 3. Export to REFI-QDA XML (.qdpx file)                     │
│ 4. Generate HTML review report                             │
│ 5. User reviews → edits → approves                         │
│ 6. User imports via Qualcoder GUI (File > Import)          │
└─────────────────────────────────────────────────────────────┘
```

---

## Component Design

### 1. Database Extensions (database.py)

Add methods to support AI coding workflow:

#### **1.1. GUID Retrieval Methods**

```python
def get_code_guids(self) -> Dict[int, str]:
    """Get mapping of code_id -> GUID for all codes.

    Qualcoder doesn't store GUIDs by default, so we need to:
    - Generate deterministic GUIDs based on code_id
    - Or query if GUIDs are stored (check schema first)

    Returns:
        Dict mapping code_id (cid) to GUID (UUID string)
    """

def get_file_guids(self) -> Dict[int, str]:
    """Get mapping of file_id -> GUID for all source files.

    Returns:
        Dict mapping file_id (id) to GUID (UUID string)
    """

def get_or_create_user_guid(self, username: str) -> str:
    """Get or create GUID for a user.

    Args:
        username: The coder name

    Returns:
        UUID string for this user
    """
```

**Note**: Need to check if Qualcoder stores GUIDs in the database. If not, we'll generate deterministic UUIDs using UUID v5 (namespace-based):
- Namespace: Qualcoder project path hash
- Name: code_id or file_id
- This ensures GUIDs are consistent across exports

#### **1.2. AI Coding Suggestion Storage**

```python
class CodingSuggestion:
    """Data class for AI-suggested coding."""
    def __init__(self):
        self.file_id: int
        self.file_name: str
        self.code_id: int
        self.code_name: str
        self.start_pos: int
        self.end_pos: int
        self.segment_text: str
        self.ai_memo: str
        self.confidence: float
        self.status: str  # 'pending', 'approved', 'rejected'
        self.guid: str  # Generated UUID for this coding

class AICodingSession:
    """Manages a session of AI coding suggestions."""
    def __init__(self, db: QualcoderDatabase):
        self.db = db
        self.suggestions: List[CodingSuggestion] = []
        self.session_id: str = str(uuid.uuid4())
        self.created_at: str

    def add_suggestion(self, suggestion: CodingSuggestion):
        """Add a coding suggestion to the session."""

    def get_suggestions_by_file(self, file_id: int) -> List[CodingSuggestion]:
        """Get all suggestions for a specific file."""

    def get_suggestions_by_code(self, code_id: int) -> List[CodingSuggestion]:
        """Get all suggestions for a specific code."""

    def filter_by_status(self, status: str) -> List[CodingSuggestion]:
        """Get suggestions by status (pending/approved/rejected)."""

    def to_dict(self) -> Dict:
        """Export session as dictionary for JSON export."""
```

#### **1.3. REFI-QDA Export Methods**

```python
def export_suggestions_as_refi_qda(
    self,
    suggestions: List[CodingSuggestion],
    output_path: str,
    project_name: str = "AI Coding Suggestions",
    include_full_codebook: bool = False
) -> str:
    """Export coding suggestions as REFI-QDA XML file.

    Args:
        suggestions: List of approved coding suggestions
        output_path: Where to save the .qdpx file
        project_name: Name for the REFI-QDA project
        include_full_codebook: If True, includes all codes; if False, only referenced codes

    Returns:
        Path to created .qdpx file

    Process:
        1. Create XML structure with proper namespaces
        2. Add Users section (AI coder + original owner if specified)
        3. Add CodeBook section (codes referenced in suggestions)
        4. Add Sources section (files with PlainTextSelection elements)
        5. Package into .qdpx ZIP file
    """
```

---

### 2. New MCP Tools (server.py)

Add 6 new MCP tools for AI coding workflow:

#### **Tool 1: `suggest_new_codes`**

```python
@mcp.tool()
def suggest_new_codes(
    file_ids: List[int],
    instruction: str = "Analyze and suggest relevant codes",
    existing_codes_context: bool = True
) -> str:
    """AI analyzes files and suggests new codes to add to codebook.

    The AI will read the specified files and suggest codes based on
    themes, patterns, and concepts found in the text.

    Args:
        file_ids: List of file IDs to analyze
        instruction: Specific guidance for code suggestion
        existing_codes_context: If True, shows existing codes to avoid duplicates

    Returns:
        JSON with suggested codes, each including:
        - code_name: Proposed name for the code
        - description: What this code represents
        - parent_category: Suggested category/hierarchy
        - example_segments: 2-3 text examples
        - estimated_frequency: How often this theme appears
        - confidence: AI confidence in this suggestion (0-1)

    Example usage:
        "Analyze interviews 1-5 and suggest codes related to workplace stress"
        "Review all files and suggest codes I might be missing"
    """
```

#### **Tool 2: `export_new_codes_for_import`**

```python
@mcp.tool()
def export_new_codes_for_import(
    codes_json: str,
    output_path: str = "~/Documents/new_codes.qdpx"
) -> str:
    """Export approved new codes as REFI-QDA Codebook for import.

    Takes the approved codes from suggest_new_codes and creates a
    REFI-QDA codebook file that can be imported into Qualcoder.

    Args:
        codes_json: JSON string with approved codes (from suggest_new_codes)
        output_path: Where to save the codebook file

    Returns:
        JSON with:
        - output_file: Path to generated .qdpx file
        - code_count: Number of codes exported
        - import_instructions: Step-by-step guide to import

    Import process:
        1. Open Qualcoder
        2. File > Import > REFI-QDA Codebook
        3. Select the generated .qdpx file
        4. Review and confirm import
    """
```

#### **Tool 3: `suggest_coding_for_files`** ⭐ Core Tool

```python
@mcp.tool()
def suggest_coding_for_files(
    file_ids: List[int],
    code_names: Optional[List[str]] = None,
    instruction: str = "Code all relevant segments",
    min_confidence: float = 0.5
) -> str:
    """AI analyzes files and suggests coded segments.

    This is the main AI coding tool. The AI will read each file and
    identify segments that should be coded with the specified codes.

    Args:
        file_ids: List of file IDs to code
        code_names: List of code names to apply (if None, uses all codes)
        instruction: Specific coding guidance for the AI
        min_confidence: Minimum confidence threshold (0-1)

    Returns:
        JSON with:
        - session_id: Unique ID for this coding session
        - total_suggestions: Count of suggested coded segments
        - by_file: Breakdown by file
        - by_code: Breakdown by code
        - suggestions: Array of all suggested codings

    Example usage:
        "Code files 3-7 with all codes related to 'workplace'"
        "Apply the 'stress' and 'coping' codes to interview transcripts"
        "Code all uncoded segments in file 5 that relate to motivation"

    Note: This only creates suggestions, nothing is written to database.
    Use export_coding_suggestions to save for review and import.
    """
```

#### **Tool 4: `export_coding_suggestions`** ⭐ Core Tool

```python
@mcp.tool()
def export_coding_suggestions(
    session_id: str,
    output_format: str = "refi-qda",
    output_path: Optional[str] = None,
    include_rejected: bool = False
) -> str:
    """Export AI coding suggestions for review and import.

    Exports the suggestions from suggest_coding_for_files in various
    formats. The primary format is REFI-QDA XML for importing into Qualcoder.

    Args:
        session_id: The session ID from suggest_coding_for_files
        output_format: "refi-qda", "html-report", "json", "csv"
        output_path: Where to save (default: ~/Documents/coding_suggestions_DATE.*)
        include_rejected: If True, includes rejected suggestions

    Returns:
        JSON with:
        - output_file: Path to generated file
        - format: Format used
        - segment_count: Number of coded segments included
        - next_steps: Instructions for importing

    Formats:
        - "refi-qda": Standard .qdpx file for Qualcoder import
        - "html-report": Interactive web page for reviewing suggestions
        - "json": Machine-readable format for editing
        - "csv": Spreadsheet format for documentation

    Typical workflow:
        1. export_coding_suggestions(session_id, "html-report")
        2. Open HTML file, review all suggestions
        3. Mark some as approved/rejected in the report
        4. export_coding_suggestions(session_id, "refi-qda")
        5. Import the .qdpx file into Qualcoder
    """
```

#### **Tool 5: `update_suggestion_status`**

```python
@mcp.tool()
def update_suggestion_status(
    session_id: str,
    updates: str
) -> str:
    """Update the status of coding suggestions (approve/reject).

    Allows you to approve or reject specific suggestions before
    exporting for import.

    Args:
        session_id: The session ID from suggest_coding_for_files
        updates: JSON array of updates, each with:
                 {suggestion_index: int, status: "approved"|"rejected"}

    Returns:
        JSON with updated statistics

    Example:
        updates = [
            {"suggestion_index": 0, "status": "approved"},
            {"suggestion_index": 1, "status": "rejected"},
            {"suggestion_index": 2, "status": "approved"}
        ]
    """
```

#### **Tool 6: `get_coding_session_info`**

```python
@mcp.tool()
def get_coding_session_info(session_id: str) -> str:
    """Get detailed information about a coding session.

    Args:
        session_id: The session ID to query

    Returns:
        JSON with:
        - session_id
        - created_at
        - total_suggestions
        - approved_count
        - rejected_count
        - pending_count
        - files_analyzed
        - codes_used
        - suggestions (full list)
    """
```

---

### 3. REFI-QDA XML Generator (new file: refi_export.py)

Create dedicated module for REFI-QDA XML generation:

```python
"""
refi_export.py - REFI-QDA XML export functionality
"""

import uuid
import xml.etree.ElementTree as ET
from xml.dom import minidom
import zipfile
from pathlib import Path
from typing import List, Dict
from datetime import datetime

class RefiQdaExporter:
    """Generate REFI-QDA XML files from coding suggestions."""

    NAMESPACE = "urn:QDA-XML:project:1.0"
    SCHEMA_LOCATION = "urn:QDA-XML:project:1.0 Project.xsd"

    def __init__(self, db: QualcoderDatabase):
        self.db = db

    def create_project_xml(
        self,
        suggestions: List[CodingSuggestion],
        project_name: str = "AI Coding Suggestions"
    ) -> ET.Element:
        """Create the main project XML structure."""

    def add_users_section(self, root: ET.Element) -> None:
        """Add Users section to XML."""

    def add_codebook_section(
        self,
        root: ET.Element,
        code_ids: List[int]
    ) -> None:
        """Add CodeBook section with referenced codes."""

    def add_sources_section(
        self,
        root: ET.Element,
        suggestions: List[CodingSuggestion]
    ) -> None:
        """Add Sources section with coded segments."""

    def create_plain_text_selection(
        self,
        suggestion: CodingSuggestion
    ) -> ET.Element:
        """Create PlainTextSelection element for a coded segment."""

    def export_to_qdpx(
        self,
        suggestions: List[CodingSuggestion],
        output_path: str,
        project_name: str = "AI Coding Suggestions"
    ) -> str:
        """Export suggestions as .qdpx file (ZIP with XML)."""

        Steps:
        1. Generate project.qde XML
        2. Create temporary directory
        3. Write XML file
        4. Create .qdpx ZIP archive
        5. Add project.qde to archive
        6. Clean up temporary files
        7. Return path to .qdpx file
```

---

### 4. HTML Review Report Generator (new file: html_report.py)

Create interactive HTML reports for reviewing suggestions:

```python
"""
html_report.py - Generate interactive HTML reports for reviewing AI coding suggestions
"""

from typing import List, Dict
from pathlib import Path
import json

class CodingReviewReport:
    """Generate HTML reports for reviewing AI coding suggestions."""

    def generate_report(
        self,
        suggestions: List[CodingSuggestion],
        output_path: str,
        db: QualcoderDatabase
    ) -> str:
        """Generate interactive HTML report.

        Features:
        - Summary statistics
        - Filterable by code, file, confidence
        - Approve/reject buttons for each segment
        - Inline editing of memos
        - Export decisions as JSON
        - Color-coded by confidence level
        """
```

HTML Template includes:
- Summary dashboard
- Filters (by code, file, confidence)
- Segment cards with:
  - File name + position
  - Coded text (highlighted)
  - Code name (color-coded)
  - AI memo
  - Confidence bar
  - Approve/Reject buttons
  - Edit memo field
- JavaScript for:
  - Filtering
  - Status updates
  - Export to JSON
  - Statistics updates

---

## Implementation Steps

### Phase 1: Foundation (Week 1)

**Step 1.1**: Add GUID management to database.py
- [ ] Check Qualcoder schema for GUID storage
- [ ] Implement `get_code_guids()`
- [ ] Implement `get_file_guids()`
- [ ] Implement `get_or_create_user_guid()`
- [ ] Test GUID generation and consistency

**Step 1.2**: Create data structures for suggestions
- [ ] Implement `CodingSuggestion` class
- [ ] Implement `AICodingSession` class
- [ ] Add session storage (in-memory for MVP)

**Step 1.3**: Create REFI-QDA export module
- [ ] Create `refi_export.py`
- [ ] Implement XML generation functions
- [ ] Implement `.qdpx` packaging
- [ ] Test with manual XML validation

### Phase 2: AI Coding Tools (Week 2)

**Step 2.1**: Implement core MCP tools
- [ ] `suggest_coding_for_files()` - Main AI coding
- [ ] `export_coding_suggestions()` - Export to REFI-QDA
- [ ] `update_suggestion_status()` - Approve/reject
- [ ] `get_coding_session_info()` - Query sessions

**Step 2.2**: Implement code discovery tools
- [ ] `suggest_new_codes()` - AI code suggestion
- [ ] `export_new_codes_for_import()` - Codebook export

### Phase 3: Review Interface (Week 3)

**Step 3.1**: Create HTML report generator
- [ ] Design HTML template
- [ ] Implement report generation
- [ ] Add JavaScript interactivity
- [ ] Test browser compatibility

**Step 3.2**: Add export formats
- [ ] JSON export for editing
- [ ] CSV export for documentation
- [ ] Integration with HTML report

### Phase 4: Testing & Documentation (Week 4)

**Step 4.1**: Testing
- [ ] Create test Qualcoder project
- [ ] Generate AI coding suggestions
- [ ] Export to REFI-QDA
- [ ] Import into Qualcoder
- [ ] Verify coded segments appear correctly
- [ ] Test edge cases (overlaps, invalid positions, etc.)

**Step 4.2**: Documentation
- [ ] User guide for AI coding workflow
- [ ] Import instructions with screenshots
- [ ] Troubleshooting guide
- [ ] Example prompts and use cases

**Step 4.3**: Update existing docs
- [ ] Update README.md with new features
- [ ] Update CHANGELOG.md
- [ ] Create AI_CODING_GUIDE.md

---

## GUID Management Strategy

### Challenge:
REFI-QDA requires GUIDs for all elements, but Qualcoder's database doesn't store GUIDs by default.

### Solution:
Generate deterministic GUIDs using UUID v5 (namespace-based):

```python
import uuid

# Create namespace UUID from project path
project_namespace = uuid.uuid5(uuid.NAMESPACE_DNS, f"qualcoder.{project_path_hash}")

# Generate deterministic GUIDs
code_guid = uuid.uuid5(project_namespace, f"code_{code_id}")
file_guid = uuid.uuid5(project_namespace, f"file_{file_id}")
user_guid = uuid.uuid5(project_namespace, f"user_{username}")
```

**Benefits**:
- Same code/file always gets same GUID
- No need to store GUIDs in database
- Consistent across multiple exports
- Reproducible

**Implementation**:
```python
def generate_deterministic_guid(self, entity_type: str, entity_id: Union[int, str]) -> str:
    """Generate consistent GUID for Qualcoder entities.

    Args:
        entity_type: "code", "file", "user", "coding"
        entity_id: The ID or name of the entity

    Returns:
        UUID string that will be consistent for this entity
    """
    # Use project path as namespace
    project_hash = hashlib.sha256(self.db_path.encode()).hexdigest()[:32]
    namespace = uuid.UUID(f"{project_hash[:8]}-{project_hash[8:12]}-{project_hash[12:16]}-{project_hash[16:20]}-{project_hash[20:32]}")

    # Generate UUID v5 based on entity
    entity_string = f"{entity_type}_{entity_id}"
    return str(uuid.uuid5(namespace, entity_string))
```

---

## Example Workflow

### Scenario: Code 5 interview transcripts

```
User: "I have 5 interview transcripts (files 10-14). Help me code them
      with themes related to workplace satisfaction."

Claude: "I'll analyze those interviews. First, let me read them to
        understand the content."

[Uses analyze_file_with_coding for files 10-14]

Claude: "I've analyzed all 5 interviews. I found discussions about:
        - Management relationships
        - Work-life balance
        - Career growth
        - Team dynamics
        - Compensation concerns

        Would you like me to:
        1. Use your existing codes, or
        2. Suggest new codes for these themes?"

User: "Suggest new codes"

Claude: [Calls suggest_new_codes(file_ids=[10,11,12,13,14])]

        "I suggest these 8 codes:
        1. Management - Supportive (28 segments)
        2. Management - Micromanagement (15 segments)
        3. Work-Life Integration (42 segments)
        4. Career Development Opportunities (31 segments)
        5. Team Collaboration (38 segments)
        6. Compensation Satisfaction (22 segments)
        7. Compensation Concerns (19 segments)
        8. Job Security (12 segments)

        Each code includes examples and descriptions. Would you like
        to approve these codes?"

User: "Yes, but merge codes 6 and 7 into just 'Compensation Views'"

Claude: [Adjusts code list]

        "Perfect. I'll export these codes for you to import into Qualcoder."

        [Calls export_new_codes_for_import]

        "I've created: ~/Documents/new_codes_workplace_satisfaction.qdpx

        To import:
        1. Open Qualcoder
        2. File > Import > REFI-QDA Codebook
        3. Select new_codes_workplace_satisfaction.qdpx
        4. Review and confirm

        Let me know when you've imported them, and I'll code the files."

User: "Done, codes are imported"

Claude: "Great! Now I'll code all 5 interviews with those codes."

        [Calls suggest_coding_for_files(file_ids=[10,11,12,13,14],
                code_names=[all the new codes])]

        "Analysis complete! I've coded 207 segments across 5 files:

        By file:
        - Interview_10.txt: 38 segments
        - Interview_11.txt: 45 segments
        - Interview_12.txt: 41 segments
        - Interview_13.txt: 52 segments
        - Interview_14.txt: 31 segments

        By code:
        - Management - Supportive: 28 segments
        - Management - Micromanagement: 15 segments
        - Work-Life Integration: 42 segments
        - Career Development: 31 segments
        - Team Collaboration: 38 segments
        - Compensation Views: 41 segments
        - Job Security: 12 segments

        I'll generate a review report for you."

        [Calls export_coding_suggestions(session_id, format="html-report")]

        "Created: ~/Documents/coding_review_2025-10-28.html

        Open this file in your browser to review all 207 suggestions.
        You can approve/reject each one or edit the memos.

        When you're ready, I'll export the approved ones for import."

User: [Opens HTML report, reviews, approves 195, rejects 12]

User: "Reviewed. Export the approved ones"

Claude: [Calls export_coding_suggestions(session_id, format="refi-qda")]

        "Created: ~/Documents/workplace_coding_2025-10-28.qdpx

        This contains 195 approved coded segments.

        To import:
        1. Open Qualcoder
        2. File > Import > REFI-QDA Project
        3. Select workplace_coding_2025-10-28.qdpx
        4. Qualcoder will show you a preview
        5. Confirm the import

        Qualcoder will add all coded segments to your project!"

User: "Done! All 195 segments are now in my project. This is amazing!"
```

---

## Safety & Validation

### Pre-Export Validation

Before generating REFI-QDA XML, validate:
- [ ] All codes exist in the project
- [ ] All files exist in the project
- [ ] Position ranges are valid (0 <= pos0 < pos1 <= file_length)
- [ ] Segment text matches file content at specified positions
- [ ] No duplicate codings (same code, file, position)
- [ ] Confidence scores are 0-1
- [ ] GUIDs are valid UUIDs

### Import Safety (Qualcoder's responsibility)

Qualcoder's REFI-QDA import will:
- ✅ Create automatic backup before import
- ✅ Validate XML against REFI-QDA schema
- ✅ Check for foreign key violations
- ✅ Preview import before confirming
- ✅ Allow user to cancel

### Error Handling

- Invalid file ID → Skip suggestion, log warning
- Invalid code ID → Skip suggestion, log warning
- Invalid position → Skip suggestion, log warning
- XML generation error → Abort export, show clear error
- GUID collision → Regenerate with new namespace

---

## File Structure After Implementation

```
qualcoder_mcp/
├── src/
│   └── qualcoder_mcp/
│       ├── __init__.py
│       ├── server.py              # MCP server (6 new tools added)
│       ├── database.py            # Database interface (GUID + suggestion methods)
│       ├── refi_export.py         # NEW: REFI-QDA XML generation
│       └── html_report.py         # NEW: HTML review report generator
├── templates/
│   └── coding_review.html         # NEW: HTML template for review interface
├── examples/
│   ├── example_coding_session.json      # NEW: Example suggestion data
│   └── example_import_workflow.md       # NEW: Step-by-step guide
├── tests/
│   ├── test_refi_export.py        # NEW: Test REFI-QDA generation
│   ├── test_ai_coding.py          # NEW: Test AI coding tools
│   └── test_guid_generation.py    # NEW: Test GUID consistency
├── docs/
│   ├── AI_CODING_GUIDE.md         # NEW: User guide for AI coding
│   └── IMPORT_INSTRUCTIONS.md     # NEW: How to import into Qualcoder
├── README.md                      # Updated with AI coding features
├── CHANGELOG.md                   # Updated for v0.3.0
└── pyproject.toml                 # Version bump to 0.3.0
```

---

## Testing Plan

### Unit Tests

1. **GUID Generation**
   - Same entity always gets same GUID
   - Different entities get different GUIDs
   - GUIDs are valid UUIDs

2. **Coding Suggestions**
   - Can create suggestions
   - Can filter by status
   - Can update status
   - Can export to dict/JSON

3. **REFI-QDA Export**
   - Valid XML structure
   - Proper namespaces
   - All required elements present
   - .qdpx ZIP structure correct

### Integration Tests

1. **End-to-End Workflow**
   - Create test project in Qualcoder
   - Generate AI coding suggestions
   - Export to REFI-QDA
   - Import into Qualcoder
   - Verify segments appear correctly

2. **Import Validation**
   - Test with various segment counts
   - Test with overlapping segments
   - Test with invalid positions
   - Test with missing codes/files

### Manual Testing Checklist

- [ ] Generate suggestions for real transcripts
- [ ] Review HTML report in multiple browsers
- [ ] Edit and approve suggestions
- [ ] Export to REFI-QDA
- [ ] Import into Qualcoder
- [ ] Verify all data integrity
- [ ] Test undo/backup features
- [ ] Test with large datasets (100+ segments)

---

## Documentation Deliverables

### 1. AI_CODING_GUIDE.md
Complete user guide with:
- Introduction to AI coding
- Step-by-step workflows
- Example prompts
- Tips for effective coding
- Troubleshooting

### 2. IMPORT_INSTRUCTIONS.md
Detailed import guide with:
- Screenshots of Qualcoder import dialog
- Step-by-step instructions
- What to expect during import
- Verification steps
- Rollback instructions if needed

### 3. Updated README.md
Add section:
- AI-Assisted Coding features
- New tools available
- Quick start guide
- Link to full documentation

### 4. CHANGELOG.md - v0.3.0 Entry
Document all new features:
- 6 new MCP tools
- REFI-QDA export capability
- HTML review interface
- Code discovery features

---

## Version Planning

### v0.3.0 - AI Coding Foundation
- Core AI coding tools
- REFI-QDA export
- HTML review interface
- Basic code discovery

### v0.4.0 - Enhanced AI Coding (Future)
- Batch coding optimization
- Advanced pattern recognition
- Code refinement suggestions
- Coding consistency checks
- Multi-coder collaboration support

### v0.5.0 - Advanced Features (Future)
- Code hierarchy suggestions
- Memo enhancement suggestions
- Relationship detection
- Visualization exports

---

## Questions Before Implementation

1. **Testing approach**: Should I create a test Qualcoder project, or do you have one we can use for testing?

2. **Confidence thresholds**: What minimum confidence level would you want by default (0.5, 0.7, 0.8)?

3. **Session persistence**: Should coding sessions be saved to disk, or kept in memory (cleared when MCP restarts)?

4. **Overlapping segments**: Should we warn about overlaps, prevent them, or allow them?

5. **Batch size limits**: Should there be a maximum number of suggestions per session (e.g., 500 max)?

---

## Timeline Estimate

**Total: 2-3 weeks**

- Week 1: Database + REFI-QDA export (Foundation)
- Week 2: MCP tools + AI coding logic (Core functionality)
- Week 3: HTML interface + testing + docs (Polish & ship)

---

## Ready to Proceed?

This is the complete implementation plan. Once you approve:

1. I'll start with Phase 1: Foundation
2. We'll test GUID generation and REFI-QDA export
3. Then move to Phase 2: AI Coding Tools
4. Finally Phase 3: Review Interface

**Do you approve this plan? Any changes or questions before I start coding?**
