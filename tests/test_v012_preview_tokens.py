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
        """H1: adding a gated tool is one row and no branch.

        The v0.12 flagship is the first tool to take that offer up:
        `pseudonymise_source` is one row here and nothing else in this
        module.
        """
        assert set(pt.REGISTRY) == {
            "merge_codes", "delete_code", "delete_category",
            "merge_category", "restore_backup", "prune_backups",
            "pseudonymise_source"}
        assert pt.canonical_args("delete_code", code_id=7) == {"code_id": 7}
        with pytest.raises(KeyError):
            pt.canonical_args("set_memo", target_id=1)

    def test_the_flagship_bind_is_keyed_with_the_secret(self, tmp_path,
                                                       monkeypatch):
        """Fix round 3, S2. D3 3.2 made `bind` a plain digest on the
        premise that every bound argument was already in the
        conversation. The flagship binds a mapping that can be the
        researcher's reverse key, and a plain digest of it, beside the
        pseudonym the preview shows, confirmed a guessed original in
        twenty tries. Keyed with the secret it confirms nothing to
        anyone without it, and the verifier, which has it, still tells
        the two refusals apart."""
        monkeypatch.setattr(pt, "STATE_HOME", tmp_path / "state")
        args = pt.canonical_args(
            "pseudonymise_source",
            mapping=[{"original": "Thomas", "pseudonym": "Alex",
                      "variants": []}],
            file_id=1, case_mode="exact", overlap_policy="snap_to_pseudonym",
            rewrite_memos=False)
        token = pt.issue("pseudonymise_source", args, "/p/data.qda", "s1")
        bind = token.split(".")[2]
        plain = pt.hashlib.sha256(pt.canonical(
            pt._binding("pseudonymise_source", args, "/p/data.qda")
        ).encode("utf-8")).hexdigest()[:8]
        assert bind != plain
        secret = pt.load_secret()
        keyed = pt.hmac.new(secret.encode("ascii"), pt.canonical(
            pt._binding("pseudonymise_source", args, "/p/data.qda")
        ).encode("utf-8"), pt.hashlib.sha256).hexdigest()[:8]
        assert bind == keyed
        assert pt.bind_id("pseudonymise_source", args, "/p/data.qda",
                          secret) == keyed
        # The two refusals are still told apart.
        assert pt.verify(token, "pseudonymise_source", args, "/p/data.qda",
                         "s2") == pt.PROJECT_CHANGED
        other = dict(args, case_mode="insensitive")
        assert pt.verify(token, "pseudonymise_source", other, "/p/data.qda",
                         "s1") == pt.OTHER_OPERATION

    def test_the_codebook_binds_stay_public(self, tmp_path, monkeypatch):
        """The six older tools bind ids the conversation holds; their
        bind is the plain digest it always was, and D3's tampered-bind
        experiment above still holds for them."""
        monkeypatch.setattr(pt, "STATE_HOME", tmp_path / "state")
        args = {"code_id": 1}
        token = pt.issue("delete_code", args, "/p/data.qda", "s")
        plain = pt.hashlib.sha256(pt.canonical(
            pt._binding("delete_code", args, "/p/data.qda")
        ).encode("utf-8")).hexdigest()[:8]
        assert token.split(".")[2] == plain

    def test_the_keyed_table_names_the_flagship_and_nothing_outside_the_registry(
            self):
        assert pt.KEYED_BIND == frozenset({"pseudonymise_source"})

    def test_a_keyed_bind_is_never_computed_without_the_secret_being_passed(
            self, tmp_path, monkeypatch):
        """Fix round 4, L1. `bind_id` used to load the researcher's own
        secret when none was passed, so any code running as the
        researcher computed a keyed bind without saying so, and a
        verifier's recomputation read like an attacker's recovery. For
        a tool in `KEYED_BIND` the secret is the caller's to pass; the
        codebook tools, whose bind is public, need none."""
        monkeypatch.setattr(pt, "STATE_HOME", tmp_path / "state")
        args = pt.canonical_args(
            "pseudonymise_source",
            mapping=[{"original": "Thomas", "pseudonym": "Alex",
                      "variants": []}],
            file_id=1, case_mode="exact", overlap_policy="snap_to_pseudonym",
            rewrite_memos=False)
        with pytest.raises(TypeError, match="needs the secret"):
            pt.bind_id("pseudonymise_source", args, "/p/data.qda")
        assert not (tmp_path / "state").exists(), "nothing was loaded"
        secret = pt.load_secret()
        assert pt.bind_id("pseudonymise_source", args, "/p/data.qda",
                          secret) == pt.issue(
            "pseudonymise_source", args, "/p/data.qda", "s").split(".")[2]
        assert len(pt.bind_id("delete_code", {"code_id": 1},
                              "/p/data.qda")) == 8
        assert pt.KEYED_BIND <= set(pt.REGISTRY)

    def test_the_flagship_binds_only_what_decides_the_effect(self):
        """The arguments that change the text or a row, and no more.

        `include_context`, `context_chars`, `scan_residue`,
        `residue_detail` and `max_spans_per_entry` change what the preview
        SHOWS; `record_in_journal` changes only whether the run records
        itself. None of them is bound, so passing a different one on the
        execute call is not "a different operation": it changes nothing.
        `rewrite_memos` changes the notes, so it IS bound (v0.13, Brief
        2), as the `bool` the server passes whatever truthy value it was
        given.
        """
        bound = pt.canonical_args(
            "pseudonymise_source",
            mapping=[{"original": "Tom", "pseudonym": "Alex",
                      "variants": []}],
            file_id=3, case_mode="exact",
            overlap_policy="snap_to_pseudonym",
            use_project_pseudonyms=True, include_context=True,
            context_chars=99, scan_residue=False, max_spans_per_entry=1,
            residue_detail="project", rewrite_memos=1,
            record_in_journal=False, allow_hidden_coder=True,
            preview_token="qcp1.1.aaaaaaaa." + "a" * 32, confirm=True)
        assert bound == {
            "file_id": 3,
            "mapping": [{"original": "Tom", "pseudonym": "Alex",
                         "variants": []}],
            "case_mode": "exact",
            "overlap_policy": "snap_to_pseudonym",
            "rewrite_memos": True}
        assert bound["rewrite_memos"] is True

    def test_the_flagship_binds_its_one_file_as_an_integer(self):
        """One file per call since v0.13 (decision A): the bind carries
        the one id the server validated, as an integer, and no longer a
        sorted list or None. Two files are two operations, and the old
        list key is gone, so a 0.12 token for `file_ids` that reached
        this binding would verify as another operation."""
        def call(fid):
            return pt.canonical_args(
                "pseudonymise_source", mapping=[], file_id=fid,
                case_mode="exact", overlap_policy="snap_to_pseudonym",
                rewrite_memos=False)
        assert call(1)["file_id"] == 1
        assert isinstance(call(1)["file_id"], int)
        assert call(1) != call(4)
        assert "file_ids" not in call(1)
        with pytest.raises(KeyError):
            pt.canonical_args("pseudonymise_source", mapping=[],
                              file_ids=[1], case_mode="exact",
                              overlap_policy="snap_to_pseudonym")

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
        """First-writer-wins is unchanged by the atomicity fix: the
        publish step is os.link, which raises FileExistsError exactly
        where O_EXCL used to, so the loser reads rather than
        overwriting and no outstanding token is orphaned."""
        state = tmp_path / "state"
        monkeypatch.setattr(pt, "STATE_HOME", state)
        winner = pt.load_secret()
        path = state / "preview_secret"
        # The create branch, entered because the file did not exist a
        # moment earlier, refuses rather than replacing.
        with pytest.raises(FileExistsError):
            pt._write_new_secret(path, exclusive=True)
        assert path.read_text(encoding="ascii").strip() == winner
        assert sorted(p.name for p in state.glob("*")) == ["preview_secret"]
        # ... and load_secret's own fallback then reads the winner's
        # value, which is the whole point: no outstanding token dies.
        real_lexists = os.path.lexists
        seen = []

        def lexists_false_once(target):
            seen.append(target)
            return False if len(seen) == 1 else real_lexists(target)

        monkeypatch.setattr(pt.os.path, "lexists", lexists_false_once)
        assert pt.load_secret() == winner

    def test_the_final_path_is_never_created_before_its_content(
            self, tmp_path, monkeypatch):
        """The window the race went through: os.open created the final
        path at length zero and the content was written afterwards, so a
        second server starting inside that window read an empty file,
        judged it malformed and rotated over the winner's secret, whose
        own write then landed on an unlinked inode. Measured at 24
        simultaneous starts: three distinct secrets in one run.

        Faulted deterministically here, between creation and content:
        whatever fails, nothing is published at the final name."""
        state = tmp_path / "state"
        monkeypatch.setattr(pt, "STATE_HOME", state)
        path = state / "preview_secret"

        def exploding_fdopen(*a, **k):
            raise OSError("disk went away mid-write")

        monkeypatch.setattr(pt.os, "fdopen", exploding_fdopen)
        with pytest.raises(OSError):
            pt._write_new_secret(path, exclusive=True)
        assert not path.exists()
        assert list(state.glob("*")) == [], list(state.glob("*"))

    def test_the_faulted_write_closes_the_descriptor_it_was_given(
            self, tmp_path, monkeypatch):
        """Why the clause above passed here and failed on the Windows
        runners (fix round 5).

        `tempfile.mkstemp` returns a descriptor nobody owns until
        `os.fdopen` takes it. When `os.fdopen` raised, it was never
        taken and never closed. POSIX unlinks an open file happily, so
        the cleanup left an empty state folder and the pin above was
        green; Windows refuses to unlink a file with a live handle
        (ERROR_SHARING_VIOLATION), the tolerant `except OSError` in the
        cleanup swallowed it, and the temp survived.

        The descriptor is what is pinned, not the litter, because the
        descriptor is the cause and it is the same on every platform.
        Counting `os.close` calls rather than probing the number avoids
        reading a descriptor the runtime has since reused."""
        state = tmp_path / "state"
        monkeypatch.setattr(pt, "STATE_HOME", state)
        path = state / "preview_secret"
        handed_out = []
        closed = []
        real_mkstemp = pt.tempfile.mkstemp
        real_close = pt.os.close

        def spy_mkstemp(*a, **k):
            fd, name = real_mkstemp(*a, **k)
            handed_out.append(fd)
            return fd, name

        def spy_close(fd):
            closed.append(fd)
            return real_close(fd)

        def exploding_fdopen(*a, **k):
            raise OSError("disk went away mid-write")

        monkeypatch.setattr(pt.tempfile, "mkstemp", spy_mkstemp)
        monkeypatch.setattr(pt.os, "close", spy_close)
        monkeypatch.setattr(pt.os, "fdopen", exploding_fdopen)
        with pytest.raises(OSError):
            pt._write_new_secret(path, exclusive=True)
        assert handed_out, "mkstemp was never reached"
        assert closed == handed_out, (closed, handed_out)

    def test_the_windows_publish_also_refuses_to_replace(self, tmp_path,
                                                         monkeypatch):
        """os.link needs NTFS and the privilege to make hard links, so
        Windows publishes with os.rename, which on that platform raises
        when the destination exists (on POSIX it would silently replace,
        which is what D3 3.3 refuses on create). Driven here by taking
        the nt branch and faulting the call it makes, because the
        platform difference cannot be observed on this one."""
        state = tmp_path / "state"
        monkeypatch.setattr(pt, "STATE_HOME", state)
        pt.load_secret()
        path = state / "preview_secret"
        before = path.read_text(encoding="ascii")
        calls = []

        def windows_rename(src, dst):
            calls.append((src, dst))
            raise FileExistsError("another server won")

        source = state / "complete.tmp"
        source.write_text("b" * 64 + "\n", encoding="ascii")
        monkeypatch.setattr(pt.os, "rename", windows_rename)
        with pytest.raises(FileExistsError):
            pt._publish_exclusive(str(source), path, windows=True)
        assert calls, "the nt branch was not taken"
        assert path.read_text(encoding="ascii") == before
        # And the POSIX spelling of the same rule, which is what this
        # platform actually runs: link, never replace.
        with pytest.raises(FileExistsError):
            pt._publish_exclusive(str(source), path, windows=False)
        assert path.read_text(encoding="ascii") == before

    @pytest.mark.skipif(
        sys.platform != "win32",
        reason="the nt arm's premise is a Windows behaviour: POSIX rename "
               "replaces silently, so the real call cannot show it here")
    def test_the_windows_arm_really_refuses_on_the_platform_that_runs_it(
            self, tmp_path, monkeypatch):
        """The one claim the pin above cannot make (fix round 5).

        Faulting `os.rename` proves the nt BRANCH is taken; it cannot
        prove the premise the branch rests on, which is that Windows'
        own `os.rename` raises when the destination exists. POSIX rename
        replaces without a word, so no test on this machine can show it,
        and the arm was written from documentation and had never run
        anywhere. It runs here, on the Windows jobs, with the real call
        and an existing destination: if that premise is ever wrong, the
        secret would be replaced rather than refused and first-writer-
        wins would be gone."""
        state = tmp_path / "state"
        monkeypatch.setattr(pt, "STATE_HOME", state)
        winner = pt.load_secret()
        path = state / "preview_secret"
        source = state / "complete.tmp"
        source.write_text("b" * 64 + "\n", encoding="ascii")
        with pytest.raises(FileExistsError):
            pt._publish_exclusive(str(source), path, windows=True)
        assert path.read_text(encoding="ascii").strip() == winner
        assert source.exists(), "a refused publish keeps its own temp"

    @POSIX_ONLY
    def test_a_secret_widened_since_creation_is_rotated(self, tmp_path,
                                                        monkeypatch):
        """The mode was set at creation and never checked again, so a
        secret widened by a restore, a sync tool or another account was
        used as though it were still private. A secret others can read
        is a secret others can mint tokens with."""
        monkeypatch.setattr(pt, "STATE_HOME", tmp_path / "state")
        first = pt.load_secret()
        path = tmp_path / "state" / "preview_secret"
        os.chmod(path, 0o644)
        rotated = pt.load_secret()
        assert len(rotated) == 64
        assert rotated != first
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        assert pt.load_secret() == rotated          # and it settles

    @POSIX_ONLY
    def test_the_state_folder_is_owner_only(self, tmp_path, monkeypatch):
        state = tmp_path / "state"
        monkeypatch.setattr(pt, "STATE_HOME", state)
        pt.load_secret()
        assert stat.S_IMODE(os.stat(state).st_mode) == 0o700

    @POSIX_ONLY
    def test_a_wider_state_folder_is_narrowed(self, tmp_path, monkeypatch):
        state = tmp_path / "state"
        state.mkdir(mode=0o755)
        monkeypatch.setattr(pt, "STATE_HOME", state)
        pt.load_secret()
        assert stat.S_IMODE(os.stat(state).st_mode) & 0o077 == 0

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
        # Close BEFORE the move. A live connection is an open handle on
        # data.qda, and Windows refuses to rename a directory that holds
        # one, so the old order was green here and failed on both Windows
        # jobs. It is also the order a researcher uses: close the
        # project, then move it.
        server.db.close()
        server.db = None
        Path(qualcoder_db_path).rename(moved)
        server.db = QualcoderDatabase(str(moved))
        server.current_project_path = str(moved)
        refused = _preview(server.delete_code, 1,
                           preview_token=out["preview_token"])
        assert refused["reason"] == "token_other_operation"


