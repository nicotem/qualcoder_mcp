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
from bisect import bisect_left, bisect_right
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
# Ours, a WARNING threshold and not a limit. The residue block reads a
# name as a bare substring, and QualCoder's own two-character minimum
# is well below the length at which that reading stops being exact: a
# form of fewer than four characters makes the residue counts generous
# (re-verification 6.2 measured the turn from zero false hits to one in
# five at four). The rewrite is whole-word and is not affected.
SHORT_FORM_CHARS = 4
# Ours (v0.13, ruling 7). The file-text count's work budget, in
# characters times surface forms, summed over the files in the order the
# count reads them. Deterministic rather than a clock, so the same
# preview always counts the same files. About two seconds at the rate
# the counts study measured (4.7 ms per megabyte per surface form):
# 1.8 MB at 200 forms, or 10 MB at 40, and a twenty-interview study at
# seven forms uses three per cent of it. A file past the budget is still
# read, with the one cheap question "does any name show here", and the
# report says which files those are; it is never omitted and never
# reported clean when it is not.
MAX_RESIDUE_SCAN_WORK = 400_000_000
# Ours (v0.13). Rows in the residue's file-text block. The totals are
# always complete, and every further file that shows a name is listed by
# id: the house shape of `max_spans_per_entry` and
# `MAX_OVERLAP_CONFLICTS`.
MAX_RESIDUE_FILE_ROWS = 200
# Ours (v0.13, decision B). The longer words listed per entry per file
# on the typed path, most frequent first; the list says when it stopped.
MAX_LONGER_WORDS_PER_ENTRY = 20
# Ours (v0.13). A run of word characters longer than this is not a word
# anybody would add as an exact entry, and returning it would return a
# slab of the file text; it is counted and not listed, and the list says
# it is incomplete.
MAX_LONGER_WORD_CHARS = 100

CASE_MODES = ("exact", "insensitive", "insensitive_preserve")
OVERLAP_POLICIES = ("snap_to_pseudonym", "qualcoder_edit_parity")

# How a row's span changed. One vocabulary for both policies, because the
# preview shape and the hidden-coder rule key on it. `substituted` is the
# owner's ruling of 2026-09-15 (QC40_PLATFORM 7.3(3), refining X1): a
# span that sat exactly on a replaced name and now sits exactly on its
# pseudonym is a pure substitution, whatever the two lengths, and is
# exempt from the hidden-coder override like a pure shift. `clamped` is
# the owner's ruling of 2026-09-16 (QC40_PLATFORM 7.4, decision 7): a
# span that CONTAINED a whole replaced name and changed length only
# because that name did is `resized` and is exempt too, so the one row
# whose extent changed for a reason the rewrite does not explain, the
# damaged row whose stored end lay past the end of the text, needs a
# class of its own to stay on the override side.
UNCHANGED = "unchanged"
SHIFTED = "shifted"
SUBSTITUTED = "substituted"
RESIZED = "resized"
SNAPPED = "snapped"
DELETED = "deleted"
CLAMPED = "clamped"

# Characters refused in any mapping string.
#
# D1 3.2 names line breaks, U+2029 and control characters. This pattern is
# a superset, and the additions are deliberate:
#
# - U+2028 as well as U+2029, because both are line separators Qt's
#   document model treats as breaks;
# - the bidirectional formatting controls, all of them: Unicode's
#   Bidi_Control set is U+061C, U+200E, U+200F, U+202A to U+202E and
#   U+2066 to U+2069, because a pseudonym is written INTO the
#   researcher's text and one of these would visually reorder every line
#   after it while leaving the stored offsets untouched. U+061C is the
#   weakest of them (an invisible strong-AL character rather than a
#   range control) and was the one this list missed; no pseudonym needs
#   it, so the set is now the whole property rather than an enumeration;
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
    (0x061C, 0x061C),      # ARABIC LETTER MARK, the fifth bidi control
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


def canonical_entry_positions(mapping: Mapping) -> Dict[int, int]:
    """Each caller entry index, as its position in `canonical_mapping`.

    The signed effect block records which ENTRY made each replacement,
    and it used to record the caller's index: so the identical mapping
    given in a different order, which `canonical_mapping` binds to the
    same token, signed a different effect and was refused as
    "the project changed" on a project that had not (fix round 3, S3).
    Keyed on the canonical order, the same mapping signs the same effect
    however it was typed, and a token issued from `pseudonyms.json`
    executes from the identical typed mapping in any order.
    """
    def nfc(value: str) -> str:
        return unicodedata.normalize("NFC", value)

    order = sorted(
        range(len(mapping.entries)),
        key=lambda index: (nfc(mapping.entries[index].original),
                           nfc(mapping.entries[index].pseudonym),
                           sorted(nfc(v) for v in mapping.entries[index].variants)))
    return {caller: position for position, caller in enumerate(order)}


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

