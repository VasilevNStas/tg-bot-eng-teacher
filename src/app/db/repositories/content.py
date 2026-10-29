"""Content item queries for exercises."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.enums import CEFRLevel, ContentKind
from app.db.models import ContentItem, ContentItemTag, Topic, TopicItem
from app.db.repository import BaseRepository


class ContentRepository(BaseRepository[ContentItem]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, ContentItem)

    async def get_topic_by_slug(self, slug: str) -> Topic | None:
        return await self._session.scalar(select(Topic).where(Topic.slug == slug))

    async def list_active(
        self,
        *,
        topic_slug: str | None = None,
        kind: ContentKind | None = None,
        level: CEFRLevel | None = None,
        tag: str | None = None,
        tags: Sequence[str] = (),
    ) -> list[ContentItem]:
        stmt = select(ContentItem).where(ContentItem.is_active.is_(True))
        if topic_slug is not None:
            stmt = (
                stmt.join(TopicItem, TopicItem.content_item_id == ContentItem.id)
                .join(Topic, TopicItem.topic_id == Topic.id)
                .where(Topic.slug == topic_slug)
            )
        if kind is not None:
            stmt = stmt.where(ContentItem.kind == kind)
        if level is not None:
            stmt = stmt.where(ContentItem.level == level)
        wanted_tags = list(tags)
        if tag is not None:
            wanted_tags.append(tag)
        for wanted_tag in dict.fromkeys(wanted_tags):
            tag_ref = aliased(ContentItemTag)
            stmt = stmt.join(
                tag_ref,
                tag_ref.content_item_id == ContentItem.id,
            ).where(tag_ref.tag == wanted_tag)
        return await self._scalars(stmt.order_by(ContentItem.id))

    async def get_active(self, content_item_id: int) -> ContentItem | None:
        return await self._session.scalar(
            select(ContentItem).where(
                ContentItem.id == content_item_id,
                ContentItem.is_active.is_(True),
            )
        )

    async def variant_roots(self) -> dict[int, int]:
        rows = await self._session.execute(select(ContentItem.id, ContentItem.variant_of_id))
        parents = {item_id: parent_id for item_id, parent_id in rows}
        roots: dict[int, int] = {}
        for start in parents:
            seen: set[int] = set()
            current = start
            while True:
                parent_id = parents.get(current)
                if parent_id is None or current in seen:
                    roots[start] = current
                    break
                seen.add(current)
                current = parent_id
        return roots
