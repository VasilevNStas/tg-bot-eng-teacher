"""Idempotent import of validated content into SQLite."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.curriculum.errors import ContentConflictError
from app.curriculum.models import ContentFile, ContentItemSpec, TopicSpec
from app.db.database import Database
from app.db.models import ContentItem, Topic


@dataclass(frozen=True, slots=True)
class ImportResult:
    created: int = 0
    updated: int = 0
    skipped: int = 0

    @property
    def total(self) -> int:
        return self.created + self.updated + self.skipped


@dataclass(frozen=True, slots=True)
class _ItemEntry:
    content_index: int
    content: ContentFile
    item: ContentItemSpec


def _payload_data(item: ContentItemSpec) -> dict[str, object]:
    return item.payload.model_dump(
        mode="json",
        exclude={"kind"},
        exclude_defaults=True,
    )


class ContentImporter:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def import_file(self, content: ContentFile) -> ImportResult:
        results = await self.import_files([content])
        return results[0]

    async def import_files(self, contents: Sequence[ContentFile]) -> list[ImportResult]:
        if not contents:
            return []

        created = [0] * len(contents)
        updated = [0] * len(contents)
        skipped = [0] * len(contents)

        async with self._database.session() as session:
            topic_ids = await self._upsert_topics(session, contents)
            entries = self._entries(contents)
            item_rows, new_slugs = await self._ensure_items(session, entries, topic_ids)

            for entry in entries:
                row = item_rows[entry.item.slug]
                if entry.item.slug in new_slugs:
                    created[entry.content_index] += 1
                    continue
                if self._update_existing(row, entry, topic_ids):
                    updated[entry.content_index] += 1
                else:
                    skipped[entry.content_index] += 1

        return [
            ImportResult(
                created=created[index],
                updated=updated[index],
                skipped=skipped[index],
            )
            for index in range(len(contents))
        ]

    async def _upsert_topics(
        self,
        session: AsyncSession,
        contents: Sequence[ContentFile],
    ) -> dict[str, int]:
        topic_ids: dict[str, int] = {}
        for content in contents:
            topic_slug = content.topic.slug
            if topic_slug in topic_ids:
                raise ContentConflictError(f"duplicate topic '{topic_slug}' in import batch")
            topic = await self._upsert_topic(session, content.topic)
            topic_ids[topic_slug] = topic.id
        return topic_ids

    def _entries(self, contents: Sequence[ContentFile]) -> list[_ItemEntry]:
        seen_items: set[str] = set()
        entries: list[_ItemEntry] = []
        for content_index, content in enumerate(contents):
            for item in content.items:
                if item.slug in seen_items:
                    raise ContentConflictError(f"duplicate item slug '{item.slug}' in import batch")
                seen_items.add(item.slug)
                entries.append(
                    _ItemEntry(
                        content_index=content_index,
                        content=content,
                        item=item,
                    )
                )
        return entries

    async def _ensure_items(
        self,
        session: AsyncSession,
        entries: Sequence[_ItemEntry],
        topic_ids: dict[str, int],
    ) -> tuple[dict[str, ContentItem], set[str]]:
        slugs = [entry.item.slug for entry in entries]
        existing = (
            {
                row.slug: row
                for row in await session.scalars(
                    select(ContentItem).where(ContentItem.slug.in_(slugs))
                )
            }
            if slugs
            else {}
        )

        item_rows: dict[str, ContentItem] = {}
        new_slugs: set[str] = set()
        for entry in entries:
            item = entry.item
            row = existing.get(item.slug)
            if row is None:
                row = ContentItem(
                    slug=item.slug,
                    topic_id=topic_ids[entry.content.topic.slug],
                    is_active=item.active,
                    kind=item.kind,
                    payload=_payload_data(item),
                    source=item.source or entry.content.source,
                    license=item.license or entry.content.license,
                )
                session.add(row)
                new_slugs.add(item.slug)
            item_rows[item.slug] = row

        await session.flush()
        return item_rows, new_slugs

    @staticmethod
    def _update_existing(
        row: ContentItem,
        entry: _ItemEntry,
        topic_ids: dict[str, int],
    ) -> bool:
        item = entry.item
        payload = _payload_data(item)
        source = item.source or entry.content.source
        license_name = item.license or entry.content.license
        topic_id = topic_ids[entry.content.topic.slug]

        if not (
            row.topic_id != topic_id
            or row.kind is not item.kind
            or row.payload != payload
            or row.source != source
            or row.license != license_name
            or row.is_active is not item.active
        ):
            return False

        row.topic_id = topic_id
        row.kind = item.kind
        row.payload = payload
        row.source = source
        row.license = license_name
        row.is_active = item.active
        return True

    @staticmethod
    async def _upsert_topic(session: AsyncSession, spec: TopicSpec) -> Topic:
        topic = await session.scalar(select(Topic).where(Topic.slug == spec.slug))
        if topic is None:
            topic = Topic(slug=spec.slug, title=spec.title)
            session.add(topic)
            await session.flush()
            return topic

        topic.title = spec.title
        return topic
