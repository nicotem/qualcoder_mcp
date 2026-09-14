"""The pseudonymisation engine (v0.12 flagship, D1).

Pure functions over strings and integers. No database, no server, no new
dependency: the edit list is computed here and is exact, so every stored
offset is remapped arithmetically rather than by diffing two texts. The
parity oracles in the test suite drive this module directly, which is
why it imports nothing of ours.

What it does, in one paragraph. A mapping of names to pseudonyms is
validated, compiled into ONE whole-word, escaped, longest-first
alternation, and run over a text in a single non-chaining pass. Each
match becomes an edit. Every `code_text`, `annotation` and `case_text`
span of that text is then remapped under one of two policies:
`snap_to_pseudonym`, which treats the pseudonym as the same token as the
name it replaces and never empties or deletes a span, and
`qualcoder_edit_parity`, which reproduces the semantics QualCoder's own
coding-view editor applies when a whole token is deleted and retyped.

Upstream reference at pin 9bddf17 (QualCoder master), read-only:

- the boundary regex and the escaping: `manage_files.py:3344-3349`,
  `re.sub(rf"(?<!\\w){re.escape(original)}(?!\\w)", pseudonym, text_)`,
  applied entry by entry over the whole text at import;
- the minimum lengths: `pseudonyms.py:72-74` (original, 2) and
  `pseudonyms.py:76-78` (pseudonym, 3);
- the span walk `qualcoder_edit_parity` reproduces: `apply_insert` at
  `code_text.py:5893-5904`, `apply_delete` at `:5906-5927`, the running
  offset at `:5933-5950`, the end clamp at `:6208-6209` (case links) and
  `:6232-6233` (codings), and the absence of a clamp for annotations at
  `:6213-6222`.

Two deliberate deviations from upstream, both documented in the tool
description and pinned by tests:

1. ONE pass, not a chain. Upstream applies each entry as its own
   `re.sub` over the whole text, so a later entry rewrites what an
   earlier one produced (`[Sam -> Alex, Alex -> Pat]` turns every "Sam"
   into "Pat"). This engine refuses a mapping whose pseudonym is also a
   name in the mapping and matches everything in one leftmost-longest
   pass, so no replacement is ever replaced again.
2. `qualcoder_edit_parity` reproduces the SEMANTICS of master's walk for
   a whole-token replacement (delete the name, insert the pseudonym at
   the same offset), not the output of diff-match-patch. Upstream feeds
   its walk whatever `diff_main` produced, and `diff_main` may factor a
   common prefix or suffix out of a replacement, which would move the
   edit boundaries. That factoring is a property of the diff library and
   is not reproduced here; the claim this module makes is the narrower,
   true one.
"""

import re
import unicodedata
from bisect import bisect_right
from typing import Any, Dict, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------
# Limits and vocabularies
# --------------------------------------------------------------------------

# Upstream's own minimum for an original (pseudonyms.py:72-74). Two code
# points, so a one-letter initial cannot be turned into a rule that
# rewrites half the transcript.
MIN_ORIGINAL_CHARS = 2
# Upstream's own minimum for a pseudonym (pseudonyms.py:76-78).
MIN_PSEUDONYM_CHARS = 3
# Ours. A pseudonym is written into the researcher's text once per match,
# so its length is what bounds how far this tool can grow a file.
MAX_PSEUDONYM_CHARS = 200
# Ours. A surface form is a NAME, and 200 characters is already absurdly
# generous for one. The cap is not about the text it matches, it is about
# `max_form_len`: the overlap diagnostic scans a window of that width
# around every match, so an uncapped form makes a READ-ONLY preview, with
# no token and no approval, cost O(matches x form length). Measured
# before the cap: a 200,000-character original over a 99 KB file took 91
# seconds, and `validate_mapping` accepted a 1,000,000-character one
# (Security S4).
MAX_FORM_CHARS = 200
# Ours. The same amplification from the other side: entries were capped
# and variants were not, so one entry could carry 200,000 of them.
MAX_VARIANTS_PER_ENTRY = 50
# Ours. What the alternation, `forms_at`'s buckets and the token payload
# are actually sized by. 500 entries of 4 forms each is far past any real
# mapping.
MAX_TOTAL_FORMS = 2000
# Ours. Five hundred entries is far past any real mapping and keeps the
# alternation, the diagnostics and the token payload bounded.
MAX_ENTRIES = 500
# Ours. `include_context` returns file text; this caps one window.
MAX_CONTEXT_CHARS = 120
# Ours, and this is the cap that actually stops a preview turning into a
# transcript dump. Per-window was never enough: the number of windows was
# uncapped, so 120 characters each side of 400 shown spans returned
# 98,100 characters of a 20,800-character transcript, 4.7 times the file,
# because the windows overlap (Security S8). The budget is per preview
# call, across every file and every entry, and a block that runs out of
# it says `context_truncated`.
MAX_CONTEXT_TOTAL_CHARS = 10000
# Ours. A paging cap in the house shape (`validate_limit`): silently
# capped rather than refused, because a caller asking for more than this
# many span positions per entry is asking for "all of them".
MAX_SPANS_PER_ENTRY = 500
# Ours, and deliberately NOT driven by a tool argument: `overlap_conflicts`
# is part of the signed effect block, so its size must be the same on the
# preview call and on the execute call.
MAX_OVERLAP_CONFLICTS = 200

CASE_MODES = ("exact", "insensitive", "insensitive_preserve")
OVERLAP_POLICIES = ("snap_to_pseudonym", "qualcoder_edit_parity")

# How a row's span changed. One vocabulary for both policies, because the
# preview shape and the hidden-coder rule key on it.
UNCHANGED = "unchanged"
SHIFTED = "shifted"
RESIZED = "resized"
SNAPPED = "snapped"
DELETED = "deleted"

