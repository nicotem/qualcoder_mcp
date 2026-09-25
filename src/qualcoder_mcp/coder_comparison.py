# SPDX-License-Identifier: LGPL-3.0-or-later
"""Coder-comparison statistics (v0.12 B3, D2).

QualCoder shows these numbers in two dialogs (`reports.py:820-1395` and
`report_compare_coder_file.py:52-1143` at the pinned master 9bddf17).
This module computes them from character sets rather than from segment
counts, and reproduces QualCoder's own expressions exactly, in its own
order, so that where the counts agree the values are bit-identical.

Two names, deliberately. `kappa_qualcoder` is QualCoder's "Kappa" column
reproduced from the same counts; it is NOT Cohen's kappa (its chance term
is a product of four proportions over the coded characters only, and its
own docstring at `reports.py:1124` describes a different formula from the
one the code computes at `:1147`). `kappa_cohen` is the textbook
statistic over every character in scope. Both are always present, so no
reader has to guess which one they are looking at, and neither is ever
called plain `kappa` for our own numbers.
"""

from typing import Any, Dict, List, Optional, Sequence

# The undefined-value reasons. Never the string "zerodiv", which is what
# QualCoder's dialog displays in the Kappa column (reports.py:1140,
# :1006): a reason belongs in a field a reader can act on, not in a
# number's place.
NOTE_NO_CHARACTERS = "no characters in scope"
NOTE_NO_VARIANCE = (
    "undefined: neither coder applied this code in the selected scope "
    "(no variance)")
NOTE_BOTH_CODED_ALL = (
    "kappa_cohen undefined: both coders coded every character in scope "
    "(no variance); kappa_qualcoder is 1.0 by QualCoder's formula")
MEAN_COHEN_FEWER_CODES_NOTE = (
    "codes_included counts the codes behind kappa_qualcoder. kappa_cohen "
    "is undefined for codes where both coders coded every character in "
    "scope, so its mean is over codes_included_kappa_cohen codes "
    "instead. The per-code rows say which.")


def kappa_qualcoder(coded_a: int, coded_b: int, both: int) -> Optional[float]:
    """QualCoder's "Kappa" column, expression for expression.

    A verbatim transcription of `reports.py:1140-1151` (identical at
    `report_compare_coder_file.py:818-830` and at
    `3.8.2:reports.py:812-820`), including the order of the operations,
    so the floating-point result is the same one the dialog shows. The
    `ZeroDivisionError` branch fires only when neither coder applied the
    code, which is the only undefined case here: the chance term is at
    most 1/16, so `1 - Pe` is never zero.
    """
    try:
        unique_codings = coded_a + coded_b - both
        Po = both / unique_codings
        Pyes = coded_a / unique_codings * coded_b / unique_codings
        Pno = ((unique_codings - coded_a) / unique_codings
               * (unique_codings - coded_b) / unique_codings)
        Pe = Pyes * Pno
        return round((Po - Pe) / (1 - Pe), 4)
    except ZeroDivisionError:
        return None


def kappa_cohen(characters: int, coded_a: int, coded_b: int,
                both: int) -> Optional[float]:
    """Cohen's kappa on the 2x2 table over every character in scope.

    Undefined exactly when there is no variance to correct for: both
    coders coded nothing, or both coded everything. Sensitive to the
    amount of uncoded text, which is the prevalence effect QualCoder's
    author was reacting to when they wrote their own formula.
    """
    n = characters
    if n <= 0:
        return None
    neither = n - coded_a - coded_b + both
    Po = (both + neither) / n
    Pe = (coded_a / n) * (coded_b / n) + ((n - coded_a) / n) * ((n - coded_b) / n)
    if Pe == 1:
        return None
    return round((Po - Pe) / (1 - Pe), 4)