# Code points a reader does not see: Unicode's Default_Ignorable_Code_Point
# property, transcribed as ranges from DerivedCoreProperties.txt of the
# Unicode version named below (4,174 code points in seventeen ranges;
# the same set in 14.0.0 and 16.0.0, and one short in 13.0.0, which had
# not yet assigned U+180F). `unicodedata` does not expose the property,
# and an interpreter's own tables are that interpreter's (Python 3.10
# carries 13.0.0), so the table is embedded and applied as it stands on
# every interpreter. It holds the format characters a reader never sees
# (the soft hyphen, the zero-width space, joiner and non-joiner, the
# word joiner, the bidi marks and controls, the byte order mark, the
# tag characters), the marks and fillers that render as nothing (the
# combining grapheme joiner, the variation selectors, the Mongolian free
# variation selectors, the Khmer inherent vowels, the Hangul fillers)
# and the reserved code points the standard says a renderer must treat
# the same way. Fix round 3 approximated the property with category Cf
# plus a hand list that stopped at U+180D, and U+180F and the reserved
# ranges walked a name past the detector into the manifest (fix round
# 4, R2). Each range boundary is pinned against this table, and the
# count against the file's own total.
DEFAULT_IGNORABLE_UNICODE_VERSION = "15.1.0"
DEFAULT_IGNORABLE_RANGES = (
    (0x00AD, 0x00AD),     # SOFT HYPHEN
    (0x034F, 0x034F),     # COMBINING GRAPHEME JOINER
    (0x061C, 0x061C),     # ARABIC LETTER MARK
    (0x115F, 0x1160),     # HANGUL CHOSEONG FILLER, HANGUL JUNGSEONG FILLER
    (0x17B4, 0x17B5),     # KHMER VOWEL INHERENT AQ, AA
    (0x180B, 0x180F),     # MONGOLIAN FREE VARIATION SELECTORS ONE to FOUR
                          # and the MONGOLIAN VOWEL SEPARATOR between them
    (0x200B, 0x200F),     # ZERO WIDTH SPACE to RIGHT-TO-LEFT MARK
    (0x202A, 0x202E),     # the bidi embeddings and overrides
    (0x2060, 0x206F),     # WORD JOINER to NOMINAL DIGIT SHAPES, with the
                          # reserved U+2065 and the bidi isolates inside
    (0x3164, 0x3164),     # HANGUL FILLER
    (0xFE00, 0xFE0F),     # VARIATION SELECTOR-1 to -16
    (0xFEFF, 0xFEFF),     # ZERO WIDTH NO-BREAK SPACE, the byte order mark
    (0xFFA0, 0xFFA0),     # HALFWIDTH HANGUL FILLER
    (0xFFF0, 0xFFF8),     # reserved
    (0x1BCA0, 0x1BCA3),   # the shorthand format controls
    (0x1D173, 0x1D17A),   # the musical symbol beam and phrase controls
    (0xE0000, 0xE0FFF),   # LANGUAGE TAG, the tag characters, VARIATION
                          # SELECTOR-17 to -256, and the reserved code
                          # points between and after them
)
DEFAULT_IGNORABLE_COUNT = 4174
_DEFAULT_IGNORABLE = frozenset(
    code_point for low, high in DEFAULT_IGNORABLE_RANGES
    for code_point in range(low, high + 1))
# A pattern that matches nothing, for a mapping whose every surface form
# is made of characters a reader does not see.
_NEVER = re.compile(r"(?!x)x")


def is_default_ignorable(code_point: int) -> bool:
    """Whether Unicode's Default_Ignorable_Code_Point property holds."""
    return code_point in _DEFAULT_IGNORABLE


# The sweep below, as one `str.translate` table: every code point in
# `_DEFAULT_IGNORABLE` and every code point the RUNNING interpreter puts
# in category Cf, each mapped to None. Built on the first non-ASCII call
# and held here, never at import: enumerating Cf over the whole code
# point space costs tens of milliseconds once, which a server that only
# ever sees ASCII should not pay (v0.13). Two threads that race to build
# it build the same table and the second assignment replaces the first
# with an equal one, so no lock is needed.
_UNSEEN_TABLE: Optional[Dict[int, None]] = None


def _unseen_table() -> Dict[int, None]:
    """The translation table `_strip_unseen` applies, built once."""
    global _UNSEEN_TABLE
    table = _UNSEEN_TABLE
    if table is None:
        table = dict.fromkeys(_DEFAULT_IGNORABLE)
        table.update(dict.fromkeys(
            code_point for code_point in range(0x110000)
            if unicodedata.category(chr(code_point)) == "Cf"))
        _UNSEEN_TABLE = table
    return table


def _strip_unseen(text: str) -> str:
    """`text` less every character a reader does not see, unnormalised.

    The property above, and category Cf beside it. The format
    characters outside the property (the Arabic, Syriac and Kaithi
    number signs, the interlinear annotation controls, the Egyptian
    hieroglyph format controls) are visible marks by Unicode's own
    account, so stripping them is a widening rather than the property;
    it is kept because a wider reading here is the safe one and the
    round before this one already read that wide. Unlike the table,
    the sweep reads the interpreter's own Unicode tables, so which
    visible format marks it removes beyond the property varies with
    the interpreter, in the over-detect direction only (fix round 5,
    D-1 note); the table does not move.

    How the sweep is applied, since v0.13, and nothing about what it
    removes: pure ASCII is returned as it stands, because no ASCII code
    point is default-ignorable or in category Cf; anything else goes
    through `_unseen_table()`, the property and the interpreter's own Cf
    sweep united in one `str.translate` table. The output is the
    per-character generator's, byte for byte (pinned against a copy of
    that generator kept in the tests), at a fraction of its cost.
    """
    if text.isascii():
        return text
    return text.translate(_unseen_table())


