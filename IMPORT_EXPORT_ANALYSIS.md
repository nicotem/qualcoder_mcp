# Qualcoder Import/Export Analysis Report

> **HISTORICAL DOCUMENT (early analysis).** This scoping report predates
> the write-enabled releases. The server is no longer read-only and does
> support importing text and exporting REFI-QDA; see the README and
> CHANGELOG for current capabilities.

## Executive Summary

The Qualcoder MCP server **currently has NO built-in import capabilities** for coded segments. The codebase is READ-ONLY and provides only export/query functionality. To implement coded segment imports, we would need to create new import functionality that writes to the Qualcoder SQLite database.

---

## 1. Current Qualcoder Import/Export Capabilities

### EXISTING EXPORT FEATURES (Read-Only):
✅ `export_code_report(code_name)` - Exports detailed code report as JSON
✅ `search_coded_text()` - Search and return coded segments
✅ `get_coded_segments()` - Retrieve all segments for a code
✅ Full transcript analysis with coding context
✅ JSON-serializable output for all queries

### MISSING IMPORT FEATURES:
❌ No built-in import for CSV files
❌ No built-in import for JSON files
❌ No built-in import for XML files
❌ No import endpoints in MCP server
❌ No import functions in database.py

---

## 2. Complete code_text Table Schema

### Table Definition:
```
Table: code_text
Purpose: Stores all text segments that have been coded/annotated

Columns:
├── ctid (INTEGER, PRIMARY KEY)
│   └── Unique identifier for each coded segment
│
├── cid (INTEGER, FOREIGN KEY → code_name.cid)
│   └── References the code applied to this segment
│
├── fid (INTEGER, FOREIGN KEY → source.id)
│   └── References the source file containing the text
│
├── seltext (TEXT)
│   └── The actual selected/highlighted text content
│
├── pos0 (INTEGER)
│   └── Character position where segment starts (0-indexed)
│
├── pos1 (INTEGER)
│   └── Character position where segment ends (exclusive)
│
├── memo (TEXT, NULLABLE)
│   └── Optional annotation/note on this coded segment
│
├── owner (TEXT)
│   └── Username/identifier of who created the coding
│
├── date (TEXT/DATETIME)
│   └── Timestamp when the segment was coded
│
└── important (INTEGER/BOOLEAN, DEFAULT=0)
    └── Flag marking important segments (0=not important, 1=important)
```

---

## 3. Related Tables Involved in Import

### Primary Dependencies:

#### code_name Table:
```
Columns:
├── cid (INTEGER, PRIMARY KEY)
│   └── Unique code identifier
├── name (TEXT, UNIQUE)
│   └── Human-readable code name
├── memo (TEXT)
│   └── Description of the code
├── catid (INTEGER, FOREIGN KEY → code_cat.catid)
│   └── Category this code belongs to
├── color (TEXT)
│   └── RGB color for visualization
├── owner (TEXT)
│   └── Code creator
└── date (TEXT)
    └── Creation date
```

#### source Table:
```
Columns:
├── id (INTEGER, PRIMARY KEY)
│   └── Unique file identifier
├── name (TEXT)
│   └── Filename or document title
├── fulltext (TEXT)
│   └── Complete text content
├── mediapath (TEXT, NULLABLE)
│   └── Path to media file (if not text)
├── memo (TEXT)
│   └── File description
├── owner (TEXT)
│   └── File owner
└── date (TEXT)
    └── Upload/creation date
```

#### code_cat Table:
```
Columns:
├── catid (INTEGER, PRIMARY KEY)
│   └── Category identifier
├── name (TEXT)
│   └── Category name
├── supercatid (INTEGER, NULLABLE)
│   └── Parent category (for hierarchies)
├── memo (TEXT)
│   └── Category description
├── owner (TEXT)
└── date (TEXT)
```

---

## 4. Constraints and Relationships

### Foreign Key Relationships:
```
code_text.cid → code_name.cid (REQUIRED)
│ └── Cannot import coded segments without existing code
│
code_text.fid → source.id (REQUIRED)
│ └── Cannot import coded segments without existing file
│
code_name.catid → code_cat.catid (OPTIONAL)
└── Code may not belong to any category
```

### Field Constraints:

| Field | Type | Null | Unique | Constraints |
|-------|------|------|--------|------------|
| ctid | INTEGER | NO | YES | AUTO-INCREMENT (Primary Key) |
| cid | INTEGER | NO | NO | Must exist in code_name |
| fid | INTEGER | NO | NO | Must exist in source |
| seltext | TEXT | YES | NO | Max length ~4GB (SQLite TEXT limit) |
| pos0 | INTEGER | YES | NO | Must be >= 0 |
| pos1 | INTEGER | YES | NO | Must be > pos0 |
| memo | TEXT | YES | NO | Unlimited length |
| owner | TEXT | YES | NO | Typically a username string |
| date | TEXT | YES | NO | ISO format timestamp |
| important | INTEGER | YES | NO | 0 or 1 (boolean as int) |

