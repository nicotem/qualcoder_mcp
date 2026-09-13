"""B4 (v0.12, D4): the novelty filter, stable cursors and sampling budgets.

Three read tools learn to say "not that, I have coded it already", to be
walked page by page without anything being stored between calls, and to
hand back a sample of a code's segments under a character budget.

The overlap rule is QualCoder's own (`ai_mcp_server.py:5245-5257` at the
pinned master 9bddf17) and is pinned here against the cases that decide
it, adjacency included. The deviations are pinned as deviations: the
exclusion source honours coder visibility where upstream reads the base
table (`:5228`), unknown ids are refused rather than ignored
(`:5211-5213`), and the first segment of a budgeted page is always
returned (`:5367-5372`).
"""

import json
import re
import sqlite3
from pathlib import Path

import pytest

import qualcoder_mcp.server as server
from qualcoder_mcp import cursors
from qualcoder_mcp.database import QualcoderDatabase


def _con(project_path):
    con = sqlite3.connect(str(Path(project_path) / "data.qda"))
    con.row_factory = sqlite3.Row
    return con


def _reopen(project_path):
    if server.db is not None:
        try:
            server.db.close()
        except Exception:
            pass
    server.db = QualcoderDatabase(project_path)


def _add_coding(project_path, ctid, cid, fid, pos0, pos1, text="x",
                owner="TestCoder", date="2024-01-15", memo=""):
    con = _con(project_path)
    con.execute(
        "INSERT INTO code_text (ctid, cid, fid, seltext, pos0, pos1, owner, "
        "date, memo, important) VALUES (?,?,?,?,?,?,?,?,?,0)",
        (ctid, cid, fid, text, pos0, pos1, owner, date, memo))
    con.commit()
    con.close()


def _add_file(project_path, fid, name, text):
    con = _con(project_path)
    con.execute(
        "INSERT INTO source (id, name, fulltext, mediapath, memo, owner, "
        "date) VALUES (?,?,?,NULL,'','TestCoder','2024-01-15')",
        (fid, name, text))
    con.commit()
    con.close()


def _add_code(project_path, cid, name, catid=None):
    con = _con(project_path)
    con.execute(
        "INSERT INTO code_name (cid, name, memo, catid, owner, date, color) "
        "VALUES (?,?,'',?,'TestCoder','2024-01-15','#FF0000')",
        (cid, name, catid))
    con.commit()
    con.close()


# =============================================================================
# PARITY PINS 1 TO 5: THE OVERLAP RULE
# =============================================================================