def _reader_sees(value: str) -> str:
    """`value` as a reader sees it.

    Compatibility normalisation (NFKC), so a fullwidth or otherwise
    compatibility-equivalent spelling reads as the plain one, and the
    default-ignorable code points removed, so an invisible character
    inside a word does not split it. Applied to every VALUE the detector
    is asked about; the forms go through `_detector_parts`, which
    splits before it strips. What this does NOT do, stated because the
    residue prose says so too: a look-alike letter from another script
    (a Cyrillic "о" for a Latin "o") is a different code point after
    every normalisation there is, and is out of scope. Spaces of every
    width are spaces: NFKC turns a thin or hair space into a plain one,
    and a plain one is a separator, not an invisible character.
    """
    return _strip_unseen(unicodedata.normalize("NFKC", value))


def _detector_parts(form: str) -> List[str]:
    """The words of one surface form, as a reader sees them.

    Split FIRST, on the normalised form, and strip inside each word
    afterwards. The order matters and was wrong in fix round 3, which
    stripped before splitting: `Mary<ZWSP>Ann` fused into the one word
    `MaryAnn`, which no longer found "Mary Ann" or "Mary_Ann.txt", a
    narrower reading than the round before it had (fix round 4, R1). An
    invisible character between two words is a separator to whoever
    put it there (a soft hyphen at a line break, a zero-width space
    from a web page); one inside a word that `\\w` counts as a letter (a
    Hangul filler) is nothing at all, and stripping it inside the word
    reads the word whole. Either way the reading is at least as wide as
    the split alone gave.
    """
    text = unicodedata.normalize("NFKC", form)
    return [part for part in (_strip_unseen(piece)
                              for piece in _SEPARATOR_SPLIT.split(text))
            if part]


def _detector_alternative(form: str, fold: bool = False) -> str:
    """One surface form as a pattern that survives a changed separator.

    A form of one word is itself, escaped. A form of several is its
    words escaped and joined by "any run of separators, or none", so a
    transcript called `Mary_Ann.txt` and a case called `MaryAnn` are
    both found. A form with no letters or digits at all (which
    validation permits: two punctuation marks pass the length rule) has
    no words to join and is used literally; one made entirely of
    characters a reader does not see comes back empty, and the caller
    drops it. With `fold`, the casefolded reading of the same words.
    """
    parts = _detector_parts(form)
    if fold:
        parts = [_fold(part) for part in parts]
    if parts:
        return _SEPARATOR_RUN.join(re.escape(part) for part in parts)
    literal = _reader_sees(form)
    return re.escape(_fold(literal) if fold else literal)


def _detector_pattern(forms: Sequence[str], fold: bool = False) -> str:
    """The alternation over `forms`, empty alternatives left out."""
    return "|".join(alternative for alternative in
                    (_detector_alternative(form, fold) for form in forms)
                    if alternative)


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
        # Each form as a reader sees it, split into its words before
        # anything is stripped (`_detector_parts`), and never an empty
        # alternative: a form made entirely of invisible characters
        # (validation lets a zero-width space through, and two of them
        # pass the length rule) would otherwise match every string.
        if not any(_detector_alternative(form) for form in forms):
            self._direct = self._folded = _NEVER
            return
        self._direct = re.compile(_detector_pattern(forms), re.IGNORECASE)
        # A second reading, because `re.IGNORECASE` and `str.casefold` do
        # not agree on every code point (U+00DF casefolds to "ss" but
        # does not match "SS" under IGNORECASE). Two searches over one
        # short string cost nothing, and here the wider answer is the
        # safe one.
        self._folded = re.compile(_detector_pattern(forms, fold=True))

    def contains(self, value: Any) -> bool:
        """True when a reader of `value` would see any surface form.

        Anything that is not a non-empty string is False: a NULL label
        carries no name, and nothing downstream should have to know that
        a missing value and a clean one are different answers.
        """
        if not isinstance(value, str) or not value:
            return False
        text = _reader_sees(value)
        return bool(self._direct.search(text)
                    or self._folded.search(_fold(text)))


class Compiled:
    """A mapping compiled into one pattern, plus the lookups it needs."""

    __slots__ = ("mapping", "case_mode", "flags", "pattern", "forms",
                 "_exact", "_folded", "_by_first", "max_form_len",
                 "pseudonym_pattern", "_pseudonym_entries", "detector",
                 "_text_lookup")

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
        # Built on first use by the file-text count, never here: nothing
        # on the rewrite path needs it (v0.13).
        self._text_lookup: Optional["_TextLookup"] = None

    def text_lookup(self) -> "_TextLookup":
        """What `names_left_in_text` needs from these forms, built once."""
        if self._text_lookup is None:
            self._text_lookup = _TextLookup(self)
        return self._text_lookup

    def carries_a_name(self, value: Any) -> bool:
        """Whether `value` shows a name to a reader OR to this run's rule.

        The union of the two matchers, and the one predicate for every
        question of the form "is a name here": the wide count of a note,
        a label or an attribute value, whether a file shows a name, and
        whether a file name, a path or a pseudonym is withheld from a
        record. The detector alone reads NFKC, which composes a combining
        mark onto the letter before it, so a name followed by one ("Rene"
        typed decomposed as "René") is matched by the whole-word rule and
        not seen by the detector; the union keeps the wide reading at
        least as wide as the rewrite, which is what v0.12 founded it on,
        and makes every withholding withhold more, never less (lead's
        ruling of 2026-09-23 on QA-1).
        """
        if not isinstance(value, str) or not value:
            return False
        return bool(self.detector.contains(value)
                    or self.pattern.search(value))

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


