"""Loads versioned prompt templates from the `prompts/` directory."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from app.db.enums import CEFRLevel, ConversationMode
from app.services.prompts.errors import PromptTemplateNotFound

SCENARIO_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    id: str
    content: str
    version: str
    title: str | None = None


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

    def scenario(self, slug: str) -> PromptTemplate:
        if not SCENARIO_SLUG.fullmatch(slug):
            raise PromptTemplateNotFound(f"invalid scenario slug: {slug!r}")
        template = self._load(f"scenarios/{slug}")
        title = _scenario_title(template.content)
        if title is None:
            raise PromptTemplateNotFound(f"scenario has no heading: {slug}")
        return PromptTemplate(
            id=slug,
            content=template.content,
            version=template.version,
            title=title,
        )

    def scenarios(self) -> tuple[PromptTemplate, ...]:
        directory = self._root / "scenarios"
        if not directory.is_dir():
            return ()
        return tuple(
            self.scenario(path.stem)
            for path in sorted(directory.glob("*.md"))
            if SCENARIO_SLUG.fullmatch(path.stem)
        )

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


def _scenario_title(content: str) -> str | None:
    first = content.splitlines()[0].strip()
    if not first.startswith("# "):
        return None
    title = first[2:].strip()
    return title or None
