# Changelog

All notable changes to the Qualcoder MCP Server will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.10.0-alpha] - 2026-08-25

QualCoder schema v14 through v17 support with full sub-code handling,
determined by capability probes rather than version strings, plus the
Experimental multi-host support (core toolset, LM Studio and API-key
recipes, the data-governance ladder). Ground-truthed against released
3.8.2 and the unreleased 4.0 Beta at pinned commit 7b074d2, and gated
through QA, security review, and six-platform CI.

### Added: QualCoder schema v14 through v17 support (sub-codes; capability probes)

Ground-truthed against the unreleased QualCoder development tree
(version string "QualCoder 4.0 Beta", schema v17) at pinned commit
7b074d2, alongside the released 3.8.2 (schema v14). Highlights:

- **Capability-probe gate**: write support and every version-dependent
  recipe now key on column/table existence probes (upstream's own
  technique), never on the version string. v14 through v17 write; a
  REAL pre-v14 project refuses with corrected guidance; schemas newer
  than v17 refuse unless QUALCODER_MCP_ALLOW_UNKNOWN_SCHEMA=1 is set
  (then every write result carries a warning). get_current_project and
  get_project_summary report a schema block with the probe results and
  the write-support verdict.
- **Sub-code support (v16+)**: create sub-codes
  (create_code parent_code_id), move without hierarchy loss (both
  parent pointers written together), merge with descendant-cycle
  refusal and sub-code reparenting (plus QualCoder's merge provenance
  memo and saved-graph cleanup), delete with branch preview and an
  explicit cascade=true for whole-branch deletion; listings expose
  parent_code_id/parent_code_name and a rendered path; frequencies
  attribute sub-codes to their top ancestor's category; codebook,
  coded-segments report chains and REFI-QDA export all preserve the
  nesting (REFI round-trips into QualCoder's importer).
- **Parity hardening**: whole-file case links standardized on
  pos1=len(fulltext) with a dedupe that treats both historical
  spellings as the same link; import normalizes lone CR too; backups
  ignore sqlite sidecar files; backup notes version-scoped; journal
  attribute domain and new system owner names tolerated everywhere.
- **Concurrency posture for QualCoder 4.0**: the 4.0 development
  builds removed the lock file, so an open 4.0 window cannot be
  detected. Text-anchored writes now re-verify inside the write
  transaction that the file text still matches what positions were
  validated against, rolling back with a clear error if an editor
  raced the write; docs and tool descriptions state the limitation
  plainly.
- **Nothing regresses for v14/3.8.2 users**: v14/v15 recipes are
  byte-exact to 3.8.2 (verified by differential tests); all
  hierarchy-aware behavior activates only when the project actually
  has the sub-code column.
- Deferred: pseudonymise_source moves to v0.11 (contract note only).

### Added (Experimental): multi-host support and the core toolset

- **`QUALCODER_MCP_TOOLSET` environment variable** (`core` | `full`,
  default `full`): `core` registers only the 20-tool supervised
  coding set (project open/select, summary, file search and
  read-with-coding, coded-text retrieval, frequencies, the full
  suggestion loop including edit_suggestion, create_code, set_memo,
  and the safety pair copy_project_to_workspace / delete_coding /
  list_backups). Required for local models, optional elsewhere;
  unknown values fail loudly at startup; resources and prompts are
  unaffected. Measured serialized tool JSON: full = 91,111 chars
  (about 22.8k tokens at chars/4); core = 33,006 chars (about 8.3k
  tokens). Functionally tested end to end over stdio in core mode.
- **Host-choice documentation**: README "Choosing your AI host:
  data-governance options", a four-rung governance ladder in
  PRIVACY.md (consumer plans, API key, Team/Enterprise, fully local)
  quoting official pages verbatim with URLs and pull dates, and two
  INSTALL.md recipes: "Claude Code with an Anthropic API key" and
  "LM Studio (fully local)", plus host-agnostic wording throughout.
- **Marked Experimental deliberately**: the recipes are written from
  official documentation and the server side is functionally tested,
  but end-to-end host verification is pending and no local model has
  been capability-evaluated with this server yet. The docs say so
  rather than claiming any model "works well".

## [0.9.0-alpha] - 2026-07-30

PyPI packaging (`pip install qualcoder-mcp`), a whole-codebase security
audit with three fixes, and a migration guide for existing testers —
plus a critical dependency cap (`mcp<2`, since mcp 2.0.0 removed the
FastMCP module the server is built on).

### Existing testers: how to upgrade (flag for the v0.9.0 release notes)

If you installed a pre-0.9 version via `git clone` + `pip install -e .`:
either stay on git (`git pull` + `pip install -e .` in the clone —
config unchanged, keeps working) or switch to the PyPI install in a
**fresh** venv/pipx/uv and point your client config's `command` at the
installed `qualcoder-mcp` (dropping the `args: ["-m", ...]` line). Do
NOT plain-`pip install qualcoder-mcp` into the old venv — pip reports
"Requirement already satisfied" and silently does nothing. Full
before/after steps: INSTALL.md § "Upgrading from an earlier (git)
install". Upgrading only replaces server code: QualCoder projects and
AI-coding session files are untouched, and 0.6/0.7/0.8 sessions load
on 0.9 unchanged (verified end-to-end) — there is no migration step.

### Security — whole-codebase audit follow-ups (C-1 / P-1 / hardening)

The whole-codebase security audit returned a ship-the-alpha verdict with
three confirmed findings; all three are fixed here so the first PyPI
publish includes them.

- **C-1 (medium) — write-safety `finally` discipline extended to the
  three older bespoke write tools.** `import_text_file`,
  `link_file_to_case` and `delete_coding` predated the `_perform_write`
  helper and lacked its unconditional cleanup, so a commit-time
  `sqlite3.Error` (disk-full / IO / BUSY) — caught by none of their
  handlers — could leave the global connection read-write with an open
  transaction, which a later write would reuse and silently co-commit.
  `delete_coding` and `link_file_to_case` now route through
  `_perform_write` (one write path); `import_text_file` keeps its
  bespoke error contract but gained the identical `try/finally`
  (roll back if uncommitted, then always downgrade to read-only). All
  three now behave identically to the `_perform_write`-native tools
  under the same fault. Regression tests inject the fault through each
  tool and pin the clean-state guarantee **and** the no-co-commit
  property (the actual harm).
- **P-1 (low) — export path containment hardened against a directory
  symlink.** `_resolve_export_path`'s directory branch now `.resolve()`s
  the joined candidate before the project-folder containment guard,
  mirroring the file branch. A dangling symlink named like the export
  file (whose target lies inside the project folder) is now collapsed
  and refused instead of being followed by `open()`.
- **Hardening — GitHub Actions SHA-pinned + Dependabot.** Every action
  in `ci.yml` and `publish.yml` is pinned to a full commit SHA (with the
  human-readable version in a trailing comment), and a
  `.github/dependabot.yml` (github-actions, weekly) keeps the pins
  maintained.

### Added — PyPI packaging (v0.9 headline)

- Distribution metadata completed for PyPI: PEP 639 SPDX license
  expression (`license = "MIT"` + `license-files`; the deprecated
  license classifier is intentionally omitted), Trove classifiers
  (Alpha, Science/Research, Python 3.10–3.13, OS Independent,
  Scientific/Engineering), keywords, and project URLs (Homepage,
  Repository, Issues, Changelog, Privacy).
- README links converted to absolute GitHub URLs so the PyPI project
  page (which renders the README without the repo around it) never
  shows broken SUPPORT/PRIVACY/CHANGELOG links.
- `.github/workflows/publish.yml`: build + `twine check --strict`,
  then publish via **PyPI Trusted Publishing** (OIDC, no stored
  tokens) — TestPyPI on manual dispatch (environment `testpypi`),
  real PyPI on GitHub Release published (environment `pypi`).
- Verified end to end without publishing: `python -m build` +
  `twine check` pass, and the wheel installed into a fresh
  non-editable venv runs the `qualcoder-mcp` console script over real
  stdio (handshake, 67 tools, live tool calls) against a synthetic
  project.
- Docs: `pip install qualcoder-mcp` (or pipx/uvx) documented as the
  RECOMMENDED install with git demoted to the contributor path;
  client config examples gain the `qualcoder-mcp` console-script
  form; updating via `pip install --upgrade qualcoder-mcp`.
- `build` and `twine` added to the dev extra.
- **Dependency capped: `mcp>=1.2.0,<2`.** The mcp SDK's 2.0.0 release
  (July 2026) removed `mcp.server.fastmcp`; with the previous uncapped
  bound a fresh install resolved to 2.x and the server could not even
  import (caught empirically in a throwaway venv during migration-guide
  verification — existing venvs were unaffected because they hold 1.x).
  Migrating to the 2.x API is future work; the cap keeps every new
  install on the working 1.x line.

## [0.8.0-alpha] - 2026-07-25

Inductive coding, report exports, and the write-surface completions —
implemented against QualCoder 3.8.2 source ground truth, shaped by the
first tester's feedback, and gated through independent QA and security
review plus six-platform CI. Tool surface: 48 → 67.

### Security — opt-in CSV formula sanitization (V8-1)

All four report exporters gain `sanitize_formulas` (default **False**).
CSV cells whose text starts with `=` `+` `-` `@` tab or CR are treated
as live formulas by Excel/LibreOffice/Google Sheets (CSV injection,
CWE-1236) — and quoting does not defuse them. Pass
`sanitize_formulas=true` to neutralize every such cell with the
standard `'` prefix (applied to all DB-derived text: code/category/
case/coder names, memos, and coded seltext — raw source text, the
sharpest vector). The default stays **verbatim** because these
exporters exist for byte-parity with QualCoder's own exports (which do
not escape either): default preserves parity, one word turns on
safety. Every export response discloses which mode produced the file.

