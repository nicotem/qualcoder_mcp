"""Per-project settings this server keeps beside data.qda (v0.12, D7).

One setting lives here so far: the AI coder name every row this server
writes into a project is attributed to. It is stored in a small JSON
sidecar, `qualcoder_mcp.json`, in the project folder next to `data.qda`,
because the setting belongs to the PROJECT and must travel with it:
backups, workspace copies, a synced folder and a second machine all carry
it, and two hosts talking to one project agree on it without a shared
machine-level store.

Why the project folder is safe for a file of our own (verified at the
pinned clone, master 9bddf17): QualCoder never enumerates the project
root looking for strangers, its own cleanup deletes only `*_BKUP_*`
folders and its lock file, its backup copies the whole tree with an
ignore set that does not match this name (`app.py:1619-1631`), and a
project merge merges INTO the open project (`merge_projects.py:200`), so
the destination keeps its sidecar.

Restart resilience: nothing here is cached. Every read goes to disk, so a
host that recycles the server process between turns sees the same answer,
and so does a second host editing the same project.
"""

import json
import os
import stat
import tempfile
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .database import (KNOWN_AI_ASSISTANT_OWNER, validate_coder_name,
                       validate_coder_note)

# The sidecar. Only this exact name is ever treated as one.
SIDECAR_NAME = "qualcoder_mcp.json"
SIDECAR_FORMAT = "qualcoder-mcp-project"
SIDECAR_FORMAT_VERSION = 1
# The payload is a few hundred bytes with a full history; anything past
# this is not ours (the MRU reader's rationale, server.py:144-170).
SIDECAR_READ_MAX_BYTES = 64 * 1024
# History is capped on write, oldest first; the current entry is never
# dropped. Twenty entries are echoed into the conversation (ruling 11).
HISTORY_CAP = 200
HISTORY_ECHO = 20

# The host declaration (v0.11's machine-wide setting, re-purposed by D7)
# and this server's built-in default.
AI_CODER_NAME_ENV = "QUALCODER_MCP_AI_CODER_NAME"
DEFAULT_AI_CODER_NAME = "AI Coding Assistant"
# QualCoder 4.0's built-in assistant's owner string, re-exported from
# database.py (one definition, H3). Rows under it that this project has
# not adopted are another coder's work; `known_ai_assistant` is a
# heuristic label for them, never a fact about who typed.
__all_known_ai = KNOWN_AI_ASSISTANT_OWNER
# The pre-0.11 import label. It is an owner on `source` and `case_text`
# rows, never a coding owner, so it is not an AI coder name and never
# enters the set below (Appendix A, R2).
LEGACY_IMPORT_OWNER = "MCP Import"

# The states read_sidecar can report.
SIDECAR_UNSET = "unset"
SIDECAR_SET = "project"
SIDECAR_UNREADABLE = "unreadable"
SIDECAR_NEWER_FORMAT = "newer_format"

UNREADABLE_MESSAGE = (
    "The AI coder name file for this project (qualcoder_mcp.json in the "
    "project folder) could not be read. Ask the user to repair or remove "
    "it; the next write will then ask for the name again. Nothing was "
    "written.")

NEWER_FORMAT_MESSAGE = (
    "The AI coder name file for this project (qualcoder_mcp.json in the "
    "project folder) was written by a newer version of qualcoder-mcp and "
    "this one cannot write it safely. Upgrade qualcoder-mcp, or ask the "
    "user to move the file aside; the next write will then ask for the "
    "name again. Nothing was written.")

READ_ONLY_FOLDER_MESSAGE = (
    "The project folder is not writable, so the AI coder name cannot be "
    "stored with the project. Make the folder writable, or copy the "
    "project to the workspace (copy_project_to_workspace) and work on the "
    "copy.")

UNSET_HINT = (
    "The first write will ask which name to store AI rows under; you can "
    "set it now with set_project_ai_coder_name.")


class SidecarWriteError(Exception):
    """The sidecar could not be written; nothing on disk was changed."""


