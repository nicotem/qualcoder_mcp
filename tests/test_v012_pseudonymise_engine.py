"""The v0.12 flagship engine: parity oracles, properties, edge cases (D1 6.1, 6.2, 6.3).

Two oracles drive this file, both literal transcriptions of QualCoder
master at pin 9bddf17, sitting beside the engine rather than inside it:

- `master_boundary_spans`, the import-time replacement regex of
  `manage_files.py:3344-3349`, which is where our whole-word rule comes
  from;
- `MasterEditWalk`, the `apply_insert` / `apply_delete` pair of
  `code_text.py:5893-5927` driven by the running offset of `:5933-5950`,
  which is what `overlap_policy="qualcoder_edit_parity"` promises.

The second oracle is a real cross-check rather than a mirror: it walks
ALL rows per edit, mutating a shared list the way upstream does, while
the engine walks all edits per row and carries one row's state. An error
in the order of the delete and the insert, in the running offset, or in
the start anchor shows up as a disagreement.

What the oracles do NOT claim, stated here because the claim would be
easy to overstate: upstream feeds its walk whatever `diff_match_patch`
produced for a keystroke, and that library may factor a common prefix or
suffix out of a replacement. This engine never diffs; it deletes the
whole name and inserts the whole pseudonym at the same offset. Parity is
with the WALK, for that edit shape, and a test below says so.
"""

import hashlib
import re
import sys
from pathlib import Path

import pytest
from hypothesis import HealthCheck, Phase, given, settings, strategies as st

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from qualcoder_mcp import pseudonymise as P


# =============================================================================
# HOUSE RULES
# =============================================================================

def _house_rules(texts, labels=None):
    """No em dashes, British English, no forbidden vocabulary.

    Checked in the module that owns the strings, as Batch A's convention
    has it, so a reworded message is checked where it is written.
    """
    forbidden_spellings = ("color", "colors", "behavior", "organize",
                           "recognize", "authorization", "analyze",
                           "labeled", "favor", "pseudonymize",
                           "pseudonymization", "anonymize")
    for index, text in enumerate(texts):
        label = (labels[index] if labels else f"text {index}")
        assert "—" not in text, label
        lowered = text.lower()
        for word in forbidden_spellings:
            pattern = r"(?<![A-Za-z_])" + word + r"(?![A-Za-z_])"
            assert not re.search(pattern, lowered), (label, word)


# =============================================================================
# ORACLE 1: master's import-time boundary regex (manage_files.py:3344-3349)
# =============================================================================

def master_boundary_spans(original, text):
    """Where QualCoder master's own import replacement would fire.

    Transcribed from `manage_files.py:3348` at pin 9bddf17:

        re.sub(rf"(?<!\\w){re.escape(pseudonym['original'])}(?!\\w)",
               pseudonym['pseudonym'], text_)

    The spans, not the substitution, because the spans are what our
    engine has to agree with; what goes in them is a separate question
    (case modes, longest-first) that upstream does not have.
    """
    pattern = re.compile(rf"(?<!\w){re.escape(original)}(?!\w)")
    return [(m.start(), m.end()) for m in pattern.finditer(text)]


def legacy_boundary_spans(original, text):
    """The 3.8.2 and survey-importer form, for the delta pins.

    `3.8.2:manage_files.py:2035-2040` and, at BOTH pins,
    `import_survey.py:134-140`:

        re.sub(rf"\\b{pseudonym['original']}\\b", ...)

    Two differences from master's form, and this function reproduces
    both: `\\b` rather than the two lookarounds, and NO `re.escape`, so
    an original carrying a regex metacharacter is read as syntax.
    """
    return [(m.start(), m.end())
            for m in re.finditer(rf"\b{original}\b", text)]


# Two text strategies, and why there are two (fix round 3, S8).
#
# `_TEXT` draws characters from an alphabet: letters that are word
# characters, the separators that are not (space, hyphen, apostrophe,
# full stop, newline), and two non-ASCII letters so the Unicode-aware
# reading of `\w` is exercised rather than assumed. It is the right
# strategy for the BOUNDARY, where what matters is which character sits
# either side of a match, and the wrong one for everything downstream of
# a match, because a text drawn one character at a time from that
# alphabet held a whole-word "Tom" or "Ann" in 0.2 per cent of examples.
# Instrumented at the final verification: the edit-walk parity oracle
# saw 0 replacements in 3,000 examples across 10 seeds, and two planted
# parity bugs stayed green under it.
#
# `_NAMED_TEXT` builds a text from TOKENS joined by generated separators.
# The vocabulary carries the names the property tests map ("Tom", "Ann",
# "ab") beside their near-misses (inside a longer word, another case,
# possessive, the two non-ASCII scripts), and the separators are drawn
# from the characters `\w` does and does not count, so a whole-word
# match, a boundary miss and a separator-joined form all occur in most
# examples. `TestTheGeneratorsExerciseWhatTheyClaim` measures both.
_TEXT_ALPHABET = "abTom ANn-'.\né中"
_TEXT = st.text(alphabet=_TEXT_ALPHABET, min_size=0, max_size=60)
_NAMES = st.sampled_from(["Tom", "Ann", "ab", "Tom Ann", "Téo", "中文",
                          "An-n", "ab.c"])

_TOKENS = st.sampled_from(["Tom", "Ann", "ab", "Tom", "Ann", "x", "Anna",
                           "Tommy", "TOM", "ann", "Téo", "中文", "a", "b",
                           "c", "n", "An"])
_SEPARATORS = st.sampled_from([" ", " ", "-", "'", ".", "\n", "", "  ",
                               ", "])


@st.composite
def _named_text(draw):
    count = draw(st.integers(min_value=0, max_value=10))
    pieces = []
    for index in range(count):
        if index:
            pieces.append(draw(_SEPARATORS))
        pieces.append(draw(_TOKENS))
    return "".join(pieces)


_NAMED_TEXT = _named_text()


@st.composite
def _boundary_sample(draw):
    """A (text, name) pair for the boundary oracles.

    The name is drawn FIRST and the text is built around it: the name
    itself, the name with a letter or digit either side (a boundary
    miss), its parts joined by another separator, and the ordinary
    tokens, joined by generated separators; one example in three is a
    text from the arbitrary-character alphabet instead, so every
    character in it still reaches the boundary. Built this way a
    two-word or hyphenated name ("Tom Ann", "An-n", "ab.c") occurs as
    often as a one-word one, where the token vocabulary alone produced
    it in under two per cent of examples.
    """
    name = draw(_NAMES)
    if draw(st.integers(min_value=0, max_value=2)) == 0:
        return draw(_TEXT), name
    parts = [part for part in re.split(r"[^\w]+", name) if part]
    near = [name + "x", "x" + name, name + "1", name.upper(),
            "-".join(parts), "".join(parts), " ".join(parts)]
    tokens = st.sampled_from([name, name, name] + near
                             + ["ab", "x", "Tom", "Ann", "é", "中", "b"])
    count = draw(st.integers(min_value=1, max_value=8))
    pieces = []
    for index in range(count):
        if index:
            pieces.append(draw(_SEPARATORS))
        pieces.append(draw(tokens))
    return "".join(pieces), name


_BOUNDARY_SAMPLE = _boundary_sample()


@st.composite
def _text_and_spans(draw):
    """A named text and up to eight spans over it.

    The spans are drawn RELATIVE to the text's length, which is what
    removes the `assume(spans)` the old span source needed (and with it
    the `filter_too_much` suppression of re-verification R-4): a span
    over an empty text is not drawn, and every span drawn is a span a
    project could hold. The names sit at token boundaries, so a span
    that touches one is common rather than lucky.
    """
    text = draw(_NAMED_TEXT)
    length = len(text)
    spans = []
    if length:
        for _ in range(draw(st.integers(min_value=0, max_value=8))):
            a = draw(st.integers(min_value=0, max_value=length))
            b = draw(st.integers(min_value=0, max_value=length))
            lo, hi = min(a, b), max(a, b)
            if lo < hi:
                spans.append((lo, hi))
    return text, spans


_TEXT_AND_SPANS = _text_and_spans()


class TestBoundaryParityOracle:
    """D1 6.1: our matching agrees with master's, span for span."""

    @settings(max_examples=250, deadline=None,
              suppress_health_check=[HealthCheck.too_slow])
    @given(sample=_BOUNDARY_SAMPLE)
    def test_one_entry_matches_exactly_where_master_would(self, sample):
        text, name = sample
        mapping = P.validate_mapping(
            [{"original": name, "pseudonym": "Pseudo"}])
        compiled = P.Compiled(mapping)
        ours = [(r.start, r.end) for r in P.find_replacements(compiled, text)]
        assert ours == master_boundary_spans(name, text)

    @settings(max_examples=200, deadline=None,
              suppress_health_check=[HealthCheck.too_slow])
    @given(sample=_BOUNDARY_SAMPLE)
    def test_the_rewritten_text_is_masters_rewritten_text(self, sample):
        """One entry cannot chain, so the whole substitution is comparable."""
        text, name = sample
        mapping = P.validate_mapping(
            [{"original": name, "pseudonym": "Pseudo"}])
        compiled = P.Compiled(mapping)
        ours = P.apply_replacements(text,
                                    P.find_replacements(compiled, text))
        theirs = re.sub(rf"(?<!\w){re.escape(name)}(?!\w)", "Pseudo", text)
        assert ours == theirs

    @pytest.mark.parametrize("text,name,expected", [
        ("Tom", "Tom", [(0, 3)]),
        ("Anna", "Ann", []),
        ("Ann.", "Ann", [(0, 3)]),
        ("Tom's cat", "Tom", [(0, 3)]),
        ("Tom’s cat", "Tom", [(0, 3)]),
        ("Jean-Paul", "Jean", [(0, 4)]),
        ("say Tom", "Tom", [(4, 7)]),
        ("Tom\nsaid", "Tom", [(0, 3)]),
        ("_Tom", "Tom", []),
        ("9Tom", "Tom", []),
        ("José went", "José", [(0, 4)]),
        ("Josés went", "José", []),
        ("中文 text", "中文", [(0, 2)]),
        ("a中文 text", "中文", []),
    ])
    def test_the_table_of_boundary_cases(self, text, name, expected):
        """D1 6.1's table, pinned against the oracle and against us."""
        assert master_boundary_spans(name, text) == expected
        mapping = P.validate_mapping(
            [{"original": name, "pseudonym": "Pseudo"}])
        compiled = P.Compiled(mapping)
        assert [(r.start, r.end)
                for r in P.find_replacements(compiled, text)] == expected


class TestBoundaryDeltas:
    """Where `\\b` and the escaping differ, documented as "we follow master"."""

    def test_a_trailing_non_word_character_inverts_the_rule(self):
        """"St." under `\\b` needs a WORD character next; master's form
        needs a non-word one. Opposite answers on the same text, which is
        exactly why the delta is pinned rather than assumed away."""
        assert legacy_boundary_spans("St\\.", "St.Ives") == [(0, 3)]
        assert legacy_boundary_spans("St\\.", "St. Ives") == []
        assert master_boundary_spans("St.", "St.Ives") == []
        assert master_boundary_spans("St.", "St. Ives") == [(0, 3)]
        mapping = P.validate_mapping(
            [{"original": "St.", "pseudonym": "Pseudo"}])
        compiled = P.Compiled(mapping)
        assert [(r.start, r.end) for r in
                P.find_replacements(compiled, "St. Ives")] == [(0, 3)]
        assert P.find_replacements(compiled, "St.Ives") == []

    def test_a_leading_non_word_character_inverts_the_rule(self):
        assert legacy_boundary_spans("-ish", "boy-ish") == [(3, 7)]
        assert master_boundary_spans("-ish", "boy-ish") == []

    def test_the_legacy_form_reads_an_original_as_a_regex(self):
        """3.8.2 and both survey importers interpolate unescaped, so a
        full stop matches any character. Ours escapes, so it does not."""
        assert legacy_boundary_spans("a.c", "abc") == [(0, 3)]
        assert master_boundary_spans("a.c", "abc") == []
        mapping = P.validate_mapping(
            [{"original": "a.c", "pseudonym": "Pseudo"}])
        compiled = P.Compiled(mapping)
        assert P.find_replacements(compiled, "abc") == []
        assert len(P.find_replacements(compiled, "a.c")) == 1

    def test_a_regex_metacharacter_cannot_reach_the_engine_as_syntax(self):
        """The whole class, not one character: every surface form is
        escaped, so no mapping string is ever read as a pattern."""
        for hostile in ["a(b", "a)b", "a[b", "a|b", "a*b", "a+b", "a?b",
                        "a{2}", "a\\b", "a$b", "^ab"]:
            mapping = P.validate_mapping(
                [{"original": hostile, "pseudonym": "Pseudo"}])
            compiled = P.Compiled(mapping)
            found = P.find_replacements(compiled, f" {hostile} ")
            assert [(r.start, r.end) for r in found] == \
                [(1, 1 + len(hostile))], hostile


# =============================================================================
# ORACLE 2: master's edit-mode walk (code_text.py:5893-5950)
# =============================================================================

class MasterEditWalk:
    """`apply_insert` and `apply_delete`, transcribed from master.

    `code_text.py:5893-5904` and `:5906-5927` at pin 9bddf17, kept in
    upstream's own shape: a list of item dicts carrying `pos0`,
    `newpos0` and `newpos1`, mutated in place, with a deletion queue.
    The driver below supplies the running offset of `:5933-5950` for the
    delete-then-insert pair each replacement amounts to.
    """

    def __init__(self):
        self.code_deletions = []

    @staticmethod
    def apply_insert(items, at, length, keep_start_anchor):
        for c in items:
            if c['newpos0'] is None:
                continue
            if c['newpos0'] >= at:
                c['newpos0'] += length
                c['newpos1'] += length
                if keep_start_anchor and c['pos0'] == 0:
                    c['newpos0'] = 0
            elif c['newpos0'] < at < c['newpos1']:
                c['newpos1'] += length

    def apply_delete(self, items, at, length, delete_sql):
        for c in items:
            if c['newpos0'] is None:
                continue
            if c['newpos0'] >= at + length:
                c['newpos0'] -= length
                c['newpos1'] -= length
            elif c['newpos0'] >= at:
                if c['newpos1'] <= at + length:
                    self.code_deletions.append(delete_sql(c))
                    c['newpos0'] = None
                else:
                    c['newpos0'] = at
                    c['newpos1'] -= length
            elif c['newpos1'] > at:
                c['newpos1'] -= min(c['newpos1'], at + length) - at
                if c['newpos1'] <= c['newpos0']:
                    self.code_deletions.append(delete_sql(c))
                    c['newpos0'] = None

    def run(self, spans, replacements, keep_start_anchor):
        """The walk over every row, one edit at a time (upstream's order).

        Each replacement reaches the walk as upstream's editor would see
        a whole-token retype: a delete of the name at the running offset
        followed by an insert of the pseudonym at the same offset.

        Upstream marks a deleted row by setting `newpos0` to None and
        NEVER touches its `newpos1` again, because the row is about to be
        removed by the queued SQL and the end is meaningless. The end is
        normalised to None here so the comparison is against the ANSWER,
        not against a field upstream stopped maintaining.
        """
        items = [{'id': index, 'pos0': a, 'pos1': b, 'newpos0': a,
                  'newpos1': b} for index, (a, b) in enumerate(spans)]
        shift = 0
        for item in replacements:
            at = item.start + shift
            length = item.end - item.start
            inserted = len(item.text)
            self.apply_delete(items, at, length, lambda c: c['id'])
            self.apply_insert(items, at, inserted, keep_start_anchor)
            shift += inserted - length
        return [(None, None) if c['newpos0'] is None
                else (c['newpos0'], c['newpos1']) for c in items]


class TestEditWalkParityOracle:
    """D1 6.1: `qualcoder_edit_parity` is master's walk, row by row.

    The oracle draws from `_TEXT_AND_SPANS`, whose spans are generated
    relative to the text, so nothing is filtered and no health check
    needs suppressing: re-verification R-4's `filter_too_much`
    suppression (seed 22456547744127925701047518566622594684) covered a
    filter that no longer exists. What it did NOT cover, and what the
    final verification found, was that the old text strategy held a
    whole-word name in 0.2 per cent of examples, so this oracle ran
    3,000 examples with zero replacements and two planted parity bugs
    stayed green under it. `TestTheGeneratorsExerciseWhatTheyClaim`
    holds the measurement now.
    """

    @settings(max_examples=300, deadline=None,
              suppress_health_check=[HealthCheck.too_slow])
    @given(sample=_TEXT_AND_SPANS, anchor=st.booleans())
    def test_the_engine_agrees_with_masters_walk(self, sample, anchor):
        text, spans = sample
        mapping = P.validate_mapping([
            {"original": "Tom", "pseudonym": "Pseudo"},
            {"original": "Ann", "pseudonym": "Quux"},
        ])
        compiled = P.Compiled(mapping)
        replacements = P.find_replacements(compiled, text)
        oracle = MasterEditWalk().run(spans, replacements, anchor)
        mapper = P.SpanMapper(replacements, len(text))
        ours = []
        for pos0, pos1 in spans:
            mapped = mapper.map_row(pos0, pos1, "qualcoder_edit_parity",
                                    anchor)
            ours.append((mapped.pos0, mapped.pos1))
        assert ours == oracle

    @pytest.mark.parametrize("span,expected", [
        ((4, 7), (None, None)),      # exactly the name: deleted
        ((4, 6), (None, None)),      # starting inside, ending inside
        ((5, 9), (10, 12)),          # starting inside: cut to after
        ((0, 6), (0, 4)),            # ending inside: cut to before
        ((0, 11), (0, 14)),          # containing the name: grows
        ((8, 11), (11, 14)),         # after the name: shifts
        ((0, 3), (0, 3)),            # before the name: unmoved
    ])
    def test_the_five_shapes_D1_names(self, span, expected):
        """"Say Tom now": the coding shapes D1 1.2 and 6.1 enumerate."""
        text = "Say Tom now"
        mapping = P.validate_mapping(
            [{"original": "Tom", "pseudonym": "Pseudo"}])
        compiled = P.Compiled(mapping)
        replacements = P.find_replacements(compiled, text)
        mapper = P.SpanMapper(replacements, len(text))
        mapped = mapper.map_row(span[0], span[1], "qualcoder_edit_parity",
                                True)
        assert (mapped.pos0, mapped.pos1) == expected
        assert MasterEditWalk().run([span], replacements, True) == [expected]

    def test_the_start_anchor_holds_for_codings_and_case_links_only(self):
        """`keep_start_anchor` is True for codings and case links and
        False for annotations (`code_text.py:5940-5942`), and it only
        matters when the span's ORIGINAL pos0 was 0."""
        text = "Tom said so"
        mapping = P.validate_mapping(
            [{"original": "Tom", "pseudonym": "Pseudo"}])
        compiled = P.Compiled(mapping)
        replacements = P.find_replacements(compiled, text)
        mapper = P.SpanMapper(replacements, len(text))
        anchored = mapper.map_row(0, len(text), "qualcoder_edit_parity", True)
        loose = mapper.map_row(0, len(text), "qualcoder_edit_parity", False)
        assert anchored.pos0 == 0
        assert loose.pos0 == 6        # the insert pushed the start right
        assert MasterEditWalk().run([(0, len(text))], replacements, True) \
            == [(anchored.pos0, anchored.pos1)]
        assert MasterEditWalk().run([(0, len(text))], replacements, False) \
            == [(loose.pos0, loose.pos1)]

    def test_an_insertion_exactly_at_the_end_is_excluded(self):
        """Master's `apply_insert` matches neither branch when the
        insertion lands on `newpos1`, so text typed straight after a
        span stays outside it (`code_text.py:5893-5904`)."""
        text = "say Tom"
        mapping = P.validate_mapping(
            [{"original": "Tom", "pseudonym": "Pseudo"}])
        compiled = P.Compiled(mapping)
        replacements = P.find_replacements(compiled, text)
        mapper = P.SpanMapper(replacements, len(text))
        mapped = mapper.map_row(0, 4, "qualcoder_edit_parity", False)
        assert (mapped.pos0, mapped.pos1) == (0, 4)

    def test_the_382_end_of_file_deletion_is_not_reproduced(self):
        """`3.8.2:code_text.py`'s `ed_update_codings` deletes every row
        with `newpos1 >= len(self.text)` on each edit-mode exit, so a
        coding that legitimately ends at the end of the file dies on
        every edit. Master replaced that with a clamp (`:6232-6233`) and
        neither policy here reproduces the deletion: the row survives
        and still ends at the end of the rewritten text."""
        text = "Tom said so"
        compiled = P.Compiled(P.validate_mapping(
            [{"original": "Tom", "pseudonym": "Pseudo"}]))
        replacements = P.find_replacements(compiled, text)
        mapper = P.SpanMapper(replacements, len(text))
        for policy in P.OVERLAP_POLICIES:
            mapped = mapper.map_row(4, len(text), policy, True)
            assert mapped.pos0 is not None, policy
            assert mapped.pos1 == mapper.new_len, policy

    def test_parity_trims_a_trailing_name_which_is_not_that_deletion(self):
        """The distinction the previous test would otherwise blur.

        When the LAST token of a span is a replaced name, master's own
        walk excludes the pseudonym: the delete takes the span's end back
        to the edit point and the insert at that same point matches
        neither branch. The row survives (it is not the 3.8.2 deletion)
        but it no longer reaches the end of the text, and that is one of
        the reasons this policy is the option rather than the default.
        """
        text = "he met Tom"
        compiled = P.Compiled(P.validate_mapping(
            [{"original": "Tom", "pseudonym": "Pseudo"}]))
        replacements = P.find_replacements(compiled, text)
        mapper = P.SpanMapper(replacements, len(text))
        parity = mapper.map_row(0, len(text), "qualcoder_edit_parity", True)
        snap = mapper.map_row(0, len(text), "snap_to_pseudonym", True)
        assert (parity.pos0, parity.pos1) == (0, 7)
        assert (snap.pos0, snap.pos1) == (0, mapper.new_len)
        assert MasterEditWalk().run([(0, len(text))], replacements, True) \
            == [(parity.pos0, parity.pos1)]

    def test_parity_is_with_the_walk_not_with_the_diff_library(self):
        """The honest limit of the claim, asserted rather than described.

        Replacing "Thomas" with "Thom" is one delete plus one insert
        here. `diff_match_patch` may instead report a common prefix and
        a shorter delete, which would move the edit boundary and give a
        different answer for a span inside the shared prefix. This test
        shows the two answers differ, so nothing downstream can read the
        parity claim more widely than it is meant.
        """
        text = "say Thomas now"
        mapping = P.validate_mapping(
            [{"original": "Thomas", "pseudonym": "Thom"}])
        compiled = P.Compiled(mapping)
        replacements = P.find_replacements(compiled, text)
        whole_token = P.SpanMapper(replacements, len(text)).map_row(
            4, 8, "qualcoder_edit_parity", False)
        # The same rewrite expressed as the prefix-factored diff a diff
        # library may produce: keep "Thom", delete "as".
        factored = [P.Replacement(8, 10, 0, "as", "")]
        prefix_factored = MasterEditWalk().run([(4, 8)], factored, False)
        assert (whole_token.pos0, whole_token.pos1) == (None, None)
        assert prefix_factored == [(4, 8)]


