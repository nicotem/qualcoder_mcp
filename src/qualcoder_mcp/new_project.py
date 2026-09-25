# SPDX-License-Identifier: LGPL-3.0-or-later
"""An empty QualCoder project in QualCoder 4.0's format (v0.14).

What `create_project` writes: the project folder with its four
subfolders, and a `data.qda` holding the tables, views and first rows
that QualCoder 4.0's own New Project writes (schema v17), built from the
column table below in ONE explicit transaction, so that a crash leaves
no committed table.

The column table is this project's own restatement of the file format
(NOTICE lists it among the facts of QualCoder's file format). It is not
QualCoder's statement text: QualCoder never reads the stored text of a
table (it looks tables up by name and reads `PRAGMA table_info`), so
what must match is the order of the tables, each column's name, type,
key, NOT NULL and default, the unique groups, the views' behaviour and
the first rows. `tests/test_v014_create_project_format.py` compares all
of them with a project QualCoder 4.0 created, from a committed fixture.

The format followed is the newest downloadable QualCoder 4.0 (the
owner's ruling of 2026-09-25): the "4.0-Beta" pre-release of 2026-09-03
writes exactly what the verified commit writes, so both are named by
one constant, `database.VERIFIED_MASTER_COMMIT`. It is re-checked at
each of this server's releases while 4.0 is in beta.
"""

import datetime
import os
import sqlite3
import unicodedata
from pathlib import Path
from typing import List, Optional, Sequence, Tuple, Union

from .database import (
    MAX_FILE_NAME_BYTES,
    VERIFIED_MASTER_COMMIT,
    _sqlite_ro_uri,
    documents_name_key,
    file_name_rule,
)

# The schema label a new project carries, and the build that writes it.
SCHEMA_VERSION = "v17"
FORMAT_COMMIT = VERIFIED_MASTER_COMMIT

# The project folder's contents, in the order QualCoder makes them.
DATABASE_FILE = "data.qda"
SUBFOLDERS = ("images", "audio", "video", "documents")

# QualCoder's speaker coder, which every new project lists among its
# coder names (a system coder, never a person).
SPEAKER_CODER_NAME = "\U0001F4CC Speaker coding"

# The date QualCoder stores: local time, offset applied, to the second.
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_INT, _TEXT, _REAL = "integer", "text", "real"
_KEY = True