def _now_iso() -> str:
    """Timezone-aware local time to the second.

    The same aware clock QualCoder writes its own dates with
    (`__main__.py:1865` at 9bddf17). The MRU file's naive local time is
    fine for a single-machine hint; this file travels between machines,
    so the offset is part of the value.
    """
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _parse_iso(value: Any) -> Optional[str]:
    """Return `value` when it parses as ISO 8601, else None.

    Timestamps are echoed into the conversation, so a hand-edited file
    must not be able to put arbitrary text there. A value that does not
    parse is dropped (null), which never invalidates the file: the name
    is what matters and the timestamp is informational.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return text


def _entry(name: str, set_at: Optional[str], note: str,
           host_declaration: Optional[str]) -> Dict[str, Any]:
    """One history entry / the current setting, in field order."""
    return {"name": name, "set_at": set_at, "note": note,
            "host_declaration": host_declaration}


def _validated_entry(raw: Any) -> Optional[Dict[str, Any]]:
    """Validate one sidecar entry, or None when it is not usable.

    Every field is validated on READ as well as on write, because the
    file can be hand-edited or arrive from another machine: a name goes
    through `validate_coder_name` so a tampered sidecar can never smuggle
    a control character, a bidi override or a '#####' marker into an
    `owner` column, and the note goes through `validate_coder_note` for
    the same reason (it is echoed into the conversation). Unknown keys
    inside an entry are dropped.
    """
    if not isinstance(raw, dict):
        return None
    try:
        name = validate_coder_name(raw.get("name"), "ai_coder_name.name")
    except ValueError:
        return None
    note_raw = raw.get("note")
    if note_raw is None:
        note_raw = ""
    try:
        note = validate_coder_note(note_raw, "ai_coder_name.note")
    except ValueError:
        return None
    host = raw.get("host_declaration")
    if host is not None:
        try:
            host = validate_coder_name(host, "ai_coder_name.host_declaration")
        except ValueError:
            return None
    return _entry(name, _parse_iso(raw.get("set_at")), note, host)


class SidecarState:
    """What the sidecar says, read fresh from disk.

    `status` is one of "unset" (no file), "project" (a valid current
    name), "unreadable" (present but not ours to read: not UTF-8, not
    JSON, wrong format, oversized, a symlink, or a field that fails
    validation) and "newer_format" (a format_version we do not write).
    A "newer_format" file whose current entry validates still reports its
    name, because reading it is safe; writing it is not.
    """

    __slots__ = ("status", "entry", "history", "path")

    def __init__(self, status: str, entry: Optional[Dict[str, Any]] = None,
                 history: Optional[List[Dict[str, Any]]] = None,
                 path: Optional[Path] = None):
        self.status = status
        self.entry = entry
        self.history = list(history or [])
        self.path = path

    @property
    def name(self) -> Optional[str]:
        return self.entry["name"] if self.entry else None

    @property
    def host_declaration(self) -> Optional[str]:
        return self.entry.get("host_declaration") if self.entry else None

    @property
    def is_set(self) -> bool:
        return self.status == SIDECAR_SET and self.entry is not None

    def __repr__(self) -> str:          # pragma: no cover - debugging aid
        return f"SidecarState({self.status!r}, name={self.name!r})"


def sidecar_path(project_folder: Any) -> Path:
    """The sidecar's path inside a project folder."""
    return Path(project_folder) / SIDECAR_NAME


