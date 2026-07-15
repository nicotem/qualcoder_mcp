# AI Coding Workflow Guide

Complete guide to using Claude for AI-assisted qualitative coding with the conversational approval workflow (v0.4.0+).

## Table of Contents

1. [Overview](#overview)
2. [Safety First](#safety-first)
3. [Workflow Steps](#workflow-steps)
4. [Example Conversations](#example-conversations)
5. [Tips and Best Practices](#tips-and-best-practices)
6. [Troubleshooting](#troubleshooting)

## Overview

The AI coding workflow in v0.4.0+ uses a **conversational approval process** where:

1. Claude analyzes your files and creates suggestions
2. You review suggestions in the chat conversation
3. You explicitly approve or reject specific suggestions
4. Claude writes only approved suggestions directly to the database
5. Automatic backups protect your data

This approach gives you **full control** through natural conversation with Claude, with no import/export steps required.

## Safety First

⚠️ **CRITICAL: Always work on copies, never on original projects!**

### Workspace Setup

The AI coding system uses a dedicated workspace folder:

```
~/Documents/Qualcoder MCP Projects/
```

**Before ANY AI coding:**
1. Copy your project to the workspace
2. Work only on the workspace copy
3. Verify results in Qualcoder before replacing original

### Close QualCoder First

QualCoder marks an open project with a `project_in_use.lock` heartbeat
file, and this server respects it: **every write operation is refused
while QualCoder has the project open** ("This project is open in
QualCoder — close the project in QualCoder, then retry"). Reads still
work, with a warning that data may change underneath.

### Automatic Backups

Every write operation creates a timestamped backup:

```
your_project_backup_20251029_143045.qda
```

Use `list_backups` to see them (QualCoder's own `_BKUP_` snapshots are
listed too) and `restore_backup` to roll the project back — it previews
first, requires explicit confirmation, and saves a safety backup of the
current state so even a restore can be undone. A single wrong coding can
be removed with `delete_coding(ctid)` instead.

## Workflow Steps

### Step 1: Copy Project to Workspace

**First time setup:**

Ask Claude to copy your project:
```
Copy my project "Interview Study.qda" to the workspace for AI coding
```

Claude will:
- Copy the entire project folder to `~/Documents/Qualcoder MCP Projects/`
- Create unique name if one already exists
- Report the workspace path

**What you'll see:**
```
✓ Copied project to workspace:
  /Users/YOUR_NAME/Documents/Qualcoder MCP Projects/Interview Study.qda

  This is now your working copy for AI coding.
  Your original project is untouched.
```

### Step 2: Create Analysis Session

Ask Claude to analyze specific files with specific codes:

```
Analyze files 1, 2, and 3 for the codes "Workplace Stress" and "Coping Strategies"
```

Or more detailed:
```
Analyze interview file 5 and code any segments related to:
- Motivation
- Barriers to participation
- Positive outcomes

Use a minimum confidence threshold of 0.7
```

**What Claude does:**
1. Creates a new analysis session with unique ID (`analyze_for_coding`)
2. Reads the specified files
3. Examines content for relevant segments
4. Identifies text that matches the codes
5. Assigns confidence scores (0.0-1.0)
6. Generates reasoning for each suggestion
7. Records the suggestions into the session with `record_suggestions` —
   every suggestion is verified against the file text before it is
   stored (positions are corrected automatically when the excerpt is
   unique in the file; mismatches are rejected with an explanation)

**What you'll see:**
```
I've analyzed files 1-3 for Workplace Stress and Coping Strategies.

Session ID: abc123-def456-789...

Found 8 suggestions:

1. File: interview_001.txt
   Code: Workplace Stress
   Position: 450-620
   Text: "I often feel overwhelmed with the workload..."
   Reasoning: Direct expression of feeling overwhelmed by work demands
   Confidence: 0.92
   GUID: guid-001

2. File: interview_001.txt
   Code: Coping Strategies
   Position: 1200-1350
   Text: "I try to take breaks and go for walks..."
   Reasoning: Describes specific coping mechanism (taking breaks)
   Confidence: 0.85
   GUID: guid-002

[... more suggestions ...]
```

### Step 3: Review Suggestions

You can ask for more details about any suggestion:

```
Show me more details about suggestion 3
```

Or see specific suggestions:
```
Show me details for suggestions 1, 3, and 5 with surrounding context
```

**What you'll see:**
```
Suggestion 3:

File: interview_002.txt (ID: 2)
Code: Workplace Stress (ID: 15)
Position: 892-1045

Selected Text:
"The constant interruptions make it impossible to focus on
anything. I can barely get through my daily tasks without
feeling exhausted by the end of the day."

Reasoning:
Clear indicators of workplace stress including interruptions,
inability to focus, and exhaustion - matches multiple stress
criteria.

Confidence: 0.88

Context Before:
"... my manager expects immediate responses to everything.
There's no time to think deeply about any problem."

Context After:
"Sometimes I wonder if this job is worth the toll it takes
on my mental health..."

GUID: guid-003
Status: pending
```

### Step 4: Approve or Reject Suggestions

Based on your review, tell Claude which to keep:

**Simple approval:**
```
Approve suggestions 1, 2, 3, and 5
```

**Approve some, reject others:**
```
Approve 1, 2, 4, 6, 7, and 8. Reject 3 and 5.
```

**Using GUIDs (more precise):**
```
Approve guid-001, guid-002, guid-004
Reject guid-003, guid-005
```

**What you'll see:**
```
✓ Updated suggestion statuses:
  - Approved: 6 suggestions
  - Rejected: 2 suggestions
  - Pending: 0 suggestions

Session saved. Ready to apply approved codings when you're ready.
```

### Step 5: Apply Approved Codings

When you're satisfied with the approvals:

```
Apply the approved codings to the project
```

**What Claude does:**
1. Creates automatic backup first
2. Loops through all approved suggestions
3. Writes each as a coding to the database
4. Includes reasoning and confidence in memo
5. Records owner as "AI Coding Assistant"
6. Reports success with coding IDs (ctids)

**What you'll see:**
```
Creating backup first...
✓ Backup created: Interview_Study_backup_20251029_143045.qda

Applying 6 approved codings...

✓ Applied coding ctid=554
  File: interview_001.txt
  Code: Workplace Stress
  Position: 450-620

✓ Applied coding ctid=555
  File: interview_001.txt
  Code: Coping Strategies
  Position: 1200-1350

[... more codings ...]

✓ Successfully applied 6 codings to database
  Backup: Interview_Study_backup_20251029_143045.qda

  You can now open this project in Qualcoder to see the results!
```

### Step 6: Verify in Qualcoder

1. Open Qualcoder
2. Open the workspace project: `~/Documents/Qualcoder MCP Projects/Interview Study.qda`
3. Go to **Coding > Code Text**
4. Select the files you analyzed
5. You should see the AI-generated codings with:
   - Owner: "AI Coding Assistant"
   - Memo containing reasoning and confidence score

## Example Conversations

### Example 1: Simple Analysis

**You:**
```
Copy "Focus Group Study.qda" to the workspace
```

**Claude:**
```
✓ Copied project to:
  ~/Documents/Qualcoder MCP Projects/Focus Group Study.qda
```

**You:**
```
Analyze file 3 for "Participant Motivation" and "Barriers" codes
```

**Claude:**
```
I've analyzed file 3 (focus_group_transcript.txt).

Session ID: 7a8b9c...

Found 5 suggestions:
1. Participant Motivation at 120-280 (confidence: 0.89) - guid-01
2. Barriers at 450-590 (confidence: 0.92) - guid-02
3. Participant Motivation at 780-920 (confidence: 0.76) - guid-03
4. Barriers at 1100-1250 (confidence: 0.88) - guid-04
5. Participant Motivation at 1500-1680 (confidence: 0.71) - guid-05
```

**You:**
```
Show me details for suggestion 2
```

**Claude:**
```
Suggestion 2:

File: focus_group_transcript.txt
Code: Barriers
Position: 450-590

Text: "The main issue is lack of time. Between work and family
responsibilities, there's just no time left for participation..."

Reasoning: Explicit mention of time constraints as a barrier to
participation, citing specific competing demands (work, family).

Confidence: 0.92
GUID: guid-02
```

**You:**
```
Approve all except 5 (confidence too low)
```

**Claude:**
```
✓ Approved: 4 suggestions (1, 2, 3, 4)
✓ Rejected: 1 suggestion (5)
```

**You:**
```
Apply the codings
```

**Claude:**
```
✓ Backup created: Focus_Group_Study_backup_20251029_143512.qda
✓ Applied 4 codings successfully (ctid 112-115)

Open the project in Qualcoder to see your AI-generated codings!
```

### Example 2: Large File with Review

**You:**
```
Copy "Interview Series.qda" to workspace and analyze file 7 with these codes:
- Work-Life Balance
- Career Satisfaction
- Professional Development

Minimum confidence 0.75
```

**Claude:**
```
✓ Project copied to workspace
Analyzing file 7...

Session ID: d4e5f6...

Found 12 suggestions across 3 codes:
- Work-Life Balance: 5 suggestions
- Career Satisfaction: 4 suggestions
- Professional Development: 3 suggestions
```

**You:**
```
Show me all the Work-Life Balance suggestions with context
```

**Claude:**
```
[Shows detailed view of all 5 Work-Life Balance suggestions
with full text, reasoning, confidence, and surrounding context]
```

**You:**
```
The first 4 look good. Show me Professional Development suggestions.
```

**Claude:**
```
[Shows 3 Professional Development suggestions]
```

**You:**
```
Approve:
- All 5 Work-Life Balance suggestions
- Work-Life Balance 1-4
- All 4 Career Satisfaction
- Professional Development 1 and 3 only
```

**Claude:**
```
✓ Approved: 11 suggestions
✓ Rejected: 1 suggestion (Professional Development #2)
```

**You:**
```
Why did I want to reject PD #2? Show me that one again.
```

**Claude:**
```
[Shows suggestion details]
```

**You:**
```
Actually that looks fine. Approve it too.
```

**Claude:**
```
✓ Approved Professional Development #2
All 12 suggestions now approved
```

**You:**
```
Apply them
```

**Claude:**
```
✓ Backup created
✓ Applied 12 codings successfully (ctid 445-456)
```

## Tips and Best Practices

### Getting Good Results

**1. Be Specific with Instructions**

❌ Bad:
```
Code this file
```

✅ Good:
```
Analyze file 5 for segments related to:
- Workplace Stress (look for expressions of feeling overwhelmed,
  time pressure, or negative emotional responses to work demands)
- Coping Strategies (identify any mention of how participants
  deal with or manage stress)

Use confidence threshold 0.75
```

**2. Start with Small Batches**

Don't analyze 20 files at once on your first try. Start with:
- 1-3 files
- 2-4 codes
- Review the results
- Adjust your approach
- Scale up gradually

**3. Review Before Applying**

Always review at least a few suggestions before approving:
```
Show me details for suggestions 1, 5, and 10
```

Check:
- Is the text relevant?
- Is the code appropriate?
- Does the reasoning make sense?
- Is the confidence score justified?

**4. Use the Session System**

You don't have to complete everything at once:

```
# Day 1
Analyze files 1-5 for Motivation codes

# Later, same day or next day
Load session abc123 and show me the suggestions
```

Claude remembers:
- All suggestions
- Your approvals/rejections
- Session details
- Ready to apply when you are

**5. Adjust Confidence Thresholds**

- **0.6-0.7**: More suggestions, may include borderline cases
- **0.7-0.8**: Balanced (recommended starting point)
- **0.8-0.9**: High confidence only, fewer but more accurate
- **0.9+**: Very strict, only obvious matches

**6. Work Iteratively**

1. Do a small test run
2. Check results in Qualcoder
3. Adjust your instructions based on what you see
4. Continue with more files

### Managing Sessions

**List recent sessions:**
```
Show me my recent coding sessions
```

**Load a session:**
```
Load session abc123 and show me what we did
```

**Delete old sessions:**
```
Delete sessions older than 30 days
```

### Backup Management

Backups are created automatically, but you can also:

**Create manual backup:**
```
Create a backup of the current workspace project
```

**Find backups:**
Backups are in the same folder as your workspace projects:
```
~/Documents/Qualcoder MCP Projects/ProjectName_backup_TIMESTAMP.qda
```

**Restore from backup:**
```
Show me the backups for this project        (list_backups)
Restore the project from <backup name>      (restore_backup — previews
                                             first, then confirm=true)
```
The restore keeps a safety backup of the pre-restore state, so it can
itself be undone.

## Troubleshooting

### "No approved suggestions to apply — N already applied"

**Problem:** Re-running apply_codings on a session that was already
written. Applied suggestions are marked and never double-applied.

**Solutions:**
- This is the expected protection; nothing to fix
- To code more segments, record new suggestions or start a new session

### "Coding already exists at this position"

**Problem:** A coding with the same code/file/span/owner already exists
in the database (e.g. created in QualCoder).

**Solutions:**
- Reject that suggestion and apply the others
- Check the existing coding with get_coded_segments

### "File ID X does not exist"

**Problem:** File was deleted or project structure changed.

**Solution:**
- List available files: `Show me all files in the project`
- Use correct file IDs for current project

### Suggestions seem off-target

**Problem:** AI is coding irrelevant segments or missing good ones.

**Solutions:**
1. **Be more specific:**
   ```
   Look specifically for segments where participants describe
   feeling stressed, not just mentioning the word "stress"
   ```

2. **Adjust confidence threshold:**
   ```
   Use minimum confidence 0.8 (stricter)
   ```

3. **Give examples:**
   ```
   Code for Workplace Stress, which includes things like:
   - Feeling overwhelmed
   - Time pressure
   - Conflict with colleagues
   - Unrealistic expectations
   ```

### Can't find workspace project in Qualcoder

**Problem:** Looking in wrong location.

**Solution:**
The workspace is:
```
/Users/YOUR_NAME/Documents/Qualcoder MCP Projects/
```

In Qualcoder:
- File > Open Project
- Navigate to "Qualcoder MCP Projects" folder
- Select the `.qda` folder

### Claude doesn't see new changes in Qualcoder

**Problem:** Made changes in Qualcoder GUI, Claude doesn't see them.

**Solution:**
Claude caches project data. If you modify in Qualcoder:
```
Reload the current project to see my latest changes
```

### Want to undo applied codings

**Problem:** Applied codings but want to revert.

**Solution:**
- One wrong coding: `Delete coding 42` (`delete_coding` — the ctid is in
  the apply output and in get_coded_segments)
- Whole batch: `restore_backup` with the backup created by the apply
  (see `list_backups`); it previews first and keeps a safety backup

Or manually in Qualcoder:
- Open project
- Find codings by owner "AI Coding Assistant"
- Delete unwanted codings

### Session file corrupted or lost

**Problem:** Can't load a session.

**Solution:**
Session files are stored at:
```
~/.qualcoder_mcp/sessions/session_ID.json
```

- Check if file exists
- Create new session if needed - previous work in database is safe

## Advanced Usage

### Batch Processing Multiple Files

Process many files in batches:

```
# Batch 1
Analyze files 1-5 for Motivation codes

# Review and apply

# Batch 2
Analyze files 6-10 for Motivation codes

# Review and apply
```

### Using Multiple Code Sets

Analyze same files with different codes:

```
# Pass 1: Emotions
Analyze files 1-3 for:
- Positive Emotions
- Negative Emotions
- Ambivalent Feelings

# Apply

# Pass 2: Behaviors
Analyze files 1-3 for:
- Coping Behaviors
- Avoidance Behaviors
- Help-Seeking Behaviors

# Apply
```

### Refining Over Time

Iterate to improve:

```
# First pass with low threshold
Analyze file 5 with min confidence 0.6

# Review to see what's borderline

# Second pass with tuned instructions
Analyze file 5 again but only code segments that explicitly
mention [specific criteria], confidence 0.75
```

### Quality Control

Check your AI coding quality:

```
In Qualcoder:
1. Open Code Text view
2. Filter by owner "AI Coding Assistant"
3. Review random sample
4. Compare with your manual coding
5. Adjust approach as needed
```

## Summary Checklist

Before starting AI coding:
- [ ] Original project safely backed up
- [ ] Project copied to workspace
- [ ] Know which files and codes to use
- [ ] Clear instructions prepared

During coding:
- [ ] Review at least some suggestions before approving
- [ ] Check confidence scores make sense
- [ ] Reasoning aligns with your coding scheme
- [ ] Approve/reject thoughtfully

After applying:
- [ ] Backup was created (check path)
- [ ] Open project in Qualcoder
- [ ] Verify codings look correct
- [ ] Owner shows "AI Coding Assistant"
- [ ] Memos contain reasoning and confidence

---

**Remember:** The AI is a coding assistant, not a replacement for your expertise. Always review and verify the suggestions match your research objectives and coding scheme.