def short_forms(mapping: Mapping) -> List[Dict[str, Any]]:
    """The surface forms shorter than `SHORT_FORM_CHARS` (a warning).

    Entry index and length only, never the form: this block travels on
    the sidecar path too, where a surface form is the researcher's
    reverse key. Length is in code points, the same measure the
    validator's minimums use.
    """
    found: List[Dict[str, Any]] = []
    for entry in mapping.entries:
        for form in entry.forms:
            if len(form) < SHORT_FORM_CHARS:
                found.append({"entry": entry.index, "length": len(form)})
    return found


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
# Names left in the file text (v0.13, Brief 1 item 4)
# --------------------------------------------------------------------------

# The kinds a wide occurrence in the file text is split into, in the
# order the report lists them. `unattributed` is not a kind: it is the
# occurrences no entry could be charged to, counted beside them.
TEXT_RESIDUE_REASONS = (
    "inside_a_longer_word", "case_only", "joined_differently",
    "normalisation_variants", "put_back_by_a_pseudonym",
    "whole_word_in_a_file_not_rewritten")

# What `names_left_in_text` returns for one file, JSON-ready:
# `occurrences` ({"wide": N, "whole_word": M}), `unattributed`,
# `entries` (one row per entry with anything to report), and the two
# spelling diagnostics `case_variants_seen` and
# `normalisation_variants_seen`.
TextResidue = Dict[str, Any]

_UNPLACED = object()


def _residue_key(value: str) -> Tuple[str, str]:
    """The attribution key of `value`: its reader words, joined, casefolded.

    `Mary Ann`, `MaryAnn`, `Mary_Ann` and `MARY ANN` all key to
    `maryann`, which is exactly the equivalence the detector's
    alternation encodes (`_detector_parts` splits on separators after
    NFKC and strips the unseen characters inside each word). The key
    JOINS the words: one that kept the word split disagreed with the
    capture-group reference in 792 of 5,479 random runs in the counts
    study, and this one in none, so the joined key is the right one. A
    value with no letters or digits keys by its literal reading, in a
    namespace of its own, so it never collides with a word key.
    """
    parts = _detector_parts(value)
    if parts:
        return ("words", _fold("".join(parts)))
    return ("literal", _fold(_reader_sees(value)))


class _TextLookup:
    """What the file-text count needs from one compiled mapping.

    Built once per `Compiled`, on first use, from the forms in the
    pattern's own order (`_form_sort_key`: longest first). Each key maps
    to every form that shares it, in that order; `_pick` breaks a tie
    the way the combined pattern breaks it.
    """

    __slots__ = ("direct", "folded", "reader_form", "all_ascii",
                 "folded_form", "entry_forms")

    def __init__(self, compiled: "Compiled"):
        self.direct: Dict[Tuple[str, str], List[Tuple[str, int, Any]]] = {}
        self.folded: Dict[Tuple[str, str], List[Tuple[str, int, Any]]] = {}
        self.reader_form: Dict[Tuple[int, str], str] = {}
        self.folded_form: Dict[Tuple[int, str], str] = {}
        self.entry_forms: Dict[int, List[str]] = {}
        for form, index in compiled.forms:
            self.entry_forms.setdefault(index, []).append(form)
            self.folded_form.setdefault((index, _fold(form)), form)
            if not _detector_alternative(form):
                continue        # a form no reader can see matches nothing
            key = _residue_key(form)
            # Each pattern built from the escaping builder at the site
            # that compiles it, as every pattern in this module is.
            self.direct.setdefault(key, []).append(
                (form, index,
                 re.compile(_detector_alternative(form), re.IGNORECASE)))
            self.folded.setdefault(key, []).append(
                (form, index,
                 re.compile(_detector_alternative(form, fold=True))))
            self.reader_form[(index, form)] = _reader_sees(form)
        self.all_ascii = all(form.isascii() for form, _ in compiled.forms)


def _pick(candidates: Sequence[Tuple[str, int, Any]], matched: str
          ) -> Tuple[str, int, Any]:
    """The form the combined pattern chose for `matched`, among those
    sharing its key.

    The alternation takes the first branch that matches at a position,
    so the first form, in the pattern's own order, whose alternative
    matches the whole of `matched` is the one it chose. One candidate
    is the common case and needs no regex at all.
    """
    if len(candidates) == 1:
        return candidates[0]
    for candidate in candidates:
        if candidate[2].fullmatch(matched):
            return candidate
    return candidates[0]


def _attributed(matches, lookup_table):
    """Each match of a detector pattern, with the form it is charged to.

    Yields `(match, (form, entry) or None)`. The entry is recovered
    from the matched TEXT by key, never from the regex engine: one
    capture group per alternative made the count cost 82 seconds per
    1.27 MB at the documented ceiling of 2,000 forms, which is the shape
    of the uncapped-form defect `MAX_FORM_CHARS` exists for. A match
    the lookup cannot place (possible where `re.IGNORECASE` and
    `str.casefold` disagree on a code point) is yielded with None: it is
    still counted, and charged to no entry.
    """
    placed: Dict[str, Any] = {}
    for match in matches:
        matched = match.group(0)
        candidate = placed.get(matched, _UNPLACED)
        if candidate is _UNPLACED:
            candidates = lookup_table.get(_residue_key(matched))
            candidate = (_pick(candidates, matched)[:2] if candidates
                         else None)
            placed[matched] = candidate
        yield match, candidate


