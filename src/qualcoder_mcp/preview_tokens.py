# SPDX-License-Identifier: LGPL-3.0-or-later
"""Authorisation tokens for destructive operations (v0.12, D3; H1).

A destructive tool executes only with proof that a preview of EXACTLY
this operation was computed and that the rows it covers have not changed
since. The proof is a token the preview issues and the execute presents.

Why a token rather than a `confirm` flag: `confirm=true` says "yes" to
whatever the tool is asked to do now, which may not be what the preview
the user saw described. A token is bound to the tool, to the arguments
that decide the effect, to the project, and to a fingerprint of the rows
the operation would touch, so a stale preview cannot authorise a changed
operation. It is also stateless: the token carries its own claims and the
server verifies them by recomputation, so a host that recycles the server
process between the preview and the execute changes nothing.

This module is the ONLY authorisation-token codec in this server. Every
gated tool is one row in `REGISTRY` below, which says how to canonicalise
its arguments; there is no tool-specific branch anywhere else, so adding
a tool (the flagship's `pseudonymise_source` next) is one row. A second
table, `KEYED_BIND`, names the tools whose bound arguments carry a value
the conversation never supplied; for those the public `bind` is keyed
with the secret, because an unkeyed digest of a secret is a confirmation
oracle for it (fix round 3, S2).

B4's cursor tokens are a different thing and deliberately do not share
this codec (D4 3.11): a cursor is a position and is replayable by design;
these are authorisations. The prefixes `qcp1.` and `c1.` keep them
distinguishable at a glance.
"""

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import stat
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

TOKEN_PREFIX = "qcp1"
# The grammar of a token this server issues, so exactly one spelling of
# each AUTHENTICATED field verifies. `bind` is outside that claim by
# design: it is non-authoritative (D3 3.2), the MAC does not cover it,
# and a token with `bind` overwritten still verifies, which a gate
# confirmed by experiment. Nothing downstream reads it from the token;
# the flagship recomputes it server-side. Public for the six codebook
# tools, whose bound arguments are ids the conversation already holds;
# keyed with the secret for the flagship, whose bound mapping can be the
# researcher's reverse key (`KEYED_BIND`). `[0-9]`, never `\d`: in
# a str pattern `\d`
# matches every Unicode Nd digit and `int()` decodes fullwidth,
# Arabic-Indic, Devanagari and the rest to the same integer, so a live
# token re-spelled in another digit script used to verify OK, because
# the MAC is computed over the DECODED integer. The 12-digit bound also
# makes `int()` below total (CPython refuses an int-str conversion past
# 4300 digits), and the hex runs are lower-case because that is what
# `hexdigest()` produces (fix round 4).
_ISSUED_RE = re.compile(r"[0-9]{1,12}")
_BIND_RE = re.compile(r"[0-9a-f]{8}")
_MAC_RE = re.compile(r"[0-9a-f]{32}")
TOKEN_VERSION = 1
# Sixty minutes back, five forward: enough for a researcher to read a
# preview and decide, and enough tolerance for a host whose clock is a
# few minutes off without making a stale preview usable tomorrow.
TOKEN_MAX_AGE_SECONDS = 60 * 60
TOKEN_MAX_SKEW_SECONDS = 5 * 60
TOKEN_VALID_FOR_MINUTES = 60

SECRET_FILENAME = "preview_secret"
SECRET_READ_MAX_BYTES = 4096
SECRET_HEX_CHARS = 64

# Where the secret lives. A module-level Path so the test suite can
# isolate it exactly as it isolates the MRU file.
STATE_HOME = Path.home() / ".qualcoder_mcp"


class PreviewSecretUnavailable(Exception):
    """The secret could not be created or read; no token can be trusted."""


SECRET_UNAVAILABLE_MESSAGE = (
    "Could not read or create the preview-token secret in "
    "~/.qualcoder_mcp: check permissions; nothing was changed.")


def state_home() -> Path:
    """The state folder, read through the module attribute.

    A function rather than a direct import, so a test that isolates
    STATE_HOME isolates it for every caller, including the export-path
    guard in the server layer.
    """
    return STATE_HOME


def _now() -> int:
    """Unix seconds. Injectable so no test depends on the wall clock."""
    return int(time.time())