# =============================================================================
# ENGINE PROPERTIES (D1 6.2)
# =============================================================================

class TestSnapProperties:

    @settings(max_examples=300, deadline=None,
              suppress_health_check=[HealthCheck.too_slow])
    @given(sample=_TEXT_AND_SPANS)
    def test_no_span_is_ever_emptied_or_deleted(self, sample):
        """The defining promise of `snap_to_pseudonym`: a pseudonym is
        the same token as the name, so a coding that marked the name
        marks the pseudonym and nothing is lost."""
        text, spans = sample
        compiled = P.Compiled(P.validate_mapping([
            {"original": "Tom", "pseudonym": "Pseudo"},
            {"original": "Ann", "pseudonym": "Quu"},
        ]))
        replacements = P.find_replacements(compiled, text)
        mapper = P.SpanMapper(replacements, len(text))
        for pos0, pos1 in spans:
            mapped = mapper.map_row(pos0, pos1, "snap_to_pseudonym", True)
            assert mapped is not None
            assert mapped.pos0 is not None
            assert 0 <= mapped.pos0 < mapped.pos1 <= mapper.new_len

    @settings(max_examples=250, deadline=None,
              suppress_health_check=[HealthCheck.too_slow])
    @given(sample=_TEXT_AND_SPANS)
    def test_the_new_slice_is_the_old_slice_with_the_names_replaced(
            self, sample):
        """A span that did not CUT a name comes out reading the same,
        with the pseudonyms in place of the names."""
        text, spans = sample
        mapping = P.validate_mapping([
            {"original": "Tom", "pseudonym": "Pseudo"},
            {"original": "Ann", "pseudonym": "Quu"},
        ])
        compiled = P.Compiled(mapping)
        replacements = P.find_replacements(compiled, text)
        new_text = P.apply_replacements(text, replacements)
        mapper = P.SpanMapper(replacements, len(text))
        for pos0, pos1 in spans:
            mapped = mapper.map_row(pos0, pos1, "snap_to_pseudonym", True)
            if mapped.change == P.SNAPPED:
                continue
            inner = [r for r in replacements
                     if r.start >= pos0 and r.end <= pos1]
            expected = P.apply_replacements(
                text[pos0:pos1],
                [P.Replacement(r.start - pos0, r.end - pos0, r.entry,
                               r.matched, r.text) for r in inner])
            assert new_text[mapped.pos0:mapped.pos1] == expected

    @settings(max_examples=200, deadline=None,
              suppress_health_check=[HealthCheck.too_slow])
    @given(sample=_TEXT_AND_SPANS)
    def test_a_disjoint_span_shifts_by_the_cumulative_delta(self, sample):
        text, spans = sample
        mapping = P.validate_mapping(
            [{"original": "Tom", "pseudonym": "Pseudo"}])
        compiled = P.Compiled(mapping)
        replacements = P.find_replacements(compiled, text)
        mapper = P.SpanMapper(replacements, len(text))
        for pos0, pos1 in spans:
            if any(r.start < pos1 and r.end > pos0 for r in replacements):
                continue
            delta = sum(r.delta for r in replacements if r.end <= pos0)
            mapped = mapper.map_row(pos0, pos1, "snap_to_pseudonym", True)
            assert (mapped.pos0, mapped.pos1) == (pos0 + delta, pos1 + delta)
            assert mapped.change in (P.UNCHANGED, P.SHIFTED)

    @settings(max_examples=200, deadline=None,
              suppress_health_check=[HealthCheck.too_slow])
    @given(sample=_TEXT_AND_SPANS)
    def test_a_span_is_substituted_exactly_when_it_is_a_replaced_name(
            self, sample):
        """Ruling 7.3(3) as a property: under the snap policy the class
        `substituted` is given to a span if and only if the span equals
        one replacement, and such a span comes out reading exactly the
        pseudonym, whatever the two lengths ("Tom" grows, "Ann" keeps
        its length here)."""
        text, spans = sample
        mapping = P.validate_mapping([
            {"original": "Tom", "pseudonym": "Pseudo"},
            {"original": "Ann", "pseudonym": "Quu"},
        ])
        compiled = P.Compiled(mapping)
        replacements = P.find_replacements(compiled, text)
        new_text = P.apply_replacements(text, replacements)
        mapper = P.SpanMapper(replacements, len(text))
        names = {(r.start, r.end): r.text for r in replacements}
        for pos0, pos1 in spans + list(names):
            mapped = mapper.map_row(pos0, pos1, "snap_to_pseudonym", True)
            if (pos0, pos1) in names:
                assert mapped.change == P.SUBSTITUTED
                assert new_text[mapped.pos0:mapped.pos1] == names[(pos0, pos1)]
            else:
                assert mapped.change != P.SUBSTITUTED

    def test_a_span_equal_to_a_name_becomes_the_pseudonym(self):
        text = "say Tom now"
        mapping = P.validate_mapping(
            [{"original": "Tom", "pseudonym": "Pseudo"}])
        compiled = P.Compiled(mapping)
        replacements = P.find_replacements(compiled, text)
        new_text = P.apply_replacements(text, replacements)
        mapper = P.SpanMapper(replacements, len(text))
        mapped = mapper.map_row(4, 7, "snap_to_pseudonym", True)
        assert new_text[mapped.pos0:mapped.pos1] == "Pseudo"
        # A resize before ruling 7.3(3), by length; a substitution since.
        assert mapped.change == P.SUBSTITUTED

    @pytest.mark.parametrize("span", [(5, 9), (0, 6), (5, 6)])
    def test_a_span_that_cut_a_name_now_contains_the_whole_pseudonym(
            self, span):
        text = "say Tom now"
        mapping = P.validate_mapping(
            [{"original": "Tom", "pseudonym": "Pseudo"}])
        compiled = P.Compiled(mapping)
        replacements = P.find_replacements(compiled, text)
        new_text = P.apply_replacements(text, replacements)
        mapper = P.SpanMapper(replacements, len(text))
        mapped = mapper.map_row(span[0], span[1], "snap_to_pseudonym", True)
        assert "Pseudo" in new_text[mapped.pos0:mapped.pos1]
        assert mapped.change == P.SNAPPED

    def test_the_snap_policy_keeps_a_whole_file_span_whole_without_an_anchor(
            self):
        """`keep_start_anchor` is a parity device: under the snap policy
        position 0 maps to 0 by arithmetic, for every table."""
        text = "Tom said so"
        mapping = P.validate_mapping(
            [{"original": "Tom", "pseudonym": "Pseudo"}])
        compiled = P.Compiled(mapping)
        replacements = P.find_replacements(compiled, text)
        mapper = P.SpanMapper(replacements, len(text))
        for anchor in (True, False):
            mapped = mapper.map_row(0, len(text), "snap_to_pseudonym", anchor)
            assert (mapped.pos0, mapped.pos1) == (0, mapper.new_len)


class TestChangeClassification:
    """`snapped`, `substituted`, `resized`, `shifted`, `clamped`,
    `unchanged`: one vocabulary.

    The distinction matters beyond presentation: the hidden-coder rule
    (D1 3.7, owner ruling X1, refined by ruling 7.3(3) of 2026-09-15 and
    by ruling 7.4 of 2026-09-16) exempts a PURE SHIFT, a PURE
    SUBSTITUTION and a RESIZE that only follows the pseudonym's own
    length, and requires the override for a snap, a deletion and a
    clamp, so a row put in the wrong class is a row whose coder's
    consent was decided wrongly.
    """

    _LENGTHS = {"shorter": "Alex", "equal": "Alexis", "longer": "Alexander"}

    def _thomas(self, text, pseudonym):
        compiled = P.Compiled(P.validate_mapping(
            [{"original": "Thomas", "pseudonym": pseudonym}]))
        replacements = P.find_replacements(compiled, text)
        return (P.SpanMapper(replacements, len(text)),
                P.apply_replacements(text, replacements))

    @pytest.mark.parametrize("pseudonym", list(_LENGTHS.values()),
                             ids=list(_LENGTHS))
    def test_a_span_exactly_on_a_name_is_substituted_whatever_the_lengths(
            self, pseudonym):
        """Ruling 7.3(3): "Thomas" to "Alex", "Alexis" or "Alexander" is
        one coding decision, unchanged. Before the ruling the shorter and
        the longer read as a resize and the equal one as a shift."""
        mapper, new_text = self._thomas("say Thomas now", pseudonym)
        mapped = mapper.map_row(4, 10, "snap_to_pseudonym", False)
        assert mapped.change == P.SUBSTITUTED
        assert mapped.touched is True
        assert new_text[mapped.pos0:mapped.pos1] == pseudonym

    def test_a_later_occurrence_is_substituted_too(self):
        """The second name moved by the first's delta and is still a
        substitution: both boundaries land on the pseudonym's."""
        mapper, new_text = self._thomas("Thomas met Thomas.", "Alex")
        mapped = mapper.map_row(11, 17, "snap_to_pseudonym", False)
        assert mapped.change == P.SUBSTITUTED
        assert (mapped.pos0, mapped.pos1) == (9, 13)
        assert new_text[9:13] == "Alex"

    @pytest.mark.parametrize("span,reads", [
        ((4, 15), "Thomas said"), ((1, 10), "ay Thomas"), ((4, 12), "Thomas s"),
    ], ids=["runs past", "starts before", "runs two past"])
    def test_a_span_that_contains_the_name_without_being_it_is_a_resize(
            self, span, reads):
        """"Thomas said" contained "Thomas" and shrinks by two characters:
        a resize of a longer span, neither a shift nor a substitution.
        Ruling 7.4 exempts it from the override; the class is what says
        which rule applies, and it is unchanged."""
        text = "say Thomas said so"
        assert text[span[0]:span[1]] == reads
        mapper, _ = self._thomas(text, "Alex")
        mapped = mapper.map_row(span[0], span[1], "snap_to_pseudonym", False)
        assert mapped.change == P.RESIZED

    def test_a_clamped_span_that_lands_exactly_on_a_name_is_clamped(self):
        """Ruling 7.4 gives the clamp its own class (fix round 1, B6).
        The stored row (4, 40) on "say Thomas" is a 36-character span
        the coder never confined to the name, so its truncation is
        neither a substitution nor an ordinary resize, and `clamped` is
        what keeps it on the override side now that a resize is exempt;
        the same span stored as the name is the substitution."""
        mapper, new_text = self._thomas("say Thomas", "Alex")
        mapped = mapper.map_row(4, 40, "snap_to_pseudonym", False)
        assert mapped.clamped is True
        assert mapped.change == P.CLAMPED
        assert new_text[mapped.pos0:mapped.pos1] == "Alex"
        unclamped = mapper.map_row(4, 10, "snap_to_pseudonym", False)
        assert unclamped.clamped is False
        assert unclamped.change == P.SUBSTITUTED

    def test_a_clamped_span_that_would_be_an_ordinary_resize_is_clamped(self):
        """The row the ruling turns on. (0, 40) on "say Thomas" contains
        the name and would change length with it, which ruling 7.4
        exempts; its end also lay past the text, which ruling 7.4 does
        not exempt. The clamp wins, so the row stays gated."""
        mapper, _ = self._thomas("say Thomas", "Alex")
        mapped = mapper.map_row(0, 40, "snap_to_pseudonym", False)
        assert mapped.clamped is True
        assert mapped.change == P.CLAMPED
        unclamped = mapper.map_row(0, 10, "snap_to_pseudonym", False)
        assert unclamped.clamped is False
        assert unclamped.change == P.RESIZED

    def test_a_clamped_span_that_was_snapped_stays_snapped(self):
        """A snap is on the override side already, so the clamp does not
        take its class away: (5, 40) on "say Thomas" clamps to (5, 10),
        which cuts into the name and snaps out to hold the pseudonym."""
        mapper, new_text = self._thomas("say Thomas", "Alex")
        mapped = mapper.map_row(5, 40, "snap_to_pseudonym", False)
        assert mapped.clamped is True
        assert mapped.change == P.SNAPPED
        assert new_text[mapped.pos0:mapped.pos1] == "Alex"

    def test_under_parity_a_clamped_span_on_a_name_is_still_deleted(self):
        """The other class the clamp leaves alone, for the same reason:
        a deletion is gated whatever else happened to the row."""
        mapper, _ = self._thomas("say Thomas", "Alex")
        mapped = mapper.map_row(4, 40, "qualcoder_edit_parity", False)
        assert mapped.clamped is True
        assert mapped.change == P.DELETED

    def test_under_parity_a_span_exactly_on_a_name_is_deleted_not_substituted(
            self):
        """The edit-parity policy deletes the row before the class is
        decided, and a deletion is on the override side whatever the
        lengths (7.3(3), point (b))."""
        for pseudonym in self._LENGTHS.values():
            mapper, _ = self._thomas("say Thomas now", pseudonym)
            mapped = mapper.map_row(4, 10, "qualcoder_edit_parity", False)
            assert mapped.change == P.DELETED, pseudonym

    def _mapper(self, text, pseudonym):
        compiled = P.Compiled(P.validate_mapping(
            [{"original": "Tom", "pseudonym": pseudonym}]))
        replacements = P.find_replacements(compiled, text)
        return P.SpanMapper(replacements, len(text))

    @pytest.mark.parametrize("policy", P.OVERLAP_POLICIES)
    def test_a_span_containing_a_same_length_pseudonym_is_a_pure_shift(
            self, policy):
        """"Tom" to "Pat" is three characters for three: the coder still
        marks the same passage, so this is exempt under X1."""
        mapper = self._mapper("say Tom now ok", "Pat")
        mapped = mapper.map_row(0, 11, policy, False)
        assert mapped.change == P.UNCHANGED
        assert mapped.touched is True         # the seltext still changes

    @pytest.mark.parametrize("policy", P.OVERLAP_POLICIES)
    def test_a_span_strictly_containing_a_name_is_resized_not_snapped(
            self, policy):
        """Master's tail-cut BRANCH fires here although nothing is cut:
        the span shrinks by the name and grows by the pseudonym. Calling
        it a snap would demand the hidden-coder override for a row whose
        boundaries never met a name."""
        mapper = self._mapper("say Tom now", "Pseudo")
        mapped = mapper.map_row(0, 11, policy, False)
        assert mapped.change == P.RESIZED

    def test_a_whole_file_span_is_not_snapped_by_the_anchor_undoing_a_cut(
            self):
        """Under parity a span at offset 0 takes a head cut that the
        start anchor immediately undoes in the same step. The start
        never moves, so it is not a boundary the run had to move."""
        mapper = self._mapper("Tom said so", "Pseudo")
        anchored = mapper.map_row(0, 11, "qualcoder_edit_parity", True)
        assert anchored.pos0 == 0
        assert anchored.change == P.RESIZED
        loose = mapper.map_row(0, 11, "qualcoder_edit_parity", False)
        assert loose.change == P.SNAPPED

    @pytest.mark.parametrize("policy", P.OVERLAP_POLICIES)
    def test_a_span_after_every_edit_is_shifted(self, policy):
        mapper = self._mapper("say Tom now", "Pseudo")
        mapped = mapper.map_row(8, 11, policy, False)
        assert mapped.change == P.SHIFTED
        assert mapped.touched is False

    @pytest.mark.parametrize("policy", P.OVERLAP_POLICIES)
    def test_a_span_before_every_edit_is_unchanged_and_untouched(
            self, policy):
        mapper = self._mapper("say Tom now", "Pseudo")
        mapped = mapper.map_row(0, 3, policy, False)
        assert mapped.change == P.UNCHANGED
        assert mapped.touched is False

    def test_a_cut_boundary_is_snapped_under_both_policies(self):
        mapper = self._mapper("say Tom now", "Pseudo")
        for policy in P.OVERLAP_POLICIES:
            assert mapper.map_row(5, 9, policy, False).change == P.SNAPPED

    def test_touched_is_what_decides_a_seltext_refresh(self):
        """A row whose positions do not move can still need its stored
        quote rewritten, which is why `touched` exists separately."""
        mapper = self._mapper("say Tom now ok", "Pat")
        inside = mapper.map_row(0, 11, "snap_to_pseudonym", False)
        outside = mapper.map_row(0, 3, "snap_to_pseudonym", False)
        assert (inside.change, inside.touched) == (P.UNCHANGED, True)
        assert (outside.change, outside.touched) == (P.UNCHANGED, False)


class TestNonChaining:
    """A pseudonym is never matched again, and the rule that guarantees it.

    D1 is internally inconsistent here and this class resolves it in
    favour of the rule. D1 3.2 refuses any mapping whose pseudonym is
    also an original or a variant; D1 6.2's first non-chaining example
    is `[Sam -> Alex, Bob -> Sam]`, which that rule refuses. D1 6.2's own
    idempotence property then says the rules enforce that no pseudonym
    equals an original, so 3.2 is the design and the 6.2 example is the
    slip. The reasoning below is why the rule is the right half to keep.
    """

    def test_a_pseudonym_that_is_also_a_name_is_refused(self):
        with pytest.raises(P.MappingError) as excinfo:
            P.validate_mapping([
                {"original": "Sam", "pseudonym": "Alex"},
                {"original": "Bob", "pseudonym": "Sam"},
            ])
        assert "chain" in str(excinfo.value)

    def test_why_that_mapping_is_refused_although_one_pass_survives_it(self):
        """Two reasons, both shown rather than asserted.

        Upstream's answer to `[Sam -> Alex, Bob -> Sam]` depends on the
        order of the list: in this order nothing chains, and in the other
        every Bob becomes Alex. And a SECOND run of the accepted mapping
        over its own output would turn the ex-Bobs into Alex as well,
        merging two identities without saying so. Refusing costs the
        researcher one rename; accepting costs a silent merge.
        """
        forwards, backwards = "Sam and Bob", "Sam and Bob"
        for original, pseudonym in (("Sam", "Alex"), ("Bob", "Sam")):
            forwards = re.sub(rf"(?<!\w){re.escape(original)}(?!\w)",
                              pseudonym, forwards)
        for original, pseudonym in (("Bob", "Sam"), ("Sam", "Alex")):
            backwards = re.sub(rf"(?<!\w){re.escape(original)}(?!\w)",
                               pseudonym, backwards)
        assert forwards == "Alex and Sam"
        assert backwards == "Alex and Alex"

    def test_the_ordering_upstream_gets_wrong_is_refused_outright(self):
        for entries in ([{"original": "Sam", "pseudonym": "Alex"},
                         {"original": "Alex", "pseudonym": "Pat"}],
                        [{"original": "Alex", "pseudonym": "Pat"},
                         {"original": "Sam", "pseudonym": "Alex"}]):
            with pytest.raises(P.MappingError) as excinfo:
                P.validate_mapping(entries)
            assert "chain" in str(excinfo.value)

    def test_what_upstream_would_have_done_with_that_mapping(self):
        """The reason the refusal exists, shown rather than asserted."""
        chained = "Sam and Alex"
        for original, pseudonym in (("Sam", "Alex"), ("Alex", "Pat")):
            chained = re.sub(rf"(?<!\w){re.escape(original)}(?!\w)",
                             pseudonym, chained)
        assert chained == "Pat and Pat"

    @settings(max_examples=150, deadline=None,
              suppress_health_check=[HealthCheck.too_slow])
    @given(text=_NAMED_TEXT)
    def test_the_order_of_the_entries_never_changes_the_result(self, text):
        """What survives of "no chaining" once the rule is in force: with
        no pseudonym able to be a name, the answer is a function of the
        SET of entries, never of their order."""
        entries = [{"original": "Tom", "pseudonym": "Pseudo"},
                   {"original": "Ann", "pseudonym": "Quu"},
                   {"original": "ab", "pseudonym": "Zed"}]
        first = P.apply_replacements(text, P.find_replacements(
            P.Compiled(P.validate_mapping(entries)), text))
        second = P.apply_replacements(text, P.find_replacements(
            P.Compiled(P.validate_mapping(list(reversed(entries)))), text))
        assert first == second


