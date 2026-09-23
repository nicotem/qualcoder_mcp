# SPDX-License-Identifier: LGPL-3.0-or-later
"""The licence (v0.13): LGPL-3.0-or-later, declared, headed and shipped.

From v0.13 qualcoder-mcp is licensed under the GNU Lesser General Public
License, version 3 or any later version, which is QualCoder's own licence;
the QualCoder routines it carries are listed in NOTICE. Releases up to
0.12.1 were published under MIT. A checkout says so in three places, and
each is pinned here, because each could be undone by an ordinary edit
that nothing else in the suite would notice:

- every `.py` file under `src/`, `tests/` and `scripts/` carries the
  one-line SPDX header, after a shebang or an encoding line where the
  file has one;
- `pyproject.toml` declares the expression `LGPL-3.0-or-later` and names
  the three licence files, which is what puts them in the wheel and the
  sdist;
- `COPYING`, `COPYING.LESSER` and `NOTICE` exist, the two licence texts
  are the FSF's own, byte for byte, and the MIT `LICENSE` is gone.

A fourth pin keeps NOTICE true as the code moves: every entry in its
"Code derived from QualCoder" section says where the item is, and every
file and name it gives exists. A routine renamed, moved or rewritten
away therefore fails here until NOTICE is updated in the same change.
The other direction is pinned too: the entries and where each says its
item is are held below as a snapshot, so an entry cannot leave NOTICE
unless the snapshot is edited in the same change.
"""

import ast
import hashlib
import pathlib
import re

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10, where pytest itself needs tomli
    import tomli as tomllib

REPO = pathlib.Path(__file__).resolve().parents[1]
EXPRESSION = "LGPL-3.0-or-later"
HEADER = f"# SPDX-License-Identifier: {EXPRESSION}"
LICENCE_FILES = ("COPYING", "COPYING.LESSER", "NOTICE")

# The FSF texts as https://www.gnu.org/licenses/ serves them. The LGPL
# text is also byte-identical to QualCoder's own LICENSE.txt. Hashed with
# CRLF folded to LF, so a Windows checkout that converts line endings
# still compares the text rather than the checkout's settings.
SHA256 = {
    "COPYING":
        "3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986",
    "COPYING.LESSER":
        "e3a994d82e644b03a792a930f574002658412f62407f5fee083f2555c5f23118",
}

# PEP 263: an encoding declaration is a comment on line 1 or 2.
_ENCODING = re.compile(r"^[ \t\f]*#.*?coding[:=][ \t]*[-\w.]+")

# Third-party fixtures keep their own licence notes and are never given
# this project's header (tests/fixtures/refi_qda/README.md is one).
_THIRD_PARTY = REPO / "tests" / "fixtures"


def _python_files():
    files = []
    for top in ("src", "tests", "scripts"):
        for path in sorted((REPO / top).rglob("*.py")):
            if _THIRD_PARTY in path.parents:
                continue
            files.append(path)
    return files


def _header_line(lines):
    """The index the header belongs at: after a shebang and an encoding
    line where the file has them, otherwise the first line."""
    index = 0
    if lines and lines[0].startswith("#!"):
        index = 1
    if index < len(lines) and index < 2 and _ENCODING.match(lines[index]):
        index += 1
    return index


def _headed(lines):
    """The header is where _header_line puts it, and no shebang follows
    it: a shebang works only on line 1, so a header put above one would
    disable it."""
    index = _header_line(lines)
    return (index < len(lines) and lines[index] == HEADER
            and not any(line.startswith("#!") for line in lines[1:3]))


class TestTheSpdxHeader:

    def test_every_python_file_carries_it(self):
        missing = []
        for path in _python_files():
            lines = path.read_text(encoding="utf-8").splitlines()
            if not _headed(lines):
                missing.append(str(path.relative_to(REPO)))
        assert not missing, (
            f"{len(missing)} file(s) lack the line {HEADER!r} at the top "
            f"(after a shebang or an encoding line where there is one): "
            f"{missing}")

    def test_the_walk_sees_the_files_it_is_about(self):
        """A walk that found nothing would pass the test above, so the
        walk is checked to reach all three trees, this file included."""
        found = {path.relative_to(REPO).as_posix()
                 for path in _python_files()}
        for expected in ("src/qualcoder_mcp/__init__.py",
                         "src/qualcoder_mcp/server.py",
                         "tests/conftest.py",
                         "tests/test_licence.py",
                         "scripts/create_test_project.py"):
            assert expected in found, expected
        assert len(found) > 70, len(found)

    def test_the_position_rule(self):
        """After a shebang and an encoding line, never before them."""
        assert _header_line([HEADER, '"""Doc."""']) == 0
        assert _header_line(["#!/usr/bin/env python3", HEADER]) == 1
        assert _header_line(["# -*- coding: utf-8 -*-", HEADER]) == 1
        assert _header_line(["#!/usr/bin/env python3",
                             "# -*- coding: utf-8 -*-", HEADER]) == 2
        assert _headed(["#!/usr/bin/env python3", HEADER, '"""Doc."""'])
        assert not _headed([HEADER, "#!/usr/bin/env python3", '"""Doc."""'])
        assert not _headed(["#!/usr/bin/env python3", '"""Doc."""'])