class TestOverlapRule:
    """`ai_mcp_server.py:5245-5257` at 9bddf17: a candidate is excluded
    iff `s < b and e > a` for some excluded span `(a, b)`. Half-open, so
    adjacency is not overlap."""

    @pytest.mark.parametrize("start,end,excluded", [
        (54, 60, True),     # overlaps the tail of [24, 55)
        (55, 60, False),    # begins exactly where it ends: adjacency
        (0, 24, False),     # ends exactly where it begins: adjacency
        (30, 31, True),     # strictly inside
        (24, 24, False),    # empty candidate never overlaps
        (0, 100, True),     # contains it
        (23, 25, True),     # overlaps the head
    ])
    def test_the_half_open_table(self, setup_server, qualcoder_db_path,
                                 start, end, excluded):
        mask = server.db.excluded_span_mask([1])
        entry = mask.get(1)
        assert entry is not None, "the fixture's ctid 1 is cid 1 on file 1"
        assert server.db.span_is_excluded(entry, start, end) is excluded

    def test_degenerate_rows_are_ignored(self, setup_server,
                                         qualcoder_db_path):
        """`:5212`: rows with pos1 <= pos0 are dropped before the test."""
        _add_coding(qualcoder_db_path, 40, 3, 2, 40, 40)
        _add_coding(qualcoder_db_path, 41, 3, 2, 50, 45)
        _reopen(qualcoder_db_path)
        mask = server.db.excluded_span_mask([3])
        assert mask.get(2) is None

    def test_the_rule_is_or_across_codes_and_per_file(self, setup_server,
                                                     qualcoder_db_path):
        _add_coding(qualcoder_db_path, 42, 2, 2, 5, 9)
        _reopen(qualcoder_db_path)
        mask = server.db.excluded_span_mask([1, 2])
        # file 1 carries cid 1 [24,55) and cid 2 [57,77); file 2 carries
        # the new cid 2 row and nothing else
        assert server.db.span_is_excluded(mask.get(1), 58, 60) is True
        assert server.db.span_is_excluded(mask.get(2), 58, 60) is False
        assert server.db.span_is_excluded(mask.get(2), 6, 7) is True

    def test_only_the_listed_codes_count(self, setup_server,
                                         qualcoder_db_path):
        """The cid list is literal: no sub-code expansion (ruling Q6)."""
        _add_code(qualcoder_db_path, 50, "Sub of Stress")
        _add_coding(qualcoder_db_path, 43, 50, 1, 5, 10)
        _reopen(qualcoder_db_path)
        mask = server.db.excluded_span_mask([1])
        assert server.db.span_is_excluded(mask.get(1), 6, 7) is False
        mask = server.db.excluded_span_mask([1, 50])
        assert server.db.span_is_excluded(mask.get(1), 6, 7) is True

    def test_self_exclusion_in_search_coded_text(self, setup_server,
                                                 qualcoder_db_path):
        """A segment is itself a coding, so excluding its own code
        returns nothing. Correct, documented, and rarely what the caller
        meant."""
        out = json.loads(server.search_coded_text("cope",
                                                  exclude_code_ids=[2]))
        assert out["result_count"] == 0
        out = json.loads(server.search_coded_text("cope",
                                                  exclude_code_ids=[1]))
        assert [r["id"] for r in out["results"]] == [2]

    def test_the_documented_example_is_in_the_description(self):
        doc = " ".join((server.search_coded_text.__doc__ or "").split())
        assert "half-open" in doc
        assert "begins exactly where an excluded coding ends is kept" in doc
        assert "a segment is itself a coding" in doc.lower()


# =============================================================================
# SEARCH_FILES: THE FILTER, THE CAP AND THE ANCHORS
# =============================================================================

class TestSearchFilesFilter:

    def test_an_excluded_file_is_not_a_result_but_is_counted(
            self, setup_server, qualcoder_db_path):
        out = json.loads(server.search_files("stress", search_content=True,
                                             exclude_code_ids=[1]))
        assert out["results"] == []
        block = out["novelty_filter"]
        assert block["exclude_code_ids"] == [1]
        assert block["exclude_code_names"] == ["Stress"]
        assert block["applies_to"] == "content"
        assert block["content_matches_excluded"] == 1
        assert block["files_with_all_matches_excluded"] == 1

    def test_a_novel_match_survives_the_filter(self, setup_server,
                                               qualcoder_db_path):
        _add_file(qualcoder_db_path, 9, "fresh.txt",
                  "A stressed passage nobody has coded yet.")
        _reopen(qualcoder_db_path)
        out = json.loads(server.search_files("stress", search_content=True,
                                             exclude_code_ids=[1]))
        assert [r["file_id"] for r in out["results"]] == [9]
        assert out["novelty_filter"]["files_with_all_matches_excluded"] == 1

    def test_the_filter_needs_content_search(self, setup_server):
        out = json.loads(server.search_files("stress", exclude_code_ids=[1]))
        assert out["error"] == (
            "exclude_code_ids filters content matches; call search_files "
            "with search_content=true to use it.")

    def test_matches_carry_absolute_anchors(self, setup_server,
                                            qualcoder_db_path):
        out = json.loads(server.search_files("stress", search_content=True))
        match = out["results"][0]["matches"][0]
        assert match["match_start"] == match["position"]
        assert match["match_end"] == match["match_start"] + len("stress")
        assert match["match_text"] == "stress"
        con = _con(qualcoder_db_path)
        text = con.execute("SELECT fulltext FROM source WHERE id=1"
                           ).fetchone()[0]
        con.close()
        assert text[match["match_start"]:match["match_end"]] == "stress"
        # preview_start is the absolute position of the preview's first
        # character, so any offset inside the preview maps back by
        # arithmetic
        preview = match["preview"].lstrip(".")
        assert text[match["preview_start"]:
                    match["preview_start"] + len(preview)] == preview

    def test_max_matches_per_file(self, setup_server, qualcoder_db_path):
        _add_file(qualcoder_db_path, 10, "many.txt", "word " * 40)
        _reopen(qualcoder_db_path)
        out = json.loads(server.search_files("word", search_content=True))
        entry = next(r for r in out["results"] if r["file_id"] == 10)
        assert entry["content_matches_found"] == 40
        assert entry["content_matches_shown"] == 5
        out = json.loads(server.search_files("word", search_content=True,
                                             max_matches_per_file=12))
        entry = next(r for r in out["results"] if r["file_id"] == 10)
        assert entry["content_matches_shown"] == 12
        assert entry["content_matches_found"] == 40

    @pytest.mark.parametrize("bad", [0, 51, -1, "five", True])
    def test_max_matches_per_file_bounds(self, setup_server, bad):
        out = json.loads(server.search_files("x", max_matches_per_file=bad))
        assert out["error"] == "max_matches_per_file must be between 1 and 50."

    def test_the_cap_is_spent_on_novel_matches(self, setup_server,
                                               qualcoder_db_path):
        """D4 3.1.5: the mask is applied BEFORE the per-file cap, so five
        already-coded occurrences do not use up the five slots."""
        text = "word " * 20
        _add_file(qualcoder_db_path, 11, "mixed.txt", text)
        for i in range(6):
            _add_coding(qualcoder_db_path, 60 + i, 1, 11, i * 5, i * 5 + 4)
        _reopen(qualcoder_db_path)
        out = json.loads(server.search_files("word", search_content=True,
                                             exclude_code_ids=[1]))
        entry = next(r for r in out["results"] if r["file_id"] == 11)
        assert entry["content_matches_found"] == 20
        assert entry["content_matches_excluded"] == 6
        assert entry["content_matches_shown"] == 5
        shown = [m["match_start"] for m in entry["matches"]]
        assert all(pos >= 30 for pos in shown), shown