def _read_raw(path: Path) -> Optional[Dict[str, Any]]:
    """The sidecar's JSON object, or None when it is not readable.

    Refuses a symlink before opening (`lstat` first, the MRU reader's
    discipline: a symlink at this name is either a mistake or a trap, and
    following it would let a write land outside the project folder), caps
    the read in BYTES so a multibyte payload cannot slip under a
    character count, and decodes as `utf-8-sig`: BOM tolerance is
    deliberate here and here only, because this file is meant to be
    hand-editable and Windows editors add one (ruling 12).
    """
    try:
        st = os.lstat(path)
    except (OSError, ValueError):
        return None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        return None
    try:
        with open(path, "rb") as f:
            raw = f.read(SIDECAR_READ_MAX_BYTES + 1)
    except OSError:
        return None
    if len(raw) > SIDECAR_READ_MAX_BYTES:
        return None
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read_sidecar(project_folder: Any) -> SidecarState:
    """Read the project's sidecar. Never raises, never caches, never writes.

    Called on every use: a host that recycled the process, and a second
    host editing the same project, both see what is on disk now.
    """
    path = sidecar_path(project_folder)
    if not os.path.lexists(path):
        return SidecarState(SIDECAR_UNSET, path=path)
    data = _read_raw(path)
    if data is None or data.get("format") != SIDECAR_FORMAT:
        return SidecarState(SIDECAR_UNREADABLE, path=path)
    version = data.get("format_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        return SidecarState(SIDECAR_UNREADABLE, path=path)
    entry = _validated_entry(data.get("ai_coder_name"))
    if data.get("ai_coder_name") is not None and entry is None:
        # A current setting we cannot validate makes the FILE unreadable:
        # we never repair it silently, because it may hold the history
        # the researcher cares about.
        return SidecarState(SIDECAR_UNREADABLE, path=path)
    # A malformed history never costs the current name: it is
    # informational, and the next set rebuilds it (B1.3).
    history: List[Dict[str, Any]] = []
    raw_history = data.get("ai_coder_name_history")
    if isinstance(raw_history, list):
        for item in raw_history:
            validated = _validated_entry(item)
            if validated is None:
                history = []
                break
            history.append(validated)
    if version > SIDECAR_FORMAT_VERSION:
        return SidecarState(SIDECAR_NEWER_FORMAT, entry, history, path)
    if entry is None:
        return SidecarState(SIDECAR_UNSET, None, history, path)
    return SidecarState(SIDECAR_SET, entry, history, path)


def _inherit_mode_bits(tmp_path: Path, project_folder: Path) -> None:
    """Give the sidecar `data.qda`'s permission bits when we can read them.

    mkstemp creates at 0600, which would make a sidecar that a shared
    project's other users cannot read while the database beside it is
    group-readable. Masked to 0o666 so no execute bit is ever set, and a
    no-op on Windows, where chmod only moves the read-only flag
    (ruling 8).
    """
    if os.name == "nt":
        return
    try:
        mode = stat.S_IMODE(os.stat(project_folder / "data.qda").st_mode)
    except OSError:
        return
    try:
        os.chmod(tmp_path, mode & 0o666)
    except OSError:
        pass


def write_ai_coder_name(project_folder: Any, name: str, note: str = "",
                        host_declaration: Optional[str] = None,
                        now: Optional[str] = None) -> Dict[str, Any]:
    """Store `name` as the project's AI coder name; return the new entry.

    The MRU write discipline (`_open_mru_tmp`, server.py:80-120), with
    one addition: a single `fsync` before the replace, because this file
    is the only record of a choice the researcher made and re-creating it
    means asking them again. `tempfile.mkstemp` opens O_CREAT|O_EXCL at
    an unpredictable name inside the project folder, so two servers can
    never share a temp name and a symlink pre-planted at a would-be name
    is refused rather than written through; `os.replace` is atomic, so a
    reader sees the old file or the new one, never a partial one; a
    failure unlinks the temp and leaves the existing file byte-identical.

    Temp litter from a crash (`qualcoder_mcp.json.<random>.tmp`) is never
    reopened and never enumerated by this server; it is harmless and a
    user may delete it.

    Raises:
        SidecarWriteError: With a message for the caller to return. The
            file on disk is unchanged.
    """
    folder = Path(project_folder)
    path = sidecar_path(folder)
    if os.path.lexists(path):
        try:
            st = os.lstat(path)
        except OSError as e:
            raise SidecarWriteError(UNREADABLE_MESSAGE) from e
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            raise SidecarWriteError(UNREADABLE_MESSAGE)
    existing = _read_raw(path) if os.path.lexists(path) else {}
    if existing is None:
        raise SidecarWriteError(UNREADABLE_MESSAGE)

    state = read_sidecar(folder)
    if state.status == SIDECAR_NEWER_FORMAT:
        raise SidecarWriteError(NEWER_FORMAT_MESSAGE)
    if state.status == SIDECAR_UNREADABLE:
        raise SidecarWriteError(UNREADABLE_MESSAGE)

    # Validated HERE as well as at the tool layer, because this writes
    # the string every later AI row is attributed to: one gate on the
    # way in, one on the way out (_validated_entry), so no path can put
    # a name in an owner column that the validator would refuse
    # (fix round 4).
    name = validate_coder_name(name, "name")
    note = validate_coder_note(note, "note")
    entry = _entry(name, now or _now_iso(), note, host_declaration)
    history = list(state.history) + [entry]
    if len(history) > HISTORY_CAP:
        # Oldest first, and never the entry we are writing.
        history = history[len(history) - HISTORY_CAP:]

    # Unknown top-level keys are preserved: a later feature, or a
    # researcher's own annotation, survives a name change (the
    # "keeping other keys intact" pattern of view_av.py:1369-1376).
    payload: Dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
    payload["format"] = SIDECAR_FORMAT
    payload["format_version"] = SIDECAR_FORMAT_VERSION
    payload["written_by"] = f"qualcoder-mcp {_package_version()}"
    payload["updated"] = entry["set_at"]
    payload["ai_coder_name"] = entry
    payload["ai_coder_name_history"] = history

    tmp: Optional[Path] = None
    try:
        fd, tmp_name = tempfile.mkstemp(dir=str(folder),
                                        prefix=f"{SIDECAR_NAME}.",
                                        suffix=".tmp")
        tmp = Path(tmp_name)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        _inherit_mode_bits(tmp, folder)
        os.replace(str(tmp), str(path))
    except OSError as e:
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass
        raise SidecarWriteError(
            f"The AI coder name could not be stored with the project "
            f"({type(e).__name__}). Nothing was changed.") from e
    return entry


def _package_version() -> str:
    from . import __version__
    return __version__


def mismatch(env: Optional[str], current: Optional[Dict[str, Any]]) -> bool:
    """True when this host's declaration conflicts with the project (c2).

    Rule c2 (ruling 1): a write is refused if and only if this host
    declares a name AND the declaration is neither the project's current
    name nor the declaration recorded when that name was set. So an
    undeclared host never nags, a host whose declaration was acknowledged
    once never nags again, and a host that declares a different name from
    the one the project uses asks the user which to use.
    """
    if env is None:
        return False
    if current is None:
        return False
    if env == current.get("name"):
        return False
    if env == current.get("host_declaration"):
        return False
    return True


def host_declaration(environ: Optional[Dict[str, str]] = None) -> Optional[str]:
    """This host's declared AI coder name, or None.

    Invalid values are treated as absent here: `main()` validates the
    variable at start-up and refuses to start on a bad one, so a bad
    value reaching this function means the server was started another way
    (a test, an embedded use); a declaration we would refuse to write is
    not one to compare against either.
    """
    env = os.environ if environ is None else environ
    raw = env.get(AI_CODER_NAME_ENV)
    if raw is None:
        return None
    try:
        return validate_coder_name(raw, AI_CODER_NAME_ENV)
    except ValueError:
        return None


def ai_coder_names_for_project(
        project_folder: Any,
        environ: Optional[Dict[str, str]] = None) -> Tuple[str, ...]:
    """The names this server treats as its own AI work in this project.

    One definition, used by every feature that needs to tell this
    server's rows from another coder's (H3): the collateral warnings of a
    cascade preview, the coder roles of a comparison, and the flagship's
    pseudonymisation next. Ordered, no duplicates:

    1. the project's current AI coder name, when set;
    2. every name in the project's history, oldest first;
    3. `DEFAULT_AI_CODER_NAME`, always, because every pre-0.12 row this
       server wrote carries it and a name change never re-attributes
       rows;
    4. this host's declaration, when set and valid, because v0.11 wrote
       under it machine-wide.

    "AI Agent" enters the set only through those rules, that is, when the
    researcher chose it for this project or this host declares it; it is
    never included on its own. "MCP Import" is an import label, not a
    coding owner, and is never included.

    Never refuses and never asks: reads do not ask, so an unset or
    unreadable sidecar yields the built-in default (plus the declaration
    when there is one).
    """
    names: List[str] = []

    def add(value: Optional[str]) -> None:
        if value and value not in names:
            names.append(value)

    state = read_sidecar(project_folder)
    add(state.name)
    for item in state.history:
        add(item.get("name"))
    add(DEFAULT_AI_CODER_NAME)
    add(host_declaration(environ))
    return tuple(names)


def known_ai_set(project_folder: Any,
                 environ: Optional[Dict[str, str]] = None) -> Tuple[str, ...]:
    """The names the ask may propose and probe for, ordered.

    Narrower than `ai_coder_names_for_project`: the migration probe of
    B1.12 asks whether a project already holds AI rows, and the answer
    may only ever name the built-in default, QualCoder 4.0's assistant
    string and this host's declaration. It never selects distinct owners,
    so it can never enumerate a human or a hidden coder.
    """
    names: List[str] = [DEFAULT_AI_CODER_NAME, KNOWN_AI_ASSISTANT_OWNER]
    declared = host_declaration(environ)
    if declared and declared not in names:
        names.append(declared)
    return tuple(names)


def folder_is_writable(project_folder: Any) -> bool:
    """Whether a sidecar can be created in this folder (ruling 2).

    `os.access` answers with the real uid, which is what mkstemp will
    use. On Windows it reports the read-only attribute only, which is the
    same answer NTFS permissions would give for the common cases; a
    genuine ACL refusal surfaces as the write error instead.
    """
    return os.access(str(project_folder), os.W_OK | os.X_OK)


def echoed_history(state: SidecarState) -> List[Dict[str, Any]]:
    """The last HISTORY_ECHO entries, newest last (ruling 11)."""
    return state.history[-HISTORY_ECHO:]


def normalise_for_case_compare(value: str) -> str:
    """Casefold plus NFC, for the setter's case-only WARNING alone.

    Coder names are compared EXACTLY everywhere a decision depends on
    them (X2): `coder_names.name` is TEXT UNIQUE under SQLite's BINARY
    collation (app.py:1470-1475) and every upstream owner match is exact
    (ai_mcp_server.py:1603-1604, :3235-3240). This helper exists only so
    the setter can TELL the user that two names differ by letter case;
    it never decides anything.
    """
    return unicodedata.normalize("NFC", value).casefold()
