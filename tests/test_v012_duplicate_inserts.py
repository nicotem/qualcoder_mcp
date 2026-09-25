# SPDX-License-Identifier: LGPL-3.0-or-later
"""v0.12 release preparation, carried defect (b): duplicate inserts meet
QualCoder's real UNIQUE constraints.

Flagship fix round 1 (F-8) gave every shared fixture the constraints
QualCoder's own schema carries (`__main__.py:1800-1808` at 9bddf17:
`annotation unique(fid,pos0,pos1,owner)` and
`attribute unique(name,attr_type,id)`; identical at the 3.8.2 tag,
`__main__.py:2351-2387`). The re-verification then observed that no test
outside the flagship's own fixture drove a duplicate insert, so
`add_annotation`, `update_annotation` and `set_attribute` were pinned only
by their app-side pre-checks: the branch that answers when the CONSTRAINT
refuses had never fired in the suite. These tests fire it.

The pre-check and the constraint guard the same fact from two sides, and
the pre-check normally wins, so the constraint path is reached here by
blinding the pre-check: the write connection is built with a
`sqlite3.Connection` subclass whose `execute` answers the pre-check's own
SELECT with no rows, which is exactly what the pre-check sees when a
duplicate lands in the window between the check and the INSERT. Everything
else runs for real: the real schema, the real INSERT, the real error branch,
the real rollback, the real downgrade back to read-only.
"""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

import qualcoder_mcp.server as server
from qualcoder_mcp import database
from qualcoder_mcp.project_settings import DEFAULT_AI_CODER_NAME

# Bound at import, before any test can route sqlite3.connect through the
# blinding factory below: the helpers here must read the truth.
REAL_CONNECT = sqlite3.connect

FIXTURE_CONSTRAINTS = {
    "annotation": "unique(fid,pos0,pos1,owner)",
    "attribute": "unique(name,attr_type,id)",
}

# The pre-check statements, as `database.py` spells them. A substring of
# each, distinctive enough that no helper query in this module carries it.
ANNOTATION_PRECHECK = "FROM annotation WHERE fid = ? AND"
ATTRIBUTE_PRECHECK = "SELECT attrid, value FROM attribute"

# What the two guards say, so a test can tell which one answered.
ANNOTATION_CONSTRAINT_TEXT = "An annotation already exists on this exact span"
ATTRIBUTE_CONSTRAINT_TEXT = "Failed to set attribute value"


def _ddl(project_path, table):
    with closing(REAL_CONNECT(str(Path(project_path) / "data.qda"))) as conn:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,)).fetchone()
    return " ".join(row[0].lower().split())


def _rows(project_path, sql, params=()):
    with closing(REAL_CONNECT(str(Path(project_path) / "data.qda"))) as conn:
        return conn.execute(sql, params).fetchall()


def _add(file_id, start, end, memo):
    out = json.loads(server.add_annotation(file_id, start, end, memo,
                                           create_backup=False))
    assert out.get("success") is True, out
    return out["annotation"]["annotation_id"]


class Blinded(sqlite3.Connection):
    """A connection whose pre-check reads see no rows.

    `MARKERS` holds substrings of the SQL to blind; a statement carrying
    one is replaced by a query that returns nothing, and `hits` counts how
    often that happened, so a test can prove the blind was consulted
    rather than passing because the pre-check was never reached.
    """

    MARKERS = ()
    hits = 0

    def execute(self, sql, *args, **kwargs):
        if any(marker in sql for marker in type(self).MARKERS):
            type(self).hits += 1
            return super().execute("SELECT 1 WHERE 0")
        return super().execute(sql, *args, **kwargs)


@pytest.fixture
def blind(monkeypatch):
    """Route every connection the code opens from now on through
    `Blinded`. Nothing is blinded until the returned `arm` is called with
    markers; `arm()` with none disarms it."""
    def connect(*args, **kwargs):
        kwargs.setdefault("factory", Blinded)
        return REAL_CONNECT(*args, **kwargs)

    Blinded.MARKERS = ()
    Blinded.hits = 0
    monkeypatch.setattr(database.sqlite3, "connect", connect)

    def arm(*markers):
        Blinded.MARKERS = markers
        Blinded.hits = 0

    yield arm
    Blinded.MARKERS = ()
    Blinded.hits = 0


