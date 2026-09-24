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


REPO = Path(__file__).resolve().parents[1]


def _flat(name):
    return " ".join((REPO / name).read_text(encoding="utf-8").split())


def test_the_description_names_the_two_tools():
    flat = " ".join(server.pseudonymise_source.__doc__.split())
    assert "Case and file names are changed with rename_case and " \
           "rename_file." in flat


def test_privacy_says_what_a_rename_cannot_reach():
    flat = _flat("PRIVACY.md")
    assert "never rewritten by the pseudonymisation tool (a case or file " \
           "name is renamed with `rename_case` or `rename_file`, below)" \
           in flat
    assert ("A rename cannot reach, and the preview's count does not read: "
            "the stored copy and stored path of an imported file (the copy "
            "in the project folder keeps the name it was imported under "
            "and, for a document, the original text, and QualCoder's "
            "exports ship that copy), saved graph labels, and saved table "
            "displays and filters.") in flat


def test_the_guide_readme_and_changelog_name_the_tools():
    assert "is renamed with `rename_case` or `rename_file`; an imported " \
           "file's stored copy keeps its original name and text." \
           in _flat("AI_CODING_GUIDE.md")
    readme = _flat("README.md")
    assert "- `rename_case(case_id, new_name, create_backup)` - **WRITES " \
           "TO DATABASE**" in readme
    assert "- `rename_file(file_id, new_name, create_backup)` - **WRITES " \
           "TO DATABASE**" in readme
    unreleased = _flat("CHANGELOG.md").split("## [0.12")[0]
    assert "### Added: `rename_case` and `rename_file`" in unreleased
    assert "**`import_text_file` shares `rename_file`'s name rules, so " \
           "some names it used to accept are refused.** Exactly these" \
           in unreleased
    # Fix round 1, QA-1 and QA-7: the bullet says there is no limit in
    # characters, and does not read as though 100 had been intended.
    assert "There is no limit in characters." in unreleased
    assert "100 characters" not in unreleased.split(
        "### Added: `rename_case` and `rename_file`")[1]


def test_the_documents_count_the_tools_the_server_registers():
    """README and INSTALL state the full count in prose; nothing read it
    before, so a tool added without them would have left both stale."""
    import asyncio
    count = len(asyncio.run(server.mcp.list_tools()))
    assert count == 72
    assert f"(the default, `QUALCODER_MCP_TOOLSET=full`) registers " \
           f"{count} tools" in _flat("README.md")
    install = _flat("INSTALL.md")
    assert f"`full` (default) registers all {count} tools" in install
    assert f"This server exposes {count} tools by default" in install


def test_every_rename_text_keeps_the_house_rules():
    from test_v012_pseudonymise_tool import _house_rules
    from qualcoder_mcp import database as D
    texts = [server.rename_case.__doc__, server.rename_file.__doc__,
             server.RENAME_CASE_NOTE, server.RENAME_FILE_NOTE,
             server.RENAME_FILE_SEARCH_INDEX_NOTE,
             server._RENAME_QC40_PARAGRAPH, D.documents_clash_message("x")]
    texts += [D.file_name_problem(n) for n in
              ("", "a​b", "\ud800", "a:b", "x" * 201,
               "\U0001d49c" * 51)]
    texts += [D.file_ending_problem(o, n, m, r) for o, n, m, r in (
        ("a.txt", "a", None, ["a"]), ("a.pdf", "a", "/docs/a.pdf", []),
        ("a", "a.transcribed", None, []), ("a.mp3", "a", "/audio/a.mp3", []),
        ("a.txt", "a.docx", None, []))]
    assert all(isinstance(t, str) and t for t in texts)
    _house_rules(texts)


def test_the_records_this_server_keeps_are_named():
    """Fix round 1, S-3: this server's own pseudonymisation journal entry
    and run record keep the file's name as it was at the run."""
    flat = " ".join(server.RENAME_FILE_NOTE.split())
    assert "this server's pseudonymisation journal entries and run " \
           "records, which keep the file's name as it was at the run " \
           "unless that name carried a name from the mapping" in flat
    # Fix round 1, QA-5 (Q10): the backup sentence is pinned too.
    assert "Every backup, including the one just taken, keeps the old " \
           "name, and so do this server's coding-session files" in flat
    assert "so do this server's pseudonymisation journal entries and run " \
           "records, which keep a file's name as it was at the run" \
           in _flat("PRIVACY.md")
