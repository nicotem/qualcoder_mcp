"""Deterministic keyset cursors for paged reads (v0.12, D4 3.2).

A cursor is a POSITION, not a permission and not a stored result set. It
carries the sort key of the last item a page returned, and the next page
is computed by re-querying for the first item that sorts strictly after
it. Nothing is stored anywhere: no server-side result set, no session
file, no memory. A host that recycles the process between turns can hand
the same cursor back and get the same continuation, and two hosts walking
one project do not interfere.

These tokens are deliberately NOT the authorisation tokens of
`preview_tokens.py` (D4 3.11, H1). Those prove that a preview of a
destructive operation was computed and are signed; these say "carry on
from here" and are signed by nothing, because a caller who forges a
position gets a page it could have asked for anyway (D4 5.4). The two
prefixes, `c1.` and `qcp1.`, stay distinct so neither is ever mistaken
for the other.

The token is bound to the tool and to the call's other arguments through
a fingerprint, so a cursor cannot be replayed against a different query,
a different coder filter, or a different tool.
"""

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

CURSOR_PREFIX = "c1."
# A key of five short values plus the fingerprint fits in well under 200
# characters; the cap is a hostile-input bound, not a design limit.
CURSOR_MAX_LENGTH = 1024

# Tool tags. Short because the token travels in every paged result.
TAG_SEARCH_FILES = "sf"
TAG_SEARCH_CODED_TEXT = "sct"
TAG_CODED_SEGMENTS = "gcs"

CURSOR_TOO_LONG = "cursor is too long (limit 1024 characters)."
# The running total a cursor carries is the caller's claim about the
# pages BEFORE this one; nothing here can verify it, and it is reported
# in the result as `returned_so_far`. Bound it so a tampered cursor
# cannot put an arbitrary integer in front of a researcher as though
# this server had counted it. The bound is far above any real walk: a
# project with this many coded segments would exhaust the character
# budget thousands of pages earlier (fix round 4).
CURSOR_MAX_RETURNED_SO_FAR = 10_000_000

DATABASE_CHANGED_NOTE = (
    "The project database changed after this cursor was issued; positions "
    "are recomputed on every page, but counts and ordering may differ from "
    "the earlier pages.")


class CursorError(ValueError):
    """An unusable cursor. The message never echoes the token."""


def cursor_invalid_message(tool_name: str) -> str:
    """The one text every unusable cursor gets (D4 3.7).

    Garbage, a token minted for another tool, a token minted for other
    arguments and a token whose key has the wrong shape are one failure
    from the caller's point of view, and the answer is the same: start
    again without the cursor. The token is never echoed back, so a
    tampered value cannot smuggle text into the conversation.
    """
    return (f"cursor is not valid for {tool_name} with these arguments. "
            f"Call the tool again without cursor to start from the "
            f"beginning.")


