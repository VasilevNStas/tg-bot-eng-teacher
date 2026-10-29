"""Answer normalization for deterministic grading (ADR-016)."""

from __future__ import annotations

import re
import unicodedata

_APOSTROPHES = str.maketrans(
    {
        "\u2019": "'",
        "\u2018": "'",
        "\u201b": "'",
        "\u00b4": "'",
        "`": "'",
    }
)
_DASHES = str.maketrans({"\u2013": "-", "\u2014": "-", "\u2212": "-"})
_NEGATED_CONTRACTION = re.compile(r"\b(\w+)n't\b")


def normalize_answer(text: str) -> str:
    """Normalize case, Unicode, punctuation, whitespace and common contractions."""
    value = unicodedata.normalize("NFKC", text).casefold()
    value = value.translate(_APOSTROPHES).translate(_DASHES)

    value = re.sub(r"\bcan't\b", "can not", value)
    value = re.sub(r"\bcannot\b", "can not", value)
    value = re.sub(r"\bwon't\b", "will not", value)
    value = re.sub(r"\bshan't\b", "shall not", value)
    value = _NEGATED_CONTRACTION.sub(r"\1 not", value)
    value = re.sub(r"\b(\w+)'re\b", r"\1 are", value)
    value = re.sub(r"\b(\w+)'ve\b", r"\1 have", value)
    value = re.sub(r"\b(\w+)'ll\b", r"\1 will", value)
    value = re.sub(r"\b(\w+)'d\b", r"\1 would", value)
    value = re.sub(r"\b(\w+)'m\b", r"\1 am", value)

    value = re.sub(r"[^\w\s'-]", " ", value, flags=re.UNICODE)
    value = re.sub(r"\s+", " ", value).strip()
    return value.strip(" -'")