def canonical(obj: Any) -> str:
    """The canonical JSON this module signs.

    Sorted keys and compact separators, so the same operation described
    in a different argument order signs identically; `ensure_ascii=False`
    with an explicit UTF-8 encode, so a non-ASCII name signs as itself
    rather than as an escape sequence (upstream signs its own records the
    same way, ai_mcp_server.py:322, :342 at 9bddf17).
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def _secret_path() -> Path:
    return STATE_HOME / SECRET_FILENAME


def _valid_secret(raw: str) -> Optional[str]:
    """The secret if it is 64 hex characters, else None."""
    text = raw.strip()
    if len(text) != SECRET_HEX_CHARS:
        return None
    try:
        int(text, 16)
    except ValueError:
        return None
    return text.lower()


def ensure_state_dir(path: Path) -> None:
    """Create one of this server's state folders, owner-only, and
    tighten a wider one.

    It holds the token secret and the MRU pointer, so the group and
    other bits have no business being set. `mkdir` alone applies the
    umask, which on a default macOS or Linux account leaves 0755: every
    local account could list the folder and stat the secret. Creating at
    0700 and narrowing an existing folder costs nothing and is not
    undone by the next start (fix round 4).

    Mode bits are meaningless on Windows, where this is a no-op beyond
    the mkdir.
    """
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "nt":
        return
    try:
        mode = stat.S_IMODE(os.stat(str(path)).st_mode)
    except OSError:
        return
    if mode & 0o077:
        try:
            os.chmod(str(path), mode & ~0o077)
        except OSError:
            logger.warning("The qualcoder-mcp state folder is readable by "
                           "other users on this machine and could not be "
                           "narrowed.")


def ensure_state_home() -> None:
    """`ensure_state_dir` for the token secret's own folder."""
    ensure_state_dir(STATE_HOME)


def _publish_exclusive(tmp_name: str, path: Path,
                       windows: Optional[bool] = None) -> None:
    """Give a complete temp file the final name, or raise FileExistsError.

    Two spellings of one rule, because the platforms differ on what
    "create only if absent" costs:

    - POSIX: `os.link`, which never replaces and raises FileExistsError.
      `os.rename` would silently replace, and replacing is exactly what
      D3 3.3 refuses on create.
    - Windows: `os.rename`, which ALREADY raises when the destination
      exists, and which works on every filesystem. `os.link` there needs
      NTFS and the privilege to make hard links, so a user on a FAT or
      exFAT profile would get "the preview-token secret could not be
      created" on a machine where nothing is wrong (fix round 4).

    The caller unlinks the temp either way; on Windows the rename has
    already consumed it, which its own unlink tolerates.

    `windows` is an argument rather than a read of `os.name` inside the
    branch so a test can drive the other platform's spelling without
    patching `os.name`, which makes `pathlib.Path()` try to build a
    WindowsPath and takes pytest itself down with it on 3.11.
    """
    if windows is None:
        windows = os.name == "nt"
    if windows:
        os.rename(tmp_name, str(path))
    else:
        os.link(tmp_name, str(path))


def _write_new_secret(path: Path, exclusive: bool) -> str:
    """Create the secret. `exclusive` refuses to replace an existing one.

    BOTH paths publish a COMPLETE file: the bytes are written to a
    private temp file, fsynced, and only then given the final name.
    First creation used to `os.open` the final path with O_CREAT|O_EXCL
    and write afterwards, which is atomic in EXISTENCE but not in
    CONTENT: a second server starting inside that window saw a
    zero-length file, judged it malformed and ROTATED over the winner's
    secret, whose own write then landed on an unlinked inode. Measured
    at 24 simultaneous starts on a fresh state home: three distinct
    secrets in one run.

    The publish step is what differs. First creation uses `os.link`,
    which creates the final name only if it does not exist and raises
    FileExistsError otherwise, so first-writer-wins still holds and the
    loser reads the winner's secret. A ROTATION (an existing file we
    cannot read) uses `os.replace`, because the final path is already
    taken. D3 3.3 rejects replace-on-create for a real reason: last
    writer wins, and the first server's outstanding tokens are orphaned.
    """
    value = secrets.token_hex(32)
    ensure_state_home()
    fd, tmp_name = tempfile.mkstemp(dir=str(STATE_HOME),
                                    prefix=f"{SECRET_FILENAME}.",
                                    suffix=".tmp")
    tmp: Optional[Path] = Path(tmp_name)
    try:
        # If `os.fdopen` raises, the descriptor mkstemp returned is
        # still open and nothing owns it. POSIX lets the cleanup below
        # unlink an open file, so the leak was invisible here; Windows
        # refuses (ERROR_SHARING_VIOLATION), so the temp file survived
        # the failure and the state folder was left with litter. Hand
        # the descriptor to the file object or close it; never neither
        # (fix round 5).
        try:
            handle = os.fdopen(fd, "w", encoding="ascii")
        except BaseException:
            os.close(fd)
            raise
        with handle as f:
            f.write(value + "\n")
            f.flush()
            os.fsync(f.fileno())
        if os.name != "nt":
            os.chmod(tmp_name, 0o600)      # mkstemp already does; be sure
        if exclusive:
            _publish_exclusive(tmp_name, path)
            # the link left the temp behind; the finally clause takes it
        else:
            os.replace(tmp_name, str(path))
            tmp = None                     # the replace consumed it
    except BaseException:
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass
            tmp = None
        raise
    finally:
        if tmp is not None:
            try:
                tmp.unlink()               # the link left the temp behind
            except OSError:
                pass
    return value