# Characters refused in any mapping string.
#
# D1 3.2 names line breaks, U+2029 and control characters. This pattern is
# a superset, and the additions are deliberate:
#
# - U+2028 as well as U+2029, because both are line separators Qt's
#   document model treats as breaks;
# - the explicit bidirectional formatting controls (U+200E, U+200F,
#   U+202A to U+202E, U+2066 to U+2069), because a pseudonym is written
#   INTO the researcher's text and one of these would visually reorder
#   every line after it while leaving the stored offsets untouched;
# - U+FEFF, because QualCoder strips a leading one on import
#   (manage_files.py:3340-3341), so a pseudonym carrying one would behave
#   differently depending on where it landed.
#
# The zero-width joiner and non-joiner (U+200C, U+200D) are NOT refused:
# they are needed to spell ordinary words in several scripts.
# The ranges are built with chr() rather than written as escapes so that
# this source file itself contains no control characters.
_FORBIDDEN_RANGES = (
    (0x0000, 0x001F),      # C0 controls, which includes every line break
    (0x007F, 0x009F),      # DEL and the C1 controls
    (0x2028, 0x2029),      # LINE SEPARATOR and PARAGRAPH SEPARATOR
    (0x200E, 0x200F),      # LEFT-TO-RIGHT and RIGHT-TO-LEFT MARK
    (0x202A, 0x202E),      # the bidirectional embeddings and overrides
    (0x2066, 0x2069),      # the bidirectional isolates
    (0xFEFF, 0xFEFF),      # ZERO WIDTH NO-BREAK SPACE, the byte-order mark
)
_FORBIDDEN_CHARS_RE = re.compile(
    "[" + "".join(chr(lo) + "-" + chr(hi)
                   for lo, hi in _FORBIDDEN_RANGES) + "]")

# QualCoder 4.0's private-memo marker. A mapping string carrying one
# could plant a private zone in any memo a later release rewrites, and
# there is no reading of a name that needs five hashes in it.
_MARKER_RUN_RE = re.compile(r"#{5,}")

# Beyond the Basic Multilingual Plane. QualCoder's editor counts UTF-16
# code units, so a single astral code point makes Qt's offsets and the
# code-point offsets every report uses diverge from that point on
# (database.py `position_safe`). A pseudonym is ours to choose, so this is
# refused rather than warned about.
_ASTRAL_MINIMUM = 0x10000


def _has_astral(value: str) -> bool:
    """Whether `value` carries a code point outside the BMP."""
    return any(ord(char) >= _ASTRAL_MINIMUM for char in value)


class MappingError(ValueError):
    """A mapping this engine refuses, with the caller-facing text.

    A ValueError subclass so the server's tool guard turns it into the
    ordinary sanitised error envelope without a branch of its own.
    """


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def _describe_bad_characters(value: str) -> Optional[str]:
    """Which character rule `value` breaks, or None."""
    if _FORBIDDEN_CHARS_RE.search(value):
        return "control"
    if _MARKER_RUN_RE.search(value):
        return "marker"
    return None


def _check_common(value: Any, entry_index: int, label: str) -> str:
    """The rules every mapping string shares. Returns the string."""
    if not isinstance(value, str):
        raise MappingError(
            f"mapping entry {entry_index}: {label} must be a string.")
    if value != value.strip():
        raise MappingError(
            f"mapping entry {entry_index}: {label} must not begin or end "
            f"with whitespace.")
    if _describe_bad_characters(value) is not None:
        raise MappingError(
            f"mapping entry {entry_index}: original/pseudonym/variant must "
            f"not contain line breaks, control characters, bidirectional "
            f"formatting characters or a run of five or more '#' "
            f"characters.")
    return value


def _check_name(value: Any, entry_index: int, label: str) -> str:
    """An `original` or a `variant`: a surface form to look for."""
    value = _check_common(value, entry_index, label)
    if len(value) < MIN_ORIGINAL_CHARS:
        raise MappingError(
            f"mapping entry {entry_index}: {label} must be at least "
            f"{MIN_ORIGINAL_CHARS} characters.")
    if len(value) > MAX_FORM_CHARS:
        raise MappingError(
            f"mapping entry {entry_index}: {label} must be at most "
            f"{MAX_FORM_CHARS} characters.")
    return value


def _check_pseudonym(value: Any, entry_index: int,
                     may_echo_names: bool = True) -> str:
    """A `pseudonym`: a string this engine writes into the file."""
    value = _check_common(value, entry_index, "pseudonym")
    if len(value) < MIN_PSEUDONYM_CHARS:
        raise MappingError(
            f"mapping entry {entry_index}: pseudonym must be at least "
            f"{MIN_PSEUDONYM_CHARS} characters (QualCoder's own minimum).")
    if len(value) > MAX_PSEUDONYM_CHARS:
        raise MappingError(
            f"mapping entry {entry_index}: pseudonym must be at most "
            f"{MAX_PSEUDONYM_CHARS} characters.")
    if _has_astral(value) or "\r" in value:
        shown = f"pseudonym '{value}'" if may_echo_names else "the pseudonym"
        raise MappingError(
            f"mapping entry {entry_index}: {shown} contains "
            f"characters beyond U+FFFF (emoji or similar) or a carriage "
            f"return; QualCoder's editor would misplace positions in the "
            f"rewritten file. Choose a pseudonym in the Basic Multilingual "
            f"Plane.")
    return value


