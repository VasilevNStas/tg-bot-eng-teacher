"""Loads versioned prompt templates from the `prompts/` directory.

Templates are Markdown files. Their version is a short SHA-256 of the content,
so editing a prompt changes the version hash recorded in ``usage_ledger``.
Templates are cached in memory for the process lifetime.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from app.db.enums import CEFRLevel, ConversationMode
from app.services.prompts.errors import PromptTemplateNotFound

# Scenario ids come from callback data, so only safe path components are allowed.
SCENARIO_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    """One rendered prompt fragment with its content hash as version."""

    id: str
    content: str
    version: str
    title: str | None = None


class PromptRegistry:
    """Resolve persona, level, mode and scenario templates by key."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._cache: dict[str, PromptTemplate] = {}

    def persona(self) -> PromptTemplate:
        """The base teacher persona shared by all modes."""
        return self._load("persona")

    def level(self, level: CEFRLevel) -> PromptTemplate:
        """Level-specific instructions (``levels/a1.md``)."""
        return self._load(f"levels/{level.value.lower()}")

    def mode(self, mode: ConversationMode) -> PromptTemplate:
        """Mode-specific instructions (``modes/chat.md``)."""
        return self._load(f"modes/{mode.value}")

    def scenario(self, slug: str) -> PromptTemplate:
        """Load a roleplay scenario and extract its title from the first heading."""
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
        """List all valid scenario files, sorted by filename."""
        directory = self._root / "scenarios"
        if not directory.is_dir():
            return ()
        return tuple(
            self.scenario(path.stem)
            for path in sorted(directory.glob("*.md"))
            if SCENARIO_SLUG.fullmatch(path.stem)
        )

    def _load(self, key: str) -> PromptTemplate:
        """Load and cache one template; empty or missing files are errors."""
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
    """Return the text after the first Markdown ``# `` heading, if present."""
    first = content.splitlines()[0].strip()
    if not first.startswith("# "):
        return None
    title = first[2:].strip()
    return title or None