class TestLongestFirst:

    @pytest.mark.parametrize("names,text,expected", [
        ((["Mary Ann", "Mary"]), "Mary Ann went", "Sam went"),
        ((["Mary", "Mary Ann"]), "Mary Ann went", "Sam went"),
        ((["Jean-Paul", "Jean"]), "Jean-Paul went", "Sam went"),
    ])
    def test_the_longest_form_wins_whatever_order_it_is_given_in(
            self, names, text, expected):
        entries = []
        for index, name in enumerate(names):
            entries.append({"original": name,
                            "pseudonym": "Sam" if len(name) == max(
                                len(n) for n in names) else "Pat"})
        compiled = P.Compiled(P.validate_mapping(entries))
        assert P.apply_replacements(
            text, P.find_replacements(compiled, text)) == expected

    def test_the_shorter_form_still_fires_where_the_longer_cannot(self):
        compiled = P.Compiled(P.validate_mapping([
            {"original": "Mary Ann", "pseudonym": "Sam"},
            {"original": "Mary", "pseudonym": "Pat"},
        ]))
        text = "Mary Anne went"
        assert P.apply_replacements(
            text, P.find_replacements(compiled, text)) == "Pat Anne went"


class TestCaseModes:

    @pytest.mark.parametrize("mode,text,expected", [
        ("exact", "TOM tom Tom tOm", "TOM tom Alex tOm"),
        ("insensitive", "TOM tom Tom tOm", "Alex Alex Alex Alex"),
        ("insensitive_preserve", "TOM tom Tom tOm",
         "ALEX alex Alex Alex"),
    ])
    def test_the_three_modes(self, mode, text, expected):
        compiled = P.Compiled(P.validate_mapping(
            [{"original": "Tom", "pseudonym": "Alex"}], case_mode=mode))
        assert P.apply_replacements(
            text, P.find_replacements(compiled, text)) == expected

    def test_insensitive_preserve_is_named_as_the_heuristic_it_is(self):
        assert "heuristic" in P.Compiled.replacement_for.__doc__.lower()

    def test_a_multi_word_pseudonym_follows_the_same_rule_as_a_whole(self):
        compiled = P.Compiled(P.validate_mapping(
            [{"original": "Tom", "pseudonym": "Alex Brown"}],
            case_mode="insensitive_preserve"))
        text = "TOM and tom"
        assert P.apply_replacements(
            text, P.find_replacements(compiled, text)) == \
            "ALEX BROWN and alex brown"

    def test_case_variants_are_only_reported_under_exact(self):
        exact = P.Compiled(P.validate_mapping(
            [{"original": "Tom", "pseudonym": "Alex"}], case_mode="exact"))
        assert P.case_variants_seen(exact, "TOM TOM Tom tom") == [
            {"entry": 0, "form": "Tom", "other_case_count": 3}]
        loose = P.Compiled(P.validate_mapping(
            [{"original": "Tom", "pseudonym": "Alex"}],
            case_mode="insensitive"))
        assert P.case_variants_seen(loose, "TOM TOM Tom tom") == []

    def test_a_spelling_another_entry_replaces_is_not_a_case_variant(self):
        """With both "Tom" and "TOM" in the mapping, neither occurrence is
        unreplaced, so neither is reported."""
        compiled = P.Compiled(P.validate_mapping([
            {"original": "Tom", "pseudonym": "Alex"},
            {"original": "TOM", "pseudonym": "Pat"},
        ], case_mode="exact"))
        assert P.case_variants_seen(compiled, "TOM Tom tom") == [
            {"entry": 0, "form": "Tom", "other_case_count": 1},
            {"entry": 1, "form": "TOM", "other_case_count": 1}]


class TestDiagnostics:

    def test_a_pseudonym_already_in_the_text_is_reported_not_refused(self):
        """Owner ruling Q3: a warning, and the run proceeds."""
        compiled = P.Compiled(P.validate_mapping([
            {"original": "Thomas", "pseudonym": "Alex"},
            {"original": "Mary", "pseudonym": "Sam"},
        ]))
        found = P.pre_existing_pseudonym_occurrences(
            compiled, "Thomas met Sam and Mary")
        assert found == [{"entry": 1, "pseudonym": "Sam", "count": 1,
                          "spans": [[11, 14]]}]

    def test_a_shared_pseudonym_reports_for_every_entry_that_uses_it(self):
        compiled = P.Compiled(P.validate_mapping([
            {"original": "Thomas", "pseudonym": "Alex"},
            {"original": "Mary", "pseudonym": "Alex"},
        ]))
        found = P.pre_existing_pseudonym_occurrences(compiled, "Alex was here")
        assert [item["entry"] for item in found] == [0, 1]

    def test_overlap_conflicts_name_the_form_that_lost(self):
        compiled = P.Compiled(P.validate_mapping([
            {"original": "Ann Marie", "pseudonym": "Sam"},
            {"original": "Marie Curie", "pseudonym": "Pat"},
        ]))
        text = "Ann Marie Curie spoke"
        replacements = P.find_replacements(compiled, text)
        conflicts, truncated = P.overlap_conflicts(compiled, text,
                                                   replacements)
        assert not truncated
        assert conflicts == [{"entry": 1, "form": "Marie Curie",
                              "span": [4, 15], "loses_to_entry": 0,
                              "chosen_span": [0, 9]}]

    def test_no_conflict_is_reported_when_nothing_competes(self):
        compiled = P.Compiled(P.validate_mapping([
            {"original": "Tom", "pseudonym": "Sam"},
            {"original": "Ann", "pseudonym": "Pat"},
        ]))
        text = "Tom and Ann"
        assert P.overlap_conflicts(
            compiled, text, P.find_replacements(compiled, text)) == ([], False)

    def test_a_form_that_is_not_a_whole_word_is_not_a_conflict(self):
        """The competing form has to be a WORD where it sits, or the
        diagnostic invents clashes that could never have happened: "Mar"
        inside "Marie" and "nn" inside "Ann" lost nothing, because
        neither would ever have matched there.
        """
        compiled = P.Compiled(P.validate_mapping([
            {"original": "Ann Marie", "pseudonym": "Sam"},
            {"original": "Mar", "pseudonym": "Pat"},      # trailing boundary
            {"original": "nn", "pseudonym": "Quu"},       # leading boundary
        ]))
        text = "Ann Marie spoke"
        replacements = P.find_replacements(compiled, text)
        assert [(r.start, r.end) for r in replacements] == [(0, 9)]
        assert P.overlap_conflicts(compiled, text, replacements) == ([], False)

    def test_a_variant_of_the_same_entry_is_not_a_conflict(self):
        compiled = P.Compiled(P.validate_mapping(
            [{"original": "Ann Marie", "pseudonym": "Sam",
              "variants": ["Marie"]}]))
        text = "Ann Marie spoke"
        conflicts, _ = P.overlap_conflicts(
            compiled, text, P.find_replacements(compiled, text))
        assert conflicts == []

    def test_the_conflict_scan_is_bounded_and_says_when_it_truncated(self):
        compiled = P.Compiled(P.validate_mapping([
            {"original": "Ann Marie", "pseudonym": "Sam"},
            {"original": "Marie Curie", "pseudonym": "Pat"},
        ]))
        text = "Ann Marie Curie spoke. " * (P.MAX_OVERLAP_CONFLICTS + 5)
        conflicts, truncated = P.overlap_conflicts(
            compiled, text, P.find_replacements(compiled, text))
        assert truncated is True
        assert len(conflicts) == P.MAX_OVERLAP_CONFLICTS


# =============================================================================
# MAPPING VALIDATION (D1 3.2, texts of D1 3.11)
# =============================================================================

class TestMappingValidation:

    @pytest.mark.parametrize("raw,fragment", [
        ([], "mapping is empty"),
        (None, "mapping is empty"),
        ([{"original": "T", "pseudonym": "Alex"}],
         "original must be at least 2 characters"),
        ([{"original": "Tom", "pseudonym": "Al"}],
         "pseudonym must be at least 3 characters"),
        ([{"original": "Tom", "pseudonym": "A" * 201}],
         "at most 200 characters"),
        ([{"original": "Tom"}], "must have both original and pseudonym"),
        ([{"original": "Tom", "pseudonym": "Alex", "extra": 1}],
         "unknown key"),
        (["Tom"], "must be an object"),
        ("Tom", "must be a list"),
        ([{"original": "Tom", "pseudonym": "Alex"},
          {"original": "Tom", "pseudonym": "Pat"}], "appears twice"),
        ([{"original": "Tom", "pseudonym": "Alex"},
          {"original": "Pat", "pseudonym": "Tom"}], "chain"),
        ([{"original": " Tom", "pseudonym": "Alex"}], "whitespace"),
        ([{"original": "Tom", "pseudonym": "A#####B"}],
         "five or more"),
        ([{"original": "Tom", "pseudonym": "Al\U0001f600ex"}],
         "beyond U+FFFF"),
        # A carriage return is a C0 control, so the general character
        # rule answers first; the U+FFFF clause is what catches a `\r`
        # that somehow got past it and is pinned on its own below.
        ([{"original": "Tom", "pseudonym": "Al\rex"}], "control characters"),
        ([{"original": "Tom", "pseudonym": "Alex", "variants": "Tommy"}],
         "variants must be a list"),
        ([{"original": "Tom", "pseudonym": "Alex", "variants": ["T"]}],
         "variant must be at least 2 characters"),
        ([{"original": "Tom", "pseudonym": 5}], "must be a string"),
    ], ids=lambda v: str(v)[:50])
    def test_the_refusals(self, raw, fragment):
        with pytest.raises(P.MappingError) as excinfo:
            P.validate_mapping(raw)
        assert fragment in str(excinfo.value)

    def test_a_line_break_is_refused_in_every_position(self):
        for bad in ["Tom\nx", "Tom\rx", "Tom x", "Tom x",
                    "Tomx"]:
            with pytest.raises(P.MappingError):
                P.validate_mapping([{"original": bad, "pseudonym": "Alex"}])

    def test_bidirectional_formatting_characters_are_refused(self):
        """A pseudonym is written INTO the researcher's text; an override
        would reorder every line after it on screen while the stored
        offsets stayed where they are."""
        for bad in ["‮Alex", "Al‭ex", "⁦Alex", "Al‏ex",
                    "Al‎ex", "؜Alex"]:
            with pytest.raises(P.MappingError):
                P.validate_mapping([{"original": "Tom", "pseudonym": bad}])

    def test_the_refused_set_is_unicodes_own_bidi_control_property(self):
        """Fix round 1, QA F-17. The list enumerated four of the five
        ranges and U+061C was accepted. An enumeration that is one short
        of a Unicode property is the kind of thing a reader believes, so
        the set is now the property."""
        import unicodedata
        bidi_controls = [chr(cp) for cp in
                         list(range(0x202A, 0x202F)) +
                         list(range(0x2066, 0x206A)) +
                         [0x061C, 0x200E, 0x200F]]
        for char in bidi_controls:
            with pytest.raises(P.MappingError):
                P.validate_mapping([{"original": "Tom",
                                     "pseudonym": f"Al{char}ex"}])

    def test_the_zero_width_joiners_are_not_refused(self):
        """They spell ordinary words in several scripts, so refusing them
        would refuse real names."""
        mapping = P.validate_mapping(
            [{"original": "Tom", "pseudonym": "Al‍ex"}])
        assert mapping.entries[0].pseudonym == "Al‍ex"

    def test_an_astral_original_is_allowed_but_an_astral_pseudonym_is_not(
            self):
        """The asymmetry is the point: an original is whatever the file
        already holds; a pseudonym is what we write, and an astral code
        point there would break Qt's offsets from that character on."""
        P.validate_mapping(
            [{"original": "T\U0001f600m", "pseudonym": "Alex"}])
        with pytest.raises(P.MappingError):
            P.validate_mapping(
                [{"original": "Tom", "pseudonym": "A\U0001f600lex"}])

    def test_duplicate_pseudonyms_are_allowed_and_reported(self):
        """Two people deliberately merged into one identity."""
        mapping = P.validate_mapping([
            {"original": "Tom", "pseudonym": "Alex"},
            {"original": "Ann", "pseudonym": "Alex"},
        ])
        assert mapping.shared_pseudonyms == (
            {"pseudonym": "Alex", "entries": [0, 1]},)

    def test_case_folded_duplicates_are_refused_only_when_case_is_ignored(
            self):
        entries = [{"original": "Tom", "pseudonym": "Alex"},
                   {"original": "TOM", "pseudonym": "Pat"}]
        P.validate_mapping(entries, case_mode="exact")
        for mode in ("insensitive", "insensitive_preserve"):
            with pytest.raises(P.MappingError):
                P.validate_mapping(entries, case_mode=mode)

    def test_a_pseudonym_that_only_case_folds_onto_a_name_chains_too(self):
        entries = [{"original": "Tom", "pseudonym": "Alex"},
                   {"original": "Pat", "pseudonym": "TOM"}]
        P.validate_mapping(entries, case_mode="exact")
        with pytest.raises(P.MappingError) as excinfo:
            P.validate_mapping(entries, case_mode="insensitive")
        assert "chain" in str(excinfo.value)

    def test_a_variant_duplicated_inside_one_entry_is_refused(self):
        with pytest.raises(P.MappingError) as excinfo:
            P.validate_mapping([{"original": "Tom", "pseudonym": "Alex",
                                 "variants": ["Tom"]}])
        assert str(excinfo.value) == (
            "mapping entry 0: the same original (or variant) appears twice.")

    def test_a_duplicate_across_two_entries_names_both_of_them(self):
        """D1 3.11's text, to the character. Asserting the fragment alone
        let the cross-entry branch be removed with the test still green,
        because the within-entry branch then answered with a message that
        contained the same fragment and named one entry instead of two.
        The researcher needs to be told which pair collided."""
        for raw in ([{"original": "Tom", "pseudonym": "Alex"},
                     {"original": "Tom", "pseudonym": "Pat"}],
                    [{"original": "Tom", "pseudonym": "Alex"},
                     {"original": "Ann", "pseudonym": "Pat",
                      "variants": ["Tom"]}]):
            with pytest.raises(P.MappingError) as excinfo:
                P.validate_mapping(raw)
            assert str(excinfo.value) == (
                "mapping entries 0 and 1: the same original (or variant) "
                "appears twice.")

    def test_the_entry_cap_is_five_hundred(self):
        """The value itself, not the constant read back.

        Fix round 1, QA F-20. This test used to build its fixture from
        `P.MAX_ENTRIES` and assert the fragment "at most 500", which is a
        prefix of "at most 5000": lifting the cap tenfold left the whole
        suite green. The cap is a documented limit (D1 3.2), so the
        number is written out here and the refusal is asserted whole.
        """
        assert P.MAX_ENTRIES == 500
        biggest = [{"original": f"Name{i:04d}", "pseudonym": f"Pseu{i:04d}"}
                   for i in range(500)]
        assert len(P.validate_mapping(biggest)) == 500
        with pytest.raises(P.MappingError) as excinfo:
            P.validate_mapping(biggest + [{"original": "Extra",
                                           "pseudonym": "More"}])
        assert str(excinfo.value) == (
            "mapping has 501 entries; at most 500 are accepted.")

    def test_a_pseudonym_of_exactly_the_cap_is_accepted(self):
        assert P.MAX_PSEUDONYM_CHARS == 200
        P.validate_mapping([{"original": "Tom", "pseudonym": "A" * 200}])
        with pytest.raises(P.MappingError) as excinfo:
            P.validate_mapping([{"original": "Tom", "pseudonym": "A" * 201}])
        assert str(excinfo.value) == (
            "mapping entry 0: pseudonym must be at most 200 characters.")

    @pytest.mark.parametrize("label", ["original", "variant"])
    def test_a_surface_form_is_capped_at_two_hundred_characters(self, label):
        """Security S4. Nothing capped the length of a name, and
        `max_form_len` is the width of the window the overlap diagnostic
        scans around every match, so a read-only preview with no token
        and no approval could be made to burn 91 seconds."""
        assert P.MAX_FORM_CHARS == 200
        entry = {"original": "Tom", "pseudonym": "Alex"}
        entry[label if label == "original" else "variants"] = (
            "Q" * 200 if label == "original" else ["Q" * 200])
        P.validate_mapping([dict(entry)])
        entry[label if label == "original" else "variants"] = (
            "Q" * 201 if label == "original" else ["Q" * 201])
        with pytest.raises(P.MappingError) as excinfo:
            P.validate_mapping([entry])
        assert str(excinfo.value) == (
            f"mapping entry 0: {label} must be at most 200 characters.")

    def test_the_variants_cap_is_fifty_per_entry(self):
        assert P.MAX_VARIANTS_PER_ENTRY == 50
        base = {"original": "Tom", "pseudonym": "Alex"}
        P.validate_mapping([dict(base, variants=[f"V{i:03d}"
                                                 for i in range(50)])])
        with pytest.raises(P.MappingError) as excinfo:
            P.validate_mapping([dict(base, variants=[f"V{i:03d}"
                                                     for i in range(51)])])
        assert str(excinfo.value) == (
            "mapping entry 0: has 51 variants; at most 50 are accepted "
            "per entry.")

    def test_the_total_surface_form_cap_is_two_thousand(self):
        """The cap that bounds the compiled alternation itself: 500
        entries times 50 variants would be 25,500 forms without it."""
        assert P.MAX_TOTAL_FORMS == 2000
        def mapping(per_entry):
            return [{"original": f"Name{i:04d}", "pseudonym": f"Pseu{i:04d}",
                     "variants": [f"V{i:04d}x{j}" for j in range(per_entry)]}
                    for i in range(500)]
        P.validate_mapping(mapping(3))          # 500 x 4 = 2000
        with pytest.raises(P.MappingError) as excinfo:
            P.validate_mapping(mapping(4))      # 500 x 5 = 2500
        assert str(excinfo.value) == (
            "mapping has 2500 surface forms (originals plus variants); at "
            "most 2000 are accepted.")

    def test_every_refusal_text_follows_the_house_rules(self):
        texts = []
        for raw in ([], [{"original": "T", "pseudonym": "Alex"}],
                    [{"original": "Tom", "pseudonym": "Al"}],
                    [{"original": "Tom", "pseudonym": "Al\U0001f600ex"}],
                    [{"original": "Tom", "pseudonym": "Alex"},
                     {"original": "Pat", "pseudonym": "Tom"}],
                    [{"original": "Tom\nx", "pseudonym": "Alex"}]):
            with pytest.raises(P.MappingError) as excinfo:
                P.validate_mapping(raw)
            texts.append(str(excinfo.value))
        _house_rules(texts)