def _canonical(obj: Any) -> str:
    """The canonical JSON this module hashes and encodes."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)


def fingerprint_arguments(tool: str, arguments: Dict[str, Any]) -> str:
    """A 16-hex digest binding a cursor to this call's other arguments.

    The caller passes the arguments already canonicalised (defaults
    filled in, order-irrelevant lists sorted and de-duplicated, `coder`
    normalised), so a call that means the same thing produces the same
    fingerprint whatever order the host serialised it in. It carries no
    authority: it decides only whether a cursor belongs to this query.
    """
    payload = {"t": tool, "a": arguments}
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:16]


def database_stamp(qda_path: Any) -> Optional[List[int]]:
    """`(st_mtime_ns, st_size)` of data.qda, or None when unavailable.

    Both values travel INSIDE the cursor and therefore into the
    transcript; PRIVACY.md enumerates them, and `list_available_projects`
    already reports the same two in plain form (fix round 4).

    A HEURISTIC (D4 3.2.5) and labelled as one wherever it is reported:
    mtime granularity, journal and WAL side files, and copy tools that
    preserve timestamps all mean a changed database can look unchanged
    and an unchanged one can look changed. Correctness never depends on
    it; it only lets a page say that the ground may have moved.
    """
    try:
        st = os.stat(str(qda_path))
    except OSError:
        return None
    return [st.st_mtime_ns, st.st_size]


def encode_cursor(tool_tag: str, fingerprint: str, key: Sequence[Any],
                  returned_so_far: int,
                  stamp: Optional[List[int]]) -> str:
    """Mint a cursor for the item last returned."""
    payload = {
        "t": tool_tag,
        "f": fingerprint,
        "k": list(key),
        "n": int(returned_so_far),
        "d": list(stamp) if stamp else [],
    }
    raw = _canonical(payload).encode("utf-8")
    return CURSOR_PREFIX + base64.urlsafe_b64encode(raw).decode(
        "ascii").rstrip("=")


def decode_cursor(token: Any, tool_tag: str, fingerprint: str,
                  key_shape: Sequence[type]) -> Tuple[List[Any], int,
                                                      Optional[List[int]]]:
    """Read a cursor, or raise CursorError.

    Every check is a refusal, never a repair: the prefix, the length, the
    base64, the JSON shape, the exact key set, the tool tag, the
    fingerprint of the current arguments, the shape of the sort key and a
    non-negative count. A cursor that fails any of them is not a cursor
    for this call.

    Returns:
        (key, returned_so_far, stamp)
    """
    if not isinstance(token, str):
        raise CursorError("cursor must be a string")
    token = token.strip()
    if len(token) > CURSOR_MAX_LENGTH:
        raise CursorError(CURSOR_TOO_LONG)
    if not token.startswith(CURSOR_PREFIX):
        raise CursorError("cursor has an unknown format")
    body = token[len(CURSOR_PREFIX):]
    padding = "=" * (-len(body) % 4)
    try:
        raw = base64.urlsafe_b64decode(body + padding)
        data = json.loads(raw.decode("utf-8"))
    except Exception as e:                       # noqa: BLE001 - any failure
        raise CursorError("cursor could not be decoded") from e
    if not isinstance(data, dict) or set(data) != {"t", "f", "k", "n", "d"}:
        raise CursorError("cursor has the wrong shape")
    if data["t"] != tool_tag:
        raise CursorError("cursor belongs to another tool")
    if not isinstance(data["f"], str) or data["f"] != fingerprint:
        raise CursorError("cursor belongs to another query")
    key = data["k"]
    if not isinstance(key, list) or len(key) != len(key_shape):
        raise CursorError("cursor key has the wrong shape")
    for value, expected in zip(key, key_shape):
        if expected is str:
            if not isinstance(value, str):
                raise CursorError("cursor key has the wrong shape")
        elif expected is int:
            if not isinstance(value, int) or isinstance(value, bool):
                raise CursorError("cursor key has the wrong shape")
        elif expected is object:
            # A nullable text column: a string or None
            if value is not None and not isinstance(value, str):
                raise CursorError("cursor key has the wrong shape")
    n = data["n"]
    if (not isinstance(n, int) or isinstance(n, bool) or n < 0
            or n > CURSOR_MAX_RETURNED_SO_FAR):
        raise CursorError("cursor count is not a count")
    stamp = data["d"]
    if not isinstance(stamp, list) or not all(
            isinstance(v, int) and not isinstance(v, bool) for v in stamp):
        raise CursorError("cursor stamp has the wrong shape")
    return key, n, (stamp or None)


def page_block(limit: int, returned: int, returned_so_far: int,
               has_more: bool, next_cursor: Optional[str],
               exhaustive: bool) -> Dict[str, Any]:
    """The `page` block every paged result carries (D4 3.2.7).

    `returned`, `has_more` and `exhaustive` are computed on this page.
    `returned_so_far` is this page's `returned` added to the count the
    CURSOR carried, so on any page but the first it rests on a value
    the caller supplied and nothing here can check. It is bounded at
    decode (CURSOR_MAX_RETURNED_SO_FAR) so a tampered cursor cannot put
    an arbitrary integer in a result, and PRIVACY.md says plainly where
    the number comes from; a stronger guarantee would mean signing
    cursors, which D4 3.11 deliberately does not do, because a caller
    who forges a POSITION only gets a page it could have asked for.
    """
    return {
        "limit": limit,
        "returned": returned,
        "returned_so_far": returned_so_far,
        "has_more": has_more,
        "next_cursor": next_cursor,
        "exhaustive": exhaustive,
    }