def direct_attribution(compiled: "Compiled", seen: str
                       ) -> Tuple[Dict[Tuple[int, str], int], int]:
    """The wide reading's direct pass over `seen`, charged by form.

    One UNGROUPED `finditer` of the detector's direct pattern over the
    reader's text, the entry recovered by key (`_attributed`). Returns
    ({(entry, form): count}, unattributed). The counterpart the tests
    compare it with, one capture group per alternative, lives in the
    test file and never here.
    """
    lookup = compiled.text_lookup()
    counts: Dict[Tuple[int, str], int] = {}
    unattributed = 0
    for _match, candidate in _attributed(
            compiled.detector._direct.finditer(seen), lookup.direct):
        if candidate is None:
            unattributed += 1
            continue
        key = (candidate[1], candidate[0])
        counts[key] = counts.get(key, 0) + 1
    return counts, unattributed


def _rewriter_form(compiled: "Compiled", lookup: _TextLookup, matched: str
                   ) -> Tuple[int, str]:
    """(entry, form) for one match of the REWRITER's pattern.

    The entry is `Compiled.entry_for`'s, which is exact by construction;
    the form is the mapping's own spelling the match stands for, looked
    up exactly, then case-folded, then by the same full match
    `entry_for` falls back to.
    """
    index = compiled.entry_for(matched)
    if compiled._exact.get(matched) == index:
        return index, matched
    form = lookup.folded_form.get((index, _fold(matched)))
    if form is not None:
        return index, form
    for candidate in lookup.entry_forms[index]:
        if re.fullmatch(re.escape(candidate), matched, compiled.flags):
            return index, candidate
    return index, compiled.mapping.entries[index].original


# How far past a whole-word match the check below reads for combining
# marks: composition only ever joins a base letter to the marks that
# follow it, and no real name carries more than a few.
_TRAILING_MARKS_READ = 16


def _with_trailing_marks(text: str, start: int, end: int) -> str:
    """A whole-word match with the combining marks that follow it.

    The rewriter's `(?!\\w)` treats a combining mark as a boundary, so
    "Rene" followed by U+0301 is a whole word to it; a reader, and NFKC,
    join the mark to the letter and read "René". Reading the match
    together with its trailing marks is how the detector is asked about
    the occurrence as a reader sees it.
    """
    stop = min(len(text), end + _TRAILING_MARKS_READ)
    while end < stop and unicodedata.category(text[end]).startswith("M"):
        end += 1
    return text[start:end]


def _longer_word(seen: str, start: int, end: int) -> Optional[str]:
    """The run of word characters around a match, as the reader sees it.

    `Thomas_P01` is one word and `Thomas_Smith.txt` gives
    `Thomas_Smith`. None when the run is longer than
    `MAX_LONGER_WORD_CHARS`: that is a slab of the text, not a word,
    and the scan stops there rather than walking it.
    """
    budget = MAX_LONGER_WORD_CHARS - (end - start)
    left = start
    while left > 0 and _is_word_char(seen[left - 1]):
        left -= 1
        budget -= 1
        if budget < 0:
            return None
    right = end
    while right < len(seen) and _is_word_char(seen[right]):
        right += 1
        budget -= 1
        if budget < 0:
            return None
    return seen[left:right]


