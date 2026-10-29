#!/usr/bin/env python3
"""Convert a vocabulary HTML table to normalized JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.curriculum.vocabulary_html import load_vocabulary_html


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        help="Output JSON (default: input path with .json suffix)",
    )
    args = parser.parse_args()

    result = load_vocabulary_html(args.input)
    output = args.output or args.input.with_suffix(".json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result.as_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"parsed {result.count} rows -> {output}")
    if result.missing_ranks:
        print(f"missing ranks: {', '.join(map(str, result.missing_ranks))}")
    if result.duplicate_terms:
        print(f"duplicate terms: {', '.join(result.duplicate_terms)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