# =============================================================================
# VALIDATION AND ERROR TEXTS (D4 3.7, verbatim)
# =============================================================================

class TestValidationTexts:

    def test_unknown_code_ids_refused_and_sorted(self, setup_server):
        out = json.loads(server.search_coded_text(
            "x", exclude_code_ids=[42, 41]))
        assert out["error"] == (
            "exclude_code_ids contains unknown code id(s): 41, 42. Use "
            "get_project_summary or export_codebook to list codes.")

    @pytest.mark.parametrize("bad", [["1"], [1.5], [True], [[1]], [0], [-3],
                                     "1,2"])
    def test_shape_refused(self, setup_server, bad):
        out = json.loads(server.search_coded_text("x", exclude_code_ids=bad))
        assert out["error"] == \
            "exclude_code_ids must be a list of positive integers."

    def test_the_cap(self, setup_server):
        out = json.loads(server.search_coded_text(
            "x", exclude_code_ids=list(range(1, 202))))
        assert out["error"] == "exclude_code_ids accepts at most 200 code ids."

    def test_empty_and_none_are_identical(self, setup_server):
        a = json.loads(server.search_coded_text("stress", exclude_code_ids=[]))
        b = json.loads(server.search_coded_text("stress"))
        assert a == b
        assert "novelty_filter" not in a

    def test_bad_strategy(self, setup_server):
        out = json.loads(server.get_coded_segments(1, strategy="random"))
        assert out["error"] == (
            "strategy must be one of: by_document, diverse_by_document, "
            "recent_first, sequential.")

    @pytest.mark.parametrize("bad", [0, -1, 50001, "800", True])
    def test_bad_max_chars(self, setup_server, bad):
        out = json.loads(server.get_coded_segments(1, max_chars=bad))
        assert out["error"] == (
            "max_chars must be a positive integer no greater than 50000.")

    def test_unknown_file_ids(self, setup_server):
        out = json.loads(server.get_coded_segments(1, file_ids=[9]))
        assert out["error"] == (
            "file_ids contains unknown file id(s): 9. Use search_files or "
            "the qualcoder://files/list resource to list files.")

    def test_bad_file_ids_shape(self, setup_server):
        out = json.loads(server.get_coded_segments(1, file_ids=["x"]))
        assert out["error"] == "file_ids must be a list of positive integers."


# =============================================================================
# CURSORS
# =============================================================================

