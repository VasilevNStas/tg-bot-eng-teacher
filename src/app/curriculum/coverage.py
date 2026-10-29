"""Content coverage report (B-043)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass

from sqlalchemy import func, select

from app.curriculum.models import ContentItemSpec
from app.db.database import Database
from app.db.enums import CEFRLevel, ContentKind
from app.db.models import ContentItem, ContentItemTag, Topic, TopicItem
from app.grading.normalize import normalize_answer


@dataclass(frozen=True)
class CoverageCell:
    topic_slug: str
    topic_title: str
    level: CEFRLevel
    kind: ContentKind
    count: int


@dataclass(frozen=True)
class TagCount:
    tag: str
    count: int


@dataclass(frozen=True)
class DuplicateGroup:
    signature: str
    slugs: tuple[str, ...]


@dataclass(frozen=True)
class ContentCoverage:
    active_items: int
    topics: int
    memberships: int
    uncollected: tuple[str, ...]
    empty_topics: tuple[str, ...]
    license_unknown: tuple[str, ...]
    cells: tuple[CoverageCell, ...]
    tags: tuple[TagCount, ...]
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
            memberships = int(
                await session.scalar(select(func.count()).select_from(TopicItem)) or 0
            )

            uncollected = tuple(
                await session.scalars(
                    select(ContentItem.slug)
                    .outerjoin(
                        TopicItem,
                        TopicItem.content_item_id == ContentItem.id,
                    )
                    .where(
                        ContentItem.is_active.is_(True),
                        TopicItem.content_item_id.is_(None),
                    )
                    .order_by(ContentItem.slug)
                )
            )
            empty_topics = tuple(
                await session.scalars(
                    select(Topic.slug)
                    .outerjoin(TopicItem, TopicItem.topic_id == Topic.id)
                    .where(TopicItem.topic_id.is_(None))
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
                    ContentItem.level,
                    ContentItem.kind,
                    func.count(ContentItem.id),
                )
                .select_from(Topic)
                .join(TopicItem, TopicItem.topic_id == Topic.id)
                .join(ContentItem, ContentItem.id == TopicItem.content_item_id)
                .where(ContentItem.is_active.is_(True))
                .group_by(Topic.slug, Topic.title, ContentItem.level, ContentItem.kind)
                .order_by(Topic.slug, ContentItem.level, ContentItem.kind)
            )
            cells = tuple(
                CoverageCell(
                    topic_slug=topic_slug,
                    topic_title=topic_title,
                    level=level,
                    kind=kind,
                    count=int(count),
                )
                for topic_slug, topic_title, level, kind, count in rows
            )

            tag_rows = await session.execute(
                select(ContentItemTag.tag, func.count(ContentItemTag.content_item_id))
                .join(ContentItem, ContentItem.id == ContentItemTag.content_item_id)
                .where(ContentItem.is_active.is_(True))
                .group_by(ContentItemTag.tag)
                .order_by(ContentItemTag.tag)
            )
            tags = tuple(TagCount(tag=tag, count=int(count)) for tag, count in tag_rows)

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
            memberships=memberships,
            uncollected=uncollected,
            empty_topics=empty_topics,
            license_unknown=license_unknown,
            cells=cells,
            tags=tags,
            duplicates=_duplicates(items),
        )


def _duplicates(items: Iterable[ContentItem]) -> tuple[DuplicateGroup, ...]:
    groups: dict[str, list[str]] = defaultdict(list)
    for item in items:
        spec = ContentItemSpec.model_validate(
            {
                "slug": item.slug,
                "level": item.level,
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
        case _:
            parts = [payload.kind.value, str(payload)]
    return " | ".join(parts)


def format_coverage(report: ContentCoverage) -> str:
    lines = [
        "Content coverage",
        f"active items: {report.active_items}",
        f"topics: {report.topics}",
        f"memberships: {report.memberships}",
        f"uncollected items: {len(report.uncollected)}",
        f"license unknown: {len(report.license_unknown)}",
        "",
        "Topic | level | kind | count",
    ]
    if report.cells:
        lines.extend(
            f"{cell.topic_slug} | {cell.level} | {cell.kind} | {cell.count}"
            for cell in report.cells
        )
    else:
        lines.append("(no items)")

    if report.empty_topics:
        lines.extend(["", "Topics without items:", *report.empty_topics])

    lines.extend(["", "Tags"])
    if report.tags:
        lines.extend(f"{tag.tag} | {tag.count}" for tag in report.tags)
    else:
        lines.append("(none)")

    lines.extend(["", "Duplicates"])
    if report.duplicates:
        lines.extend(f"{group.signature}: {', '.join(group.slugs)}" for group in report.duplicates)
    else:
        lines.append("(none)")

    if report.uncollected:
        lines.extend(["", "Uncollected items:", *report.uncollected])
    if report.license_unknown:
        lines.extend(["", "Items with unknown license:", *report.license_unknown])

    return "\n".join(lines)
