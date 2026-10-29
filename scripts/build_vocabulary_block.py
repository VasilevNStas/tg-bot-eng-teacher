#!/usr/bin/env python3
"""Build a vocabulary YAML block from normalized vocabulary JSON."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import yaml

PILOT_STOPWORDS = {
    "another",
    "anyone",
    "anything",
    "everyone",
    "everything",
    "herself",
    "himself",
    "itself",
    "many",
    "mr",
    "mrs",
    "ms",
    "much",
    "myself",
    "nobody",
    "none",
    "nothing",
    "oh",
    "please",
    "someone",
    "something",
    "somebody",
    "though",
    "upon",
    "what",
    "which",
    "while",
    "who",
    "whom",
    "whose",
    "yourself",
    "yourselves",
    "themselves",
    "ourselves",
}

PILOT_TRANSLATION_OVERRIDES = {
    "life": ["жизнь"],
    "soon": ["скоро", "рано"],
}


def level_for_rank(rank: int) -> str:
    if rank <= 500:
        return "A1"
    if rank <= 1500:
        return "A2"
    if rank <= 3000:
        return "B1"
    return "B2"


def slugify(term: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", term.casefold()).strip("-")
    return slug or "word"


def build_block(
    source: dict[str, Any],
    *,
    start: int,
    limit: int,
) -> dict[str, Any]:
    candidates = [
        item
        for item in source["items"]
        if not item["function_word"]
        and item.get("frequency_rank")
        and item["term"].casefold() not in PILOT_STOPWORDS
    ]
    selected = candidates[start : start + limit]
    if not selected:
        raise ValueError("no vocabulary items selected")

    first = start + 1
    last = start + len(selected)
    topic_slug = f"core-words-{first:04d}-{last:04d}"
    topic_level = level_for_rank(selected[0]["frequency_rank"])

    items = []
    for item in selected:
        translations = PILOT_TRANSLATION_OVERRIDES.get(
            item["term"].casefold(), item["translations_ru"]
        )
        payload: dict[str, Any] = {
            "kind": "vocabulary",
            "term": item["term"],
            "translations_ru": translations,
            "accepted_en": [item["term"]],
            "frequency": item["frequency"],
            "frequency_rank": item["frequency_rank"],
            "function_word": item["function_word"],
        }
        if item.get("transcription"):
            payload["transcription"] = item["transcription"]
        if item.get("notes_ru"):
            payload["notes_ru"] = item["notes_ru"]
        items.append(
            {
                "slug": f"word-{slugify(item['term'])}",
                "level": level_for_rank(item["frequency_rank"]),
                "tags": ["purpose:vocabulary"],
                "payload": payload,
            }
        )

    return {
        "topic": {
            "slug": topic_slug,
            "title": f"Core words {first}-{last}",
            "level": topic_level,
            "description": "Пилотный частотный блок для карточек слов",
            "tags": ["purpose:vocabulary"],
        },
        "source": "3000_words.html + 5000_word.rtf (normalized)",
        "license": "unknown",
        "items": items,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()
    if args.start < 0 or args.limit < 1:
        parser.error("--start must be >= 0 and --limit must be > 0")

    source = json.loads(args.input.read_text(encoding="utf-8"))
    block = build_block(source, start=args.start, limit=args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        yaml.safe_dump(block, allow_unicode=True, sort_keys=False, width=1000),
        encoding="utf-8",
    )
    print(f"wrote {len(block['items'])} items to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
