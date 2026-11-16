from __future__ import annotations

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


def _normalize_tags(tags: tuple[str, ...] | list[str] | None) -> str:
    if not tags:
        return ""
    result: list[str] = []
    for tag in tags:
        value = tag.strip()
        if value:
            result.append(value if value.startswith("#") else f"#{value}")
    return " ".join(dict.fromkeys(result))


def build_thread(
    note: Note,
    translation: Translation,
    media_paths: list[Path],
    source_url: str,
    max_chars: int = 280,
    tags: tuple[str, ...] | list[str] | None = None,
) -> list[DraftPost]:
    attribution = f"Source: {source_url}"
    tag_line = _normalize_tags(tags)
    main = translation.as_text().strip()
    first_suffix = "\n\n".join(part for part in (attribution, tag_line) if part)
    available = max(1, max_chars - len(first_suffix) - 2)
    text_parts = _chunks(main, available) if main else [""]
    text_parts[0] = "\n\n".join(part for part in (text_parts[0], first_suffix) if part)

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
        elif index == count - 1:
            text = attribution
        else:
            text = f"({index + 1}/{count})"
        if len(text) > max_chars:
            raise ValueError(f"draft post {index + 1} exceeds X_MAX_CHARS={max_chars}")
        paths = media_groups[index] if index < len(media_groups) else []
        posts.append(DraftPost(index=index, text=text, image_paths=[str(path) for path in paths]))
    return posts


def draft_to_dict(posts: list[DraftPost]) -> list[dict[str, object]]:
    return [asdict(post) for post in posts]
