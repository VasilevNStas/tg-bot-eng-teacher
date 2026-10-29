#!/usr/bin/env python3
"""Merge vocabulary HTML and numbered text into normalized JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.curriculum.vocabulary_clean import (
    load_numbered_vocabulary_text,
    normalize_vocabulary,
)
from app.curriculum.vocabulary_html import load_vocabulary_html


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", type=Path, required=True)
    parser.add_argument("--rtf-text", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    result = normalize_vocabulary(
        load_vocabulary_html(args.html),
        load_numbered_vocabulary_text(args.rtf_text),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result.as_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"html: {result.html_count}, rtf: {result.rtf_count}")
    print(f"unique: {result.unique_terms}, merged: {result.merged_terms}")
    print(f"function words: {result.function_words}")
    print(f"output: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