# (table, ((column, type[, primary key]), ...), (unique group, ...)), in
# the order QualCoder 4.0 creates them. `project`, `stored_sql` and `ris`
# have no primary key; `attribute_type`'s key is its text name; every
# other table's first column is an integer key. `linewidth` is the only
# real column. No table has a foreign key.
TABLES: Tuple[Tuple[str, Tuple[tuple, ...], Tuple[Tuple[str, ...], ...]],
              ...] = (
    ("project", (
        ("databaseversion", _TEXT), ("date", _TEXT), ("memo", _TEXT),
        ("about", _TEXT), ("bookmarkfile", _INT), ("bookmarkpos", _INT),
        ("codername", _TEXT), ("recently_used_codes", _TEXT),
        ("avbookmarkfile", _INT), ("avbookmarkmsec", _INT),
        ("avbookmarktextpos", _INT)), ()),
    ("source", (
        ("id", _INT, _KEY), ("name", _TEXT), ("fulltext", _TEXT),
        ("mediapath", _TEXT), ("memo", _TEXT), ("owner", _TEXT),
        ("date", _TEXT), ("av_text_id", _INT), ("risid", _INT)),
     (("name",),)),
    ("code_image", (
        ("imid", _INT, _KEY), ("id", _INT), ("x1", _INT), ("y1", _INT),
        ("width", _INT), ("height", _INT), ("cid", _INT), ("memo", _TEXT),
        ("date", _TEXT), ("owner", _TEXT), ("important", _INT),
        ("pdf_page", _INT)), ()),
    ("code_av", (
        ("avid", _INT, _KEY), ("id", _INT), ("pos0", _INT), ("pos1", _INT),
        ("cid", _INT), ("memo", _TEXT), ("date", _TEXT), ("owner", _TEXT),
        ("important", _INT)), ()),
    ("annotation", (
        ("anid", _INT, _KEY), ("fid", _INT), ("pos0", _INT), ("pos1", _INT),
        ("memo", _TEXT), ("owner", _TEXT), ("date", _TEXT)),
     (("fid", "pos0", "pos1", "owner"),)),
    ("attribute_type", (
        ("name", _TEXT, _KEY), ("date", _TEXT), ("owner", _TEXT),
        ("memo", _TEXT), ("caseOrFile", _TEXT), ("valuetype", _TEXT)), ()),
    ("attribute", (
        ("attrid", _INT, _KEY), ("name", _TEXT), ("attr_type", _TEXT),
        ("value", _TEXT), ("id", _INT), ("date", _TEXT), ("owner", _TEXT)),
     (("name", "attr_type", "id"),)),
    ("case_text", (
        ("id", _INT, _KEY), ("caseid", _INT), ("fid", _INT), ("pos0", _INT),
        ("pos1", _INT), ("owner", _TEXT), ("date", _TEXT), ("memo", _TEXT)),
     ()),
    ("cases", (
        ("caseid", _INT, _KEY), ("name", _TEXT), ("memo", _TEXT),
        ("owner", _TEXT), ("date", _TEXT)), (("name",),)),
    ("code_cat", (
        ("catid", _INT, _KEY), ("name", _TEXT), ("owner", _TEXT),
        ("date", _TEXT), ("memo", _TEXT), ("supercatid", _INT)),
     (("name",),)),
    ("code_text", (
        ("ctid", _INT, _KEY), ("cid", _INT), ("fid", _INT),
        ("seltext", _TEXT), ("pos0", _INT), ("pos1", _INT), ("owner", _TEXT),
        ("date", _TEXT), ("memo", _TEXT), ("avid", _INT),
        ("important", _INT)),
     (("cid", "fid", "pos0", "pos1", "owner"),)),
    # supercid: the parent code of a sub-code (4.0's sub-codes)
    ("code_name", (
        ("cid", _INT, _KEY), ("name", _TEXT), ("memo", _TEXT),
        ("catid", _INT), ("owner", _TEXT), ("date", _TEXT), ("color", _TEXT),
        ("supercid", _INT)), (("name",),)),
    ("journal", (
        ("jid", _INT, _KEY), ("name", _TEXT), ("jentry", _TEXT),
        ("date", _TEXT), ("owner", _TEXT)), (("name",),)),
    ("stored_sql", (
        ("title", _TEXT), ("description", _TEXT), ("grouper", _TEXT),
        ("ssql", _TEXT)), (("title",),)),
    ("graph", (
        ("grid", _INT, _KEY), ("name", _TEXT), ("description", _TEXT),
        ("date", _TEXT), ("scene_width", _INT), ("scene_height", _INT)),
     (("name",),)),
    ("gr_cdct_text_item", (
        ("gtextid", _INT, _KEY), ("grid", _INT), ("x", _INT), ("y", _INT),
        ("supercatid", _INT), ("catid", _INT), ("cid", _INT),
        ("font_size", _INT), ("bold", _INT), ("isvisible", _INT),
        ("displaytext", _TEXT)), ()),
    ("gr_case_text_item", (
        ("gcaseid", _INT, _KEY), ("grid", _INT), ("x", _INT), ("y", _INT),
        ("caseid", _INT), ("font_size", _INT), ("bold", _INT),
        ("color", _TEXT), ("displaytext", _TEXT)), ()),
    ("gr_file_text_item", (
        ("gfileid", _INT, _KEY), ("grid", _INT), ("x", _INT), ("y", _INT),
        ("fid", _INT), ("font_size", _INT), ("bold", _INT), ("color", _TEXT),
        ("displaytext", _TEXT)), ()),
    ("gr_free_text_item", (
        ("gfreeid", _INT, _KEY), ("grid", _INT), ("freetextid", _INT),
        ("x", _INT), ("y", _INT), ("free_text", _TEXT), ("font_size", _INT),
        ("bold", _INT), ("color", _TEXT), ("tooltip", _TEXT), ("ctid", _INT),
        ("memo_ctid", _INT), ("memo_imid", _INT), ("memo_avid", _INT)), ()),
    # label and arrow_mode: 4.0's relation labels and arrows
    ("gr_cdct_line_item", (
        ("glineid", _INT, _KEY), ("grid", _INT), ("fromcatid", _INT),
        ("fromcid", _INT), ("tocatid", _INT), ("tocid", _INT),
        ("color", _TEXT), ("linewidth", _REAL), ("linetype", _TEXT),
        ("isvisible", _INT), ("label", _TEXT), ("arrow_mode", _TEXT)), ()),
    ("gr_free_line_item", (
        ("gflineid", _INT, _KEY), ("grid", _INT))
     + tuple((f"{end}{item}", _INT) for end in ("from", "to")
             for item in ("freetextid", "catid", "cid", "caseid", "fileid",
                          "imid", "avid"))
     + (("color", _TEXT), ("linewidth", _REAL), ("linetype", _TEXT),
        ("label", _TEXT), ("arrow_mode", _TEXT)), ()),
    # 4.0's memo nodes on graphs
    ("gr_memo_item", (
        ("gmemoid", _INT, _KEY), ("grid", _INT), ("memo_source_type", _TEXT),
        ("memo_source_id", _INT), ("x", _INT), ("y", _INT), ("color", _TEXT),
        ("font_size", _INT)), ()),
    ("gr_pix_item", (
        ("grpixid", _INT, _KEY), ("grid", _INT), ("imid", _INT), ("x", _INT),
        ("y", _INT), ("px", _INT), ("py", _INT), ("w", _INT), ("h", _INT),
        ("filepath", _TEXT), ("tooltip", _TEXT), ("pdf_page", _INT)), ()),
    ("gr_av_item", (
        ("gr_avid", _INT, _KEY), ("grid", _INT), ("avid", _INT), ("x", _INT),
        ("y", _INT), ("pos0", _INT), ("pos1", _INT), ("filepath", _TEXT),
        ("tooltip", _TEXT), ("color", _TEXT)), ()),
    ("ris", (
        ("risid", _INT), ("tag", _TEXT), ("longtag", _TEXT),
        ("value", _TEXT)), ()),
    ("manage_files_display", (
        ("mfid", _INT, _KEY), ("name", _TEXT), ("tblrows", _TEXT),
        ("tblcolumns", _TEXT), ("owner", _TEXT)), ()),
    ("files_filter", (
        ("filterid", _INT, _KEY), ("name", _TEXT), ("filter", _TEXT),
        ("owner", _TEXT)), ()),
)

