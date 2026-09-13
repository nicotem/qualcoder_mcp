"""B2 (v0.12, D3): an execute needs proof that a preview was computed.

`confirm=true` answered a question the caller asked twice and could
answer differently the second time. A token answers the question the
preview asked: this tool, these arguments, this project, these rows. The
tests here pin the codec, the secret file, the verification outcomes and
the refusal texts; the collateral disclosure is in
test_v012_collateral.py.

Upstream parity pins (P1 to P9) cite `ai_mcp_server.py` at the pinned
master 9bddf17, whose own confirm tokens are stored records rather than
signed claims; the numbered pins say where ours matches its semantics.
"""

import json
import os
import re
import stat
import sys
from pathlib import Path

import pytest

import qualcoder_mcp.server as server
import track5_helpers as H
from qualcoder_mcp import preview_tokens as pt
from qualcoder_mcp.database import QualcoderDatabase

POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX file modes and symlinks: Windows chmod moves only the "
           "read-only flag and symlink creation needs a privilege")


def _backups(project_path):
    parent = Path(project_path).parent
    stem = Path(project_path).stem
    return sorted(p.name for p in parent.glob(f"{stem}_backup_*"))


def _preview(tool, *args, **kwargs):
    return json.loads(tool(*args, **kwargs))


def _house_rules(texts, labels=None):
    """No em dashes, British English, no forbidden vocabulary.

    The house rules are pinned on every text this batch adds, in the
    module that owns it, so a reworded string is checked where it is
    written rather than in one distant place (the convention Batch A's D6
    test 8 established).
    """
    forbidden_spellings = ("color", "colors", "behavior", "organize",
                           "recognize", "authorization", "analyze",
                           "labeled", "favor")
    for index, text in enumerate(texts):
        label = (labels[index] if labels else f"text {index}")
        assert "\u2014" not in text, label
        lowered = text.lower()
        for word in forbidden_spellings:
            # Identifiers are exempt by house rule (analyze_file_with_coding,
            # honor_visibility, color): the word counts only when it is
            # prose, that is, not glued to another identifier character.
            pattern = r"(?<![A-Za-z_])" + word + r"(?![A-Za-z_])"
            assert not re.search(pattern, lowered), (label, word)
        for token in re.findall(r"(?<![A-Za-z_])session_id(?![A-Za-z_])",
                                text):
            raise AssertionError((label, "session_id"))

# =============================================================================
# THE CODEC (pure functions, no database)
# =============================================================================

