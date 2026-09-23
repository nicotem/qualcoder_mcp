# SPDX-License-Identifier: LGPL-3.0-or-later
"""The licence (v0.13): LGPL-3.0-or-later, declared, headed and shipped.

From v0.13 qualcoder-mcp is licensed under the GNU Lesser General Public
License, version 3 or any later version, which is QualCoder's own licence;
the QualCoder routines it carries are listed in NOTICE. Releases up to
0.12.1 were published under MIT. A checkout says so in three places, and
each is pinned here, because each could be undone by an ordinary edit
that nothing else in the suite would notice:

- every `.py` file under `src/` and `tests/` carries the one-line SPDX
  header, after a shebang or an encoding line where the file has one;
- `pyproject.toml` declares the expression `LGPL-3.0-or-later` and names
  the three licence files, which is what puts them in the wheel and the
  sdist;
- `COPYING`, `COPYING.LESSER` and `NOTICE` exist, the two licence texts
  are the FSF's own, byte for byte, and the MIT `LICENSE` is gone.

A fourth pin keeps NOTICE true as the code moves: every entry in its
"Code derived from QualCoder" section says where the item is, and every
file and name it gives exists. A routine renamed, moved or rewritten
away therefore fails here until NOTICE is updated in the same change.
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
    for top in ("src", "tests"):
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


class TestTheSpdxHeader:

    def test_every_python_file_carries_it(self):
        missing = []
        for path in _python_files():
            lines = path.read_text(encoding="utf-8").splitlines()
            index = _header_line(lines)
            if index >= len(lines) or lines[index] != HEADER:
                missing.append(str(path.relative_to(REPO)))
        assert not missing, (
            f"{len(missing)} file(s) lack the line {HEADER!r} at the top "
            f"(after a shebang or an encoding line where there is one): "
            f"{missing}")

    def test_the_walk_sees_the_files_it_is_about(self):
        """A walk that found nothing would pass the test above, so the
        walk is checked to reach both trees, this file included."""
        found = {path.relative_to(REPO).as_posix()
                 for path in _python_files()}
        for expected in ("src/qualcoder_mcp/__init__.py",
                         "src/qualcoder_mcp/server.py",
                         "tests/conftest.py",
                         "tests/test_licence.py"):
            assert expected in found, expected
        assert len(found) > 70, len(found)

    def test_the_position_rule(self):
        """After a shebang and an encoding line, never before them."""
        assert _header_line([HEADER, '"""Doc."""']) == 0
        assert _header_line(["#!/usr/bin/env python3", HEADER]) == 1
        assert _header_line(["# -*- coding: utf-8 -*-", HEADER]) == 1
        assert _header_line(["#!/usr/bin/env python3",
                             "# -*- coding: utf-8 -*-", HEADER]) == 2


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
        notice = (REPO / "NOTICE").read_text(encoding="utf-8")
        assert EXPRESSION in notice
        assert "Code derived from QualCoder" in notice

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