# The coder names table: the one table with NOT NULL, a default and a
# CHECK. QualCoder 4.0 creates it after the others, fills it, then
# creates the four views that hide a coder whose visibility is 0.
CODER_NAMES_TABLE = (
    "CREATE TABLE coder_names (name TEXT UNIQUE NOT NULL, "
    "visibility INTEGER NOT NULL DEFAULT 1 CHECK (visibility IN (0, 1)))")
VISIBILITY_VIEW_TABLES = ("code_image", "code_text", "code_av", "annotation")

# The project row's columns, named (QualCoder inserts by position; the
# stored row is the same).
PROJECT_COLUMNS = (
    "databaseversion", "date", "memo", "about", "bookmarkfile",
    "bookmarkpos", "codername", "recently_used_codes", "avbookmarkfile",
    "avbookmarkmsec", "avbookmarktextpos")

Statement = Tuple[str, Tuple]


def about_line(version: str) -> str:
    """The project row's `about`: this server and its version, and the
    word QualCoder's open check looks for ("QualCoder", with that
    capitalisation: both builds refuse a project whose `about` lacks
    it). QualCoder shows it and tests it, and reads nothing else in it."""
    return f"qualcoder-mcp {version} (QualCoder schema {SCHEMA_VERSION})"


def creation_date(now: Optional[datetime.datetime] = None) -> str:
    """The date QualCoder writes into a new project row."""
    now = now or datetime.datetime.now()
    return now.astimezone().strftime(DATE_FORMAT)


def _column_text(column: tuple) -> str:
    name, kind = column[0], column[1]
    return f"{name} {kind}" + (" primary key" if len(column) > 2 else "")