@pytest.fixture
def paging_project(setup_server, qualcoder_db_path):
    """Five files, twelve codings of code 1, known dates and positions."""
    for fid in range(3, 8):
        _add_file(qualcoder_db_path, fid, f"file{fid}.txt",
                  "stress " * 60)
    ctid = 100
    for fid in range(3, 8):
        for n in range(2):
            _add_coding(qualcoder_db_path, ctid, 1, fid,
                        n * 20, n * 20 + 6,
                        text="stress",
                        date=f"2024-02-{(ctid % 20) + 1:02d} 10:00:00")
            ctid += 1
    _reopen(qualcoder_db_path)
    return qualcoder_db_path


class TestCursorWalk:

    @pytest.mark.parametrize("strategy", ["by_document",
                                          "diverse_by_document",
                                          "recent_first", "sequential"])
    def test_the_walk_equals_the_unpaged_result(self, paging_project,
                                                strategy):
        full = json.loads(server.get_coded_segments(1, limit=5000,
                                                    strategy=strategy))
        walked = []
        cursor = None
        pages = 0
        while True:
            page = json.loads(server.get_coded_segments(
                1, limit=3, strategy=strategy, cursor=cursor))
            walked.extend(s["id"] for s in page["segments"])
            pages += 1
            cursor = page["page"]["next_cursor"]
            if not page["page"]["has_more"]:
                break
            assert pages < 20, "walk did not terminate"
        assert len(walked) == len(set(walked)), "a row was returned twice"
        if strategy == "diverse_by_document":
            # pages are re-sorted for presentation, so compare as sets
            assert set(walked) == {s["id"] for s in full["segments"]}
        else:
            assert walked == [s["id"] for s in full["segments"]]

    def test_search_coded_text_walk(self, paging_project):
        full = json.loads(server.search_coded_text("stress", limit=5000))
        walked = []
        cursor = None
        while True:
            page = json.loads(server.search_coded_text("stress", limit=2,
                                                       cursor=cursor))
            walked.extend(r["id"] for r in page["results"])
            cursor = page["page"]["next_cursor"]
            if not page["page"]["has_more"]:
                break
        assert walked == [r["id"] for r in full["results"]]
        assert len(walked) == len(set(walked))

    def test_search_files_walk(self, paging_project):
        full = json.loads(server.search_files("file", limit=5000))
        walked = []
        cursor = None
        while True:
            page = json.loads(server.search_files("file", limit=2,
                                                  cursor=cursor))
            walked.extend(r["file_id"] for r in page["results"])
            cursor = page["page"]["next_cursor"]
            if not page["page"]["has_more"]:
                break
        assert walked == [r["file_id"] for r in full["results"]]

    def test_next_request_round_trip(self, paging_project):
        page = json.loads(server.search_coded_text("stress", limit=2))
        assert page["request"]["tool"] == "search_coded_text"
        assert page["request"]["arguments"]["cursor"] is None
        nxt = page["next_request"]
        assert nxt["arguments"]["cursor"] == page["page"]["next_cursor"]
        second = json.loads(server.search_coded_text(**nxt["arguments"]))
        assert second["results"]
        assert {r["id"] for r in second["results"]} & \
            {r["id"] for r in page["results"]} == set()

    def test_returned_so_far_accumulates(self, paging_project):
        cursor = None
        seen = 0
        while True:
            page = json.loads(server.get_coded_segments(1, limit=4,
                                                        cursor=cursor))
            seen += page["page"]["returned"]
            assert page["page"]["returned_so_far"] == seen
            cursor = page["page"]["next_cursor"]
            if not page["page"]["has_more"]:
                assert page["page"]["exhaustive"] is True
                break


