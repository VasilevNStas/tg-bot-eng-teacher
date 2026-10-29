from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.curriculum.errors import ContentLoadError
from app.curriculum.loader import load_content_file, load_content_path
from app.curriculum.models import ContentFile
from app.db.enums import ContentKind


def valid_doc() -> dict[str, Any]:
    return {
        "topic": {
            "slug": "test-topic",
            "title": "Test topic",
            "level": "A1",
            "description": "Fixture",
            "tags": ["test"],
        },
        "source": "Test",
        "license": "unknown",
        "items": [
            {
                "slug": "test-irregular-go",
                "level": "A1",
                "payload": {
                    "kind": "irregular_verb",
                    "base": "go",
                    "past": ["went"],
                    "participle": ["gone"],
                    "translation_ru": "идти",
                    "base_ipa": "ɡəʊ",
                },
            },
            {
                "slug": "test-gap-present-simple",
                "level": "A1",
                "payload": {
                    "kind": "gap_fill",
                    "sentence": "She ___ to school.",
                    "answer": "goes",
                },
            },
            {
                "slug": "test-translation",
                "level": "A2",
                "payload": {
                    "kind": "translation_pair",
                    "prompt_ru": "Я в кино.",
                    "reference": "I am at the cinema.",
                },
            },
            {
                "slug": "test-word-order",
                "level": "A2",
                "payload": {
                    "kind": "word_order",
                    "words": ["I", "went", "home"],
                    "answer": "I went home",
                },
            },
            {
                "slug": "test-mc",
                "level": "B1",
                "payload": {
                    "kind": "multiple_choice",
                    "question": "I ___ here since 2020.",
                    "options": ["live", "lived", "have lived"],
                    "correct_index": 2,
                },
            },
        ],
    }