def load_secret() -> str:
    """The per-user HMAC secret, read from disk on every use.

    Never cached: several servers on one machine must agree, and a
    rotation by one must be seen by the others. A symlink at the path is
    refused rather than followed (the pre-planted-symlink class the MRU
    reader already guards against), an unreadable or malformed file is
    ROTATED and logged at warning level (outstanding tokens then fail as
    "a different operation", which is the safe direction), and any
    failure to create or read raises rather than falling back to a
    token-less execute.

    Raises:
        PreviewSecretUnavailable: with the fixed caller-facing message.
    """
    path = _secret_path()
    try:
        if not os.path.lexists(path):
            try:
                return _write_new_secret(path, exclusive=True)
            except FileExistsError:
                pass                      # another server won the race
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            raise PreviewSecretUnavailable(SECRET_UNAVAILABLE_MESSAGE)
        # The mode was set at creation and never looked at again, so a
        # secret that had been widened since (by a restore, a copy, a
        # sync tool, or another local account) was used as though it
        # were still private. A secret any local user can read is a
        # secret any local user can mint tokens with, so it is treated
        # like any other unusable one and ROTATED, which writes a fresh
        # 0600 file; tightening the old one in place would leave a value
        # that may already have been read (fix round 4).
        widened = os.name != "nt" and bool(stat.S_IMODE(st.st_mode) & 0o077)
        if widened:
            logger.warning(
                "The preview-token secret was readable by other users on "
                "this machine and has been rotated; outstanding preview "
                "tokens will be refused.")
            return _write_new_secret(path, exclusive=False)
        with open(path, "r", encoding="ascii", errors="replace") as f:
            raw = f.read(SECRET_READ_MAX_BYTES + 1)
        value = _valid_secret(raw)
        if value is None:
            logger.warning(
                "The preview-token secret was not usable and has been "
                "rotated; outstanding preview tokens will be refused.")
            return _write_new_secret(path, exclusive=False)
        return value
    except PreviewSecretUnavailable:
        raise
    except OSError as e:
        logger.error("Preview secret unavailable: %s", type(e).__name__)
        raise PreviewSecretUnavailable(SECRET_UNAVAILABLE_MESSAGE) from e


# ---------------------------------------------------------------------------
# The registration table (H1): one row per gated tool, no branches elsewhere
# ---------------------------------------------------------------------------
# `args` maps the tool's call arguments to the canonical `A` that decides
# the EFFECT of the operation. Arguments that do not change what the
# operation does to the data are deliberately absent: `preview_token` and
# `confirm` never appear, and `cascade` is not bound because the preview
# always reports the whole branch, so the impact shown does not depend on
# it (D3 3.2); `cascade` still gates the execute on its own.

def _args_merge_codes(kwargs):
    return {"from_code_id": int(kwargs["from_code_id"]),
            "into_code_id": int(kwargs["into_code_id"])}


def _args_delete_code(kwargs):
    return {"code_id": int(kwargs["code_id"])}


def _args_delete_category(kwargs):
    return {"category_id": int(kwargs["category_id"])}


def _args_merge_category(kwargs):
    target = kwargs.get("into_category_id")
    return {"from_category_id": int(kwargs["from_category_id"]),
            # The RESOLVED id, never the name: if the name is given to a
            # different category between preview and execute, the binding
            # differs and the model is told to preview again.
            "into_category_id": None if target is None else int(target)}


def _args_restore_backup(kwargs):
    return {"backup": os.path.normcase(str(kwargs["backup"]))}


def _args_prune_backups(kwargs):
    keep = kwargs.get("keep_last")
    older = kwargs.get("older_than_days")
    return {"keep_last": None if keep is None else int(keep),
            "older_than_days": None if older is None else float(older)}