class Entry:
    """One validated mapping entry."""

    __slots__ = ("index", "original", "pseudonym", "variants")

    def __init__(self, index: int, original: str, pseudonym: str,
                 variants: Tuple[str, ...]):
        self.index = index
        self.original = original
        self.pseudonym = pseudonym
        self.variants = variants

    @property
    def forms(self) -> Tuple[str, ...]:
        """Every surface form this entry replaces, original first."""
        return (self.original,) + self.variants

    def __repr__(self) -> str:                # pragma: no cover - debugging
        return (f"Entry({self.index}, pseudonym={self.pseudonym!r}, "
                f"forms={len(self.forms)})")


class Mapping:
    """A validated mapping, with the case mode it was validated under."""

    __slots__ = ("entries", "case_mode", "shared_pseudonyms")

    def __init__(self, entries: Tuple[Entry, ...], case_mode: str,
                 shared_pseudonyms: Tuple[Dict[str, Any], ...]):
        self.entries = entries
        self.case_mode = case_mode
        # Two entries pointing at one pseudonym is a legitimate choice
        # (two people merged into one identity), so it is reported rather
        # than refused (D1 3.2).
        self.shared_pseudonyms = shared_pseudonyms

    def __len__(self) -> int:
        return len(self.entries)


def _fold(value: str) -> str:
    return value.casefold()


def validate_mapping(raw: Any, case_mode: str = "exact",
                     may_echo_names: bool = True) -> Mapping:
    """Validate a caller's mapping, or raise MappingError.

    Refused as a whole, with nothing previewed, on any of: an empty
    mapping, more than `MAX_ENTRIES` entries, a malformed entry, a string
    that breaks the character rules, a duplicate surface form, or a
    pseudonym that is also a name in the same mapping.

    That last rule is where this engine parts company with upstream.
    QualCoder applies entries sequentially (`manage_files.py:3344-3349`),
    so `[Sam -> Alex, Alex -> Pat]` silently turns every "Sam" into "Pat";
    the dialog checks only that the list has no duplicate originals and no
    duplicate pseudonyms (`pseudonyms.py:84-89`). Refusing is the honest
    answer, because there is no reading of that mapping the researcher
    meant.

    `case_mode` matters here: in the two insensitive modes two forms that
    differ only in case ARE the same rule, so they are duplicates, and a
    pseudonym that case-folds onto a name would chain.

    `may_echo_names=False` says the mapping did NOT come from the caller:
    it was read out of the project's own `pseudonyms.json`, which is the
    researcher's reverse key. D1 5.1's premise ("the mapping is
    user-supplied and therefore already in the conversation") is false on
    that path and D1 3.10 promises the opposite, so no refusal text on it
    quotes a value. Entry indices carry everything the reader needs, and
    the chaining refusal is where it mattered most: the value it quotes
    is a pseudonym that is ALSO somebody's real name, which is the whole
    reason the entry is refused (Security S3).
    """
    if case_mode not in CASE_MODES:
        raise MappingError(
            f"case_mode must be one of {', '.join(CASE_MODES)}.")
    if raw is None or (isinstance(raw, (list, tuple)) and not raw):
        raise MappingError(
            "mapping is empty: give at least one entry with original and "
            "pseudonym.")
    if not isinstance(raw, (list, tuple)):
        raise MappingError(
            "mapping must be a list of entries, each an object with "
            "original and pseudonym.")
    if len(raw) > MAX_ENTRIES:
        raise MappingError(
            f"mapping has {len(raw)} entries; at most {MAX_ENTRIES} are "
            f"accepted.")

    insensitive = case_mode != "exact"
    entries: List[Entry] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise MappingError(
                f"mapping entry {index}: each entry must be an object with "
                f"original and pseudonym.")
        unknown = set(item) - {"original", "pseudonym", "variants"}
        if unknown:
            raise MappingError(
                f"mapping entry {index}: unknown key(s) "
                f"{', '.join(sorted(unknown))}; an entry has original, "
                f"pseudonym and optionally variants.")
        if "original" not in item or "pseudonym" not in item:
            raise MappingError(
                f"mapping entry {index}: each entry must have both original "
                f"and pseudonym.")
        original = _check_name(item["original"], index, "original")
        pseudonym = _check_pseudonym(item["pseudonym"], index,
                                     may_echo_names)
        raw_variants = item.get("variants")
        variants: List[str] = []
        if raw_variants is not None:
            if not isinstance(raw_variants, (list, tuple)):
                raise MappingError(
                    f"mapping entry {index}: variants must be a list of "
                    f"strings.")
            if len(raw_variants) > MAX_VARIANTS_PER_ENTRY:
                raise MappingError(
                    f"mapping entry {index}: has {len(raw_variants)} "
                    f"variants; at most {MAX_VARIANTS_PER_ENTRY} are "
                    f"accepted per entry.")
            for variant in raw_variants:
                variants.append(_check_name(variant, index, "variant"))
        entries.append(Entry(index, original, pseudonym, tuple(variants)))

    total_forms = sum(len(entry.forms) for entry in entries)
    if total_forms > MAX_TOTAL_FORMS:
        raise MappingError(
            f"mapping has {total_forms} surface forms (originals plus "
            f"variants); at most {MAX_TOTAL_FORMS} are accepted.")

    # Duplicate surface forms, across the whole mapping and within one
    # entry. Exact spelling always; case-folded as well when the run is
    # case-insensitive, because there the two are one rule.
    seen: Dict[str, int] = {}
    for entry in entries:
        for form in entry.forms:
            for key in ({form, _fold(form)} if insensitive else {form}):
                previous = seen.get(key)
                if previous is not None and previous != entry.index:
                    raise MappingError(
                        f"mapping entries {previous} and {entry.index}: the "
                        f"same original (or variant) appears twice.")
                if previous is not None:
                    raise MappingError(
                        f"mapping entry {entry.index}: the same original (or "
                        f"variant) appears twice.")
                seen[key] = entry.index

    # A pseudonym that is also a name in this mapping would chain upstream
    # and is refused here.
    for entry in entries:
        keys = {entry.pseudonym, _fold(entry.pseudonym)} if insensitive \
            else {entry.pseudonym}
        if any(key in seen for key in keys):
            shown = (f"pseudonym '{entry.pseudonym}'" if may_echo_names
                     else f"the pseudonym of entry {entry.index}")
            raise MappingError(
                f"mapping entry {entry.index}: {shown} "
                f"is also an original or variant in this "
                f"mapping. QualCoder's import would chain the two "
                f"replacements; this tool refuses instead. Choose a "
                f"pseudonym that does not occur as a name in the mapping.")

    by_pseudonym: Dict[str, List[int]] = {}
    for entry in entries:
        by_pseudonym.setdefault(entry.pseudonym, []).append(entry.index)
    shared = tuple(
        {"pseudonym": pseudonym, "entries": indices}
        for pseudonym, indices in sorted(by_pseudonym.items())
        if len(indices) > 1)
    return Mapping(tuple(entries), case_mode, shared)