class TestCanonicalJson:

    def test_argument_order_does_not_change_the_signature(self):
        a = pt.canonical({"b": 2, "a": 1})
        b = pt.canonical({"a": 1, "b": 2})
        assert a == b == '{"a":1,"b":2}'

    def test_unicode_names_sign_as_themselves(self):
        text = pt.canonical({"name": "研究助手"})
        assert "研究助手" in text
        assert "\\u" not in text

    def test_the_token_shape(self):
        token = pt.issue("delete_code", {"code_id": 1}, "/p/data.qda", "state")
        assert re.fullmatch(r"qcp1\.\d+\.[0-9a-f]{8}\.[0-9a-f]{32}", token)

    def test_a_token_verifies_for_its_own_operation(self):
        args = {"code_id": 1}
        token = pt.issue("delete_code", args, "/p/data.qda", "state")
        assert pt.verify(token, "delete_code", args, "/p/data.qda",
                         "state") == pt.OK

    def test_bound_to_the_tool(self):
        """P2: a token for one tool does not authorise another."""
        token = pt.issue("delete_code", {"code_id": 1}, "/p/data.qda", "s")
        assert pt.verify(token, "delete_category", {"category_id": 1},
                         "/p/data.qda", "s") == pt.OTHER_OPERATION

    def test_bound_to_the_arguments(self):
        """P3: same tool, different id, refused."""
        token = pt.issue("delete_code", {"code_id": 1}, "/p/data.qda", "s")
        assert pt.verify(token, "delete_code", {"code_id": 2}, "/p/data.qda",
                         "s") == pt.OTHER_OPERATION

    def test_bound_to_the_project(self):
        token = pt.issue("delete_code", {"code_id": 1}, "/p/data.qda", "s")
        assert pt.verify(token, "delete_code", {"code_id": 1},
                         "/other/data.qda", "s") == pt.OTHER_OPERATION

    def test_bound_to_the_state(self):
        """Same operation, different rows: the MAC fails, and because the
        public `bind` still matches, the verifier can say WHICH of the two
        happened. That is the only thing `bind` is for (D3 3.2)."""
        token = pt.issue("delete_code", {"code_id": 1}, "/p/data.qda", "s1")
        assert pt.verify(token, "delete_code", {"code_id": 1}, "/p/data.qda",
                         "s2") == pt.PROJECT_CHANGED

    @pytest.mark.parametrize("offset,expected", [
        (0, pt.OK),
        (-(pt.TOKEN_MAX_AGE_SECONDS - 1), pt.OK),
        (-pt.TOKEN_MAX_AGE_SECONDS, pt.OK),
        (-(pt.TOKEN_MAX_AGE_SECONDS + 1), pt.EXPIRED),
        (pt.TOKEN_MAX_SKEW_SECONDS, pt.OK),
        (pt.TOKEN_MAX_SKEW_SECONDS + 1, pt.EXPIRED),
    ])
    def test_expiry_at_the_exact_boundary(self, offset, expected):
        """No test sleeps: `now` is injected on both sides."""
        now = 1_700_000_000
        token = pt.issue("delete_code", {"code_id": 1}, "/p/data.qda", "s",
                         now=now + offset)
        assert pt.verify(token, "delete_code", {"code_id": 1}, "/p/data.qda",
                         "s", now=now) == expected

    @pytest.mark.parametrize("bad", [
        "", "qcp1", "qcp1.1.2.3", "qcp2.1.aaaaaaaa." + "a" * 32,
        "qcp1.x.aaaaaaaa." + "a" * 32, "qcp1.1.zzzzzzzz." + "a" * 32,
        "qcp1.1.aaaaaaaa." + "z" * 32, "qcp1.1.aaaaaaa." + "a" * 32,
        17, None,
    ], ids=repr)
    def test_malformed_tokens(self, bad):
        assert pt.verify(bad, "delete_code", {"code_id": 1}, "/p/data.qda",
                         "s") == pt.MALFORMED

    def test_every_nibble_of_the_mac_matters(self):
        args = {"code_id": 1}
        token = pt.issue("delete_code", args, "/p/data.qda", "s")
        prefix, issued, bind, mac = token.split(".")
        for i in range(len(mac)):
            flipped = "0" if mac[i] != "0" else "1"
            tampered = f"{prefix}.{issued}.{bind}.{mac[:i]}{flipped}{mac[i+1:]}"
            assert pt.verify(tampered, "delete_code", args, "/p/data.qda",
                             "s") != pt.OK, i

    def test_a_tampered_bind_is_not_an_authorisation(self):
        args = {"code_id": 1}
        token = pt.issue("delete_code", args, "/p/data.qda", "s")
        prefix, issued, bind, mac = token.split(".")
        tampered = f"{prefix}.{issued}.{'0' * 8}.{mac}"
        assert pt.verify(tampered, "delete_code", args, "/p/data.qda",
                         "s") == pt.OK, \
            "bind is public and non-authoritative; the MAC decides"

    def test_comparison_is_constant_time(self, monkeypatch):
        calls = []
        real = pt.hmac.compare_digest
        monkeypatch.setattr(pt.hmac, "compare_digest",
                            lambda a, b: (calls.append(1), real(a, b))[1])
        args = {"code_id": 1}
        token = pt.issue("delete_code", args, "/p/data.qda", "s")
        pt.verify(token, "delete_code", args, "/p/data.qda", "s")
        assert calls, "the MAC comparison must use hmac.compare_digest"

    def test_the_registration_table_is_the_only_place_arguments_live(self):
        """H1: adding a gated tool is one row and no branch."""
        assert set(pt.REGISTRY) == {
            "merge_codes", "delete_code", "delete_category",
            "merge_category", "restore_backup", "prune_backups"}
        assert pt.canonical_args("delete_code", code_id=7) == {"code_id": 7}
        with pytest.raises(KeyError):
            pt.canonical_args("set_memo", target_id=1)

    def test_confirm_and_the_token_are_never_bound(self):
        """They describe the call, not the effect, so they cannot be part
        of what the token authorises."""
        a = pt.canonical_args("delete_code", code_id=7)
        assert "confirm" not in a and "preview_token" not in a

    def test_cascade_is_deliberately_not_bound(self):
        """The preview always reports the whole branch, so the impact the
        user saw does not depend on cascade; binding it would force a
        second preview in the ordinary flow (D3 3.2)."""
        assert pt.canonical_args("delete_code", code_id=7,
                                 cascade=True) == {"code_id": 7}

    def test_merge_category_binds_the_resolved_id_not_the_name(self):
        assert pt.canonical_args("merge_category", from_category_id=1,
                                 into_category_id=2) == {
            "from_category_id": 1, "into_category_id": 2}
        assert pt.canonical_args("merge_category", from_category_id=1,
                                 into_category_id=None)["into_category_id"] \
            is None