def names_left_in_text(compiled: "Compiled", text: str,
                       list_longer_words: bool,
                       rewritten_by_this_run: bool) -> TextResidue:
    """Where this mapping's names are left in one file's text.

    `text` is the file as it will be AFTER the run: the rewritten text
    for the one file the run rewrites, the stored text for every other.
    Counts, never spans and never surrounding text; with
    `list_longer_words`, the longer words a name sits inside (the typed
    path only, decided by the caller).

    Two readings of every count, in one object `{"wide", "whole_word"}`:

    - wide: the detector's reading of the reader's text, and beside it
      every whole word the rewriter matches that the reader's reading
      does not see (a name followed by a combining mark, which NFKC
      composes onto its last letter): an occurrence counts in the wide
      reading when either matcher finds it. Every wide occurrence is
      charged to exactly one kind (`TEXT_RESIDUE_REASONS`) or to
      `unattributed`, so the kinds and `unattributed` add up to the wide
      count on every row; the split is a heuristic and the total is
      not.
    - whole_word: the rewriter's own pattern over the RAW text, reported
      as it stands. On the file this run rewrote it is normally zero,
      and non-zero means a pseudonym put a name back.

    The kinds, for each wide match in the reader's text: a word
    character touches either end (inside_a_longer_word); otherwise the
    match casefolds differently from the form as the reader sees it
    (joined_differently); otherwise it differs from that form and the
    case mode is exact (case_only); otherwise it IS the form, whole
    word, and the rewriter left it. That last class has two causes,
    split per form by the whole-word count of the raw text: on the row
    of the file this run rewrote, `min(whole_word, class)` is charged to
    put_back_by_a_pseudonym and the rest to normalisation_variants; on
    every other row nothing was put back by this run, so the same share
    is charged to whole_word_in_a_file_not_rewritten instead. The `min`
    and `max` are a declared HEURISTIC about which cause an occurrence
    is charged to when both are present; their sum is exact.
    """
    lookup = compiled.text_lookup()
    seen = _reader_sees(text)
    rows: Dict[Tuple[int, str], Dict[str, Any]] = {}

    def row_for(key: Tuple[int, str]) -> Dict[str, Any]:
        row = rows.get(key)
        if row is None:
            row = rows[key] = {"direct": 0, "inside_a_longer_word": 0,
                               "case_only": 0, "joined_differently": 0,
                               "left_whole": 0, "folded_excess": 0,
                               "whole_word": 0, "rewriter_only": 0,
                               "words": {}, "words_withheld": False}
        return row

    unattributed = 0
    direct_total = 0
    exact_mode = compiled.case_mode == "exact"
    for match, candidate in _attributed(
            compiled.detector._direct.finditer(seen), lookup.direct):
        direct_total += 1
        if candidate is None:
            unattributed += 1
            continue
        form, index = candidate
        row = row_for((index, form))
        row["direct"] += 1
        matched = match.group(0)
        start, end = match.span()
        if ((start > 0 and _is_word_char(seen[start - 1]))
                or (end < len(seen) and _is_word_char(seen[end]))):
            row["inside_a_longer_word"] += 1
            if list_longer_words:
                word = _longer_word(seen, start, end)
                if word is None:
                    row["words_withheld"] = True
                else:
                    row["words"][word] = row["words"].get(word, 0) + 1
            continue
        reader_form = lookup.reader_form[(index, form)]
        if _fold(matched) != _fold(reader_form):
            row["joined_differently"] += 1
        elif matched != reader_form and exact_mode:
            row["case_only"] += 1
        else:
            row["left_whole"] += 1

    # The casefolded second reading, which `NameDetector.contains` makes
    # too: `re.IGNORECASE` and `str.casefold` disagree on some code
    # points (U+00DF casefolds to "ss" and does not match "SS" under
    # IGNORECASE), so a spelling only case-folding reaches is missed by
    # the direct pass above. Where the folded pass finds more for a form
    # than the direct pass did, the excess is added to that form's wide
    # count and charged to normalisation_variants. This is a HEURISTIC,
    # declared as one: a spelling only case-folding reaches is one the
    # rewriter cannot see under any case mode, so it is charged there,
    # and it is what keeps `wide` from being zero on a file where
    # `detector.contains` says a name shows. The excess is capped at the
    # folded pass's net surplus over the direct one, so an occurrence the
    # direct pass could not place (dotted capital I, which IGNORECASE
    # matches and casefolding spells as two code points) and the folded
    # pass could is not counted twice. On ASCII text with ASCII forms
    # the two passes cannot disagree, so the second is skipped.
    if not (seen.isascii() and lookup.all_ascii):
        folded_counts: Dict[Tuple[int, str], int] = {}
        folded_unattributed = 0
        for _match, candidate in _attributed(
                compiled.detector._folded.finditer(_fold(seen)),
                lookup.folded):
            if candidate is None:
                folded_unattributed += 1
                continue
            key = (candidate[1], candidate[0])
            folded_counts[key] = folded_counts.get(key, 0) + 1
        surplus = (sum(folded_counts.values()) + folded_unattributed
                   - direct_total)
        for form, index in compiled.forms:
            if surplus <= 0:
                break
            key = (index, form)
            excess = folded_counts.get(key, 0) - (
                rows[key]["direct"] if key in rows else 0)
            if excess > 0:
                excess = min(excess, surplus)
                row_for(key)["folded_excess"] += excess
                surplus -= excess
        if surplus > 0:
            unattributed += min(surplus, max(
                0, folded_unattributed - unattributed))

    # The whole-word half, over the RAW text. A whole word the rewriter
    # matches that the detector does not see, read with the combining
    # marks after it as a reader reads it, is an occurrence of the wide
    # reading too (the union; lead's ruling on QA-1), charged below to
    # the whole-word kind of its row. Asked once per distinct spelling.
    detector_sees: Dict[str, bool] = {}
    whole_word_total = 0
    for match in compiled.pattern.finditer(text):
        whole_word_total += 1
        row = row_for(_rewriter_form(compiled, lookup, match.group(0)))
        row["whole_word"] += 1
        snippet = _with_trailing_marks(text, match.start(), match.end())
        seen_alone = detector_sees.get(snippet)
        if seen_alone is None:
            seen_alone = detector_sees[snippet] = \
                compiled.detector.contains(snippet)
        if not seen_alone:
            row["rewriter_only"] += 1

    entries: Dict[int, Dict[str, Any]] = {}
    case_variants: List[Dict[str, Any]] = []
    normalisation_variants: List[Dict[str, Any]] = []
    wide_total = unattributed
    for form, index in compiled.forms:
        row = rows.get((index, form))
        if row is None:
            continue
        # The heuristic split of the class the rewriter left whole, per
        # form (see the docstring); the sum of the two is exact. The
        # whole words only the rewriter sees are whole words of that
        # kind outright, and are added to the wide count.
        only = row["rewriter_only"]
        seen_whole = row["whole_word"] - only
        shared = min(seen_whole, row["left_whole"]) + only
        spelling = (max(0, row["left_whole"] - seen_whole)
                    + row["folded_excess"])
        wide = row["direct"] + row["folded_excess"] + only
        wide_total += wide
        entry = entries.get(index)
        if entry is None:
            entry = entries[index] = {
                "entry": index,
                "form": compiled.mapping.entries[index].original,
                "occurrences": {"wide": 0, "whole_word": 0},
                "inside_a_longer_word": 0, "case_only": 0,
                "joined_differently": 0, "normalisation_variants": 0,
                "put_back_by_a_pseudonym": 0,
                "whole_word_in_a_file_not_rewritten": 0,
                "_words": {}, "_withheld": False}
        entry["occurrences"]["wide"] += wide
        entry["occurrences"]["whole_word"] += row["whole_word"]
        entry["inside_a_longer_word"] += row["inside_a_longer_word"]
        entry["case_only"] += row["case_only"]
        entry["joined_differently"] += row["joined_differently"]
        entry["normalisation_variants"] += spelling
        if rewritten_by_this_run:
            entry["put_back_by_a_pseudonym"] += shared
        else:
            entry["whole_word_in_a_file_not_rewritten"] += shared
        for word, count in row["words"].items():
            entry["_words"][word] = entry["_words"].get(word, 0) + count
        entry["_withheld"] = entry["_withheld"] or row["words_withheld"]
        if row["case_only"]:
            case_variants.append({"entry": index, "form": form,
                                  "other_case_count": row["case_only"]})
        if spelling:
            normalisation_variants.append({"entry": index, "form": form,
                                           "count": spelling})

    listed: List[Dict[str, Any]] = []
    for index in sorted(entries):
        entry = entries[index]
        words = entry.pop("_words")
        withheld = entry.pop("_withheld")
        if not (entry["occurrences"]["wide"]
                or entry["occurrences"]["whole_word"]):
            continue
        if list_longer_words:
            ranked = sorted(words.items(), key=lambda kv: (-kv[1], kv[0]))
            entry["longer_words"] = [
                {"word": word, "count": count}
                for word, count in ranked[:MAX_LONGER_WORDS_PER_ENTRY]]
            if withheld or len(ranked) > MAX_LONGER_WORDS_PER_ENTRY:
                entry["longer_words_truncated"] = True
        listed.append(entry)
    return {"occurrences": {"wide": wide_total,
                            "whole_word": whole_word_total},
            "unattributed": unattributed,
            "entries": listed,
            "case_variants_seen": case_variants,
            "normalisation_variants_seen": normalisation_variants}


