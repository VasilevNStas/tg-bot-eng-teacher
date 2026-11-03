#!/usr/bin/env python3
"""Build minimal vocabulary bank YAML files from the parsed HTML word list."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

DEFAULT_INPUT = Path("content/private/vocabulary/3000_words.json")
DEFAULT_OUTPUT = Path("content/vocabulary")
DEFAULT_SOURCE = "3000_words.html"
DEFAULT_LICENSE = "unknown"


def build_entries(source: dict[str, Any]) -> list[dict[str, Any]]:
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
            "position": position,
            "frequency": frequency,
            "term": term,
        }
        transcription = item.get("transcription")
        if transcription:
            entry["transcription"] = str(transcription).strip()
        entry["translation_ru"] = translation
        entries.append(entry)

    entries.sort(key=lambda entry: entry["position"])
    return entries


def split_blocks(entries: list[dict[str, Any]], block_size: int) -> list[list[dict[str, Any]]]:
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
    first = entries[0]["position"]
    last = entries[-1]["position"]
    output = output_dir / f"core_words_{first:04d}_{last:04d}.yaml"
    payload = {
        "source": source,
        "license": license_name,
        "words": entries,
    }
    output.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, width=1000),
        encoding="utf-8",
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--block-size", type=int, default=1000)
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--license", dest="license_name", default=DEFAULT_LICENSE)
    args = parser.parse_args()

    source = json.loads(args.input.read_text(encoding="utf-8"))
    entries = build_entries(source)
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