def statistics(characters: int, coded_a: int, coded_b: int,
               both: int) -> Dict[str, Any]:
    """Every field of one comparison row, from four counts.

    Percentages use QualCoder's expressions and its 2-decimal rounding;
    the kappas use 4 decimals, as its own code does.
    """
    a_only = coded_a - both
    b_only = coded_b - both
    union = coded_a + coded_b - both
    neither = characters - union
    row: Dict[str, Any] = {
        "characters": characters,
        "coded_a": coded_a,
        "coded_b": coded_b,
        "both": both,
        "a_only": a_only,
        "b_only": b_only,
        "neither": neither,
    }
    if characters <= 0:
        row.update({
            "agreement_pct": None, "dual_coded_pct": None,
            "uncoded_pct": None, "disagreement_pct": None,
            "agree_coded_only_pct": None,
            "kappa_qualcoder": None, "kappa_cohen": None,
            "kappa_note": NOTE_NO_CHARACTERS,
        })
        return row

    agreement = round(100 * (both + neither) / characters, 2)
    row["agreement_pct"] = agreement
    row["dual_coded_pct"] = round(100 * both / characters, 2)
    row["uncoded_pct"] = round(100 * neither / characters, 2)
    row["disagreement_pct"] = round(100 - agreement, 2)
    row["agree_coded_only_pct"] = (round(100 * both / union, 2)
                                   if union else None)
    row["kappa_qualcoder"] = kappa_qualcoder(coded_a, coded_b, both)
    row["kappa_cohen"] = kappa_cohen(characters, coded_a, coded_b, both)
    if union == 0:
        row["kappa_note"] = NOTE_NO_VARIANCE
    elif coded_a == coded_b == characters:
        row["kappa_note"] = NOTE_BOTH_CODED_ALL
    return row


# ---------------------------------------------------------------------------
# The GUI-divergence port (D2 3.8): QualCoder's own multiplicity counting
# ---------------------------------------------------------------------------
# This exists ONLY so a result can say what QualCoder's dialog would show
# for the same data when a coder's own segments of one code overlap, and
# so the parity tests can compare the two. It is never the headline
# number. A verbatim port of the counting loop and statistics of
# `reports.py:1061-1152` (cited also to `3.8.2:reports.py:705-822`).

def qualcoder_report_values(text_length: int,
                            spans_a: Sequence[Sequence[int]],
                            spans_b: Sequence[Sequence[int]]) -> Dict[str, Any]:
    """What QualCoder's Coder comparison report would show.

    A verbatim port of `reports.py:1061-1108` at 9bddf17 (the same code at
    `3.8.2:reports.py:705-765`), including the details that make it differ
    from ours: ONE shared array is incremented once per coded character
    PER SEGMENT for both coders, so two overlapping segments of the same
    coder push a character to 2; "dual coded" is then literally `count ==
    2`, so such a character is counted as agreement although only one
    coder coded it, and a character covered by three segments is counted
    as neither uncoded, single nor dual and vanishes from the
    percentages. Characters beyond the end of the text are dropped by its
    `IndexError` catch (`:1067-1073`).
    """
    coded0 = coded1 = 0
    char_list = [0] * max(0, int(text_length))
    for pos0, pos1 in spans_a:
        for char in range(int(pos0), int(pos1)):
            if 0 <= char < text_length:
                char_list[char] += 1
                coded0 += 1
    for pos0, pos1 in spans_b:
        for char in range(int(pos0), int(pos1)):
            if 0 <= char < text_length:
                char_list[char] += 1
                coded1 += 1
    uncoded = single_coded = dual_coded = 0
    for char in char_list:
        if char == 0:
            uncoded += 1
        if char == 1:
            single_coded += 1
        if char == 2:
            dual_coded += 1
    values: Dict[str, Any] = {"coded0": coded0, "coded1": coded1,
                              "dual_coded": dual_coded,
                              "single_coded": single_coded,
                              "uncoded": uncoded,
                              "characters": int(text_length)}
    if text_length:
        agreement = round(100 * (dual_coded + uncoded) / text_length, 2)
        values["agreement_pct"] = agreement
        values["dual_coded_pct"] = round(100 * dual_coded / text_length, 2)
        values["uncoded_pct"] = round(100 * uncoded / text_length, 2)
        values["disagreement_pct"] = round(100 - agreement, 2)
        try:
            values["agree_coded_only_pct"] = round(
                100 * dual_coded / (dual_coded + single_coded), 2)
        except ZeroDivisionError:
            # Upstream shows the string "zero div" here; a reason belongs
            # in a note, so ours is null and the note says why (D2 3.7).
            values["agree_coded_only_pct"] = None
    else:
        values["agreement_pct"] = None
        values["dual_coded_pct"] = None
        values["uncoded_pct"] = None
        values["disagreement_pct"] = None
        values["agree_coded_only_pct"] = None
    # The GUI's own column name, reproduced under the GUI's own name
    # inside this disclosure block only (X3).
    values["kappa"] = kappa_qualcoder(coded0, coded1, dual_coded)
    return values


SAME_CODER_OVERLAP_NOTE = (
    "QualCoder's Coder comparison report counts a character once per "
    "segment, so overlapping segments of the same code by the same coder "
    "change its numbers; the values above are what QualCoder would show. "
    "compare_coders counts each character once per coder.")