def table_statement(table: str, columns: Sequence[tuple],
                    uniques: Sequence[Sequence[str]]) -> str:
    """One CREATE TABLE statement from the column table."""
    parts = [_column_text(column) for column in columns]
    parts += [f"unique({', '.join(group)})" for group in uniques]
    return f"CREATE TABLE {table} ({', '.join(parts)})"


def view_statement(table: str) -> str:
    """A coder-visibility view: the table's rows, less a hidden coder's."""
    return (f"CREATE VIEW {table}_visible AS SELECT t.* FROM {table} t "
            f"WHERE NOT EXISTS (SELECT 1 FROM coder_names c "
            f"WHERE c.name = t.owner AND c.visibility = 0)")


def creation_statements(coder_name: str, about: str,
                        date: str) -> List[Statement]:
    """Every statement of a new project's database, in QualCoder 4.0's
    order: the 27 tables, `coder_names`, its rows (the researcher's coder
    name when known, then the speaker coder), the four views and the
    project row. `coder_name` is '' when the researcher's name is not
    known: the row is then left out and the project row stores ''."""
    if not isinstance(coder_name, str):
        raise TypeError("coder_name must be a string ('' when not known)")
    statements: List[Statement] = [
        (table_statement(table, columns, uniques), ())
        for table, columns, uniques in TABLES]
    statements.append((CODER_NAMES_TABLE, ()))
    if coder_name:
        statements.append((
            "INSERT INTO coder_names (name, visibility) VALUES (?, 1)",
            (coder_name,)))
    statements.append((
        "INSERT OR IGNORE INTO coder_names (name) VALUES (?)",
        (SPEAKER_CODER_NAME,)))
    statements.extend((view_statement(table), ())
                      for table in VISIBILITY_VIEW_TABLES)
    statements.append((
        f"INSERT INTO project ({', '.join(PROJECT_COLUMNS)}) "
        f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)",
        (SCHEMA_VERSION, date, "", about, 0, 0, coder_name, "")))
    return statements


def _connect(path: Path) -> sqlite3.Connection:
    """A connection with no implicit transactions: the caller's BEGIN
    and COMMIT are the only ones (Python's default mode would commit
    each CREATE TABLE on its own)."""
    return sqlite3.connect(str(path), isolation_level=None)


def build_database(path: Union[str, Path],
                   statements: Sequence[Statement]) -> None:
    """Write `statements` into a new database at `path` in one explicit
    transaction. A failure rolls back and re-raises; the connection is
    closed either way, so the caller can remove the file afterwards (on
    Windows an open file cannot be removed). Nothing reaches the file
    before COMMIT: a new database's pages stay in memory until then, so a
    process killed part way leaves an empty file and a journal."""
    conn = _connect(Path(path))
    try:
        conn.execute("BEGIN")
        for sql, args in statements:
            conn.execute(sql, args)
        conn.execute("COMMIT")
    except BaseException:
        try:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


# The files SQLite may make beside the database while it is written.
_DATABASE_SIDE_FILES = ("data.qda-journal", "data.qda-wal", "data.qda-shm")


class ProjectWriteFailed(Exception):
    """Writing a new project failed after its folder was claimed.

    `stage` is "subfolder" or "database"; `cause` is the error that
    stopped it; `left` names what could not be removed afterwards
    (empty when everything this call made is gone). The caller words
    the answer; the cause's own message is never shown, because an
    operating system or SQLite message can carry a path.
    """

    def __init__(self, stage: str, cause: BaseException, left: List[str]):
        super().__init__(f"project write failed at the {stage} stage")
        self.stage = stage
        self.cause = cause
        self.left = left


def remove_what_was_made(folder: Path, subfolders: Sequence[str],
                         database_started: bool) -> List[str]:
    """Remove what one creation made, by name and never recursively.

    The database, its side files and the named subfolders, then the
    folder itself, each only if it is empty by then. Anything another
    program put into the new folder meanwhile stays, and so does the
    folder that holds it. Returns what could not be removed (names
    relative to `folder`, the folder itself as ".").
    """
    left: List[str] = []
    if database_started:
        for name in _DATABASE_SIDE_FILES + (DATABASE_FILE,):
            try:
                (folder / name).unlink()
            except FileNotFoundError:
                pass
            except OSError:
                left.append(name)
    for name in reversed(list(subfolders)):
        try:
            (folder / name).rmdir()
        except FileNotFoundError:
            pass
        except OSError:
            left.append(name)
    try:
        folder.rmdir()
    except FileNotFoundError:
        pass
    except OSError:
        left.append(".")
    return left