def _args_pseudonymise_source(kwargs):
    """The arguments that decide what a pseudonymisation run does.

    `mapping` arrives already in its canonical form (entries sorted by
    original, variants sorted, every string NFC-normalised), the same way
    `merge_category` passes the RESOLVED category id rather than the name
    it was given: the canonicalisation belongs to the module that knows
    what a mapping is, and what reaches this table is the settled value.
    Canonicalising it there is also what keeps `use_project_pseudonyms`
    OUT of the binding, so the identical mapping binds the same whether
    it was typed out or read from the project's own `pseudonyms.json`.

    The preview-only arguments are absent, as `cascade` is: `include_context`,
    `context_chars`, `scan_residue`, `residue_detail` and
    `max_spans_per_entry` change what the preview SHOWS, and
    `record_in_journal` changes only whether the run records itself, so
    none of them changes what happens to the text or to a single row.

    `rewrite_memos` changes what happens to rows (the public part of
    every note in the project), so it is bound (v0.13, Brief 2).
    `save_mapping_to_project` writes a file of real names into the
    project folder, a side effect the human must have seen in the
    preview, so it is bound. `researcher_keeps_mapping` is an attestation
    with no side effect, in the class of `record_in_journal`, and is not.

    `file_id` is one id since v0.13 (one file per call), bound as the
    integer the server validated. `TOKEN_VERSION` is not bumped for it:
    a token lives sixty minutes, and one issued by 0.12 for `file_ids`
    that did reach this binding would fail as `token_other_operation`,
    which is what it is. The `bool()` on each switch is so that a truthy
    value that is not `True` cannot bind differently from `True`.
    """
    return {"file_id": int(kwargs["file_id"]),
            "mapping": kwargs["mapping"],
            "case_mode": str(kwargs["case_mode"]),
            "overlap_policy": str(kwargs["overlap_policy"]),
            "rewrite_memos": bool(kwargs["rewrite_memos"]),
            "save_mapping_to_project": bool(
                kwargs["save_mapping_to_project"])}


REGISTRY: Dict[str, Any] = {
    "merge_codes": _args_merge_codes,
    "delete_code": _args_delete_code,
    "delete_category": _args_delete_category,
    "merge_category": _args_merge_category,
    "restore_backup": _args_restore_backup,
    "prune_backups": _args_prune_backups,
    "pseudonymise_source": _args_pseudonymise_source,
}

# The tools whose public `bind` is keyed with the secret. D3 3.2 declared
# `bind` public on the premise that every bound argument was already in
# the conversation, which is true of a code id and false of a mapping
# read from the project's own `pseudonyms.json`: with the pseudonym and
# the other three arguments visible in the preview, an unkeyed sha256
# over the canonical mapping let a dictionary of first names confirm
# the original in twenty guesses. Keying it costs nothing the verifier
# does not already have (it holds the secret), so `verify` still tells
# `project_changed` from `token_other_operation`, and nobody without the
# secret can confirm a guess (fix round 3, S2).
KEYED_BIND = frozenset({"pseudonymise_source"})


def canonical_args(tool: str, **kwargs) -> Dict[str, Any]:
    """The canonical arguments for a gated tool (the only place they live)."""
    if tool not in REGISTRY:
        raise KeyError(f"{tool} is not a token-gated tool")
    return REGISTRY[tool](kwargs)


def fingerprint_rows(preview: Any, rows: Any) -> str:
    """The state element `S`: what the preview said, and which rows it covers.

    The digest binds BOTH the numbers the user was shown and the identity
    of the rows behind them, so neither a changed count nor a swapped row
    can slip through a token issued for the earlier state. Row tuples
    carry ids, positions and ownership plus `has_memo` and `has_private`
    booleans; note rows (v0.13, `rewrite_memos`) carry their key, the
    private-part flag and the public part's length. Memo text never
    enters the payload, on principle, even though an HMAC would not
    reveal it.
    """
    return hashlib.sha256(
        canonical({"preview": preview, "rows": rows}).encode("utf-8")
    ).hexdigest()


def _binding(tool: str, args: Dict[str, Any], project: str) -> Dict[str, Any]:
    return {"v": TOKEN_VERSION, "tool": tool, "args": args,
            "project": project}


def bind_id(tool: str, args: Dict[str, Any], project: str,
            secret: Optional[str] = None) -> str:
    """The non-authoritative operation id carried in the token.

    Eight hex characters over the binding, so the verifier can tell "this
    token is for another operation" from "the project changed under this
    one" and say the more useful of the two. It proves nothing by itself;
    the MAC does that.

    A plain digest for the tools whose arguments the conversation already
    holds; HMAC under the secret for the tools in `KEYED_BIND`, whose
    arguments it may not. For those the secret is the caller's to pass
    (`issue`, `verify` and the flagship all hold it), never loaded here
    on the caller's behalf: a keyed bind computed by any code running
    as the researcher must say so, so that a verifier's recomputation
    cannot be mistaken for an attacker's (fix round 4, L1).
    """
    payload = canonical(_binding(tool, args, project)).encode("utf-8")
    if tool in KEYED_BIND:
        if secret is None:
            raise TypeError(
                f"bind_id needs the secret for {tool!r}: its bind is "
                f"keyed, and the caller passes the secret it holds")
        return hmac.new(secret.encode("ascii"), payload,
                        hashlib.sha256).hexdigest()[:8]
    return hashlib.sha256(payload).hexdigest()[:8]