class TestCursorRobustness:

    ERRORS = {
        "search_coded_text": (
            "cursor is not valid for search_coded_text with these "
            "arguments. Call the tool again without cursor to start from "
            "the beginning."),
        "get_coded_segments": (
            "cursor is not valid for get_coded_segments with these "
            "arguments. Call the tool again without cursor to start from "
            "the beginning."),
        "search_files": (
            "cursor is not valid for search_files with these arguments. "
            "Call the tool again without cursor to start from the "
            "beginning."),
    }

    def _first_cursor(self, tool="search_coded_text"):
        if tool == "search_coded_text":
            page = json.loads(server.search_coded_text("stress", limit=1))
        elif tool == "get_coded_segments":
            page = json.loads(server.get_coded_segments(1, limit=1))
        else:
            page = json.loads(server.search_files("file", limit=1))
        return page["page"]["next_cursor"]

    def test_a_flipped_character(self, paging_project):
        token = self._first_cursor()
        broken = token[:-1] + ("A" if token[-1] != "A" else "B")
        out = json.loads(server.search_coded_text("stress", limit=1,
                                                  cursor=broken))
        assert out["error"] == self.ERRORS["search_coded_text"]
        assert broken[4:] not in json.dumps(out)

    def test_a_cursor_from_another_tool(self, paging_project):
        token = self._first_cursor("get_coded_segments")
        out = json.loads(server.search_coded_text("stress", cursor=token))
        assert out["error"] == self.ERRORS["search_coded_text"]

    @pytest.mark.parametrize("kwargs", [
        {"limit": 2}, {"coder": "TestCoder"}, {"query": "cope"},
        {"exclude_code_ids": [2]},
    ])
    def test_a_changed_argument_invalidates_the_cursor(self, paging_project,
                                                       kwargs):
        page = json.loads(server.search_coded_text("stress", limit=1))
        token = page["page"]["next_cursor"]
        call = {"query": "stress", "limit": 1, "cursor": token}
        call.update(kwargs)
        out = json.loads(server.search_coded_text(**call))
        assert out["error"] == self.ERRORS["search_coded_text"]

    def test_an_over_long_cursor(self, paging_project):
        out = json.loads(server.search_coded_text(
            "stress", cursor="c1." + "A" * 1100))
        assert out["error"] == "cursor is too long (limit 1024 characters)."

    @pytest.mark.parametrize("token", [
        "garbage", "c2.abc", "c1.", "c1.!!!not base64!!!",
        "c1.eyJ0IjogInNjdCJ9",          # valid base64, wrong shape
    ])
    def test_assorted_rubbish(self, paging_project, token):
        out = json.loads(server.search_coded_text("stress", cursor=token))
        assert out["error"] == self.ERRORS["search_coded_text"]

    def test_the_token_is_never_echoed(self, paging_project):
        token = "c1." + "Zm9yZ2VkLXRva2VuLXdpdGgtYS1zZWNyZXQ"
        out = json.loads(server.search_coded_text("stress", cursor=token))
        assert "Zm9yZ2Vk" not in json.dumps(out)

    def test_tokens_are_url_safe_ascii(self, paging_project):
        for token in (self._first_cursor("search_coded_text"),
                      self._first_cursor("get_coded_segments"),
                      self._first_cursor("search_files")):
            assert token is None or re.fullmatch(r"c1\.[A-Za-z0-9_-]+",
                                                 token), token

    def test_order_and_duplicates_in_the_id_list_do_not_matter(
            self, paging_project):
        a = json.loads(server.search_coded_text(
            "stress", limit=1, exclude_code_ids=[2, 1, 2]))
        b = json.loads(server.search_coded_text(
            "stress", limit=1, exclude_code_ids=[1, 2]))
        assert a["page"]["next_cursor"] == b["page"]["next_cursor"]


