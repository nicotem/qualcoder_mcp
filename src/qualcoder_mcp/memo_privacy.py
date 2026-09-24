"""Memo privacy: QualCoder's '#####' personal-note convention.

QualCoder 4.0 lets researchers keep the tail of any memo private from
the AI: everything from the first '#####' marker onward is never shown
to the model, and AI memo updates preserve that private suffix
verbatim (upstream src/qualcoder/ai_memo.py:28-59 at pin 9bddf17,
applied to tool results in ai_mcp_server.py:210-223). This server
honours the same convention on every memo it returns to the client and
on every memo it writes, so a project touched by both tools keeps the
same promise to the researcher.

Behaviour is matched to upstream ai_memo.py exactly (independent MIT
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
from typing import Any, Callable, Optional, Tuple

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


def rewrite_public_memo(stored: Any,
                        rewrite: Callable[[str], str]) -> Optional[str]:
    """Apply `rewrite` to the public part of a note; the private part is
    carried across verbatim and never read.

    The public part is a PREFIX of the stored text
    (`split_public_private_memo` returns `text[:mark]`), so an offset in
    the public part is an offset in the stored note and the two halves
    concatenate back without a separator. Returns the new stored text,
    or None when the note would not read back with the rewritten public
    part: the rewrite made a marker inside the public part, or across its
    boundary with the private part (a pseudonym ending in a hash, written
    just before the marker, moves the marker earlier; Brief 2 fix round
    1, QA-B2-3). Writing it would hide part of the researcher's note
    from every later AI read, or move a pseudonym behind the marker, so
    the caller leaves that note as it is and counts it.

    Not `merge_public_memo`, on purpose. That function re-adds the
    whitespace run that preceded the old marker, and a rewrite that
    substitutes names never touches that run (a surface form cannot
    begin or end with whitespace), so the rewritten public part already
    ends in it: merging would double it. Concatenation gives, byte for
    byte, what `merge_public_memo(stored, new_public.rstrip(" \\t\\r\\n"))`
    gives, and `merge_public_memo` itself stays matched to upstream
    (the pseudonymisation tool's note rewrite, v0.13 Brief 2, 4.3).
    The old public part can never contain a marker (it ends where the
    first one begins), so a marker in the new one was MADE by the
    rewrite: hashes already in the note meeting a pseudonym, or two
    pseudonyms meeting across a character that is not a word character.
    One test covers both places: the new stored text, split again, must
    give back exactly the new public part.
    """
    public, private = split_public_private_memo(stored)
    new_public = rewrite(public)
    new_stored = new_public + private
    if split_public_private_memo(new_stored)[0] != new_public:
        return None
    return new_stored


def strip_private_memos(value: Any) -> Any:
    """Recursively strip private suffixes from a result payload.

    Walks dicts, lists and tuples; every string under a 'memo' key is
    reduced to its public part (upstream's payload sanitiser contract,
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
