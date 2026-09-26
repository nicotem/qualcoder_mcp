# SPDX-License-Identifier: LGPL-3.0-or-later
"""v0.14, existing projects handled honestly (brief D).

Saved graphs after a category is deleted or merged; the PDF guard rails;
region and audio/video codings disclosed in reads; backups made
consistently and unclean ones named; the backup tools on a project set
in the host's configuration.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import qualcoder_mcp.server as server
import track5_helpers as H
from qualcoder_mcp.sessions import SessionManager
from test_v17_support import make_project

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def opened(tmp_path, monkeypatch):
    """Open a fixture project at a schema rung; restore the selection."""
    monkeypatch.delenv("QUALCODER_MCP_ALLOW_UNKNOWN_SCHEMA", raising=False)
    saved = (server.db, server.current_project_path, server.session_manager)
    server.db = None
    server.current_project_path = None
    server.session_manager = SessionManager(str(tmp_path / "sessions"))

    def open_version(version: str) -> Path:
        folder = make_project(tmp_path, version)
        return folder

    yield open_version

    if server.db is not None:
        try:
            server.db.close()
        except Exception:
            pass
    server.db, server.current_project_path, server.session_manager = saved


def _rows(folder, sql, args=()):
    conn = sqlite3.connect(str(folder / "data.qda"))
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


# ===========================================================================
# 1. Saved graphs after a category is deleted or merged
# ===========================================================================

def _seed_graph(folder: Path) -> None:
    """A saved graph in the shape QualCoder's Graph window writes it
    (the graphs study's probe project, reduced): category 1's node with
    its two codes under it and a tree line to each; a line between the
    two codes; a case linked by a free line to the category node and to
    code 1; and another category's node, which nothing here touches."""
    conn = sqlite3.connect(str(folder / "data.qda"))
    conn.execute("INSERT INTO code_cat VALUES (2, 'Other', '', 'V17Test', "
                 "'2024-01-15', NULL)")
    conn.execute("INSERT INTO cases (caseid, name, memo, owner, date) "
                 "VALUES (1, 'P1', '', 'V17Test', '2024-01-15')")
    conn.execute("INSERT INTO graph VALUES (1, 'tree', '', '2024-01-15', "
                 "1200, 900)")
    nodes = [(1, None, 1, None, 'Category A'), (2, 1, 1, 1, 'Stress'),
             (3, 1, 1, 2, 'Coping'), (4, None, 2, None, 'Other')]
    for gtextid, supercatid, catid, cid, text in nodes:
        conn.execute(
            "INSERT INTO gr_cdct_text_item (gtextid, grid, x, y, supercatid, "
            "catid, cid, font_size, bold, isvisible, displaytext) "
            "VALUES (?, 1, 10, 10, ?, ?, ?, 9, 0, 1, ?)",
            (gtextid, supercatid, catid, cid, text))
    # tree lines, category node to each code node
    for glineid, tocid in ((1, 1), (2, 2)):
        conn.execute(
            "INSERT INTO gr_cdct_line_item (glineid, grid, fromcatid, "
            "fromcid, tocatid, tocid, color, linewidth, linetype, "
            "isvisible) VALUES (?, 1, 1, NULL, 1, ?, 'gray', 2, 'solid', 1)",
            (glineid, tocid))
    # code 1 to code 2 (both carry category 1 in their rows)
    conn.execute(
        "INSERT INTO gr_free_line_item (gflineid, grid, fromcatid, fromcid, "
        "tocatid, tocid, color, linewidth, linetype) "
        "VALUES (1, 1, 1, 1, 1, 2, 'blue', 3, 'solid')")
    # case to the category node, and case to code 1
    conn.execute(
        "INSERT INTO gr_free_line_item (gflineid, grid, fromcaseid, "
        "tocatid, tocid, color, linewidth, linetype) "
        "VALUES (2, 1, 1, 1, NULL, 'gray', 2, 'dotted')")
    conn.execute(
        "INSERT INTO gr_free_line_item (gflineid, grid, fromcaseid, "
        "tocatid, tocid, color, linewidth, linetype) "
        "VALUES (3, 1, 1, 1, 1, 'gray', 2, 'dotted')")
    conn.commit()
    conn.close()


def _graph_state(folder: Path):
    return (
        sorted(r[0] for r in _rows(folder,
                                   "SELECT gtextid FROM gr_cdct_text_item")),
        sorted(r[0] for r in _rows(folder,
                                   "SELECT glineid FROM gr_cdct_line_item")),
        sorted(r[0] for r in _rows(folder,
                                   "SELECT gflineid FROM gr_free_line_item")),
    )


# What stays after either operation on v16 and later: the two code nodes
# and the other category's node; no tree line (both end on the category
# node); the code-to-code line and the case-to-code line.
KEPT = ([2, 3, 4], [], [1, 3])
UNTOUCHED = ([1, 2, 3, 4], [1, 2], [1, 2, 3])


class TestSavedGraphsAfterACategoryGoes:

    @pytest.mark.parametrize("version", ["v16", "v17"])
    def test_delete_category_removes_its_node_and_its_lines(self, opened,
                                                            version):
        folder = opened(version)
        _seed_graph(folder)
        server.select_project(str(folder))
        preview = json.loads(server.delete_category(1))
        graphs = preview["preview"]["saved_graph_rows_removed"]
        assert (graphs["category_nodes"], graphs["lines"]) == (1, 3)
        out = json.loads(H.execute_destructive(server.delete_category, 1))
        assert out.get("success") is True, out
        assert _graph_state(folder) == KEPT
        # the codes survive, at the top level, still on the graph
        assert _rows(folder, "SELECT cid, catid FROM code_name "
                             "ORDER BY cid") == [(1, None), (2, None)]

    @pytest.mark.parametrize("version", ["v16", "v17"])
    def test_merge_category_keeps_the_codes_nodes_and_lines(self, opened,
                                                            version):
        folder = opened(version)
        _seed_graph(folder)
        server.select_project(str(folder))
        preview = json.loads(server.merge_category(1, into_category="Other"))
        graphs = preview["preview"]["saved_graph_rows_removed"]
        assert (graphs["category_nodes"], graphs["lines"]) == (1, 3)
        out = json.loads(H.execute_destructive(
            server.merge_category, 1, into_category="Other"))
        assert out.get("success") is True, out
        assert _graph_state(folder) == KEPT
        assert _rows(folder, "SELECT cid, catid FROM code_name "
                             "ORDER BY cid") == [(1, 2), (2, 2)]

    @pytest.mark.parametrize("version", ["v14", "v15"])
    def test_older_projects_keep_every_graph_row(self, opened, version):
        """3.8.2 cleans no graph row after either operation, and our v14
        and v15 writes stay byte-exact with it (the v16 gate)."""
        folder = opened(version)
        _seed_graph(folder)
        server.select_project(str(folder))
        preview = json.loads(server.delete_category(1))
        assert "saved_graph_rows_removed" not in preview["preview"]
        out = json.loads(H.execute_destructive(server.delete_category, 1))
        assert out.get("success") is True, out
        assert _graph_state(folder) == UNTOUCHED

    def test_merge_to_the_top_level_cleans_the_same_rows(self, opened):
        folder = opened("v17")
        _seed_graph(folder)
        server.select_project(str(folder))
        out = json.loads(H.execute_destructive(server.merge_category, 1))
        assert out.get("success") is True, out
        assert _graph_state(folder) == KEPT


# ===========================================================================
# 2. PDF guard rails
# ===========================================================================

from qualcoder_mcp import database as dbmod  # noqa: E402

ARTICLE = ("The things in themselves are what first appear to reason. "
           "Hume tells us that reason is the slave of the passions.")
# A 3.8.2 binary row, in miniature: the PDF file decoded as text
STORED_PDF = ("%PDF-1.7\n%âã\n1 0 obj\n<< /Type /Catalog >>\n"
              "endobj\nstream\x00\x00x\u009c\x00SAMPLE obj\nendstream\n"
              "% Written by MuPDF 1.28.2\n%%EOF\n")
SCANNED = "\n\n"      # QualCoder 4.0's import of a two-page scan

# id: (name, mediapath, fulltext)
PDF_SOURCES = {
    2: ("article.pdf", "/docs/article.pdf", ARTICLE),
    3: ("scan.pdf", "/docs/scan.pdf", SCANNED),
    4: ("scan382.pdf", "/docs/scan382.pdf", STORED_PDF),
    5: ("linked.PDF", "docs:/elsewhere/linked.PDF", "   \t\n"),
}


def _add_pdfs(folder: Path) -> None:
    conn = sqlite3.connect(str(folder / "data.qda"))
    for fid, (name, mediapath, text) in PDF_SOURCES.items():
        conn.execute(
            "INSERT INTO source (id, name, fulltext, mediapath, memo, owner, "
            "date) VALUES (?, ?, ?, ?, '', 'V17Test', '2024-01-15')",
            (fid, name, text, mediapath))
    conn.commit()
    conn.close()


@pytest.fixture
def pdf_project(opened):
    folder = opened("v17")
    _add_pdfs(folder)
    out = json.loads(server.select_project(str(folder)))
    assert out.get("success") is True, out
    return folder


class TestRecognisingAnUnusablePdf:

    @pytest.mark.parametrize("text, expected", [
        (ARTICLE, None),
        (SCANNED, "no_text_layer"),
        ("", "no_text_layer"),
        (None, "no_text_layer"),
        (" \t\r\n  ", "no_text_layer"),
        (STORED_PDF, "pdf_file_stored_as_text"),
        ("x" * 1000 + "%PDF-1.4", "pdf_file_stored_as_text"),
        ("x" * 1020 + "%PDF-1.4", None),        # past the header window
        ("text\x00more", "pdf_file_stored_as_text"),
        ("\n" * 5000, "no_text_layer"),
        ("\n" * 5000 + "a", None),
        ("x" * 5000 + "\x00", "pdf_file_stored_as_text"),
    ])
    def test_the_rule(self, text, expected):
        assert dbmod.pdf_text_problem(text) == expected

    def test_the_list_reading_agrees_with_the_rule(self, opened):
        """The list reads only a prefix; it must answer as the rule does
        on the whole text, whatever the text's length."""
        folder = opened("v17")
        texts = [ARTICLE, SCANNED, "", None, " \t\r\n  ",
                 STORED_PDF, "x" * 1000 + "%PDF-1.4",
                 "x" * 1020 + "%PDF-1.4", "text\x00more", "\n" * 5000,
                 "\n" * 5000 + "a", "x" * 5000 + "\x00",
                 "é" * 400 + " " * 700 + "b"]
        conn = sqlite3.connect(str(folder / "data.qda"))
        for i, text in enumerate(texts, start=10):
            conn.execute("INSERT INTO source (id, name, fulltext, mediapath) "
                         "VALUES (?, ?, ?, ?)", (i, f"f{i}.pdf", text,
                                                 f"/docs/f{i}.pdf"))
        # a BLOB where text belongs, as a damaged row might hold
        conn.execute("INSERT INTO source (id, name, fulltext, mediapath) "
                     "VALUES (99, 'blob.pdf', ?, '/docs/blob.pdf')",
                     (b"%PDF-1.7 \x00\x01",))
        conn.commit()
        conn.close()
        server.select_project(str(folder))
        found = server.db.pdf_text_problems(
            list(range(10, 10 + len(texts))) + [99])
        for i, text in enumerate(texts, start=10):
            assert found.get(i) == dbmod.pdf_text_problem(text), (i, text)
        assert found[99] == "pdf_file_stored_as_text"


class TestUnusablePdfsInReads:

    def test_the_file_resource_names_them_and_withholds_the_file(
            self, pdf_project):
        stored = json.loads(server.get_file_content(4))
        assert stored["content"] == ""
        assert "%PDF" not in json.dumps(stored)
        assert stored["unusable_pdf"]["reason"] == "pdf_file_stored_as_text"
        assert stored["unusable_pdf"]["recognised_by"] == "heuristic"
        assert "Restructure" in stored["unusable_pdf"]["message"]
        assert stored["is_text"] is False
        scan = json.loads(server.get_file_content(3))
        assert scan["unusable_pdf"]["reason"] == "no_text_layer"
        assert "OCR" in scan["unusable_pdf"]["message"]
        assert scan["is_text"] is False
        article = json.loads(server.get_file_content(2))
        assert "unusable_pdf" not in article and article["is_text"] is True
        assert article["content"] == ARTICLE

    def test_the_file_list_and_the_summary_name_them(self, pdf_project):
        files = {f["id"]: f for f in json.loads(server.list_all_files())}
        assert files[3]["unusable_pdf"] == "no_text_layer"
        assert files[4]["unusable_pdf"] == "pdf_file_stored_as_text"
        assert files[5]["unusable_pdf"] == "no_text_layer"
        assert "unusable_pdf" not in files[2]
        assert "unusable_pdf" not in files[1]
        summary = json.loads(server.get_project_summary())
        assert sorted((u["file_id"], u["reason"]) for u in
                      summary["unusable_pdfs"]) == [
            (3, "no_text_layer"), (4, "pdf_file_stored_as_text"),
            (5, "no_text_layer")]

    def test_analyze_file_withholds_the_stored_file(self, pdf_project):
        out = json.loads(server.analyze_file_with_coding(4))
        assert out["full_text"] == ""
        assert out["statistics"]["text_length"] == 0
        assert "%PDF" not in json.dumps(out)
        assert out["file_info"]["unusable_pdf"]["reason"] == \
            "pdf_file_stored_as_text"
        assert "Restructure" in out["note"] and "OCR" in out["note"]
        scan = json.loads(server.analyze_file_with_coding(3))
        assert scan["file_info"]["unusable_pdf"]["reason"] == "no_text_layer"
        assert "no text layer" in scan["note"]
        article = json.loads(server.analyze_file_with_coding(2))
        assert article["full_text"] == ARTICLE
        assert "unusable_pdf" not in article["file_info"]

    def test_a_content_search_counts_them_as_not_searched(self,
                                                          pdf_project):
        out = json.loads(server.search_files("obj", search_filename=False,
                                             search_content=True))
        assert out["results"] == []
        info = out["performance_info"]
        assert info["files_skipped_unusable_pdf"] == 3
        assert {(u["file_id"], u["reason"])
                for u in info["unusable_pdfs_not_searched"]} == {
            (3, "no_text_layer"), (4, "pdf_file_stored_as_text"),
            (5, "no_text_layer")}
        assert "finding nothing in them means nothing" in \
            info["unusable_pdf_note"]
        # a PDF with a text layer is still searched
        hit = json.loads(server.search_files("Hume", search_filename=False,
                                             search_content=True))
        assert [r["file_id"] for r in hit["results"]] == [2]
        # file names are still searched
        named = json.loads(server.search_files("scan382"))
        assert [r["file_id"] for r in named["results"]] == [4]


class TestUnusablePdfsInCodingTools:

    def test_analyze_for_coding_refuses_them_by_name(self, pdf_project):
        out = json.loads(server.analyze_for_coding([3, 4]))
        assert "error" in out
        assert {f["file_id"] for f in out["files_refused"]} == {3, 4}
        mixed = json.loads(server.analyze_for_coding([2, 4]))
        assert "coding_session_id" in mixed
        assert [f["file_id"] for f in mixed["files_refused"]] == [4]
        assert "scan382.pdf" not in mixed["instructions"]
        assert "article.pdf" in mixed["instructions"]

    def test_record_suggestions_refuses_them_with_the_way_forward(
            self, pdf_project):
        sid = json.loads(server.analyze_for_coding([2]))["coding_session_id"]
        out = json.loads(server.record_suggestions(sid, [
            {"file_id": 4, "code_name": "Stress", "segment_text": "SAMPLE",
             "reasoning": "r", "confidence": 0.9},
            {"file_id": 3, "code_name": "Stress", "segment_text": "\n",
             "start_pos": 0, "end_pos": 1, "reasoning": "r",
             "confidence": 0.9},
            {"file_id": 2, "code_name": "Stress", "segment_text": "Hume",
             "reasoning": "r", "confidence": 0.9},
        ]))
        reasons = {r["index"]: r["reason"] for r in out["rejected"]}
        assert "pdf_file_stored_as_text" in reasons[0]
        assert "Restructure" in reasons[0]
        assert "no_text_layer" in reasons[1] and "OCR" in reasons[1]
        assert out["recorded_count"] == 1

    def test_proposal_evidence_refuses_them(self, pdf_project):
        sid = json.loads(server.analyze_for_coding([2]))["coding_session_id"]
        out = json.loads(server.propose_codes(sid, [
            {"name": "Scanned", "memo": "m", "rationale": "r",
             "example_segments": [{"file_id": 4,
                                   "segment_text": "SAMPLE"}]}]))
        text = json.dumps(out)
        assert "pdf_file_stored_as_text" in text

    def test_add_annotation_refuses_before_any_backup(self, pdf_project):
        folder = pdf_project
        before = sorted(p.name for p in folder.parent.iterdir())
        out = json.loads(server.add_annotation(4, 0, 4, "a note"))
        assert "pdf_file_stored_as_text" in out["error"]
        out = json.loads(server.add_annotation(3, 0, 1, "a note"))
        assert "no_text_layer" in out["error"]
        assert sorted(p.name for p in folder.parent.iterdir()) == before

    def test_apply_and_edit_refuse_a_file_that_became_unusable(
            self, pdf_project):
        """A suggestion recorded while the file had text, applied or
        edited after the row turned into a stored PDF file (a restore of
        an older backup, say): refused with the way forward."""
        folder = pdf_project
        sid = json.loads(server.analyze_for_coding([2]))["coding_session_id"]
        rec = json.loads(server.record_suggestions(sid, [
            {"file_id": 2, "code_name": "Stress", "segment_text": "Hume",
             "reasoning": "r", "confidence": 0.9}]))
        guid = rec["recorded"][0]["guid"]
        conn = sqlite3.connect(str(folder / "data.qda"))
        conn.execute("UPDATE source SET fulltext = ? WHERE id = 2",
                     (STORED_PDF,))
        conn.commit()
        conn.close()
        edit = json.loads(server.edit_suggestion(sid, guid, start_pos=0,
                                                 end_pos=3))
        assert "pdf_file_stored_as_text" in edit["error"]
        edit = json.loads(server.edit_suggestion(sid, guid,
                                                 use_alternative="longer"))
        assert "pdf_file_stored_as_text" in edit["error"]
        server.update_suggestion_status(sid, approve=[guid])
        applied = json.loads(server.apply_codings(sid, create_backup=False))
        assert "pdf_file_stored_as_text" in json.dumps(applied)
        assert _rows(folder, "SELECT COUNT(*) FROM code_text WHERE fid = 2"
                     ) == [(0,)]


# ===========================================================================
# 3. Region and audio/video codings disclosed in reads
# ===========================================================================

def _add_media_codings(folder: Path) -> None:
    """Code 1 (Stress): its one text coding (the base fixture) plus two
    areas on the PDF with a text layer, one by another coder; code 2
    (Coping): one audio segment on an audio file."""
    _add_pdfs(folder)
    conn = sqlite3.connect(str(folder / "data.qda"))
    conn.execute("INSERT INTO source (id, name, fulltext, mediapath, owner, "
                 "date) VALUES (6, 'talk.mp3', NULL, '/audio/talk.mp3', "
                 "'V17Test', '2024-01-15')")
    conn.execute("INSERT INTO code_image (imid, id, x1, y1, width, height, "
                 "cid, memo, date, owner, pdf_page) VALUES "
                 "(1, 2, 157, 49, 113, 32, 1, '', '2024-01-15', 'V17Test', 0)")
    conn.execute("INSERT INTO code_image (imid, id, x1, y1, width, height, "
                 "cid, memo, date, owner, pdf_page) VALUES "
                 "(2, 2, 10, 10, 50, 20, 1, '', '2024-01-15', 'Other', 1)")
    conn.execute("INSERT INTO code_av (avid, cid, id, pos0, pos1, memo, "
                 "owner, date) VALUES (1, 2, 6, 1000, 5000, '', 'V17Test', "
                 "'2024-01-15')")
    conn.commit()
    conn.close()


@pytest.fixture
def media_project(opened):
    folder = opened("v17")
    _add_media_codings(folder)
    out = json.loads(server.select_project(str(folder)))
    assert out.get("success") is True, out
    return folder


class TestRegionCodingsDisclosed:

    def test_coded_segments_agree_with_the_delete_preview(self,
                                                          media_project):
        out = json.loads(server.get_coded_segments(1))
        assert out["segment_count"] == 1
        block = out["codings_not_shown"]
        assert (block["region"], block["audio_video"]) == (2, 0)
        assert "areas on PDF pages or images" in block["note"]
        preview = json.loads(server.delete_code(1))["preview"]
        assert preview["image_codings_to_delete"] == block["region"]
        assert preview["av_codings_to_delete"] == block["audio_video"]
        assert preview["total_codings_to_delete"] == (
            out["segment_count"] + block["region"] + block["audio_video"])

    def test_the_scope_of_the_read_is_the_scope_of_the_count(
            self, media_project):
        other = json.loads(server.get_coded_segments(1, coder="Other"))
        assert other["segment_count"] == 0
        assert other["codings_not_shown"]["region"] == 1
        text_only = json.loads(server.get_coded_segments(1, file_ids=[1]))
        assert "codings_not_shown" not in text_only
        av = json.loads(server.get_coded_segments(2))
        assert (av["codings_not_shown"]["region"],
                av["codings_not_shown"]["audio_video"]) == (0, 1)

    def test_a_file_read_counts_its_areas(self, media_project):
        pdf = json.loads(server.analyze_file_with_coding(2))
        assert pdf["coded_segments"] == []
        assert pdf["codings_not_shown"]["region"] == 2
        text = json.loads(server.analyze_file_with_coding(1))
        assert "codings_not_shown" not in text
        audio = json.loads(server.analyze_file_with_coding(6))
        assert audio["codings_not_shown"]["audio_video"] == 1

    def test_frequencies_and_the_summary_say_what_they_do_not_count(
            self, media_project):
        freq = json.loads(server.get_coding_frequencies())
        codes = {c["code_id"]: c for c in freq["codes"]}
        assert codes[1]["frequency"] == 1
        assert codes[1]["codings_not_counted"] == {"region": 2,
                                                   "audio_video": 0}
        assert codes[2]["codings_not_counted"] == {"region": 0,
                                                   "audio_video": 1}
        total = freq["codings_not_counted"]
        assert (total["region"], total["audio_video"]) == (2, 1)
        # QualCoder's own counts (its Codebook export and Code Frequencies
        # report: text, image and audio/video rows, every coder) are what
        # the read counts plus what it says it does not
        qualcoder = server.db.get_codebook_frequencies()
        for cid, entry in codes.items():
            extra = entry.get("codings_not_counted",
                              {"region": 0, "audio_video": 0})
            assert qualcoder.get(cid, 0) == (entry["frequency"]
                                             + extra["region"]
                                             + extra["audio_video"])
        mine = json.loads(server.get_coding_frequencies(coder="V17Test"))
        assert mine["codings_not_counted"]["region"] == 1
        summary = json.loads(server.get_project_summary())
        stats = summary["statistics"]["codings_not_counted"]
        assert (stats["region"], stats["audio_video"]) == (2, 1)

    def test_nothing_is_said_when_there_is_nothing(self, opened):
        folder = opened("v17")
        server.select_project(str(folder))
        assert "codings_not_shown" not in json.loads(
            server.get_coded_segments(1))
        freq = json.loads(server.get_coding_frequencies())
        assert "codings_not_counted" not in freq
        assert all("codings_not_counted" not in c for c in freq["codes"])

    def test_a_hidden_coders_areas_are_not_counted(self, tmp_path):
        """On a project that hides a coder, the count is the visible
        coders' own, as the read's is."""
        from qualcoder_mcp import new_project
        folder = tmp_path / "Hidden.qda"
        new_project.write_project(folder, new_project.creation_statements(
            "carol", new_project.about_line("0.14.0"),
            "2026-09-26 10:00:00"))
        conn = sqlite3.connect(str(folder / "data.qda"))
        conn.execute("INSERT INTO coder_names (name, visibility) "
                     "VALUES ('Other', 0)")
        conn.execute("INSERT INTO code_name (cid, name, memo, owner, date, "
                     "color) VALUES (1, 'Stress', '', 'carol', '2026', "
                     "'#FF0000')")
        conn.execute("INSERT INTO source (id, name, fulltext, mediapath, "
                     "owner, date) VALUES (2, 'a.pdf', 'text', "
                     "'/docs/a.pdf', 'carol', '2026')")
        for imid, owner in ((1, "carol"), (2, "Other"), (3, "Other")):
            conn.execute("INSERT INTO code_image (imid, id, x1, y1, width, "
                         "height, cid, memo, date, owner, pdf_page) VALUES "
                         "(?, 2, 1, 1, 5, 5, 1, '', '2026', ?, 0)",
                         (imid, owner))
        conn.commit()
        conn.close()
        H.write_fixture_sidecar(folder)
        saved = (server.db, server.current_project_path)
        try:
            server.select_project(str(folder))
            out = json.loads(server.get_coded_segments(1))
            assert out["codings_not_shown"]["region"] == 1
            other = json.loads(server.get_coded_segments(1, coder="Other"))
            assert other["codings_not_shown"]["region"] == 2
        finally:
            if server.db is not None and server.db is not saved[0]:
                server.db.close()
            server.db, server.current_project_path = saved


# ===========================================================================
# 5. The backup tools on a project set in the host's configuration
# ===========================================================================

@pytest.fixture
def configured(opened, monkeypatch):
    """A server started with QUALCODER_PROJECT_PATH and no tool run yet."""
    folder = opened("v17")
    monkeypatch.setenv("QUALCODER_PROJECT_PATH", str(folder))
    server.db = None
    server.current_project_path = None
    return folder


class TestAConfiguredProjectAtFirstUse:

    def test_list_backups_answers_first(self, configured):
        out = json.loads(server.list_backups())
        assert "error" not in out, out
        assert out["project"] == configured.stem
        assert out["backup_count"] == 0
        assert Path(server.current_project_path).resolve() == \
            configured.resolve()

    def test_prune_backups_answers_first(self, configured):
        out = json.loads(server.prune_backups(keep_last=5))
        assert "No Qualcoder project selected" not in json.dumps(out)
        assert "error" not in out, out

    def test_restore_backup_answers_first(self, configured):
        out = json.loads(server.restore_backup(
            str(configured.parent / "missing_backup.qda")))
        assert "Backup path not found" in out["error"]

    @pytest.mark.parametrize("call", [
        lambda: server.get_current_project(),
        lambda: server.read_pseudonym_list(),
        lambda: server.set_project_ai_coder_name("Model A"),
        lambda: server.set_memo("code", 1, "a note", create_backup=False),
    ])
    def test_the_other_tools_that_asked_first(self, configured, call):
        out = json.loads(call())
        assert "No Qualcoder project selected" not in json.dumps(out)
        assert "No project currently open" not in json.dumps(out)
        assert out.get("error") is None, out

    def test_a_configured_path_that_cannot_be_opened_says_why(
            self, tmp_path, monkeypatch, opened):
        monkeypatch.setenv("QUALCODER_PROJECT_PATH",
                           str(tmp_path / "gone.qda"))
        server.db = None
        server.current_project_path = None
        out = json.loads(server.list_backups())
        assert "No Qualcoder project selected" not in out["error"]
        assert server.current_project_path is None

    def test_without_a_configured_project_nothing_changes(self, opened,
                                                          monkeypatch):
        monkeypatch.delenv("QUALCODER_PROJECT_PATH", raising=False)
        server.db = None
        server.current_project_path = None
        out = json.loads(server.list_backups())
        assert "No Qualcoder project selected" in out["error"]

    def test_over_the_wire_the_first_call_is_list_backups(self, tmp_path):
        """The real start-up path: a server started with the project in
        its configuration, and list_backups as the very first call."""
        import asyncio
        import os
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        folder = make_project(tmp_path, "v17")
        backup = folder.parent / f"{folder.stem}_backup_20260926_101500.qda"
        backup.mkdir()
        (backup / "data.qda").write_bytes((folder / "data.qda").read_bytes())
        home = tmp_path / "wirehome"
        home.mkdir()
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)
        env["QUALCODER_PROJECT_PATH"] = str(folder)
        env.pop("QUALCODER_MCP_TOOLSET", None)
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "qualcoder_mcp.server"],
            env=env)

        async def drive():
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("list_backups", {})
                    return "".join(b.text for b in result.content
                                   if getattr(b, "type", None) == "text")

        out = json.loads(asyncio.run(drive()))
        assert "error" not in out, out
        assert [b["name"] for b in out["backups"]] == [backup.name]