### Added — report exports (v0.8 phase B, per the reporting ground-truth dossier)

Four read-only file exporters whose numbers and columns match
QualCoder's own GUI exports (the parity discipline: same rows, same
counting, stated rules, disclosed divergences). All follow the
export_refi_qda path posture, accept an existing directory (QualCoder's
default filename with `_0`, `_1` collision suffixes), refuse writing
inside the project folder, and write UTF-8 with BOM — QualCoder's own
export encoding. CSV/txt/md only in the alpha: no xlsx, no new runtime
dependency (researchers open CSV in Excel).

- **`export_codebook`** (csv/txt/md): the code tree with colours, memos
  and QualCoder-Codebook counts (text + image + A/V codings, all
  coders, orphans included).
- **`export_coded_segments_report`** (csv/txt): QualCoder's Coding
  Report. Exact CSV dialect (`File, Coder, Coded, Id, Codename,
  Coded_Memo, Category×N` — category chain immediate-parent-first,
  padded; `ctid:N` ids; every cell quoted; CRLF). Filters mirror the
  GUI: code/case selection, EXACT coder match, file list, search text,
  important-only, and the variables checkbox (`FileVar_`/`CaseVar_`
  columns). Case mode uses the CONTAINMENT rule and says so in the
  response — QualCoder itself ships a second, conflicting rule.
  Text codings only (disclosed).
