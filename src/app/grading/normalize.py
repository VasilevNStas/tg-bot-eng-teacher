"""Answer normalization for deterministic grading (ADR-016).

Normalization makes user input and expected answers comparable before any
exact/fuzzy check. It deliberately keeps apostrophes and hyphens because they
are meaningful inside English words (``don't``, ``well-known``).
"""

from __future__ import annotations

import re
import unicodedata

# Telegram clients send different Unicode apostrophes; map them all to ASCII
# so "don't" written with different apostrophes normalizes the same.
_APOSTROPHES = str.maketrans(
    {
        "\u2019": "'",
        "\u2018": "'",
        "\u201b": "'",
        "\u00b4": "'",
        "`": "'",
    }
)
# Typographic dashes appear in copied text and should not change the answer.
_DASHES = str.maketrans({"\u2013": "-", "\u2014": "-", "\u2212": "-"})
# Generic rule for contractions such as ``isn't`` -> ``is not``.
_NEGATED_CONTRACTION = re.compile(r"\b(\w+)n't\b")


def normalize_answer(text: str) -> str:
    """Normalize case, Unicode, punctuation, whitespace and common contractions.

    Contractions are expanded so that ``don't`` and ``do not`` are treated as
    the same answer. This happens before punctuation removal, while the
    apostrophe is still available to recognize the contraction.
    """
    value = unicodedata.normalize("NFKC", text).casefold()
    value = value.translate(_APOSTROPHES).translate(_DASHES)

    # Irregular contractions cannot be produced by the generic ``n't`` rule.
    value = re.sub(r"\bcan't\b", "can not", value)
    value = re.sub(r"\bcannot\b", "can not", value)
    value = re.sub(r"\bwon't\b", "will not", value)
    value = re.sub(r"\bshan't\b", "shall not", value)
    # ``isn't`` -> ``is not``, ``shouldn't`` -> ``should not``, etc.
    value = _NEGATED_CONTRACTION.sub(r"\1 not", value)
    value = re.sub(r"\b(\w+)'re\b", r"\1 are", value)
    value = re.sub(r"\b(\w+)'ve\b", r"\1 have", value)
    value = re.sub(r"\b(\w+)'ll\b", r"\1 will", value)
    value = re.sub(r"\b(\w+)'d\b", r"\1 would", value)
    value = re.sub(r"\b(\w+)'m\b", r"\1 am", value)

    # Drop punctuation but keep word characters, spaces, apostrophes and
    # hyphens; then collapse repeated whitespace and trim leading/trailing
    # quotes or dashes.
    value = re.sub(r"[^\w\s'-]", " ", value, flags=re.UNICODE)
    value = re.sub(r"\s+", " ", value).strip()
    return value.strip(" -'")