# =============================================================================
# THE SECRET FILE
# =============================================================================

class TestSecretFile:

    def test_created_on_first_use(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pt, "STATE_HOME", tmp_path / "state")
        secret = pt.load_secret()
        assert len(secret) == 64
        path = tmp_path / "state" / "preview_secret"
        assert path.exists()
        assert path.read_text(encoding="ascii").strip() == secret

    @POSIX_ONLY
    def test_created_owner_only(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pt, "STATE_HOME", tmp_path / "state")
        pt.load_secret()
        mode = stat.S_IMODE(os.stat(tmp_path / "state" /
                                    "preview_secret").st_mode)
        assert mode == 0o600

    def test_an_existing_secret_is_read_not_rewritten(self, tmp_path,
                                                      monkeypatch):
        monkeypatch.setattr(pt, "STATE_HOME", tmp_path / "state")
        first = pt.load_secret()
        path = tmp_path / "state" / "preview_secret"
        before = os.stat(path).st_mtime_ns
        assert pt.load_secret() == first
        assert os.stat(path).st_mtime_ns == before

    def test_the_loser_of_a_race_reads_the_winners_secret(self, tmp_path,
                                                          monkeypatch):
        """O_EXCL on the final path: the second creator gets EEXIST and
        reads rather than overwriting, so no token is orphaned."""
        monkeypatch.setattr(pt, "STATE_HOME", tmp_path / "state")
        winner = pt.load_secret()
        real_open = pt.os.open

        def racing_open(path, flags, mode=0o777):
            raise FileExistsError("another server won")

        monkeypatch.setattr(pt.os, "open", racing_open)
        assert pt.load_secret() == winner

    @pytest.mark.parametrize("content", ["", "not hex at all", "abc",
                                         "z" * 64, "a" * 63])
    def test_corrupt_content_is_rotated(self, tmp_path, monkeypatch, content):
        monkeypatch.setattr(pt, "STATE_HOME", tmp_path / "state")
        first = pt.load_secret()
        path = tmp_path / "state" / "preview_secret"
        path.write_text(content, encoding="ascii")
        rotated = pt.load_secret()
        assert len(rotated) == 64
        assert rotated != first
        assert pt.load_secret() == rotated      # and it stays

    def test_an_oversized_file_is_treated_as_corrupt(self, tmp_path,
                                                     monkeypatch):
        monkeypatch.setattr(pt, "STATE_HOME", tmp_path / "state")
        pt.load_secret()
        path = tmp_path / "state" / "preview_secret"
        path.write_text("a" * 64 + "\n" + "x" * 9000, encoding="ascii")
        assert len(pt.load_secret()) == 64
        assert path.read_text(encoding="ascii").strip() != "a" * 64

    def test_a_rotation_invalidates_outstanding_tokens(self, tmp_path,
                                                       monkeypatch):
        monkeypatch.setattr(pt, "STATE_HOME", tmp_path / "state")
        args = {"code_id": 1}
        token = pt.issue("delete_code", args, "/p/data.qda", "s")
        (tmp_path / "state" / "preview_secret").write_text("rubbish",
                                                           encoding="ascii")
        # A rotation cannot be told from drift without state, and both
        # have the same remedy; the refusal text names both causes.
        assert pt.verify(token, "delete_code", args, "/p/data.qda",
                         "s") in (pt.PROJECT_CHANGED, pt.OTHER_OPERATION)

    @POSIX_ONLY
    def test_a_symlinked_secret_is_refused(self, tmp_path, monkeypatch):
        state = tmp_path / "state"
        state.mkdir()
        outside = tmp_path / "outside_secret"
        outside.write_text("a" * 64 + "\n", encoding="ascii")
        (state / "preview_secret").symlink_to(outside)
        monkeypatch.setattr(pt, "STATE_HOME", state)
        with pytest.raises(pt.PreviewSecretUnavailable):
            pt.load_secret()

    @POSIX_ONLY
    def test_an_unwritable_state_home_refuses_rather_than_falling_back(
            self, tmp_path, monkeypatch, setup_server, qualcoder_db_path):
        state = tmp_path / "readonly_state"
        state.mkdir()
        os.chmod(state, 0o500)
        monkeypatch.setattr(pt, "STATE_HOME", state)
        try:
            out = _preview(server.delete_code, 1)
            assert out["reason"] == "preview_secret_unavailable"
            assert out["nothing_changed"] is True
            assert out["error"] == pt.SECRET_UNAVAILABLE_MESSAGE
            assert _backups(qualcoder_db_path) == []
        finally:
            os.chmod(state, 0o700)

    def test_the_secret_never_reaches_a_result(self, setup_server,
                                               qualcoder_db_path):
        secret = pt.load_secret()
        for raw in (server.delete_code(1), server.merge_codes(1, 2),
                    server.delete_category(1)):
            assert secret not in raw