- **`export_frequencies_csv`**: QualCoder's Code Frequencies numbers
  exactly — per-coder columns, recursive category roll-ups, counts over
  all three media tables with orphaned codings included — with an
  explicit divergence note versus the conversational
  get_coding_frequencies (which counts text-on-existing-files only).
- **`export_case_code_matrix_csv`**: the case × code cross-tab
  (containment rule, stated; no totals row — parity).
- `find_cooccurring_codes` now documents that its counting is NOT
  QualCoder's co-occurrence matrix (different pairing semantics).

### Docs — any MCP client (from first real tester feedback, F3)

- README, INSTALL and QUICKSTART now document running the server under
  **Claude Code** (`claude mcp add ...` / `.mcp.json`) and state that
  any MCP client works — the first real-world tester ran the whole loop
  from Claude Code in Obsidian's side panel, not Claude Desktop.

### Added — review-time span editing (v0.8, from first real tester feedback)

Our first tester's #1 friction: AI-suggested spans were too short to
stand alone as quotable extracts, and there was no way to widen one at
review time.

- **`edit_suggestion`**: adjust a PENDING suggestion's span
  (extend/shrink/move) and/or its code during review — no more
  reject-and-re-record round-trips. New spans are re-verified against
  the file text with the same machinery as record_suggestions
  (authoritative slices, unique-locate, position-safety relay);
  surrounding context is refreshed; edits that would duplicate another
  suggestion are refused. Applied suggestions are immutable;
  approved/rejected ones reflect a decision already made and carry
  per-status hints.