def write_yaml(path: Path, doc: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")


def test_valid_document_parses_all_payload_kinds() -> None:
    content = ContentFile.model_validate(valid_doc())

    assert [item.kind for item in content.items] == [
        ContentKind.IRREGULAR_VERB,
        ContentKind.GAP_FILL,
        ContentKind.TRANSLATION_PAIR,
        ContentKind.WORD_ORDER,
        ContentKind.MULTIPLE_CHOICE,
    ]


def test_loader_reads_yaml(tmp_path: Path) -> None:
    path = tmp_path / "sample.yaml"
    write_yaml(path, valid_doc())

    content = load_content_file(path)

    assert content.topic.slug == "test-topic"
    assert len(content.items) == 5


def test_item_topics_are_parsed() -> None:
    doc = valid_doc()
    doc["items"][0]["topics"] = ["test-topic", "other-topic"]

    content = ContentFile.model_validate(doc)

    assert content.items[0].topics == ["test-topic", "other-topic"]


def test_item_variant_of_is_parsed() -> None:
    doc = valid_doc()
    doc["items"][0]["variant_of"] = "test-irregular-go"

    content = ContentFile.model_validate(doc)

    assert content.items[0].variant_of == "test-irregular-go"


def test_item_variant_of_slug_is_validated() -> None:
    doc = valid_doc()
    doc["items"][0]["variant_of"] = "Bad Slug"

    with pytest.raises(ValueError, match="variant_of"):
        ContentFile.model_validate(doc)


def test_empty_topic_file_is_allowed() -> None:
    doc = valid_doc()
    doc["items"] = []

    content = ContentFile.model_validate(doc)

    assert content.items == []


def test_item_topics_must_not_be_empty() -> None:
    doc = valid_doc()
    doc["items"][0]["topics"] = []

    with pytest.raises(ValueError, match="must not be empty"):
        ContentFile.model_validate(doc)


def test_item_topics_must_be_unique() -> None:
    doc = valid_doc()
    doc["items"][0]["topics"] = ["test-topic", "test-topic"]

    with pytest.raises(ValueError, match="unique"):
        ContentFile.model_validate(doc)


def test_item_topic_slug_is_validated() -> None:
    doc = valid_doc()
    doc["items"][0]["topics"] = ["Bad Slug"]

    with pytest.raises(ValueError, match="invalid topic slug"):
        ContentFile.model_validate(doc)


def test_directory_resolves_item_topics_across_files(tmp_path: Path) -> None:
    first = valid_doc()
    first["items"][0]["topics"] = ["test-topic", "other-topic"]
    second = valid_doc()
    second["topic"]["slug"] = "other-topic"
    second["items"] = []

    write_yaml(tmp_path / "a.yaml", first)
    write_yaml(tmp_path / "b.yaml", second)

    loaded = load_content_path(tmp_path)

    assert {content.topic.slug for _, content in loaded} == {"test-topic", "other-topic"}


def test_unknown_item_topic_is_rejected(tmp_path: Path) -> None:
    doc = valid_doc()
    doc["items"][0]["topics"] = ["missing-topic"]
    write_yaml(tmp_path / "a.yaml", doc)

    with pytest.raises(ContentLoadError, match="unknown topic"):
        load_content_path(tmp_path)


def test_duplicate_topic_definitions_are_rejected(tmp_path: Path) -> None:
    first = valid_doc()
    first["items"] = []
    second = valid_doc()
    second["items"] = []
    write_yaml(tmp_path / "a.yaml", first)
    write_yaml(tmp_path / "b.yaml", second)

    with pytest.raises(ContentLoadError, match="duplicate topic"):
        load_content_path(tmp_path)


def test_loader_reports_bad_yaml(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("topic: [unclosed", encoding="utf-8")

    with pytest.raises(ContentLoadError, match="invalid YAML"):
        load_content_file(path)


def test_loader_reports_validation_error(tmp_path: Path) -> None:
    doc = valid_doc()
    doc["items"][0]["payload"]["base"] = ""
    path = tmp_path / "bad.yaml"
    write_yaml(path, doc)

    with pytest.raises(ContentLoadError, match="base"):
        load_content_file(path)


def test_irregular_verb_keeps_variants_and_ipa() -> None:
    doc = valid_doc()
    doc["items"][0]["payload"]["past"] = ["learned", "learnt"]
    doc["items"][0]["payload"]["past_ipa"] = "lɜːnd / lɜːnt"
    content = ContentFile.model_validate(doc)
    payload = content.items[0].payload

    assert getattr(payload, "past", None) == ["learned", "learnt"]
    assert getattr(payload, "past_ipa", None) == "lɜːnd / lɜːnt"


def test_item_tags_are_parsed() -> None:
    doc = valid_doc()
    doc["items"][0]["tags"] = ["grammar:past-simple", "theme:travel"]

    content = ContentFile.model_validate(doc)

    assert content.items[0].tags == ["grammar:past-simple", "theme:travel"]


def test_unknown_tag_namespace_is_rejected() -> None:
    doc = valid_doc()
    doc["items"][0]["tags"] = ["food:travel"]

    with pytest.raises(ValueError, match="namespace"):
        ContentFile.model_validate(doc)


def test_invalid_tag_format_is_rejected() -> None:
    doc = valid_doc()
    doc["items"][0]["tags"] = ["Past Simple"]

    with pytest.raises(ValueError, match="invalid tag"):
        ContentFile.model_validate(doc)


def test_duplicate_item_tags_are_rejected() -> None:
    doc = valid_doc()
    doc["items"][0]["tags"] = ["theme:travel", "theme:travel"]

    with pytest.raises(ValueError, match="unique"):
        ContentFile.model_validate(doc)


def test_irregular_verb_forms_must_be_unique() -> None:
    doc = valid_doc()
    doc["items"][0]["payload"]["past"] = ["went", "Went"]

    with pytest.raises(ValueError, match="unique"):
        ContentFile.model_validate(doc)


def test_gap_fill_requires_a_blank() -> None:
    doc = valid_doc()
    doc["items"][1]["payload"]["sentence"] = "She goes to school."

    with pytest.raises(ValueError, match="___"):
        ContentFile.model_validate(doc)


def test_word_order_answer_must_use_the_same_words() -> None:
    doc = valid_doc()
    doc["items"][3]["payload"]["answer"] = "I went"

    with pytest.raises(ValueError, match="permutation"):
        ContentFile.model_validate(doc)


def test_multiple_choice_index_must_be_in_range() -> None:
    doc = valid_doc()
    doc["items"][4]["payload"]["correct_index"] = 3

    with pytest.raises(ValueError, match="out of range"):
        ContentFile.model_validate(doc)


def test_duplicate_slugs_in_one_file_are_rejected() -> None:
    doc = valid_doc()
    doc["items"][1]["slug"] = doc["items"][0]["slug"]

    with pytest.raises(ValueError, match="unique"):
        ContentFile.model_validate(doc)


def test_duplicate_slugs_across_files_are_rejected(tmp_path: Path) -> None:
    first = valid_doc()
    second = copy.deepcopy(valid_doc())
    second["topic"]["slug"] = "other-topic"
    write_yaml(tmp_path / "a.yaml", first)
    write_yaml(tmp_path / "b.yaml", second)

    with pytest.raises(ContentLoadError, match="duplicate slug"):
        load_content_path(tmp_path)


def test_directory_without_yaml_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ContentLoadError, match="no YAML"):
        load_content_path(tmp_path)


def test_project_content_loads() -> None:
    root = Path(__file__).resolve().parents[2]
    loaded = load_content_path(root / "content" / "topics")
    topics = {content.topic.slug for _, content in loaded}

    assert "irregular-verbs" in topics
    verbs = [
        item
        for _, content in loaded
        if content.topic.slug == "irregular-verbs"
        for item in content.items
    ]
    assert verbs
    assert all("grammar:irregular-verbs" in item.tags for item in verbs)
    assert any(getattr(item.payload, "base_ipa", None) for item in verbs)