class TestThePackageDeclaresIt:

    @staticmethod
    def _project():
        with open(REPO / "pyproject.toml", "rb") as handle:
            return tomllib.load(handle)["project"]

    def test_the_expression(self):
        assert self._project()["license"] == EXPRESSION

    def test_the_licence_files_are_named(self):
        assert sorted(self._project()["license-files"]) == \
            sorted(LICENCE_FILES)

    def test_no_licence_classifier_contradicts_it(self):
        """PEP 639: with an expression, a `License ::` classifier is an
        error, and a stale one is how QualCoder 3.8.2's own setup.py came
        to say MIT beside an LGPL licence file."""
        assert not [c for c in self._project().get("classifiers", [])
                    if c.startswith("License ::")]


class TestTheFilesShip:

    def test_the_three_files_exist(self):
        for name in LICENCE_FILES:
            path = REPO / name
            assert path.is_file(), name
            assert path.stat().st_size > 0, name

    def test_the_licence_texts_are_the_fsf_texts(self):
        for name, expected in SHA256.items():
            data = (REPO / name).read_bytes().replace(b"\r\n", b"\n")
            assert hashlib.sha256(data).hexdigest() == expected, name

    def test_notice_names_the_licence_and_the_derived_code(self):
        """NOTICE grants this project's licence before its section on
        QualCoder's code. That section names the same expression for
        QualCoder's own licence, so the expression found anywhere in the
        file would not show that the grant is there."""
        notice = (REPO / "NOTICE").read_text(encoding="utf-8")
        grant, heading, _ = notice.partition("Code derived from QualCoder")
        assert heading, "NOTICE lacks its 'Code derived from QualCoder' part"
        grant = " ".join(grant.split())
        assert "qualcoder-mcp is free software" in grant
        assert f"(SPDX: {EXPRESSION})" in grant

    def test_the_mit_licence_file_is_gone(self):
        """MIT stays in git history and in every release up to 0.12.1;
        a LICENSE file in the tree would say it still applies."""
        assert not (REPO / "LICENSE").exists()


# NOTICE's entries: a numbered line ("12. ...") opens one, and its
# "Here:" lines say where the item is, as "<path>, <name>, ... and
# <name>." or "<path>." for a whole file. A Here line may wrap; it ends
# at the next label, blank line or entry.
_ENTRY = re.compile(r"^(\d+)\. ")
_LABELS = ("Here:", "From:", "Author:", "Why:")


def _notice_entries():
    """{entry number: [(path, [names])]} from NOTICE's Here lines."""
    lines = (REPO / "NOTICE").read_text(encoding="utf-8").splitlines()
    entries, current, i = {}, None, 0
    while i < len(lines):
        opened = _ENTRY.match(lines[i])
        if opened:
            current = int(opened.group(1))
            entries[current] = []
        text = lines[i].strip()
        i += 1
        if not text.startswith("Here: "):
            continue
        text = text[len("Here: "):]
        while i < len(lines):
            follow = lines[i].strip()
            if (not follow or follow.startswith(_LABELS)
                    or _ENTRY.match(lines[i])):
                break
            text += " " + follow
            i += 1
        assert current is not None and text.endswith("."), text
        path, _, rest = text[:-1].partition(", ")
        names = [name.strip() for part in rest.split(", ")
                 for name in part.split(" and ") if name.strip()]
        entries[current].append((path, names))
    return entries