### Business Logic Constraints:
```
Position Constraints:
├── pos0 must be >= 0
├── pos1 must be > pos0
├── pos1 must be <= length(source.fulltext)
└── segment text should match source.fulltext[pos0:pos1]

Code Constraints:
├── Code must exist in code_name table
├── Code must be active (not deleted)
└── Code must match the coding style (e.g., no special chars)

File Constraints:
├── File must exist in source table
├── File must contain the text (if importing text segments)
└── File must not be deleted/archived
```

---

## 5. Required vs Optional Fields

### REQUIRED for code_text insertion:
```
✓ cid (code ID) - Foreign key, must reference valid code
✓ fid (file ID) - Foreign key, must reference valid file
✓ seltext - The actual coded text content
✓ pos0 - Start position in source file
✓ pos1 - End position in source file
```

### OPTIONAL for code_text insertion:
```
~ memo - Can be null (no annotation)
~ owner - Can be null (defaults to current coder or empty)
~ date - Can be null (defaults to current timestamp)
~ important - Defaults to 0 if not specified
```

### AUTO-GENERATED:
```
✓ ctid - Automatically assigned on insertion (PRIMARY KEY)
```

---

## 6. Current Implementation Details

### Read-Only Architecture:
```python
# Database connection is READ-ONLY
conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
                                                  ↑
                                         Read-only flag
```

### Schema Validation:
```python
required_tables = ['project', 'code_name', 'code_text', 'source', 'cases']
# Server validates these tables exist on connection
```

### Supported Database Versions:
```python
SUPPORTED_DB_VERSIONS = ['v6', 'v7', 'v8', 'v9', 'v10', 'v11', 'v12', 'v13']
# Different Qualcoder versions may have schema variations
```

---

## 7. Recommended Import Formats

### Based on code_text schema structure:

#### Option A: CSV Format (RECOMMENDED - Simplest)
```csv
code_name,file_name,position_start,position_end,segment_text,memo,owner,important
"Theme1","interview_001.txt",0,45,"This is the coded text","Optional note","coder_name",0
"Theme2","interview_001.txt",50,120,"Another segment","","coder_name",1
```

**Advantages:**
- Human-readable and editable
- Easy to import from Excel/Sheets
- Clear column mapping to database fields
- Can be validated with existing tools

**Import Script Structure:**
```python
def import_coded_segments_from_csv(db_path, csv_path, coder_name):
    """
    1. Validate all codes exist in code_name table
    2. Validate all files exist in source table
    3. Validate position ranges don't exceed file lengths
    4. Validate text matches file content at specified positions
    5. Insert into code_text with FOREIGN KEY constraints
    """
```

#### Option B: JSON Format (Flexible)
```json
{
  "import_metadata": {
    "version": "1.0",
    "date": "2025-10-28",
    "coder": "user@example.com"
  },
  "coded_segments": [
    {
      "code_name": "Theme1",
      "file_name": "interview_001.txt",
      "position_start": 0,
      "position_end": 45,
      "segment_text": "This is the coded text",
      "memo": "Optional note",
      "important": false
    }
  ]
}
```

**Advantages:**
- Supports nested structures
- Can include validation metadata
- Supports batch operations with metadata
- Reversible (can export and re-import)

#### Option C: XML Format (Verbose)
```xml
<?xml version="1.0" encoding="UTF-8"?>
<coded_segments>
  <metadata>
    <version>1.0</version>
    <coder>user@example.com</coder>
    <date>2025-10-28</date>
  </metadata>
  <segments>
    <segment>
      <code_name>Theme1</code_name>
      <file_name>interview_001.txt</file_name>
      <positions>
        <start>0</start>
        <end>45</end>
      </positions>
      <text>This is the coded text</text>
      <memo>Optional note</memo>
      <important>false</important>
    </segment>
  </segments>
</coded_segments>
```

**Advantages:**
- Structured and validatable against schema
- Can include attributes and metadata
- Clear parent-child relationships
- Wide tool support

---

## 8. Import Implementation Requirements

### Pre-Import Validation Checklist:
```
□ Database path valid and writable
□ All referenced codes (by code_name) exist in code_name table
□ All referenced files (by file_name) exist in source table
□ No duplicate segments (prevent duplicate ctid)
□ Position values valid (pos0 >= 0, pos1 > pos0)
□ Text content matches file at specified positions
□ Owner/coder username exists or create as new
□ Date format valid (ISO 8601 or SQL datetime)
□ No conflicting segment overlaps (if enforced)
□ Memo field doesn't exceed SQLite TEXT limits
```

### Database Modification Requirements:
```
1. CHANGE CONNECTION MODE
   ├── Current: sqlite3.connect(f"file:{path}?mode=ro", uri=True)
   └── New: sqlite3.connect(db_path) # Normal read-write mode

2. TRANSACTION MANAGEMENT
   ├── Begin transaction for batch imports
   ├── Rollback on any validation error
   ├── Commit only after full validation

3. FOREIGN KEY ENFORCEMENT
   ├── Enable "PRAGMA foreign_keys = ON"
   ├── SQLite has this OFF by default
   └── Prevents inserting non-existent codes/files

4. PRIMARY KEY HANDLING
   ├── ctid is AUTOINCREMENT
   ├── Don't specify ctid in INSERT
   ├── Let SQLite assign next available ID
```