def write_project(folder: Union[str, Path],
                  statements: Sequence[Statement]) -> Path:
    """Make a new project at `folder`: claim it, make the four
    subfolders, and build its database in one transaction.

    The claim is an atomic `mkdir`: a folder, file or link of that name
    raises FileExistsError and nothing is touched. After the claim, any
    failure removes what this call made (`remove_what_was_made`) and
    raises ProjectWriteFailed. Returns the path of `data.qda`.
    """
    folder = Path(folder)
    folder.mkdir()
    made: List[str] = []
    stage, database_started = "subfolder", False
    try:
        for name in SUBFOLDERS:
            (folder / name).mkdir()
            made.append(name)
        stage, database_started = "database", True
        build_database(folder / DATABASE_FILE, statements)
    except BaseException as error:
        left = remove_what_was_made(folder, made, database_started)
        if not isinstance(error, Exception):
            raise
        raise ProjectWriteFailed(stage, error, left) from None
    return folder / DATABASE_FILE


# ---------------------------------------------------------------------------
# The project's name (v0.13's file-name rule, worded for a project, and
# the project-only refusals). The folder is "<name>.qda".
# ---------------------------------------------------------------------------

PROJECT_SUFFIX = ".qda"
# This server's backups are "<project>_backup_<time>[...].qda" and
# QualCoder's "<project>_BKUP_<date>[<operation>].qda": a name holding
# either is hidden by this server's project list, and a folder named
# like another project's "_BKUP_" backup is deleted by QualCoder 4.0
# beyond the newest five when that project closes.
BACKUP_MARKERS = ("_backup_", "_BKUP_")

_PROJECT_NAME_TEXT = {
    "empty": "A project name must not be empty, spaces only or dots only.",
    "surrogate": ("A project name must not contain an unpaired surrogate "
                  "character."),
    "path": ("A project name must not contain '/', '\\', '..' or ':': it "
             "names one folder. To create the project somewhere else, "
             "name that folder in `directory`."),
    "windows_characters": (
        "A project name must not contain < > ? * or \" (Windows cannot "
        "store them in a folder name, and a project may be moved to "
        "Windows)."),
    "windows_ending": (
        "A project name must not end with a dot or a space (Windows drops "
        "them, so there the folder would have another name)."),
    "windows_device": (
        "A project name must not be a Windows device name (CON, PRN, AUX, "
        "NUL, CONIN$, CONOUT$, COM0 to COM9, LPT0 to LPT9, or COM or LPT "
        "followed by a superscript 1, 2 or 3): Windows cannot store such "
        "a folder."),
}

PIPE_REFUSAL = (
    "A project name must not contain '|': QualCoder creates such a project "
    "but can never open it, because it reads the text after a '|' as the "
    "project's path.")


def normalise_project_name(raw: str) -> str:
    """The name as it will be used: stripped at both ends, NFC, and one
    trailing '.qda' (any letter case) removed, since a researcher who
    types "Study.qda" means the folder "Study.qda" (QualCoder would make
    "Study.qda.qda")."""
    name = unicodedata.normalize("NFC", raw.strip())
    if name.lower().endswith(PROJECT_SUFFIX):
        name = name[:-len(PROJECT_SUFFIX)]
    return name


