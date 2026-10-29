"""Content selection by kind, level, topic and tags."""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from app.db.enums import CEFRLevel, ContentKind
from app.db.models import ContentItem
from app.db.repositories.content import ContentRepository


@dataclass(frozen=True, slots=True)
class SelectionCriteria:
    kind: ContentKind | None = None
    level: CEFRLevel | None = None
    tags: tuple[str, ...] = ()
    topic_slug: str | None = None
    exclude_ids: frozenset[int] = field(default_factory=frozenset)
    limit: int | None = None


class ContentSelector:
    def __init__(
        self,
        repository: ContentRepository,
        *,
        rng: random.Random | None = None,
    ) -> None:
        self._repository = repository
        self._rng = rng or random.Random()

    async def select(self, criteria: SelectionCriteria) -> list[ContentItem]:
        if criteria.limit is not None and criteria.limit < 0:
            raise ValueError("limit must not be negative")

        items = await self._repository.list_active(
            topic_slug=criteria.topic_slug,
            kind=criteria.kind,
            level=criteria.level,
            tags=criteria.tags,
        )
        if criteria.exclude_ids:
            items = [item for item in items if item.id not in criteria.exclude_ids]
        if any(item.variant_of_id is not None for item in items):
            roots = await self._repository.variant_roots()
            families: dict[int, ContentItem] = {}
            for item in items:
                root = roots.get(item.id, item.id)
                current = families.get(root)
                if current is None or (item.id == root and current.id != root):
                    families[root] = item
            items = list(families.values())
        if criteria.limit is not None and criteria.limit < len(items):
            items = self._rng.sample(items, k=criteria.limit)
        return items