def canonical_mapping(mapping: Mapping) -> List[Dict[str, Any]]:
    """The mapping as the authorisation token binds it (D1 3.8).

    Entries sorted by original, variants sorted, every string normalised
    to NFC, so the same mapping described in a different order binds
    identically and a mapping read from `pseudonyms.json` binds the same
    as the identical mapping typed out by the caller (which is what keeps
    the source flag itself out of the binding).

    Note what NFC does NOT do: the ENGINE matches the strings exactly as
    supplied, never normalised, because normalising what we look for
    could silently stop matching a file stored in another normal form.
    So a mapping re-sent in a different normal form binds the same and
    has a different effect, which the signed state catches as
    "the project changed"; a test pins that.
    """
    def nfc(value: str) -> str:
        return unicodedata.normalize("NFC", value)

    return sorted(
        ({"original": nfc(entry.original),
          "pseudonym": nfc(entry.pseudonym),
          "variants": sorted(nfc(v) for v in entry.variants)}
         for entry in mapping.entries),
        key=lambda item: (item["original"], item["pseudonym"],
                          item["variants"]))


# --------------------------------------------------------------------------
# Compiling and matching
# --------------------------------------------------------------------------

def _form_sort_key(item: Tuple[str, int]) -> Tuple[int, int, str]:
    """Longest first, then entry order, then alphabetical (D1 3.4).

    Length first is what makes "Mary Ann" win over "Mary" and
    "Jean-Paul" over "Jean": Python's alternation takes the first branch
    that lets the whole pattern succeed, so the longest viable form at a
    position is chosen. The two remaining keys only make ties stable, so
    the same mapping always compiles to the same pattern.
    """
    form, index = item
    return (-len(form), index, form)


# Runs of anything that is not a letter or a digit, underscore included
# (`\W` alone excludes it, and `_` is the separator this whole finding is
# about). Used to join the parts of a multi-word name, so the detector
# reads "Mary Ann", "Mary_Ann", "Mary-Ann" and "MaryAnn" as the same
# name, which is what a person reading a file list does.
_SEPARATOR_RUN = r"[\W_]*"
_SEPARATOR_SPLIT = re.compile(r"[\W_]+")


def _detector_alternative(form: str) -> str:
    """One surface form as a pattern that survives a changed separator.

    A form of one word is itself, escaped. A form of several is its
    words escaped and joined by "any run of separators, or none", so a
    transcript called `Mary_Ann.txt` and a case called `MaryAnn` are
    both found. A form with no letters or digits at all (which
    validation permits: two punctuation marks pass the length rule) has
    no words to join and is used literally.
    """
    parts = [part for part in _SEPARATOR_SPLIT.split(form) if part]
    if not parts:
        return re.escape(form)
    return _SEPARATOR_RUN.join(re.escape(part) for part in parts)


class NameDetector:
    """Would a human reading this string see one of the mapping's names?

    The DETECTOR, and it is deliberately NOT the rewriter's matcher. The
    two answer different questions under opposite correctness conditions
    and must never be swapped for one another:

    - `Compiled.pattern` is the REWRITER's matcher: master's file-import
      form, whole-word on both sides, so "Ann" is never replaced inside
      "Anna" and an unrelated word is never corrupted. Conservative is
      right there, and nothing about it changes.
    - This class decides whether a string is REPORTED as still carrying
      a name, or WITHHELD from a durable record because it carries one.
      Both are questions about what a reader sees, not about what the
      rewrite would fire on, so it is liberal by design: a bare
      substring with no word boundary at all, because `_` is a word
      character and `Thomas_interview.txt` is the commonest transcript
      file name there is; case-insensitive under EVERY `case_mode`,
      because `THOMAS_P01` names the participant whether or not this run
      would rewrite it; over originals and variants alike.

    The asymmetry is the whole point. Over-detecting costs a withheld
    file name, which the file id replaces, or a residue count that is
    one too high, which sends the researcher to look at a label that
    does mention the name. Under-detecting costs a participant's real
    name in a journal entry that ships inside the project.

    The cost is real and is stated rather than hidden: an entry for
    "Tom" makes a memo that says "tomorrow" count as carrying a name,
    because a rule that catches `ThomasB.txt` cannot also spare
    `tomorrow`, and of the two mistakes only one of them ships a
    participant's name. The tool description says the residue counts
    read wider than the rewrite.
    """

    __slots__ = ("_direct", "_folded")

    def __init__(self, forms: Sequence[str]):
        normalised = [unicodedata.normalize("NFC", form) for form in forms]
        self._direct = re.compile(
            "|".join(_detector_alternative(form) for form in normalised),
            re.IGNORECASE)
        # A second reading, because `re.IGNORECASE` and `str.casefold` do
        # not agree on every code point (U+00DF casefolds to "ss" but
        # does not match "SS" under IGNORECASE). Two searches over one
        # short string cost nothing, and here the wider answer is the
        # safe one.
        self._folded = re.compile(
            "|".join(_detector_alternative(_fold(form))
                     for form in normalised))

    def contains(self, value: Any) -> bool:
        """True when a reader of `value` would see any surface form.

        Anything that is not a non-empty string is False: a NULL label
        carries no name, and nothing downstream should have to know that
        a missing value and a clean one are different answers.
        """
        if not isinstance(value, str) or not value:
            return False
        text = unicodedata.normalize("NFC", value)
        return bool(self._direct.search(text)
                    or self._folded.search(_fold(text)))


