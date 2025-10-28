# Qualcoder Import Capabilities - Research Summary

## What I Found

After examining the Qualcoder source code, I discovered Qualcoder **DOES have import capabilities**, but they're specific and different from what I initially proposed.

---

## Supported Import Formats

### 1. REFI-QDA XML Format ⭐ **MOST IMPORTANT**

**What it is**: Rotterdam Exchange Format Initiative - a standard XML format for exchanging qualitative data analysis projects between different QDA software (Qualcoder, NVivo, MAXQDA, Atlas.ti, etc.)

**File**: `refi.py` - Full REFI-QDA import implementation

**What can be imported**:
- ✅ Codebooks (codes and code hierarchy)
- ✅ **Coded text segments** (our main need!)
- ✅ Cases
- ✅ Attributes (variables)
- ✅ Source files (text, images, audio, video)
- ✅ Annotations
- ✅ Users
- ✅ Memos
- ✅ Links

**Format for coded segments** (from the source code):
```xml
<PlainTextSelection
    guid="08cbced0-d736-44c8-8fd6-eb4d29fe46c5"
    name=""
    startPosition="1967"
    endPosition="2207"
    creatingUser="5c94bc9e-db8c-4f1d-9cd6-e900c7440860"
    creationDateTime="2019-06-07T03:36:36Z"
    modifyingUser="5c94bc9e-db8c-4f1d-9cd6-e900c7440860"
    modifiedDateTime="2019-06-07T03:36:36Z">

    <Description>This is a memo for this coded segment</Description>

    <Coding
        guid="76414714-63c4-4a25-a47e-66fef80bd52e"
        creatingUser="5c94bc9e-db8c-4f1d-9cd6-e900c7440860"
        creationDateTime="2019-06-06T06:27:01Z">
        <CodeRef targetGUID="2dfba8c9-59f5-4424-99d6-ea9bce18134b"/>
    </Coding>
</PlainTextSelection>
```

