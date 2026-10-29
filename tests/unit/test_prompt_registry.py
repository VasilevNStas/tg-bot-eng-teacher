from __future__ import annotations

from pathlib import Path

import pytest

from app.db.enums import CEFRLevel, ConversationMode
from app.services.prompts.errors import PromptTemplateNotFound
from app.services.prompts.registry import PromptRegistry

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


def test_all_levels_load_with_distinct_versions() -> None:
    registry = PromptRegistry(PROMPTS_DIR)

    versions = {registry.level(level).version for level in CEFRLevel}

    assert len(versions) == len(CEFRLevel)


def test_all_modes_load() -> None:
    registry = PromptRegistry(PROMPTS_DIR)

    for mode in ConversationMode:
        template = registry.mode(mode)
        assert template.content
        assert len(template.version) == 12


def test_missing_template_raises(tmp_path: Path) -> None:
    registry = PromptRegistry(tmp_path)

    with pytest.raises(PromptTemplateNotFound):
        registry.persona()


def test_empty_template_raises(tmp_path: Path) -> None:
    (tmp_path / "persona.md").write_text("   \n", encoding="utf-8")
    registry = PromptRegistry(tmp_path)

    with pytest.raises(PromptTemplateNotFound):
        registry.persona()


def test_version_is_stable_and_content_sensitive(tmp_path: Path) -> None:
    path = tmp_path / "persona.md"
    path.write_text("first", encoding="utf-8")
    first = PromptRegistry(tmp_path).persona().version

    path.write_text("first", encoding="utf-8")
    second = PromptRegistry(tmp_path).persona().version

    path.write_text("second", encoding="utf-8")
    third = PromptRegistry(tmp_path).persona().version

    assert first == second
    assert first != third