class Compiled:
    """A mapping compiled into one pattern, plus the lookups it needs."""

    __slots__ = ("mapping", "case_mode", "flags", "pattern", "forms",
                 "_exact", "_folded", "_by_first", "max_form_len",
                 "pseudonym_pattern", "_pseudonym_entries", "detector")

    def __init__(self, mapping: Mapping):
        self.mapping = mapping
        self.case_mode = mapping.case_mode
        self.flags = 0 if mapping.case_mode == "exact" else re.IGNORECASE
        forms: List[Tuple[str, int]] = []
        for entry in mapping.entries:
            for form in entry.forms:
                forms.append((form, entry.index))
        forms.sort(key=_form_sort_key)
        self.forms = tuple(forms)
        self.max_form_len = max(len(form) for form, _ in forms)
        # The master file-import form, verbatim: a Unicode-aware word
        # boundary on both sides and every surface form escaped, so no
        # caller string ever reaches the regex engine as syntax
        # (manage_files.py:3348; the survey importer at :134-140 still
        # interpolates unescaped, which is the bug this parity does not
        # inherit).
        self.pattern = re.compile(
            "(?<!\\w)(?:" + "|".join(re.escape(form) for form, _ in forms)
            + ")(?!\\w)", self.flags)
        # The other matcher, with the opposite contract: never used to
        # rewrite, always used to decide whether a string is reported or
        # withheld as carrying a name. See `NameDetector`.
        self.detector = NameDetector([form for form, _ in forms])
        self._exact = {form: index for form, index in forms}
        self._folded: Dict[str, int] = {}
        for form, index in forms:
            self._folded.setdefault(_fold(form), index)
        self._by_first: Dict[str, List[Tuple[str, int]]] = {}
        for form, index in forms:
            self._by_first.setdefault(_fold(form[0]), []).append((form, index))

        pseudonyms = sorted({entry.pseudonym for entry in mapping.entries},
                            key=lambda p: (-len(p), p))
        self.pseudonym_pattern = re.compile(
            "(?<!\\w)(?:" + "|".join(re.escape(p) for p in pseudonyms)
            + ")(?!\\w)", self.flags)
        self._pseudonym_entries: Dict[str, List[int]] = {}
        for entry in mapping.entries:
            key = _fold(entry.pseudonym) if self.flags else entry.pseudonym
            self._pseudonym_entries.setdefault(key, []).append(entry.index)

    def entry_for(self, matched: str) -> int:
        """Which entry produced `matched`.

        Three steps, fastest first, and the last is exact by
        construction: the combined pattern matched, so at least one
        escaped alternative full-matches the matched text under the same
        flags, and the alternatives are tested in the pattern's own
        order. The dictionaries are a short cut, not the authority,
        because `re.IGNORECASE` and `str.casefold` do not agree on every
        code point (U+00DF folds to "ss" but does not match "SS" under
        IGNORECASE).
        """
        found = self._exact.get(matched)
        if found is not None:
            return found
        if self.flags:
            found = self._folded.get(_fold(matched))
            if found is not None:
                return found
        for form, index in self.forms:
            if re.fullmatch(re.escape(form), matched, self.flags):
                return index
        raise AssertionError(                 # pragma: no cover - impossible
            "a matched span belongs to no surface form")

    def forms_at(self, text: str, position: int) -> List[Tuple[str, int]]:
        """Every surface form that matches, standalone, at `position`.

        Used only by the overlap diagnostic, which asks what the combined
        pass did NOT choose. Bucketed by first character so the scan is
        proportional to the forms that could start here rather than to
        the whole mapping.
        """
        if position >= len(text):
            return []
        found: List[Tuple[str, int]] = []
        for form, index in self._by_first.get(_fold(text[position]), ()):
            end = position + len(form)
            if end > len(text):
                continue
            candidate = text[position:end]
            if self.flags:
                # The regex, not a case-folded comparison: this decides
                # whether a form is REPORTED as having lost, so it has
                # to be the same test the matching pass itself applies.
                if not re.fullmatch(re.escape(form), candidate, self.flags):
                    continue
            elif candidate != form:
                continue
            if position > 0 and _is_word_char(text[position - 1]):
                continue
            if end < len(text) and _is_word_char(text[end]):
                continue
            found.append((form, index))
        return found

    def replacement_for(self, entry_index: int, matched: str) -> str:
        """The text that replaces one match, under this case mode.

        Under `insensitive_preserve` this is a HEURISTIC, and the tool
        description says so in the same word: it copies the shape of the
        matched text onto the pseudonym, which gets SHOUTED and
        lower-case passages right because that is what transcripts
        actually contain, and takes the pseudonym as written for
        anything else.
        """
        pseudonym = self.mapping.entries[entry_index].pseudonym
        if self.case_mode != "insensitive_preserve":
            return pseudonym
        # Anything that is not all upper or all lower (mixed case, a
        # capitalised word) takes the pseudonym as the researcher wrote
        # it. The length guard is upstream-shaped rather than reachable:
        # no surface form is shorter than two characters.
        if len(matched) > 1 and matched.isupper():
            return pseudonym.upper()
        if matched.islower():
            return pseudonym.lower()
        return pseudonym