class TestTheFixturesCarryTheConstraints:
    """Anti-vacuity for everything below: the schema these tests run
    against enforces the two keys, as QualCoder's does, and SQLite itself
    is what refuses the raw duplicate."""

    @pytest.mark.parametrize("table", sorted(FIXTURE_CONSTRAINTS))
    def test_the_stock_fixture_declares_the_key(self, qualcoder_db_path,
                                                table):
        assert FIXTURE_CONSTRAINTS[table] in _ddl(qualcoder_db_path, table)

    def test_sqlite_refuses_a_raw_duplicate_annotation(self,
                                                       qualcoder_db_path):
        with closing(REAL_CONNECT(
                str(Path(qualcoder_db_path) / "data.qda"))) as conn:
            conn.execute(
                "INSERT INTO annotation (fid, pos0, pos1, memo, owner, date) "
                "VALUES (1, 0, 10, 'a', 'X', 'd')")
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO annotation (fid, pos0, pos1, memo, owner, "
                    "date) VALUES (1, 0, 10, 'b', 'X', 'd')")

    def test_sqlite_refuses_a_raw_duplicate_attribute_row(self,
                                                          qualcoder_db_path):
        # The fixture already holds ('Age', 'case', id 1) = '30'.
        with closing(REAL_CONNECT(
                str(Path(qualcoder_db_path) / "data.qda"))) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO attribute (name, value, id, attr_type, "
                    "date, owner) VALUES ('Age', '31', 1, 'case', 'd', 'X')")


class TestAddAnnotationAgainstTheConstraint:

    def test_the_pre_check_answers_first_and_names_the_edit_path(
            self, setup_server, qualcoder_db_path):
        """The control: the pre-check's text is distinguishable from the
        constraint's, so the test below can tell which guard spoke."""
        anid = _add(1, 0, 10, "first")
        out = json.loads(server.add_annotation(1, 0, 10, "second",
                                               create_backup=False))
        assert f"already exists on this exact span (anid={anid})" \
            in out["error"]
        assert "update_annotation" in out["error"]
        assert out["error"] != ANNOTATION_CONSTRAINT_TEXT

    def test_a_duplicate_that_slips_past_the_pre_check_meets_the_constraint(
            self, setup_server, qualcoder_db_path, blind):
        anid = _add(1, 0, 10, "first")

        blind(ANNOTATION_PRECHECK)
        out = json.loads(server.add_annotation(1, 0, 10, "second",
                                               create_backup=False))
        # The pre-check ran blind (both the exact-span read and the
        # overlap read carry the marker), so the INSERT was reached and
        # the constraint is what refused.
        assert Blinded.hits >= 1
        assert out["error"] == ANNOTATION_CONSTRAINT_TEXT
        assert "success" not in out
        rows = _rows(qualcoder_db_path,
                     "SELECT anid, memo FROM annotation ORDER BY anid")
        assert rows == [(anid, "first")]

        # The failed INSERT was rolled back and the connection handed back
        # read-only in the usual way: the next write goes through.
        blind()
        again = json.loads(server.add_annotation(1, 20, 30, "third",
                                                 create_backup=False))
        assert again["success"] is True
        assert _rows(qualcoder_db_path,
                     "SELECT COUNT(*) FROM annotation") == [(2,)]