class TestCursorDrift:

    def test_an_insert_before_the_key_neither_duplicates_nor_skips(
            self, paging_project):
        page = json.loads(server.get_coded_segments(1, limit=4,
                                                    strategy="sequential"))
        first_ids = [s["id"] for s in page["segments"]]
        _add_coding(paging_project, 5, 1, 1, 5, 9)   # ctid 5 sorts first
        _reopen(paging_project)
        page2 = json.loads(server.get_coded_segments(
            1, limit=4, strategy="sequential",
            cursor=page["page"]["next_cursor"]))
        second_ids = [s["id"] for s in page2["segments"]]
        assert set(first_ids) & set(second_ids) == set()
        assert 5 not in second_ids          # it sorts before the key

    def test_deleting_the_keyed_row_resumes_at_its_successor(
            self, paging_project):
        page = json.loads(server.get_coded_segments(1, limit=3,
                                                    strategy="sequential"))
        last = page["segments"][-1]["id"]
        cursor = page["page"]["next_cursor"]
        con = _con(paging_project)
        con.execute("DELETE FROM code_text WHERE ctid = ?", (last,))
        con.commit()
        con.close()
        _reopen(paging_project)
        page2 = json.loads(server.get_coded_segments(
            1, limit=3, strategy="sequential", cursor=cursor))
        assert last not in [s["id"] for s in page2["segments"]]
        assert page2["segments"], "the walk continued past the deleted row"

    def test_the_database_stamp_is_a_labelled_heuristic(self, paging_project,
                                                        monkeypatch):
        page = json.loads(server.get_coded_segments(1, limit=2))
        cursor = page["page"]["next_cursor"]
        assert "database_changed_since_cursor" not in page

        real = server.database_stamp

        def moved(path):
            stamp = real(path)
            return [stamp[0] + 1, stamp[1]] if stamp else stamp

        # os.stat through monkeypatch, never a sleep: the stamp is a
        # heuristic about the file, and the test says so without waiting
        # for a clock (D4 6, point 22).
        monkeypatch.setattr(server, "database_stamp", moved)
        page2 = json.loads(server.get_coded_segments(1, limit=2,
                                                     cursor=cursor))
        assert page2["database_changed_since_cursor"] is True
        assert page2["database_changed_note"] == cursors.DATABASE_CHANGED_NOTE
        assert page2["segments"], "the page is still correct"


class TestRestartResilience:

    def test_a_cursor_survives_a_process_recycle(self, paging_project):
        page = json.loads(server.get_coded_segments(1, limit=3))
        cursor = page["page"]["next_cursor"]
        # The host recycled the server process between turns
        server.db = None
        server.current_project_path = None
        json.loads(server.select_project(paging_project))
        page2 = json.loads(server.get_coded_segments(1, limit=3,
                                                     cursor=cursor))
        assert page2["segments"]
        assert {s["id"] for s in page2["segments"]} & \
            {s["id"] for s in page["segments"]} == set()

    def test_the_same_cursor_twice_gives_the_same_page(self, paging_project):
        page = json.loads(server.search_coded_text("stress", limit=2))
        cursor = page["page"]["next_cursor"]
        a = server.search_coded_text("stress", limit=2, cursor=cursor)
        server.db = None
        server.current_project_path = None
        json.loads(server.select_project(paging_project))
        b = server.search_coded_text("stress", limit=2, cursor=cursor)
        assert a == b

    def test_nothing_is_stored_for_a_cursor(self, paging_project, tmp_path):
        """Option B by construction: no session file, no state file, no
        module attribute grows when a cursor is minted."""
        sessions = Path(server.session_manager.storage_dir)
        before = sorted(p.name for p in sessions.glob("*")) \
            if sessions.exists() else []
        json.loads(server.get_coded_segments(1, limit=2))
        after = sorted(p.name for p in sessions.glob("*")) \
            if sessions.exists() else []
        assert before == after
        assert not [n for n in dir(cursors)
                    if not n.startswith("__") and "cache" in n.lower()]


# =============================================================================
# SAMPLING STRATEGIES AND THE BUDGET
# =============================================================================

class TestStrategies:

    def test_sequential_is_ctid_order(self, paging_project):
        out = json.loads(server.get_coded_segments(1, strategy="sequential"))
        ids = [s["id"] for s in out["segments"]]
        assert ids == sorted(ids)

    def test_recent_first_is_date_then_ctid_descending(self, paging_project):
        out = json.loads(server.get_coded_segments(1,
                                                   strategy="recent_first"))
        keys = [(s["date"] or "", s["id"]) for s in out["segments"]]
        assert keys == sorted(keys, reverse=True)

    def test_recent_first_documents_the_heuristic(self):
        doc = " ".join((server.get_coded_segments.__doc__ or "").split())
        assert "compared as stored text" in doc
        assert "heuristic" in doc

    def test_diverse_takes_one_per_file_before_a_second(self,
                                                        paging_project):
        page = json.loads(server.get_coded_segments(
            1, limit=5, strategy="diverse_by_document"))
        files = [s["file_id"] for s in page["segments"]]
        assert len(set(files)) == len(files), files
        # the page is presented in document order
        assert files == sorted(files)

    def test_by_document_is_the_default_and_total(self, paging_project):
        out = json.loads(server.get_coded_segments(1))
        assert out["selection"]["strategy"] == "by_document"
        keys = [(s["file_name"] or "", s["file_id"], s["position_start"],
                 s["position_end"], s["id"]) for s in out["segments"]]
        assert keys == sorted(keys)

    def test_file_ids_restrict_the_sample(self, paging_project):
        out = json.loads(server.get_coded_segments(1, file_ids=[3, 4]))
        assert {s["file_id"] for s in out["segments"]} == {3, 4}
        assert out["selection"]["file_ids"] == [3, 4]