class TestCanonicalMapping:

    def test_order_does_not_change_the_canonical_form(self):
        a = P.canonical_mapping(P.validate_mapping([
            {"original": "Tom", "pseudonym": "Alex", "variants": ["T2", "T1"]},
            {"original": "Ann", "pseudonym": "Pat"}]))
        b = P.canonical_mapping(P.validate_mapping([
            {"original": "Ann", "pseudonym": "Pat"},
            {"original": "Tom", "pseudonym": "Alex", "variants": ["T1", "T2"]}]))
        assert a == b
        assert a[1]["variants"] == ["T1", "T2"]

    def test_canonical_positions_follow_the_canonical_order(self):
        """Fix round 3, S3: the signed effect keys entries on these
        positions, so the same mapping in another order signs the same
        effect. Caller index 0 is "Zed" here and sorts last."""
        mapping = P.validate_mapping([
            {"original": "Zed", "pseudonym": "Pat"},
            {"original": "Ann", "pseudonym": "Sam", "variants": ["Annie"]},
            {"original": "Mary", "pseudonym": "Kim"},
        ])
        assert P.canonical_entry_positions(mapping) == {0: 2, 1: 0, 2: 1}
        reordered = P.validate_mapping([
            {"original": "Mary", "pseudonym": "Kim"},
            {"original": "Zed", "pseudonym": "Pat"},
            {"original": "Ann", "pseudonym": "Sam", "variants": ["Annie"]},
        ])
        assert P.canonical_entry_positions(reordered) == {0: 1, 1: 2, 2: 0}
        assert P.canonical_mapping(mapping) == P.canonical_mapping(reordered)

    def test_the_canonical_form_is_nfc(self):
        composed = "José"
        decomposed = "José"
        assert composed != decomposed
        a = P.canonical_mapping(P.validate_mapping(
            [{"original": composed, "pseudonym": "Alex"}]))
        b = P.canonical_mapping(P.validate_mapping(
            [{"original": decomposed, "pseudonym": "Alex"}]))
        assert a == b

    def test_matching_is_not_normalised_even_though_binding_is(self):
        """The asymmetry, pinned so nobody "fixes" it into a silent miss:
        normalising what we LOOK FOR could stop matching a file stored in
        the other normal form, so the engine matches the string exactly
        as supplied. The consequence is that two mappings can bind the
        same and do different things, which the signed state catches."""
        text = "José spoke"
        composed = P.Compiled(P.validate_mapping(
            [{"original": "José", "pseudonym": "Alex"}]))
        decomposed = P.Compiled(P.validate_mapping(
            [{"original": "José", "pseudonym": "Alex"}]))
        assert len(P.find_replacements(composed, text)) == 1
        assert P.find_replacements(decomposed, text) == []


# =============================================================================
# DAMAGED AND EDGE-CASE ROWS (D1 6.3)
# =============================================================================

class TestDamagedRows:

    def _mapper(self, text="say Tom now"):
        compiled = P.Compiled(P.validate_mapping(
            [{"original": "Tom", "pseudonym": "Pseudo"}]))
        replacements = P.find_replacements(compiled, text)
        return P.SpanMapper(replacements, len(text)), text

    @pytest.mark.parametrize("pos0,pos1", [
        (None, 5), (5, None), (None, None), ("3", 5), (3, "5"),
        (5, 5), (6, 3), (True, 5), (3, False),
    ], ids=repr)
    def test_a_row_that_is_not_a_span_is_left_exactly_as_it_is(
            self, pos0, pos1):
        mapper, _ = self._mapper()
        assert mapper.map_row(pos0, pos1, "snap_to_pseudonym", True) is None

    def test_an_end_past_the_text_is_clamped_on_the_old_side(self):
        mapper, text = self._mapper()
        mapped = mapper.map_row(0, len(text) + 40, "snap_to_pseudonym", True)
        assert mapped.clamped is True
        assert mapped.pos1 == mapper.new_len

    def test_a_row_wholly_past_the_text_becomes_unmappable_after_clamping(
            self):
        mapper, text = self._mapper()
        assert mapper.map_row(len(text) + 5, len(text) + 9,
                              "snap_to_pseudonym", True) is None

    @pytest.mark.parametrize("pos0,pos1", [(-1, 11), (-4, -3), (0, -2)],
                             ids=repr)
    def test_a_negative_position_is_left_exactly_as_it_is(self, pos0, pos1):
        """Fix round 1, QA F-9. A negative stored position is a damaged
        row QualCoder never writes. It used to be mapped, which wrote it
        back negative -- into the range the write parks rows in while it
        resolves a transient UNIQUE collision, where it made the whole
        run fail with "could not rewrite this project's text". Reporting
        it as unmappable puts it in `null_position_rows`, which is where
        a researcher can see it."""
        mapper, _ = self._mapper()
        for policy in P.OVERLAP_POLICIES:
            assert mapper.map_row(pos0, pos1, policy, True) is None

    def test_the_clamp_is_reported_for_every_policy(self):
        mapper, text = self._mapper()
        for policy in P.OVERLAP_POLICIES:
            assert mapper.map_row(0, len(text) + 3, policy, True).clamped


class TestTextEdgeCases:

    def _run(self, text, names=("Tom",), pseudonym="Pseudo"):
        compiled = P.Compiled(P.validate_mapping(
            [{"original": n, "pseudonym": pseudonym} for n in names]))
        replacements = P.find_replacements(compiled, text)
        return replacements, P.apply_replacements(text, replacements)

    def test_a_name_at_offset_zero(self):
        replacements, new = self._run("Tom said")
        assert replacements[0].start == 0
        assert new == "Pseudo said"

    def test_a_name_at_the_end_of_the_text(self):
        replacements, new = self._run("said Tom")
        assert replacements[0].end == 8
        assert new == "said Pseudo"

    def test_a_name_as_the_entire_text(self):
        _, new = self._run("Tom")
        assert new == "Pseudo"

    def test_two_names_adjacent(self):
        compiled = P.Compiled(P.validate_mapping([
            {"original": "Tom", "pseudonym": "Aaa"},
            {"original": "Ann", "pseudonym": "Bbb"}]))
        text = "Tom Ann"
        assert P.apply_replacements(
            text, P.find_replacements(compiled, text)) == "Aaa Bbb"

    def test_names_separated_by_a_single_character(self):
        compiled = P.Compiled(P.validate_mapping([
            {"original": "Tom", "pseudonym": "Aaa"},
            {"original": "Ann", "pseudonym": "Bbb"}]))
        text = "Tom,Ann"
        assert P.apply_replacements(
            text, P.find_replacements(compiled, text)) == "Aaa,Bbb"

    def test_a_name_immediately_followed_by_a_newline(self):
        _, new = self._run("Tom\nsaid")
        assert new == "Pseudo\nsaid"

    def test_nothing_matches_across_a_line_break(self):
        """The space in a multi-word form is literal, so a name split
        over two lines is not a match (upstream parity)."""
        compiled = P.Compiled(P.validate_mapping(
            [{"original": "Mary Ann", "pseudonym": "Sam"}]))
        assert P.find_replacements(compiled, "Mary\nAnn") == []

    def test_an_empty_text_produces_no_edits_and_no_new_text(self):
        replacements, new = self._run("")
        assert replacements == []
        assert new == ""

    def test_a_file_with_no_match_is_returned_byte_for_byte(self):
        text = "nothing to see here"
        replacements, new = self._run(text)
        assert replacements == []
        assert new is text


class TestUniqueConstraintPreCheck:

    def test_two_rows_snapping_onto_one_span_are_reported(self):
        """One coding on "Thomas" and one on "Thom": under the snap
        policy both become the pseudonym, and `code_text` is unique on
        (cid, fid, pos0, pos1, owner)."""
        text = "say Thomas now"
        compiled = P.Compiled(P.validate_mapping(
            [{"original": "Thomas", "pseudonym": "Alex"}]))
        replacements = P.find_replacements(compiled, text)
        mapper = P.SpanMapper(replacements, len(text))
        a = mapper.map_row(4, 10, "snap_to_pseudonym", True)
        b = mapper.map_row(4, 8, "snap_to_pseudonym", True)
        assert (a.pos0, a.pos1) == (b.pos0, b.pos1)
        collisions = P.unique_constraint_collisions(
            [(1, 1, a.pos0, a.pos1, "C"), (1, 1, b.pos0, b.pos1, "C")],
            [11, 12])
        assert collisions == [{"key": [1, 1, 4, 8, "C"], "row_ids": [11, 12]}]

    def test_rows_that_differ_in_any_key_column_do_not_collide(self):
        assert P.unique_constraint_collisions(
            [(1, 1, 0, 4, "C"), (2, 1, 0, 4, "C"), (1, 1, 0, 4, "D")],
            [1, 2, 3]) == []

    def test_the_parity_policy_resolves_the_collision_by_deleting(self):
        text = "say Thomas now"
        compiled = P.Compiled(P.validate_mapping(
            [{"original": "Thomas", "pseudonym": "Alex"}]))
        replacements = P.find_replacements(compiled, text)
        mapper = P.SpanMapper(replacements, len(text))
        for span in ((4, 10), (4, 8)):
            mapped = mapper.map_row(span[0], span[1],
                                    "qualcoder_edit_parity", True)
            assert mapped.change == P.DELETED


# =============================================================================
# ROUND TRIP AND DETERMINISM (D1 6.2)
# =============================================================================

class TestRoundTripOracle:
    """The v0.13 reverse tool's oracle, written now (D1 6.2).

    Reversing over the manifest's SPANS rather than by matching text is
    what makes reversal safe when a pseudonym also occurs naturally, and
    this is the property the later tool has to satisfy.
    """

    # No `assume` and no suppression (fix round 3, S8): the spans come
    # with the text, and the pseudonyms are chosen outside the token
    # vocabulary, so a pre-existing occurrence cannot be generated and
    # the reversal is exact by construction rather than by filtering.
    @settings(max_examples=200, deadline=None,
              suppress_health_check=[HealthCheck.too_slow])
    @given(sample=_TEXT_AND_SPANS)
    def test_reversing_over_the_spans_restores_the_text_and_the_rows(
            self, sample):
        text, spans = sample
        compiled = P.Compiled(P.validate_mapping([
            {"original": "Tom", "pseudonym": "Pseudo"},
            {"original": "Ann", "pseudonym": "Quu"},
        ]))
        replacements = P.find_replacements(compiled, text)
        assert not P.pre_existing_pseudonym_occurrences(compiled, text)
        new_text = P.apply_replacements(text, replacements)
        mapper = P.SpanMapper(replacements, len(text))
        forward = [mapper.map_row(a, b, "snap_to_pseudonym", True)
                   for a, b in spans]

        # The inverse edit list, exactly what a manifest records: each
        # new span and the text it replaced.
        inverse = []
        offset = 0
        for item in replacements:
            start = item.start + offset
            inverse.append(P.Replacement(start, start + len(item.text),
                                         item.entry, item.text, item.matched))
            offset += item.delta
        assert P.apply_replacements(new_text, inverse) == text

        back = P.SpanMapper(inverse, len(new_text))
        for (a, b), mapped in zip(spans, forward):
            if mapped.change == P.SNAPPED:
                continue          # a cut boundary is information lost
            returned = back.map_row(mapped.pos0, mapped.pos1,
                                    "snap_to_pseudonym", True)
            assert (returned.pos0, returned.pos1) == (a, b)


class TestTheGeneratorsExerciseWhatTheyClaim:
    """The measurement behind the oracles (fix round 3, S8).

    A property test proves nothing about a branch its inputs never
    reach. These count, over the REAL hypothesis distribution
    (`derandomize=True`, so the figure is the same on every run and
    every platform, and generation only, so a failure is a count and
    not a shrunk example), how often each strategy produces the thing
    the test built on it is about. The thresholds sit well below what
    was measured, and far above the 0.2 to 1.7 per cent the old
    alphabet strategy managed.
    """

    TOMANN = P.Compiled(P.validate_mapping([
        {"original": "Tom", "pseudonym": "Pseudo"},
        {"original": "Ann", "pseudonym": "Quux"},
    ]))

    @staticmethod
    def _count(strategy_kwargs, body, examples=500):
        seen = {"examples": 0, "hits": 0}

        @settings(max_examples=examples, deadline=None, database=None,
                  derandomize=True, phases=[Phase.generate],
                  suppress_health_check=[HealthCheck.too_slow])
        @given(**strategy_kwargs)
        def probe(**kwargs):
            seen["examples"] += 1
            if body(**kwargs):
                seen["hits"] += 1

        probe()
        assert seen["examples"] >= examples * 0.9, seen
        return seen["hits"] / seen["examples"]

    def test_the_named_text_holds_a_replacement_in_most_examples(self):
        """Fix round 4, L2: the bar stays at 0.5 and the sample grew.
        Measured at 2,000 examples: 62.3 per cent under the derandomised
        draw, 11.4 standard errors over the bar, and 62.2 to 67.9 per
        cent under live seeds 1 to 5, 11.2 standard errors at the least.
        At 500 examples the derandomised figure was 68.8 per cent but
        one live seed sat 2.7 standard errors over the bar, a red
        waiting for a hypothesis release. A red here now is a change in
        what the generator draws, and the count in the message says by
        how much; it is not a shrunk example."""
        rate = self._count(
            {"text": _NAMED_TEXT},
            lambda text: bool(P.find_replacements(self.TOMANN, text)),
            examples=2000)
        assert rate >= 0.5, rate

    def test_the_spans_touch_a_replacement_in_a_substantial_fraction(self):
        """What the edit-walk oracle needs: a replacement AND a span
        that meets it, because a span that meets none is mapped by a
        shift and the walk's delete and insert branches never run."""
        def touching(sample):
            text, spans = sample
            replacements = P.find_replacements(self.TOMANN, text)
            return any(a < r.end and r.start < b
                       for a, b in spans for r in replacements)
        rate = self._count({"sample": _TEXT_AND_SPANS}, touching)
        assert rate >= 0.3, rate

    def test_every_walk_branch_is_reached(self):
        """The four shapes master's walk distinguishes (inside a name,
        containing one, a head cut, a tail cut) each occur in at least
        five per cent of examples, so a bug in any one branch has
        dozens of chances per run to show."""
        shapes = {"inside": 0, "contains": 0, "head": 0, "tail": 0}

        def classify(sample):
            text, spans = sample
            replacements = P.find_replacements(self.TOMANN, text)
            hit = False
            for a, b in spans:
                for r in replacements:
                    if a >= r.start and b <= r.end:
                        shapes["inside"] += 1
                    elif a < r.start and b > r.end:
                        shapes["contains"] += 1
                    elif r.start <= a < r.end < b:
                        shapes["head"] += 1
                    elif a < r.start < b <= r.end:
                        shapes["tail"] += 1
                    else:
                        continue
                    hit = True
            return hit
        total = 500
        self._count({"sample": _TEXT_AND_SPANS}, classify, examples=total)
        for shape, count in shapes.items():
            assert count >= total * 0.05, (shape, count, shapes)

    def test_the_boundary_sample_holds_a_master_match_often(self):
        rate = self._count(
            {"sample": _BOUNDARY_SAMPLE},
            lambda sample: bool(master_boundary_spans(sample[1], sample[0])))
        assert rate >= 0.4, rate

    @pytest.mark.parametrize("name", ["Tom", "Ann", "ab", "Tom Ann", "Téo",
                                      "中文", "An-n", "ab.c"])
    def test_every_boundary_name_is_matched_and_missed(self, name):
        """Each name in `_NAMES` both matches and narrowly misses (a
        letter beside it) in the samples drawn for it, so the two
        boundary oracles compare our rule with master's on both sides of
        the boundary for every shape of name.

        Fix round 4, L2: the floors stay at 15 and 3 and the sample
        grew. Measured at 3,000 examples under the derandomised draw,
        the fewest matches for any name is 136 (An-n) and the fewest
        misses 43 (Tom), 10.6 and 6.1 standard errors over the floors;
        under live seeds 1 to 5 the least margins are 10.1 and 5.4. At
        1,200 examples the miss floor sat 2.0 standard errors under "ab"
        and 0.9 under one live seed."""
        seen = {"match": 0, "miss": 0}

        def look(sample):
            text, drawn = sample
            if drawn != name:
                return False
            if master_boundary_spans(name, text):
                seen["match"] += 1
            if name in text and not master_boundary_spans(name, text):
                seen["miss"] += 1
            return True
        self._count({"sample": _BOUNDARY_SAMPLE}, look, examples=3000)
        assert seen["match"] >= 15 and seen["miss"] >= 3, (name, seen)

    def test_the_old_alphabet_alone_would_fail_these(self):
        """The measurement that made the round: the strategy the oracles
        used to draw from, counted the same way, so nobody reads the
        thresholds above as generous."""
        rate = self._count(
            {"text": _TEXT},
            lambda text: bool(P.find_replacements(self.TOMANN, text)))
        assert rate < 0.05, rate


class TestIdempotenceAndDeterminism:

    def test_running_the_same_mapping_again_finds_nothing(self):
        """Guaranteed by the validation rule that no pseudonym is a name
        in the mapping, so a rewritten text holds no match."""
        compiled = P.Compiled(P.validate_mapping([
            {"original": "Tom", "pseudonym": "Pseudo"},
            {"original": "Ann", "pseudonym": "Quu"},
        ]))
        text = "Tom met Ann and Tom"
        once = P.apply_replacements(text, P.find_replacements(compiled, text))
        assert P.find_replacements(compiled, once) == []

    @settings(max_examples=120, deadline=None,
              suppress_health_check=[HealthCheck.too_slow])
    @given(text=_NAMED_TEXT)
    def test_the_same_inputs_give_the_same_edits_every_time(self, text):
        entries = [{"original": "Tom", "pseudonym": "Pseudo"},
                   {"original": "Ann", "pseudonym": "Quu"}]
        first = [(r.start, r.end, r.entry, r.text) for r in
                 P.find_replacements(P.Compiled(P.validate_mapping(entries)),
                                     text)]
        second = [(r.start, r.end, r.entry, r.text) for r in
                  P.find_replacements(
                      P.Compiled(P.validate_mapping(list(reversed(entries)))),
                      text)]
        # Reversing the entry order renumbers the entries but must not
        # move a single character.
        assert [(s, e, t) for s, e, _, t in first] == \
               [(s, e, t) for s, e, _, t in second]

    def test_the_compiled_pattern_is_a_function_of_the_mapping_alone(self):
        """Same mapping, same pattern, and the longest form first."""
        entries = [{"original": "Ann", "pseudonym": "Quu"},
                   {"original": "Mary Ann", "pseudonym": "Pseudo"}]
        first = P.Compiled(P.validate_mapping(entries))
        second = P.Compiled(P.validate_mapping(list(reversed(entries))))
        assert first.pattern.pattern == second.pattern.pattern
        assert [form for form, _ in first.forms] == ["Mary Ann", "Ann"]


class TestContext:

    def test_context_is_capped_however_much_is_asked_for(self):
        text = "x" * 1000 + "Tom" + "y" * 1000
        wide = P.context_for(text, 1000, 1003, 10_000)
        assert len(wide) == 3 + 2 * P.MAX_CONTEXT_CHARS

    def test_context_clips_at_the_edges_of_the_text(self):
        assert P.context_for("Tom said", 0, 3, 30) == "Tom said"

    def test_a_negative_request_returns_the_match_alone(self):
        assert P.context_for("say Tom now", 4, 7, -5) == "Tom"


# =============================================================================
# THE DETECTOR (fix round 1: QA F-1, Security S1 and S2)
# =============================================================================