class TestTheConfirmArgumentIsGone:
    """v0.13 A1. 0.12.0 announced it: "`confirm` stays in the signatures
    for this release and is removed in v0.13." It is removed here, from
    the six token-gated tools, from the gate helpers they share and from
    the preview note that explained it.

    What a caller that still passes it now meets is recorded rather than
    assumed, because the two routes differ and neither is a schema
    refusal. A direct Python call raises TypeError. An MCP `tools/call`
    is not refused at all: the Python MCP server validates arguments
    against a pydantic model whose extra-field policy is the default
    "ignore" and whose published schema sets no `additionalProperties`,
    so an argument no tool declares is dropped and the call runs as if
    it had not been passed. For these tools that means the preview, with
    the `hint` and `execute_with` recipe that already say what to call
    next. Driven below so the sentence the CHANGELOG carries is measured
    rather than believed.
    """

    TOKEN_GATED = ("merge_codes", "delete_code", "delete_category",
                   "merge_category", "restore_backup", "prune_backups")

    CALL_ARGS = {"merge_codes": (1, 2), "delete_code": (1,),
                 "delete_category": (1,), "merge_category": (1,),
                 "restore_backup": ("nowhere",), "prune_backups": ()}

    def test_no_signature_still_takes_it(self):
        import inspect
        for name in self.TOKEN_GATED:
            params = inspect.signature(getattr(server, name)).parameters
            assert "confirm" not in params, name

    def test_no_published_schema_still_declares_it(self):
        import asyncio
        tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
        for name in self.TOKEN_GATED:
            properties = tools[name].inputSchema.get("properties", {})
            assert "confirm" not in properties, name

    def test_the_shared_gate_helpers_no_longer_carry_it(self):
        import inspect
        for helper in (server._issue_preview, server._guarded_destructive):
            params = inspect.signature(helper).parameters
            assert "confirm" not in params, helper.__name__
        # `confirm_hint` is what the preview still needs to say and
        # stays; it is the hint, not the argument.
        assert "confirm_hint" in inspect.signature(
            server._guarded_destructive).parameters

    def test_the_preview_note_that_explained_it_is_gone(self):
        assert not hasattr(server, "DEPRECATED_CONFIRM_NOTE")

    @pytest.mark.parametrize("name", TOKEN_GATED)
    def test_a_direct_python_call_that_passes_it_is_refused(self,
                                                            setup_server,
                                                            name):
        """In-process, the TypeError never leaves the tool: `_tool_guard`
        turns it into the ordinary error envelope, naming the argument.
        """
        out = json.loads(getattr(server, name)(*self.CALL_ARGS[name],
                                               confirm=True))
        assert "confirm" in out["error"], out
        assert "unexpected keyword argument" in out["error"], out

    def test_an_mcp_call_that_still_passes_it_is_ignored_not_refused(
            self, setup_server, qualcoder_db_path):
        """The measured answer, and the one the CHANGELOG states.

        Not a schema error: the argument is dropped and the call
        behaves exactly as the same call without it, which for a
        token-gated tool with no token is the preview. Nothing is
        written and no backup is taken, so a 0.11-era caller still
        cannot execute by saying yes twice.
        """
        import asyncio
        plain = asyncio.run(server.mcp.call_tool("delete_code",
                                                 {"code_id": 1}))
        stale = asyncio.run(server.mcp.call_tool(
            "delete_code", {"code_id": 1, "confirm": True}))

        def payload(answer):
            blocks = answer[0] if isinstance(answer, tuple) else answer
            return json.loads("".join(b.text for b in blocks))

        one, two = payload(plain), payload(stale)
        assert one["requires_confirmation"] is True
        assert two["requires_confirmation"] is True
        assert "deprecated_argument" not in two
        # The same answer but for the token, which is minted per call.
        for answer in (one, two):
            answer.pop("preview_token")
            answer["execute_with"]["arguments"].pop("preview_token")
        assert one == two
        assert _backups(qualcoder_db_path) == []
        assert H.query(qualcoder_db_path,
                       "SELECT COUNT(*) AS n FROM code_name WHERE cid=1"
                       )[0]["n"] == 1

    def test_the_docstrings_carry_the_two_step_paragraph(self):
        for name in self.TOKEN_GATED:
            doc = " ".join((getattr(server, name).__doc__ or "").split())
            assert "Two-step by design." in doc, name
            assert "valid for 60 minutes" in doc, name
            assert "preview_token=<the token>" in doc, name

    def test_the_backup_sentence_is_true_of_the_tool_that_carries_it(self):
        """B2.3 asks for the shared paragraph on all six. Its closing
        sentence is about the four codebook tools and the restore, which
        do take a backup first; prune_backups takes none (it removes
        backup folders and never opens the database), so it says that
        instead of claiming a backup nobody made (QA round 1, fix round 1
        judgement call)."""
        for name in ("merge_codes", "delete_code", "delete_category",
                     "merge_category"):
            doc = " ".join((getattr(server, name).__doc__ or "").split())
            assert "A backup is always created first." in doc, name
        restore = " ".join((server.restore_backup.__doc__ or "").split())
        assert ("A safety backup of the current state is always created "
                "first." in restore)
        prune = " ".join((server.prune_backups.__doc__ or "").split())
        assert "No backup is taken here" in prune
        assert "A backup is always created first." not in prune

    def test_no_docstring_still_mentions_the_argument(self):
        """A docstring that asks for the flag sends the model down a
        path that writes nothing; a docstring that still documents it as
        deprecated describes an argument that no longer exists. Neither
        survives the removal. "preview then confirm" stays: that is the
        two-step workflow, not the argument."""
        for name in self.TOKEN_GATED:
            doc = " ".join((getattr(server, name).__doc__ or "").split())
            assert "confirm=true" not in doc, name
            assert "without confirm" not in doc, name
            assert "Deprecated, ignored" not in doc, name
            assert "confirm:" not in doc, name

    # Every document this project ships to a researcher who will paste
    # it at a model. The docstrings are swept above; these are the texts
    # a human hands over, and they are where the instruction survived a
    # round that had already corrected the docstrings and the README.
    SHIPPED_GUIDES = ("AI_CODING_GUIDE.md", "AI_CODING_WORKFLOW.md",
                      "README.md", "QUICKSTART.md", "INSTALL.md")

    @staticmethod
    def _asks_for_the_flag(text):
        """Whether a passage TELLS the reader to pass the flag.

        Saying that `confirm` no longer executes is the opposite of
        asking for it, and README.md's deprecation paragraph must stay,
        so a passage that carries the deprecation is not an offender.
        """
        flat = " ".join(text.split())
        if "no longer executes" in flat or "Deprecated, ignored" in flat:
            return False
        return ("confirm=true" in flat.lower()
                or ", confirm)" in flat
                or "(confirm)" in flat)

    def test_no_shipped_guide_still_asks_for_the_flag(self):
        root = Path(__file__).resolve().parents[1]
        offenders = []
        for name in self.SHIPPED_GUIDES:
            path = root / name
            if not path.exists():
                continue
            for block in path.read_text(encoding="utf-8").split("\n\n"):
                if self._asks_for_the_flag(block):
                    offenders.append((name, " ".join(block.split())[:140]))
        assert offenders == [], offenders

    def test_that_sweep_would_notice(self):
        """Driven both ways, so it cannot pass by never firing."""
        assert self._asks_for_the_flag(
            "Restore the project from <backup name> (restore_backup; "
            "previews first, then confirm=true)")
        assert self._asks_for_the_flag(
            "| `list_backups()` / `restore_backup(backup_path, confirm)` |")
        # ... and an ordinary English "confirm" is not an instruction to
        # pass the flag, which INSTALL.md's relaunch step depends on.
        assert not self._asks_for_the_flag(
            "Fully quit and relaunch the client, then confirm the "
            "installed version with qualcoder-mcp --version.")
        assert not self._asks_for_the_flag(
            "`confirm=true` no longer executes; it returns the preview "
            "with a note and is removed in v0.13.")
        assert not self._asks_for_the_flag(
            "Restore the project from <backup name> (restore_backup; "
            "previews first, then again with the preview_token it "
            "returns)")

    def test_the_readme_lists_the_token_not_the_flag(self):
        readme = (Path(__file__).resolve().parents[1]
                  / "README.md").read_text(encoding="utf-8")
        for name in self.TOKEN_GATED:
            line = next(ln for ln in readme.splitlines()
                        if ln.startswith(f"- `{name}("))
            assert "preview_token" in line, line
            assert "confirm)" not in line, line


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

    def test_a_write_inside_the_restore_window_stops_it(
            self, setup_server, qualcoder_db_path, monkeypatch):
        """B2.4's recheck immediately before `rmtree` (QA round 1, F12).

        It is the only guard for the window between verifying the token
        and destroying the project folder, and that window spans the
        whole safety-backup copy of the project tree, so it is the more
        destructive of the two recheck sites. The codebook path's
        equivalent window is pinned in test_v012_collateral.py; this one
        was not, and replacing the comparison with `if False:` left the
        suite green.

        The racing write lands inside the window by riding on the safety
        backup itself, so no sleep and no wall clock are involved.
        """
        H.execute_destructive(server.delete_code, 2)   # makes a backup
        backups = _backups(qualcoder_db_path)
        backup = str(Path(qualcoder_db_path).parent / backups[-1])
        token = _preview(server.restore_backup, backup)["preview_token"]

        real_backup = server.backup_project

        def racing_backup(folder, *args, **kwargs):
            made = real_backup(folder, *args, **kwargs)
            H.execute(qualcoder_db_path,
                      "INSERT INTO code_name (cid, name, memo, catid, "
                      "owner, date, color) VALUES (99, 'RacedIn', '', NULL, "
                      "'TestCoder', '2024-01-15', '#FF0000')")
            return made

        monkeypatch.setattr(server, "backup_project", racing_backup)
        out = _preview(server.restore_backup, backup, preview_token=token)
        assert out["reason"] == "project_changed", out
        assert out["nothing_changed"] is True
        assert "A backup had already been taken" in out["error"]
        assert Path(out["safety_backup"]).exists()
        # The racing row is still there: the folder was not destroyed.
        assert H.query(qualcoder_db_path,
                       "SELECT COUNT(*) AS n FROM code_name WHERE cid=99"
                       )[0]["n"] == 1