_WORD_RE = re.compile(r"\w")


def _is_word_char(char: str) -> bool:
    """`\\w` for one character, the way the boundary assertions read it."""
    return _WORD_RE.match(char) is not None


class Replacement:
    """One edit: the span it covers and the text that takes its place."""

    __slots__ = ("start", "end", "entry", "matched", "text")

    def __init__(self, start: int, end: int, entry: int, matched: str,
                 text: str):
        self.start = start
        self.end = end
        self.entry = entry
        self.matched = matched
        self.text = text

    @property
    def delta(self) -> int:
        return len(self.text) - (self.end - self.start)

    def __repr__(self) -> str:                # pragma: no cover - debugging
        return f"Replacement({self.start}, {self.end}, entry={self.entry})"


def find_replacements(compiled: Compiled, text: str) -> List[Replacement]:
    """Every edit this mapping makes to `text`, left to right.

    One `finditer` over one pattern, so the matches are non-overlapping
    and leftmost, and at each start the longest viable surface form wins.
    Nothing this pass produces is ever matched again, which is the
    single-pass guarantee.
    """
    found: List[Replacement] = []
    for match in compiled.pattern.finditer(text):
        matched = match.group(0)
        entry = compiled.entry_for(matched)
        found.append(Replacement(match.start(), match.end(), entry, matched,
                                 compiled.replacement_for(entry, matched)))
    return found


def apply_replacements(text: str, replacements: Sequence[Replacement]) -> str:
    """The rewritten text."""
    if not replacements:
        return text
    pieces: List[str] = []
    cursor = 0
    for item in replacements:
        pieces.append(text[cursor:item.start])
        pieces.append(item.text)
        cursor = item.end
    pieces.append(text[cursor:])
    return "".join(pieces)


# --------------------------------------------------------------------------
# Diagnostics (computed from the same pass)
# --------------------------------------------------------------------------

def pre_existing_pseudonym_occurrences(
        compiled: Compiled, text: str,
        max_spans: Optional[int] = None) -> List[Dict[str, Any]]:
    """Where each pseudonym ALREADY occurs in the text (D1 3.4).

    A warning, never a refusal (owner ruling Q3): some researchers reuse
    common given names deliberately. What it costs is told plainly: after
    the run the rewritten text cannot distinguish a pseudonym this tool
    wrote from one that was always there, so a reversal driven by text
    would be unsafe. The run manifest records spans rather than strings
    for exactly this reason.
    """
    counts: Dict[int, List[Tuple[int, int]]] = {}
    for match in compiled.pseudonym_pattern.finditer(text):
        matched = match.group(0)
        key = _fold(matched) if compiled.flags else matched
        for index in compiled._pseudonym_entries.get(key, ()):
            counts.setdefault(index, []).append((match.start(), match.end()))
    report: List[Dict[str, Any]] = []
    for index in sorted(counts):
        spans = counts[index]
        shown = spans if max_spans is None else spans[:max_spans]
        item: Dict[str, Any] = {
            "entry": index,
            "pseudonym": compiled.mapping.entries[index].pseudonym,
            "count": len(spans),
            "spans": [[s, e] for s, e in shown],
        }
        if max_spans is not None and len(spans) > len(shown):
            item["spans_truncated"] = True
        report.append(item)
    return report


def overlap_conflicts(compiled: Compiled, text: str,
                      replacements: Sequence[Replacement]
                      ) -> Tuple[List[Dict[str, Any]], bool]:
    """Surface forms that lost the competition for the same characters.

    "Ann Marie" and "Marie Curie" over "Ann Marie Curie": leftmost and
    longest chooses "Ann Marie" and leaves "Curie" behind, so the second
    entry never fires there. The researcher has to see that, because
    nothing else in the preview would show it.

    Only the neighbourhood of each chosen match is examined: a form can
    only conflict with a match it overlaps, so the scan is bounded by the
    number of matches times the longest surface form, never by the text.

    Returns (conflicts, truncated).
    """
    conflicts: List[Dict[str, Any]] = []
    truncated = False
    for item in replacements:
        first = max(0, item.start - compiled.max_form_len + 1)
        for position in range(first, item.end):
            for form, index in compiled.forms_at(text, position):
                end = position + len(form)
                if end <= item.start:
                    continue                  # ends before this match
                if index == item.entry:
                    continue                  # the same entry, not a clash
                if position == item.start and end == item.end:
                    continue                  # the very match that was chosen
                if len(conflicts) >= MAX_OVERLAP_CONFLICTS:
                    return conflicts, True
                conflicts.append({
                    "entry": index,
                    "form": form,
                    "span": [position, end],
                    "loses_to_entry": item.entry,
                    "chosen_span": [item.start, item.end],
                })
    return conflicts, truncated


def case_variants_seen(compiled: Compiled, text: str) -> List[Dict[str, Any]]:
    """Occurrences that differ from every form only in case (D1 3.4).

    Reported under `case_mode="exact"` only, where they are precisely the
    occurrences this run does NOT replace. Counts, never spans: the point
    is "TOM appears twelve times and is not being touched", and a span
    list would not add to it.
    """
    if compiled.case_mode != "exact":
        return []
    insensitive = re.compile(compiled.pattern.pattern, re.IGNORECASE)
    pools: Dict[str, Dict[str, int]] = {}
    for match in insensitive.finditer(text):
        matched = match.group(0)
        pool = pools.setdefault(_fold(matched), {})
        pool[matched] = pool.get(matched, 0) + 1
    exact_forms = {form for form, _ in compiled.forms}
    report: List[Dict[str, Any]] = []
    for form, index in compiled.forms:
        pool = pools.get(_fold(form), {})
        other = sum(count for spelling, count in pool.items()
                    if spelling not in exact_forms)
        if other:
            report.append({"entry": index, "form": form,
                           "other_case_count": other})
    return report