class TestNameDetector:
    """The matcher that answers a different question from the rewriter's.

    One compiled pattern used to serve both jobs, and their correctness
    conditions are opposite. Rewriting must be conservative: whole-word,
    so "Ann" is not replaced inside "Anna" and upstream parity holds.
    Deciding whether a string still CARRIES a name must be liberal,
    because `_` is a word character and `Thomas_interview.txt` is the
    ordinary shape of a transcript file name. The conflation put a real
    name into a journal entry that ships inside the project, and made
    the residue block report zero where five names survived.

    Everything here is about the second matcher. The first is pinned,
    unchanged, by the parity oracles above.
    """

    MAPPING = [{"original": "Thomas", "pseudonym": "Alex",
                "variants": ["Tom"]},
               {"original": "Mary Ann", "pseudonym": "Sam"}]

    def _detector(self, case_mode="exact"):
        return P.Compiled(P.validate_mapping(self.MAPPING,
                                             case_mode)).detector

    SEES_A_NAME = [
        ("a space", "Thomas interview.txt"),
        ("an underscore", "Thomas_interview.txt"),
        ("a hyphen", "Thomas-interview.txt"),
        ("a dot", "Thomas.interview.txt"),
        ("a digit after", "Thomas2.txt"),
        ("a digit before", "2Thomas.txt"),
        ("a letter after", "ThomasB.txt"),
        ("a letter before", "BThomas.txt"),
        ("the bare name", "Thomas"),
        ("at the start", "Thomas_01_transcript.txt"),
        ("in the middle", "int_Thomas_01.txt"),
        ("at the end", "interview_Thomas"),
        ("upper case", "THOMAS_P01"),
        ("lower case", "thomas_p01"),
        ("mixed case", "tHoMaS_p01"),
        ("a variant", "Tom_2.docx"),
        ("a variant in a sentence", "Interviewed Tom_Smith at home."),
        ("a multi-word name", "Mary Ann.txt"),
        ("a multi-word name, underscore", "Mary_Ann.txt"),
        ("a multi-word name, hyphen", "Mary-Ann.txt"),
        ("a multi-word name, run together", "MaryAnn_notes.txt"),
        ("a multi-word name, upper case", "MARYANN"),
        ("a multi-word name, doubled space", "Mary  Ann"),
        ("a multi-word name, comma", "Mary,Ann"),
        ("a case label", "Thomas_P01"),
        ("an attribute value", "Thomas_Smith"),
        ("a memo sentence", "Interviewed Thomas_Smith at home."),
        ("a path", "/Users/r/Documents/Thomas study.qda/data.qda"),
    ]

    @pytest.mark.parametrize("shape,value", SEES_A_NAME,
                             ids=[shape for shape, _ in SEES_A_NAME])
    def test_a_reader_of_this_string_would_see_a_name(self, shape, value):
        assert self._detector().contains(value) is True

    @pytest.mark.parametrize("value", [
        "interview_01.txt", "quiet.txt", "Participant A", "fieldwork.qda",
        "Alex", "Sam", "Alex_01.txt", "notes about the weather",
        "Thom", "Mar", "Ann", "Ann Mary", "Mary and Ann",
    ], ids=repr)
    def test_a_string_with_no_name_in_it(self, value):
        assert self._detector().contains(value) is False

    @pytest.mark.parametrize("value", [None, 5, True, b"Thomas", "", []],
                             ids=repr)
    def test_anything_that_is_not_a_non_empty_string_carries_no_name(
            self, value):
        assert self._detector().contains(value) is False

    @pytest.mark.parametrize("case_mode", P.CASE_MODES)
    def test_the_answer_does_not_depend_on_case_mode(self, case_mode):
        """`case_mode` decides what the run REWRITES. Whether a label
        still names the participant is a question about the label, and
        `THOMAS_P01` names them under every mode."""
        detector = self._detector(case_mode)
        for value in ("THOMAS_P01", "thomas_p01", "Thomas_P01"):
            assert detector.contains(value) is True

    def test_the_rewriter_and_the_detector_answer_differently_on_purpose(
            self):
        """The whole finding in one test. Neither answer is wrong; they
        are answers to different questions, and the rewrite's must not
        move."""
        compiled = P.Compiled(P.validate_mapping(self.MAPPING))
        assert compiled.pattern.search("Thomas_interview.txt") is None
        assert compiled.detector.contains("Thomas_interview.txt") is True
        # The rewrite is untouched: still whole-word, still QualCoder's
        # own rule, so the text of a file is not rewritten inside a
        # longer word.
        assert P.find_replacements(compiled, "Thomas_Smith spoke") == []
        replacements = P.find_replacements(compiled, "Thomas spoke")
        assert [(r.start, r.end, r.text) for r in replacements] == [
            (0, 6, "Alex")]

    def test_a_metacharacter_in_a_name_is_escaped_here_too(self):
        """The detector compiles a second pattern out of caller strings,
        so it inherits the same obligation as the first."""
        detector = P.Compiled(P.validate_mapping(
            [{"original": "a.c", "pseudonym": "Zed"},
             {"original": "x(y", "pseudonym": "Qux"}])).detector
        assert detector.contains("file_a.c_01.txt") is True
        assert detector.contains("file_x(y_01.txt") is True
        assert detector.contains("aXc") is False
        assert detector.contains("abc") is False

    def test_a_form_of_punctuation_alone_is_matched_literally(self):
        """Validation allows it (two characters, no control, no marker),
        and splitting it into words leaves nothing to join."""
        detector = P.Compiled(P.validate_mapping(
            [{"original": "--", "pseudonym": "Zed"}])).detector
        assert detector.contains("a--b") is True
        assert detector.contains("ab") is False

    def test_the_two_readings_together_catch_a_folded_spelling(self):
        """`re.IGNORECASE` and `str.casefold` disagree: U+00DF folds to
        "ss" and does not match "SS" under IGNORECASE. The detector runs
        both, because the wider answer is the safe one."""
        detector = P.Compiled(P.validate_mapping(
            [{"original": "Straße", "pseudonym": "Zed"}])).detector
        assert detector.contains("Straße_01.txt") is True
        assert detector.contains("STRASSE_01.txt") is True

    def test_a_decomposed_spelling_is_the_same_name(self):
        """NFC on both sides: "René" typed as e + combining acute
        renders identically and is the same participant."""
        detector = P.Compiled(P.validate_mapping(
            [{"original": "René", "pseudonym": "Zed"}])).detector
        assert detector.contains("René_interview.txt") is True

    NFD, NFC = "Rene\u0301", "Ren\u00e9"

    @pytest.mark.parametrize("form,value", [
        (NFD, NFC + "_interview.txt"),          # the mapping side is NFD
        (NFC, NFD + "_interview.txt"),          # the value side is NFD
        (NFD, NFD + "_interview.txt"),
        (NFC, NFC + "_interview.txt"),
    ], ids=["nfd-form", "nfd-value", "both-nfd", "both-nfc"])
    def test_either_side_may_be_decomposed(self, form, value):
        """Fix round 3, B2. The FORM side of the normalisation reverted
        green: the shapes above were value-side only, so
        `normalised = list(forms)` passed everything, and at tool level
        an NFD mapping let the NFC spelling of a file name into the
        journal body. One case per side, and the two controls."""
        assert self.NFD != self.NFC
        detector = P.Compiled(P.validate_mapping(
            [{"original": form, "pseudonym": "Zed"}])).detector
        assert detector.contains(value) is True

    # Characters a reader does not see, inside a one-word name (fix
    # round 3, S7). Each renders as "Thomas" and each walked past the
    # detector, so a file called `Tho\u00admas_interview.txt` went into
    # the journal body and the manifest.
    INVISIBLE = [
        ("soft hyphen", "\u00ad"),
        ("zero-width space", "\u200b"),
        ("zero-width non-joiner", "\u200c"),
        ("zero-width joiner", "\u200d"),
        ("left-to-right mark", "\u200e"),
        ("word joiner", "\u2060"),
        ("combining grapheme joiner", "\u034f"),
        ("variation selector 16", "\ufe0f"),
        ("byte order mark", "\ufeff"),
        ("Mongolian free variation selector", "\u180b"),
        # Outside fix round 3's approximation (fix round 4, R2): a mark
        # the hand list stopped short of, and a reserved code point.
        ("Mongolian free variation selector four", "\u180f"),
        ("reserved default-ignorable U+2065", "\u2065"),
        # A Hangul filler (fix round 5, S2): category Lo, so `\w` counts
        # it as a letter and the split leaves it inside the word. On the
        # form side only the per-piece strip in `_detector_parts`
        # removes it, and that strip reverted with the suite green.
        ("Hangul filler", "\u3164"),
    ]

    @pytest.mark.parametrize("label,char", INVISIBLE,
                             ids=[label for label, _ in INVISIBLE])
    def test_an_invisible_character_inside_the_name_is_seen_through(
            self, label, char):
        detector = self._detector()
        assert detector.contains(f"Tho{char}mas_interview.txt") is True
        assert detector.contains(f"Tho{char}mas") is True
        assert detector.contains(f"Mary{char}Ann") is True

    # The same list less the two characters a mapping string may not
    # contain at all: the left-to-right mark is a bidi control and the
    # byte order mark is refused by name (`_FORBIDDEN_RANGES`), so neither
    # can reach the detector from the form side.
    INVISIBLE_IN_A_FORM = [(label, char) for label, char in INVISIBLE
                           if char not in ("\u200e", "\ufeff")]

    @pytest.mark.parametrize("label,char", INVISIBLE_IN_A_FORM,
                             ids=[label for label, _ in INVISIBLE_IN_A_FORM])
    def test_an_invisible_character_inside_the_form_is_seen_through(
            self, label, char):
        """The mapping side too: a name pasted with a soft hyphen in it
        still finds the plain spelling in a label."""
        detector = P.Compiled(P.validate_mapping(
            [{"original": f"Tho{char}mas", "pseudonym": "Zed"}])).detector
        assert detector.contains("Thomas_interview.txt") is True

    def test_a_fullwidth_spelling_is_the_same_name(self):
        """Compatibility normalisation (NFKC) on both sides: a fullwidth
        spelling renders as the name and used to be missed."""
        detector = self._detector()
        assert detector.contains("\uff34\uff48\uff4f\uff4d\uff41\uff53"
                                 " case memo") is True
        assert P.Compiled(P.validate_mapping(
            [{"original": "\uff34\uff48\uff4f\uff4d\uff41\uff53",
              "pseudonym": "Zed"}])).detector.contains("Thomas_P01") is True

    def test_a_look_alike_from_another_script_is_out_of_scope(self):
        """Pinned as the LIMIT the prose states, not as a wish: a
        Cyrillic letter is a different code point after every
        normalisation there is, and PRIVACY.md says the detector does not
        reach it. If this ever passes, the prose must change with it."""
        detector = self._detector()
        assert detector.contains("\u0422homas") is False       # Cyrillic Te
        assert detector.contains("Th\u043emas") is False       # Cyrillic o

    # The same four characters BETWEEN the two words of a form (fix round
    # 4, R1). Fix round 3 stripped the form before splitting it, so
    # `Mary<ZWSP>Ann` fused into the one word `MaryAnn` and the separated
    # spellings a file list holds were no longer found: an under-report,
    # and a regression against the round before. The byte order mark is
    # refused by validation and reaches the detector directly only.
    # Beside them, the two visible separators a file list holds (fix
    # round 5, S1): no form-side pin had one, so the split class could
    # be narrowed to `[^\w.\-]+` with the suite green, under which a
    # typed "Mary-Ann" found neither "Mary Ann" nor "Mary_Ann.txt".
    # And the two visible separators a NAME holds (the final
    # re-performance, 2026-09-15): with only hyphen and underscore
    # pinned, the split class could still drop the apostrophe or the
    # full stop with the suite green, under which "O'Brien" no longer
    # found "OBrien_interview.txt" and "J.R." no longer found
    # "JR_interview.txt". The curly apostrophe rides with them because
    # a transcript pasted from a word processor carries U+2019.
    BETWEEN_TWO_WORDS = [
        ("zero-width space", "\u200b"),
        ("soft hyphen", "\u00ad"),
        ("word joiner", "\u2060"),
        ("byte order mark", "\ufeff"),
        ("hyphen", "-"),
        ("underscore", "_"),
        ("apostrophe", "'"),
        ("curly apostrophe", "\u2019"),
        ("full stop", "."),
    ]

    @pytest.mark.parametrize("label,char", BETWEEN_TWO_WORDS,
                             ids=[label for label, _ in BETWEEN_TWO_WORDS])
    def test_an_invisible_character_between_two_words_of_a_form_separates_them(
            self, label, char):
        detector = P.NameDetector([f"Mary{char}Ann"])
        for value in ("Mary Ann", "Mary_Ann.txt", "Mary-Ann", "MaryAnn",
                      "MARY ANN", f"Mary{char}Ann"):
            assert detector.contains(value) is True, (label, value)
        assert detector.contains("Mary and Ann") is False
        if char != "\ufeff":
            through_validation = P.Compiled(P.validate_mapping(
                [{"original": f"Mary{char}Ann", "pseudonym": "Zed"}])
            ).detector
            assert through_validation.contains("Mary_Ann.txt") is True

    def test_a_hangul_filler_between_two_words_fuses_them_as_documented(
            self):
        """The exception to the pin above, pinned as the limit it is
        (fix round 5, R1 note): the four Hangul fillers are letters to
        `\\w`, so between two words of a FORM they are not a split point
        and the per-piece strip fuses the form into `MaryAnn`, exactly
        as `_detector_parts` says and as it was at ac359e3. A filler in
        a VALUE is stripped like any other, so that side still reads
        `Mary<filler>Ann` as "Mary Ann". If this ever passes the other
        way, the docstring must change with it."""
        for filler in ("\u115f", "\u1160", "\u3164", "\uffa0"):
            assert P._detector_parts(f"Mary{filler}Ann") == ["MaryAnn"]
            fused = P.NameDetector([f"Mary{filler}Ann"])
            assert fused.contains("MaryAnn") is True, hex(ord(filler))
            assert fused.contains("Mary Ann") is False, hex(ord(filler))
            assert self._detector().contains(f"Mary{filler}Ann") is True

    def test_a_space_of_any_width_is_a_separator_not_an_invisible_character(
            self):
        """Decided with the property table (fix round 4, R2): NFKC turns
        a thin space (U+2009) or a hair space (U+200A) into a plain one,
        and a plain space between the halves of a one-word name is a
        gap the reader sees, exactly as it is for a plain space. Between
        the words of a two-word name it is the separator it always was.
        Pinned so that the code and this sentence move together."""
        detector = self._detector()
        for space in ("\u2009", "\u200a", " "):
            assert detector.contains(f"Tho{space}mas") is False, repr(space)
            assert detector.contains(f"Mary{space}Ann") is True, repr(space)

    def test_a_form_made_of_invisible_characters_matches_nothing(self):
        """Validation lets a zero-width space through and two of them
        pass the length rule; stripped, the form is empty, and an empty
        alternative would match every string in the project."""
        detector = P.Compiled(P.validate_mapping(
            [{"original": "\u200b\u200b", "pseudonym": "Zed"}])).detector
        assert detector.contains("anything at all") is False
        assert detector.contains("\u200b\u200b") is False

    # The Default_Ignorable_Code_Point table (fix round 4, R2), pinned at
    # every range boundary: the first and last code point of each range
    # are in it, the code points on either side are not, and the total
    # is the file's own. A dropped range, or one boundary moved on its
    # own, is red here. A range shifted by one at BOTH ends is not: the
    # count and the range total are unchanged, and the boundary pin
    # reads its boundaries from the table it pins (fix round 5, S3).
    # The digest below states the set independently of the module.
    def test_the_table_is_the_unicode_property_as_transcribed(self):
        assert P.DEFAULT_IGNORABLE_UNICODE_VERSION == "15.1.0"
        assert P.DEFAULT_IGNORABLE_COUNT == 4174
        assert len(P._DEFAULT_IGNORABLE) == P.DEFAULT_IGNORABLE_COUNT
        assert len(P.DEFAULT_IGNORABLE_RANGES) == 17
        previous_high = -2
        for low, high in P.DEFAULT_IGNORABLE_RANGES:
            assert low <= high
            assert low > previous_high + 1, "ranges ascend and never touch"
            previous_high = high

    @pytest.mark.parametrize("low,high", P.DEFAULT_IGNORABLE_RANGES,
                             ids=[f"U+{low:04X}..U+{high:04X}"
                                  for low, high in P.DEFAULT_IGNORABLE_RANGES])
    def test_each_range_boundary_is_in_the_table_and_its_neighbours_are_not(
            self, low, high):
        assert P.is_default_ignorable(low) is True
        assert P.is_default_ignorable(high) is True
        assert P.is_default_ignorable(low - 1) is False
        assert P.is_default_ignorable(high + 1) is False
        # And the detector sees through both ends of the range inside a
        # one-word name, on the value side, whatever category the
        # interpreter's own Unicode tables give the code point.
        detector = self._detector()
        for code_point in (low, high):
            assert detector.contains(
                f"Tho{chr(code_point)}mas_interview.txt") is True, \
                f"U+{code_point:04X}"

    # The set stated independently of the module (fix round 5, S3): the
    # sha256 of the Default_Ignorable_Code_Point code points of Unicode
    # 15.1.0's DerivedCoreProperties.txt, one per line as `U+XXXX` in
    # ascending order with no trailing newline, computed from the file
    # itself (14.0.0 and 16.0.0 give the same digest; 13.0.0, which had
    # not assigned U+180F, gives 9be1ed89...). An edit to a range that
    # keeps the count and the range total is red here.
    DEFAULT_IGNORABLE_SHA256 = (
        "b4a55dee5bbfce621938f79f5e5d3581f65d359afeb4c406ea8d79ab6ee54ed8")

    def test_the_table_is_the_property_file_by_digest(self):
        listing = "\n".join(f"U+{code_point:04X}"
                            for code_point in sorted(P._DEFAULT_IGNORABLE))
        assert hashlib.sha256(listing.encode("ascii")).hexdigest() == \
            self.DEFAULT_IGNORABLE_SHA256

    def test_the_price_of_the_wider_reading_is_paid_knowingly(self):
        """An entry for "Tom" makes "tomorrow" count. A rule that
        catches `ThomasB.txt` cannot spare `tomorrow`, and of the two
        mistakes only one ships a participant's name. Pinned so that
        nobody "fixes" it into a boundary test by accident."""
        detector = self._detector()
        assert detector.contains("tomorrow.txt") is True
        assert detector.contains("thomasina notes") is True


# =============================================================================
# THE CHARACTER SWEEP, MADE FAST (v0.13 Brief 1, item 2)
# =============================================================================

def reference_strip_unseen(text):
    """The sweep as shipped in v0.12, verbatim: the per-character
    generator `_strip_unseen` was, kept HERE and not in the product, so
    the fast path is pinned against the reading it replaced rather than
    against itself."""
    import unicodedata
    return "".join(ch for ch in text
                   if ord(ch) not in P._DEFAULT_IGNORABLE
                   and unicodedata.category(ch) != "Cf")


def _live_cf():
    """Every code point in category Cf on the RUNNING interpreter."""
    import unicodedata
    return {code_point for code_point in range(0x110000)
            if unicodedata.category(chr(code_point)) == "Cf"}


class TestTheCharacterSweepIsTheGeneratorByteForByte:
    """`_strip_unseen` decides what the residue counts AND whether a file
    name or a path is withheld from the run record and the journal entry
    (`_pseudonymise_safe_name`), so the speed-up is pinned identical to
    the generator it replaced, over every code point the reading turns
    on. These pins are not to be softened (cross-check, section 9)."""

    @staticmethod
    def _everything():
        """One string that carries every code point the sweep decides
        on, each between two letters so nothing is at an edge: the
        4,174 default-ignorable code points, every Cf code point on this
        interpreter, the fullwidth block U+FF01 to U+FF5E, U+00DF, an
        astral-plane sample and a pure-ASCII sample."""
        special = sorted(P._DEFAULT_IGNORABLE | _live_cf())
        assert len(special) >= P.DEFAULT_IGNORABLE_COUNT
        fullwidth = range(0xFF01, 0xFF5F)
        astral = (0x1F600, 0x1D400, 0x20000, 0x10000, 0x1D173, 0xE0001,
                  0x10FFFD)
        pieces = ["Tho" + chr(code_point) + "mas "
                  for code_point in (*special, *fullwidth, 0x00DF, *astral)]
        pieces.append("plain ASCII: Thomas, Mary_Ann & Co. 0123456789 ~")
        return "".join(pieces)

    def test_the_fast_sweep_is_the_generator_byte_for_byte(self):
        text = self._everything()
        assert not text.isascii()
        assert P._strip_unseen(text) == reference_strip_unseen(text)

    def test_the_reader_sees_the_same_after_nfkc_too(self):
        import unicodedata
        text = self._everything()
        assert P._reader_sees(text) == reference_strip_unseen(
            unicodedata.normalize("NFKC", text))

    @pytest.mark.parametrize("text", [
        "", "Thomas", "plain ascii with a tab\tand a newline\n",
        "".join(chr(code_point) for code_point in range(128))],
        ids=["empty", "word", "controls", "every-ascii"])
    def test_ascii_comes_back_as_it_went_in(self, text):
        assert P._strip_unseen(text) == reference_strip_unseen(text) == text

    def test_the_table_is_the_property_and_the_live_cf_sweep(self):
        """Membership, not a count: an interpreter upgrade that moves the
        Cf set fails here loudly rather than narrowing the reading."""
        table = P._unseen_table()
        assert set(table) == P._DEFAULT_IGNORABLE | _live_cf()
        assert set(table.values()) == {None}

    def test_the_ascii_guard_is_exact_on_this_interpreter(self):
        """The guard returns ASCII untouched; three facts make that exact,
        read from the running interpreter rather than asserted in prose:
        no code point below 128 is default-ignorable, none is in category
        Cf, and NFKC moves none of them."""
        import unicodedata
        ascii_range = range(128)
        assert not any(P.is_default_ignorable(cp) for cp in ascii_range)
        assert not any(unicodedata.category(chr(cp)) == "Cf"
                       for cp in ascii_range)
        every = "".join(chr(cp) for cp in ascii_range)
        assert unicodedata.normalize("NFKC", every) == every
        assert all(unicodedata.normalize("NFKC", chr(cp)) == chr(cp)
                   for cp in ascii_range)

    def test_the_table_is_built_lazily_and_only_for_non_ascii(self):
        """Importing the module does not build it, an ASCII call does not
        build it, and the first non-ASCII call does. In a fresh
        interpreter, because this one has long since built it."""
        import subprocess
        src = Path(P.__file__).resolve().parents[1]
        probe = (
            "import sys\n"
            f"sys.path.insert(0, {str(src)!r})\n"
            "import qualcoder_mcp.pseudonymise as P\n"
            "print(P._UNSEEN_TABLE is None)\n"
            "P._reader_sees('Thomas_interview.txt, plain ASCII')\n"
            "P._strip_unseen('Mary Ann')\n"
            "print(P._UNSEEN_TABLE is None)\n"
            "P._reader_sees('Tho\\u00admas')\n"
            "print(P._UNSEEN_TABLE is not None)\n"
            "print(P.__file__)\n")
        result = subprocess.run([sys.executable, "-u", "-B", "-c", probe],
                                capture_output=True, text=True, timeout=60)
        lines = result.stdout.splitlines()
        assert result.returncode == 0, result.stderr
        assert lines[:3] == ["True", "True", "True"], result.stdout
        assert Path(lines[3]).resolve() == Path(P.__file__).resolve()


