# AI-Assisted Coding Guide

Complete guide to using AI-assisted coding features in Qualcoder MCP v0.3.0

## Table of Contents

- [Introduction](#introduction)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Detailed Workflow](#detailed-workflow)
- [Tool Reference](#tool-reference)
- [Best Practices](#best-practices)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)

## Introduction

### What is AI-Assisted Coding?

AI-assisted coding allows Claude to analyze your qualitative data and suggest coded segments automatically. Instead of manually reading through transcripts and applying codes, Claude can:

- Analyze interview transcripts, field notes, or other text documents
- Identify relevant segments that match your coding framework
- Suggest appropriate codes with confidence scores
- Generate detailed memos explaining the coding rationale

**Important**: Your original Qualcoder project remains **read-only** and is never modified. All AI suggestions are stored separately and only imported after your review and approval.

### How It Works

1. **Analysis**: Claude reads your files and codes from Qualcoder
2. **Suggestion**: Claude identifies relevant text segments and suggests codes
3. **Review**: You review suggestions with confidence scores and AI explanations
4. **Export**: Approved suggestions are exported as REFI-QDA format
5. **Import**: You import the REFI-QDA file into Qualcoder via the GUI

### Key Features

- **Native AI Analysis**: Uses Claude's conversational abilities (no API key needed)
- **Confidence Scoring**: Each suggestion includes a 0.0-1.0 confidence score
- **Session Persistence**: Resume your work anytime, sessions saved to disk
- **Read-Only Safety**: Original database never modified
- **REFI-QDA Standard**: Industry-standard format for QDA software
- **Review Workflow**: Approve/reject suggestions before import

## Prerequisites

Before using AI coding, ensure you have:

1. **Qualcoder MCP v0.3.0 or later** installed and configured
2. **Claude Desktop** with the MCP server connected
3. **A Qualcoder project** with:
   - At least one text file (interview, field notes, etc.)
   - A codebook with codes defined
4. **Basic familiarity** with:
   - Qualcoder's interface and coding process
   - Your research question and coding framework

## Quick Start

### 1. Get Help

First, ask Claude about the AI coding tools:

```
Explain the AI coding tools
```

Claude will provide an overview of all available tools.

### 2. Simple AI Coding Example

Let's code a single interview using existing codes:

```
I want to code file 1 (the first interview) using the codes "Workplace Stress" and "Coping Strategies".
Please analyze the interview and suggest coded segments where you see evidence of workplace stress
or coping strategies being discussed.
```

Claude will:
1. Read the file content
2. Read the code definitions
3. Analyze the text
4. Create suggestions with confidence scores
5. Save them in a session
6. Tell you the session ID

### 3. Review the Session

```
Show me the statistics for session [session-id]
```

Claude will display:
- Total suggestions
- Breakdown by status (pending/approved/rejected)
- Counts by file and by code
- Average confidence scores

### 4. Export for Import

```
Export the coding suggestions from session [session-id] as REFI-QDA to ~/Desktop/ai_coding.qdpx
```

Claude will create a `.qdpx` file on your Desktop.

### 5. Import into Qualcoder

See [IMPORT_INSTRUCTIONS.md](IMPORT_INSTRUCTIONS.md) for detailed import steps, but briefly:

1. Open Qualcoder
2. Go to **File > Import > REFI-QDA Project**
3. Select the `.qdpx` file
4. Review and confirm the import

Your AI-coded segments now appear in Qualcoder!

## Detailed Workflow

### Phase 1: Optional Code Discovery

If you're still developing your codebook, Claude can suggest new codes:

#### Step 1: Explore Your Data

```
Analyze files 1-3 and tell me what major themes you see that aren't captured by my existing codes.
```

Claude will read your files, understand your existing codes, and suggest new ones.

#### Step 2: Suggest Specific Codes

```
Suggest new codes for files 1-3. I'm particularly interested in themes around work-life balance
and professional development that might not be in my codebook yet.
```

Claude will respond with a JSON structure of suggested codes with descriptions.

#### Step 3: Export New Codes (Optional)

If you like the suggestions:

```
Export these new codes as a REFI-QDA codebook to ~/Desktop/new_codes.qdpx
```

You can then import this codebook into Qualcoder to add the codes to your project.

### Phase 2: AI-Assisted Coding

#### Step 1: Initiate Coding Session

Be specific about:
- Which files to code
- Which codes to apply
- Any special instructions

**Example: Specific files and codes**
```
Code files 1, 2, and 3 using the codes "Workplace Stress - Causes", "Workplace Stress - Effects",
and "Coping Strategies". Focus on explicit statements about stress, not just mentions of work.
```

**Example: All files, subset of codes**
```
Code all interview files using only codes in the "Emotions" category.
Look for direct expressions of feeling, not just behavioral descriptions.
```

**Example: With confidence threshold**
```
Code files 4-6 with codes from the "Motivation" category. Only include suggestions
where you're at least 70% confident (min_confidence=0.7).
```

#### Step 2: Claude Analyzes and Creates Session

Claude will:
1. Validate files and codes exist
2. Read file content and code definitions
3. Create a new coding session
4. Return the session ID and confirm it's ready

#### Step 3: Claude Performs Analysis

Claude will analyze each file and create suggestions. For each coded segment, Claude determines:

- **Text position**: Character start and end positions
- **Segment text**: The actual text being coded
- **Code assignment**: Which code applies
- **AI memo**: Explanation of why this segment was coded
- **Confidence score**: 0.0 (low) to 1.0 (high) confidence

#### Step 4: Review Session Statistics

```
Show me statistics for session [session-id]
```

Or:

```
Show me all suggestions in session [session-id]
```

Claude will display a summary or detailed view of all suggestions.

#### Step 5: Refine Suggestions (Optional)

You can ask Claude to:

**Review specific suggestions**
```
Show me all suggestions for file 1 in session [session-id]
```

**Filter by confidence**
```
Show me only suggestions with confidence < 0.7 in session [session-id]
```

**Reject specific suggestions**
```
In session [session-id], reject suggestions at indices 5, 7, and 12
```

#### Step 6: Export Approved Suggestions

Export as REFI-QDA format:

```
Export coding suggestions from session [session-id] as REFI-QDA to ~/Desktop/ai_coding_batch1.qdpx
```

**Export options**:

- **Format**: `refi-qda` (default), `json`, or `csv`
- **Include rejected**: By default, only approved and pending suggestions are exported
- **Output path**: Can use `~` for home directory

**Examples**:

```
Export session [session-id] as JSON to ~/Desktop/suggestions.json
```

```
Export session [session-id] as REFI-QDA including rejected suggestions to ~/Desktop/all_suggestions.qdpx
```

#### Step 7: Import into Qualcoder

See [IMPORT_INSTRUCTIONS.md](IMPORT_INSTRUCTIONS.md) for complete instructions.

### Phase 3: Session Management

#### List All Sessions

```
List all my coding sessions
```

Shows all sessions with summary statistics.

#### List Sessions for Specific Project

```
List coding sessions for the current project
```

#### List Recent Sessions Only

```
List coding sessions from the last 7 days
```

#### Get Detailed Session Info

```
Show me full details for session [session-id]
```

#### Delete a Session

```
Delete session [session-id]
```

#### Clean Up Old Sessions

```
Clean up coding sessions older than 30 days
```

## Tool Reference

### Main AI Coding Tools

#### 1. `suggest_coding_for_files`

**Purpose**: Main AI coding tool that analyzes files and suggests coded segments.

**Parameters**:
- `file_ids` (required): List of file IDs to analyze
- `code_names` (optional): List of code names to use (if omitted, uses all codes)
- `instruction` (optional): Special instructions for coding (default: "Code all relevant segments")
- `min_confidence` (optional): Minimum confidence threshold 0.0-1.0 (default: 0.6)

**Usage**:
```
Code files [1, 2, 3] using codes ["Workplace Stress", "Coping"] with instruction
"Focus on explicit statements only" and min_confidence 0.7
```

#### 2. `export_coding_suggestions`

**Purpose**: Export AI coding suggestions for review and import.

**Parameters**:
- `session_id` (required): The session ID to export
- `output_format` (optional): "refi-qda" (default), "json", or "csv"
- `output_path` (optional): Where to save (defaults to temp directory)
- `include_rejected` (optional): Include rejected suggestions (default: false)

**Usage**:
```
Export session [session-id] as REFI-QDA to ~/Desktop/coding.qdpx
```

#### 3. `update_suggestion_status`

**Purpose**: Approve or reject specific suggestions before export.

**Parameters**:
- `session_id` (required): The session ID
- `updates` (required): List of {index, status} objects

**Usage**:
```
In session [session-id], approve suggestions 0-5 and reject suggestion 6
```

#### 4. `get_coding_session_info`

**Purpose**: View all details of a coding session.

**Parameters**:
- `session_id` (required): The session ID to view

**Usage**:
```
Show me full details for session [session-id]
```

### Session Management Tools

#### 5. `list_coding_sessions`

**Purpose**: List all saved coding sessions.

**Parameters**:
- `project_path` (optional): Filter by specific project
- `days_old` (optional): Only show sessions from last N days (default: 30)

**Usage**:
```
List all coding sessions
List coding sessions from the last 7 days
```

#### 6. `delete_coding_session`

**Purpose**: Delete a saved session.

**Parameters**:
- `session_id` (required): The session ID to delete

**Usage**:
```
Delete session [session-id]
```

#### 7. `cleanup_old_sessions`

**Purpose**: Automatically clean up old sessions.

**Parameters**:
- `days_old` (optional): Delete sessions older than N days (default: 30)

**Usage**:
```
Clean up coding sessions older than 30 days
```

### Code Discovery Tools

#### 8. `suggest_new_codes`

**Purpose**: AI analyzes files and suggests new codes to add.

**Parameters**:
- `file_ids` (required): List of file IDs to analyze
- `instruction` (optional): Guidance on what codes to suggest
- `existing_codes_context` (optional): Whether to consider existing codes (default: true)

**Usage**:
```
Analyze files 1-5 and suggest new codes. I'm looking for themes around technology adoption.
```

#### 9. `export_new_codes_for_import`

**Purpose**: Export approved codes as REFI-QDA codebook.

**Parameters**:
- `codes_json` (required): JSON structure of codes to export
- `output_path` (optional): Where to save the codebook

**Usage**:
```
Export these new codes as REFI-QDA codebook to ~/Desktop/new_codes.qdpx
```

### Help System

#### 10. `explain_ai_coding_tools`

**Purpose**: Get comprehensive help for AI coding tools.

**Parameters**:
- `tool_name` (optional): Get help for a specific tool

**Usage**:
```
Explain the AI coding tools
Explain how to use suggest_coding_for_files
```

## Best Practices

### Before You Start

1. **Define Your Codes**: Have a clear codebook before AI coding
2. **Test on Small Sample**: Start with 1-2 files to understand results
3. **Set Clear Instructions**: Be specific about what you're looking for
4. **Know Your Data**: Familiarize yourself with the files being coded

### During AI Coding

1. **Use Descriptive Instructions**: Tell Claude what patterns to look for
2. **Set Appropriate Confidence**: Lower for exploratory, higher for selective
3. **Review Statistics First**: Check counts before diving into details
4. **Iterate if Needed**: Refine instructions and re-run if results aren't right

### Reviewing Suggestions

1. **Check Confidence Distribution**: Are most suggestions high or low confidence?
2. **Review Low Confidence First**: These need the most scrutiny
3. **Spot Check High Confidence**: Verify AI reasoning is sound
4. **Read AI Memos**: Understand why each segment was coded
5. **Check Boundary Precision**: Are start/end positions accurate?

### Best Results

**Good instruction examples**:
```
Focus on explicit emotional expressions, not implied feelings
Only code segments where stress is directly attributed to work, not family
Look for future-oriented statements about career goals
Include both positive and negative mentions of the topic
```

**Less effective**:
```
Code relevant stuff
Find important themes
Look for stress
```

### Managing Sessions

1. **Descriptive Names**: Use session descriptions that explain what was coded
2. **Regular Cleanup**: Delete test sessions, keep only final ones
3. **Export Promptly**: Don't wait too long between coding and import
4. **Track Session IDs**: Keep notes on which sessions contain what

## Troubleshooting

### "No suggestions to export"

**Cause**: Session has no suggestions or all were rejected.

**Solution**: Check session statistics. Re-run analysis with different parameters.

### "Code ID not found in project"

**Cause**: Code was deleted from project after suggestions were created.

**Solution**: Either restore the code in Qualcoder or update suggestions to use different code.

### "File ID not found in project"

**Cause**: File was deleted from project after suggestions were created.

**Solution**: Cannot export suggestions for deleted files. Remove them from session.

### Low confidence scores across the board

**Causes**:
- Ambiguous coding framework
- AI unsure about interpretations
- Data doesn't clearly match codes

**Solutions**:
- Provide more detailed code definitions in Qualcoder memos
- Give Claude more specific instructions
- Consider whether codes match your data

### Too many suggestions

**Causes**:
- Confidence threshold too low
- Instructions too broad
- Codes too general

**Solutions**:
- Increase `min_confidence` parameter
- Be more specific in instructions
- Use more specific codes

### Too few suggestions

**Causes**:
- Confidence threshold too high
- Instructions too narrow
- Codes don't match data

**Solutions**:
- Lower `min_confidence` parameter (try 0.5)
- Broaden instructions
- Check if codes actually apply to these files

### Import fails in Qualcoder

**Cause**: REFI-QDA format issue or Qualcoder version incompatibility.

**Solution**: See [IMPORT_INSTRUCTIONS.md](IMPORT_INSTRUCTIONS.md) troubleshooting section.

## FAQ

### Is my Qualcoder project modified?

No. The project database is opened in read-only mode. AI suggestions are stored separately in `~/.qualcoder_mcp/sessions/`. Only when you import via Qualcoder's GUI are changes made.

### Where are sessions stored?

Sessions are saved as JSON files in `~/.qualcoder_mcp/sessions/`. Each session has its own file named `session_[UUID].json`.

### Can I edit suggestions before export?

Yes, use the `update_suggestion_status` tool to approve/reject specific suggestions. You can also export as JSON, edit manually, and use the edited file.

### What's a good confidence threshold?

- **0.5-0.6**: Exploratory coding, cast a wide net
- **0.7-0.8**: Standard coding, balanced approach
- **0.9+**: Conservative, only very clear matches

Start with 0.6 and adjust based on results.

### Can I combine manual and AI coding?

Absolutely! Common workflows:
- AI codes first pass, you refine manually
- You code exemplars, AI codes rest
- AI suggests, you review and modify

### How long do sessions persist?

Sessions persist indefinitely until you delete them or run cleanup. By default, cleanup removes sessions older than 30 days.

### Can I use AI coding on large files?

Yes, but very large files (>100,000 characters) may take time to analyze. Consider splitting large files or coding in batches.

### What if AI codes something incorrectly?

Simply reject that suggestion before export, or delete it after import in Qualcoder. Review is always recommended.

### Can I reuse a session?

No, sessions are write-once. Create a new session for each coding run. However, you can export the same session multiple times.

### Does this work with codes in categories?

Yes! Claude understands your code hierarchy. You can specify codes from categories or use category names in instructions.

### What file types are supported?

Any text file in Qualcoder (`.txt`, interviews, field notes, documents). Media files (audio, video, images) are not supported for AI coding.

## Tips for Success

1. **Start Small**: Code 1-2 files first to calibrate
2. **Be Specific**: Clear instructions produce better results
3. **Review Everything**: AI is a tool, you're the researcher
4. **Iterate**: First pass might not be perfect
5. **Document**: Keep notes on session IDs and what worked
6. **Trust Your Expertise**: You know your data and research best
7. **Use AI Memos**: They explain the reasoning
8. **Check Boundaries**: Verify segment boundaries make sense
9. **Test Confidence**: Try different thresholds
10. **Keep Learning**: Each project teaches you how to work better with AI

## Next Steps

- Review [IMPORT_INSTRUCTIONS.md](IMPORT_INSTRUCTIONS.md) for detailed import steps
- Try the Quick Start example with your data
- Experiment with confidence thresholds
- Develop your instruction-writing style
- Share feedback on what works well

## Support

For issues or questions:
- Check this guide and the troubleshooting section
- Review the main [README.md](README.md)
- Check [INSTALL.md](INSTALL.md) for configuration issues
- Consult [Qualcoder documentation](https://github.com/ccbogel/QualCoder/wiki)
- Open an issue on GitHub

---

**Version**: v0.3.0
**Last Updated**: October 2025
**License**: MIT