# The owner's ruling of 2026-09-23 on Brief 1's finding F-1: a pseudonym
# that contains a name from the mapping is withheld, by the rule that
# withholds a file name carrying one, from the run record and the journal
# entry on both mapping paths and from the preview on the
# `use_project_pseudonyms` path. The one sentence every such place says.
PSEUDONYMS_WITHHELD_NOTE = (
    "A pseudonym that contains a name from this mapping is withheld here, "
    "by the rule that withholds a file name that carries one; its entry "
    "number identifies it. The run still writes it into the text.")


def pseudonyms_withheld(compiled: "Compiled") -> Tuple[int, ...]:
    """The entries whose pseudonym carries a name from the mapping.

    `Compiled.carries_a_name` over each pseudonym: the union of the two
    matchers, so "Alex Smith" (a whole word of `Smith`), "Thomas Jr" (its
    own original) and "Thomasina" (inside a longer word, which the static
    check of `pseudonyms_containing_a_name` cannot see) are all withheld.
    The price is the wide reading's: a short name withholds a pseudonym
    that merely contains its letters ("Ed" in "Fred"), and the entry
    number still identifies it.
    """
    return tuple(entry.index for entry in compiled.mapping.entries
                 if compiled.carries_a_name(entry.pseudonym))


def pseudonyms_containing_a_name(compiled: "Compiled"
                                 ) -> List[Dict[str, Any]]:
    """Pseudonyms the rewriter's own rule finds a name in (v0.13, item 5).

    A static check, before any text is read: the rewriter's compiled
    pattern run over each pseudonym. `{"Smith": "Jones", "Thomas Smith":
    "Alex Smith"}` puts the real surname back wherever the second entry
    fires, and a pseudonym that contains its OWN original ("Thomas" to
    "Thomas Jr") does the same. What it cannot see, and says so where
    it is reported: a pseudonym that puts a name back inside a longer
    word (`xThomasx`), which no whole-word rule matches, and a
    pseudonym that forms a name with the words around it.

    Each finding once per (entry, contained form): the entry whose
    pseudonym it is, the pseudonym, the entry it contains and that
    entry's form as the mapping spells it.
    """
    lookup = compiled.text_lookup()
    found: List[Dict[str, Any]] = []
    for entry in compiled.mapping.entries:
        reported = set()
        for match in compiled.pattern.finditer(entry.pseudonym):
            index, form = _rewriter_form(compiled, lookup, match.group(0))
            if (index, form) in reported:
                continue
            reported.add((index, form))
            found.append({"entry": entry.index,
                          "pseudonym": entry.pseudonym,
                          "contains_entry": index, "form": form})
    return found


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

    def _substitutes(self, pos0: int, pos1: int, new0: int, new1: int
                     ) -> bool:
        """Whether the old span WAS one replaced name and the new span
        IS that name's pseudonym, both boundaries on the edit's own.

        Under `snap_to_pseudonym` the second half follows from the first
        by the arithmetic of `map_start` and `map_end`; under
        `qualcoder_edit_parity` such a span is deleted before it reaches
        `_classify`. Both halves are checked all the same, because the
        class is defined by both (owner ruling 7.3(3)) and a third
        policy would inherit the definition rather than the coincidence.
        """
        index = bisect_left(self.starts, pos0)
        if index >= len(self.starts) or self.starts[index] != pos0 \
                or self.ends[index] != pos1:
            return False
        start = self.starts[index] + self.cumulative[index]
        return (new0, new1) == (start, start + self.new_lengths[index])

    def _classify(self, pos0: int, pos1: int, new0: int, new1: int,
                  cut: bool) -> RemappedSpan:
        """One vocabulary for both policies.

        `snapped` means the run had to MOVE a boundary because it met a
        replaced name: under `snap_to_pseudonym` a boundary that lay
        strictly inside a name snapped outward to contain the whole
        pseudonym; under `qualcoder_edit_parity` a head or tail cut
        excluded the pseudonym from the span. `substituted` is a span
        that sat exactly on one replaced name and now sits exactly on
        that name's pseudonym, whatever the two lengths: the coder
        marked the name and still marks it under its new spelling, so
        no coding decision changed (owner ruling 7.3(3) of 2026-09-15,
        refining X1; before it this span was classified by length, a
        shift when the lengths matched and a resize when they did not).
        `resized` is any other length change, which is what a span that
        contains a name without being the name gets when the pseudonym
        is a different length: "Mr Thomas said" grows or shrinks with the
        pseudonym and records the same decision about the same words, so
        ruling 7.4 of 2026-09-16 exempts it from the hidden-coder
        override as well. `shifted` is a span that kept its length and
        moved, which includes one containing a name whose pseudonym is
        exactly as long: the coder still marks the same passage.

        The order matters. A cut is a snap before anything else, so no
        span that had to grow can read as a substitution. `clamped` is
        decided in `map_row`, which alone holds the flag, and it comes
        before every test here but the two whose classes are gated
        already.
        """
        touched = self._intersects_edit(pos0, pos1) or cut
        if cut:
            change = SNAPPED
        elif self._substitutes(pos0, pos1, new0, new1):
            change = SUBSTITUTED
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

        A clamped row has a class of its own, `clamped`, which nothing
        else produces. The classes describe what happens to the STORED
        span, and a clamp truncates it: the annotation stored at
        (76, 200) on an 81-character text is written as (66, 71), which
        is a 119-character truncation however the rest of the run moved
        it. Classifying that as `shifted` put it under ruling X1's
        exemption and let a hidden coder's row be cut back with no
        override asked for (Security S5); classifying it as `resized`
        kept it gated until ruling 7.4 of 2026-09-16 exempted the
        ordinary resize, which is why the clamp now has a class instead
        of borrowing one. The same holds when the clamp lands the
        truncated span exactly on a name that ends the text: the coder
        never marked that name alone, so the row is `clamped` and not a
        substitution. Only a snap and a deletion keep their own class
        through a clamp, both being on the override side already; the
        test is written that way round so that a class added later
        stays gated rather than exempt by omission.
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
        if clamped and mapped.change not in (SNAPPED, DELETED):
            mapped.change = CLAMPED
        return mapped