class TestCharacterBudget:

    def _three_long_codings(self, project_path):
        _add_file(project_path, 20, "long.txt", "z" * 12000)
        for i in range(3):
            _add_coding(project_path, 200 + i, 1, 20, i * 3000,
                        (i + 1) * 3000, text="z" * 3000,
                        memo="m" * 500)
        _reopen(project_path)

    def test_the_budget_stops_the_page_and_the_cursor_resumes(
            self, setup_server, qualcoder_db_path):
        self._three_long_codings(qualcoder_db_path)
        page = json.loads(server.get_coded_segments(
            1, file_ids=[20], max_chars=8000, strategy="sequential"))
        assert page["segment_count"] == 2
        assert page["selection"]["hit_max_char_limit"] is True
        assert page["selection"]["chars_returned"] == 6000
        assert page["page"]["has_more"] is True
        page2 = json.loads(server.get_coded_segments(
            1, file_ids=[20], max_chars=8000, strategy="sequential",
            cursor=page["page"]["next_cursor"]))
        assert [s["id"] for s in page2["segments"]] == [202]

    def test_memos_do_not_count_towards_the_budget(self, setup_server,
                                                   qualcoder_db_path):
        self._three_long_codings(qualcoder_db_path)
        page = json.loads(server.get_coded_segments(
            1, file_ids=[20], max_chars=6000, strategy="sequential"))
        assert page["segment_count"] == 2
        assert page["selection"]["chars_returned"] == 6000

    def test_the_zero_progress_rule(self, setup_server, qualcoder_db_path):
        """A deviation from ai_mcp_server.py:5367-5372: the first segment
        of a page is always returned, truncated if need be, so a caller is
        never handed a cursor that returns nothing."""
        _add_file(qualcoder_db_path, 21, "huge.txt", "y" * 25000)
        _add_coding(qualcoder_db_path, 210, 1, 21, 0, 20000,
                    text="y" * 20000)
        _add_coding(qualcoder_db_path, 211, 1, 21, 20000, 20010,
                    text="y" * 10)
        _reopen(qualcoder_db_path)
        page = json.loads(server.get_coded_segments(
            1, file_ids=[21], max_chars=100, strategy="sequential"))
        assert page["segment_count"] == 1
        segment = page["segments"][0]
        assert segment["id"] == 210
        assert segment["text_truncated"] is True
        assert segment["text_full_length"] == 20000
        assert len(segment["text"]) == 100
        assert segment["position_start"] == 0
        assert segment["position_end"] == 20000
        assert page["page"]["has_more"] is True
        page2 = json.loads(server.get_coded_segments(
            1, file_ids=[21], max_chars=100, strategy="sequential",
            cursor=page["page"]["next_cursor"]))
        assert [s["id"] for s in page2["segments"]] == [211]

    def test_no_budget_keeps_the_old_behaviour(self, setup_server,
                                               qualcoder_db_path):
        self._three_long_codings(qualcoder_db_path)
        page = json.loads(server.get_coded_segments(1, file_ids=[20]))
        assert page["segment_count"] == 3
        assert page["selection"]["hit_max_char_limit"] is False
        assert all("text_truncated" not in s for s in page["segments"])

    def test_the_description_offers_the_upstream_default(self):
        doc = " ".join((server.get_coded_segments.__doc__ or "").split())
        assert "max_chars=8000" in doc
        assert "QualCoder 4.0's assistant uses 8000 per code" in doc


# =============================================================================
# VISIBILITY: THE EXCLUSION SOURCE IS NOT AN ORACLE
# =============================================================================

