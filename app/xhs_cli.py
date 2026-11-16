from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from .models import Note, ProfileCandidate, ProfileIdentity
from .url_utils import public_source_url

LOGGER = logging.getLogger(__name__)


class XhsCLIError(RuntimeError):
    """The read-only xhs CLI could not return verifiable data."""


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _timestamp(value: Any) -> str | None:
    if isinstance(value, (int, float)):
        if value <= 0:
            return None
        seconds = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(seconds, UTC).isoformat()
    if isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).isoformat()
    return None


def _records(stdout: str) -> list[dict[str, Any]]:
    text = stdout.strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        result: list[dict[str, Any]] = []
        for line in text.splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, Mapping):
                result.append(dict(value))
        return result
    if isinstance(parsed, Mapping):
        return [dict(parsed)]
    if isinstance(parsed, list):
        return [dict(item) for item in parsed if isinstance(item, Mapping)]
    return []


def _first(record: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if record.get(key) not in (None, ""):
            return record[key]
    return None


def _public_note_url(note_id: str, token: str = "") -> str:
    if token:
        return (
            f"https://www.xiaohongshu.com/explore/{note_id}"
            f"?xsec_token={token}&xsec_source=pc_feed"
        )
    return f"https://www.xiaohongshu.com/explore/{note_id}"


def _clean_raw(value: Any) -> Any:
    secret_keys = {"xsec_token", "xsecToken", "cookie", "authorization", "access_token"}
    if isinstance(value, Mapping):
        return {
            str(key): _clean_raw(child)
            for key, child in value.items()
            if str(key) not in secret_keys
        }
    if isinstance(value, list):
        return [_clean_raw(child) for child in value]
    return value


def _image_urls(record: Mapping[str, Any]) -> list[str]:
    images = record.get("images") or record.get("image_list") or record.get("imageList") or []
    result: list[str] = []
    if isinstance(images, list):
        for image in images:
            if isinstance(image, str):
                result.append(image)
            elif isinstance(image, Mapping):
                value = _first(image, "url", "url_default", "urlDefault", "stream_url")
                if value:
                    result.append(str(value))
    return list(dict.fromkeys(result))


def _video_urls(record: Mapping[str, Any]) -> list[str]:
    video = record.get("video")
    result: list[str] = []
    if isinstance(video, Mapping):
        masters = video.get("masters") or video.get("master_urls") or video.get("masterUrls")
        if isinstance(masters, list):
            result.extend(str(item) for item in masters if item)
        for key in ("url", "video_url", "videoUrl"):
            if video.get(key):
                result.append(str(video[key]))
    for key in ("video_url", "videoUrl"):
        value = record.get(key)
        if value:
            result.append(str(value))
    return list(dict.fromkeys(result))


def parse_candidate(record: Mapping[str, Any]) -> ProfileCandidate:
    note_id = _text(_first(record, "note_id", "noteId", "id"))
    token = _text(_first(record, "xsec_token", "xsecToken"))
    if not note_id:
        raise XhsCLIError("xhs profile record has no note_id")
    return ProfileCandidate(
        note_id=note_id,
        title=_text(_first(record, "title", "display_title", "displayTitle")),
        source_url=public_source_url(_text(record.get("url")) or _public_note_url(note_id)),
        published_at=_timestamp(_first(record, "time", "published_at", "publishedAt")),
        is_pinned=(
            bool(record["is_pinned"])
            if "is_pinned" in record
            else (bool(record["isPinned"]) if "isPinned" in record else None)
        ),
        author=_text(_first(record, "nickname", "author")),
        author_user_id=_text(_first(record, "user_id", "userId")),
        xsec_token=token,
        note_type=_text(record.get("type")),
    )


def parse_note(record: Mapping[str, Any], source_url: str = "") -> Note:
    note_id = _text(_first(record, "note_id", "noteId", "id"))
    if not note_id:
        raise XhsCLIError("xhs note record has no note_id")
    images = _image_urls(record)
    videos = _video_urls(record)
    note_type = _text(record.get("type")) or ("video" if videos else "normal")
    if note_type.lower() in {"normal", "image", "images"}:
        note_type = "image"
    elif note_type.lower() == "video" or videos:
        note_type = "video"
    user = record.get("user") if isinstance(record.get("user"), Mapping) else {}
    author = _text(_first(record, "nickname", "author")) or _text(
        _first(user, "nickname", "name")
    )
    author_user_id = _text(_first(record, "user_id", "userId")) or _text(
        _first(user, "user_id", "userId")
    )
    tags = record.get("tags") or record.get("tag_list") or record.get("tagList") or []
    tag_names = [
        _text(tag.get("name")) if isinstance(tag, Mapping) else _text(tag)
        for tag in tags
    ]
    return Note(
        note_id=note_id,
        author=author,
        title=_text(_first(record, "title", "display_title", "displayTitle")),
        body=_text(_first(record, "desc", "body", "description")),
        note_type=note_type,
        published_at=_timestamp(_first(record, "time", "published_at", "publishedAt")),
        image_urls=[] if note_type == "video" else images,
        video_urls=videos,
        cover_urls=images if note_type == "video" else [],
        source_url=public_source_url(
            source_url or _text(record.get("url")) or _public_note_url(note_id)
        ),
        final_url=public_source_url(_text(record.get("url")) or _public_note_url(note_id)),
        author_user_id=author_user_id or None,
        tags=[tag for tag in tag_names if tag],
        engagement={
            key: str(record[key])
            for key in ("liked_count", "collected_count", "comment_count", "share_count")
            if key in record
        },
        raw=_clean_raw(dict(record)),
    )


class XhsCLIClient:
    def __init__(
        self,
        executable: str = "xhs",
        config_dir: Path | None = None,
        cache_dir: Path | None = None,
        cookie_file: Path | None = None,
        rate: str = "2s",
        timeout: float = 45,
    ) -> None:
        self.executable = executable
        self.config_dir = config_dir
        self.cache_dir = cache_dir
        self.cookie_file = cookie_file
        self.rate = rate
        self.timeout = timeout

    def _environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        if self.config_dir:
            environment["XHS_CONFIG_DIR"] = str(self.config_dir)
        if self.cache_dir:
            environment["XHS_CACHE_DIR"] = str(self.cache_dir)
        if self.cookie_file and self.cookie_file.exists():
            environment["XHS_COOKIE_FILE"] = str(self.cookie_file)
        return environment

    async def _run(self, *args: str) -> list[dict[str, Any]]:
        if not shutil.which(self.executable) and not Path(self.executable).exists():
            raise XhsCLIError(
                f"xhs CLI not found at {self.executable!r}; install xhs 0.2.0 or set XHS_CLI_PATH"
            )
        command = [self.executable, *args]
        try:
            completed = await asyncio.to_thread(
                subprocess.run,
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=self._environment(),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise XhsCLIError(f"xhs CLI execution failed: {type(exc).__name__}") from exc
        if completed.returncode != 0:
            # stderr may contain URLs/tokens, so only expose the exit code.
            raise XhsCLIError(f"xhs CLI exited with code {completed.returncode}")
        return _records(completed.stdout)

    async def resolve_share_url(self, url: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                response = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                response.raise_for_status()
                return str(response.url)
        except httpx.HTTPError as exc:
            raise XhsCLIError(f"share URL resolution failed: {type(exc).__name__}") from exc

    async def identity(self, user_id: str) -> ProfileIdentity:
        records = await self._run("user", user_id, "-o", "json")
        if not records:
            raise XhsCLIError("xhs user returned no identity record")
        record = records[0]
        return ProfileIdentity(
            user_id=_text(_first(record, "user_id", "userId")) or user_id,
            nickname=_text(_first(record, "nickname", "name")),
            red_id=_text(_first(record, "red_id", "redId")),
            profile_url=_text(record.get("url"))
            or f"https://www.xiaohongshu.com/user/profile/{user_id}",
        )

    async def list_user_notes(self, user_id: str, limit: int) -> list[ProfileCandidate]:
        records = await self._run(
            "user",
            user_id,
            "--notes",
            "-n",
            str(limit),
            "--rate",
            self.rate,
            "-o",
            "jsonl",
        )
        candidates: list[ProfileCandidate] = []
        seen: set[str] = set()
        for record in records:
            try:
                candidate = parse_candidate(record)
            except XhsCLIError:
                continue
            if candidate.note_id not in seen:
                candidates.append(candidate)
                seen.add(candidate.note_id)
        return candidates

    async def note(self, note_id: str, token: str = "", source_url: str = "") -> Note:
        target = _public_note_url(note_id, token)
        records = await self._run("note", target, "-o", "json")
        if not records:
            raise XhsCLIError(f"xhs note returned no detail for {note_id}")
        return parse_note(records[0], source_url or target)

    async def note_from_url(self, source_url: str) -> Note:
        records = await self._run("note", source_url, "-o", "json")
        if not records:
            raise XhsCLIError("xhs note returned no detail for supplied URL")
        return parse_note(records[0], source_url)

    async def resolve_profile_identity(
        self, profile_url: str, expected_user_id: str, expected_nickname: str
    ) -> ProfileIdentity:
        final_url = await self.resolve_share_url(profile_url)
        identity = await self.identity(expected_user_id)
        if identity.user_id != expected_user_id:
            raise XhsCLIError("resolved identity user_id does not match configured expected_user_id")
        path_parts = [part for part in urlsplit(final_url).path.split("/") if part]
        if "profile" not in path_parts:
            raise XhsCLIError("profile share link did not resolve to a profile URL")
        profile_index = path_parts.index("profile")
        if profile_index + 1 >= len(path_parts) or path_parts[profile_index + 1] != expected_user_id:
            raise XhsCLIError("profile share link resolved to a different user_id")
        if identity.nickname != expected_nickname:
            raise XhsCLIError(
                f"target nickname mismatch: expected {expected_nickname!r}, got {identity.nickname!r}"
            )
        identity.final_url = public_source_url(final_url)
        return identity
