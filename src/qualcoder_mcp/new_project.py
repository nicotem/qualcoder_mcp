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
import sqlite3
from pathlib import Path
from typing import List, Optional, Sequence, Tuple, Union

from .database import VERIFIED_MASTER_COMMIT

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