class TestRaceAndStateBinding:
    """D3 6.4 R3 to R8: what the token is bound to, one limb at a time.

    R1 and R2 live in TestTwoStepFlow above (delete_code's rows, and the
    per-operation choice that a second operation does not invalidate the
    first token). The rest were named by the design and never written
    (QA round 1), which left four of the six fingerprint functions
    unpinned: each of these tests fails when the limb it names stops
    being part of the fingerprint.
    """

    @staticmethod
    def _reopen(project_path):
        server.db.close()
        server.db = QualcoderDatabase(project_path)

    def test_r3_a_renamed_destination_refuses_the_merge(
            self, setup_server, qualcoder_db_path):
        token = _preview(server.merge_codes, 1, 2)["preview_token"]
        H.execute(qualcoder_db_path,
                  "UPDATE code_name SET name = 'Coping (renamed)' "
                  "WHERE cid = 2")
        self._reopen(qualcoder_db_path)
        refused = _preview(server.merge_codes, 1, 2, preview_token=token)
        assert refused["reason"] == "project_changed"
        assert refused["nothing_changed"] is True
        assert _backups(qualcoder_db_path) == []
        assert H.query(qualcoder_db_path,
                       "SELECT COUNT(*) AS n FROM code_text WHERE cid=1"
                       )[0]["n"] == 1

    def test_r4_a_new_collision_refuses_the_merge(self, setup_server,
                                                 qualcoder_db_path):
        """The collision set is what the merge would DISCARD, so it is
        the number the user was shown and the one they approved."""
        before = _preview(server.merge_codes, 1, 2)
        assert before["preview"]["text_codings_discarded_as_duplicates"] == 0
        token = before["preview_token"]
        # cid 2 on the same file, span and owner as cid 1's ctid 1: the
        # merge would now drop that row instead of moving it.
        H.execute(qualcoder_db_path,
                  "INSERT INTO code_text (ctid, cid, fid, seltext, pos0, "
                  "pos1, owner, date, memo, important) VALUES "
                  "(91, 2, 1, 'x', 24, 55, 'TestCoder', '2024-01-15', '', 0)")
        self._reopen(qualcoder_db_path)
        refused = _preview(server.merge_codes, 1, 2, preview_token=token)
        assert refused["reason"] == "project_changed"
        assert _backups(qualcoder_db_path) == []

    def test_r5_a_changed_project_refuses_the_restore(self, setup_server,
                                                     qualcoder_db_path):
        H.execute_destructive(server.delete_code, 2)
        backups = _backups(qualcoder_db_path)
        backup = str(Path(qualcoder_db_path).parent / backups[-1])
        token = _preview(server.restore_backup, backup)["preview_token"]
        H.execute(qualcoder_db_path,
                  "INSERT INTO code_name (cid, name, memo, catid, owner, "
                  "date, color) VALUES (92, 'AfterThePreview', '', NULL, "
                  "'TestCoder', '2024-01-15', '#FF0000')")
        self._reopen(qualcoder_db_path)
        refused = _preview(server.restore_backup, backup,
                           preview_token=token)
        assert refused["reason"] == "project_changed"
        assert refused["nothing_changed"] is True
        assert H.query(qualcoder_db_path,
                       "SELECT COUNT(*) AS n FROM code_name WHERE cid=92"
                       )[0]["n"] == 1

    def test_r6_a_newer_backup_refuses_the_prune(self, setup_server,
                                                qualcoder_db_path):
        server.create_code("PruneRaceOne")
        server.create_code("PruneRaceTwo")
        out = _preview(server.prune_backups, keep_last=1)
        would_remove = [b["name"] for b in out["would_remove"]]
        token = out["preview_token"]
        # A third backup appears: what keep_last=1 would remove is no
        # longer the list the user approved.
        server.create_code("PruneRaceThree")
        refused = _preview(server.prune_backups, keep_last=1,
                           preview_token=token)
        assert refused["reason"] == "project_changed"
        assert refused["nothing_changed"] is True
        for name in would_remove:
            assert (Path(qualcoder_db_path).parent / name).exists()

    def test_r8_a_renamed_target_category_is_another_operation(
            self, setup_server, qualcoder_db_path):
        """The RESOLVED id is bound, never the name (B2.1), so a name
        that moves to a different category between the two calls is a
        different operation rather than a changed project."""
        H.execute(qualcoder_db_path,
                  "INSERT INTO code_cat (catid, name, memo, owner, date, "
                  "supercatid) VALUES (2, 'Category B', '', 'TestCoder', "
                  "'2024-01-15', NULL)")
        H.execute(qualcoder_db_path,
                  "INSERT INTO code_cat (catid, name, memo, owner, date, "
                  "supercatid) VALUES (3, 'Category C', '', 'TestCoder', "
                  "'2024-01-15', NULL)")
        self._reopen(qualcoder_db_path)
        token = _preview(server.merge_category, 1,
                         into_category="Category B")["preview_token"]
        H.execute(qualcoder_db_path,
                  "UPDATE code_cat SET name = 'Category B (old)' "
                  "WHERE catid = 2")
        H.execute(qualcoder_db_path,
                  "UPDATE code_cat SET name = 'Category B' WHERE catid = 3")
        self._reopen(qualcoder_db_path)
        refused = _preview(server.merge_category, 1,
                           into_category="Category B", preview_token=token)
        assert refused["reason"] == "token_other_operation"
        assert refused["nothing_changed"] is True
        assert H.query(qualcoder_db_path,
                       "SELECT COUNT(*) AS n FROM code_cat WHERE catid=1"
                       )[0]["n"] == 1

    def test_a_new_child_code_refuses_the_category_delete(
            self, setup_server, qualcoder_db_path):
        """fingerprint_rows_category's child limb, which no test reached:
        the codes that would be moved to the top level are exactly what
        the preview counted."""
        token = _preview(server.delete_category, 1)["preview_token"]
        H.execute(qualcoder_db_path,
                  "INSERT INTO code_name (cid, name, memo, catid, owner, "
                  "date, color) VALUES (93, 'NewChild', '', 1, 'TestCoder', "
                  "'2024-01-15', '#FF0000')")
        self._reopen(qualcoder_db_path)
        refused = _preview(server.delete_category, 1, preview_token=token)
        assert refused["reason"] == "project_changed"
        assert H.query(qualcoder_db_path,
                       "SELECT COUNT(*) AS n FROM code_cat WHERE catid=1"
                       )[0]["n"] == 1

    def test_a_changed_destination_refuses_the_category_merge(
            self, setup_server, qualcoder_db_path):
        """The destination limb of the same fingerprint, which only
        merge_category fills in."""
        H.execute(qualcoder_db_path,
                  "INSERT INTO code_cat (catid, name, memo, owner, date, "
                  "supercatid) VALUES (4, 'Destination', '', 'TestCoder', "
                  "'2024-01-15', NULL)")
        self._reopen(qualcoder_db_path)
        token = _preview(server.merge_category, 1,
                         into_category="Destination")["preview_token"]
        H.execute(qualcoder_db_path,
                  "UPDATE code_cat SET name = 'Destination', "
                  "memo = 'renamed in place' WHERE catid = 4")
        self._reopen(qualcoder_db_path)
        refused = _preview(server.merge_category, 1,
                           into_category="Destination",
                           preview_token=token)
        assert refused["reason"] == "project_changed"
        assert _backups(qualcoder_db_path) == []


