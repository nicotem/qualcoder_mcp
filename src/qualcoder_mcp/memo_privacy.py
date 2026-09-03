"""Memo privacy: QualCoder's '#####' personal-note convention.

QualCoder 4.0 lets researchers keep the tail of any memo private from
the AI: everything from the first '#####' marker onward is never shown
to the model, and AI memo updates preserve that private suffix
verbatim (upstream src/qualcoder/ai_memo.py:28-59 at pin 9bddf17,
applied to tool results in ai_mcp_server.py:210-223). This server
honors the same convention on every memo it returns to the client and
on every memo it writes, so a project touched by both tools keeps the
same promise to the researcher.

Behavior is matched to upstream ai_memo.py exactly (independent MIT
implementation of the same contract; upstream is LGPL):

- Split: the FIRST marker wins; the private suffix starts AT the
  marker, marker included. Text with no marker is entirely public.
  A marker at position 0 makes the whole memo private. A marker with
  nothing after it still creates a (marker-only) private suffix.
- Extract: the public part is returned byte-for-byte, including any
  whitespace that preceded the marker. No annotation is added: the
  strip is silent by owner ruling.
- Merge (AI writes): the AI-provided text is itself reduced to its
  public part first, so an AI write can never create a private zone,
  and never reads, replaces, or deletes an existing one. An existing
  private suffix is preserved verbatim, re-joined with the whitespace
  run that separated the old public text from the marker. Writing an
  empty public text against a memo with a private suffix leaves just
  the suffix (the row keeps the researcher's private note).

The one deliberate exception, ruled by the owner for parity with
QualCoder's own exports: file exports (REFI-QDA, codebook files,
report and CSV files) carry FULL memos, marker and suffix included.
Export tools disclose this in their descriptions; tools that return
memo content into the AI conversation always strip.
"""

import re
from typing import Any, Tuple

# The marker QualCoder 4.0 documents for private memo tails
# (upstream ai_memo.py:28).
PERSONAL_NOTE_MARK = "#####"

# Any run of five or more hashes contains the marker. neutralize_marker
# collapses the whole run: a plain .replace("#####", "####") would turn
# "######" into "#####" and re-form the marker it meant to remove.
_MARKER_RUN_RE = re.compile(r"#{5,}")

# Payload keys treated as memo text by strip_private_memos. Journal
# entries are exposed under 'content' by this server and are stripped
# at their build site instead ('content' also names file fulltext, so
# it cannot be a blanket key here).
_MEMO_KEYS = frozenset({"memo"})

# The whitespace characters upstream treats as the public/private
# separator when re-joining a preserved suffix (ai_memo.py:57).
_SEPARATOR_CHARS = " \t\r\n"


def split_public_private_memo(memo: Any) -> Tuple[str, str]:
    """Split a memo into (public_text, private_suffix).

    The private suffix starts at the FIRST marker and includes it;
    it is empty when no marker is present. None is treated as ''.
    """
    text = "" if memo is None else str(memo)
    mark = text.find(PERSONAL_NOTE_MARK)
    if mark < 0:
        return text, ""
    return text[:mark], text[mark:]


def extract_ai_memo(memo: Any) -> str:
    """The part of a memo that may be shown to the AI (public text)."""
    public, _private = split_public_private_memo(memo)
    return public


def neutralize_marker(text: Any) -> str:
    """Make a non-memo string safe to embed in a memo's public zone.

    Code and category names and owner strings are written verbatim into
    merge provenance blocks. They carry no privacy semantics of their
    own, so a '#####' inside one would otherwise plant a private zone in
    the target memo and hide everything after it (including the merged
    source memo) from every AI read. Every run of five or more hashes is
    collapsed to four; text without such a run is returned unchanged.
    None is treated as ''.
    """
    return _MARKER_RUN_RE.sub("####", "" if text is None else str(text))


def merge_public_memo(existing_memo: Any, new_public_memo: Any) -> str:
    """Replace a memo's public text, preserving any private suffix.

    The new text is reduced to its own public part first (an AI write
    cannot smuggle a marker in). With no existing suffix this is a
    plain replace; with one, the suffix survives verbatim, joined by
    the whitespace run that preceded the old marker.
    """
    existing_public, private_suffix = split_public_private_memo(existing_memo)
    public = extract_ai_memo(new_public_memo)
    if private_suffix == "":
        return public
    if public == "":
        return private_suffix
    trimmed = existing_public.rstrip(_SEPARATOR_CHARS)
    separator = existing_public[len(trimmed):]
    return public + separator + private_suffix


def strip_private_memos(value: Any) -> Any:
    """Recursively strip private suffixes from a result payload.

    Walks dicts, lists and tuples; every string under a 'memo' key is
    reduced to its public part (upstream's payload sanitizer contract,
    ai_mcp_server.py:210-223). Everything else passes through
    unchanged. Returns a new structure; the input is not mutated.
    """
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if key in _MEMO_KEYS and isinstance(item, str):
                cleaned[key] = extract_ai_memo(item)
            else:
                cleaned[key] = strip_private_memos(item)
        return cleaned
    if isinstance(value, list):
        return [strip_private_memos(item) for item in value]
    if isinstance(value, tuple):
        return tuple(strip_private_memos(item) for item in value)
    return value