# =============================================================================
# THE TWO-STEP FLOW THROUGH THE TOOLS
# =============================================================================

class TestTwoStepFlow:

    def test_a_call_without_a_token_writes_nothing(self, setup_server,
                                                   qualcoder_db_path):
        """P1 and P5: the preview is read-only, takes no backup and does
        not upgrade the connection."""
        before = H.query(qualcoder_db_path,
                         "SELECT COUNT(*) AS n FROM code_name")[0]["n"]
        out = _preview(server.delete_code, 1)
        assert out["requires_confirmation"] is True
        assert out["preview_token"].startswith("qcp1.")
        assert out["token_valid_for_minutes"] == 60
        assert H.query(qualcoder_db_path,
                       "SELECT COUNT(*) AS n FROM code_name")[0]["n"] == before
        assert _backups(qualcoder_db_path) == []
        assert server.db.read_only is True

    def test_the_token_executes_the_operation_it_was_issued_for(
            self, setup_server, qualcoder_db_path):
        out = _preview(server.delete_code, 1)
        done = _preview(server.delete_code, 1,
                        preview_token=out["preview_token"])
        assert done["success"] is True
        assert done["preview_verified"] is True
        assert len(_backups(qualcoder_db_path)) == 1
        assert H.query(qualcoder_db_path,
                       "SELECT COUNT(*) AS n FROM code_name WHERE cid=1"
                       )[0]["n"] == 0

    def test_execute_with_spells_out_the_follow_up_call(self, setup_server,
                                                        qualcoder_db_path):
        """P8: the preview shape a small local model can act on."""
        out = _preview(server.delete_code, 1)
        assert out["execute_with"]["tool"] == "delete_code"
        arguments = out["execute_with"]["arguments"]
        assert arguments["code_id"] == 1
        assert arguments["preview_token"] == out["preview_token"]
        done = _preview(server.delete_code, **arguments)
        assert done["success"] is True

    def test_a_token_for_another_tool_is_refused(self, setup_server,
                                                 qualcoder_db_path):
        """P2 and P9: refusing one use does not consume the token."""
        out = _preview(server.delete_code, 1)
        token = out["preview_token"]
        refused = _preview(server.delete_category, 1, preview_token=token)
        assert refused["reason"] == "token_other_operation"
        assert refused["nothing_changed"] is True
        assert _backups(qualcoder_db_path) == []
        # still valid for its own operation
        done = _preview(server.delete_code, 1, preview_token=token)
        assert done["success"] is True

    def test_a_token_for_another_id_is_refused(self, setup_server,
                                               qualcoder_db_path):
        out = _preview(server.delete_code, 1)
        refused = _preview(server.delete_code, 2,
                           preview_token=out["preview_token"])
        assert refused["reason"] == "token_other_operation"

    def test_a_changed_project_refuses_the_token(self, setup_server,
                                                 qualcoder_db_path):
        """The point of the state binding: the rows the user was shown are
        not the rows the execute would touch."""
        out = _preview(server.delete_code, 1)
        H.execute(qualcoder_db_path,
                  "INSERT INTO code_text (ctid, cid, fid, seltext, pos0, "
                  "pos1, owner, date, memo, important) VALUES "
                  "(90, 1, 1, 'x', 70, 71, 'TestCoder', '2024-01-15', '', 0)")
        server.db.close()
        server.db = QualcoderDatabase(qualcoder_db_path)
        refused = _preview(server.delete_code, 1,
                           preview_token=out["preview_token"])
        assert refused["reason"] == "project_changed"
        assert refused["nothing_changed"] is True
        assert _backups(qualcoder_db_path) == []
        assert H.query(qualcoder_db_path,
                       "SELECT COUNT(*) AS n FROM code_name WHERE cid=1"
                       )[0]["n"] == 1

    def test_a_second_operation_does_not_invalidate_the_first_token(
            self, setup_server, qualcoder_db_path):
        """Per-operation fingerprints, not a whole-project one: previewing
        and executing B must not force a re-preview of A (test R2)."""
        token_a = _preview(server.delete_code, 1)["preview_token"]
        token_b = _preview(server.delete_code, 2)["preview_token"]
        done_b = _preview(server.delete_code, 2, preview_token=token_b)
        assert done_b["success"] is True
        server.db.close()
        server.db = QualcoderDatabase(qualcoder_db_path)
        done_a = _preview(server.delete_code, 1, preview_token=token_a)
        assert done_a["success"] is True, done_a

    def test_an_expired_token_says_so(self, setup_server, qualcoder_db_path,
                                      monkeypatch):
        out = _preview(server.delete_code, 1)
        monkeypatch.setattr(pt, "_now",
                            lambda: int(pt.time.time()) + 60 * 61)
        refused = _preview(server.delete_code, 1,
                           preview_token=out["preview_token"])
        assert refused["reason"] == "token_expired"
        assert "60 minutes" in refused["error"]

    def test_a_malformed_token_says_so(self, setup_server):
        refused = _preview(server.delete_code, 1, preview_token="not a token")
        assert refused["reason"] == "token_malformed"
        assert "not a token this server issued" in refused["error"]

    def test_the_refusal_texts_are_count_free_and_name_free(
            self, setup_server, qualcoder_db_path):
        refused = _preview(server.delete_code, 1, preview_token="rubbish")
        assert "TestCoder" not in refused["error"]
        assert not re.search(r"\b\d+ coding", refused["error"])

    def test_a_moved_project_invalidates_the_token(self, setup_server,
                                                   qualcoder_db_path,
                                                   tmp_path):
        out = _preview(server.delete_code, 1)
        moved = Path(qualcoder_db_path).parent / "moved_project.qda"
        Path(qualcoder_db_path).rename(moved)
        server.db.close()
        server.db = QualcoderDatabase(str(moved))
        server.current_project_path = str(moved)
        refused = _preview(server.delete_code, 1,
                           preview_token=out["preview_token"])
        assert refused["reason"] == "token_other_operation"