---

## 9. Security Considerations for Import

### Critical Security Measures:
```
1. INPUT VALIDATION
   ├── Validate all code names exist (prevent injection of new codes)
   ├── Validate all file names exist (prevent file creation)
   ├── Escape all text strings (prevent SQL injection)
   └── Validate numeric ranges (position, important flag)

2. DATA INTEGRITY
   ├── Verify text content matches positions in source file
   ├── Ensure position values are within file bounds
   ├── Check for logical conflicts (overlapping segments with same code)
   └── Maintain owner audit trail (log who imported data)

3. DATABASE INTEGRITY
   ├── Use parameterized queries (never string concatenation)
   ├── Enable FOREIGN KEY constraints
   ├── Wrap in transaction with rollback on failure
   ├── Verify constraints after insertion
   └── Maintain backup before large imports

4. FILE ACCESS
   ├── Verify import file is not a database file
   ├── Check file encoding (UTF-8 expected)
   ├── Scan for malicious payloads in import data
   └── Log all imports with timestamp and source
```

---

## 10. Recommendations for Import Design

### Recommended Approach:

**Phase 1: CSV Import (PRIORITY - Implement First)**
```python
@mcp.tool()
def import_coded_segments_csv(
    csv_file_path: str,
    validate_only: bool = True
) -> str:
    """
    Import coded segments from CSV file.
    
    CSV Format:
    code_name,file_name,position_start,position_end,segment_text,memo,owner,important
    
    Args:
        csv_file_path: Path to CSV file
        validate_only: If True, validate but don't import (dry-run)
    
    Returns:
        JSON report with:
        - segments_validated: count
        - segments_imported: count
        - errors: list of validation failures
        - warnings: list of warnings
    """
```

**Why CSV First:**
- Easiest for users to prepare in Excel
- Simple to parse and validate
- Can be done incrementally
- Easy error handling and reporting

**Phase 2: JSON Batch Import**
```python
@mcp.tool()
def import_coded_segments_json(
    json_file_path: str,
    validate_only: bool = True
) -> str:
    """Import coded segments from JSON file."""
```

**Why JSON Second:**
- More flexible for programmatic sources
- Better for batch operations
- Supports metadata
- Can include validation info

**Phase 3: Direct API Import**
```python
@mcp.tool()
def add_coded_segment(
    code_name: str,
    file_name: str,
    position_start: int,
    position_end: int,
    segment_text: str,
    memo: str = "",
    owner: str = None,
    important: bool = False
) -> str:
    """Add a single coded segment (no file needed)."""
```

**Why API Third:**
- For real-time/programmatic imports
- Single-segment or batch via repeated calls
- Direct integration with external systems

---

## 11. Export/Query Capabilities (Current)

### Available Export Methods:
```
✅ export_code_report(code_name)
   └── Returns JSON with code metadata and all coded segments

✅ get_coded_segments(code_id, limit=100)
   └── Returns JSON array of all segments for a code

✅ search_coded_text(query, code_name=None, limit=50)
   └── Full-text search returning matching segments

✅ analyze_file_with_coding(file_id)
   └── Returns complete file text + all coding annotations

✅ get_file_with_coding(file_id)
   └── Enhanced export with rich context and statistics

✅ get_case_code_matrix()
   └── Cross-tabulation of cases vs codes
```

### Output Format:
- All exports are JSON-serializable
- Include metadata (dates, owners, codes)
- Preserve all segment information
- Include statistics and context

---

## Summary Table: Import Readiness

| Aspect | Status | Notes |
|--------|--------|-------|
| **Export Capability** | ✅ Full | JSON output available |
| **Import Capability** | ❌ None | Need to implement |
| **Database Writeable** | ❌ No | Currently read-only |
| **Schema Documented** | ✅ Yes | See section 2-3 |
| **CSV Format Ready** | ✅ Yes | Clear column mapping |
| **JSON Format Ready** | ✅ Yes | Can design schema |
| **Validation Logic** | ⚠️ Partial | Need to add FK validation |
| **Security Measures** | ✅ Yes | Input validation exists |

---

## Implementation Roadmap

```
PHASE 1 (CRITICAL):
├── [ ] Add import_coded_segments_csv() tool
├── [ ] Add CSV validation logic
├── [ ] Add database write capability (mode switch)
├── [ ] Add FK constraint checking
└── [ ] Test with sample data

PHASE 2 (IMPORTANT):
├── [ ] Add import_coded_segments_json() tool
├── [ ] Add batch import with transaction support
├── [ ] Add import logging/audit trail
└── [ ] Add rollback capability on errors

PHASE 3 (NICE-TO-HAVE):
├── [ ] Add add_coded_segment() for single items
├── [ ] Add import progress reporting
├── [ ] Add duplicate detection
└── [ ] Add conflict resolution strategies
```

