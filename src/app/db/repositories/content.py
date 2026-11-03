"""Content item queries for exercises."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import ContentKind
from app.db.models import ContentItem, Topic
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
    ) -> list[ContentItem]:
        stmt = select(ContentItem).where(ContentItem.is_active.is_(True))
        if topic_slug is not None:
            stmt = stmt.join(Topic, Topic.id == ContentItem.topic_id).where(
                Topic.slug == topic_slug
            )
        if kind is not None:
            stmt = stmt.where(ContentItem.kind == kind)
        return await self._scalars(stmt.order_by(ContentItem.id))

    async def get_active(self, content_item_id: int) -> ContentItem | None:
        return await self._session.scalar(
            select(ContentItem).where(
                ContentItem.id == content_item_id,
                ContentItem.is_active.is_(True),
            )
        )