class TestTheFingerprintCarriesItsOwnWeight:
    """The rows half of the state, changed where the preview cannot see it.

    `state` is signed over the preview AND the fingerprint rows
    (B2.1), so a change the preview also counts is refused either way:
    the tests above would stay green with `fingerprint_rows_merge_codes`
    or `_prune_fingerprint` returning a constant, which is exactly the
    gap the gate found (QA round 1). Each test here changes something
    the preview does not report, so it fails when its own fingerprint
    function stops looking.
    """

    @staticmethod
    def _reopen(project_path):
        server.db.close()
        server.db = QualcoderDatabase(project_path)

    def test_a_moved_span_refuses_the_delete(self, setup_server,
                                             qualcoder_db_path):
        """Same number of codings, different codings: the preview counts
        one either way, so only the row digest can tell."""
        before = _preview(server.delete_code, 1)
        assert before["preview"]["total_codings_to_delete"] == 1
        H.execute(qualcoder_db_path,
                  "UPDATE code_text SET pos0 = 30, pos1 = 60 WHERE ctid = 1")
        self._reopen(qualcoder_db_path)
        after = _preview(server.delete_code, 1)
        assert after["preview"]["total_codings_to_delete"] == 1
        refused = _preview(server.delete_code, 1,
                           preview_token=before["preview_token"])
        assert refused["reason"] == "project_changed"
        assert H.query(qualcoder_db_path,
                       "SELECT COUNT(*) AS n FROM code_name WHERE cid=1"
                       )[0]["n"] == 1

    def test_a_moved_span_refuses_the_merge(self, setup_server,
                                            qualcoder_db_path):
        token = _preview(server.merge_codes, 1, 2)["preview_token"]
        H.execute(qualcoder_db_path,
                  "UPDATE code_text SET pos0 = 30, pos1 = 60 WHERE ctid = 1")
        self._reopen(qualcoder_db_path)
        refused = _preview(server.merge_codes, 1, 2, preview_token=token)
        assert refused["reason"] == "project_changed"
        assert _backups(qualcoder_db_path) == []

    def test_a_renamed_child_code_refuses_the_category_delete(
            self, setup_server, qualcoder_db_path):
        """The preview counts the codes that would move; the fingerprint
        knows which ones they are."""
        before = _preview(server.delete_category, 1)
        assert before["preview"]["codes_moved_to_top_level"] == 2
        H.execute(qualcoder_db_path,
                  "UPDATE code_name SET name = 'Stress (renamed)' "
                  "WHERE cid = 1")
        self._reopen(qualcoder_db_path)
        after = _preview(server.delete_category, 1)
        assert after["preview"]["codes_moved_to_top_level"] == 2
        refused = _preview(server.delete_category, 1,
                           preview_token=before["preview_token"])
        assert refused["reason"] == "project_changed"

    def test_a_grown_backup_folder_refuses_the_prune(self, setup_server,
                                                    qualcoder_db_path):
        """prune signs a stable core of its preview (the folder names,
        deviation 9 of this batch), so the sizes the user was shown live
        in the fingerprint alone."""
        server.create_code("SizeOne")
        server.create_code("SizeTwo")
        out = _preview(server.prune_backups, keep_last=1)
        doomed = out["would_remove"][0]["name"]
        assert out["would_remove"][0]["size_mb"] > 0
        (Path(qualcoder_db_path).parent / doomed / "grown.bin").write_bytes(
            b"0" * 2_000_000)
        refused = _preview(server.prune_backups, keep_last=1,
                           preview_token=out["preview_token"])
        assert refused["reason"] == "project_changed"
        assert refused["nothing_changed"] is True
        assert (Path(qualcoder_db_path).parent / doomed).exists()


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


