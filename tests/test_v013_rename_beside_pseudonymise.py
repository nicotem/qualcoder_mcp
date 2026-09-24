"""v0.13: rename_case and rename_file beside the pseudonymisation tool
(RENAME_TOOLS_DOSSIER.md 2.6 and 4.3), on the flagship's own fixture.

A rename is safe between a preview and its execute: the approval token
signs ids, never names. After a rename the residue count no longer finds
the old label, and the preview's texts say what a rename cannot reach.
"""

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server
from qualcoder_mcp.database import QualcoderDatabase
from test_v012_pseudonymise_tool import (  # noqa: F401  (`project` is a fixture)
    execute_from, preview_of, project, query)


def _label(folder, sql):
    con = sqlite3.connect(str(folder / "data.qda"))
    con.execute(sql)
    con.commit()
    con.close()
    server.db.close()
    server.db = QualcoderDatabase(str(folder))


def test_a_rename_between_preview_and_execute_keeps_the_token(project):
    out = preview_of()
    assert out.get("preview_token"), out
    assert json.loads(server.rename_file(
        1, "P01_interview.txt"))["changed"] is True
    assert json.loads(server.rename_case(1, "P01"))["changed"] is True
    result = execute_from(out)
    assert result.get("success") is True, result
    row = query(project, "SELECT name, fulltext FROM source WHERE id = 1")[0]
    assert row["name"] == "P01_interview.txt"
    assert "Thomas" not in row["fulltext"] and "Alex" in row["fulltext"]


def test_the_residue_count_follows_the_rename(project):
    _label(project, "UPDATE cases SET name = 'Thomas_P01' WHERE caseid = 1")
    _label(project, "UPDATE source SET name = 'Thomas_notes.txt' "
                    "WHERE id = 4")
    residue = preview_of()["preview"]["residue"]
    assert residue["case_names"]["wide"] == 1
    assert residue["file_names"]["wide"] == 1
    server.rename_case(1, "P01", create_backup=False)
    server.rename_file(4, "P01_notes.txt", create_backup=False)
    residue = preview_of()["preview"]["residue"]
    assert residue["case_names"] == {"wide": 0, "whole_word": 0}
    assert residue["file_names"] == {"wide": 0, "whole_word": 0}


def test_the_preview_says_what_a_rename_cannot_reach(project):
    residue = preview_of()["preview"]["residue"]
    note = residue["scope_note"]
    assert ("The labels include case and file names, which rename_case "
            "and rename_file change. A rename does not reach, and this "
            "block does not read, the stored copy and stored path of an "
            "imported file (the copy in the project folder keeps the name "
            "it was imported under and, for a document, the original "
            "text), saved graph labels, or saved table displays and "
            "filters.") in note
    assert "renamed with rename_case, and a file named after the person " \
           "with rename_file." in residue["file_text"]["reading_note"]
    assert "by hand" not in json.dumps(residue)


def test_the_sidecar_path_carries_the_same_texts(project):
    (project / "pseudonyms.json").write_text(json.dumps(
        [{"original": "Thomas", "pseudonym": "Alex"}]), encoding="utf-8")
    out = json.loads(server.pseudonymise_source(
        file_id=1, use_project_pseudonyms=True))
    residue = out["preview"]["residue"]
    assert "rename_case and rename_file change" in residue["scope_note"]
    assert "renamed with rename_case" in residue["file_text"]["reading_note"]
