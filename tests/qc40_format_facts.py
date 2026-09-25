# SPDX-License-Identifier: LGPL-3.0-or-later
"""What QualCoder reads of a project's database, as comparable facts.

Shared by the format tests and by the one-off run that made the oracle
fixture `tests/fixtures/qc40_new_project.json` from a project QualCoder
4.0's own New Project created (commit 9bddf17, driven headlessly by the
create-project study's harness, coder name "alice40"):

    python tests/qc40_format_facts.py <made_by_40.qda> \
        > tests/fixtures/qc40_new_project.json

The facts are the ones QualCoder depends on (the study's check, section
D): the objects and their order; every table's and view's columns as
`PRAGMA table_info` gives them (order, name, declared type, NOT NULL,
default, key); foreign keys (none); the indexes and their columns (the
unique groups); triggers (none); the header fields that do not change
with every writer; the first rows; the subfolders; and what the four
coder-visibility views return. Not the stored statement text, which
QualCoder never reads, and not the four header fields every writer
changes (the change counter, the schema cookie, version-valid-for and
the writer's SQLite version).
"""

import json
import shutil
import sqlite3
import struct
import sys
import tempfile
from pathlib import Path

VIEW_TABLES = ("code_text", "code_image", "code_av", "annotation")
_VIEW_ROW = {
    "code_text": "INSERT INTO code_text (cid, fid, pos0, pos1, owner) "
                 "VALUES (1, 1, 0, 1, ?)",
    "code_image": "INSERT INTO code_image (id, cid, owner) VALUES (1, 1, ?)",
    "code_av": "INSERT INTO code_av (id, cid, owner) VALUES (1, 1, ?)",
    "annotation": "INSERT INTO annotation (fid, pos0, pos1, owner) "
                  "VALUES (1, 0, 1, ?)",
}


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def header_fields(db: Path) -> dict:
    """The file header's fields that describe the format, not the writer."""
    head = db.read_bytes()[:100]
    return {
        "page_size": struct.unpack(">H", head[16:18])[0],
        "write_version": head[18],       # 1: rollback journal, 2: WAL
        "read_version": head[19],
        "schema_format": struct.unpack(">I", head[44:48])[0],
        "text_encoding": struct.unpack(">I", head[56:60])[0],
        "user_version": struct.unpack(">I", head[60:64])[0],
        "auto_vacuum": struct.unpack(">I", head[52:56])[0],
        "incremental_vacuum": struct.unpack(">I", head[64:68])[0],
        "application_id": struct.unpack(">I", head[68:72])[0],
    }


def structure(folder: Path) -> dict:
    """Objects, columns, keys, indexes, triggers and header of data.qda."""
    db = Path(folder) / "data.qda"
    conn = _ro(db)
    try:
        order = [list(row) for row in conn.execute(
            "SELECT type, name, tbl_name FROM sqlite_master ORDER BY rowid")]
        columns, foreign, indexes = {}, {}, {}
        for kind, name, _ in order:
            if kind not in ("table", "view"):
                continue
            columns[name] = [list(row) for row in conn.execute(
                f"PRAGMA table_info('{name}')")]
            if kind != "table":
                continue
            foreign[name] = [list(row) for row in conn.execute(
                f"PRAGMA foreign_key_list('{name}')")]
            entries = []
            for row in conn.execute(f"PRAGMA index_list('{name}')"):
                _, index, unique, origin, partial = row
                cols = [r[2] for r in conn.execute(
                    f"PRAGMA index_info('{index}')")]
                entries.append({"index": index, "unique": unique,
                                "origin": origin, "partial": partial,
                                "columns": cols})
            indexes[name] = entries
        triggers = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'")]
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    return {"order": order, "columns": columns, "foreign_keys": foreign,
            "indexes": indexes, "triggers": triggers,
            "journal_mode": journal_mode, "header": header_fields(db)}


def first_rows(folder: Path) -> dict:
    """The rows a new project holds, and the row count of every table."""
    conn = _ro(Path(folder) / "data.qda")
    try:
        project = [list(row) for row in conn.execute(
            "SELECT * FROM project")]
        coder_names = [list(row) for row in conn.execute(
            "SELECT rowid, name, visibility FROM coder_names ORDER BY rowid")]
        tables = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "ORDER BY rowid")]
        counts = {t: conn.execute(f"SELECT count(*) FROM '{t}'").fetchone()[0]
                  for t in tables}
    finally:
        conn.close()
    return {"project": project, "coder_names": coder_names,
            "row_counts": counts}


def view_behaviour(folder: Path) -> dict:
    """What each view returns for a hidden, a visible, an unknown and a
    NULL owner, and whether the CHECK refuses a visibility of 2; on a
    copy, so the project itself is not touched."""
    scratch = Path(tempfile.mkdtemp())
    try:
        copy = scratch / "copy.qda"
        shutil.copytree(folder, copy)
        conn = sqlite3.connect(str(copy / "data.qda"))
        try:
            conn.execute("INSERT INTO coder_names (name, visibility) "
                         "VALUES ('hid', 0)")
            conn.execute("INSERT INTO coder_names (name, visibility) "
                         "VALUES ('vis', 1)")
            result = {}
            for table in VIEW_TABLES:
                for owner in ("hid", "vis", "nobody", None):
                    conn.execute(_VIEW_ROW[table], (owner,))
                result[table] = sorted(
                    str(row[0]) for row in conn.execute(
                        f"SELECT owner FROM {table}_visible"))
            try:
                conn.execute("INSERT INTO coder_names (name, visibility) "
                             "VALUES ('bad', 2)")
                result["check"] = "accepted"
            except sqlite3.IntegrityError:
                result["check"] = "refused"
            conn.rollback()
        finally:
            conn.close()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return result


def subfolders(folder: Path) -> list:
    return sorted(p.name for p in Path(folder).iterdir() if p.is_dir())


def facts(folder: Path) -> dict:
    return {"structure": structure(folder), "rows": first_rows(folder),
            "views": view_behaviour(folder),
            "subfolders": subfolders(folder)}


if __name__ == "__main__":
    oracle = Path(sys.argv[1])
    out = facts(oracle)
    out["source"] = {
        "made_by": "QualCoder 4.0's own New Project (MainWindow.new_project), "
                   "driven headlessly by the create-project study's harness",
        "commit": "9bddf17",
        "coder_name": "alice40",
        "note": "ai_data/ is written by the open that follows creation, "
                "not by creation, and is left out of the subfolders",
    }
    out["subfolders"] = [s for s in out["subfolders"] if s != "ai_data"]
    json.dump(out, sys.stdout, indent=1, ensure_ascii=False)
    sys.stdout.write("\n")
