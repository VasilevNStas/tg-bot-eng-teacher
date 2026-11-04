#!/usr/bin/env python3
"""Build unified vocabulary YAML files from the parsed HTML word list."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import yaml

DEFAULT_INPUT = Path("content/private/vocabulary/3000_words.json")
DEFAULT_OUTPUT = Path("content/vocabulary")
DEFAULT_SOURCE = "3000_words.html"
DEFAULT_LICENSE = "unknown"
_SLUG_CLEAN = re.compile(r"[^a-z0-9]+")


def slugify_term(term: str) -> str:
    """Turn a term into a URL/path-safe slug."""
    slug = _SLUG_CLEAN.sub("-", term.casefold()).strip("-")
    return slug or "word"


def build_entries(source: dict[str, Any]) -> list[dict[str, Any]]:
    """Read source items and validate terms, positions, and translations."""
    items = source.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("input must contain a non-empty 'items' list")

    entries: list[dict[str, Any]] = []
    seen_terms: set[str] = set()
    seen_positions: set[int] = set()
    for item in items:
        position = int(item["position"])
        frequency = int(item["frequency"])
        term = str(item["term"]).strip()
        translation = str(item.get("translation_ru") or "").strip()
        if not term:
            raise ValueError(f"position {position}: empty term")
        if not translation:
            raise ValueError(f"position {position}: empty translation_ru")
        key = term.casefold()
        if key in seen_terms:
            raise ValueError(f"position {position}: duplicate term '{term}'")
        if position in seen_positions:
            raise ValueError(f"position {position}: duplicate position")
        seen_terms.add(key)
        seen_positions.add(position)

        entry: dict[str, Any] = {
            "kind": "vocabulary",
            "term": term,
        }
        transcription = item.get("transcription")
        if transcription:
            entry["transcription"] = str(transcription).strip()
        entry["translation_ru"] = translation
        entry["frequency"] = frequency
        entry["frequency_rank"] = position
        entries.append(entry)

    entries.sort(key=lambda entry: entry["frequency_rank"])
    return entries


def assign_slugs(entries: list[dict[str, Any]]) -> None:
    """Assign stable word slugs, suffixing global collisions with -2, -3."""
    used: set[str] = set()
    for index, entry in enumerate(entries):
        base = f"word-{slugify_term(str(entry['term']))}"
        slug = base
        suffix = 2
        while slug in used:
            slug = f"{base}-{suffix}"
            suffix += 1
        used.add(slug)
        entries[index] = {"slug": slug, **entry}


def split_blocks(entries: list[dict[str, Any]], block_size: int) -> list[list[dict[str, Any]]]:
    """Split ordered entries into fixed-size output blocks."""
    if block_size < 1:
        raise ValueError("block_size must be positive")
    return [entries[index : index + block_size] for index in range(0, len(entries), block_size)]


def write_block(
    output_dir: Path,
    entries: list[dict[str, Any]],
    *,
    source: str,
    license_name: str,
) -> Path:
    """Write one topic + items YAML file and return its path."""
    first = entries[0]["frequency_rank"]
    last = entries[-1]["frequency_rank"]
    topic_slug = f"core-words-{first:04d}-{last:04d}"
    output = output_dir / f"{topic_slug.replace('-', '_')}.yaml"
    payload = {
        "topic": {
            "slug": topic_slug,
            "title": f"Core words {first:04d}-{last:04d}",
        },
        "source": source,
        "license": license_name,
        "items": entries,
    }
    output.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, width=1000),
        encoding="utf-8",
    )
    return output


def main() -> int:
    """Parse CLI arguments and regenerate the core vocabulary blocks."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--block-size", type=int, default=1000)
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--license", dest="license_name", default=DEFAULT_LICENSE)
    args = parser.parse_args()

    source = json.loads(args.input.read_text(encoding="utf-8"))
    entries = build_entries(source)
    assign_slugs(entries)
    blocks = split_blocks(entries, args.block_size)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for block in blocks:
        output = write_block(
            args.output_dir,
            block,
            source=args.source,
            license_name=args.license_name,
        )
        print(f"wrote {len(block)} words to {output}")

    print(f"total: {len(entries)} words in {len(blocks)} blocks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