class TestLegacyConfirm:
    """B2.7: `confirm` stays accepted and inert for one release, so a
    0.11 caller gets a preview and an explanation rather than silence."""

    def test_confirm_true_returns_the_preview_and_writes_nothing(
            self, setup_server, qualcoder_db_path):
        out = _preview(server.delete_code, 1, confirm=True)
        assert out["requires_confirmation"] is True
        assert "deprecated_argument" in out
        assert "removed in v0.13" in out["deprecated_argument"]
        assert _backups(qualcoder_db_path) == []
        assert H.query(qualcoder_db_path,
                       "SELECT COUNT(*) AS n FROM code_name WHERE cid=1"
                       )[0]["n"] == 1

    def test_confirm_with_a_token_is_ignored(self, setup_server,
                                             qualcoder_db_path):
        token = _preview(server.delete_code, 1)["preview_token"]
        done = _preview(server.delete_code, 1, preview_token=token,
                        confirm=True)
        assert done["success"] is True

    @pytest.mark.parametrize("tool,args", [
        ("merge_codes", (1, 2)), ("delete_code", (1,)),
        ("delete_category", (1,)), ("merge_category", (1,)),
    ])
    def test_every_codebook_tool_keeps_the_deprecation(self, setup_server,
                                                       tool, args):
        out = _preview(getattr(server, tool), *args, confirm=True)
        assert "deprecated_argument" in out

    def test_the_docstrings_carry_the_two_step_paragraph(self):
        for name in ("merge_codes", "delete_code", "delete_category",
                     "merge_category"):
            doc = " ".join((getattr(server, name).__doc__ or "").split())
            assert "Two-step by design." in doc, name
            assert "valid for 60 minutes" in doc, name
            assert "A backup is always created first." in doc, name