# =============================================================================
# NAMES LEFT IN THE FILE TEXT (v0.13 Brief 1, item 4)
# =============================================================================

def capture_group_attribution(compiled, seen):
    """The attribution the engine must NOT be built on, as the reference.

    One capture group per alternative, in the pattern's own order, and
    `lastindex` names the form: exact, and 82 seconds per 1.27 MB at the
    documented ceiling of 2,000 forms, which is why it lives here, in the
    test file, and never in the product (counts study, 5.3 and 5.4).
    """
    alternatives = [(form, index, P._detector_alternative(form))
                    for form, index in compiled.forms]
    alternatives = [item for item in alternatives if item[2]]
    if not alternatives:
        return {}
    pattern = re.compile("|".join(f"({alternative})"
                                  for _, _, alternative in alternatives),
                         re.IGNORECASE)
    counts = {}
    for match in pattern.finditer(seen):
        form, index, _ = alternatives[match.lastindex - 1]
        counts[(index, form)] = counts.get((index, form), 0) + 1
    return counts


# The counts study's alphabet: letters, separators, soft hyphens,
# zero-width spaces, fullwidth letters, U+00DF and mixed case; and since
# the Brief 1 fix round, a combining mark after a letter (U+0301,
# U+0308), which the whole-word rule reads as a boundary and NFKC
# composes onto the letter (QA-1: the alphabet without it could not reach
# the case that made the wide reading narrower than the rewrite).
_RESIDUE_LETTERS = "abABßSａＢ"
_RESIDUE_SEPARATORS = (" ", "_", "-", ".", "\u00ad", "\u200b", "", ", ",
                       "\u0301 ", "\u0308")
_FULLWIDTH = {ord(c): chr(ord(c) + 0xFEE0) for c in
              "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"}


@st.composite
def _residue_form(draw, one_word=False):
    """A surface form: one or two words of the alphabet's letters."""
    words = draw(st.lists(
        st.text(alphabet=_RESIDUE_LETTERS, min_size=1, max_size=3),
        min_size=1, max_size=1 if one_word else 2))
    joiner = draw(st.sampled_from((" ", "-", "_", "­", "​", "")))
    return joiner.join(words)


@st.composite
def _residue_case(draw, max_entries=3, one_form=False):
    """A mapping, a text built around its forms, and a case mode.

    The text is made of the forms themselves and the spellings the
    report has to classify (another case, the words joined another way,
    a letter attached, a soft hyphen inside, fullwidth), with letters
    and separators between, so every kind of occurrence is common
    rather than lucky.
    """
    mode = draw(st.sampled_from(P.CASE_MODES))
    count = 1 if one_form else draw(st.integers(1, max_entries))
    forms = [draw(_residue_form(one_word=one_form)) for _ in range(count)]
    # One pseudonym in three may carry the first form as a word of its
    # own, the shape that puts a name back (item 5), so the rewritten
    # row reaches put_back_by_a_pseudonym.
    pseudonyms = ["Qqq", "Www", "Eee"]
    if draw(st.integers(0, 2)) == 0:
        pseudonyms[draw(st.integers(0, count - 1))] = "Qqq " + forms[0]
    raw = [{"original": form, "pseudonym": pseudonym}
           for form, pseudonym in zip(forms, pseudonyms)]
    try:
        compiled = P.Compiled(P.validate_mapping(raw, mode))
    except P.MappingError:
        from hypothesis import assume
        assume(False)
    tokens = []
    for form in forms:
        parts = [part for part in re.split("[ _\\-.­​]+", form)
                 if part]
        tokens += [form, form, form.upper(), form.lower(), form.swapcase(),
                   "".join(parts), "_".join(parts), " ".join(parts),
                   "x" + form, form + "b", form.translate(_FULLWIDTH),
                   form[:1] + "\u00ad" + form[1:],
                   form + "\u0301", form + "\u0308"]
    tokens += ["a", "S", "ß", "ss", "B"]
    pieces = []
    for index in range(draw(st.integers(0, 8))):
        if index:
            pieces.append(draw(st.sampled_from(_RESIDUE_SEPARATORS)))
        pieces.append(draw(st.sampled_from(tokens)))
    text = "".join(pieces)
    rewritten = draw(st.booleans())
    if rewritten:
        text = P.apply_replacements(text, P.find_replacements(compiled, text))
    return compiled, text, rewritten


_RESIDUE_SETTINGS = settings(max_examples=300, deadline=None,
                             suppress_health_check=[HealthCheck.too_slow,
                                                    HealthCheck.filter_too_much])


def _left(mapping, text, mode="exact", rewritten=False, words=True):
    compiled = P.Compiled(P.validate_mapping(mapping, mode))
    return P.names_left_in_text(compiled, text, words, rewritten)


class TestNamesLeftInText:
    """The engine of the residue's file-text block: two readings, the
    kinds a wide occurrence is split into, and the cheap attribution."""

    FIXTURE_BEFORE = (
        "Thomas said the file was ready. See Thomas_Smith.txt and "
        "Thomas_P01. Thomasin arrived later. THOMAS shouted, and thomas "
        "whispered. Tho­mas signed the form. "
        "Ｔｈｏｍａｓ in fullwidth. Mary Ann and "
        "MaryAnn and Mary_Ann.")
    FIXTURE_MAPPING = [{"original": "Thomas", "pseudonym": "Alex"},
                       {"original": "Mary Ann", "pseudonym": "Robin Lee"}]

    # The counts study's fixture after a run, class by class (5.7).
    @pytest.mark.parametrize("mode,thomas", [
        ("exact", {"wide": 7, "inside_a_longer_word": 3, "case_only": 2,
                   "joined_differently": 0, "normalisation_variants": 2}),
        ("insensitive", {"wide": 5, "inside_a_longer_word": 3,
                         "case_only": 0, "joined_differently": 0,
                         "normalisation_variants": 2}),
        ("insensitive_preserve", {"wide": 5, "inside_a_longer_word": 3,
                                  "case_only": 0, "joined_differently": 0,
                                  "normalisation_variants": 2}),
    ])
    def test_the_counts_study_fixture_class_by_class(self, mode, thomas):
        compiled = P.Compiled(P.validate_mapping(self.FIXTURE_MAPPING, mode))
        after = P.apply_replacements(
            self.FIXTURE_BEFORE,
            P.find_replacements(compiled, self.FIXTURE_BEFORE))
        found = P.names_left_in_text(compiled, after, True, True)
        rows = {entry["entry"]: entry for entry in found["entries"]}
        assert rows[0]["occurrences"]["wide"] == thomas["wide"]
        assert rows[0]["occurrences"]["whole_word"] == 0
        for kind in ("inside_a_longer_word", "case_only",
                     "joined_differently", "normalisation_variants"):
            assert rows[0][kind] == thomas[kind], kind
        assert rows[0]["put_back_by_a_pseudonym"] == 0
        assert rows[1]["occurrences"]["wide"] == 2
        assert rows[1]["joined_differently"] == 2
        assert found["unattributed"] == 0
        assert found["occurrences"]["whole_word"] == 0
        if mode == "exact":
            assert found["case_variants_seen"] == [
                {"entry": 0, "form": "Thomas", "other_case_count": 2}]
        else:
            assert found["case_variants_seen"] == []
        assert found["normalisation_variants_seen"] == [
            {"entry": 0, "form": "Thomas", "count": 2}]
        assert rows[0]["longer_words"] == [
            {"word": "Thomas_P01", "count": 1},
            {"word": "Thomas_Smith", "count": 1},
            {"word": "Thomasin", "count": 1}]

    def test_the_residue_copy_of_the_case_variants_agrees_with_the_preview(
            self):
        """The counts study measured the post-run reading and the pre-run
        reading of `case_variants_seen` to agree; on this fixture they
        do, key for key."""
        compiled = P.Compiled(P.validate_mapping(self.FIXTURE_MAPPING))
        after = P.apply_replacements(
            self.FIXTURE_BEFORE,
            P.find_replacements(compiled, self.FIXTURE_BEFORE))
        assert P.names_left_in_text(compiled, after, True, True)[
            "case_variants_seen"] == P.case_variants_seen(
                compiled, self.FIXTURE_BEFORE)

    def test_the_fixture_attribution_is_the_capture_group_one(self):
        compiled = P.Compiled(P.validate_mapping(self.FIXTURE_MAPPING))
        seen = P._reader_sees(self.FIXTURE_BEFORE)
        counts, unattributed = P.direct_attribution(compiled, seen)
        assert unattributed == 0
        assert counts == capture_group_attribution(compiled, seen)

    def test_no_word_is_listed_when_the_caller_asks_for_none(self):
        found = _left(self.FIXTURE_MAPPING, "See Thomas_P01.", words=False)
        assert found["entries"][0]["inside_a_longer_word"] == 1
        assert "longer_words" not in found["entries"][0]
        assert "longer_words_truncated" not in found["entries"][0]
        assert "Thomas_P01" not in repr(found)

    def test_the_longer_words_are_ranked_and_capped(self):
        words = [f"Thomas_{n:02d}" for n in range(P.MAX_LONGER_WORDS_PER_ENTRY
                                                  + 5)]
        text = " ".join(words + ["Thomasin", "Thomasin", "Thomasin"])
        entry = _left(self.FIXTURE_MAPPING, text)["entries"][0]
        assert entry["inside_a_longer_word"] == len(words) + 3
        listed = entry["longer_words"]
        assert len(listed) == P.MAX_LONGER_WORDS_PER_ENTRY
        assert listed[0] == {"word": "Thomasin", "count": 3}
        assert listed[1] == {"word": "Thomas_00", "count": 1}
        assert entry["longer_words_truncated"] is True

    # Fix round 1, F3 (the lead's ruling on S-1): a longer word is a word.
    # Listed only when it extends the name by at most eight characters,
    # left and right together, and never when what extends it is in a
    # script written without separators. The Security gate's samples.
    @pytest.mark.parametrize("name,text", [
        ("Thomas", "See Thomas_Smith_DOB_1984_03_12_NHS_4857773456_Grimsby_"
                   "ward_7 today."),
        ("Thomas", "x" * 40 + "Thomas" + "y" * 53 + "."),
        ("\u30c8\u30fc\u30de\u30b9",
         "\u30c8\u30fc\u30de\u30b9\u3055\u3093\u306f\u30b0\u30ea\u30e0"
         "\u30b9\u30d3\u30fc\u306e\u75c5\u9662\u3067\u770b\u8b77\u5e2b"
         "\u3068\u3057\u3066\u50cd\u3044\u3066\u3044\u307e\u3057\u305f"
         "\u3002"),
        ("\u6258\u9a6c\u65af",
         "\u6258\u9a6c\u65af\u5728\u683c\u91cc\u59c6\u65af\u6bd4\u7684"
         "\u533b\u9662\u5f53\u62a4\u58eb\uff0c\u4ed6\u7684\u59b9\u59b9"
         "\u4f4f\u5728\u4f26\u6566\u3002"),
        ("\u0e42\u0e17\u0e21\u0e31\u0e2a",
         "\u0e42\u0e17\u0e21\u0e31\u0e2a\u0e17\u0e33\u0e07\u0e32\u0e19"
         " \u0e17\u0e38\u0e01\u0e27\u0e31\u0e19"),
        # One Han character is enough, however short the extension.
        ("Thomas", "Thomas\u5148 said so."),
        # And on the LEFT of the name (fix round 2, the re-verification's
        # DR-3: every sample before had it on the right), and a Latin name
        # inside a Chinese sentence, Han on both sides.
        ("Thomas", "\u5148Thomas said so."),
        ("Thomas", "\u6628\u5929Thomas\u6765\u4e86\u533b\u9662\u3002"),
    ], ids=["identifier", "latin-run", "japanese", "chinese", "thai",
            "one-han-character", "one-han-character-left",
            "chinese-middle"])
    def test_a_run_that_is_not_a_word_is_counted_not_listed(self, name,
                                                             text):
        entry = _left([{"original": name, "pseudonym": "Alexandra"}],
                      text)["entries"][0]
        assert entry["inside_a_longer_word"] == 1
        assert entry["longer_words"] == []
        assert entry["longer_words_truncated"] is True

    def test_the_extension_bound_is_eight_characters_in_all(self):
        assert P.MAX_LONGER_WORD_EXTENSION == 8
        for word in ("Thomasson", "Thomas_P01", "Thomasin", "Thomas_Smith",
                     "xxThomasyyyyyy", "Thomasxxxxxxxx", "xxxxxxxxThomas"):
            entry = _left(self.FIXTURE_MAPPING, f"see {word} now")[
                "entries"][0]
            assert entry["longer_words"] == [{"word": word, "count": 1}], word
            assert "longer_words_truncated" not in entry
        for word in ("xxThomasyyyyyyy", "Thomasxxxxxxxxx", "xxxxxxxxxThomas"):
            entry = _left(self.FIXTURE_MAPPING, f"see {word} now")[
                "entries"][0]
            assert entry["longer_words"] == [], word
            assert entry["longer_words_truncated"] is True

    # The table, pinned range by range, so an edit to it is red here.
    NO_SEPARATOR_RANGES = (
        (0x0E00, 0x0E7F), (0x0E80, 0x0EFF), (0x1000, 0x109F),
        (0x1780, 0x17FF), (0x19E0, 0x19FF), (0x2E80, 0x2EFF),
        (0x2F00, 0x2FDF), (0x3005, 0x3007), (0x3021, 0x3029),
        (0x3038, 0x303B), (0x3040, 0x309F), (0x30A0, 0x30FF),
        (0x31F0, 0x31FF), (0x3400, 0x4DBF), (0x4E00, 0x9FFF),
        (0xA9E0, 0xA9FF), (0xAA60, 0xAA7F), (0xF900, 0xFAFF),
        (0xFF66, 0xFF9F), (0x1AFF0, 0x1B16F), (0x20000, 0x2FA1F),
        (0x30000, 0x323AF))

    def test_the_no_separator_scripts_are_the_seven_named(self):
        assert tuple((low, high) for low, high, _ in
                     P.NO_SEPARATOR_RANGES) == self.NO_SEPARATOR_RANGES
        previous = -1
        for low, high in self.NO_SEPARATOR_RANGES:
            assert previous < low <= high
            previous = high
            for code_point in (low, high):
                assert P.in_a_no_separator_script(chr(code_point))
            assert not P.in_a_no_separator_script(chr(low - 1)) or \
                any(l <= low - 1 <= h for l, h in self.NO_SEPARATOR_RANGES)
        # One character from each script the ruling names, and some that
        # are not in them: Latin, Cyrillic, Greek, Hangul (written with
        # spaces), a digit and the underscore.
        for char in "\u6258\u3055\u30c8\u0e17\u0ea5\u1780\u1000":
            assert P.in_a_no_separator_script(char), hex(ord(char))
        for char in "a\u0416\u03a9\uac00_7":
            assert not P.in_a_no_separator_script(char), hex(ord(char))

    def test_a_match_inside_a_long_run_costs_a_bounded_walk(self):
        """S-4's per-match cost: the walk stops one character past the
        bound on each side, so a hostile run of the name repeated is not
        walked once per match. Counted here by the characters the walk
        reads, which is fixed per match whatever the run's length."""
        reads = []
        original = P._is_word_char

        def counting(char):
            reads.append(char)
            return original(char)

        P._is_word_char = counting
        try:
            _left(self.FIXTURE_MAPPING, "Thomas" * 2000)
        finally:
            P._is_word_char = original
        # 2,000 matches; the boundary test reads two characters each and
        # the walk at most nine more on each side.
        assert len(reads) <= 2000 * (2 + 2 * (P.MAX_LONGER_WORD_EXTENSION
                                              + 1))

    def test_the_tie_is_broken_the_way_the_pattern_breaks_it(self):
        """Two forms whose words concatenate to one key, `Ann Marie` and
        a single-word `AnnMarie` that sorts first because of an invisible
        trailing character: the combined pattern charges `Ann Marie` to
        the only alternative that can match it, and so does the lookup."""
        mapping = [{"original": "AnnMarie​", "pseudonym": "Pat"},
                   {"original": "Ann Marie", "pseudonym": "Sue"}]
        compiled = P.Compiled(P.validate_mapping(mapping))
        seen = P._reader_sees("Ann Marie and AnnMarie and Ann_Marie")
        counts, unattributed = P.direct_attribution(compiled, seen)
        assert unattributed == 0
        assert counts == capture_group_attribution(compiled, seen) == {
            (0, "AnnMarie​"): 1, (1, "Ann Marie"): 2}

    def test_a_spelling_only_casefolding_reaches_is_counted(self):
        """U+00DF casefolds to "ss" and does not match "SS" under
        IGNORECASE: the folded second reading counts those spellings,
        charged to normalisation_variants as the declared heuristic."""
        found = _left([{"original": "Straße", "pseudonym": "Weg"}],
                      "STRASSE and strasse and Straße")
        entry = found["entries"][0]
        assert entry["occurrences"]["wide"] == 3
        assert entry["normalisation_variants"] == 2
        assert found["unattributed"] == 0

    def test_an_occurrence_neither_pass_can_share_is_counted_once(self):
        """Dotted capital I: IGNORECASE matches it to "i" and casefolding
        spells it as two code points. Until fix round 2 the direct pass
        keyed matches by casefolding and could not place it, and it was
        charged to no entry; the key is now the pattern's own comparison
        (`ignorecase_key`), so it is ONE occurrence, counted once, charged
        to its entry as a difference of letter case, and the total still
        adds up."""
        found = _left([{"original": "Ali", "pseudonym": "Bob"}],
                      "ALİ came")
        assert found["occurrences"]["wide"] == 1
        assert found["unattributed"] == 0
        assert [(entry["entry"], entry["case_only"])
                for entry in found["entries"]] == [(0, 1)]

    # The re-verification's CORR-1 (lane 2) and finding 1 (lane 4): the
    # property below met these on about one random seed in twenty, and
    # the engine gave the same answer at 4b46345. The folded pass keyed
    # its match through NFKC again, which composed the acute onto the
    # casefolded "ss" ("sś") so no form's key matched; it is keyed as it
    # stands now (`_folded_key`), and each case is charged to an entry.
    @pytest.mark.parametrize("mapping, text", [
        ([("ß A", "Qqq ß A"), ("A A", "Www"), ("ß ß", "Eee")],
         "SS SS ß\u0301 ß A"),
        ([("ß ß", "Eee")], "ß ss ß ß ß\u0301 ß ß"),
        ([("ss ß", "Eee")], "ß\u0301 ß"),
    ], ids=["lane-4-seed", "lane-2-seed-1212", "lane-2-shortest"])
    def test_a_folded_match_before_a_combining_mark_is_attributed(
            self, mapping, text):
        found = _left([{"original": original, "pseudonym": pseudonym}
                       for original, pseudonym in mapping], text)
        assert found["unattributed"] == 0
        kinds = sum(entry[kind] for entry in found["entries"]
                    for kind in P.TEXT_RESIDUE_REASONS)
        assert kinds == found["occurrences"]["wide"] > 0

    def test_the_whole_run_of_marks_after_a_word_is_read(self):
        """The re-verification's CORR-2: sixteen marks that compose with
        nothing (U+0316) and a seventeenth that composes onto the "e"
        across them (its combining class is higher). Fix round 1 read
        sixteen marks and the file read {wide 0, whole_word 1}; the whole
        run is read now, so the rewriter's whole word is in the wide
        reading too. Forty marks as well, past any window."""
        compiled = P.Compiled(P.validate_mapping(
            [{"original": "Rene", "pseudonym": "Alex"}]))
        for below in (16, 39):
            text = f"Later Rene{chr(0x316) * below}\u0301 arrived."
            assert not compiled.detector.contains(text)
            found = P.names_left_in_text(compiled, text, True, False)
            assert found["occurrences"] == {"wide": 1, "whole_word": 1}

    def test_the_whole_detector_question_is_charged(self):
        """A whole word the form's own alternatives do not find in its
        reading (a name before a composing mark) is asked of the whole
        detector, once per spelling in a preview, and each question is
        charged `residue_work` of the word and its marks against the
        caller's allowance (fix round 2): past it the count stops."""
        def compiled():
            return P.Compiled(P.validate_mapping(
                [{"original": "Rene", "pseudonym": "Alex"}]))
        first, second = "Rene\u0316\u0301", "Rene\u0317\u0301"
        text = f"Later {first} and {second} and {first} arrived."
        found = P.names_left_in_text(compiled(), text, True, False)
        one = compiled()
        charged = P.residue_work(one, first) + P.residue_work(one, second)
        assert found["extra_work"] == charged
        assert found["occurrences"] == {"wide": 3, "whole_word": 3}
        assert P.names_left_in_text(compiled(), text, True, False,
                                    max_extra_work=charged - 1) is None
        assert P.names_left_in_text(compiled(), text, True, False,
                                    max_extra_work=charged) is not None

    # QA-1, fixed at the engine: a name followed by a combining mark (an
    # NFD "René") is a whole word to the rewriter and composed away by the
    # detector's NFKC reading. The union counts it in the wide reading.
    NFD_RENE = "Rene\u0301"

    @pytest.mark.parametrize("mark", ["\u0301", "\u0308"],
                             ids=["acute", "diaeresis"])
    def test_a_name_before_a_combining_mark_is_in_the_wide_reading(
            self, mark):
        compiled = P.Compiled(P.validate_mapping(
            [{"original": "Rene", "pseudonym": "Alex"}]))
        text = f"Later Rene{mark} arrived."
        assert compiled.pattern.search(text)
        assert not compiled.detector.contains(text)
        assert compiled.carries_a_name(text)
        found = P.names_left_in_text(compiled, text, True, False)
        assert found["occurrences"] == {"wide": 1, "whole_word": 1}
        entry = found["entries"][0]
        assert entry["whole_word_in_a_file_not_rewritten"] == 1
        assert sum(entry[kind] for kind in P.TEXT_RESIDUE_REASONS) == 1

    def test_a_combining_mark_counts_once_beside_a_plain_name(self):
        """The plain whole word is counted by both matchers once; the
        decomposed one by the rewriter alone, once; the longer word by the
        detector alone, once."""
        compiled = P.Compiled(P.validate_mapping(
            [{"original": "Rene", "pseudonym": "Alex"}]))
        found = P.names_left_in_text(
            compiled, f"Rene met {self.NFD_RENE} and Renes.", True, False)
        assert found["occurrences"] == {"wide": 3, "whole_word": 2}
        entry = found["entries"][0]
        assert entry["inside_a_longer_word"] == 1
        assert entry["whole_word_in_a_file_not_rewritten"] == 2

    def test_on_the_rewritten_row_it_is_put_back(self):
        """A pseudonym that writes the name before a mark already there."""
        compiled = P.Compiled(P.validate_mapping(
            [{"original": "Thomas", "pseudonym": "Jo Rene"},
             {"original": "Rene", "pseudonym": "Alex"}]))
        before = "Thomas\u0301 spoke."
        after = P.apply_replacements(before,
                                     P.find_replacements(compiled, before))
        assert after == "Jo Rene\u0301 spoke."
        found = P.names_left_in_text(compiled, after, True, True)
        rows = {entry["entry"]: entry for entry in found["entries"]}
        assert rows[1]["occurrences"] == {"wide": 1, "whole_word": 1}
        assert rows[1]["put_back_by_a_pseudonym"] == 1

    # What the two readings are NOT, stated as pins so nobody builds on
    # the stronger claims. The wide reading joins a name's words across
    # any separators, and an invisible character splits words for the
    # rewriter but not for a reader, so at FILE level a whole-word count
    # can exceed the wide one; and a longer form can merge two shorter
    # ones, so adding an entry can LOWER an occurrence count.
    def test_one_wide_occurrence_can_be_two_whole_words(self):
        mapping = [{"original": "Mary Ann", "pseudonym": "Pat"},
                   {"original": "Mary", "pseudonym": "Sue"},
                   {"original": "Ann", "pseudonym": "Joy"}]
        found = _left(mapping, "Mary, Ann came.")
        assert found["occurrences"] == {"wide": 1, "whole_word": 2}
        rows = {entry["entry"]: entry for entry in found["entries"]}
        assert rows[0]["joined_differently"] == 1
        # The entries the rewriter would match are listed although no
        # wide occurrence is charged to them.
        assert rows[1]["occurrences"] == {"wide": 0, "whole_word": 1}
        assert rows[2]["occurrences"] == {"wide": 0, "whole_word": 1}
        joined = _left([{"original": "Mary", "pseudonym": "Sue"},
                        {"original": "Ann", "pseudonym": "Joy"},
                        {"original": "MaryAnn", "pseudonym": "Pat"}],
                       "Mary­Ann came.")
        assert joined["occurrences"] == {"wide": 1, "whole_word": 2}

    def test_adding_an_entry_can_lower_an_occurrence_count(self):
        two = [{"original": "Mary", "pseudonym": "Sue"},
               {"original": "Ann", "pseudonym": "Joy"}]
        three = two + [{"original": "Mary Ann", "pseudonym": "Pat"}]
        assert _left(two, "Mary Ann came.")["occurrences"] == {
            "wide": 2, "whole_word": 2}
        assert _left(three, "Mary Ann came.")["occurrences"] == {
            "wide": 1, "whole_word": 1}

    # ---- properties over the counts study's alphabet ----------------

    @_RESIDUE_SETTINGS
    @given(case=_residue_case())
    def test_the_key_attribution_agrees_with_the_capture_group_reference(
            self, case):
        compiled, text, _rewritten = case
        seen = P._reader_sees(text)
        counts, unattributed = P.direct_attribution(compiled, seen)
        assert unattributed == 0
        assert counts == capture_group_attribution(compiled, seen)
        per_entry, reference = {}, {}
        for (index, _form), count in counts.items():
            per_entry[index] = per_entry.get(index, 0) + count
        for (index, _form), count in capture_group_attribution(
                compiled, seen).items():
            reference[index] = reference.get(index, 0) + count
        assert per_entry == reference

    @_RESIDUE_SETTINGS
    @given(case=_residue_case())
    def test_every_occurrence_is_in_exactly_one_kind(self, case):
        compiled, text, rewritten = case
        found = P.names_left_in_text(compiled, text, True, rewritten)
        total = found["unattributed"]
        for entry in found["entries"]:
            kinds = sum(entry[kind] for kind in P.TEXT_RESIDUE_REASONS)
            assert kinds == entry["occurrences"]["wide"], entry
            total += kinds
            if rewritten:
                assert entry["whole_word_in_a_file_not_rewritten"] == 0
            else:
                assert entry["put_back_by_a_pseudonym"] == 0
            shared = (entry["put_back_by_a_pseudonym"]
                      + entry["whole_word_in_a_file_not_rewritten"])
            assert shared <= entry["occurrences"]["whole_word"]
            assert set(entry["occurrences"]) >= {"wide", "whole_word"}
        assert total == found["occurrences"]["wide"]
        assert found["unattributed"] == 0

    @_RESIDUE_SETTINGS
    @given(case=_residue_case())
    def test_wide_is_non_zero_exactly_where_either_matcher_sees_a_name(
            self, case):
        """`wide` is never zero on a text either matcher finds a name in,
        and never non-zero where neither does: the folded second reading
        covers what IGNORECASE misses, and the union (fix round 1, QA-1)
        covers the whole words the reader's NFKC reading composes away."""
        compiled, text, rewritten = case
        found = P.names_left_in_text(compiled, text, False, rewritten)
        assert (found["occurrences"]["wide"] >= 1) == \
            compiled.carries_a_name(text)

    @_RESIDUE_SETTINGS
    @given(case=_residue_case())
    def test_a_field_the_rewriter_matches_is_one_the_wide_reading_counts(
            self, case):
        """`wide >= whole_word` for every FIELD: a field the rewriter's
        own rule matches is always a field the wide reading counts. The
        detector alone does NOT satisfy this (QA-1: "Rene" + U+0301); the
        union does, and this is the pin on the union."""
        compiled, text, _rewritten = case
        if compiled.pattern.search(text):
            assert compiled.carries_a_name(text)

    @_RESIDUE_SETTINGS
    @given(case=_residue_case(one_form=True))
    def test_for_one_single_word_form_wide_is_at_least_whole_word(
            self, case):
        """`wide >= whole_word` for a FILE, for a mapping of one
        single-word form, a combining mark after it included (QA-1);
        `test_one_wide_occurrence_can_be_two_whole_words` shows why it
        cannot hold for a mapping of several. Fix round 1 read sixteen
        marks after a whole word, and a seventeenth that composed onto
        the name made this false (the re-verification's CORR-2); the whole
        run is read since fix round 2 (`test_the_whole_run_of_marks_after_
        a_word_is_read`). That nothing else can make it false rests on
        lane 2's sweep of every code point (no character before a word
        composes into it), an argument and not a proof, so the defensive
        branch of the file-text warning stays."""
        compiled, text, rewritten = case
        found = P.names_left_in_text(compiled, text, False, rewritten)
        assert found["occurrences"]["wide"] >= \
            found["occurrences"]["whole_word"]

    @_RESIDUE_SETTINGS
    @given(case=_residue_case(max_entries=2),
           extra=_residue_form())
    def test_adding_an_entry_never_hides_a_name(self, case, extra):
        """Adding an entry never turns a field that shows a name, or a
        file that shows one, into one that does not, under either
        reading; `test_adding_an_entry_can_lower_an_occurrence_count`
        shows why the occurrence counts themselves are not monotone."""
        from hypothesis import assume
        compiled, text, rewritten = case
        raw = [{"original": entry.original, "pseudonym": entry.pseudonym}
               for entry in compiled.mapping.entries]
        raw.append({"original": extra, "pseudonym": "Rrr"})
        try:
            wider = P.Compiled(P.validate_mapping(raw, compiled.case_mode))
        except P.MappingError:
            assume(False)
        if compiled.detector.contains(text):
            assert wider.detector.contains(text)
        if compiled.pattern.search(text):
            assert wider.pattern.search(text)
        if compiled.carries_a_name(text):
            assert wider.carries_a_name(text)
        if P.names_left_in_text(compiled, text, False, rewritten)[
                "occurrences"]["wide"]:
            assert P.names_left_in_text(wider, text, False, rewritten)[
                "occurrences"]["wide"]

    def test_the_generator_reaches_every_kind(self):
        """A property proves nothing about a branch its inputs never
        reach: over the real distribution (derandomised), every kind is
        reached, the whole-word count is non-zero somewhere, and so is a
        whole word only the rewriter sees (a combining mark after a
        name, fix round 1)."""
        seen = {kind: 0 for kind in P.TEXT_RESIDUE_REASONS}
        seen["whole_word"] = 0
        seen["rewriter_only"] = 0

        @settings(max_examples=400, deadline=None, derandomize=True,
                  phases=[Phase.generate],
                  suppress_health_check=[HealthCheck.too_slow,
                                         HealthCheck.filter_too_much])
        @given(case=_residue_case())
        def count(case):
            compiled, text, rewritten = case
            found = P.names_left_in_text(compiled, text, False, rewritten)
            for entry in found["entries"]:
                for kind in P.TEXT_RESIDUE_REASONS:
                    seen[kind] += bool(entry[kind])
            seen["whole_word"] += bool(found["occurrences"]["whole_word"])
            seen["rewriter_only"] += bool(
                compiled.pattern.search(text)
                and not compiled.detector.contains(text))

        count()
        assert all(value > 0 for value in seen.values()), seen