**Key fields**:
- `startPosition` / `endPosition`: Character positions in the source text
- `creationDateTime`: When the coding was created
- `creatingUser`: GUID reference to a user
- `Description`: Optional memo/annotation for the coded segment
- `CodeRef targetGUID`: References the code being applied (must match a Code element's GUID)

**File structure**: REFI-QDA projects are `.qdpx` files (ZIP archives) containing:
- `project.qde` - Main XML file with project structure
- `/Sources/` - Source documents
- `/Codes/` - Codebook
- Various other XML files

**Status**:
- ⚠️ Marked as "experimental" as of December 2020 (may have bugs)
- ✅ Has been tested with exports from NVivo, MAXQDA, Atlas.ti, Quirkos
- ✅ Fully implemented import functionality in Qualcoder

---

### 2. CSV Import for Cases and Attributes ✅

**File**: `cases.py` - `import_cases_and_attributes()` function

**What can be imported**:
- ✅ Case names
- ✅ Case attributes (demographics, metadata)
- ❌ **Cannot import coded segments via CSV**

**Format** (from `Examples/cases.csv`):
```csv
case,Age,gender,interest
ID1,20,f,science
ID2,22,m,performing arts
ID3,45,f,politics
```

**Requirements**:
- First column: Case names (required)
- Subsequent columns: Attribute values
- First row: Header with attribute names
- Comma-delimited
- Attribute types (numeric vs. character) are auto-detected

**Import process**:
1. Reads CSV file
2. Creates cases from first column
3. Creates attribute types from header row
4. Auto-detects if attribute is numeric or character
5. Inserts attribute values for each case

---

### 3. Excel (.xlsx) Import for Cases and Attributes ✅

**Same as CSV** - Qualcoder can read Excel files for cases and attributes with the same format.

---

### 4. Survey Data Import 🔍

**Files found**:
- `import_survey.py`
- Examples: `survey456.csv`, `survey789.xlsx`

**Purpose**: Import survey responses as cases with attributes (similar to CSV import but with survey-specific handling)

---

### 5. Twitter Data Import 🐦

**Files found**:
- `import_twitter_data.py`
- Example: `rtweet_judo_tweets_data.csv`

**Purpose**: Import tweets as source documents with metadata

---

## ❌ What Qualcoder CANNOT Import Directly

1. **Coded segments via CSV** - There is NO CSV import for coded text segments
2. **Coded segments via JSON** - There is NO JSON import for any data
3. **Standalone code applications** - Can only import codes as part of REFI-QDA project

---

## 🎯 Implications for Our MCP Server

### **Revised Strategy: Generate REFI-QDA XML**

Instead of generating CSV/JSON files, we should:

1. **Generate REFI-QDA XML** format for importing coded segments
2. This is the **official, tested, standard way** Qualcoder expects to receive coded segments
3. It's interoperable with other QDA software
4. Already has validation and error handling built into Qualcoder

### **New Implementation Plan**

#### **Phase 1: Code Discovery & Approval** (No change)
- AI suggests new codes
- User approves
- Export as REFI-QDA Codebook XML for import into Qualcoder

#### **Phase 2: Automated Coding** (MAJOR CHANGE)
Instead of CSV/JSON + custom import script, we:

**Step 2.1**: AI codes files (same as before)
```
User: "Code files 3, 5, and 7 using these codes"
AI analyzes and stores suggestions in memory
```

**Step 2.2**: Export to REFI-QDA XML format
```python
New MCP Tool: export_coding_as_refi_qda(
    output_path="~/Documents/ai_coding_suggestions.qdpx"
)
```

Generates a `.qdpx` file (ZIP archive) with:
- `project.qde` - XML with PlainTextSelection elements for each coded segment
- Proper GUIDs for codes, users, sources
- Timestamps, memos, all metadata

**Step 2.3**: Review interface (HTML report - same as before)
User reviews suggestions before importing

**Step 2.4**: Edit the XML (Advanced users only)
Or we can provide a JSON intermediate format for easier editing, then convert to REFI-QDA

**Step 2.5**: Import into Qualcoder
```
File > Import > REFI-QDA Project
Select: ai_coding_suggestions.qdpx
Qualcoder merges the coded segments into your project
```

---

## 🔍 REFI-QDA Format Details

### Required XML Structure

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Project
    xmlns="urn:QDA-XML:project:1.0"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xsi:schemaLocation="urn:QDA-XML:project:1.0 Project.xsd"
    name="AI Coding Suggestions"
    origin="QualCoder MCP"
    creatingUserGUID="..."
    creationDateTime="2025-10-28T14:30:00Z">

    <Users>
        <User
            guid="..."
            name="ai_coder"/>
    </Users>

    <CodeBook>
        <Codes>
            <Code
                guid="..."
                name="Workplace Stress - Causes"
                isCodable="true"
                color="#FF5733">
                <Description>Factors that create stress at work</Description>
            </Code>
            <!-- More codes... -->
        </Codes>
    </CodeBook>

    <Sources>
        <TextSource
            guid="..."
            name="interview_003.txt"
            plainTextPath="internal://interview_003.txt"
            creatingUser="..."
            creationDateTime="...">
            <Description/>

            <!-- THIS IS WHERE THE CODED SEGMENTS GO -->
            <PlainTextSelection
                guid="..."
                startPosition="450"
                endPosition="678"
                creatingUser="..."
                creationDateTime="...">
                <Description>AI suggested: Clear causal relationship</Description>
                <Coding guid="..." creatingUser="..." creationDateTime="...">
                    <CodeRef targetGUID="[GUID of the code]"/>
                </Coding>
            </PlainTextSelection>

            <!-- More coded segments for this file... -->

        </TextSource>
        <!-- More source files... -->
    </Sources>
</Project>
```

### GUID Management

Each element needs a unique GUID (UUID v4):
- Users
- Codes
- Sources (files)
- Codings
- PlainTextSelections

We need to:
1. Query Qualcoder database for existing GUIDs of codes and files
2. Generate new GUIDs for our AI-suggested codings
3. Ensure GUID references are consistent

---

## 📊 Comparison: Original Plan vs. REFI-QDA Approach

| Aspect | Original Plan (CSV) | REFI-QDA Approach |
|--------|---------------------|-------------------|
| **Format** | CSV + custom import script | Standard REFI-QDA XML |
| **Validation** | Custom implementation | Built into Qualcoder |
| **Import Method** | Python script we write | Qualcoder's native import |
| **Risk** | Higher (custom code) | Lower (tested standard) |
| **Compatibility** | Qualcoder only | Works with NVivo, MAXQDA, etc. |
| **User Experience** | Run script from command line | File > Import in Qualcoder GUI |
| **Error Handling** | Custom | Qualcoder's built-in |
| **Backup** | We implement | Qualcoder does automatically |
| **Complexity** | Medium | Medium-High (XML generation) |
| **Review UI** | HTML report (we build) | HTML report (we build) same |
| **Interoperability** | None | Can share with other QDA tools |

---

## 🤔 Questions for You

### 1. **REFI-QDA vs. Custom Script**

Given that Qualcoder has native REFI-QDA import:
- **Option A**: Generate REFI-QDA XML → Use Qualcoder's File > Import (standard, safer)
- **Option B**: Stick with CSV + custom import script (more control, but custom code)
- **Option C**: Provide both options

Which appeals to you?

### 2. **GUID Management**

REFI-QDA requires GUIDs for everything. We need to:
- Read existing code/file GUIDs from your database
- Generate new GUIDs for the coded segments
- Ensure consistency

This means we'd need to:
- Add a READ-ONLY query to get GUIDs from database
- Or generate a mapping file

Is this acceptable?

### 3. **Import Workflow**

**Option A**: REFI-QDA approach
```
1. Claude generates coding_suggestions.qdpx file
2. You review HTML report
3. You open Qualcoder: File > Import > REFI-QDA Project
4. Qualcoder imports coded segments
```

**Option B**: Custom script approach
```
1. Claude generates coding_suggestions.csv
2. You review HTML report
3. You run: python import_coding.py --input coding_suggestions.csv
4. Script writes to database directly
```

Which workflow feels better?

### 4. **Partial vs. Full Project**

REFI-QDA can import:
- Full project (codes + files + codings)
- Just codebook (codes only)
- **Unclear**: Can it import just codings without full project structure?

We may need to:
- Include your existing codes in the XML (reference)
- Include your existing files in the XML (reference)
- Then add the new coded segments

This means the REFI export would reference your existing project. Is that okay?

---

## 💡 My Recommendation

### **Hybrid Approach**

1. **Generate REFI-QDA XML** as the primary method
   - Standard, tested, safe
   - Uses Qualcoder's native import
   - Better error handling

2. **Provide HTML review interface** (same as before)
   - Visual review of suggestions
   - Edit/approve/reject before import
   - Generate final REFI-QDA from approved suggestions only

3. **Also provide CSV export option** (secondary)
   - For users who want to see data in spreadsheet
   - For record-keeping
   - Not for import, just for review/documentation

4. **Create simple import guide**
   - Step-by-step: How to import REFI-QDA into Qualcoder
   - Screenshots of the process
   - Troubleshooting

---

## 🚀 Next Steps

I need your decision on:

1. **Approach**: REFI-QDA, custom script, or both?
2. **GUID handling**: Query database for existing GUIDs? Or manual mapping?
3. **Import workflow**: Which feels more natural to you?
4. **Scope**: Should we test REFI import with a small example first?

Once you decide, I'll implement accordingly!

---

## 📚 Resources

- **REFI-QDA Standard**: https://www.qdasoftware.org/
- **Qualcoder REFI Code**: `/tmp/QualCoder/src/qualcoder/refi.py`
- **Qualcoder CSV Import**: `/tmp/QualCoder/src/qualcoder/cases.py` (lines 489-562)
- **Example CSV**: `/tmp/QualCoder/Examples/cases.csv`

---

## ⚠️ Important Notes

1. **REFI-QDA marked experimental** - As of Dec 2020, Qualcoder notes it's still experimental. We should test thoroughly.

2. **Backup critical** - Whether using REFI or custom script, backing up the .qda file before import is essential.

3. **GUIDs must match** - For REFI approach, code and file GUIDs must match what's in the database.

4. **Position validation** - Coded segment positions must be valid for the source file text.

5. **User references** - All codings must reference a valid user GUID.

---

Let me know your thoughts and I'll adjust the implementation plan!