def _mac(secret: str, tool: str, args: Dict[str, Any], project: str,
         state: str, issued: int) -> str:
    payload = _binding(tool, args, project)
    payload["state"] = state
    payload["issued"] = issued
    return hmac.new(secret.encode("ascii"),
                    canonical(payload).encode("utf-8"),
                    hashlib.sha256).hexdigest()[:32]


def issue(tool: str, args: Dict[str, Any], project: str, state: str,
          now: Optional[int] = None) -> str:
    """Mint a token for one previewed operation."""
    issued = _now() if now is None else int(now)
    secret = load_secret()
    bind = bind_id(tool, args, project, secret)
    token = (f"{TOKEN_PREFIX}.{issued}.{bind}."
             f"{_mac(secret, tool, args, project, state, issued)}")
    logger.debug("Issued preview token for %s (%s)", tool, bind)
    return token


# Verification outcomes. The caller turns these into the fixed texts of
# D3 3.5; this module decides WHICH failure it was and nothing else.
OK = "ok"
MALFORMED = "token_malformed"
EXPIRED = "token_expired"
OTHER_OPERATION = "token_other_operation"
PROJECT_CHANGED = "project_changed"


def verify(token: Any, tool: str, args: Dict[str, Any], project: str,
           state: str, now: Optional[int] = None) -> str:
    """Check a token against the operation it is being used for.

    Returns one of OK, MALFORMED, EXPIRED, PROJECT_CHANGED or
    OTHER_OPERATION. A token whose MAC does not match is a refusal either
    way; the public `bind` decides which of the two explanations the
    caller gets, since a matching bind means this token WAS issued for
    this operation and the rows have moved since. Expiry is checked
    before the MAC so an old token for the right operation gets the more
    useful message. Every comparison uses `hmac.compare_digest`.
    """
    if not isinstance(token, str):
        return MALFORMED
    token = token.strip()
    # Every field of a token this server issues is ASCII: the prefix, a
    # decimal timestamp and two lower-case hex runs. Gate on that once,
    # here, rather than per field: hmac.compare_digest REFUSES to
    # compare strings with non-ASCII characters and raises TypeError,
    # which would leave verify() by raising instead of returning
    # MALFORMED, and the caller would lose the fixed refusal envelope
    # for a raw Python message (fix round 4).
    if not token.isascii():
        return MALFORMED
    parts = token.split(".")
    if len(parts) != 4 or parts[0] != TOKEN_PREFIX:
        return MALFORMED
    _, issued_text, bind, mac = parts
    # A grammar, not a predicate: str.isdigit() is True for superscripts
    # and circled digits that int() then REJECTS, raising ValueError
    # past every caller, and int(x, 16) accepts spellings that are not
    # what this server writes.
    if not (_ISSUED_RE.fullmatch(issued_text) and _BIND_RE.fullmatch(bind)
            and _MAC_RE.fullmatch(mac)):
        return MALFORMED
    issued = int(issued_text)
    # One spelling only: "0000001700000000" decodes to the same integer
    # and would verify under the same MAC.
    if str(issued) != issued_text:
        return MALFORMED
    current = _now() if now is None else int(now)
    if issued > current + TOKEN_MAX_SKEW_SECONDS:
        return EXPIRED
    if issued < current - TOKEN_MAX_AGE_SECONDS:
        return EXPIRED
    secret = load_secret()
    expected = _mac(secret, tool, args, project, state, issued)
    if not hmac.compare_digest(mac, expected):
        # The MAC covers the state as well as the operation, so a failure
        # means one of the two moved. `bind` is exactly what tells them
        # apart: it covers the tool, the arguments and the project and
        # nothing else, so a token whose bind still matches was issued
        # for THIS operation and the project has changed under it, which
        # is the more useful thing to say. It is public and proves
        # nothing on its own; the MAC has already refused either way.
        if hmac.compare_digest(bind, bind_id(tool, args, project, secret)):
            return PROJECT_CHANGED
        return OTHER_OPERATION
    return OK