class TestWindowsPathBinding:
    """D3 6.8: the project identity a token binds is `os.path.normcase` of
    the resolved data.qda path, which folds case on Windows and does
    nothing on POSIX. Pinned as a pure function with `ntpath` on EVERY
    operating system, so the Windows behaviour is checked on the machine
    that writes the code as well as on the runner."""

    def test_ntpath_folds_case_and_separators(self):
        import ntpath
        a = ntpath.normcase(r"C:\Users\Researcher\Study.qda\data.qda")
        b = ntpath.normcase(r"c:/users/researcher/study.qda/data.qda")
        assert a == b, (a, b)

    def test_posixpath_does_not_fold_case(self):
        import posixpath
        assert posixpath.normcase("/Home/Study.qda/data.qda") != \
            posixpath.normcase("/home/study.qda/data.qda")

    def test_two_windows_spellings_of_one_project_share_a_token(self):
        """The consequence that matters: a host that hands the path back
        in a different case does not invalidate the token."""
        import ntpath
        args = {"code_id": 1}
        one = ntpath.normcase(r"C:\Users\R\Study.qda\data.qda")
        other = ntpath.normcase(r"c:\users\r\study.qda\data.qda")
        token = pt.issue("delete_code", args, one, "s")
        assert pt.verify(token, "delete_code", args, other, "s") == pt.OK

    def test_a_different_project_never_shares_a_token(self):
        import ntpath
        args = {"code_id": 1}
        token = pt.issue("delete_code", args,
                         ntpath.normcase(r"C:\Study.qda\data.qda"), "s")
        assert pt.verify(token, "delete_code", args,
                         ntpath.normcase(r"C:\Other.qda\data.qda"),
                         "s") == pt.OTHER_OPERATION


