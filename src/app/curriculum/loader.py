"""YAML content loading and cross-file validation."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from app.curriculum.errors import ContentLoadError
from app.curriculum.models import ContentFile

YAML_SUFFIXES = {".yaml", ".yml"}


def load_content_file(path: Path) -> ContentFile:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ContentLoadError(f"cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ContentLoadError(f"invalid YAML in {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ContentLoadError(f"{path}: top-level YAML value must be a mapping")

    try:
        return ContentFile.model_validate(raw)
    except ValidationError as exc:
        raise ContentLoadError(f"{path}: {exc}") from exc


def load_content_path(path: Path) -> list[tuple[Path, ContentFile]]:
    files = _collect_files(path)
    loaded = [(file_path, load_content_file(file_path)) for file_path in files]

    topics: dict[str, Path] = {}
    for file_path, content in loaded:
        previous_topic = topics.get(content.topic.slug)
        if previous_topic is not None:
            raise ContentLoadError(
                f"duplicate topic '{content.topic.slug}' in {file_path} and {previous_topic}"
            )
        topics[content.topic.slug] = file_path

    seen: dict[str, Path] = {}
    for file_path, content in loaded:
        for item in content.items:
            previous = seen.get(item.slug)
            if previous is not None:
                raise ContentLoadError(
                    f"duplicate slug '{item.slug}' in {file_path} and {previous}"
                )
            seen[item.slug] = file_path

            for topic_slug in item.topics or ():
                if topic_slug not in topics:
                    raise ContentLoadError(
                        f"item '{item.slug}' references unknown topic '{topic_slug}'"
                    )

    return loaded


def _collect_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.exists():
        raise ContentLoadError(f"path does not exist: {path}")
    if not path.is_dir():
        raise ContentLoadError(f"path is neither a file nor a directory: {path}")

    files = sorted(
        candidate
        for candidate in path.iterdir()
        if candidate.is_file() and candidate.suffix.lower() in YAML_SUFFIXES
    )
    if not files:
        raise ContentLoadError(f"no YAML files found in {path}")
    return files