class TestFileLevelOperations:
    """restore_backup and prune_backups are file-level and stay outside
    the row-level hidden-coder rule, but they are token-gated too."""

    def test_restore_backup_two_step(self, setup_server, qualcoder_db_path):
        H.execute_destructive(server.delete_code, 2)   # makes a backup
        backups = _backups(qualcoder_db_path)
        backup = str(Path(qualcoder_db_path).parent / backups[-1])
        out = _preview(server.restore_backup, backup)
        assert out["requires_confirmation"] is True
        assert out["preview_token"].startswith("qcp1.")
        done = _preview(server.restore_backup, backup,
                        preview_token=out["preview_token"])
        assert done.get("success") is True, done
        assert done["preview_verified"] is True

    def test_a_restore_token_is_bound_to_its_backup(self, setup_server,
                                                    qualcoder_db_path):
        server.create_code("BackupOne")          # each write takes a backup
        server.create_code("BackupTwo")
        backups = _backups(qualcoder_db_path)
        assert len(backups) >= 2
        parent = Path(qualcoder_db_path).parent
        token = _preview(server.restore_backup,
                         str(parent / backups[0]))["preview_token"]
        refused = _preview(server.restore_backup, str(parent / backups[-1]),
                           preview_token=token)
        assert refused["reason"] == "token_other_operation"

    def test_prune_backups_two_step(self, setup_server, qualcoder_db_path):
        server.create_code("PruneOne")
        server.create_code("PruneTwo")
        out = _preview(server.prune_backups, keep_last=1)
        assert out["requires_confirmation"] is True
        assert out["would_remove"]
        done = _preview(server.prune_backups, keep_last=1,
                        preview_token=out["preview_token"])
        assert done.get("success") is True, done

    def test_a_prune_token_is_bound_to_its_policy(self, setup_server,
                                                  qualcoder_db_path):
        server.create_code("PolicyOne")
        server.create_code("PolicyTwo")
        token = _preview(server.prune_backups, keep_last=1)["preview_token"]
        refused = _preview(server.prune_backups, keep_last=0,
                           preview_token=token)
        assert refused["reason"] == "token_other_operation"