- **Server-computed span alternatives**: every verified span (coding
  suggestions AND proposal evidence) carries up to two ready-made,
  deterministic adjustments — "shorter" (the LONGEST sentence wholly
  inside the span, so abbreviation fragments like "Dr." can never win)
  and "longer" (the enclosing paragraph with any speaker label stripped
  so quotes start with speech; in blank-line-free speaker-turn
  transcripts the turn IS the paragraph; else ± one sentence, never
  splicing across another speaker's turn). Degeneracy and materiality
  floors mean no filler alternatives, previews are truncated and
  newline-flattened for token cost, and `length` is a code-point count.
  One call applies one: `edit_suggestion(use_alternative="shorter"|
  "longer")` — recomputed from the CURRENT fulltext at use time, so
  pre-v0.8 session files work unchanged. Boundaries handle \r\n\r\n,
  \n\n and U+2029 uniformly; no new dependencies.
- **Server-emitted affordance hints**: the first manual span edit in a
  session emits a hint teaching the shorter/longer shortcut; the third
  same-direction alternative pick emits a calibration-escalation hint
  (offer the session-level fix — e.g. "code paragraph-level spans" —
  instead of continuing per-item picks). Suggestions the researcher
  already adjusted render "(adjusted)" and get no further offers.
- **`update_proposal(example_segments=...)`**: proposal evidence spans
  are editable the same way — the replacement list is validated with
  the same position machinery.
- **Guidance recalibration** (tester findings F1b/F2):
  analyze_for_coding and record_suggestions now direct the model to
  prefer complete-thought spans (a quote that stands alone) over
  minimal phrases, to ACTIVELY consider multiple codes per segment
  (co-coding is normal qualitative practice), and to treat a researcher
  adding a second code during review as a calibration signal.
  review_suggestions shows surrounding context by default and offers
  the span alternatives as a compact one-line affordance (calibrated
  against decision fatigue). The `instruction`-parameter pattern for
  span style ("code generous spans") is documented in the docstrings
  and explain_ai_coding_tools.

### Added — attributes (v0.8 phase D2, per the cases-attributes ground-truth dossier)

- **`create_attribute_type`**: define a case, file or **journal** attribute
  (QualCoder's real domain set — `'both'` does not exist) with the
  placeholder back-fill QualCoder's GUI performs: one empty (`''`) value
  row per existing entity of the domain. Attribute names are global
  across all three domains; the `Ref_*` reference-importer names are
  reserved in **both** spellings (`Ref_Author` AND `Ref_Authors` — the
  upstream dialog reserves only the singular but its RIS importer
  creates the plural).
- **`set_attribute`** (unified for case/file/journal, per Q-D2): set or
  clear (`""`) an attribute value with byte-fidelity per domain — the
  case path refreshes owner+date on update, the file/journal paths write
  the value only, exactly like the three GUI paths — and the
  insert-if-missing dance (never assume the placeholder row exists;
  QualCoder's case-side placeholder heal is a no-op in 3.8.2).
  **Documented deviation:** a non-castable value for a numeric attribute
  is refused with an error; QualCoder's GUI silently blanks it.

### Fixed — dossier-exposed bugs in existing tools (v0.8 phase D2)

- **`query_by_attribute` numeric semantics**: `CAST('' AS REAL)` is
  `0.0` in SQLite, so every UNSET attribute (empty placeholder) matched
  numeric `gt/gte/lt/lte` comparisons as zero — unset rows are now
  excluded from numeric comparisons. `equals` on a numeric attribute now
  compares numerically (`"5"` finds a stored `"5.0"`); `equals ""`
  keeps string semantics as the way to find unset attributes.
  (QualCoder's own attribute report shares the cast-empty flaw; this is
  a deliberate, documented divergence.)
- **`link_file_to_case` overlap-aware duplicate check**: QualCoder ships
  TWO conflicting whole-file link conventions (`pos1 = len-1` from the
  case file manager, `pos1 = len` from Manage Files "Assign case" and
  survey import) and each GUI path's probe only matches its own — so
  cross-path double-links happen silently upstream. The MCP link now
  refuses when ANY existing row already covers the whole file in either
  convention.
- **`import_text_file` / `create_case` placeholder back-fill exactness**:
  both were back-filling for a hypothetical `'both'` attribute domain;
  the real domain set is `case|file|journal` and upstream drives each
  back-fill with a single-domain filter — matched exactly.
- **Annotation addenda** (dossier §7): `add_annotation` now refuses a
  same-coder OVERLAPPING annotation (the GUI never creates one — and
  overlapping rows are hazardous to QualCoder's pos0-keyed clear path),
  pointing at the existing row; read paths tolerate and normalize
  REFI-born empty/NULL-memo annotation rows.

### Added — inductive / open coding (v0.8 phase A)

Six new tools close the loop the AI coding surface was missing: until
now Claude could only APPLY codes that already existed in the codebook —
it can now propose brand-new codes it finds in the data, with the same
review-first discipline as coding suggestions.

- **`propose_codes`**: records brand-new code proposals (name,
  definition, rationale, optional colour/category, evidence spans) on
  the existing AI-coding session — **nothing touches the project
  database**. Evidence spans get the full record_suggestions treatment:
  exact-match verification, unique-locate correction, authoritative
  slices, and the position-safety relay. Proposal names that collide
  with existing codes (case-insensitively) are **flagged, not blocked**,
  so the user can decide between renaming and applying the existing
  code.
- **`review_proposals`**: detailed read-only review (definitions,
  rationales, collisions, evidence) for the approval conversation.
- **`update_proposal`** / **`merge_proposals`**: the session-only refine
  loop — rename (collision flag refreshed), recolour, recategorise
  (existing categories only), rewrite the definition, or fold two
  proposals into one (evidence deduplicated by span, source marked
  rejected). Proposals already created are immutable — the real
  codebook tools take over.
- **`update_proposal_status`**: records the USER'S approve/reject
  decisions, mirroring update_suggestion_status (created proposals are
  skipped, never reopened).
- **`create_proposed_codes`**: the single write step. Every approved
  proposal is validated against the live project BEFORE the backup and
  the write (name collisions now **block** — flag-then-block; missing
  categories refuse; evidence must still match the file text) so the
  batch lands atomically or not at all. Colours default to QualCoder's
  own palette. `apply_coded_segments=False` by default: codes only,
  so the freshly created codes flow through the normal
  record_suggestions → apply_codings review loop; opt in to write the
  evidence spans as codings (owner "AI Coding Assistant") in the same
  transaction.

### Added — write-surface completions (v0.8 phase D1)

- **Annotations** (per the QualCoder 3.8.2 ground-truth contract):
  `add_annotation` (the note IS the annotation — empty notes refused; one
  per coder per exact span, pre-checked; position-validated with the
  position-safety relay on unsafe files), `update_annotation` (note +
  date updated, owner/span immutable; **clearing the note deletes the
  annotation**, exactly as QualCoder behaves), `delete_annotation`
  (keyed by anid — never by pos0, avoiding the upstream delete-by-pos0
  bug on colliding spans).
- **`merge_category`** (preview → confirm → backup): reparents the
  source category's codes and sub-categories to the TARGET (unlike
  delete_category's orphan-to-top-level), then removes the source;
  merging into the source's own descendant is refused; codings are never
  touched. Completes the category surface.
- **`create_case`**: unique name, `''` memo convention, owner from the
  project codername, and attribute placeholder rows for existing case
  attributes — exactly the rows QualCoder's own create-case writes.
  Composes with `link_file_to_case` / `import_text_file(case_name=...)`.

### Added — backup retention (v0.8 phase C)

- `prune_backups(keep_last, older_than_days, confirm)`: prune this
  server's own backup snapshots by retention policy, with the
  preview → confirm gate. When both criteria are given a backup is
  removed only if it fails BOTH (conservative intersection); the newest
  MCP backup is always kept unless `keep_last=0` is explicit; removing
  the latest pre-restore safety snapshot is flagged. QualCoder's own
  `_BKUP_` backups are NEVER touched. Works even while QualCoder has the
  project open (the live database is never involved).
- `list_backups` entries now carry `age_days`, and its notes disclose
  that MCP backups accumulate until pruned (closes the long-standing D7
  compat item).

## [0.7.0-alpha] - 2026-07-17

Adds memo writing and full codebook editing, implemented against
QualCoder 3.8.2 source ground truth and hardened through QA, security,
and four parallel test tracks (transport, property-based, scale/media,
and fault injection). Tool surface: 36 → 48.

### Added — memo writing & codebook editing

- **Memo writing**: `set_memo(target_type, target_id, memo)` for codes,
  categories, files, codings and cases (content-only, matching
  QualCoder — never rewrites date/owner; `""` clears, never NULL) and
  `add_journal_entry(name, entry)` (name charset/uniqueness enforced).
  Fixed a pre-existing bug where coding-memo edits stamped the coding's
  `date`.
- **Codebook editing**: `create_code`, `rename_code`, `recolor_code`,
  `move_code_to_category`, `create_category`, `rename_category`,
  `move_category` (with a cycle guard QualCoder lacks).
- **Destructive codebook ops** with preview → confirm → safety-backup
  gating: `merge_codes` (lossy-by-design, matching QualCoder exactly),
  `delete_code` (bulk delete, previews the coding count QualCoder's
  dialog omits), `delete_category` (shallow reparent to top level, no
  cascade to coded data).
- All new write tools honor the QualCoder lock, refuse below schema v14,
  back up before writing, and reject over-length memo/journal content
  rather than silently truncate it. Implemented against QualCoder 3.8.2
  source ground truth.

### Changed — consolidated polish round (QA + four parallel test tracks)

- Name-based category parameters refuse ambiguous case-variant matches
  ('Theme' vs 'theme') with the candidates listed instead of silently
  picking one; journal-name validation is ASCII like QualCoder's own
  validator; code names are stripped/validated consistently.
- The MCP handshake now advertises the package version (was the mcp SDK
  version); `__version__` reads the installed package metadata.
- LLM-guidance hardening: every write tool documents the
  QualCoder-open refusal with the close → re-check → retry recipe;
  position-safety warnings are imperative (relay to the researcher) and
  re-signaled at apply time; `analyze_for_coding` returns structured
  `session_id`/`qualcoder_open`/`action_required` fields alongside the
  prose banner; `update_suggestion_status` documents the
  applied-is-immutable rule and models user-decision-centric approval;
  `select_project`'s QualCoder-open warning is imperative.
- Performance at scale: `find_cooccurring_codes` no longer O(n²) per
  densely-coded file (1.46 s → ~5 ms at 8k codings; no schema changes to
  user databases); REFI export serializes once (the pretty-print reparse
  doubled peak memory).
- Researcher-facing honesty: REFI export discloses audio/video and image
  codings it cannot carry; `analyze_file_with_coding` flags non-text
  sources instead of returning an empty text result.
- Fixed: a restore whose copy failed partway could leave a half-replaced
  live project; the recovery handler now clears the partial folder and
  restores from the safety backup.
- Fixed: the shared write helper now guarantees a rollback and read-only
  downgrade on every failure path (including commit-time database
  errors), so a failed write can never leave the connection writable or
  its changes to be silently co-committed by a later write.

## [0.6.0-alpha] - 2026-07-15

Everything since 0.4.0: the QualCoder v14 schema alignment, the
`import_text_file` tool, and the pre-release fix wave (write-path
blockers, QualCoder-3.8.2 ground-truth reconciliation, recovery tooling,
REFI-QDA revival), hardened through three QA/security review rounds.

### Added — the AI coding loop now works end-to-end

- **`record_suggestions(session_id, suggestions, replace)`** — the
  previously missing middle step of the workflow. `analyze_for_coding`
  created an empty session and instructed Claude to call a Python API no
  MCP client can reach, so no coding could ever be written through the
  advertised workflow. Suggestions are now recorded through a real tool;
  each one is validated (file exists and is a text source, code exists
  by id or case-insensitive name) and **verified against the file text**:
  positions are auto-corrected when the excerpt occurs exactly once,
  mismatches are rejected with expected/provided snippets, and the
  authoritative fulltext slice is stored so `seltext ==
  fulltext[pos0:pos1]` always holds for MCP-written rows.

### Added — error recovery (full restore tooling)

- **`delete_coding(coding_id, create_backup)`** — remove one coded
  segment (never the code or the file), backup-first.
- **`list_backups()`** — lists both backup families next to the project:
  this server's `*_backup_*` snapshots and QualCoder's own `*_BKUP_*`
  open-time backups (flagged: those may exclude A/V media).
- **`restore_backup(backup_path, confirm)`** — guarded restore: previews
  until `confirm=true`, only accepts sibling backups of the open
  project, refuses while QualCoder has the project open, creates a
  `_prerestore` safety backup first, and strips stray lock files.

### Added — other new tools

- **`copy_project_to_workspace(source_path, new_name)`** — the
  documented "work on a copy" safety step is now a real tool (it was
  listed in the README but never registered).
- **`link_file_to_case(file_id, case_id|case_name)`** and an optional
  `case_name` parameter on `import_text_file` — imported files were
  invisible to every case-based analysis because no case_text row was
  ever written. The link replicates QualCoder's own Case file manager
  row exactly (pos0=0, pos1=len(fulltext)-1, app-side duplicate check —
  the table has no unique constraint).
- **`export_refi_qda(output_path, session_id, overwrite)`** — REFI-QDA
  export revived (dead code since the v0.4.0 tool removals) and made
  actually importable: `internal://{guid}.txt` source references and
  GUID-named members per spec §8.3/8.4 (QualCoder's importer
  hard-depends on the `internal:/` scheme), unqualified Project `name`
  attribute, XML-1.0 character sanitization (control characters in
  memos/code names crashed the exporter), validate-before-export (stale
  references and empty-content files fail loudly instead of producing
  archives that crash importers), per-document GUID uniqueness, category
  hierarchy as nested `isCodable="false"` codes, real-UTC timestamps,
  documented position convention, UTF-8 without BOM. Exports are
  schema-validated against the official REFI-QDA Project.xsd in the test
  suite (vendored with provenance; xmlschema as a dev dependency) —
  QualCoder itself never validates, so this is where conformance is
  proven.

### Changed — write-path safety (QualCoder 3.8.2 ground truth)

- **Writes respect QualCoder's `project_in_use.lock` heartbeat.**
  QualCoder holds no SQLite lock while idle — its lock file is its only
  concurrency control. Every write tool now refuses with "This project
  is open in QualCoder (user X)…" while the heartbeat is fresh (≤30 s),
  holds the lock itself during its own write window, re-checks
  immediately before commit when proceeding over a stale foreign lock,
  and `select_project` warns when QualCoder has the project open.
  Backups no longer include `*.lock` files.
- **Session-start QualCoder check**: `analyze_for_coding` detects an
  open QualCoder at the START of a coding session and instructs the
  assistant to ask the user to close it before continuing
  (`qualcoder_open: true` + `action_required` in the response), instead
  of letting the whole suggest → review → approve flow run only to hit
  the write refusal at apply time. `get_current_project` now reports
  `qualcoder_open` so the state can be cheaply re-checked after the
  user confirms. The concurrency story: warn at select, ask at session
  start, refuse at write.
- **`apply_codings` is bound to its session's project** — applying a
  session while a different project is open (cross-project corruption)
  is refused; `record_suggestions` enforces the same binding.
- **Every approved suggestion is re-validated before the backup and the
  write**: file exists and is a text source (junk codings on
  image/A/V sources were accepted silently), code exists, positions in
  range, segment text matches the stored positions. Failures return a
  per-GUID list; nothing is written and no backup is created.
- **Applied suggestions are marked `applied`** — re-running
  `apply_codings` explains the batch was already applied instead of
  failing wholesale on the duplicate constraint.
- **Writes match QualCoder's value contract**: `important` stored as
  1/NULL (never 0), new-code colors drawn from QualCoder's own palette
  with strict `#RRGGBB` validation, and writes hard-require schema v14
  (older projects: "open and save in QualCoder 3.8 to upgrade").
- **Position semantics documented and enforced** (code-point offsets,
  0-based, end-exclusive — what SQLite substr, all QualCoder reports and
  its own AI pipeline use). One-way U+2029→`\n` tolerance for text
  copied from GUI-created codings; per-file `position_safe` warnings on
  texts where QualCoder's GUI diverges (its documented emoji/CRLF bug);
  `import_text_file` strips a leading BOM and normalizes CRLF so new
  files are position-safe from birth.

### Changed — robustness and correctness

- Locked databases are reported as locked (previously mislabeled
  "Invalid or corrupted SQLite database"), and a failed read-write
  upgrade no longer leaves the server with a dead connection that broke
  every subsequent call.
- Old-schema projects (pre-v14 columns) and corrupted databases are
  refused at connect/select with clear guidance instead of raw
  tracebacks; all 30+ tools return sanitized `{"error": ...}` JSON for
  anticipated failures.
- Backup names get a uniquifying suffix — two writes in the same second
  no longer abort with "File exists".
- Imports are fully validated (including NUL/control-character filenames
  that bypassed both duplicate guards, now rejected with NFC
  normalization) BEFORE the read-write upgrade and backup, so rejected
  calls no longer litter full-project backup copies.
- `validate_qda_path` accepts only what QualCoder can open: a lowercase
  `.qda` directory containing `data.qda` (bare `.qda` files and
  uppercase variants are rejected).
- Case-code analyses (`get_case_code_matrix`, `get_codes_by_case`,
  `get_cases_by_code`) use full containment, matching QualCoder's own
  report semantics (previously overlap, which over-counted).
- Orphaned codings (deleted files) are excluded from all counting tools,
  consistently with segment listings.
- Project discovery no longer double-lists projects (inner `data.qda`)
  or lists backup folders (`_backup_`/`_BKUP_`) as projects.
- `search_files`: a NULL filename no longer aborts every search; content
  search covers imported documents/PDFs (previously silently skipped)
  and reports how many textless sources were not searched.
- `query_by_attribute` gained real operators (`equals`, `contains`,
  `gt/gte/lt/lte`) — the docstring had promised substring and numeric
  queries the implementation couldn't do.
- `cleanup_old_sessions` refuses `days_old < 1` (0 silently deleted ALL
  sessions); `analyze_for_coding` clamps `min_confidence` to [0,1];
  stale docstrings corrected to actual return shapes.

### Documentation

- Support policy: new SUPPORT.md and a "Support & Feedback" README
  section — all bug reports, questions and feature requests go through
  GitHub Issues; the author's email in the package metadata/LICENSE is
  an authorship signature, not a support channel.
- Truth pass over README, AI_CODING_WORKFLOW and AI_CODING_GUIDE: only
  tools that exist are described, the workflow includes the
  `record_suggestions` step and the close-QualCoder-first rule, and the
  v0.3.0 export/import guide content is replaced (historical plan docs
  are marked as such).

### From earlier on this development line

- **Schema v14 alignment** (QualCoder 3.8.x): schema fixtures and file
  type detection aligned with QualCoder's mediapath conventions.
- **`import_text_file` tool**: create new text sources with validation,
  attribute placeholders and automatic backup.

## [0.4.0] - 2025-10-30

### Added - Enhanced File Search 🔍

This release adds powerful file search capabilities to eliminate the need for filesystem-wide searches and improve the user experience when locating files.

#### **New MCP Tool:**

**`search_files(pattern, search_filename, search_content, search_memo, case_sensitive, limit)`**
- Comprehensive file search across multiple locations
- **Filename search** (fast): Find files by name - perfect for locating interview transcripts by participant name
- **Content search** (slower): Full-text search across all file content - find specific quotes or themes
- **Memo search** (fast): Search through file annotations and memos
- Combine any or all search locations
- Smart clarification workflow guides Claude to ask users which search scope they want
- Returns rich results showing WHERE matches were found (filename, content, or memo)
- Match preview with context snippets for content matches
- Performance warnings for large content searches

#### **New Database Methods:**

**`search_file_content(query, case_sensitive, limit, context_chars)`**
- Search through full text content of all files
- Returns matches with context snippets
- Performance-aware with warnings for large projects

**`search_files(pattern, search_filename, search_content, search_memo, case_sensitive, limit, context_chars)`**
- Multi-location search with aggregated results
- Shows match locations and counts
- File type detection (text, audio, video, image, pdf)
- Context-aware previews for content matches

### Fixed

- **Dynamic project selection now works correctly** - Server no longer requires `QUALCODER_PROJECT_PATH` environment variable at startup, enabling users to select projects in conversation
- Removed requirement for hardcoded project path in config - supports both Option A (dynamic selection) and Option B (fixed project)

### Changed

- File discovery workflow now uses dedicated MCP tools instead of filesystem commands
- Improved tool descriptions to guide Claude toward correct tool usage
- Better performance awareness with warnings for resource-intensive operations

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

[0.2.0]: https://github.com/nicotem/qualcoder_mcp/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/nicotem/qualcoder_mcp/releases/tag/v0.1.0