class TestThePropertiesAreDerandomisedInCI:
    """Fix round 2, the lead's ruling on CORR-1: CI runs the properties
    derandomised through a Hypothesis profile (tests/conftest.py), so a
    push is never red by the luck of a seed; a random or a chosen seed
    stays available locally."""

    def test_the_environment_picks_the_profile(self):
        import conftest
        pick = conftest.hypothesis_profile_for
        assert pick({"CI": "true"}) == "ci"
        assert pick({}) == "default"
        assert pick({"CI": "true", "HYPOTHESIS_PROFILE": "default"}) == \
            "default"
        profile = settings.get_profile("ci")
        assert profile.derandomize is True
        assert profile.database is None
        assert profile.deadline is None

    # The variables Hypothesis reads to decide for itself that it is on a
    # CI machine (it then loads a "ci" profile of its own), and CI.
    CI_VARIABLES = ("CI", "__TOX_ENVIRONMENT_VARIABLE_ORIGINAL_CI",
                    "TF_BUILD", "bamboo.buildKey", "BUILDKITE", "CIRCLECI",
                    "CIRRUS_CI", "CODEBUILD_BUILD_ID", "GITHUB_ACTIONS",
                    "GITLAB_CI", "HEROKU_TEST_RUN_ID", "TEAMCITY_VERSION")

    def _derandomised_in_a_fresh_interpreter(self, **extra):
        import os
        import subprocess
        here = Path(__file__).resolve()
        probe = ("import sys; sys.path.insert(0, 'tests'); import conftest; "
                 "from hypothesis import settings; "
                 "print(settings(max_examples=3).derandomize)")
        environ = {key: value for key, value in os.environ.items()
                   if key not in self.CI_VARIABLES
                   and key != "HYPOTHESIS_PROFILE"}
        environ.update(extra)
        result = subprocess.run([sys.executable, "-B", "-c", probe],
                                cwd=str(here.parents[1]), env=environ,
                                capture_output=True, text=True, timeout=120)
        lines = result.stdout.strip().splitlines()
        assert lines[-1:] in (["True"], ["False"]), result.stderr[-2000:]
        return lines[-1] == "True"

    def test_a_settings_object_made_at_import_inherits_the_profile(self):
        """The mechanism itself, in a fresh interpreter: a module-level
        `settings(...)` such as `_RESIDUE_SETTINGS`, made after conftest
        has loaded the profile, is derandomised when the profile is
        asked for by name on a machine Hypothesis does not take for CI
        (so it is this suite's profile doing it, not Hypothesis's own),
        and random when nothing asks for it."""
        assert self._derandomised_in_a_fresh_interpreter(
            HYPOTHESIS_PROFILE="ci") is True
        assert self._derandomised_in_a_fresh_interpreter() is False

    def test_ci_is_derandomised(self):
        assert self._derandomised_in_a_fresh_interpreter(CI="true") is True


