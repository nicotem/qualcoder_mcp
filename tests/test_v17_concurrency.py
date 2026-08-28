"""v17 WS5: concurrency posture vs lockless QualCoder master (T17/T18, C7).

Master removed the project_in_use.lock protocol entirely, so an open
4.0 build cannot be detected. C7 compensates for the one corrupting
write class: text-anchored writes re-verify, INSIDE the write
transaction, that source.fulltext still matches what positions were
validated against. These tests inject the undetectable-editor race
(a separate connection mutating fulltext mid-write) and pin rollback,
the structured error, and unchanged row counts.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server
from qualcoder_mcp.database import QualcoderDatabase

FULLTEXT = ("This is interview text. I feel stressed about deadlines. "
            "I cope by exercising.")


def _mutate_fulltext(project_path, new_text):
    conn = sqlite3.connect(str(Path(project_path) / "data.qda"))
    conn.execute("UPDATE source SET fulltext = ? WHERE id = 1", (new_text,))
    conn.commit()
    conn.close()


def _counts(project_path):
    conn = sqlite3.connect(str(Path(project_path) / "data.qda"))
    out = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
           for t in ("code_text", "annotation", "code_name")}
    conn.close()
    return out


def _make_session(setup_server):
    out = server.analyze_for_coding([1])
    return out.split("Session ID: `")[1].split("`")[0]


class TestT18ApplyCodingsPrecondition:

    def test_editor_race_rolls_back_apply(self, setup_server,
                                          qualcoder_db_path, monkeypatch):
        """The exact master-editor shape: fulltext rewritten between
        validation and the write transaction. Everything rolls back."""
        sid = _make_session(setup_server)
        out = json.loads(server.record_suggestions(sid, [{
            "file_id": 1, "code_name": "Stress",
            "segment_text": "I feel stressed about deadlines",
        }]))
        guid = out["recorded"][0]["guid"]
        server.update_suggestion_status(sid, approve=[guid])
        before = _counts(qualcoder_db_path)

        real_add_coding = QualcoderDatabase.add_coding
        state = {"mutated": False}

        def racing_add_coding(self, *args, **kwargs):
            if not state["mutated"]:
                state["mutated"] = True
                # tail-append keeps the coded slice byte-identical, so
                # add_coding's per-row seltext check passes and only the
                # C7 whole-file fingerprint can catch the rewrite
                _mutate_fulltext(qualcoder_db_path, FULLTEXT + " APPENDED")
            return real_add_coding(self, *args, **kwargs)

        monkeypatch.setattr(QualcoderDatabase, "add_coding",
                            racing_add_coding)
        result = server.apply_codings(sid, create_backup=False)
        data = json.loads(result) if result.strip().startswith("{") else None
        blob = result.lower()
        assert "changed while this write" in blob
        assert "traceback" not in blob
        # nothing landed; connection healthy and read-only again
        after = _counts(qualcoder_db_path)
        assert after["code_text"] == before["code_text"]
        assert server.db.read_only is True
        assert server.db.conn.in_transaction is False
        # suggestion NOT marked applied (retryable)
        session = server.session_manager.load_session(sid)
        assert session.get_suggestion_by_guid(guid).status == "approved"

    def test_no_race_applies_normally(self, setup_server,
                                      qualcoder_db_path):
        sid = _make_session(setup_server)
        out = json.loads(server.record_suggestions(sid, [{
            "file_id": 1, "code_name": "Stress",
            "segment_text": "I feel stressed about deadlines",
        }]))
        server.update_suggestion_status(
            sid, approve=[out["recorded"][0]["guid"]])
        result = server.apply_codings(sid, create_backup=False)
        assert "CODINGS APPLIED" in result


class TestT18AnnotationPrecondition:

    def test_stale_snapshot_detected(self, setup_server, qualcoder_db_path,
                                     monkeypatch):
        """add_annotation validated positions against a snapshot the DB
        no longer holds: the in-transaction re-check refuses."""
        before = _counts(qualcoder_db_path)
        real_get = QualcoderDatabase.get_file_content

        def stale_get(self, file_id):
            fc = real_get(self, file_id)
            if fc and self.read_only is False:
                # the write path sees a STALE snapshot (editor raced us)
                fc = dict(fc)
                fc["content"] = (fc.get("content") or "") + " EDITED"
            return fc

        monkeypatch.setattr(QualcoderDatabase, "get_file_content", stale_get)
        out = json.loads(server.add_annotation(1, 0, 10, "note",
                                               create_backup=False))
        assert "changed while this write" in out["error"]
        assert _counts(qualcoder_db_path)["annotation"] == before["annotation"]
        assert server.db.read_only is True
        assert server.db.conn.in_transaction is False

    def test_normal_annotation_unaffected(self, setup_server,
                                          qualcoder_db_path):
        out = json.loads(server.add_annotation(1, 0, 10, "note",
                                               create_backup=False))
        assert out.get("success") is True


class TestT18CreateProposedCodesPrecondition:

    def test_editor_race_rolls_back_proposal_codings(self, setup_server,
                                                     qualcoder_db_path,
                                                     monkeypatch):
        sid = _make_session(setup_server)
        out = json.loads(server.propose_codes(sid, [{
            "name": "Deadline pressure",
            "example_segments": [{
                "file_id": 1,
                "segment_text": "I feel stressed about deadlines"}],
        }]))
        guid = out["recorded"][0]["guid"]
        server.update_proposal_status(sid, approve=[guid])
        before = _counts(qualcoder_db_path)

        real_add_code = QualcoderDatabase.add_code
        state = {"mutated": False}

        def racing_add_code(self, *args, **kwargs):
            if not state["mutated"]:
                state["mutated"] = True
                _mutate_fulltext(qualcoder_db_path, FULLTEXT + " APPENDED")
            return real_add_code(self, *args, **kwargs)

        monkeypatch.setattr(QualcoderDatabase, "add_code", racing_add_code)
        out = json.loads(server.create_proposed_codes(
            sid, apply_coded_segments=True, create_backup=False))
        assert "changed while this write" in out["error"]
        after = _counts(qualcoder_db_path)
        assert after == before                # code AND codings rolled back
        assert server.db.read_only is True
        assert server.db.conn.in_transaction is False
        # proposal not burned: still approved, retryable
        session = server.session_manager.load_session(sid)
        assert session.get_proposal_by_guid(guid).status == "approved"


class TestT17DocsPosture:

    def test_write_docstrings_name_the_master_gap(self):
        # P1-5 posture: 4.0 has no lock file; detection is best-effort
        # heuristics, and every write docstring says so
        for tool in (server.apply_codings, server.set_memo,
                     server.import_text_file, server.delete_coding,
                     server.merge_codes, server.create_code):
            doc = tool.__doc__ or ""
            assert "best-effort" in doc, tool.__name__
            assert "4.0" in doc, tool.__name__

    def test_select_project_names_the_limitation(self, setup_server,
                                                 qualcoder_db_path,
                                                 tmp_path):
        import shutil
        dest = tmp_path / "warn.qda"
        shutil.copytree(qualcoder_db_path, dest)
        out = json.loads(server.select_project(str(dest)))
        warning = out.get("warning", "")
        # With no heuristic signals the limitation is named; with
        # signals the appears-open warning replaces it. Either way the
        # heuristic posture and 4.0 are named, never certainty.
        assert ("best-effort" in warning
                or "APPEARS to be open" in warning)
        assert "4.0" in warning
        assert "qualcoder_gui_signals" in out
