from __future__ import annotations

import asyncio
import logging
import mimetypes
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from .models import Note

LOGGER = logging.getLogger(__name__)


class MediaDownloadError(RuntimeError):
    pass


def _valid_image_signature(data: bytes) -> bool:
    """Reject HTML/error bodies and mislabeled non-image downloads."""
    return (
        data.startswith(b"\xff\xd8\xff")
        or data.startswith(b"\x89PNG\r\n\x1a\n")
        or data.startswith((b"GIF87a", b"GIF89a"))
        or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")
        or data.startswith(b"\x00\x00\x01\x00")
    )


def _extension(url: str, content_type: str, default: str = ".bin") -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    allowed = {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".gif",
        ".avif",
        ".mp4",
        ".mov",
        ".m4v",
        ".webm",
    }
    if suffix in allowed:
        return suffix
    return mimetypes.guess_extension(content_type.split(";", 1)[0].strip()) or default


async def _download(
    client: httpx.AsyncClient,
    url: str,
    destination: Path,
    expected_prefix: str,
    attempts: int,
) -> Path:
    last_error: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        temporary = destination.with_suffix(destination.suffix + ".part")
        try:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if expected_prefix and content_type and not content_type.startswith(expected_prefix):
                    raise MediaDownloadError(
                        f"unexpected media content-type {content_type!r} for {urlparse(url).path}"
                    )
                content_length = response.headers.get("content-length")
                if content_length == "0":
                    raise MediaDownloadError("media response was empty")
                with temporary.open("wb") as file:
                    async for chunk in response.aiter_bytes(1024 * 1024):
                        file.write(chunk)
            if not temporary.exists() or temporary.stat().st_size == 0:
                raise MediaDownloadError("media file was empty after download")
            if expected_prefix == "image/":
                with temporary.open("rb") as file:
                    if not _valid_image_signature(file.read(16)):
                        raise MediaDownloadError(
                            "image response did not have a valid image signature"
                        )
            actual_suffix = _extension(url, content_type, destination.suffix or ".bin")
            if actual_suffix != destination.suffix:
                destination = destination.with_suffix(actual_suffix)
            temporary.replace(destination)
            return destination
        except (httpx.HTTPError, OSError, MediaDownloadError) as exc:
            last_error = exc
            temporary.unlink(missing_ok=True)
            if attempt < attempts:
                await asyncio.sleep(min(2**attempt, 8))
    raise MediaDownloadError(
        f"media download failed: {type(last_error).__name__ if last_error else 'unknown'}"
    )


async def download_images(
    urls: list[str], output_dir: Path, timeout: float = 45, attempts: int = 3
) -> list[Path]:
    return await download_media_urls(urls, output_dir, "image", timeout, attempts)


async def download_media_urls(
    urls: list[str],
    output_dir: Path,
    media_kind: str,
    timeout: float = 45,
    attempts: int = 3,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[Path] = []
    prefix = "image/" if media_kind == "image" else "video/"
    default_extension = ".jpg" if media_kind == "image" else ".mp4"
    headers = {
        "Accept": "image/*" if media_kind == "image" else "video/*,*/*;q=0.8",
        "Referer": "https://www.xiaohongshu.com/",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/128 Safari/537.36",
    }
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=True, headers=headers, transport=transport
    ) as client:
        for index, raw_url in enumerate(urls, start=1):
            url = urljoin("https:", raw_url)
            suffix = _extension(url, "", default_extension)
            destination = output_dir / (
                f"{index:02d}{suffix}" if media_kind == "image" else f"video{suffix}"
            )
            try:
                result = await _download(client, url, destination, prefix, max(1, attempts))
            except MediaDownloadError:
                raise
            results.append(result)
    LOGGER.info("downloaded %d/%d %s assets", len(results), len(urls), media_kind)
    return results


async def download_note_media(
    note: Note, output_dir: Path, timeout: float = 45, attempts: int = 3
) -> list[Path]:
    if note.note_type == "video":
        if not note.video_urls:
            raise MediaDownloadError("video note has no playable video URL")
        video_paths = await download_media_urls(
            note.video_urls[:1], output_dir, "video", timeout, attempts
        )
        cover_paths = (
            await download_media_urls(
                note.cover_urls[:1], output_dir / "cover", "image", timeout, attempts
            )
            if note.cover_urls
            else []
        )
        return video_paths + cover_paths
    return await download_media_urls(
        note.image_urls, output_dir / "images", "image", timeout, attempts
    )