def unique_constraint_collisions(keys: Sequence[Tuple[Any, ...]],
                                 row_ids: Sequence[Any],
                                 shown=None) -> List[Dict[str, Any]]:
    """Rows that would land on the same unique key after the remap.

    `code_text` is unique on (cid, fid, pos0, pos1, owner) and
    `annotation` on (fid, pos0, pos1, owner) in QualCoder's own schema
    (`__main__.py:1819-1821` and `:1800-1801` at master, in that order,
    identical at the 3.8.2 tag). Two rows can collapse onto one span when both were cut by
    the same name: one marking "Thomas" and one marking "Thom" both snap
    to the pseudonym. Rare enough that the right answer is a human
    decision, so the preview lists it and the execute refuses.

    Rows are GROUPED on the whole key, because that is the constraint,
    and REPORTED under `shown(key)` when a caller gives one. The
    database layer uses it to leave the owner column out of what the
    preview carries: the key's owner can be a coder the project hides,
    and two rows of one hidden coder cut by one name is an ordinary
    shape (one marking "Thomas", one marking "Thom"), so the owner in
    the reported key named a hidden coder in a preview whose every
    other field withholds the name (X1). `row_ids` identify the rows
    on their own.
    """
    groups: Dict[Tuple[Any, ...], List[Any]] = {}
    for key, row_id in zip(keys, row_ids):
        groups.setdefault(key, []).append(row_id)
    return [{"key": list(key if shown is None else shown(key)),
             "row_ids": sorted(ids)}
            for key, ids in sorted(groups.items(), key=lambda kv: str(kv[0]))
            if len(ids) > 1]


def context_for(text: str, start: int, end: int, chars: int) -> str:
    """The text around one match, for `include_context`.

    This returns FILE CONTENT, which is why the argument is off by
    default and why the tool description says so in as many words.
    """
    chars = max(0, min(int(chars), MAX_CONTEXT_CHARS))
    return text[max(0, start - chars):min(len(text), end + chars)]