def _defined_names(path):
    """Every function, class and module or class level name in a .py
    file, by qualified name ("Class.method", "Class.NAME")."""
    names = set()

    def walk(node, prefix, in_function):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef)):
                names.add(prefix + child.name)
                walk(child, prefix + child.name + ".",
                     not isinstance(child, ast.ClassDef))
            elif isinstance(child, (ast.Assign, ast.AnnAssign)):
                if not in_function:
                    targets = (child.targets if isinstance(child, ast.Assign)
                               else [child.target])
                    names.update(prefix + t.id for t in targets
                                 if isinstance(t, ast.Name))
            elif isinstance(child, ast.stmt):
                walk(child, prefix, in_function)

    walk(ast.parse(path.read_text(encoding="utf-8")), "", False)
    return names


# NOTICE's section as it was last checked against the provenance audit
# (61 entries): one row per Here line, as (entry, path, names...). An
# entry dropped, the rest renumbered over it, or a Here line changed
# fails the test until this snapshot is edited in the same change, so
# that no item leaves NOTICE by accident.
NOTICE_ENTRIES = 61
NOTICE_HERE = (
    (1, "src/qualcoder_mcp/memo_privacy.py", "PERSONAL_NOTE_MARK",
     "_SEPARATOR_CHARS", "split_public_private_memo", "extract_ai_memo",
     "merge_public_memo"),
    (2, "src/qualcoder_mcp/coder_comparison.py", "kappa_qualcoder"),
    (3, "src/qualcoder_mcp/coder_comparison.py", "statistics"),
    (4, "src/qualcoder_mcp/coder_comparison.py", "qualcoder_report_values"),
    (5, "src/qualcoder_mcp/database.py", "snap_to_palette"),
    (6, "src/qualcoder_mcp/pseudonymise.py", "SpanMapper._parity"),
    (7, "src/qualcoder_mcp/database.py",
     "QualcoderDatabase.code_is_descendant"),
    (8, "src/qualcoder_mcp/database.py", "QualcoderDatabase.get_branch_cids"),
    (9, "src/qualcoder_mcp/database.py", "QualcoderDatabase.code_path"),
    (10, "src/qualcoder_mcp/database.py", "QualcoderDatabase.merge_codes",
     "QualcoderDatabase.merge_category", "_append_provenance_block"),
    (11, "src/qualcoder_mcp/server.py", "METHODOLOGY_VOCABULARY",
     "explain_ai_coding_tools"),
    (12, "src/qualcoder_mcp/server.py", "_pseudonymise_journal_attempt"),
    (13, "src/qualcoder_mcp/database.py", "QUALCODER_COLORS"),
    (14, "src/qualcoder_mcp/database.py", "QUALCODER_BACKUP_IGNORE_PATTERNS"),
    (15, "src/qualcoder_mcp/database.py", "HARVEST_OWNER_TABLES"),
    (16, "src/qualcoder_mcp/pseudonymise.py", "MIN_ORIGINAL_CHARS",
     "MIN_PSEUDONYM_CHARS"),
    (17, "src/qualcoder_mcp/database.py", "KNOWN_AI_ASSISTANT_OWNER"),
    (17, "src/qualcoder_mcp/server.py", "SPEAKER_SYSTEM_CODER"),
    (18, "src/qualcoder_mcp/server.py", "MAX_SEGMENT_CHARS",
     "SEGMENT_STRATEGIES", "_resolve_exclude_code_ids", "get_coded_segments",
     "prune_backups"),
    (19, "src/qualcoder_mcp/server.py", "JOURNAL_NAME_ATTEMPTS"),
    (20, "src/qualcoder_mcp/server.py", "export_coded_segments_report"),
    (21, "src/qualcoder_mcp/server.py", "export_frequencies_csv"),
    (22, "src/qualcoder_mcp/database.py",
     "QualcoderDatabase.preview_delete_code", "QualcoderDatabase.delete_code"),
    (22, "src/qualcoder_mcp/server.py", "delete_code"),
    (23, "src/qualcoder_mcp/database.py",
     "QualcoderDatabase.preview_merge_codes"),
    (24, "src/qualcoder_mcp/database.py",
     "QualcoderDatabase.RESERVED_ATTRIBUTE_NAMES"),
    (25, "src/qualcoder_mcp/server.py", "METHODS_GUIDANCE"),
    (26, "src/qualcoder_mcp/database.py", "QUALCODER_LOCK_FILENAME",
     "QUALCODER_LOCK_TIMEOUT", "hold_project_lock"),
    (27, "src/qualcoder_mcp/database.py", "VISIBILITY_VIEWS"),
    (28, "src/qualcoder_mcp/database.py", "SchemaCapabilities",
     "QualcoderDatabase._probe_capabilities",
     "QualcoderDatabase.write_support"),
    (29, "src/qualcoder_mcp/database.py", "_detect_file_type",
     "QualcoderDatabase.search_file_content"),
    (30, "src/qualcoder_mcp/database.py", "validate_qda_path",
     "QualcoderDatabase._check_version"),
    (31, "src/qualcoder_mcp/database.py", "PSEUDONYMS_JSON_NAME",
     "read_project_pseudonyms"),
    (32, "src/qualcoder_mcp/database.py", "QualcoderDatabase._hierarchy_maps",
     "QualcoderDatabase.fingerprint_rows_category",
     "QualcoderDatabase.add_code", "QualcoderDatabase.import_text_file"),
    (33, "src/qualcoder_mcp/database.py", "QualcoderDatabase.import_text_file",
     "QualcoderDatabase.add_case", "QualcoderDatabase.add_attribute_type"),
    (34, "src/qualcoder_mcp/database.py", "QualcoderDatabase.find_text_coding",
     "QualcoderDatabase.link_file_to_case",
     "QualcoderDatabase.move_code_to_category",
     "QualcoderDatabase._pseudonymise_collisions",
     "QualcoderDatabase._pseudonymise_counts"),
    (34, "src/qualcoder_mcp/pseudonymise.py", "unique_constraint_collisions"),
    (35, "src/qualcoder_mcp/database.py",
     "QualcoderDatabase._cleanup_graph_rows_for_cid",
     "QualcoderDatabase.merge_codes"),
    (36, "src/qualcoder_mcp/database.py",
     "QualcoderDatabase.get_coded_text_segments",
     "QualcoderDatabase.search_files",
     "QualcoderDatabase.list_attribute_types",
     "QualcoderDatabase._pseudonymise_rows",
     "QualcoderDatabase.add_attribute_type"),
    (37, "src/qualcoder_mcp/database.py",
     "QualcoderDatabase.PSEUDONYMISE_SIDECARS"),
    (37, "src/qualcoder_mcp/server.py", "discover_projects",
     "_collect_backups", "restore_backup"),
    (38, "src/qualcoder_mcp/refi_export.py",
     "RefiQdaExporter._add_codebook_section"),
    (39, "tests/test_v012_palette_idempotent.py", "UPSTREAM_COLORS",
     "TestPaletteParity.test_palette_identical_to_pinned_upstream"),
    (40, "tests/test_v012_palette_idempotent.py", "upstream_color_matcher"),
    (41, "tests/test_v012_palette_idempotent.py",
     "TestPaletteParity.test_named_cases"),
    (42, "tests/test_v012_pseudonymise_engine.py", "MasterEditWalk"),
    (43, "tests/test_v012_pseudonymise_engine.py", "master_boundary_spans",
     "legacy_boundary_spans",
     "TestNonChaining.test_why_that_mapping_is_refused_although_one_pass_survives_it",
     "TestNonChaining.test_what_upstream_would_have_done_with_that_mapping"),
    (44, "tests/test_v012_ai_coder_setting.py",
     "TestUpstreamParity.HARVEST_SQL"),
    (45, "tests/test_v17_support.py", "replay_master_open_repair"),
    (45, "tests/test_qa_v17_gate_core.py", "master_repair_rowcount"),
    (46, "tests/test_qa_memo_codebook_gotchas.py",
     "TestQualCoderFidelityDifferential.test_delete_code_differential",
     "TestQualCoderFidelityDifferential.test_delete_category_differential_with_grandchildren"),
    (47, "tests/test_qa_memo_codebook_gotchas.py", "_qualcoder_merge"),
    (48, "tests/test_qa_v17_gate_core.py",
     "TestSubcodeWriteDifferentials.test_merge_differential_provenance_reparent_graphs",
     "TestSubcodeWriteDifferentials.test_delete_branch_preview_refuse_and_cascade_differential"),
    (49, "tests/test_qa_v17_gate_core.py",
     "TestSubcodeWriteDifferentials.test_move_dual_pointer_differential_and_oracle",
     "TestSubcodeWriteDifferentials.test_v14_projects_hierarchy_inert"),
    (50, "tests/test_qa_memo_codebook_gotchas.py",
     "TestQualCoderFidelityDifferential.test_rename_recolor_move_differential",
     "TestQualCoderFidelityDifferential.test_move_category_differential"),
    (51, "tests/test_qc40_visibility.py", "_VIEW_DDL",
     "_VISIBILITY_COLUMN_DDL", "_BROKEN_VIEWS"),
    (52, "tests/test_v012_ai_coder_setting.py",
     "TestUpstreamParity.CODER_NAMES_DDL",
     "TestUpstreamParity.test_a_name_absent_from_coder_names_is_visible"),
    (53, "tests/test_v012_pseudonymise_tool.py", "SCHEMA", "VISIBILITY_COLUMN",
     "VISIBILITY_VIEWS"),
    (54, "tests/test_v17_support.py", "BASE_SCHEMA", "_migrate_v15",
     "_migrate_v16", "_migrate_v17"),
    (55, "tests/track6_build.py", "DDL"),
    (56, "tests/conftest.py"),
    (56, "tests/test_database_reads.py"),
    (56, "tests/test_database_writes.py"),
    (56, "tests/test_fault_injection.py"),
    (56, "tests/test_qa_toolset_gate.py"),
    (56, "tests/test_toolset_modes.py"),
    (56, "tests/test_transport.py"),
    (56, "tests/track5_helpers.py"),
    (56, "tests/test_v17_support.py"),
    (56, "scripts/create_test_project.py"),
    (57, "tests/test_v012_pseudonymise_tool.py"),
    (57, "tests/test_v012_duplicate_inserts.py"),
    (57, "tests/test_fix_wave.py"),
    (57, "tests/test_qa_round2_regressions.py"),
    (57, "tests/test_v08_attributes.py"),
    (57, "tests/test_qa_v08_d1d2_attack.py"),
    (58, "tests/test_qc40_backup_parity.py",
     "TestIgnoreSetPinned.test_qualcoder_set_is_byte_exact_with_upstream"),
    (58, "tests/test_qc40_memo_privacy.py",
     "TestMergeCategoryProvenance.test_source_memo_carried_with_parity_recipe"),
    (59, "QUALCODER_IMPORT_CAPABILITIES.md"),
    (60, "QUALCODER_IMPORT_CAPABILITIES.md"),
    (61, "RESEARCH_SUMMARY.md"),
)