class TestWhatTheRefusalSaysWhenATokenDoesNotVerify:
    """v0.13 A4, carried from fix round 4.

    A MAC failure whose public `bind` still matches was refused with
    "The project changed since this preview was made", an assertion
    about the project that this branch cannot make. Three different
    things land here and only one of them is about the project: a live
    token whose rows moved, a secret rotated since the preview, and a
    token whose MAC was forged or whose issue time was edited while the
    bind it was copied from stayed as it was. `bind` is public for the
    six codebook tools by design (D3 3.2), so keeping it while changing
    anything else is a one-line edit, not an attack that needs the
    secret. Nothing in the codec can tell the three apart, so the text
    says the one certain thing, lists what causes it and gives the
    single remedy they share.

    Where `project_changed` IS true it is still said, in the words it
    always used: the in-transaction re-check under `_state_guarded`
    fires on a token that DID verify and then found the rows moved
    inside the transaction, and that refusal is pinned in
    test_v012_collateral.py.

    The machine-readable `reason` is deliberately unchanged. It is the
    codec's outcome name, callers and a dozen tests read it, and
    renaming it is an interface change rather than a wording fix; it is
    asserted here so that a future change to it is a deliberate one.
    """

    @staticmethod
    def _forge_mac(token):
        prefix, issued, bind, mac = token.split(".")
        forged = "0" * 32 if mac != "0" * 32 else "1" * 32
        return ".".join([prefix, issued, bind, forged])

    @staticmethod
    def _shift_issue_time(token, seconds=-60):
        prefix, issued, bind, mac = token.split(".")
        return ".".join([prefix, str(int(issued) + seconds), bind, mac])

    def _still_there(self, qualcoder_db_path):
        assert _backups(qualcoder_db_path) == []
        assert H.query(qualcoder_db_path,
                       "SELECT COUNT(*) AS n FROM code_name WHERE cid=1"
                       )[0]["n"] == 1

    @pytest.mark.parametrize("tamper", ["_forge_mac", "_shift_issue_time"])
    def test_a_tampered_token_is_not_called_a_project_change(
            self, setup_server, qualcoder_db_path, tamper):
        token = _preview(server.delete_code, 1)["preview_token"]
        tampered = getattr(self, tamper)(token)
        # The branch the defect lived in: the bind is the real one, so
        # verify() reaches PROJECT_CHANGED rather than OTHER_OPERATION.
        assert tampered.split(".")[2] == token.split(".")[2]
        assert tampered != token

        out = _preview(server.delete_code, 1, preview_token=tampered)

        assert out["reason"] == "project_changed"
        assert out["nothing_changed"] is True
        assert out["error"].startswith("preview_token did not verify")
        assert "The project changed since this preview was made" \
            not in out["error"]
        self._still_there(qualcoder_db_path)

    def test_the_text_names_all_three_and_one_remedy(self, setup_server):
        token = _preview(server.delete_code, 1)["preview_token"]
        out = _preview(server.delete_code, 1,
                       preview_token=self._forge_mac(token))
        error = out["error"]
        assert "the project changed since the preview" in error
        assert "preview secret has been rotated" in error
        assert "not one this server issued for this state" in error
        assert "the remedy is the same for all of them" in error
        assert "without preview_token for a fresh preview" in error
        # Count-free and name-free, as every refusal in this server is.
        assert not re.search(r"\d", error.replace("{tool}", ""))

    def test_a_project_that_really_changed_gets_the_same_text(
            self, setup_server, qualcoder_db_path):
        """The honest half: the likeliest cause is still named first,
        and a live token whose rows moved still lands here."""
        token = _preview(server.delete_code, 1)["preview_token"]
        H.execute(qualcoder_db_path,
                  "UPDATE code_name SET name = 'Stress (renamed)' "
                  "WHERE cid = 1")
        server.db.close()
        server.db = QualcoderDatabase(qualcoder_db_path)
        out = _preview(server.delete_code, 1, preview_token=token)
        assert out["reason"] == "project_changed"
        assert out["error"].startswith("preview_token did not verify")
        assert "the rows this operation would affect are no longer " \
               "exactly those previewed" in out["error"]

    def test_the_in_transaction_refusal_keeps_the_claim_it_can_make(self):
        """The distinction the fix preserves, as two different strings.

        `_state_guarded` fires on a token that verified, so there the
        project really did change and the text says so.
        """
        verified = server.TOKEN_ERROR_TEXTS["project_changed"]
        pre_verify = server.TOKEN_ERROR_TEXTS["project_changed_or_rotated"]
        assert verified.startswith(
            "The project changed since this preview was made")
        assert not pre_verify.startswith(
            "The project changed since this preview was made")
        assert verified != pre_verify
        # And the one that can only guess does not claim the certainty
        # of the one that knows.
        assert "did not verify" in pre_verify
        assert "did not verify" not in verified

    def test_the_new_text_keeps_the_house_rules(self):
        _house_rules([server.TOKEN_ERROR_TEXTS["project_changed_or_rotated"]],
                     ["project_changed_or_rotated"])


