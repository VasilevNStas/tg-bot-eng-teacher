"""Content coverage report (B-043)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass

from sqlalchemy import func, select

from app.curriculum.models import ContentItemSpec
from app.db.database import Database
from app.db.enums import ContentKind
from app.db.models import ContentItem, Topic
from app.grading.normalize import normalize_answer

LICENSE_PREVIEW_LIMIT = 30


@dataclass(frozen=True)
class CoverageCell:
    """One row of the topic x kind matrix."""

    topic_slug: str
    topic_title: str
    kind: ContentKind
    count: int


@dataclass(frozen=True)
class DuplicateGroup:
    """Items that normalize to the same signature (same exercise, different slug)."""

    signature: str
    slugs: tuple[str, ...]


@dataclass(frozen=True)
class ContentCoverage:
    """Snapshot used by ``content-report`` and the CI dry-run."""

    active_items: int
    topics: int
    empty_topics: tuple[str, ...]
    license_unknown: tuple[str, ...]
    cells: tuple[CoverageCell, ...]
    duplicates: tuple[DuplicateGroup, ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class CoverageService:
    """Builds the read-only content coverage report."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def build(self) -> ContentCoverage:
        """Collect all report sections with a handful of read-only queries."""
        async with self._database.session() as session:
            # Headline counters: active items and all topics (including empty).
            active_items = int(
                await session.scalar(
                    select(func.count())
                    .select_from(ContentItem)
                    .where(ContentItem.is_active.is_(True))
                )
                or 0
            )
            topics = int(await session.scalar(select(func.count()).select_from(Topic)) or 0)
            # Left join + IS NULL finds topics that currently have no items.
            empty_topics = tuple(
                await session.scalars(
                    select(Topic.slug)
                    .outerjoin(ContentItem, ContentItem.topic_id == Topic.id)
                    .where(ContentItem.id.is_(None))
                    .order_by(Topic.slug)
                )
            )
            # Legally risky content: active items without a known license.
            license_unknown = tuple(
                await session.scalars(
                    select(ContentItem.slug)
                    .where(
                        ContentItem.is_active.is_(True),
                        ContentItem.license == "unknown",
                    )
                    .order_by(ContentItem.slug)
                )
            )

            # Topic x kind matrix: one GROUP BY row per combination.
            rows = await session.execute(
                select(
                    Topic.slug,
                    Topic.title,
                    ContentItem.kind,
                    func.count(ContentItem.id),
                )
                .select_from(Topic)
                .join(ContentItem, ContentItem.topic_id == Topic.id)
                .where(ContentItem.is_active.is_(True))
                .group_by(Topic.slug, Topic.title, ContentItem.kind)
                .order_by(Topic.slug, ContentItem.kind)
            )
            cells = tuple(
                CoverageCell(
                    topic_slug=topic_slug,
                    topic_title=topic_title,
                    kind=kind,
                    count=int(count),
                )
                for topic_slug, topic_title, kind, count in rows
            )

            # Duplicate detection needs full payloads; load active items once.
            items = tuple(
                await session.scalars(
                    select(ContentItem)
                    .where(ContentItem.is_active.is_(True))
                    .order_by(ContentItem.slug)
                )
            )

        return ContentCoverage(
            active_items=active_items,
            topics=topics,
            empty_topics=empty_topics,
            license_unknown=license_unknown,
            cells=cells,
            duplicates=_duplicates(items),
        )


def _duplicates(items: Iterable[ContentItem]) -> tuple[DuplicateGroup, ...]:
    """Group active items whose normalized content is identical.

    The database ``kind`` is injected back into the payload because it is
    stored as a separate column and the discriminated union needs it.
    """
    groups: dict[str, list[str]] = defaultdict(list)
    for item in items:
        spec = ContentItemSpec.model_validate(
            {
                "slug": item.slug,
                "payload": {"kind": item.kind, **item.payload},
            }
        )
        groups[_signature(spec)].append(item.slug)
    return tuple(
        DuplicateGroup(signature=signature, slugs=tuple(sorted(slugs)))
        for signature, slugs in sorted(groups.items())
        if len(slugs) > 1
    )


def _signature(spec: ContentItemSpec) -> str:
    """Build a kind-specific, normalized fingerprint for duplicate detection.

    Only the pedagogically meaningful fields are included: translations, IPA,
    source ids and display formatting are ignored, so two files that teach the
    same exercise are reported as duplicates even if their metadata differs.
    """
    payload = spec.payload
    match payload.kind:
        case ContentKind.IRREGULAR_VERB:
            parts = [
                payload.kind.value,
                normalize_answer(payload.base),
                # Form order does not matter, so sort before comparing.
                *sorted(normalize_answer(form) for form in payload.past),
                *sorted(normalize_answer(form) for form in payload.participle),
            ]
        case ContentKind.GAP_FILL:
            parts = [
                payload.kind.value,
                normalize_answer(payload.sentence),
                normalize_answer(payload.answer),
            ]
        case ContentKind.TRANSLATION_PAIR:
            parts = [
                payload.kind.value,
                normalize_answer(payload.prompt_ru),
                normalize_answer(payload.reference),
            ]
        case ContentKind.WORD_ORDER:
            parts = [payload.kind.value, normalize_answer(payload.answer)]
        case ContentKind.MULTIPLE_CHOICE:
            parts = [
                payload.kind.value,
                normalize_answer(payload.question),
                *(normalize_answer(option) for option in payload.options),
                str(payload.correct_index),
            ]
        case ContentKind.VOCABULARY:
            parts = [payload.kind.value, normalize_answer(payload.term)]
        case _:
            # Future kinds at least remain comparable instead of crashing.
            parts = [payload.kind.value, str(payload)]
    return " | ".join(parts)


def format_coverage(report: ContentCoverage) -> str:
    """Render the report as plain text for the CLI."""
    lines = [
        "Content coverage",
        f"active items: {report.active_items}",
        f"topics: {report.topics}",
        f"license unknown: {len(report.license_unknown)}",
        "",
        "Topic | kind | count",
    ]
    if report.cells:
        lines.extend(f"{cell.topic_slug} | {cell.kind} | {cell.count}" for cell in report.cells)
    else:
        lines.append("(no items)")

    if report.empty_topics:
        lines.extend(["", "Topics without items:", *report.empty_topics])

    lines.extend(["", "Duplicates"])
    if report.duplicates:
        lines.extend(f"{group.signature}: {', '.join(group.slugs)}" for group in report.duplicates)
    else:
        lines.append("(none)")

    # The word bank alone can have thousands of unknown-license items, so the
    # text report prints a bounded preview instead of flooding the terminal.
    if report.license_unknown:
        preview = report.license_unknown[:LICENSE_PREVIEW_LIMIT]
        lines.extend(["", "Items with unknown license:", *preview])
        hidden = len(report.license_unknown) - len(preview)
        if hidden > 0:
            lines.append(f"... and {hidden} more")

    return "\n".join(lines)
