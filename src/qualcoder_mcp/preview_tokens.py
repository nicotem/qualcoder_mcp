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
a tool (the flagship's `pseudonymise_source` next) is one row.

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
import secrets
import stat
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

TOKEN_PREFIX = "qcp1"
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


def _write_new_secret(path: Path, exclusive: bool) -> str:
    """Create the secret. `exclusive` uses O_EXCL on the final path.

    First creation is exclusive on the final path, so two servers
    starting at once cannot both write: the loser gets EEXIST and reads
    the winner's secret. A ROTATION (an existing file we cannot read)
    goes through mkstemp plus an atomic replace instead, because the
    final path is already taken.
    """
    value = secrets.token_hex(32)
    STATE_HOME.mkdir(parents=True, exist_ok=True)
    if exclusive:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        fd = os.open(str(path), flags, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(value + "\n")
        return value
    fd, tmp_name = tempfile.mkstemp(dir=str(STATE_HOME),
                                    prefix=f"{SECRET_FILENAME}.",
                                    suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(value + "\n")
        os.replace(str(tmp), str(path))
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
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
        logger.error("Preview secret unavailable: %s", e)
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


REGISTRY: Dict[str, Any] = {
    "merge_codes": _args_merge_codes,
    "delete_code": _args_delete_code,
    "delete_category": _args_delete_category,
    "merge_category": _args_merge_category,
    "restore_backup": _args_restore_backup,
    "prune_backups": _args_prune_backups,
}


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
    booleans; memo text never enters the payload, on principle, even
    though an HMAC would not reveal it.
    """
    return hashlib.sha256(
        canonical({"preview": preview, "rows": rows}).encode("utf-8")
    ).hexdigest()


def _binding(tool: str, args: Dict[str, Any], project: str) -> Dict[str, Any]:
    return {"v": TOKEN_VERSION, "tool": tool, "args": args,
            "project": project}


def bind_id(tool: str, args: Dict[str, Any], project: str) -> str:
    """The public, non-authoritative operation id carried in the token.

    Eight hex characters over the binding, so the verifier can tell "this
    token is for another operation" from "the project changed under this
    one" and say the more useful of the two. It proves nothing by itself;
    the MAC does that.
    """
    return hashlib.sha256(
        canonical(_binding(tool, args, project)).encode("utf-8")
    ).hexdigest()[:8]


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
    token = (f"{TOKEN_PREFIX}.{issued}.{bind_id(tool, args, project)}."
             f"{_mac(secret, tool, args, project, state, issued)}")
    logger.debug("Issued preview token for %s (%s)", tool,
                 bind_id(tool, args, project))
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
    parts = token.strip().split(".")
    if len(parts) != 4 or parts[0] != TOKEN_PREFIX:
        return MALFORMED
    _, issued_text, bind, mac = parts
    if not issued_text.isdigit() or len(bind) != 8 or len(mac) != 32:
        return MALFORMED
    try:
        int(bind, 16)
        int(mac, 16)
    except ValueError:
        return MALFORMED
    issued = int(issued_text)
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
        if hmac.compare_digest(bind, bind_id(tool, args, project)):
            return PROJECT_CHANGED
        return OTHER_OPERATION
    return OK