class TestTheFileTextCountStaysCheap:
    """The performance guard (Brief 1, 6.4). One capture group per
    alternative costs seconds per megabyte at a hundred forms and 82
    seconds at the documented ceiling; the ungrouped pass with the key
    lookup costs a fraction of a second. The ceiling is generous, so no
    ordinary machine trips it, and the measured rate is recorded and
    printed in the run's summary (tests/conftest.py) so the six CI logs
    carry it for ruling 7's sanity check of the work budget."""

    CEILING_SECONDS = 3.0
    CURLY_RATIO_CEILING = 2.8

    @staticmethod
    def _timed(compiled, text):
        """One count, timed with the collector paused after a collection.

        The count allocates small objects per occurrence, and inside a
        full suite run the interpreter's heap is thousands of tests deep,
        so a cyclic collection landing in the timed section measured 10.3
        ms per MB per form against 4.2 standalone on the same machine. A
        server process has no such heap, and the guard is about the
        algorithm's shape, so the collector is kept out of the timing."""
        import gc
        import time
        gc.collect()
        gc.disable()
        try:
            started = time.perf_counter()
            found = P.names_left_in_text(compiled, text, True, False)
            return found, time.perf_counter() - started
        finally:
            gc.enable()

    @staticmethod
    def _corpus(forms=100, size=1_000_000):
        import random
        rng = random.Random(1300)
        letters = "abcdefghijklmnopqrstuvwxyz"
        names = set()
        while len(names) < forms:
            names.add(rng.choice(letters).upper() + "".join(
                rng.choice(letters) for _ in range(rng.randint(4, 7))))
        names = sorted(names)
        mapping = [{"original": name, "pseudonym": f"Pseudonym{index:03d}"}
                   for index, name in enumerate(names)]
        vocabulary = ["the", "and", "said", "interview", "because", "then",
                      "we", "it", "was", "a", "long", "day", "at", "work",
                      "home", "they", "told", "me", "about", "it"] * 20
        vocabulary += names + [name + "son" for name in names[:30]] + [
            name.upper() for name in names[:30]] + [
            name + "_P01" for name in names[:10]]
        pieces, written = [], 0
        while written < size:
            word = rng.choice(vocabulary)
            pieces.append(word)
            written += len(word) + 1
        return mapping, " ".join(pieces)

    @classmethod
    def measure(cls):
        """The rate probe's measurements, in THIS interpreter: a dict.

        The same megabyte at a hundred forms and at one, as it stands
        (ASCII) and with curly quotes and apostrophes (fix round 2, B-1: a
        rate taken on ASCII alone cannot see what a transcript from a word
        processor costs), and at one form with its common words in
        Cyrillic, Japanese and decomposed accents; the cheap question
        past the budgets on half a
        megabyte where no name shows (B-2); and a dense text, the name
        repeated as one run on the typed path, the dearest ordinary match
        there is."""
        mapping, text = cls._corpus()
        curly = text.replace("said", "“said”").replace(
            " it ", " it’s ")
        many = P.Compiled(P.validate_mapping(mapping))
        one = P.Compiled(P.validate_mapping(mapping[:1]))
        for compiled in (many, one):
            compiled.text_lookup()
        found, elapsed = cls._timed(many, text)
        _, one_elapsed = cls._timed(one, text)
        curly_found, curly_elapsed = cls._timed(many, curly)
        _, curly_one = cls._timed(one, curly)
        # The other classes the constants probe measured, at one form,
        # where the per-character term is what the count spends: the same
        # megabyte with its common words in Cyrillic, in Japanese and in
        # decomposed accented Latin (the names stay as they are).
        import re
        import unicodedata
        common = ["the", "and", "said", "interview", "because", "then",
                  "we", "it", "was", "a", "long", "day", "at", "work",
                  "home", "they", "told", "me", "about"]
        scripts = {
            "Cyrillic": "и и сказал интервью потому потом мы это был а "
                        "долгий день на работе дома они рассказали мне об",
            "Japanese": "そして と 言った 面接 なぜなら その後 私たち それ だった ある "
                        "長い 日 で 仕事 家 彼ら 話した 私に について",
            "decomposed accents": unicodedata.normalize(
                "NFD", "thé ànd séid întérviéw bécàusé thén wé ït wàs à "
                       "lóng dày àt wörk hómé théy töld mé àbóut")}
        pattern = re.compile(r"\b(" + "|".join(common) + r")\b")
        other_one = {}
        for label, words in scripts.items():
            table = dict(zip(common, words.split()))
            converted = pattern.sub(lambda m: table[m.group(1)], text)
            _, seconds = cls._timed(one, converted)
            other_one[label] = seconds * 1000 / (len(converted) / 1e6)
        import random
        rng = random.Random(1301)
        words = ["the", "and", "“said”", "interview", "it’s",
                 "because", "then", "a", "long", "day", "at", "work"]
        clean = " ".join(rng.choice(words) for _ in range(90_000))
        assert not many.carries_a_name(clean)
        import gc
        import time
        gc.collect()
        gc.disable()
        try:
            started = time.perf_counter()
            many.carries_a_name(clean)
            check_elapsed = time.perf_counter() - started
        finally:
            gc.enable()
        pair = P.Compiled(P.validate_mapping(
            [{"original": "Ab", "pseudonym": "Xyz"}]))
        pair.text_lookup()
        dense = "ab" * 50_000
        dense_found, dense_elapsed = cls._timed(pair, dense)
        return {
            "megabytes": len(text) / 1e6, "curly_megabytes": len(curly) / 1e6,
            "elapsed": elapsed, "one_elapsed": one_elapsed,
            "curly_elapsed": curly_elapsed, "curly_one": curly_one,
            "other_one": other_one,
            "check_elapsed": check_elapsed,
            "check_work": P.residue_work(many, clean),
            "check_megabytes": len(clean) / 1e6,
            "dense_chars": len(dense), "dense_elapsed": dense_elapsed,
            "dense_matches": dense_found["matches"],
            "wide": found["occurrences"]["wide"],
            "curly_wide": curly_found["occurrences"]["wide"],
            "unattributed": found["unattributed"]
            + curly_found["unattributed"]}

    @staticmethod
    def line(m):
        """The summary line, and the worst case of the three budgets."""
        at_100 = m["elapsed"] * 1000 / m["megabytes"]
        at_1 = m["one_elapsed"] * 1000 / m["megabytes"]
        unit = max((at_100 - at_1) / 99, 1e-6)
        term = at_1 / unit - 1
        curly_100 = m["curly_elapsed"] * 1000 / m["curly_megabytes"]
        curly_1 = m["curly_one"] * 1000 / m["curly_megabytes"]
        curly_unit = max((curly_100 - curly_1) / 99, 1e-6)
        curly_term = curly_1 / curly_unit - 1
        others = ", ".join(f"{label} {ms:.1f}"
                           for label, ms in m["other_one"].items())
        worst_term = max([curly_term] + [ms / curly_unit - 1 for ms in
                                         m["other_one"].values()])
        check_unit = m["check_elapsed"] * 1000 / (m["check_work"] / 1e6)
        check_100 = m["check_elapsed"] * 1000 / m["check_megabytes"]
        per_match = max(m["dense_elapsed"] * 1000 - m["dense_chars"] / 1e6 * (
            1 + P.RESIDUE_WORK_PER_CHARACTER) * unit, 0) / m["dense_matches"]
        work_s = P.MAX_RESIDUE_SCAN_WORK / 1e6 * max(unit, curly_unit) / 1000
        check_s = P.MAX_RESIDUE_CHECK_WORK / 1e6 * check_unit / 1000
        match_s = P.MAX_RESIDUE_SCAN_MATCHES * per_match / 1000
        return (f"{at_100 / 100:.3f} ms per MB per surface form at 100 "
                f"forms, {at_1:.1f} ms per MB at one form (per-character "
                f"term {term:.2f}, model {P.RESIDUE_WORK_PER_CHARACTER}); "
                f"with curly quotes {curly_100 / 100:.3f} and "
                f"{curly_1:.1f}; at one form {others} ms per MB (the worst "
                f"per-character term not ASCII {worst_term:.2f}, model "
                f"{P.RESIDUE_WORK_PER_CHARACTER_NON_ASCII}); the question "
                f"past the budgets {check_100 / 100:.3f} ms per MB per "
                f"surface form at 100 forms; worst case at the full budgets "
                f"about {work_s + check_s + match_s:.2f} s (work "
                f"{work_s:.2f} s, check {check_s:.2f} s, matches "
                f"{match_s:.2f} s)")

    def test_a_megabyte_at_a_hundred_forms(self, record_property):
        """The rate probe, and the worst case ruling 7 is checked on.

        Measured in a FRESH interpreter (fix round 2): inside the full
        suite the same count read three and a half times slower (14.8 ms
        per MB per form against 4.3, the collector already paused), from
        the suite's own heap, which a server process does not have, so
        the line CI publishes was the suite's, not the tool's. From the
        measurements, the rate per unit of work, the per-character term of
        each class of text and the worst case of each budget follow; the
        line written into the run's summary carries them, so every CI log
        says whether the budgets are "about two seconds" on its
        platform."""
        import json
        import subprocess
        here = Path(__file__).resolve()
        probe = ("import sys, json; sys.path[:0] = ['src', 'tests']; "
                 "import test_v012_pseudonymise_engine as t; "
                 "print(json.dumps("
                 "t.TestTheFileTextCountStaysCheap.measure()))")
        result = subprocess.run([sys.executable, "-B", "-c", probe],
                                cwd=str(here.parents[1]),
                                capture_output=True, text=True, timeout=300)
        assert result.returncode == 0, result.stderr[-2000:]
        m = json.loads(result.stdout.strip().splitlines()[-1])
        line = self.line(m)
        record_property("file_text_rate", line)
        sys.stderr.write(f"\nfile-text count rate: {line}\n")
        assert m["wide"] > 1000 and m["curly_wide"] > 1000
        assert m["unattributed"] == 0
        # 50,000 matches of one spelling, whose first sight is charged
        # `RESIDUE_FIRST_SIGHT_MATCHES` (fix round 2).
        assert m["dense_matches"] == 50_000 - 1 + \
            P.RESIDUE_FIRST_SIGHT_MATCHES
        assert m["elapsed"] < self.CEILING_SECONDS, (
            f"{m['elapsed']:.2f} s for 1 MB at 100 forms: the file-text "
            f"count has lost its ungrouped pass")
        # B-1's guard, a ratio in one fresh process so the machine cancels:
        # a one-form count of the curly megabyte against the plain one.
        # 1.6 to 2.0 since the unseen characters are stripped by one
        # character class; 3.1 (3.11.13) to 4.0 (3.13.5) with the
        # `str.translate` table it replaced.
        ratio = (m["curly_one"] / m["curly_megabytes"]) / (
            m["one_elapsed"] / m["megabytes"])
        assert ratio < self.CURLY_RATIO_CEILING, (
            f"a curly-quoted megabyte costs {ratio:.1f} times a plain one "
            f"at one form: the reader's reading of text that is not ASCII "
            f"has become dear again")

    def test_the_guard_that_tells_the_two_shapes_apart(self):
        """The absolute guard: the rate probe above does not discriminate
        on its own (at 100 forms one capture group per alternative
        measured 2.4 s for its pass alone over this megabyte, under the
        ceiling). The curve is steep in the number of forms, so the same
        ceiling is applied at a quarter of a megabyte and 400 forms: 0.25
        s with the key lookup and 5.4 s with capture groups on the
        machine that measured it."""
        mapping, text = self._corpus(forms=400, size=250_000)
        compiled = P.Compiled(P.validate_mapping(mapping))
        assert len(compiled.forms) == 400
        compiled.text_lookup()
        found, elapsed = self._timed(compiled, text)
        assert found["occurrences"]["wide"] > 100
        assert elapsed < self.CEILING_SECONDS, (
            f"{elapsed:.2f} s for a quarter of a megabyte at 400 forms: "
            f"the file-text count has lost its ungrouped pass")

    # The ratio guard (fix round 1, QA-9): the count against a plain
    # ungrouped `findall` of the same pattern over the same text, both in
    # this process, so the margin does not depend on the machine. Measured
    # at 400 forms: the count 1.3 times the plain pass on both
    # interpreters, one capture group per alternative 27 times.
    RATIO_CEILING = 5.0

    def test_the_count_is_a_small_multiple_of_one_plain_pass(self):
        mapping, text = self._corpus(forms=400, size=250_000)
        compiled = P.Compiled(P.validate_mapping(mapping))
        compiled.text_lookup()
        seen = P._reader_sees(text)
        import gc
        import time
        gc.collect()
        gc.disable()
        try:
            plain = min(self._stopwatch(
                lambda: compiled.detector._direct.findall(seen))
                for _ in range(3))
            count = min(self._stopwatch(
                lambda: P.names_left_in_text(compiled, text, True, False))
                for _ in range(3))
        finally:
            gc.enable()
        assert count / plain < self.RATIO_CEILING, (
            f"the count took {count / plain:.1f} times one plain pass: it "
            f"has lost its ungrouped pass")

    @staticmethod
    def _stopwatch(fn):
        import time
        started = time.perf_counter()
        fn()
        return time.perf_counter() - started

    def test_the_rate_reaches_the_summary_ci_prints(self):
        """Ruling 7's check reads the rate from the six CI logs, and CI
        runs `pytest -ra -q`, which prints nothing of a passing test's
        own output. The rate is written into the run's summary by
        `pytest_terminal_summary` in tests/conftest.py; driven here, the
        way CI runs, in a separate interpreter."""
        import subprocess
        here = Path(__file__).resolve()
        target = (f"{here}::TestTheFileTextCountStaysCheap::"
                  f"test_a_megabyte_at_a_hundred_forms")
        result = subprocess.run(
            [sys.executable, "-B", "-m", "pytest", "-ra", "-q",
             "-p", "no:cacheprovider", target],
            cwd=str(here.parents[1]), capture_output=True, text=True,
            timeout=300)
        assert result.returncode == 0, result.stdout[-2000:]
        lines = [line for line in result.stdout.splitlines()
                 if line.startswith("file-text count rate: ")]
        assert len(lines) == 1, result.stdout[-2000:]
        assert " ms per MB per surface form at 100 forms, " in lines[0]
        assert " ms per MB at one form " in lines[0]
        # Fix round 2: text that is not ASCII (B-1), the question past the
        # budgets (B-2), and the third budget in the worst case.
        assert "; with curly quotes " in lines[0]
        assert "; at one form Cyrillic " in lines[0]
        assert ", Japanese " in lines[0]
        assert ", decomposed accents " in lines[0]
        assert "(the worst per-character term not ASCII " in lines[0]
        assert "; the question past the budgets " in lines[0]
        assert "; worst case at the full budgets about " in lines[0]
        assert ", check " in lines[0]
        assert "test passed)" in lines[0]


def _two_thousand_forms(mode="insensitive", stem="Zq", first="Ali"):
    """1,997 forms, the documented ceiling's shape (entries of four, the
    last entry alone and last in pattern order), as the bounds lane built
    them for B-3."""
    def word(i):
        a, b = divmod(i, 26 * 26)
        b, c = divmod(b, 26)
        return stem + chr(97 + a % 26) + chr(97 + b) + chr(97 + c)
    entries, made, i = [], 0, 0
    while made + 4 <= 1996 and len(entries) < P.MAX_ENTRIES - 1:
        entries.append({"original": word(4 * i),
                        "pseudonym": "Pp" + word(4 * i)[2:],
                        "variants": [word(4 * i + k) for k in (1, 2, 3)]})
        made += 4
        i += 1
    entries.append({"original": first, "pseudonym": "Xyz"})
    return P.Compiled(P.validate_mapping(entries, mode))


class TestAMatchIsPlacedOncePerSpelling:
    """The re-verification's B-3: under an insensitive mode a spelling
    such as `ALİ` misses both of `entry_for`'s dictionaries and fell to a
    `re.fullmatch` per form, recompiled once the forms outnumber `re`'s
    cache: 12 to 13.5 ms a match at 1,997 forms, 32.8 s and 249.6 s for
    one preview inside both budgets, and 66 to 84 s to plan the rewrite
    of a 90,000-character transcript. Decided once per spelling now,
    through `ignorecase_key`, against patterns held compiled."""

    TURKISH = "ALİ: evet, dün geldim ve toplantıya katıldım. "

    def test_the_key_is_the_modules_own_comparison(self):
        """`ignorecase_key(a) == ignorecase_key(b)` exactly when a literal
        `a` matches `b` under re.IGNORECASE: every cased code point
        against every cased one, and no uncased code point matched by a
        cased literal or sharing its key. The whole range was checked on
        3.13.5, 3.11.13 and 3.10.16 (the fix report's evidence); this is
        the part a changed interpreter would move."""
        cased = [chr(c) for c in range(0x110000)
                 if not 0xD800 <= c <= 0xDFFF and P._SRE_ISCASED(c)]
        all_cased = "".join(cased)
        keys = {ch: P.ignorecase_key(ch) for ch in cased}
        for ch in cased:
            matched = set(re.findall(re.escape(ch), all_cased, re.IGNORECASE))
            assert matched == {other for other in cased
                               if keys[other] == keys[ch]}, ch
        any_cased = re.compile(
            "[" + "".join(re.escape(ch) for ch in cased) + "]", re.IGNORECASE)
        every = "".join(chr(c) for c in range(0x110000)
                        if not 0xD800 <= c <= 0xDFFF)
        assert set(any_cased.findall(every)) <= set(cased)
        assert set(keys.values()) <= set(cased)

    def test_a_spelling_is_placed_once(self, monkeypatch):
        compiled = _two_thousand_forms()
        calls = []
        real = P.Compiled.first_full_match

        def counted(self, matched):
            calls.append(matched)
            return real(self, matched)

        monkeypatch.setattr(P.Compiled, "first_full_match", counted)
        found = P.names_left_in_text(compiled, "ALİ: evet.\n" * 500, True,
                                     False)
        assert found["occurrences"]["whole_word"] == 500
        assert calls == ["ALİ"]
        assert [compiled.entry_for("ALİ") for _ in range(3)] == [
            len(compiled.mapping.entries) - 1] * 3
        assert calls == ["ALİ"]

    def test_a_form_pattern_is_compiled_once(self):
        """Held by the compiled mapping, not by `re`'s cache of 512, which
        2,000 forms overflow: the first form's pattern is the same object
        after every other form's has been asked for."""
        compiled = _two_thousand_forms()
        first = compiled.form_pattern(compiled.forms[0][0])
        for form, _ in compiled.forms:
            compiled.form_pattern(form)
        assert compiled.form_pattern(compiled.forms[0][0]) is first

    # Generous: measured 0.14 s to plan and about 0.3 s to count on this
    # Mac, against 66 to 84 s and 32.8 s before; a machine forty times
    # slower passes, and the shape before the fix cannot.
    CEILING_SECONDS = 6.0

    def test_the_rewrite_is_planned_quickly_at_two_thousand_forms(self):
        import time
        compiled = _two_thousand_forms()
        text = (self.TURKISH * (90_000 // len(self.TURKISH) + 1))[:90_000]
        started = time.perf_counter()
        found = P.find_replacements(compiled, text)
        elapsed = time.perf_counter() - started
        assert len(found) == text.count("ALİ")
        assert elapsed < self.CEILING_SECONDS, elapsed

    def test_distinct_spellings_are_placed_quickly(self):
        """Every label a NEW spelling, so no memo helps: the forms a
        spelling can belong to are found through `ignorecase_key`, one
        dictionary lookup, rather than by a full match against every form
        (which, at 12 ms a spelling, is 18 s for these 1,500 labels)."""
        import time
        compiled = _two_thousand_forms(stem="i")
        labels = []
        for form, _ in compiled.forms[:500]:
            rest = form[1:]
            labels += ["\u0130" + rest.upper(), "\u0130" + rest,
                       "\u0130" + rest.capitalize()]
        text = "".join(f"{label}: evet.\n" for label in labels)
        started = time.perf_counter()
        found = P.names_left_in_text(compiled, text, True, False)
        elapsed = time.perf_counter() - started
        assert found["occurrences"]["whole_word"] == len(labels) == 1500
        assert elapsed < self.CEILING_SECONDS, elapsed

    def test_the_count_is_quick_at_two_thousand_forms(self):
        import time
        compiled = _two_thousand_forms()
        text = (self.TURKISH * (90_000 // len(self.TURKISH) + 1))[:90_000]
        started = time.perf_counter()
        found = P.names_left_in_text(compiled, text, True, False)
        elapsed = time.perf_counter() - started
        assert found["occurrences"]["whole_word"] == text.count("ALİ")
        assert elapsed < self.CEILING_SECONDS, elapsed


class TestPseudonymsContainingAName:
    """The static check of item 5 (ruling 8), at the engine."""

    @staticmethod
    def _check(mapping, mode="exact"):
        return P.pseudonyms_containing_a_name(
            P.Compiled(P.validate_mapping(mapping, mode)))

    def test_the_four_mappings_of_the_counts_study(self):
        assert self._check([{"original": "Thomas", "pseudonym": "Alex"},
                            {"original": "Mary", "pseudonym": "a Thomas b"}]
                           ) == [{"entry": 1, "pseudonym": "a Thomas b",
                                  "contains_entry": 0, "form": "Thomas"}]
        assert self._check([{"original": "Thomas",
                             "pseudonym": "not Thomas"}]) == [
            {"entry": 0, "pseudonym": "not Thomas", "contains_entry": 0,
             "form": "Thomas"}]
        assert self._check([{"original": "Thomas", "pseudonym": "Alex"},
                            {"original": "Mary", "pseudonym": "xThomasx"}]
                           ) == []
        assert self._check([{"original": "Smith", "pseudonym": "Jones"},
                            {"original": "Thomas Smith",
                             "pseudonym": "Alex Smith"}]) == [
            {"entry": 1, "pseudonym": "Alex Smith", "contains_entry": 0,
             "form": "Smith"}]

    def test_each_finding_once_and_the_form_as_the_mapping_spells_it(self):
        found = self._check(
            [{"original": "Smith", "pseudonym": "Jones",
              "variants": ["Smyth"]},
             {"original": "Thomas", "pseudonym": "Smith and SMITH and Smyth"}],
            mode="insensitive")
        assert found == [
            {"entry": 1, "pseudonym": "Smith and SMITH and Smyth",
             "contains_entry": 0, "form": "Smith"},
            {"entry": 1, "pseudonym": "Smith and SMITH and Smyth",
             "contains_entry": 0, "form": "Smyth"}]

    def test_the_case_mode_is_the_rewriters(self):
        mapping = [{"original": "Smith", "pseudonym": "Jones"},
                   {"original": "Thomas", "pseudonym": "Alex SMITH"}]
        assert self._check(mapping, "exact") == []
        assert self._check(mapping, "insensitive")[0]["form"] == "Smith"


class TestPseudonymsWithheld:
    """The predicate of the owner's F-1 ruling, at the engine."""

    @staticmethod
    def _withheld(mapping):
        return P.pseudonyms_withheld(
            P.Compiled(P.validate_mapping(mapping)))

    def test_a_whole_word_its_own_original_and_a_longer_word(self):
        assert self._withheld([{"original": "Smith", "pseudonym": "Jones"},
                               {"original": "Thomas Smith",
                                "pseudonym": "Alex Smith"}]) == (1,)
        assert self._withheld([{"original": "Thomas",
                                "pseudonym": "Thomas Jr"}]) == (0,)
        # The static check of item 5 cannot see this one; the union can.
        assert self._withheld([{"original": "Thomas",
                                "pseudonym": "Thomasina"}]) == (0,)
        assert P.pseudonyms_containing_a_name(P.Compiled(P.validate_mapping(
            [{"original": "Thomas", "pseudonym": "Thomasina"}]))) == []

    def test_a_pseudonym_only_the_rewriter_reads_as_carrying_a_name(self):
        """The re-verification's DR-1: a pseudonym carrying a mapped name
        before a combining mark ("Rene" then U+0301, the decomposed
        spelling of René, when "Rene" is mapped). NFKC composes the mark
        onto the "e", so the detector does not see "Rene"; the rewriter's
        whole-word rule does. Withheld by the union's rewriter half."""
        mapping = [{"original": "Rene", "pseudonym": "Paul"},
                   {"original": "Thomas", "pseudonym": "Rene\u0301 Martin"}]
        compiled = P.Compiled(P.validate_mapping(mapping))
        assert not compiled.detector.contains("Rene\u0301 Martin")
        assert compiled.pattern.search("Rene\u0301 Martin")
        assert self._withheld(mapping) == (1,)

    def test_a_clean_mapping_and_the_declared_price(self):
        assert self._withheld([{"original": "Thomas", "pseudonym": "Alex"},
                               {"original": "Mary Ann",
                                "pseudonym": "Sam"}]) == ()
        # The wide reading's price, declared: a short name withholds a
        # pseudonym that merely contains its letters.
        assert self._withheld([{"original": "Ed", "pseudonym": "Fred"}]) \
            == (0,)


class TestTheRateReachesThePublicRunPage:
    """Fix round 1 (the lead's addition to the owner's F-1 ruling): the CI
    workflow copies the `file-text count rate:` line into the step
    summary, so ruling 7's freezing check reads from the public run page
    without admin rights. Read from the workflow's own run step."""

    def test_the_step_summary_copies_the_rate_line(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github" /
                    "workflows" / "ci.yml").read_text(encoding="utf-8")
        step = workflow[workflow.index("- name: Run test suite"):
                        workflow.index("- name: Upload pytest output")]
        # The block the step pipes into the summary: from its opening
        # brace to the redirect.
        closing = step.index('} >> "$GITHUB_STEP_SUMMARY"')
        summary = step[step.rindex("{\n", 0, closing):closing]
        assert summary.count("echo") >= 2       # the block, not a fragment
        assert 'grep -E "^file-text count rate: " pytest_output.txt' in \
            summary

    def test_the_rate_is_also_a_check_run_annotation(self):
        """The lead's follow-on (fix round 2, G5): GitHub hides a job's
        step summary from signed-out visitors, and a check run's
        annotations are public through the API. So the step also prints
        the line as a `::notice title=file-text count rate::` workflow
        command, OUTSIDE the block piped into the summary (a workflow
        command inside it would be written to the summary as text)."""
        workflow = (Path(__file__).resolve().parents[1] / ".github" /
                    "workflows" / "ci.yml").read_text(encoding="utf-8")
        step = workflow[workflow.index("- name: Run test suite"):
                        workflow.index("- name: Upload pytest output")]
        closing = step.index('} >> "$GITHUB_STEP_SUMMARY"')
        outside = step[closing:]
        assert ("sed -n 's/^file-text count rate: "
                "/::notice title=file-text count rate::/p' "
                "pytest_output.txt") in outside
        assert outside.index("::notice title=file-text count rate::") < \
            outside.index("exit $code")
