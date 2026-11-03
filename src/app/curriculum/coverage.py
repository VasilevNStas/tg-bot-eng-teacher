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
    topic_slug: str
    topic_title: str
    kind: ContentKind
    count: int


@dataclass(frozen=True)
class DuplicateGroup:
    signature: str
    slugs: tuple[str, ...]


@dataclass(frozen=True)
class ContentCoverage:
    active_items: int
    topics: int
    empty_topics: tuple[str, ...]
    license_unknown: tuple[str, ...]
    cells: tuple[CoverageCell, ...]
    duplicates: tuple[DuplicateGroup, ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class CoverageService:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def build(self) -> ContentCoverage:
        async with self._database.session() as session:
            active_items = int(
                await session.scalar(
                    select(func.count())
                    .select_from(ContentItem)
                    .where(ContentItem.is_active.is_(True))
                )
                or 0
            )
            topics = int(await session.scalar(select(func.count()).select_from(Topic)) or 0)
            empty_topics = tuple(
                await session.scalars(
                    select(Topic.slug)
                    .outerjoin(ContentItem, ContentItem.topic_id == Topic.id)
                    .where(ContentItem.id.is_(None))
                    .order_by(Topic.slug)
                )
            )
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
    groups: dict[str, list[str]] = defaultdict(list)
    for item in items:
        spec = ContentItemSpec.model_validate(
            {
                "slug": item.slug,
                "payload": item.payload,
            }
        )
        groups[_signature(spec)].append(item.slug)
    return tuple(
        DuplicateGroup(signature=signature, slugs=tuple(sorted(slugs)))
        for signature, slugs in sorted(groups.items())
        if len(slugs) > 1
    )


def _signature(spec: ContentItemSpec) -> str:
    payload = spec.payload
    match payload.kind:
        case ContentKind.IRREGULAR_VERB:
            parts = [
                payload.kind.value,
                normalize_answer(payload.base),
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
            parts = [payload.kind.value, str(payload)]
    return " | ".join(parts)


def format_coverage(report: ContentCoverage) -> str:
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

    if report.license_unknown:
        preview = report.license_unknown[:LICENSE_PREVIEW_LIMIT]
        lines.extend(["", "Items with unknown license:", *preview])
        hidden = len(report.license_unknown) - len(preview)
        if hidden > 0:
            lines.append(f"... and {hidden} more")

    return "\n".join(lines)