class TestVisibilityOfTheMask:

    @pytest.fixture
    def hidden(self, setup_server, qualcoder_db_path):
        from test_qc40_visibility import _apply_visibility_schema, HIDDEN
        _apply_visibility_schema(qualcoder_db_path)
        _reopen(qualcoder_db_path)
        return qualcoder_db_path, HIDDEN

    def test_a_hidden_coders_coding_does_not_exclude_a_passage(self,
                                                               hidden):
        """D4 5.1(a): upstream's filter reads the base table
        (ai_mcp_server.py:5228), which would let a hidden coder's work
        silently suppress a passage and so answer "is there hidden work
        here?". Ours reads what the caller may see."""
        project, _ = hidden
        # ctid 5 is the hidden coder's cid-2 coding on [30, 40)
        out = json.loads(server.search_files("stressed", search_content=True,
                                             exclude_code_ids=[2]))
        assert [r["file_id"] for r in out["results"]] == [1]
        assert out["novelty_filter"]["coder_visibility"] == "honoured"
        assert out["novelty_filter"]["content_matches_excluded"] == 0

    def test_the_named_coder_override_adds_that_coders_own_rows(self,
                                                                hidden):
        project, hidden_name = hidden
        out = json.loads(server.search_coded_text(
            "stressed", coder=hidden_name, exclude_code_ids=[2]))
        assert out["novelty_filter"]["coder_visibility"] == \
            "honoured_plus_named_coder"
        # the hidden coder's own cid-2 row [30,40) now excludes its cid-1
        # row [24,55), which overlaps it
        assert out["result_count"] == 0

    def test_without_the_capability_the_block_says_so(self, setup_server,
                                                      qualcoder_db_path):
        out = json.loads(server.search_coded_text("stress",
                                                  exclude_code_ids=[2]))
        assert out["novelty_filter"]["coder_visibility"] == "not_applicable"

    def test_an_unqueryable_view_fails_closed(self, setup_server,
                                              qualcoder_db_path):
        """The view exists and the capability probes true, but the table
        behind it is gone: the call is an error, never an empty mask."""
        from test_qc40_visibility import _apply_visibility_schema
        _apply_visibility_schema(qualcoder_db_path)
        _reopen(qualcoder_db_path)
        assert server.db.capabilities.has_coder_visibility is True
        con = _con(qualcoder_db_path)
        con.execute("DROP TABLE coder_names")
        con.commit()
        con.close()
        out = json.loads(server.search_coded_text("stress",
                                                  exclude_code_ids=[1]))
        assert "error" in out
        assert "novelty filter" in out["error"].lower() or \
            "search" in out["error"].lower()

    def test_no_marker_reaches_any_page(self, setup_server,
                                        qualcoder_db_path):
        con = _con(qualcoder_db_path)
        con.execute("UPDATE code_text SET memo = ? WHERE ctid = 1",
                    ("public ##### private text",))
        con.commit()
        con.close()
        _reopen(qualcoder_db_path)
        for raw in (server.search_coded_text("stress"),
                    server.get_coded_segments(1),
                    server.search_files("stress", search_content=True)):
            assert "#####" not in raw
            assert "private text" not in raw


# =============================================================================
# SQL AND PLATFORM DISCIPLINE
# =============================================================================

class TestImplementationDiscipline:

    def test_no_window_functions_or_row_values(self):
        """D4 3.2.6: keyset predicates are expanded lexicographic form, so
        no SQLite version floor is introduced and no build can order
        differently."""
        from qualcoder_mcp import database
        text = Path(database.__file__).read_text(encoding="utf-8")
        assert "ROW_NUMBER" not in text.upper()
        assert "OVER (" not in text.upper()
        # a row-value comparison would look like "(a, b) > (?, ?)"
        assert not re.search(r"\(\s*\w+\s*,\s*\w+\s*\)\s*>\s*\(", text)

    def test_the_cursor_codec_is_not_the_authorisation_codec(self):
        """H1 and D4 3.11: positions and permissions are different
        things, so they do not share a codec or a prefix."""
        assert cursors.CURSOR_PREFIX == "c1."
        assert not hasattr(cursors, "issue")
        assert not hasattr(cursors, "verify")

    def test_the_core_toolset_is_unchanged_by_this_item(self):
        assert "search_files" in server.CORE_TOOLSET
        assert "get_coded_segments" in server.CORE_TOOLSET
        assert "search_coded_text" in server.CORE_TOOLSET