# --------------------------------------------------------------------------
# The remap
# --------------------------------------------------------------------------

class RemappedSpan:
    """What the run would do to one stored span."""

    __slots__ = ("pos0", "pos1", "change", "clamped", "touched")

    def __init__(self, pos0: Optional[int], pos1: Optional[int],
                 change: str, clamped: bool, touched: bool):
        self.pos0 = pos0
        self.pos1 = pos1
        self.change = change
        self.clamped = clamped
        # Whether the old span met an edit at all. A row whose positions
        # do not move can still need its `seltext` refreshed, because a
        # pseudonym of the same length changes the text inside it.
        self.touched = touched

    def __repr__(self) -> str:                # pragma: no cover - debugging
        return (f"RemappedSpan({self.pos0}, {self.pos1}, {self.change}"
                f"{', clamped' if self.clamped else ''})")


class SpanMapper:
    """Maps old offsets to new ones for one text and one edit list.

    `snap_to_pseudonym` is arithmetic on a prefix sum and needs no walk;
    `qualcoder_edit_parity` walks the edits per row, which is the same
    semantics as upstream's walk over all rows per edit but the opposite
    iteration order, so the two are a real cross-check of each other.
    """

    def __init__(self, replacements: Sequence[Replacement], old_len: int):
        self.old_len = old_len
        self.starts = [item.start for item in replacements]
        self.ends = [item.end for item in replacements]
        self.new_lengths = [len(item.text) for item in replacements]
        self.replacements = list(replacements)
        cumulative = [0]
        for item in replacements:
            cumulative.append(cumulative[-1] + item.delta)
        self.cumulative = cumulative
        self.new_len = old_len + cumulative[-1]

    # -- snap_to_pseudonym ------------------------------------------------

    def _edit_containing(self, position: int) -> Optional[int]:
        """The edit `position` falls strictly inside, or None."""
        index = bisect_right(self.ends, position)
        if index < len(self.starts) and self.starts[index] < position:
            return index
        return None

    def _delta_before(self, position: int) -> int:
        return self.cumulative[bisect_right(self.ends, position)]

    def map_start(self, position: int) -> int:
        """Where a span's START goes under `snap_to_pseudonym`.

        Strictly inside a replaced name, the start snaps OUTWARD to the
        pseudonym's first character, so a coding that began in the middle
        of the name now begins at the pseudonym. Anywhere else it moves
        by the cumulative length change of the edits that finished before
        it.
        """
        inside = self._edit_containing(position)
        if inside is not None:
            return self.starts[inside] + self.cumulative[inside]
        return position + self._delta_before(position)

    def map_end(self, position: int) -> int:
        """Where a span's END goes under `snap_to_pseudonym`.

        The mirror of `map_start`: strictly inside a replaced name the
        end snaps outward to just past the pseudonym, so a coding that
        stopped in the middle of the name now contains the whole
        pseudonym. An end exactly at the name's end lands exactly at the
        pseudonym's end.
        """
        inside = self._edit_containing(position)
        if inside is not None:
            return (self.starts[inside] + self.new_lengths[inside]
                    + self.cumulative[inside])
        return position + self._delta_before(position)

    def _intersects_edit(self, pos0: int, pos1: int) -> bool:
        index = bisect_right(self.ends, pos0)
        return index < len(self.starts) and self.starts[index] < pos1

    def _snap(self, pos0: int, pos1: int) -> RemappedSpan:
        cut = (self._edit_containing(pos0) is not None
               or self._edit_containing(pos1) is not None)
        new0 = self.map_start(pos0)
        new1 = self.map_end(pos1)
        return self._classify(pos0, pos1, new0, new1, cut)

    # -- qualcoder_edit_parity --------------------------------------------

    def _parity(self, pos0: int, pos1: int,
                keep_start_anchor: bool) -> RemappedSpan:
        """Master's `apply_delete` then `apply_insert`, per edit.

        Transcribed from `code_text.py:5906-5927` and `:5893-5904` at pin
        9bddf17 and driven by the running offset of `:5933-5950`: each
        replacement is the deletion of the name followed by the insertion
        of the pseudonym at the same evolving offset. The consequences
        are upstream's own and are the reason this is the OPTION and not
        the default: a span that sits exactly on a name is deleted, and a
        span that merely touches one is trimmed to exclude the pseudonym
        entirely.
        """
        original_pos0 = pos0
        new0: Optional[int] = pos0
        new1 = pos1
        shift = 0
        head_cut = False
        tail_cut = False
        for index, item in enumerate(self.replacements):
            at = item.start + shift
            length = item.end - item.start
            inserted = self.new_lengths[index]
            if new0 is not None:
                # apply_delete (code_text.py:5906-5927)
                if new0 >= at + length:
                    new0 -= length
                    new1 -= length
                elif new0 >= at:
                    if new1 <= at + length:
                        new0 = None           # span fully inside the name
                    else:
                        # The span's head held the name, whether it began
                        # inside it or exactly on it; either way the
                        # pseudonym ends up outside the span.
                        new0 = at
                        new1 -= length
                        head_cut = True
                elif new1 > at:
                    # This branch also fires for a span that STRICTLY
                    # CONTAINS the name, where nothing is cut at all: the
                    # span shrinks by the name and grows by the pseudonym
                    # through the insert below. Only an end at or inside
                    # the name's end really loses the pseudonym, so only
                    # that is recorded as a cut.
                    if new1 <= at + length:
                        tail_cut = True
                    new1 -= min(new1, at + length) - at
                    if new1 <= new0:
                        new0 = None
            if new0 is not None:
                # apply_insert (code_text.py:5893-5904)
                if new0 >= at:
                    new0 += inserted
                    new1 += inserted
                    if keep_start_anchor and original_pos0 == 0:
                        new0 = 0
                elif new0 < at < new1:
                    new1 += inserted
            shift += inserted - length
        if new0 is None:
            return RemappedSpan(None, None, DELETED, False, True)
        # A span anchored at the file start never moves its start, so a
        # head cut on it was undone in the same step and is not a cut.
        anchored = keep_start_anchor and original_pos0 == 0
        cut = tail_cut or (head_cut and not anchored)
        return self._classify(pos0, pos1, new0, new1, cut)

    # -- shared ------------------------------------------------------------

    def _classify(self, pos0: int, pos1: int, new0: int, new1: int,
                  cut: bool) -> RemappedSpan:
        """One vocabulary for both policies.

        `snapped` means the run had to MOVE a boundary because it met a
        replaced name: under `snap_to_pseudonym` a boundary that lay
        strictly inside a name snapped outward to contain the whole
        pseudonym; under `qualcoder_edit_parity` a head or tail cut
        excluded the pseudonym from the span. `resized` is any other
        length change, which is what a span that strictly contains a
        name gets when the pseudonym is a different length. `shifted` is
        a span that kept its length, which includes one containing a
        name whose pseudonym is exactly as long: the hidden-coder rule
        treats that as a pure shift, because the coder still marks the
        same passage.
        """
        touched = self._intersects_edit(pos0, pos1) or cut
        if cut:
            change = SNAPPED
        elif (new1 - new0) != (pos1 - pos0):
            change = RESIZED
        elif (new0, new1) != (pos0, pos1):
            change = SHIFTED
        else:
            change = UNCHANGED
        return RemappedSpan(new0, new1, change, False, touched)

    def map_row(self, pos0: Any, pos1: Any, policy: str,
                keep_start_anchor: bool) -> Optional[RemappedSpan]:
        """Map one stored row, or None when the row is not mappable.

        Returning None is the "leave it exactly as it is" answer for a
        row whose stored positions are not a span: NULL, not an integer,
        NEGATIVE, or `pos0 >= pos1`. None refuses the run; all are
        listed. A negative position is a damaged row QualCoder never
        writes, and mapping one would write it back negative and could
        collide with the parked values the write uses; left alone and
        reported, it is a row the researcher can go and look at (QA F-9).

        A `pos1` past the end of the text is CLAMPED on the old side
        before mapping and reported, which is master's own rule applied a
        step earlier (`code_text.py:6232-6233`, "clamp, never delete a
        valid code"). Clamping before rather than after is what makes
        the guarantee that no row is written with an end past the new
        text hold under both policies rather than only under one.

        A clamped row is never classified as a pure shift. The classes
        describe what happens to the STORED span, and a clamp truncates
        it: the annotation stored at (76, 200) on an 81-character text
        is written as (66, 71), which is a 119-character resize however
        the rest of the run moved it. Classifying that as `shifted` put
        it under ruling X1's exemption and let a hidden coder's row be
        resized with no override asked for (Security S5).
        """
        if isinstance(pos0, bool) or isinstance(pos1, bool):
            return None
        if not isinstance(pos0, int) or not isinstance(pos1, int):
            return None
        if pos0 < 0 or pos1 < 0:
            return None
        clamped = False
        if pos1 > self.old_len:
            pos1 = self.old_len
            clamped = True
        if pos0 >= pos1:
            return None
        if policy == "qualcoder_edit_parity":
            mapped = self._parity(pos0, pos1, keep_start_anchor)
        elif policy == "snap_to_pseudonym":
            mapped = self._snap(pos0, pos1)
        else:
            raise ValueError(
                f"overlap_policy must be one of "
                f"{', '.join(OVERLAP_POLICIES)}.")
        mapped.clamped = clamped
        if clamped and mapped.change in (UNCHANGED, SHIFTED):
            mapped.change = RESIZED
        return mapped