# ===========================================================================
# 4. Backups made consistently; unclean ones named, never used
# ===========================================================================

import subprocess  # noqa: E402
from qualcoder_mcp.database import (backup_project,  # noqa: E402
                                    copy_project_to_workspace,
                                    DatabaseLockedError)

HOLDER = r"""
import sqlite3, sys
c = sqlite3.connect(sys.argv[1], isolation_level=None)
if sys.argv[2] == "exclusive":
    c.execute("BEGIN EXCLUSIVE")
else:
    c.execute("BEGIN IMMEDIATE")
    c.execute("UPDATE code_name SET memo = 'uncommitted' WHERE cid = 1")
    c.execute("DELETE FROM code_text")
print("holding", flush=True)
sys.stdin.readline()
c.execute("ROLLBACK")
c.close()
"""


class _Writer:
    """Another program inside a write on the project's database."""

    def __init__(self, folder: Path, mode: str):
        self.proc = subprocess.Popen(
            [sys.executable, "-c", HOLDER, str(folder / "data.qda"), mode],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        assert self.proc.stdout.readline().strip() == "holding"

    def release(self):
        try:
            self.proc.stdin.write("\n")
            self.proc.stdin.flush()
        except OSError:
            pass
        self.proc.wait(timeout=30)


def _read_backup(folder: Path, how: str):
    data = folder / "data.qda"
    uri = {"ro": data.as_uri() + "?mode=ro",
           "immutable": data.as_uri() + "?mode=ro&immutable=1"}[how]
    conn = sqlite3.connect(uri, uri=True)
    try:
        return (conn.execute("SELECT memo FROM code_name WHERE cid = 1"
                             ).fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM code_text").fetchone()[0],
                conn.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        conn.close()


class TestBackupsMadeConsistently:

    def test_a_backup_during_another_programs_write_is_clean(self, opened):
        """The undo study's check, as a test: another program has begun
        a write (its journal is beside the database) when the backup is
        taken. The backup holds no side file and reads, read-only and as
        immutable, as the last committed state, on every SQLite."""
        folder = opened("v17")
        writer = _Writer(folder, "reserved")
        try:
            assert (folder / "data.qda-journal").exists()
            backup = backup_project(folder)
        finally:
            writer.release()
        assert sorted(p.name for p in backup.iterdir()
                      if p.name.startswith("data.qda")) == ["data.qda"]
        for how in ("ro", "immutable"):
            assert _read_backup(backup, how) == ("stress memo", 1, "ok")

    def test_the_workspace_copy_is_made_the_same_way(self, opened,
                                                     tmp_path):
        folder = opened("v17")
        writer = _Writer(folder, "reserved")
        try:
            copy = copy_project_to_workspace(folder,
                                             workspace=tmp_path / "ws")
        finally:
            writer.release()
        assert not (copy / "data.qda-journal").exists()
        assert _read_backup(copy, "ro") == ("stress memo", 1, "ok")

    def test_a_database_kept_locked_refuses_the_write(self, opened,
                                                      monkeypatch):
        """Past the wait, no backup and so no write: said as a lock."""
        import qualcoder_mcp.database as database
        monkeypatch.setattr(database, "BACKUP_BUSY_SECONDS", 0.2)
        folder = opened("v17")
        server.select_project(str(folder))
        before = sorted(p.name for p in folder.parent.iterdir())
        writer = _Writer(folder, "exclusive")
        try:
            with pytest.raises(DatabaseLockedError):
                backup_project(folder)
            assert sorted(p.name for p in folder.parent.iterdir()) == before
        finally:
            writer.release()
        # through a write tool, once the lock has moved on to a write in
        # flight at the backup (the tool's own lock wait is not this one)
        monkeypatch.setattr(database, "_copy_database",
                            lambda *a, **k: (_ for _ in ()).throw(
                                DatabaseLockedError("locked")))
        out = json.loads(server.set_memo("code", 1, "new", create_backup=True))
        assert "locked" in out["error"] and "nothing was written" in \
            out["error"]
        assert _rows(folder, "SELECT memo FROM code_name WHERE cid = 1") == \
            [("stress memo",)]
        assert sorted(p.name for p in folder.parent.iterdir()) == before

    def test_a_wal_database_is_backed_up_whole_in_one_file(self, opened):
        folder = opened("v17")
        conn = sqlite3.connect(str(folder / "data.qda"))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("UPDATE code_name SET memo = 'in the wal' WHERE cid = 1")
        conn.commit()
        try:
            assert (folder / "data.qda-wal").exists()
            backup = backup_project(folder)
        finally:
            conn.close()
        assert not (backup / "data.qda-wal").exists()
        assert not (backup / "data.qda-shm").exists()
        check = sqlite3.connect(str(backup / "data.qda"))
        try:
            assert check.execute("PRAGMA journal_mode").fetchone()[0] == \
                "delete"
            assert check.execute("SELECT memo FROM code_name WHERE cid = 1"
                                 ).fetchone()[0] == "in the wal"
        finally:
            check.close()

    def test_a_database_sqlite_cannot_read_is_kept_as_it_is(self, tmp_path):
        """A restore's safety backup of a damaged project keeps the bytes
        and the journal, and the backup is then named unclean."""
        folder = tmp_path / "Damaged.qda"
        folder.mkdir()
        (folder / "data.qda").write_bytes(b"not a database, " * 64)
        (folder / "data.qda-journal").write_bytes(b"journal bytes")
        (folder / "documents").mkdir()
        report = {}
        backup = backup_project(folder, report=report)
        assert (backup / "data.qda").read_bytes() == b"not a database, " * 64
        assert (backup / "data.qda-journal").read_bytes() == b"journal bytes"
        assert report["database_copied_as_file"]
        assert (backup / "documents").is_dir()

    def test_a_crash_journal_is_kept_and_the_project_left_alone(
            self, opened):
        """A write killed part-way leaves a hot journal and pages of the
        uncommitted write in the file. The backup reads read-only, so it
        never rolls the live project back itself: it keeps the database
        and the journal as they are, and the backup is named unclean."""
        folder = opened("v17")
        conn = sqlite3.connect(str(folder / "data.qda"))
        conn.executemany("INSERT INTO code_text (cid, fid, seltext, pos0, "
                         "pos1, owner) VALUES (1, 1, ?, ?, ?, 'x')",
                         [("s" * 400, i, i + 1) for i in range(2, 400)])
        conn.commit()
        conn.close()
        crash = subprocess.run([sys.executable, "-c", (
            "import os, sqlite3, sys\n"
            "c = sqlite3.connect(sys.argv[1], isolation_level=None)\n"
            "c.execute('PRAGMA cache_size=1')\n"
            "c.execute('BEGIN IMMEDIATE')\n"
            "c.execute(\"UPDATE code_text SET seltext = 'uncommitted'\")\n"
            "os._exit(0)\n"), str(folder / "data.qda")])
        assert crash.returncode == 0
        journal = folder / "data.qda-journal"
        assert journal.exists() and journal.stat().st_size > 0
        before = journal.read_bytes()
        report = {}
        backup = backup_project(folder, report=report)
        assert report["database_copied_as_file"]
        assert journal.read_bytes() == before
        assert (backup / "data.qda-journal").read_bytes() == before
        # (select_project itself refuses a project with a hot journal,
        # so the listing is read the way list_backups builds it)
        listed = server._collect_backups(folder)
        assert [b["name"] for b in listed if "unclean" in b] == \
            [backup.name]

    def test_the_rest_of_the_folder_is_copied_as_before(self, opened):
        folder = opened("v17")
        (folder / "documents").mkdir()
        (folder / "documents" / "a.txt").write_text("text", encoding="utf-8")
        (folder / "ai_data").mkdir()
        (folder / "ai_data" / "search.sqlite").write_bytes(b"index")
        (folder / "ai_data" / "chat_history.sqlite").write_bytes(b"chat")
        (folder / "project_in_use.lock").write_text("x", encoding="utf-8")
        backup = backup_project(folder)
        assert (backup / "documents" / "a.txt").read_text(
            encoding="utf-8") == "text"
        assert (backup / "ai_data" / "chat_history.sqlite").read_bytes() == \
            b"chat"
        assert not (backup / "ai_data" / "search.sqlite").exists()
        assert not (backup / "project_in_use.lock").exists()
        assert (backup / "qualcoder_mcp.json").exists()


def _plant_backup(folder: Path, suffix: str, side=()):
    backup = folder.parent / f"{folder.stem}_backup_{suffix}.qda"
    backup.mkdir()
    (backup / "data.qda").write_bytes((folder / "data.qda").read_bytes())
    for name in side:
        (backup / name).write_bytes(b"side")
    return backup


class TestUncleanBackupsNamed:

    def test_list_backups_names_them(self, opened):
        folder = opened("v17")
        _plant_backup(folder, "20260901_100000")
        _plant_backup(folder, "20260902_100000", ["data.qda-journal"])
        _plant_backup(folder, "20260903_100000", ["data.qda-wal",
                                                  "data.qda-shm"])
        _plant_backup(folder, "20260904_100000", ["data.qda-shm"])
        server.select_project(str(folder))
        out = json.loads(server.list_backups())
        marks = {b["name"][-19:-4]: b.get("unclean", {}).get("side_files")
                 for b in out["backups"]}
        assert marks == {"20260901_100000": None,
                         "20260902_100000": ["data.qda-journal"],
                         "20260903_100000": ["data.qda-wal"],
                         "20260904_100000": None}
        assert len(out["unclean_backups"]) == 2
        assert "restore_backup refuses it" in out["unclean_note"]

    def test_restore_refuses_them_on_the_preview_and_the_execute(
            self, opened):
        folder = opened("v17")
        clean = _plant_backup(folder, "20260901_100000")
        server.select_project(str(folder))
        preview = json.loads(server.restore_backup(str(clean)))
        token = preview["preview_token"]
        # the side file appears between the preview and the execute
        (clean / "data.qda-journal").write_bytes(b"side")
        out = json.loads(server.restore_backup(str(clean),
                                               preview_token=token))
        assert out["reason"] == "unclean_backup"
        assert out["side_files"] == ["data.qda-journal"]
        assert _rows(folder, "SELECT memo FROM code_name WHERE cid = 1") == \
            [("stress memo",)]
        again = json.loads(server.restore_backup(str(clean)))
        assert again["reason"] == "unclean_backup"
        assert "preview_token" not in again


# ===========================================================================
# 7. Two people who share a name, and notes: the caveat, in every place
#    v0.13's promise of two pseudonyms was made
# ===========================================================================

def _flat(text: str) -> str:
    """A document with its wrapping (and Markdown quote marks and code
    marks) flattened, so a line break cannot hide a sentence."""
    return " ".join(text.replace("\n>", " ").replace("`", "").split())


class TestTheSharedNameCaveat:

    def _description(self):
        import asyncio
        tools = asyncio.run(server.mcp.list_tools())
        return _flat(next(t.description for t in tools
                          if t.name == "pseudonymise_source"))

    @pytest.mark.parametrize("where", ["description", "README.md",
                                       "PRIVACY.md", "CHANGELOG.md"])
    def test_the_caveat_and_the_safe_route(self, where):
        text = (self._description() if where == "description" else
                _flat((REPO / where).read_text(encoding="utf-8")))
        if where == "CHANGELOG.md":
            text = text.split("## [0.13")[0]    # the release being written
        assert "whichever run carries it rewrites that name in notes " \
               "across the whole project" in text
        assert "the other person's notes included" in text
        assert "keep rewrite_memos off on every run" in text.lower()
        assert "change the notes that name either person by hand" in text
        assert "save_mapping_to_project off" in text
        assert "researcher_keeps_mapping on" in text
        assert "pseudonyms.json holds one pseudonym per name" in text

    def test_the_v013_entry_carries_it_as_the_release_notes_do(self):
        text = _flat((REPO / "CHANGELOG.md").read_text(encoding="utf-8"))
        v013 = text[text.index("## [0.13"):text.index("## [0.12")]
        promise = v013.index("share a name get two pseudonyms")
        assert "with rewrite_memos on, a run rewrites that name in notes " \
               "across the whole project" in v013[promise:promise + 500]

