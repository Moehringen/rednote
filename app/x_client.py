from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from oauthlib.oauth1 import SIGNATURE_TYPE_AUTH_HEADER
from oauthlib.oauth1 import Client as OAuth1Signer

from .thread_builder import DraftPost

LOGGER = logging.getLogger(__name__)


class XAPIError(RuntimeError):
    pass


class XClient:
    def __init__(
        self,
        base_url: str,
        access_token: str = "",
        upload_path: str = "/2/media/upload",
        timeout: float = 45,
        attempts: int = 3,
        max_chars: int = 280,
        *,
        auth_mode: str = "oauth2",
        post_api_version: str = "v2",
        token_source: str = "env",
        xurl_app: str = "",
        xurl_path: str = "xurl",
        consumer_key: str = "",
        consumer_secret: str = "",
        resource_owner_key: str = "",
        resource_owner_secret: str = "",
        media_base_url: str = "https://upload.twitter.com",
        legacy_upload_path: str = "/1.1/media/upload.json",
    ) -> None:
        if auth_mode not in {"oauth1", "oauth2", "auto"}:
            raise XAPIError("X_AUTH_MODE must be oauth1, oauth2, or auto")
        if post_api_version not in {"v2", "v1.1"}:
            raise XAPIError("X_POST_API_VERSION must be v2 or v1.1")
        if token_source not in {"env", "xurl"}:
            raise XAPIError("X_TOKEN_SOURCE must be env or xurl")
        if token_source == "xurl" and not xurl_app:
            raise XAPIError("XURL_APP is required when X_TOKEN_SOURCE=xurl")
        oauth1_ready = all(
            (consumer_key, consumer_secret, resource_owner_key, resource_owner_secret)
        )
        if auth_mode == "auto":
            auth_mode = "oauth1" if oauth1_ready else "oauth2"
        if auth_mode == "oauth1" and not oauth1_ready:
            raise XAPIError(
                "OAuth1 credentials are incomplete; need consumer and access token four-tuple"
            )
        if auth_mode == "oauth2" and token_source == "env" and not access_token:
            raise XAPIError("X_USER_ACCESS_TOKEN is missing")
        if post_api_version == "v1.1" and auth_mode != "oauth1":
            raise XAPIError("X_POST_API_VERSION=v1.1 requires X_AUTH_MODE=oauth1")
        self.base_url = base_url.rstrip("/")
        self.auth_mode = auth_mode
        self.post_api_version = post_api_version
        self.access_token = access_token
        self.token_source = token_source
        self.xurl_app = xurl_app
        self.xurl_path = xurl_path
        self.upload_path = upload_path
        self.timeout = timeout
        self.attempts = max(1, attempts)
        self.max_chars = max_chars
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self.resource_owner_key = resource_owner_key
        self.resource_owner_secret = resource_owner_secret
        self.media_base_url = media_base_url.rstrip("/")
        self.legacy_upload_path = legacy_upload_path

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if self.auth_mode == "oauth1":
            return await self._request_oauth1(method, path, **kwargs)
        return await self._request_bearer(method, path, **kwargs)

    async def _request_bearer(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, self.attempts + 1):
            try:
                access_token = await self._current_access_token()
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.request(
                        method,
                        f"{self.base_url}{path}",
                        headers={"Authorization": f"Bearer {access_token}"},
                        **kwargs,
                    )
                response.raise_for_status()
                return self._payload(response)
            except httpx.HTTPStatusError as exc:
                last_error = XAPIError(self._http_error_message(exc.response))
                if attempt < self.attempts:
                    await asyncio.sleep(min(2**attempt, 8))
            except (httpx.HTTPError, ValueError, XAPIError) as exc:
                last_error = exc
                if attempt < self.attempts:
                    await asyncio.sleep(min(2**attempt, 8))
        raise XAPIError(f"X API request failed: {last_error or 'unknown error'}")

    async def _current_access_token(self) -> str:
        if self.token_source == "env":
            return self.access_token
        executable = shutil.which(self.xurl_path) or self.xurl_path
        process = await asyncio.create_subprocess_exec(
            executable,
            "token",
            "--app",
            self.xurl_app,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=20)
        token = stdout.decode("utf-8", "replace").strip()
        if process.returncode != 0 or not token:
            detail = stderr.decode("utf-8", "replace").strip()
            raise XAPIError(f"xurl token lookup failed{': ' + detail[:240] if detail else ''}")
        if any(char.isspace() for char in token) or token.lower().startswith("bearer "):
            raise XAPIError("xurl returned an invalid access token format")
        return token

    async def _request_oauth1(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """Call a JSON X API endpoint with OAuth 1.0a user-context signing."""
        json_body = kwargs.pop("json", None)
        params = kwargs.pop("params", None)
        headers = dict(kwargs.pop("headers", {}) or {})
        body = None
        if json_body is not None:
            body = json.dumps(json_body, ensure_ascii=False, separators=(",", ":"))
            headers.setdefault("Content-Type", "application/json")
        url, signed_headers, signed_body = self._sign_oauth1(
            f"{self.base_url}{path}", method, body=body, headers=headers, params=params
        )
        request_kwargs = dict(kwargs)
        request_kwargs["headers"] = signed_headers
        if signed_body is not None:
            request_kwargs["content"] = signed_body.encode("utf-8")
        last_error: Exception | None = None
        for attempt in range(1, self.attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.request(method, url, **request_kwargs)
                response.raise_for_status()
                return self._payload(response)
            except httpx.HTTPStatusError as exc:
                last_error = XAPIError(self._http_error_message(exc.response))
                if attempt < self.attempts:
                    await asyncio.sleep(min(2**attempt, 8))
            except (httpx.HTTPError, ValueError, XAPIError) as exc:
                last_error = exc
                if attempt < self.attempts:
                    await asyncio.sleep(min(2**attempt, 8))
        raise XAPIError(f"X API request failed: {last_error or 'unknown error'}")

    @staticmethod
    def _payload(response: httpx.Response) -> dict[str, Any]:
        if response.status_code == 204 or not response.content:
            return {}
        payload = response.json()
        if not isinstance(payload, dict):
            raise XAPIError("X API returned a non-object response")
        if payload.get("errors"):
            raise XAPIError("X API returned an error payload")
        return payload

    @staticmethod
    def _http_error_message(response: httpx.Response) -> str:
        try:
            payload = response.json()
            detail = None
            if isinstance(payload, dict):
                detail = payload.get("detail") or payload.get("title")
                errors = payload.get("errors")
                if not detail and isinstance(errors, list) and errors:
                    first = errors[0]
                    if isinstance(first, dict):
                        detail = first.get("detail") or first.get("message") or first.get("title")
            if detail:
                return f"HTTP {response.status_code}: {str(detail)[:240]}"
        except (ValueError, TypeError, IndexError, AttributeError):
            pass
        return f"HTTP {response.status_code}"

    def _sign_oauth1(
        self,
        url: str,
        method: str,
        *,
        body: str | None = None,
        headers: dict[str, str] | None = None,
        params: Any = None,
    ) -> tuple[str, dict[str, str], str | None]:
        if params:
            query = urlencode(params, doseq=True)
            url = f"{url}{'&' if '?' in url else '?'}{query}"
        signer = OAuth1Signer(
            self.consumer_key,
            client_secret=self.consumer_secret,
            resource_owner_key=self.resource_owner_key,
            resource_owner_secret=self.resource_owner_secret,
            signature_type=SIGNATURE_TYPE_AUTH_HEADER,
        )
        signed_url, signed_headers, signed_body = signer.sign(
            url,
            http_method=method.upper(),
            body=body,
            headers=headers or {},
        )
        return signed_url, signed_headers, signed_body

    async def _oauth1_form_request(
        self, method: str, url: str, form: dict[str, object]
    ) -> dict[str, Any]:
        body = urlencode(form)
        signed_url, headers, signed_body = self._sign_oauth1(
            url,
            method,
            body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        last_error: Exception | None = None
        for attempt in range(1, self.attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.request(
                        method,
                        signed_url,
                        headers=headers,
                        content=(signed_body or body).encode("utf-8"),
                    )
                response.raise_for_status()
                return self._payload(response)
            except httpx.HTTPStatusError as exc:
                last_error = XAPIError(self._http_error_message(exc.response))
                if attempt < self.attempts:
                    await asyncio.sleep(min(2**attempt, 8))
            except (httpx.HTTPError, ValueError, XAPIError) as exc:
                last_error = exc
                if attempt < self.attempts:
                    await asyncio.sleep(min(2**attempt, 8))
        raise XAPIError(f"X media request failed: {last_error or 'unknown error'}")

    async def _oauth1_multipart_request(
        self, url: str, form: dict[str, object], media: bytes, media_type: str, name: str
    ) -> dict[str, Any]:
        # OAuth 1.0a does not sign multipart body fields. The form values are sent
        # as multipart fields alongside the media segment.
        _, headers, _ = self._sign_oauth1(url, "POST")
        last_error: Exception | None = None
        for attempt in range(1, self.attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(
                        url,
                        headers=headers,
                        data={key: str(value) for key, value in form.items()},
                        files={"media": (name, media, media_type)},
                    )
                response.raise_for_status()
                return self._payload(response)
            except httpx.HTTPStatusError as exc:
                last_error = XAPIError(self._http_error_message(exc.response))
                if attempt < self.attempts:
                    await asyncio.sleep(min(2**attempt, 8))
            except (httpx.HTTPError, ValueError, XAPIError) as exc:
                last_error = exc
                if attempt < self.attempts:
                    await asyncio.sleep(min(2**attempt, 8))
        raise XAPIError(f"X media request failed: {last_error or 'unknown error'}")

    @property
    def _legacy_upload_url(self) -> str:
        return f"{self.media_base_url}{self.legacy_upload_path}"

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
        if self.auth_mode == "oauth1":
            return await self._oauth1_simple_media_upload(path, content_type)
        return await self._chunked_upload(path, content_type, "tweet_image")

    async def upload_video(self, path: Path) -> str:
        if path.stat().st_size == 0:
            raise XAPIError("video file is empty")
        if self.auth_mode == "oauth1":
            return await self._oauth1_chunked_media_upload(path, "video/mp4")
        return await self._chunked_upload(path, "video/mp4", "tweet_video")

    async def _oauth1_simple_media_upload(self, path: Path, media_type: str) -> str:
        payload = await self._oauth1_multipart_request(
            self._legacy_upload_url, {}, path.read_bytes(), media_type, path.name
        )
        return self._media_id(payload)

    async def _oauth1_chunked_media_upload(self, path: Path, media_type: str) -> str:
        payload = await self._oauth1_form_request(
            "POST",
            self._legacy_upload_url,
            {
                "command": "INIT",
                "total_bytes": path.stat().st_size,
                "media_type": media_type,
                "media_category": "tweet_video",
            },
        )
        media_id = self._media_id(payload)
        chunk_size = 4 * 1024 * 1024
        with path.open("rb") as file:
            segment_index = 0
            while chunk := file.read(chunk_size):
                await self._oauth1_multipart_request(
                    self._legacy_upload_url,
                    {
                        "command": "APPEND",
                        "media_id": media_id,
                        "segment_index": segment_index,
                    },
                    chunk,
                    media_type,
                    path.name,
                )
                segment_index += 1
        finalized = await self._oauth1_form_request(
            "POST",
            self._legacy_upload_url,
            {"command": "FINALIZE", "media_id": media_id},
        )
        if self._processing_info(finalized):
            await self._wait_for_oauth1_processing(media_id)
        return media_id

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
        data = payload.get("data") or payload
        media_id = str(data.get("id") or data.get("media_id_string") or "")
        if not media_id:
            raise XAPIError("X media upload response has no media id")
        return media_id

    @staticmethod
    def _processing_info(payload: dict[str, Any]) -> dict[str, Any] | None:
        return (payload.get("data") or {}).get("processing_info") or payload.get(
            "processing_info"
        )

    async def _wait_for_oauth1_processing(self, media_id: str) -> None:
        for _ in range(self.attempts * 10):
            payload = await self._oauth1_form_request(
                "POST",
                self._legacy_upload_url,
                {"command": "STATUS", "media_id": media_id},
            )
            info = self._processing_info(payload)
            if not info or info.get("state") == "succeeded":
                return
            if info.get("state") == "failed":
                raise XAPIError("X video processing failed")
            await asyncio.sleep(float(info.get("check_after_secs", 2)))
        raise XAPIError("X video processing timed out")

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
        if self.post_api_version == "v1.1":
            return await self._create_post_v1_1(text, media_ids, reply_to)
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

    async def _create_post_v1_1(
        self, text: str, media_ids: list[str], reply_to: str | None
    ) -> str:
        form: dict[str, object] = {"status": text}
        if media_ids:
            form["media_ids"] = ",".join(media_ids)
        if reply_to:
            form["in_reply_to_status_id"] = reply_to
        payload = await self._oauth1_form_request(
            "POST", f"{self.base_url}/1.1/statuses/update.json", form
        )
        tweet_id = str(payload.get("id_str") or payload.get("id") or "")
        if not tweet_id:
            raise XAPIError("legacy X post response has no status id")
        return tweet_id

    async def verify_credentials(self) -> dict[str, Any]:
        """Perform a read-only user-context check without publishing anything."""
        if self.post_api_version == "v1.1":
            return await self._request("GET", "/1.1/account/verify_credentials.json")
        return await self._request("GET", "/2/users/me")

    @staticmethod
    def validate_identity(
        payload: dict[str, Any], expected_user_id: str = "", expected_username: str = ""
    ) -> tuple[str, str]:
        """Fail closed if a route's credentials resolve to the wrong X account."""
        account = payload.get("data") or payload
        if not isinstance(account, dict):
            raise XAPIError("X identity response has no account object")
        user_id = str(account.get("id") or account.get("id_str") or "")
        username = str(account.get("username") or account.get("screen_name") or "")
        if expected_user_id and user_id != expected_user_id:
            raise XAPIError(
                f"X identity mismatch; expected user_id {expected_user_id}, got {user_id or 'missing'}"
            )
        normalized_expected = expected_username.lstrip("@").casefold()
        if normalized_expected and username.casefold() != normalized_expected:
            raise XAPIError(
                f"X identity mismatch; expected username @{normalized_expected}, got @{username or 'missing'}"
            )
        return user_id, username

    async def publish_thread(
        self,
        posts: list[DraftPost],
        saved_tweet_ids: list[str] | None = None,
        saved_media_ids: dict[str, list[str]] | None = None,
        on_progress: Callable[[list[str], dict[str, list[str]]], None] | None = None,
    ) -> list[str]:
        """Publish one post; the legacy method name is kept for state compatibility."""
        if len(posts) != 1 or posts[0].index != 0:
            raise XAPIError("single-post mode requires exactly one draft post at index 0")
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
