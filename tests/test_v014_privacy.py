# SPDX-License-Identifier: LGPL-3.0-or-later
"""v0.14 privacy: what the run record, the error answers and the log keep.

Each class is one item of the v0.14 privacy brief:

1. The run's fingerprints. The run's result no longer carries a digest of
   each file's text before the run, and the run record fingerprints the
   text before and after the run with a digest keyed with the token
   secret (format 3), so neither, beside the pseudonymised text,
   confirms a guessed name. The tool's list of what it does not rewrite
   names the stored copy in `documents/`.

The fixture and helpers are the flagship's own
(`test_v012_pseudonymise_tool.py`), imported so this file drives exactly
the project every other pin drives.
"""

import hashlib
import hmac
import itertools
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qualcoder_mcp.server as server
from qualcoder_mcp import preview_tokens as pt
from test_v012_pseudonymise_tool import (  # noqa: F401  (`project` is a fixture)
    TEXT, execute_from, preview_of, project, query)

# The label a text digest in the run record is keyed over, written out
# here rather than imported, so a change to it is a change a test sees.
TEXT_LABEL = b"qualcoder-mcp run record text\n"

# No variants, so one guessed name per entry rebuilds the text before
# the run exactly (the flagship's MAPPING also maps "Tom").
PLAIN_MAPPING = [{"original": "Thomas", "pseudonym": "Alex"},
                 {"original": "Mary Ann", "pseudonym": "Sam"}]


def _run(project):
    result = execute_from(preview_of(mapping=PLAIN_MAPPING),
                          mapping=PLAIN_MAPPING)
    assert result.get("success") is True, result
    record = json.loads(Path(result["manifest_path"]).read_text(
        encoding="utf-8"))
    new_text = query(project, "SELECT fulltext FROM source WHERE id=1"
                     )[0]["fulltext"]
    return result, record, new_text


def _put_back(new_text, spans, by_entry):
    """The text before the run as a guess rebuilds it: each pseudonym's
    span in the new text replaced by the guessed name for its entry."""
    text = new_text
    for span in sorted(spans, key=lambda s: -s["new_span"][0]):
        start, end = span["new_span"]
        text = text[:start] + by_entry[span["entry"]] + text[end:]
    return text


def _every_string(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _every_string(item)
    elif isinstance(value, list):
        for item in value:
            yield from _every_string(item)
    elif isinstance(value, str):
        yield value


def _keyed(text):
    return hmac.new(pt.load_secret().encode("ascii"),
                    TEXT_LABEL + text.encode("utf-8"),
                    hashlib.sha256).hexdigest()


# =============================================================================
# 1. THE RUN'S FINGERPRINTS
# =============================================================================

class TestTheRunsFingerprints:

    # The v0.13 release's security gate ran the same attack with 3,000
    # names and recovered both in a fifth of a second.
    GUESSES = ["Anna", "Tom", "Thomas", "Mary", "Mary Ann", "Peter", "Sue"]

    def _confirmed(self, record, new_text):
        """Every guess the record confirms: a name put back where each
        pseudonym sits, the text's plain SHA-256 compared with every
        string anywhere in the record."""
        item = record["files"][0]
        held = set(_every_string(record))
        entries = sorted({s["entry"] for s in item["replacements"]})
        found = []
        for guess in itertools.product(self.GUESSES, repeat=len(entries)):
            by_entry = dict(zip(entries, guess))
            text = _put_back(new_text, item["replacements"], by_entry)
            if hashlib.sha256(text.encode("utf-8")).hexdigest() in held:
                found.append(by_entry)
        return found

    def test_the_attack_rebuilds_the_text_before_the_run(self, project):
        """The control half: the right guess rebuilds the old text
        exactly, so the pin below cannot pass because the attack is
        broken."""
        _, record, new_text = _run(project)
        assert new_text != TEXT
        assert _put_back(new_text, record["files"][0]["replacements"],
                         {0: "Thomas", 1: "Mary Ann"}) == TEXT

    def test_the_record_confirms_no_guessed_name(self, project):
        _, record, new_text = _run(project)
        assert self._confirmed(record, new_text) == []

    def test_the_record_keys_both_texts_with_the_secret(self, project):
        _, record, new_text = _run(project)
        assert record["format"] == 3
        item = record["files"][0]
        assert "old_fingerprint" not in item
        assert "new_fingerprint" not in item
        assert item["old_length"] == len(TEXT)
        assert item["new_length"] == len(new_text)
        assert item["old_text_hmac_sha256"] == _keyed(TEXT)
        assert item["new_text_hmac_sha256"] == _keyed(new_text)

    def test_neither_digest_is_the_plain_or_unlabelled_one(self, project):
        """Keyed over the label as well as the text: without it the
        digest of a text would be the digest of any other value keyed
        with the same secret that the text was built to spell."""
        _, record, new_text = _run(project)
        item = record["files"][0]
        secret = pt.load_secret().encode("ascii")
        for text, key in ((TEXT, "old_text_hmac_sha256"),
                          (new_text, "new_text_hmac_sha256")):
            data = text.encode("utf-8")
            assert item[key] != hashlib.sha256(data).hexdigest()
            assert item[key] != hmac.new(secret, data,
                                         hashlib.sha256).hexdigest()

    def test_the_result_carries_no_digest_of_the_text_before_the_run(
            self, project):
        result, _, new_text = _run(project)
        item = result["files"][0]
        assert "old_sha256" not in item
        old = hashlib.sha256(TEXT.encode("utf-8")).hexdigest()
        assert not [s for s in _every_string(result) if old in s]
        # What stays: the lengths, which the preview already gives, and
        # the digest of the new text, which the reader can read.
        assert item["old_length"] == len(TEXT)
        assert item["new_length"] == len(new_text)
        assert item["new_sha256"] == hashlib.sha256(
            new_text.encode("utf-8")).hexdigest()

    def test_the_preview_gives_the_same_two_lengths(self, project):
        """PRIVACY.md says the lengths the record and the result keep
        plain are the ones the preview already gives the conversation."""
        out = preview_of(mapping=PLAIN_MAPPING)
        item = out["preview"]["files"][0]
        result = execute_from(out, mapping=PLAIN_MAPPING)
        assert item["text_length"] == result["files"][0]["old_length"]
        assert item["new_text_length"] == result["files"][0]["new_length"]

    def test_the_documents_say_what_an_old_record_holds(self):
        privacy = " ".join((Path(__file__).parent.parent / "PRIVACY.md"
                            ).read_text(encoding="utf-8").split())
        assert ("Records written before v0.14 (format 1 by v0.12, format 2 "
                "by v0.13) carry each file's length and plain SHA-256 "
                "before and after the run instead") in privacy
        assert ("the keyed digests in the run manifests already written "
                "can then no longer be checked") in privacy
        assert "Deleting it invalidates outstanding preview tokens, which " \
               "means the next execute asks for a fresh preview; nothing " \
               "else." not in privacy

    def test_the_does_not_rewrite_list_names_the_stored_copy(self):
        doc = " ".join(server.pseudonymise_source.__doc__.split())
        start = doc.index("What this does NOT rewrite")
        listed = doc[start:doc.index("Case and file names are changed",
                                     start)]
        assert "stored copy" in listed
        assert "documents/" in listed
