"""Content selection by kind and topic."""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from app.db.enums import ContentKind
from app.db.models import ContentItem
from app.db.repositories.content import ContentRepository


@dataclass(frozen=True, slots=True)
class SelectionCriteria:
    kind: ContentKind | None = None
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
        )
        if criteria.exclude_ids:
            items = [item for item in items if item.id not in criteria.exclude_ids]
        if criteria.limit is not None and criteria.limit < len(items):
            items = self._rng.sample(items, k=criteria.limit)
        return items
