"""Build the supplemental vocabulary block from a plain-text word list.

The source is an RTF list of 5000 words. Convert it first:

    textutil -convert txt -output /tmp/5000_word.txt \
        .opencode/educational_materials/words/5000_word.rtf

    uv run python scripts/build_extra_words.py /tmp/5000_word.txt

Each entry is three lines: position, term, translation. Terms already present
in the core vocabulary files are skipped. Duplicate terms inside the input are
merged into one item with translations joined by ``; ``.
"""

from __future__ import annotations

import argparse
import re
import unicodedata
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

from app.curriculum.loader import load_content_file

_SLUG_CLEAN = re.compile(r"[^a-z0-9]+")
_VARIANT_SPLIT = re.compile(r"\s*[;,/]\s*")


def norm(term: str) -> str:
    """Normalize a term for cross-list comparison."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", term)).strip().casefold()


def slugify_term(term: str) -> str:
    """Turn a term into a URL/path-safe slug."""
    slug = _SLUG_CLEAN.sub("-", term.casefold()).strip("-")
    return slug or "word"


def parse_entries(path: Path) -> list[tuple[str, str]]:
    """Parse the three-line numbered word list into (term, translation)."""
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    entries: list[tuple[str, str]] = []
    index = 0
    while index + 2 < len(lines):
        if lines[index].isdigit():
            term, translation = lines[index + 1], lines[index + 2]
            if term and translation:
                entries.append((term, translation))
            index += 3
        else:
            index += 1
    return entries


def load_existing(vocabulary_dir: Path, output: Path) -> tuple[set[str], set[str]]:
    """Collect terms and slugs already present in the vocabulary bank."""
    terms: set[str] = set()
    slugs: set[str] = set()
    for path in sorted(vocabulary_dir.glob("*.yaml")):
        if path.resolve() == output.resolve():
            continue
        data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
        for item in data["items"]:
            terms.add(norm(item["term"]))
            slugs.add(item["slug"])
    return terms, slugs


def merge_missing(
    entries: Iterable[tuple[str, str]], existing_terms: set[str]
) -> list[dict[str, Any]]:
    """Keep only new terms; merge duplicate terms into one entry."""
    merged: dict[str, dict[str, Any]] = {}
    for term, translation in entries:
        key = norm(term)
        if key in existing_terms:
            continue
        entry = merged.setdefault(key, {"term": term.strip(), "translations": []})
        translation = translation.strip()
        if translation and translation not in entry["translations"]:
            entry["translations"].append(translation)
    return list(merged.values())


def assign_slugs(entries: list[dict[str, Any]], used: set[str]) -> None:
    """Assign stable word slugs, suffixing global collisions with -2, -3."""
    for entry in entries:
        base = f"word-{slugify_term(entry['term'])}"
        slug = base
        suffix = 2
        while slug in used:
            slug = f"{base}-{suffix}"
            suffix += 1
        used.add(slug)
        entry["slug"] = slug


def accepted_variants(translation: str) -> list[str]:
    """Split a translation line into individually accepted answers."""
    parts = [part.strip() for part in _VARIANT_SPLIT.split(translation)]
    unique: dict[str, str] = {}
    for part in parts:
        if part:
            unique.setdefault(part.casefold(), part)
    return list(unique.values())


def main() -> None:
    """Parse CLI arguments and write the supplemental vocabulary block."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="plain-text export of the RTF list")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("content/vocabulary/extra_words.yaml"),
    )
    parser.add_argument(
        "--vocabulary-dir",
        type=Path,
        default=Path("content/vocabulary"),
    )
    parser.add_argument("--topic-slug", default="extra-words")
    parser.add_argument("--topic-title", default="Extra words")
    parser.add_argument("--source", default="5000_word.rtf")
    parser.add_argument("--license", default="unknown")
    args = parser.parse_args()

    entries = parse_entries(args.input)
    if not entries:
        raise SystemExit(f"no entries parsed from {args.input}")

    existing_terms, used_slugs = load_existing(args.vocabulary_dir, args.output)
    missing = merge_missing(entries, existing_terms)
    assign_slugs(missing, used_slugs)

    items = []
    for entry in missing:
        translation = "; ".join(entry["translations"])
        item: dict[str, Any] = {
            "slug": entry["slug"],
            "kind": "vocabulary",
            "term": entry["term"],
            "translation_ru": translation,
        }
        variants = accepted_variants(translation)
        if len(variants) > 1:
            item["accepted_ru"] = variants
        items.append(item)
    document = {
        "topic": {"slug": args.topic_slug, "title": args.topic_title},
        "source": args.source,
        "license": args.license,
        "items": items,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        yaml.safe_dump(
            document,
            allow_unicode=True,
            sort_keys=False,
            width=10_000,
            default_flow_style=False,
        ),
        encoding="utf-8",
    )

    loaded = load_content_file(args.output)
    if len(loaded.items) != len(items):
        raise SystemExit("written file does not round-trip through the loader")

    print(f"parsed: {len(entries)} entries")
    print(f"existing terms: {len(existing_terms)}")
    print(f"written: {len(items)} items -> {args.output}")


if __name__ == "__main__":
    main()
