from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from .thread_builder import DraftPost

LOGGER = logging.getLogger(__name__)


class XAPIError(RuntimeError):
    pass


class XClient:
    def __init__(
        self,
        base_url: str,
        access_token: str,
        upload_path: str = "/2/media/upload",
        timeout: float = 45,
        attempts: int = 3,
        max_chars: int = 280,
    ) -> None:
        if not access_token:
            raise XAPIError("X_USER_ACCESS_TOKEN is missing")
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self.upload_path = upload_path
        self.timeout = timeout
        self.attempts = max(1, attempts)
        self.max_chars = max_chars

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, self.attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.request(
                        method,
                        f"{self.base_url}{path}",
                        headers={"Authorization": f"Bearer {self.access_token}"},
                        **kwargs,
                    )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise XAPIError("X API returned a non-object response")
                if payload.get("errors"):
                    raise XAPIError("X API returned an error payload")
                return payload
            except (httpx.HTTPError, ValueError, XAPIError) as exc:
                last_error = exc
                if attempt < self.attempts:
                    await asyncio.sleep(min(2**attempt, 8))
        raise XAPIError(f"X API request failed: {last_error or 'unknown error'}")

    async def upload_image(self, path: Path) -> str:
        if path.stat().st_size > 5 * 1024 * 1024:
            raise XAPIError(f"image exceeds configured 5 MB limit: {path.name}")
        content_type = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }.get(path.suffix.lower(), "application/octet-stream")
        return await self._chunked_upload(path, content_type, "tweet_image")

    async def upload_video(self, path: Path) -> str:
        if path.stat().st_size == 0:
            raise XAPIError("video file is empty")
        return await self._chunked_upload(path, "video/mp4", "tweet_video")

    async def _chunked_upload(self, path: Path, media_type: str, media_category: str) -> str:
        payload = await self._request(
            "POST",
            f"{self.upload_path.rstrip('/')}/initialize",
            json={
                "total_bytes": path.stat().st_size,
                "media_type": media_type,
                "media_category": media_category,
            },
        )
        media_id = self._media_id(payload)
        chunk_size = 4 * 1024 * 1024
        with path.open("rb") as file:
            segment_index = 0
            while chunk := file.read(chunk_size):
                await self._request(
                    "POST",
                    f"{self.upload_path.rstrip('/')}/{media_id}/append",
                    json={
                        "media": base64.b64encode(chunk).decode("ascii"),
                        "segment_index": segment_index,
                    },
                )
                segment_index += 1
        finalized = await self._request(
            "POST", f"{self.upload_path.rstrip('/')}/{media_id}/finalize"
        )
        if (finalized.get("data") or {}).get("processing_info"):
            await self._wait_for_processing(media_id)
        return media_id

    @staticmethod
    def _media_id(payload: dict[str, Any]) -> str:
        data = payload.get("data") or {}
        media_id = str(data.get("id") or data.get("media_id_string") or "")
        if not media_id:
            raise XAPIError("X media upload response has no media id")
        return media_id

    async def _wait_for_processing(self, media_id: str) -> None:
        for _ in range(self.attempts * 10):
            payload = await self._request(
                "GET", self.upload_path, params={"media_id": media_id, "command": "STATUS"}
            )
            info = (payload.get("data") or {}).get("processing_info") or payload.get(
                "processing_info"
            )
            if not info or info.get("state") == "succeeded":
                return
            if info.get("state") == "failed":
                raise XAPIError("X video processing failed")
            await asyncio.sleep(float(info.get("check_after_secs", 2)))
        raise XAPIError("X video processing timed out")

    async def create_post(
        self, text: str, media_ids: list[str], reply_to: str | None = None
    ) -> str:
        if len(text) > self.max_chars:
            raise XAPIError(f"post text exceeds X_MAX_CHARS={self.max_chars}")
        body: dict[str, Any] = {"text": text}
        if media_ids:
            body["media"] = {"media_ids": media_ids}
        if reply_to:
            body["reply"] = {"in_reply_to_tweet_id": reply_to}
        payload = await self._request("POST", "/2/tweets", json=body)
        tweet_id = str(payload.get("data", {}).get("id") or "")
        if not tweet_id:
            raise XAPIError("X post response has no tweet id")
        return tweet_id

    async def publish_thread(
        self,
        posts: list[DraftPost],
        saved_tweet_ids: list[str] | None = None,
        saved_media_ids: dict[str, list[str]] | None = None,
        on_progress: Callable[[list[str], dict[str, list[str]]], None] | None = None,
    ) -> list[str]:
        tweet_ids = list(saved_tweet_ids or [])
        media_ids_by_index = dict(saved_media_ids or {})
        previous_id = tweet_ids[-1] if tweet_ids else None
        for post in posts:
            if post.index < len(tweet_ids) and tweet_ids[post.index]:
                previous_id = tweet_ids[post.index]
                continue
            cache_key = str(post.index)
            media_ids = media_ids_by_index.get(cache_key, [])
            if not media_ids:
                if post.video_path:
                    media_ids = [await self.upload_video(Path(post.video_path))]
                else:
                    media_ids = [await self.upload_image(Path(path)) for path in post.image_paths]
                media_ids_by_index[cache_key] = media_ids
                if on_progress:
                    on_progress(tweet_ids, media_ids_by_index)
            tweet_id = await self.create_post(post.text, media_ids, reply_to=previous_id)
            while len(tweet_ids) <= post.index:
                tweet_ids.append("")
            tweet_ids[post.index] = tweet_id
            previous_id = tweet_id
            if on_progress:
                on_progress(tweet_ids, media_ids_by_index)
        return tweet_ids