class TestRestartResilience:
    """S1 to S4: the token is a claim, not a memory."""

    def test_a_token_survives_a_process_recycle(self, setup_server,
                                                qualcoder_db_path):
        out = _preview(server.delete_code, 1)
        server.db = None
        server.current_project_path = None
        json.loads(server.select_project(qualcoder_db_path))
        done = _preview(server.delete_code, 1,
                        preview_token=out["preview_token"])
        assert done["success"] is True

    def test_nothing_is_stored_when_a_token_is_issued(self, setup_server,
                                                      qualcoder_db_path,
                                                      tmp_path):
        state = Path(pt.state_home())
        before = sorted(p.name for p in state.glob("*")) if state.exists() \
            else []
        _preview(server.delete_code, 1)
        after = sorted(p.name for p in state.glob("*")) if state.exists() \
            else []
        # only the secret itself may appear
        assert set(after) - set(before) <= {"preview_secret"}

    def test_the_module_keeps_no_ledger(self):
        assert not [n for n in dir(pt)
                    if not n.startswith("__")
                    and ("ledger" in n.lower() or "cache" in n.lower()
                         or "issued_tokens" in n.lower())]


class TestExportPathsAreNotAimedAtTheState:
    """B2.8 (D3 5.2): no export can be aimed at the secret, the sessions
    or the MRU file, on principle rather than by inspection."""

    def test_refi_export_refuses_the_state_home(self, setup_server,
                                                tmp_path, monkeypatch):
        monkeypatch.setattr(pt, "STATE_HOME", tmp_path / "state")
        (tmp_path / "state").mkdir()
        out = _preview(server.export_refi_qda,
                       output_path=str(tmp_path / "state" / "x.qdpx"))
        assert "state folder" in out["error"]

    def test_report_export_refuses_the_state_home(self, setup_server,
                                                  tmp_path, monkeypatch):
        monkeypatch.setattr(pt, "STATE_HOME", tmp_path / "state")
        (tmp_path / "state" / "sub").mkdir(parents=True)
        out = _preview(server.export_codebook,
                       output_path=str(tmp_path / "state" / "sub" / "c.csv"))
        assert "state folder" in out["error"]

    def test_an_ordinary_path_still_works(self, setup_server, tmp_path,
                                          monkeypatch):
        monkeypatch.setattr(pt, "STATE_HOME", tmp_path / "state")
        out = _preview(server.export_codebook,
                       output_path=str(tmp_path / "codebook.csv"))
        assert out.get("success") is True, out


class TestHouseRulesOnTheNewTexts:

    def test_every_new_text_of_this_item(self):
        texts = list(server.TOKEN_ERROR_TEXTS.values()) + [
            server.TWO_STEP_PARAGRAPH,
            server.DEPRECATED_CONFIRM_NOTE,
            pt.SECRET_UNAVAILABLE_MESSAGE,
            server.merge_codes.__doc__ or "",
            server.delete_code.__doc__ or "",
        ]
        _house_rules(texts)