class TestHouseRulesOnTheNewTexts:

    def test_every_new_text_of_this_item(self):
        texts = list(server.TOKEN_ERROR_TEXTS.values()) + [
            server.TWO_STEP_PARAGRAPH,
            pt.SECRET_UNAVAILABLE_MESSAGE,
            server.merge_codes.__doc__ or "",
            server.delete_code.__doc__ or "",
            # the two file-level tools, whose two-step paragraphs this
            # fix round wrote (QA round 1, priority 5)
            server.restore_backup.__doc__ or "",
            server.prune_backups.__doc__ or "",
        ]
        _house_rules(texts)


# =============================================================================
# A TOKEN HAS EXACTLY ONE SPELLING (fix round 4, S9 and S10)
# =============================================================================

class TestTokenGrammar:
    """verify() gated the timestamp with str.isdigit() and then called
    int() on it. isdigit() is True for superscripts and circled digits
    that int() REJECTS, so a crafted token left verify() by raising
    instead of being refused, and the caller lost the fixed refusal
    envelope for a raw Python message. In the other direction isdigit()
    accepts fullwidth and Arabic-Indic digits that int() DECODES to the
    same integer, so a live token re-spelled in another script verified
    OK, because the MAC is computed over the decoded integer."""

    ARGS = {"code_id": 1}
    PROJECT = "/p/data.qda"
    STATE = "state-fingerprint"

    @pytest.fixture
    def token(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pt, "STATE_HOME", tmp_path / "state")
        return pt.issue("delete_code", self.ARGS, self.PROJECT, self.STATE)

    def _verify(self, token):
        return pt.verify(token, "delete_code", self.ARGS, self.PROJECT,
                         self.STATE)

    def test_the_token_it_issued_verifies(self, token):
        assert self._verify(token) == pt.OK

    @pytest.mark.parametrize("digits", [
        "０１２３４５６７８９",
        "٠١٢٣٤٥٦٧٨٩",
        "०१२३४५६७८९",
    ], ids=["fullwidth", "arabic-indic", "devanagari"])
    def test_a_respelled_timestamp_is_refused_not_accepted(self, token,
                                                           digits):
        prefix, issued, bind, mac = token.split(".")
        respelled = "".join(digits[int(c)] for c in issued)
        assert respelled != issued
        assert int(respelled) == int(issued)     # the codec's own reading
        other = ".".join((prefix, respelled, bind, mac))
        assert self._verify(other) == pt.MALFORMED

    @pytest.mark.parametrize("odd", ["²", "³", "①",
                                     "₂", "۱"])
    def test_a_digit_int_would_reject_is_refused_not_raised(self, token,
                                                            odd):
        prefix, issued, bind, mac = token.split(".")
        assert odd.isdigit() or odd.isdecimal()
        other = ".".join((prefix, odd * len(issued), bind, mac))
        assert self._verify(other) == pt.MALFORMED

    def test_a_non_ascii_field_is_refused_not_raised(self, token):
        """hmac.compare_digest REFUSES a non-ASCII str and raises
        TypeError; the gate has to come before it."""
        prefix, issued, bind, mac = token.split(".")
        for other in (".".join((prefix, issued, "٠" * 8, mac)),
                      ".".join((prefix, issued, bind, "٠" * 32)),
                      ".".join((prefix, issued, bind, "ａ" * 32))):
            assert self._verify(other) == pt.MALFORMED

    def test_leading_zeros_are_a_second_spelling_and_are_refused(self,
                                                                 token):
        prefix, issued, bind, mac = token.split(".")
        other = ".".join((prefix, "0" * 4 + issued, bind, mac))
        assert int(other.split(".")[1]) == int(issued)
        assert self._verify(other) == pt.MALFORMED

    def test_upper_case_hex_is_a_second_spelling_and_is_refused(self,
                                                                token):
        prefix, issued, bind, mac = token.split(".")
        assert self._verify(
            ".".join((prefix, issued, bind.upper(), mac))) == pt.MALFORMED
        assert self._verify(
            ".".join((prefix, issued, bind, mac.upper()))) == pt.MALFORMED

    def test_an_underscore_separator_is_refused(self, token):
        """int(x, 16) accepts '1_2'; the grammar does not."""
        prefix, issued, bind, mac = token.split(".")
        assert self._verify(
            ".".join((prefix, issued, "1_2ab3cd", mac))) == pt.MALFORMED

    def test_an_overlong_timestamp_cannot_reach_int(self, token):
        prefix, issued, bind, mac = token.split(".")
        assert self._verify(
            ".".join((prefix, "9" * 5000, bind, mac))) == pt.MALFORMED

    def test_the_six_gated_tools_keep_the_fixed_refusal_envelope(
            self, setup_server, qualcoder_db_path):
        """The consequence that mattered: a raised ValueError reached
        _tool_guard's generic branch and the caller got a raw Python
        message with no reason and no nothing_changed."""
        crafted = "qcp1.²²²².abcdef12." + "a" * 32
        for out in (_preview(server.delete_code, 1, preview_token=crafted),
                    _preview(server.merge_codes, 1, 2,
                             preview_token=crafted),
                    _preview(server.delete_category, 1,
                             preview_token=crafted),
                    _preview(server.merge_category, 1,
                             preview_token=crafted)):
            assert out["reason"] == "token_malformed", out
            assert out["nothing_changed"] is True, out
            assert "invalid literal" not in json.dumps(out)