def project_name_problem(name: str) -> Optional[str]:
    """Why `name` (already normalised) may not name a project, or None.

    v0.13's file-name rule first (`file_name_rule`: the same rules as a
    file name, in the same order, worded for a project folder), then the
    project-only refusals: a backup marker, a leading dot, and a name
    still ending in '.qda'.
    """
    broken = file_name_rule(name)
    if broken is not None:
        rule, detail = broken
        if rule == "invisible":
            return f"A project name must not contain {detail}."
        if rule == "too_long":
            return (f"A project name must be at most {MAX_FILE_NAME_BYTES} "
                    f"bytes in UTF-8 (this one has {detail}): the limit "
                    f"leaves room for the backup names QualCoder and this "
                    f"server add beside a project.")
        if rule == "windows_characters" and "|" in name:
            return PIPE_REFUSAL
        return _PROJECT_NAME_TEXT[rule]
    for marker in BACKUP_MARKERS:
        if marker in name:
            return (f"A project name must not contain '{marker}': this "
                    f"server's project list hides such folders as backups, "
                    f"and QualCoder 4.0 deletes a folder named like "
                    f"another project's '_BKUP_' backup, beyond the newest "
                    f"five, when it closes that project.")
    if name.startswith("."):
        return ("A project name must not start with a dot: the folder "
                "would be hidden.")
    if name.lower().endswith(PROJECT_SUFFIX):
        return ("A project name must not end in '.qda' twice; give the name "
                "without the ending.")
    return None


# ---------------------------------------------------------------------------
# Where the project goes: the folder guard (new code, not the export path
# resolver, which is made for files and needs a project selected).
# ---------------------------------------------------------------------------

class Refusal(ValueError):
    """A creation refused before anything was written; the message is the
    tool's answer."""


def _inside(path: Path, folder: Path) -> bool:
    return path == folder or folder in path.parents


def check_parent_folder(parent: Path, state_home: Optional[Path],
                        is_default: bool) -> None:
    """Refuse a parent folder a project must not be created in.

    `parent` is resolved. The researcher's own folder must exist and be
    a folder (this tool creates no folder but its workspace); no parent
    may lie inside this server's state folder, inside a folder whose
    name ends in '.qda' (a project inside a project is copied into every
    backup of the outer one), or on a path containing '|' (QualCoder
    cannot open such a project).
    """
    where = "The workspace folder" if is_default else "The folder"
    if not is_default:
        if not parent.exists():
            raise Refusal(
                f"{where} '{parent}' does not exist. Name an existing "
                f"folder (this tool creates no folder but its own "
                f"workspace), or leave `directory` out to use the "
                f"workspace.")
        if not parent.is_dir():
            raise Refusal(f"'{parent}' is not a folder. Name an existing "
                          f"folder, or leave `directory` out.")
    if state_home is not None and _inside(parent, state_home):
        raise Refusal(
            f"{where} is inside this server's state folder "
            f"(~/.qualcoder_mcp), which holds its internal state; choose "
            f"another folder.")
    for part in (parent,) + tuple(parent.parents):
        if part.name.lower().endswith(PROJECT_SUFFIX):
            raise Refusal(
                f"{where} '{parent}' is inside the project folder "
                f"'{part.name}'. A project inside a project is copied into "
                f"every backup of the outer one; choose a folder outside "
                f"it.")
    if "|" in str(parent):
        raise Refusal(
            f"{where} '{parent}' has a '|' in its path. QualCoder creates a "
            f"project there but can never open it, because it reads the "
            f"text after a '|' as the path; choose another folder.")


def resolve_parent_folder(directory: Optional[str],
                          workspace: Path) -> Tuple[Path, bool]:
    """(the parent folder, resolved; whether it is the workspace)."""
    if directory is None:
        return Path(workspace).expanduser().resolve(), True
    if not isinstance(directory, str):
        raise Refusal("`directory` must be a folder path given as text, or "
                      "left out to use the workspace.")
    if not directory.strip():
        raise Refusal("`directory` is empty. Name an existing folder, or "
                      "leave `directory` out to use the workspace.")
    try:
        return Path(directory).expanduser().resolve(), False
    except (OSError, RuntimeError, ValueError):
        raise Refusal(f"'{directory}' is not a folder path this tool can "
                      f"use.") from None


# ---------------------------------------------------------------------------
# What is already there: the same name (by the strictest disk's rule), and
# older folders named like the new project's backups.
# ---------------------------------------------------------------------------

ORPHAN = "orphan"
PROJECT = "project"
UNREADABLE = "unreadable"
NOT_A_FOLDER = "not_a_folder"