class TestSetAttributeAgainstTheConstraint:

    def test_a_duplicate_row_that_slips_past_the_pre_check_is_refused(
            self, setup_server, qualcoder_db_path, blind):
        """`set_attribute_value` is insert-if-missing then update. With
        the existence read blinded, the INSERT lands on a key the fixture
        already holds and `unique(name,attr_type,id)` refuses it.

        The constraint path has no message of its own: `IntegrityError`
        is a `sqlite3.Error`, so it reaches `_raise_query_error` and the
        generic text. That is the behaviour as it ships and is pinned as
        such; a duplicate-specific text would be a behaviour change and
        is recorded for v0.13 in the release-prep report.
        """
        key = ("SELECT attrid, value FROM attribute WHERE name = 'Age' "
               "AND attr_type = 'case' AND id = 1")
        assert _rows(qualcoder_db_path, key) == [(1, "30")]

        blind(ATTRIBUTE_PRECHECK)
        out = json.loads(server.set_attribute("case", 1, "Age", "31",
                                              create_backup=False))
        assert Blinded.hits >= 1
        assert out["error"] == ATTRIBUTE_CONSTRAINT_TEXT
        assert "success" not in out
        # Nothing changed: one row, the old value, and no second row.
        assert _rows(qualcoder_db_path, key) == [(1, "30")]
        assert _rows(qualcoder_db_path,
                     "SELECT COUNT(*) FROM attribute WHERE name = 'Age' "
                     "AND attr_type = 'case' AND id = 1") == [(1,)]

        # And the same call succeeds once the pre-check can see again:
        # the update branch, on the existing row.
        blind()
        ok = json.loads(server.set_attribute("case", 1, "Age", "31",
                                             create_backup=False))
        assert ok["success"] is True
        assert ok["attribute"]["row_created"] is False
        assert ok["attribute"]["previous_value"] == "30"
        assert _rows(qualcoder_db_path, key) == [(1, "31")]


class TestUpdateAnnotationUnderTheConstraint:
    """`update_annotation` has no duplicate pre-check because it never
    inserts: an edit touches memo and date, and clearing deletes the row.
    Against the real key these two facts are what keep it out of the
    constraint's way, and both are now pinned on the constrained fixture."""

    def test_editing_the_note_leaves_the_unique_key_alone(
            self, setup_server, qualcoder_db_path):
        first = _add(1, 0, 10, "first")
        second = _add(1, 20, 30, "second")
        out = json.loads(server.update_annotation(first, "first, edited",
                                                  create_backup=False))
        assert out["success"] is True and out["updated"] is True
        rows = _rows(qualcoder_db_path,
                     "SELECT anid, fid, pos0, pos1, owner, memo "
                     "FROM annotation ORDER BY anid")
        assert [row[:5] for row in rows] == [
            (first, 1, 0, 10, DEFAULT_AI_CODER_NAME),
            (second, 1, 20, 30, DEFAULT_AI_CODER_NAME)]
        assert rows[0][5] == "first, edited"
        # The key those rows carry is enforced on this fixture: a raw
        # duplicate of the edited row is refused by SQLite itself.
        with closing(REAL_CONNECT(
                str(Path(qualcoder_db_path) / "data.qda"))) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO annotation (fid, pos0, pos1, memo, owner, "
                    "date) VALUES (1, 0, 10, 'dup', ?, 'd')",
                    (DEFAULT_AI_CODER_NAME,))

    def test_clearing_deletes_the_row_so_the_span_is_free_again(
            self, setup_server, qualcoder_db_path):
        """Under the constraint a row left behind by a clear would refuse
        the next annotation on that span; the delete has to be real."""
        first = _add(1, 0, 10, "first")
        out = json.loads(server.update_annotation(first, "",
                                                  create_backup=False))
        assert out["success"] is True
        assert out["deleted_because_cleared"] is True
        assert _rows(qualcoder_db_path,
                     "SELECT COUNT(*) FROM annotation") == [(0,)]
        again = _add(1, 0, 10, "again")
        # `anid` is INTEGER PRIMARY KEY without AUTOINCREMENT, so SQLite
        # may hand the freed rowid out again; the id is not what proves
        # the delete, the row count and the memo are.
        assert _rows(qualcoder_db_path,
                     "SELECT anid, memo FROM annotation") == [(again, "again")]