class TestNoticeStaysTrue:

    def test_every_entry_says_where_the_item_is(self):
        entries = _notice_entries()
        assert sorted(entries) == list(range(1, len(entries) + 1)), \
            sorted(entries)
        assert not [n for n, where in entries.items() if not where]

    def test_every_file_and_name_it_gives_exists(self):
        stale = []
        for number, where in sorted(_notice_entries().items()):
            for path, names in where:
                target = REPO / path
                if not target.is_file():
                    stale.append(f"{number}: {path} (no such file)")
                    continue
                if names:
                    defined = _defined_names(target)
                    stale += [f"{number}: {path}, {name}" for name in names
                              if name not in defined]
        assert not stale, (
            "NOTICE names what is no longer there; update the entry in the "
            f"same change that moved, renamed or rewrote it: {stale}")

    def test_no_entry_leaves_without_an_edit_here(self):
        """The test above runs from NOTICE to the code; this one keeps
        NOTICE complete. Removing an entry, or changing where one says
        its item is, needs NOTICE_ENTRIES and NOTICE_HERE edited too."""
        entries = _notice_entries()
        rows = [(number, path, *names)
                for number, where in sorted(entries.items())
                for path, names in where]
        assert len(entries) == NOTICE_ENTRIES, (
            f"NOTICE has {len(entries)} entries, not {NOTICE_ENTRIES}")
        missing = [row for row in NOTICE_HERE if row not in rows]
        added = [row for row in rows if row not in NOTICE_HERE]
        assert not missing and not added, (
            "NOTICE's entries differ from the snapshot in "
            f"tests/test_licence.py; gone: {missing}; new: {added}")
        assert rows == list(NOTICE_HERE)

    def test_the_reader_sees_what_it_checks(self):
        """A reader that parsed nothing would pass both tests above, so
        it is checked against entries whose shape is known."""
        entries = _notice_entries()
        assert len(entries) >= 25, len(entries)
        assert entries[2] == [("src/qualcoder_mcp/coder_comparison.py",
                               ["kappa_qualcoder"])]
        assert ("src/qualcoder_mcp/database.py",
                ["QualcoderDatabase.merge_codes",
                 "QualcoderDatabase.merge_category",
                 "_append_provenance_block"]) in entries[10]
        assert sum(len(where) for where in entries.values()) > \
            len(entries)
        names = _defined_names(REPO / "src" / "qualcoder_mcp" / "database.py")
        for name in ("QUALCODER_COLORS", "snap_to_palette",
                     "QualcoderDatabase.code_path",
                     "QualcoderDatabase.RESERVED_ATTRIBUTE_NAMES"):
            assert name in names, name