def unique_constraint_collisions(keys: Sequence[Tuple[Any, ...]],
                                 row_ids: Sequence[Any]
                                 ) -> List[Dict[str, Any]]:
    """Rows that would land on the same unique key after the remap.

    `code_text` is unique on (cid, fid, pos0, pos1, owner) and
    `annotation` on (fid, pos0, pos1, owner) in QualCoder's own schema
    (`__main__.py:1819-1821` and `:1800-1801` at master, in that order,
    identical at the 3.8.2 tag). Two rows can collapse onto one span when both were cut by
    the same name: one marking "Thomas" and one marking "Thom" both snap
    to the pseudonym. Rare enough that the right answer is a human
    decision, so the preview lists it and the execute refuses.
    """
    groups: Dict[Tuple[Any, ...], List[Any]] = {}
    for key, row_id in zip(keys, row_ids):
        groups.setdefault(key, []).append(row_id)
    return [{"key": list(key), "row_ids": sorted(ids)}
            for key, ids in sorted(groups.items(), key=lambda kv: str(kv[0]))
            if len(ids) > 1]


def context_for(text: str, start: int, end: int, chars: int) -> str:
    """The text around one match, for `include_context`.

    This returns FILE CONTENT, which is why the argument is off by
    default and why the tool description says so in as many words.
    """
    chars = max(0, min(int(chars), MAX_CONTEXT_CHARS))
    return text[max(0, start - chars):min(len(text), end + chars)]
