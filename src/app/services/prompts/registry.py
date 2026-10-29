"""Loads versioned prompt templates from the `prompts/` directory."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from app.db.enums import CEFRLevel, ConversationMode
from app.services.prompts.errors import PromptTemplateNotFound


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    id: str
    content: str
    version: str


class PromptRegistry:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._cache: dict[str, PromptTemplate] = {}

    def persona(self) -> PromptTemplate:
        return self._load("persona")

    def level(self, level: CEFRLevel) -> PromptTemplate:
        return self._load(f"levels/{level.value.lower()}")

    def mode(self, mode: ConversationMode) -> PromptTemplate:
        return self._load(f"modes/{mode.value}")

    def _load(self, key: str) -> PromptTemplate:
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        path = self._root / f"{key}.md"
        if not path.is_file():
            raise PromptTemplateNotFound(f"prompt template not found: {path}")

        content = path.read_text(encoding="utf-8").strip()
        if not content:
            raise PromptTemplateNotFound(f"prompt template is empty: {path}")

        template = PromptTemplate(
            id=key,
            content=content,
            version=hashlib.sha256(content.encode("utf-8")).hexdigest()[:12],
        )
        self._cache[key] = template
        return template
