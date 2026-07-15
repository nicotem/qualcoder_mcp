# REFI-QDA Import Instructions

> **HISTORICAL DOCUMENT.** These instructions belong to the removed
> v0.3.0 export/import workflow. Since v0.4.0 approved codings are
> written directly to the project database (see AI_CODING_WORKFLOW.md);
> REFI-QDA files produced by `export_refi_qda` are for OTHER QDA
> software, not for re-importing into the same project.

Step-by-step guide for importing AI coding suggestions into Qualcoder.

## Table of Contents

- [Overview](#overview)
- [Before You Import](#before-you-import)
- [Import Steps](#import-steps)
- [What Happens During Import](#what-happens-during-import)
- [After Import](#after-import)
- [Troubleshooting](#troubleshooting)
- [Advanced Topics](#advanced-topics)

## Overview

AI coding suggestions are exported as `.qdpx` files using the REFI-QDA standard format. This is a widely-supported format for exchanging qualitative data between QDA software.

**Important**: The import process **adds** coded segments to your project. It does not replace or delete anything. Your existing codes and coding remain intact.

### What Gets Imported

When you import a `.qdpx` file from AI coding:

✅ **Coded segments** - The text positions and code assignments
✅ **AI memos** - Explanations for each coded segment (with confidence scores)
✅ **User attribution** - All coding appears as done by "AI Coding Assistant"
✅ **Timestamps** - Import date/time is recorded

❌ **Not imported**: New codes (codes must already exist in your project)
❌ **Not imported**: New files (files must already exist in your project)

## Before You Import

### 1. Backup Your Project

Always backup before importing:

```bash
# On Mac/Linux:
cp ~/Documents/QualCoder_projects/MyProject/MyProject.qda ~/Documents/QualCoder_projects/MyProject/MyProject_backup.qda

# Or just duplicate the folder in Finder
```

### 2. Verify the Export File

Check that your `.qdpx` file exists and has reasonable size:

```bash
ls -lh ~/Desktop/ai_coding.qdpx
# Should show file size (typically a few KB)
```

### 3. Confirm Project State

Before importing:
- Your Qualcoder project should be open
- All codes referenced in the export must exist in your project
- All files referenced in the export must exist in your project
- Consider closing other projects to avoid confusion

### 4. Plan Your Import Strategy

Decide:
- **Import everything**: Full session import
- **Import selectively**: Filter by file or code first, then export only what you want
- **Test import first**: Use a copy of your project for testing

## Import Steps

### Step 1: Open Qualcoder

1. Launch Qualcoder
2. Open your research project (the same one you used with the MCP)
3. Make sure the project loads without errors

### Step 2: Navigate to Import Function

1. Go to the menu bar
2. Click **File**
3. Hover over **Import**
4. Click **REFI-QDA Project**

![Import Menu](https://placeholder-for-screenshot)

**Menu path**: `File > Import > REFI-QDA Project`

**Keyboard shortcut**: None (must use menu)

### Step 3: Select the .qdpx File

1. A file browser dialog opens
2. Navigate to where you saved the export (e.g., `~/Desktop/`)
3. Select the `.qdpx` file (e.g., `ai_coding.qdpx`)
4. Click **Open**

**Tip**: If you don't see the file, make sure the file filter is set to show `.qdpx` files or "All Files".

### Step 4: Review Import Preview (if shown)

Qualcoder may show a preview of what will be imported:

- **Codes**: List of codes that will be applied
- **Files**: Files that will have new coded segments
- **Selections**: Number of coded segments to import

**Review carefully**:
- ✅ All codes are recognized (green checkmark or listed)
- ✅ All files are recognized
- ⚠️ Any warnings or conflicts are noted

### Step 5: Confirm Import

1. Review the preview information
2. Click **Import** or **OK** to proceed
3. Wait for the import to complete

**Progress indicator**: Qualcoder may show:
- Progress bar for large imports
- Status messages
- Number of items imported

### Step 6: Verify Import Success

Look for confirmation:
- **Success message**: "Import completed successfully" or similar
- **Status bar**: Shows number of items imported
- **No error dialog**: If import failed, you'll see an error message

If you see an error, see [Troubleshooting](#troubleshooting) section.

## What Happens During Import

### Behind the Scenes

When you import a REFI-QDA file, Qualcoder:

1. **Extracts** the `.qdpx` ZIP archive
2. **Parses** the `project.qde` XML file inside
3. **Validates** that codes and files exist in your project
4. **Creates** new `code_text` entries in the database for each coded segment
5. **Sets** the owner to "AI Coding Assistant" (or the user specified in the export)
6. **Records** the import timestamp
7. **Preserves** AI memos as memo text for each coded segment

### Database Changes

The import **adds rows** to these Qualcoder database tables:

- `code_text`: One row per coded segment (file_id, code_id, start_pos, end_pos, owner, date, memo)
- Possibly `source` memo fields if file descriptions were included

The import **does not**:
- Modify existing coding
- Delete anything
- Change code definitions
- Alter file content

### User Attribution

All imported coding is attributed to the user specified in the REFI-QDA export:
- Default: "AI Coding Assistant"
- Visible in Qualcoder as the "Coder" or "Owner" of each segment
- You can filter by coder to see only AI-coded segments

## After Import

### Step 1: Verify Import in Qualcoder

Check that coding appears correctly:

#### View by File

1. Click on a file that was coded
2. Click **Codes** button or go to **Codes** view
3. You should see new coded segments
4. Each segment should show:
   - The code name
   - The coded text
   - "AI Coding Assistant" as the coder
   - A memo with AI reasoning and confidence score

#### View by Code

1. Go to **Codes** view
2. Click on a code that was applied
3. View all segments for that code
4. Look for segments attributed to "AI Coding Assistant"

#### Check Coding Matrix

1. Go to **Reports > Coding Matrix** (or similar)
2. Verify counts increased for the coded files and codes
3. Filter by coder to see only AI coding

### Step 2: Review AI Memos

For each coded segment:

1. Click the segment in Qualcoder
2. View the memo (look for memo icon or panel)
3. Read the AI's explanation
4. Check the confidence score (shown as [AI confidence: 0.XX])

**Memo format**:
```
Clear expression of workplace stress related to deadlines. [AI confidence: 0.85]
```

### Step 3: Review and Refine

Now that coding is imported, review it:

1. **Spot Check**: Review a sample of AI-coded segments
2. **Verify Boundaries**: Check that start/end positions make sense
3. **Confirm Codes**: Ensure code assignments are appropriate
4. **Read Context**: View segments in context of full file
5. **Modify if Needed**: Edit, delete, or recoded as necessary

### Step 4: Clean Up

Optional housekeeping:

1. **Delete the .qdpx file** if you don't need it anymore
2. **Update project memo** to note AI coding was used
3. **Document** which files/codes were AI-coded and when
4. **Back up** your project with the new coding

## Troubleshooting

### Import Failed: "Code not found"

**Error**: Import fails with message about code not being found.

**Cause**: The `.qdpx` file references a code that doesn't exist in your Qualcoder project.

**Solutions**:

1. **Create the missing code** in Qualcoder, then retry import
2. **Edit the export**: Export again, ensuring only existing codes are included
3. **Check code names**: Make sure code names match exactly (case-sensitive)

### Import Failed: "File not found"

**Error**: Import fails with message about source file not found.

**Cause**: The `.qdpx` file references a file that doesn't exist in your Qualcoder project.

**Solutions**:

1. **Check file IDs**: Make sure you exported for files that exist
2. **Don't delete files**: Files coded in AI session must still exist in project
3. **Re-export**: Export again with correct file IDs

### Import Failed: "Invalid REFI-QDA file"

**Error**: Qualcoder rejects the file as invalid or corrupted.

**Causes**:
- File is corrupted during download/transfer
- File isn't actually a `.qdpx` file
- XML inside is malformed

**Solutions**:

1. **Re-export** the REFI-QDA file from the MCP
2. **Check file integrity**: Try unzipping the `.qdpx` manually (it's a ZIP file)
3. **Verify contents**: There should be a `project.qde` XML file inside
4. **Update Qualcoder**: Ensure you have a recent version that supports REFI-QDA

### Import Succeeds But No Coding Appears

**Symptom**: Import completes without errors, but you don't see new coded segments.

**Causes**:
- All suggestions were rejected before export
- Coding was imported but for different files than you're viewing
- Filter settings in Qualcoder are hiding the coding

**Solutions**:

1. **Check export included suggestions**: Re-run export and verify it contains approved suggestions
2. **View correct files**: Make sure you're looking at the files that were coded
3. **Check filters**: Remove any coder or code filters in Qualcoder
4. **View by coder**: Filter to show only "AI Coding Assistant" coding

### Segments in Wrong Position

**Symptom**: Coded segments appear at wrong location in the text.

**Causes**:
- File was edited after AI coding but before import
- Character position calculation error
- File encoding issues (rare)

**Solutions**:

1. **Don't edit files** between AI coding and import
2. **Re-export with current file state**: Re-run AI coding if file changed
3. **Manually adjust**: Edit segment boundaries in Qualcoder after import
4. **Check file encoding**: Ensure file is UTF-8 encoded

### Duplicate Coding

**Symptom**: Some segments are coded twice with same code.

**Causes**:
- Imported the same `.qdpx` file twice
- AI suggested a segment you already coded manually

**Solutions**:

1. **Check import history**: Did you import this file before?
2. **Don't import twice**: Each `.qdpx` should be imported once
3. **Remove duplicates**: Use Qualcoder to delete duplicate segments
4. **Filter before export**: Exclude already-coded segments

### Qualcoder Crashes During Import

**Symptom**: Qualcoder freezes or crashes when importing.

**Causes**:
- Very large import (thousands of segments)
- Qualcoder bug
- System resource limitations

**Solutions**:

1. **Import in batches**: Export smaller sets of suggestions
2. **Update Qualcoder**: Get the latest version
3. **Check system resources**: Close other applications
4. **Report bug**: File an issue with Qualcoder if reproducible

### Can't Find Import Menu Item

**Symptom**: No "Import > REFI-QDA" option in Qualcoder menus.

**Causes**:
- Qualcoder version is too old
- No project is open
- Feature disabled in your Qualcoder build

**Solutions**:

1. **Open a project first**: Import is only available when a project is open
2. **Update Qualcoder**: Get version 3.0+ which supports REFI-QDA
3. **Check documentation**: Confirm your Qualcoder version supports REFI-QDA import

## Advanced Topics

### Importing Multiple Sessions

You can import multiple AI coding sessions into the same project:

1. Export each session separately
2. Import them one at a time
3. Each import adds to existing coding (doesn't replace)

**Tip**: Use descriptive file names like `ai_coding_batch1.qdpx`, `ai_coding_batch2.qdpx`.

### Selective Import

To import only specific suggestions:

1. Use `update_suggestion_status` to reject unwanted suggestions
2. Export only approved suggestions (default behavior)
3. Alternatively, export as JSON, filter manually, import into new `.qdpx`

### Testing Imports

Before importing into your real project:

1. Make a copy of your project
2. Import into the copy first
3. Review results
4. If satisfied, import into real project

```bash
cp ~/Documents/QualCoder_projects/MyProject/MyProject.qda ~/Documents/QualCoder_projects/MyProject_test/MyProject_test.qda
```

### Batch Processing

For coding many files:

1. **Strategy A - One big session**: Code all files, review, export once
2. **Strategy B - Multiple sessions**: Code in batches, export each, import separately
3. **Strategy C - Iterative**: Code some, import, refine approach, code more

Recommended: Start with Strategy A for consistency, use B for very large projects.

### Understanding REFI-QDA Structure

If you're curious about the file format:

1. Rename `.qdpx` to `.zip`
2. Extract the archive
3. Open `project.qde` in a text editor (it's XML)

You'll see:
- `<Users>` section with coder information
- `<CodeBook>` section with codes used
- `<Sources>` section with files and coded segments
- `<PlainTextSelection>` elements for each coded segment
- `<Coding>` elements linking selections to codes

### Manual Editing (Advanced)

You can manually edit the XML before import:

1. Extract the `.qdpx` file
2. Edit `project.qde` XML
3. Re-zip as `.qdpx`
4. Import the modified file

**Use cases**:
- Bulk editing AI memos
- Adjusting segment boundaries
- Changing code assignments
- Adding custom metadata

**Warning**: XML must remain valid REFI-QDA format.

### Exporting from Qualcoder

You can also export from Qualcoder as REFI-QDA:

1. File > Export > REFI-QDA
2. Creates a `.qdpx` of your project
3. Can be imported into other QDA software
4. Or reimported into a different Qualcoder project

This is useful for:
- Sharing coded data
- Moving between projects
- Backing up coding

## Tips for Successful Imports

1. ✅ **Always backup** before importing
2. ✅ **Test with small sample** first
3. ✅ **Review before import** using session statistics
4. ✅ **Check you're in the right project** before importing
5. ✅ **Document your imports** (which session, when, what files)
6. ✅ **Verify after import** by spot-checking several segments
7. ✅ **Keep export files** until you confirm import success
8. ✅ **Import promptly** after export to avoid project changes

## Common Workflow

Typical workflow:

1. **AI codes** files in MCP session
2. **Review** session statistics and samples
3. **Refine** suggestions (approve/reject)
4. **Export** approved suggestions to Desktop
5. **Backup** Qualcoder project
6. **Import** `.qdpx` file into Qualcoder
7. **Verify** coding appears correctly
8. **Review** sample of AI-coded segments
9. **Refine** coding manually in Qualcoder as needed
10. **Document** that AI coding was used
11. **Delete** `.qdpx` file and MCP session if no longer needed

## Support

If you encounter issues:

1. **Check this guide** troubleshooting section
2. **Review [AI_CODING_GUIDE.md](AI_CODING_GUIDE.md)** for workflow issues
3. **Consult [Qualcoder docs](https://github.com/ccbogel/QualCoder/wiki)** for import problems
4. **Test with a small export** to isolate the issue
5. **Check Qualcoder version** supports REFI-QDA import
6. **Open an issue** on GitHub if problem persists

## Quick Reference

### Import Steps Summary

1. File > Import > REFI-QDA Project
2. Select `.qdpx` file
3. Review preview
4. Confirm import
5. Verify coding appears

### Verification Checklist

- [ ] Coding appears in files
- [ ] Segments have correct code assignments
- [ ] AI memos are visible
- [ ] Coder is "AI Coding Assistant"
- [ ] Segment boundaries make sense
- [ ] Counts match expected numbers

### Common File Locations

- Exports: `~/Desktop/*.qdpx` (or wherever you specified)
- Qualcoder projects: `~/Documents/QualCoder_projects/`
- MCP sessions: `~/.qualcoder_mcp/sessions/`

---

**Version**: v0.3.0
**Last Updated**: October 2025
**For**: Qualcoder 3.0+
**License**: MIT
