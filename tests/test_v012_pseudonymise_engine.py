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
        assert mapped.change == P.RESIZED

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
    """`snapped`, `resized`, `shifted`, `unchanged`: one vocabulary.

    The distinction matters beyond presentation: the hidden-coder rule
    (D1 3.7, owner ruling X1) exempts a PURE SHIFT and requires the
    override for a resize, a snap or a deletion, so a row put in the
    wrong class is a row whose coder's consent was decided wrongly.
    """

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
        rate = self._count(
            {"text": _NAMED_TEXT},
            lambda text: bool(P.find_replacements(self.TOMANN, text)))
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
        the boundary for every shape of name."""
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
        self._count({"sample": _BOUNDARY_SAMPLE}, look, examples=1200)
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
    BETWEEN_TWO_WORDS = [
        ("zero-width space", "\u200b"),
        ("soft hyphen", "\u00ad"),
        ("word joiner", "\u2060"),
        ("byte order mark", "\ufeff"),
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
    # is the file's own. A transcription slip in any range is red here.
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

    def test_the_price_of_the_wider_reading_is_paid_knowingly(self):
        """An entry for "Tom" makes "tomorrow" count. A rule that
        catches `ThomasB.txt` cannot spare `tomorrow`, and of the two
        mistakes only one ships a participant's name. Pinned so that
        nobody "fixes" it into a boundary test by accident."""
        detector = self._detector()
        assert detector.contains("tomorrow.txt") is True
        assert detector.contains("thomasina notes") is True
