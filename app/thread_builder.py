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


_BLOCKED_TAGS = {"#xiaohongshu", "#rednote", "#小红书"}
_MAX_IMAGES_PER_POST = 4


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


def _truncate_text(text: str, max_chars: int) -> str:
    """Trim text deterministically at a readable boundary and add an ellipsis."""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    if max_chars <= 1:
        return "…"[:max_chars]

    cutoff = max_chars - 1
    candidate = text[:cutoff].rstrip()
    boundary = max(
        candidate.rfind("\n"),
        candidate.rfind(". "),
        candidate.rfind("! "),
        candidate.rfind("? "),
        candidate.rfind("。"),
        candidate.rfind("！"),
        candidate.rfind("？"),
        candidate.rfind(" "),
    )
    if boundary >= cutoff // 2:
        candidate = candidate[:boundary].rstrip()
    return f"{candidate}…"


def build_thread(
    note: Note,
    translation: Translation,
    media_paths: list[Path],
    _source_url: str,
    max_chars: int = 280,
    tags: tuple[str, ...] | list[str] | None = None,
    tag_count: int = 4,
) -> list[DraftPost]:
    """Build exactly one X post; ordinary accounts do not use Threads."""
    if max_chars < 1:
        raise ValueError("X_MAX_CHARS must be positive")

    tag_line = _format_tags(select_tags(note.note_id, tags, tag_count))
    suffix = f"\n\n{tag_line}" if tag_line else ""
    main = translation.as_text().strip()
    available = max(0, max_chars - len(suffix))
    if main and available:
        text = _truncate_text(main, available) + suffix
    elif suffix:
        text = suffix.strip()[:max_chars]
    else:
        text = _truncate_text(main, max_chars)

    if note.note_type == "video":
        video_path = str(media_paths[0]) if media_paths else None
        if video_path is None:
            raise ValueError("video note has no downloaded video")
        return [DraftPost(index=0, text=text, image_paths=[], video_path=video_path)]

    # The source download still keeps every original image. X receives only the
    # first four because a single post cannot carry more media without a Thread.
    return [
        DraftPost(
            index=0,
            text=text,
            image_paths=[str(path) for path in media_paths[:_MAX_IMAGES_PER_POST]],
        )
    ]


def draft_to_dict(posts: list[DraftPost]) -> list[dict[str, object]]:
    return [asdict(post) for post in posts]
