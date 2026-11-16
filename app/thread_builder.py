from __future__ import annotations

import random
from dataclasses import asdict, dataclass
from pathlib import Path

from .models import Note, Translation


@dataclass(slots=True)
class DraftPost:
    index: int
    text: str
    image_paths: list[str]
    video_path: str | None = None


def _chunks(text: str, max_chars: int) -> list[str]:
    text = text.strip()
    if not text:
        return [""]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        boundary = max(
            remaining.rfind("\n", 0, max_chars + 1),
            remaining.rfind(". ", 0, max_chars + 1),
            remaining.rfind(" ", 0, max_chars + 1),
        )
        if boundary < max_chars // 2:
            boundary = max_chars
        chunk = remaining[:boundary].rstrip()
        remaining = remaining[boundary:].lstrip()
        chunks.append(chunk)
    if remaining:
        chunks.append(remaining)
    return chunks


_BLOCKED_TAGS = {"#xiaohongshu", "#rednote", "#小红书"}


def _normalize_tag_pool(tags: tuple[str, ...] | list[str] | None) -> list[str]:
    if not tags:
        return []
    result: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        value = tag.strip()
        if not value:
            continue
        value = value if value.startswith("#") else f"#{value}"
        normalized = value.casefold()
        if normalized in _BLOCKED_TAGS or normalized in seen:
            continue
        seen.add(normalized)
        result.append(value)
    return result


def select_tags(
    note_id: str,
    tags: tuple[str, ...] | list[str] | None,
    count: int = 4,
) -> tuple[str, ...]:
    """Select a stable, note-specific subset so retries keep the same draft."""
    pool = _normalize_tag_pool(tags)
    if count <= 0 or not pool:
        return ()
    if count >= len(pool):
        return tuple(pool)
    return tuple(random.Random(note_id).sample(pool, count))


def _format_tags(tags: tuple[str, ...]) -> str:
    return " ".join(tags)


def build_thread(
    note: Note,
    translation: Translation,
    media_paths: list[Path],
    _source_url: str,
    max_chars: int = 280,
    tags: tuple[str, ...] | list[str] | None = None,
    tag_count: int = 4,
) -> list[DraftPost]:
    tag_line = _format_tags(select_tags(note.note_id, tags, tag_count))
    main = translation.as_text().strip()
    suffix = tag_line
    available = max(1, max_chars - len(suffix) - 2)
    text_parts = _chunks(main, available) if main else [""]
    text_parts[0] = "\n\n".join(part for part in (text_parts[0], suffix) if part)

    if note.note_type == "video":
        video_path = str(media_paths[0]) if media_paths else None
        if video_path is None:
            raise ValueError("video note has no downloaded video")
        if len(text_parts[0]) > max_chars:
            raise ValueError(f"draft post 1 exceeds X_MAX_CHARS={max_chars}")
        return [DraftPost(index=0, text=text_parts[0], image_paths=[], video_path=video_path)]

    media_groups = [media_paths[index : index + 4] for index in range(0, len(media_paths), 4)]
    count = max(len(text_parts), len(media_groups), 1)
    posts: list[DraftPost] = []
    for index in range(count):
        if index < len(text_parts):
            text = text_parts[index]
        else:
            text = f"({index + 1}/{count})"
        if len(text) > max_chars:
            raise ValueError(f"draft post {index + 1} exceeds X_MAX_CHARS={max_chars}")
        paths = media_groups[index] if index < len(media_groups) else []
        posts.append(DraftPost(index=index, text=text, image_paths=[str(path) for path in paths]))
    return posts


def draft_to_dict(posts: list[DraftPost]) -> list[dict[str, object]]:
    return [asdict(post) for post in posts]
