# AI-Assisted Coding Guide

Guide to coding qualitative data with Claude through the QualCoder MCP
server (v0.5.0 conversational workflow).

> **This guide replaces the v0.3.0 export/import guide.** The old
> `suggest_coding_for_files` / `export_coding_suggestions` /
> `suggest_new_codes` / `export_new_codes_for_import` tools were removed
> in v0.4.0. Codings are now written directly to the project database
> after your explicit approval — no REFI import step is needed. (A
> REFI-QDA *export* tool, `export_refi_qda`, exists for interchange with
> other QDA software.) For the step-by-step walkthrough with example
> conversations, see [AI_CODING_WORKFLOW.md](AI_CODING_WORKFLOW.md).

## How It Works

1. **You** ask Claude to analyze files for specific codes
   (`analyze_for_coding` creates a session)
2. **Claude** reads the files and records its suggestions into the
   session (`record_suggestions`) — every suggestion is verified against
   the file text before it is stored; positions are corrected
   automatically when the excerpt is unique in the file
3. **You** review the suggestions in the chat (`review_suggestions`)
   and approve or reject them (`update_suggestion_status`)
4. **Claude** writes only the approved suggestions to the database
   (`apply_codings`) — after re-validating each one, creating a backup,
   and confirming QualCoder does not have the project open
5. **You** open the project in QualCoder and see the codings

Nothing is written without your approval, every write is backed up
first, and mistakes can be undone (`delete_coding` for one coding,
`restore_backup` for a whole snapshot).

## Prerequisites

- The MCP server configured in Claude Desktop (see [INSTALL.md](INSTALL.md))
- A QualCoder project — work on a copy in the workspace:
  say "Copy my project 'Interview Study' to the workspace"
  (`copy_project_to_workspace`), then open the copy with `select_project`
- **QualCoder closed** for the project you are writing to — writes are
  refused while QualCoder's `project_in_use.lock` heartbeat is fresh
- Codes already defined in the project (the AI applies existing codes;
  creating or reorganizing codes still happens in QualCoder)
- Schema v14 (QualCoder 3.8.x). Older projects: open and save them in
  QualCoder 3.8 once to upgrade

## Quick Start

```
You:    Copy "Interview Study" to the workspace and open the copy.
You:    Analyze files 1-3 for the codes "Workplace Stress" and
        "Coping Strategies".
Claude: (creates a session, reads the files, records suggestions,
         presents them with reasoning and confidence scores)
You:    Show me suggestion 3 with context.
You:    Approve 1, 2 and 5; reject the rest.
You:    Apply the approved codings.
Claude: (backs up, writes, reports the new coding IDs)
```

Ask "Explain the AI coding tools" any time — the built-in
`explain_ai_coding_tools` help covers every step.

## Tool Reference (current)

| Tool | Purpose |
|---|---|
| `analyze_for_coding(file_ids, code_names, instruction, min_confidence)` | Create an analysis session |
| `record_suggestions(session_id, suggestions, replace)` | Persist Claude's suggestions (text-verified) |
| `review_suggestions(session_id, suggestion_guids, show_context)` | Inspect suggestions in detail |
| `update_suggestion_status(session_id, approve, reject)` | Approve/reject by GUID |
| `apply_codings(session_id, create_backup, owner)` | **Write** approved suggestions (project-bound, validated, all-or-nothing) |
| `delete_coding(coding_id, create_backup)` | **Write** — remove one coded segment |
| `list_backups()` / `restore_backup(backup_path, confirm)` | List snapshots / guarded project restore |
| `import_text_file(filename, content, memo, owner, create_backup, case_name)` | **Write** — add a new transcript, optionally linked to a case |
| `link_file_to_case(file_id, case_id, case_name, create_backup)` | **Write** — make a file visible to case-based analyses |
| `export_refi_qda(output_path, session_id, overwrite)` | Export codings as a REFI-QDA .qdpx for other QDA software |
| `get_coding_session_info` / `list_coding_sessions` / `delete_coding_session` / `cleanup_old_sessions` | Session management |
| `copy_project_to_workspace(source_path, new_name)` | Copy a project into the safe workspace |

## Best Practices

### Before You Start

1. **Define Your Codes**: Have a clear codebook before AI coding
2. **Test on Small Sample**: Start with 1-2 files to understand results
3. **Set Clear Instructions**: Be specific about what you're looking for
   ("segments where participants describe feeling overwhelmed, not just
   mentions of the word stress")
4. **Know Your Data**: Familiarize yourself with the files being coded

### During AI Coding

1. **Use Descriptive Instructions**: Tell Claude what patterns to look for,
   with examples of what each code covers
2. **Set Appropriate Confidence**: Lower (0.5–0.6) for exploratory passes,
   higher (0.8+) for selective coding
3. **Review Statistics First**: Check counts before diving into details
4. **Iterate if Needed**: `record_suggestions(replace=true)` discards the
   pending suggestions from a previous pass

### Reviewing Suggestions

1. **Check Confidence Distribution**: Are most suggestions high or low?
2. **Review Low Confidence First**: These need the most scrutiny
3. **Spot Check High Confidence**: Verify the AI reasoning is sound
4. **Use Context**: `show_context=true` shows the surrounding text
5. **Check Boundary Precision**: The recorded positions always match the
   file text exactly — but check the *span* is what you want coded

## Safety Model

- Reads are the default; every write tool re-opens the database
  read-only afterwards
- Writes require schema v14 (QualCoder 3.8) — older projects get an
  "open and save in QualCoder to upgrade" message
- Writes are refused while QualCoder has the project open, and the
  server holds the project lock itself during its own writes
- Every write creates a timestamped backup first (`list_backups` shows
  them, including QualCoder's own `_BKUP_` snapshots)
- Sessions only apply to the project they were created in
- Applied suggestions are marked and cannot be double-applied

## FAQ

**Do I need an API key?** No — Claude itself does the analysis through
the conversation; the server only stores and applies what you approve.

**Can the AI create new codes?** Not yet — it applies your existing
codebook. Codebook management is planned co-design work with early
testers.

**What happens to my original project?** Nothing, if you follow the
workspace workflow: copy first, work on the copy, and compare in
QualCoder before adopting changes.

**Where are sessions stored?** `~/.qualcoder_mcp/sessions/` as JSON, one
file per session. `cleanup_old_sessions(days_old)` prunes old ones.
