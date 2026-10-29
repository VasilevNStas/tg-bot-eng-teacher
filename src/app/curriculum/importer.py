"""Idempotent import of validated content into SQLite."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.curriculum.errors import ContentConflictError, ContentReferenceError
from app.curriculum.models import ContentFile, ContentItemSpec, TopicSpec
from app.db.database import Database
from app.db.models import ContentItem, ContentItemTag, Topic, TopicItem


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
    position: int
    content: ContentFile
    item: ContentItemSpec


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
            item_rows, new_slugs = await self._ensure_items(session, entries)
            variant_changed = await self._resolve_variants(session, entries, item_rows)
            await session.flush()
            await self._ensure_no_variant_cycles(session)

            for entry in entries:
                row = item_rows[entry.item.slug]
                if entry.item.slug in new_slugs:
                    await self._apply_memberships_and_tags(
                        session,
                        row.id,
                        entry,
                        topic_ids,
                    )
                    created[entry.content_index] += 1
                    continue

                if await self._update_existing(
                    session,
                    row,
                    entry,
                    topic_ids,
                    variant_changed=variant_changed[entry.item.slug],
                ):
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
            for position, item in enumerate(content.items):
                if item.slug in seen_items:
                    raise ContentConflictError(f"duplicate item slug '{item.slug}' in import batch")
                seen_items.add(item.slug)
                entries.append(
                    _ItemEntry(
                        content_index=content_index,
                        position=position,
                        content=content,
                        item=item,
                    )
                )
        return entries

    async def _ensure_items(
        self,
        session: AsyncSession,
        entries: Sequence[_ItemEntry],
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
                    variant_of_id=None,
                    version=1,
                    is_active=item.active,
                    kind=item.kind,
                    level=item.level,
                    payload=item.payload.model_dump(mode="json"),
                    source=item.source or entry.content.source,
                    license=item.license or entry.content.license,
                )
                session.add(row)
                new_slugs.add(item.slug)
            item_rows[item.slug] = row

        await session.flush()
        return item_rows, new_slugs

    async def _resolve_variants(
        self,
        session: AsyncSession,
        entries: Sequence[_ItemEntry],
        item_rows: dict[str, ContentItem],
    ) -> dict[str, bool]:
        variant_changed: dict[str, bool] = {}
        for entry in entries:
            item = entry.item
            row = item_rows[item.slug]
            previous = row.variant_of_id
            if item.variant_of is None:
                desired = None
            else:
                base = item_rows.get(item.variant_of)
                if base is None:
                    base = await session.scalar(
                        select(ContentItem).where(ContentItem.slug == item.variant_of)
                    )
                if base is None:
                    raise ContentReferenceError(
                        f"variant base '{item.variant_of}' for '{item.slug}' is not defined"
                    )
                if base.id == row.id:
                    raise ContentConflictError(f"item '{item.slug}' cannot be a variant of itself")
                desired = base.id
            variant_changed[item.slug] = previous != desired
            row.variant_of_id = desired
        return variant_changed

    @staticmethod
    async def _ensure_no_variant_cycles(session: AsyncSession) -> None:
        rows = await session.execute(select(ContentItem.id, ContentItem.variant_of_id))
        graph = {item_id: parent_id for item_id, parent_id in rows}
        for start in graph:
            seen: set[int] = set()
            current: int | None = start
            while current is not None:
                if current in seen:
                    raise ContentConflictError(f"variant cycle detected at item {current}")
                seen.add(current)
                current = graph.get(current)

    async def _apply_memberships_and_tags(
        self,
        session: AsyncSession,
        content_item_id: int,
        entry: _ItemEntry,
        topic_ids: dict[str, int],
    ) -> None:
        desired = await self._desired_memberships(session, entry, topic_ids)
        await self._add_memberships(session, content_item_id, desired)
        await self._replace_tags(session, content_item_id, entry.item.tags)

    async def _update_existing(
        self,
        session: AsyncSession,
        row: ContentItem,
        entry: _ItemEntry,
        topic_ids: dict[str, int],
        *,
        variant_changed: bool,
    ) -> bool:
        item = entry.item
        payload = item.payload.model_dump(mode="json")
        source = item.source or entry.content.source
        license_name = item.license or entry.content.license

        desired_memberships = await self._desired_memberships(session, entry, topic_ids)
        membership_rows = list(
            await session.scalars(select(TopicItem).where(TopicItem.content_item_id == row.id))
        )
        current_memberships = {link.topic_id: link.position for link in membership_rows}
        existing_tags = await self._load_tags(session, row.id)
        tags_changed = existing_tags != set(item.tags)
        memberships_changed = current_memberships != desired_memberships
        content_changed = (
            row.kind is not item.kind
            or row.level is not item.level
            or row.payload != payload
            or row.source != source
            or row.license != license_name
            or row.is_active is not item.active
        )
        if not (content_changed or tags_changed or memberships_changed or variant_changed):
            return False

        row.version += 1
        row.kind = item.kind
        row.level = item.level
        row.payload = payload
        row.source = source
        row.license = license_name
        row.is_active = item.active
        if tags_changed:
            await self._replace_tags(session, row.id, item.tags)
        if memberships_changed:
            await self._sync_memberships(
                session,
                row.id,
                membership_rows,
                desired_memberships,
            )
        return True

    async def _desired_memberships(
        self,
        session: AsyncSession,
        entry: _ItemEntry,
        topic_ids: dict[str, int],
    ) -> dict[int, int]:
        topic_slugs = (
            entry.item.topics if entry.item.topics is not None else [entry.content.topic.slug]
        )
        resolved = await self._resolve_topics(session, topic_slugs, topic_ids)
        return {topic_id: entry.position for topic_id in resolved}

    async def _resolve_topics(
        self,
        session: AsyncSession,
        topic_slugs: Sequence[str],
        topic_ids: dict[str, int],
    ) -> list[int]:
        resolved: list[int] = []
        for topic_slug in topic_slugs:
            topic_id = topic_ids.get(topic_slug)
            if topic_id is None:
                topic = await session.scalar(select(Topic).where(Topic.slug == topic_slug))
                if topic is None:
                    raise ContentReferenceError(f"topic '{topic_slug}' is not defined")
                topic_ids[topic_slug] = topic.id
                topic_id = topic.id
            resolved.append(topic_id)
        return resolved

    @staticmethod
    async def _add_memberships(
        session: AsyncSession,
        content_item_id: int,
        memberships: dict[int, int],
    ) -> None:
        for topic_id, position in memberships.items():
            session.add(
                TopicItem(
                    topic_id=topic_id,
                    content_item_id=content_item_id,
                    position=position,
                )
            )

    @staticmethod
    async def _sync_memberships(
        session: AsyncSession,
        content_item_id: int,
        current_rows: list[TopicItem],
        desired: dict[int, int],
    ) -> None:
        current = {row.topic_id: row for row in current_rows}
        for topic_id, row in current.items():
            if topic_id not in desired:
                await session.delete(row)
        for topic_id, position in desired.items():
            existing_row = current.get(topic_id)
            if existing_row is None:
                session.add(
                    TopicItem(
                        topic_id=topic_id,
                        content_item_id=content_item_id,
                        position=position,
                    )
                )
            else:
                existing_row.position = position

    @staticmethod
    async def _load_tags(session: AsyncSession, content_item_id: int) -> set[str]:
        tags = await session.scalars(
            select(ContentItemTag.tag).where(ContentItemTag.content_item_id == content_item_id)
        )
        return set(tags.all())

    @staticmethod
    async def _replace_tags(
        session: AsyncSession,
        content_item_id: int,
        tags: list[str],
    ) -> None:
        await session.execute(
            delete(ContentItemTag).where(ContentItemTag.content_item_id == content_item_id)
        )
        for tag in sorted(set(tags)):
            session.add(
                ContentItemTag(
                    content_item_id=content_item_id,
                    tag=tag,
                )
            )

    @staticmethod
    async def _upsert_topic(session: AsyncSession, spec: TopicSpec) -> Topic:
        topic = await session.scalar(select(Topic).where(Topic.slug == spec.slug))
        if topic is None:
            topic = Topic(
                slug=spec.slug,
                title=spec.title,
                level=spec.level,
                description=spec.description,
                tags=spec.tags,
            )
            session.add(topic)
            await session.flush()
            return topic

        topic.title = spec.title
        topic.level = spec.level
        topic.description = spec.description
        topic.tags = spec.tags
        return topic
