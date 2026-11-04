from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.curriculum.errors import ContentLoadError
from app.curriculum.loader import load_content_file, load_content_path
from app.curriculum.models import ContentFile, VocabularyPayload
from app.db.enums import CEFRLevel, ContentKind


def valid_doc() -> dict[str, Any]:
    return {
        "topic": {
            "slug": "test-topic",
            "title": "Test topic",
        },
        "source": "Test",
        "license": "unknown",
        "items": [
            {
                "slug": "test-irregular-go",
                "kind": "irregular_verb",
                "base": "go",
                "past": "went",
                "participle": "gone",
                "translation_ru": "идти",
                "base_ipa": "ɡəʊ",
            },
            {
                "slug": "test-gap-present-simple",
                "kind": "gap_fill",
                "sentence": "She .... to school.",
                "answer": "goes",
            },
            {
                "slug": "test-translation",
                "kind": "translation_pair",
                "prompt_ru": "Я в кино.",
                "reference": "I am at the cinema.",
            },
            {
                "slug": "test-word-order",
                "kind": "word_order",
                "words": ["I", "went", "home"],
                "answer": "I went home",
            },
            {
                "slug": "test-mc",
                "kind": "multiple_choice",
                "question": "I .... here since 2020.",
                "options": ["live", "lived", "have lived"],
                "correct_index": 2,
                "level": "B1",
            },
            {
                "slug": "test-vocabulary-abandon",
                "kind": "vocabulary",
                "term": "abandon",
                "translation_ru": "оставить, забросить",
                "accepted_en": ["abandon"],
                "frequency": 812,
                "frequency_rank": 812,
                "transcription": "əˈbændən",
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
        ContentKind.VOCABULARY,
    ]


def test_loader_reads_yaml(tmp_path: Path) -> None:
    path = tmp_path / "sample.yaml"
    write_yaml(path, valid_doc())

    content = load_content_file(path)

    assert content.topic.slug == "test-topic"
    assert len(content.items) == 6


def test_multiple_choice_level_is_parsed() -> None:
    content = ContentFile.model_validate(valid_doc())

    assert content.items[4].payload.level is CEFRLevel.B1


def test_empty_topic_file_is_allowed() -> None:
    doc = valid_doc()
    doc["items"] = []

    content = ContentFile.model_validate(doc)

    assert content.items == []


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
    doc["items"][0]["base"] = ""
    path = tmp_path / "bad.yaml"
    write_yaml(path, doc)

    with pytest.raises(ContentLoadError, match="base"):
        load_content_file(path)


def test_irregular_verb_keeps_variants_and_ipa() -> None:
    doc = valid_doc()
    doc["items"][0]["past"] = ["learned", "learnt"]
    doc["items"][0]["past_ipa"] = "lɜːnd / lɜːnt"
    content = ContentFile.model_validate(doc)
    payload = content.items[0].payload

    assert getattr(payload, "past", None) == ["learned", "learnt"]
    assert getattr(payload, "past_ipa", None) == "lɜːnd / lɜːnt"


def test_irregular_verb_forms_must_be_unique() -> None:
    doc = valid_doc()
    doc["items"][0]["past"] = ["went", "Went"]

    with pytest.raises(ValueError, match="unique"):
        ContentFile.model_validate(doc)


def test_gap_fill_requires_a_blank() -> None:
    doc = valid_doc()
    doc["items"][1]["sentence"] = "She goes to school."

    with pytest.raises(ValueError, match="must contain"):
        ContentFile.model_validate(doc)


def test_word_order_answer_must_use_the_same_words() -> None:
    doc = valid_doc()
    doc["items"][3]["answer"] = "I went"

    with pytest.raises(ValueError, match="permutation"):
        ContentFile.model_validate(doc)


def test_multiple_choice_index_must_be_in_range() -> None:
    doc = valid_doc()
    doc["items"][4]["correct_index"] = 3

    with pytest.raises(ValueError, match="out of range"):
        ContentFile.model_validate(doc)


def test_vocabulary_payload_is_parsed() -> None:
    doc = valid_doc()
    content = ContentFile.model_validate(doc)
    payload = content.items[5].payload

    assert isinstance(payload, VocabularyPayload)
    assert payload.kind is ContentKind.VOCABULARY
    assert payload.term == "abandon"
    assert payload.translation_ru == "оставить, забросить"
    assert payload.accepted_en == ["abandon"]
    assert payload.frequency == 812
    assert payload.frequency_rank == 812
    assert payload.function_word is False
    assert payload.transcription == "əˈbændən"


def test_vocabulary_term_is_stripped_and_required() -> None:
    doc = valid_doc()
    doc["items"][5]["term"] = "  abandon  "
    content = ContentFile.model_validate(doc)
    assert content.items[5].payload.term == "abandon"

    doc["items"][5]["term"] = "   "
    with pytest.raises(ValueError, match="non-empty"):
        ContentFile.model_validate(doc)


def test_vocabulary_accepted_variants_must_be_unique() -> None:
    doc = valid_doc()
    doc["items"][5]["accepted_ru"] = ["оставить", "Оставить"]

    with pytest.raises(ValueError, match="unique"):
        ContentFile.model_validate(doc)


def test_vocabulary_frequency_is_optional() -> None:
    doc = valid_doc()
    doc["items"][5].pop("frequency")
    doc["items"][5].pop("frequency_rank")
    content = ContentFile.model_validate(doc)

    assert content.items[5].payload.frequency is None
    assert content.items[5].payload.frequency_rank is None


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
    assert any(getattr(item.payload, "base_ipa", None) for item in verbs)


def test_project_irregular_verbs_keep_full_ipa() -> None:
    root = Path(__file__).resolve().parents[2]
    loaded = load_content_path(root / "content" / "topics")
    verbs = [
        item
        for _, content in loaded
        if content.topic.slug == "irregular-verbs"
        for item in content.items
    ]

    assert len(verbs) == 137
    for item in verbs:
        assert getattr(item.payload, "base_ipa", None), item.slug
        assert getattr(item.payload, "past_ipa", None), item.slug
        assert getattr(item.payload, "participle_ipa", None), item.slug
