"""Select curated knowledge entries for a learner's goals and interests."""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import select

from app.curriculum.models import KnowledgePayload
from app.db.database import Database
from app.db.enums import CEFRLevel, ContentKind
from app.db.models import ContentItem
from app.services.goals.model import GoalsProfile
from app.services.knowledge.model import KnowledgeSnippet


class KnowledgeService:
    """Read-only selection over admin-curated ``knowledge`` content.

    Selection is deterministic: entries are ranked by how many tags match the
    learner's goals/interests, then by slug. The number of entries and the
    total body size are capped so the prompt stays cheap.
    """

    def __init__(self, database: Database, *, limit: int = 2, max_chars: int = 1200) -> None:
        if limit < 0:
            raise ValueError("limit must not be negative")
        if max_chars < 0:
            raise ValueError("max_chars must not be negative")
        self._database = database
        self._limit = limit
        self._max_chars = max_chars

    async def select_for_profile(
        self,
        profile: Mapping[str, object],
        *,
        level: CEFRLevel | None = None,
    ) -> tuple[KnowledgeSnippet, ...]:
        """Return the best matching snippets, or an empty tuple.

        Users without goals/interests get no block: knowledge is opt-in
        context, not a global lecture appended to every prompt.
        """
        preferences = GoalsProfile.from_profile(profile)
        wanted = set(preferences.goals) | set(preferences.interests)
        if not wanted or self._limit == 0 or self._max_chars == 0:
            return ()

        async with self._database.session() as session:
            rows = tuple(
                await session.scalars(
                    select(ContentItem)
                    .where(
                        ContentItem.kind == ContentKind.KNOWLEDGE,
                        ContentItem.is_active.is_(True),
                    )
                    .order_by(ContentItem.slug)
                )
            )

        scored: list[tuple[int, str, KnowledgePayload]] = []
        for row in rows:
            payload = KnowledgePayload.model_validate(
                {"kind": ContentKind.KNOWLEDGE, **row.payload}
            )
            if payload.level is not None and level is not None and payload.level != level:
                continue
            score = len(set(payload.tags) & wanted)
            if score:
                scored.append((score, row.slug, payload))

        scored.sort(key=lambda pair: (-pair[0], pair[1]))

        selected: list[KnowledgeSnippet] = []
        total_chars = 0
        for _, slug, payload in scored:
            if len(selected) >= self._limit:
                break
            if total_chars + len(payload.body_md) > self._max_chars:
                continue
            selected.append(
                KnowledgeSnippet(
                    slug=slug,
                    title=payload.title,
                    body_md=payload.body_md,
                    tags=tuple(payload.tags),
                    level=payload.level,
                )
            )
            total_chars += len(payload.body_md)
        return tuple(selected)