def is_unfinished(path: Path) -> bool:
    """A folder whose `data.qda` is missing or empty: what a creation that
    did not finish leaves. Read from the folder listing and the file's
    size alone; the database is not opened."""
    database = path / DATABASE_FILE
    try:
        if path.is_symlink() or not path.is_dir():
            return False
        if not os.path.lexists(database):
            return True
        return database.is_file() and database.stat().st_size == 0
    except OSError:
        return False


def classify_existing(path: Path) -> str:
    """What an existing entry with the new project's name is.

    ORPHAN only for a folder whose `data.qda` is missing or empty: what a
    creation that did not finish leaves. PROJECT for a readable database
    with a project row. UNREADABLE for anything else in a folder, which
    includes a real project that another program was writing when it
    stopped (a full `data.qda` with a journal beside it): that one is not
    called an orphan. The database is opened read-only, so a stranger's
    journal is never rolled back; NOT_A_FOLDER for a file or a link.
    """
    if path.is_symlink() or not path.is_dir():
        return NOT_A_FOLDER
    if is_unfinished(path):
        return ORPHAN
    database = path / DATABASE_FILE
    if not database.is_file():
        return UNREADABLE
    conn = None
    try:
        conn = sqlite3.connect(_sqlite_ro_uri(database), uri=True)
        row = conn.execute("SELECT count(*) FROM project").fetchone()
        return PROJECT if row and row[0] else UNREADABLE
    except sqlite3.Error:
        return UNREADABLE
    finally:
        if conn is not None:
            conn.close()


def existing_name_refusal(entry: str, folder_name: str, kind: str) -> str:
    """The refusal for an entry that already holds the new folder's name."""
    if entry != folder_name:
        return (f"The folder already holds '{entry}', which is the same "
                f"name as '{folder_name}' on a disk that ignores letter "
                f"case, accent composition or a trailing dot or space "
                f"(macOS and Windows by default). Two projects under those "
                f"names would become one folder when copied to such a "
                f"disk, and one of them would be lost. Choose another "
                f"name.")
    if kind == PROJECT:
        return (f"A project called '{folder_name}' already exists there. "
                f"Select it with select_project to work on it, or choose "
                f"another name.")
    if kind == ORPHAN:
        return (f"A folder called '{folder_name}' exists there with no "
                f"usable project database in it: it looks like the remains "
                f"of a project creation that did not finish, and nothing "
                f"in it can be opened. The researcher may delete it by "
                f"hand; otherwise choose another name. This tool never "
                f"deletes an existing folder.")
    if kind == NOT_A_FOLDER:
        return (f"Something called '{folder_name}' exists there that is "
                f"not a project folder (a file or a link). Choose another "
                f"name.")
    return (f"A folder called '{folder_name}' exists there and its database "
            f"could not be read. Choose another name.")


def backup_siblings(entries: Sequence[str], stem: str) -> List[str]:
    """Entries named like the new project's backups: '<stem>_backup_...'
    or '<stem>_BKUP_...', ending in '.qda'. Compared with letter case
    folded, as a Windows disk and Python's own matching there do."""
    prefixes = tuple(f"{stem}{marker}".casefold()
                     for marker in BACKUP_MARKERS)
    return sorted(entry for entry in entries
                  if entry.casefold().startswith(prefixes)
                  and entry.casefold().endswith(PROJECT_SUFFIX))


def backup_siblings_refusal(found: Sequence[str], stem: str) -> str:
    shown = ", ".join(f"'{name}'" for name in found[:5])
    more = f" and {len(found) - 5} more" if len(found) > 5 else ""
    return (f"The folder already holds {shown}{more}, named like backups "
            f"of a project called '{stem}'. This server's backup tools "
            f"would offer them as the new project's own backups, and could "
            f"restore one in its place; QualCoder 4.0 deletes '_BKUP_' "
            f"folders of a project beyond the newest five when it closes "
            f"it. Choose another name, or move those folders first.")


def scan_parent(parent: Path, folder_name: str,
                stem: str) -> Optional[str]:
    """The refusal the folder's present contents call for, or None.

    A parent that does not exist yet (the workspace before its first
    project) holds nothing. The same name is found by
    `documents_name_key` (NFC, letter case folded, trailing dots and
    spaces dropped): the atomic `mkdir` stays the final check, but on a
    case-sensitive disk it would let 'study.qda' beside 'Study.qda'.
    """
    if not parent.exists():
        return None
    try:
        entries = [entry.name for entry in os.scandir(parent)]
    except OSError as error:
        raise Refusal(
            f"The folder '{parent}' could not be read "
            f"({type(error).__name__}); check that it exists and that "
            f"this program may read it.") from None
    key = documents_name_key(folder_name)
    same = [entry for entry in entries if documents_name_key(entry) == key]
    if same:
        entry = folder_name if folder_name in same else sorted(same)[0]
        kind = classify_existing(parent / entry) if entry == folder_name \
            else ""
        return existing_name_refusal(entry, folder_name, kind)
    siblings = backup_siblings(entries, stem)
    if siblings:
        return backup_siblings_refusal(siblings, stem)
    return None


# ---------------------------------------------------------------------------
# Path lengths. Windows, unless long paths are switched on (it leaves them
# off by default), cannot make a folder at a path of more than 247
# characters (MAX_PATH less room for a short name) or open a file at more
# than 259 (MAX_PATH less the closing null). A project is refused on
# Windows when it could not be made there; everywhere, a path that leaves
# little room for the names of imported files draws a warning, since a
# project may be moved to Windows.
# ---------------------------------------------------------------------------

WINDOWS_MAX_FOLDER_PATH = 247
WINDOWS_MAX_FILE_PATH = 259
# The room a file name inside documents/ should have (file names may be
# up to 200 bytes; 100 characters holds most real ones).
FILE_NAME_ROOM = 100
_LONGEST_SUBFOLDER = max(SUBFOLDERS, key=len)
_LONGEST_DATABASE_FILE = max(_DATABASE_SIDE_FILES + (DATABASE_FILE,),
                             key=len)


def windows_long_paths_enabled() -> bool:
    """Whether Windows has long paths switched on (False elsewhere, and
    False when the setting cannot be read)."""
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Control\FileSystem"
                            ) as key:
            value, _ = winreg.QueryValueEx(key, "LongPathsEnabled")
        return value == 1
    except (ImportError, OSError):
        return False


def path_lengths(folder: Path) -> Tuple[int, int]:
    """(the longest folder path creation makes, the longest file path)."""
    return (len(str(folder / _LONGEST_SUBFOLDER)),
            len(str(folder / _LONGEST_DATABASE_FILE)))


def windows_path_refusal(folder: Path, on_windows: bool,
                         long_paths: bool) -> Optional[str]:
    """The refusal for a project Windows could not make, or None."""
    if not on_windows or long_paths:
        return None
    longest_folder, longest_file = path_lengths(folder)
    if (longest_folder <= WINDOWS_MAX_FOLDER_PATH
            and longest_file <= WINDOWS_MAX_FILE_PATH):
        return None
    return (f"The project's paths would be too long for Windows: its "
            f"'{_LONGEST_SUBFOLDER}' folder would be {longest_folder} "
            f"characters and its database's journal {longest_file}, and "
            f"Windows cannot make a folder beyond "
            f"{WINDOWS_MAX_FOLDER_PATH} characters or open a file beyond "
            f"{WINDOWS_MAX_FILE_PATH} unless long paths are switched on. "
            f"Choose a shorter name, or a folder nearer the top of the "
            f"disk.")


def file_name_room(folder: Path) -> int:
    """How many characters a file name inside documents/ may have before
    its path passes Windows' 259."""
    return WINDOWS_MAX_FILE_PATH - len(str(folder / "documents")) - 1


def long_path_warning(folder: Path) -> Optional[str]:
    """A warning when the project's path leaves little room for the names
    of the files imported into it, or None."""
    room = file_name_room(folder)
    if room >= FILE_NAME_ROOM:
        return None
    return (f"This project's folder path is {len(str(folder))} characters "
            f"long, which leaves room for file names of only {max(room, 0)} "
            f"characters inside it before a path passes the "
            f"{WINDOWS_MAX_FILE_PATH} characters Windows allows (unless "
            f"long paths are switched on there). Importing a file with a "
            f"longer name, or opening the project on Windows, could fail. "
            f"Short names, in folders near the top of the disk, travel "
            f"better.")
