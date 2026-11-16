from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from app.x_client import XClient


def test_video_upload_uses_chunked_v2_flow(tmp_path: Path) -> None:
    media = tmp_path / "video.mp4"
    media.write_bytes(b"video-bytes")
    calls: list[tuple[str, str, dict[str, object]]] = []
    client = XClient("https://api.x.com", "token")

    async def fake_request(method: str, path: str, **kwargs: object) -> dict[str, object]:
        calls.append((method, path, kwargs))
        if path.endswith("/initialize"):
            return {"data": {"id": "123"}}
        if path.endswith("/finalize"):
            return {"data": {"id": "123"}}
        if path.endswith("/append"):
            return {"data": {}}
        raise AssertionError(path)

    client._request = fake_request  # type: ignore[method-assign]
    assert asyncio.run(client.upload_video(media)) == "123"
    assert [path for _, path, _ in calls] == [
        "/2/media/upload/initialize",
        "/2/media/upload/123/append",
        "/2/media/upload/123/finalize",
    ]
    assert calls[0][2]["json"] == {
        "total_bytes": len(b"video-bytes"),
        "media_type": "video/mp4",
        "media_category": "tweet_video",
    }


def test_oauth1_signing_uses_user_context_without_secret_in_header() -> None:
    client = XClient(
        "https://api.x.com",
        auth_mode="oauth1",
        consumer_key="consumer-key",
        consumer_secret="consumer-secret",
        resource_owner_key="access-token",
        resource_owner_secret="access-secret",
    )

    url, headers, body = client._sign_oauth1(
        "https://api.x.com/2/tweets",
        "POST",
        body='{"text":"hello"}',
        headers={"Content-Type": "application/json"},
    )

    assert url == "https://api.x.com/2/tweets"
    assert body == '{"text":"hello"}'
    assert headers["Authorization"].startswith("OAuth ")
    assert "consumer-key" in headers["Authorization"]
    assert "consumer-secret" not in headers["Authorization"]
    assert "access-secret" not in headers["Authorization"]


def test_oauth1_video_upload_uses_legacy_chunked_commands(tmp_path: Path) -> None:
    media = tmp_path / "video.mp4"
    media.write_bytes(b"video-bytes")
    form_calls: list[dict[str, object]] = []
    append_calls: list[dict[str, object]] = []
    client = XClient(
        "https://api.x.com",
        auth_mode="oauth1",
        consumer_key="ck",
        consumer_secret="cs",
        resource_owner_key="tk",
        resource_owner_secret="ts",
    )

    async def fake_form(
        method: str, url: str, form: dict[str, object]
    ) -> dict[str, object]:
        form_calls.append(form)
        if form["command"] == "INIT":
            return {"media_id_string": "123"}
        return {"processing_info": {"state": "succeeded"}}

    async def fake_multipart(
        url: str,
        form: dict[str, object],
        media_bytes: bytes,
        media_type: str,
        name: str,
    ) -> dict[str, object]:
        append_calls.append(form)
        assert media_bytes == b"video-bytes"
        assert media_type == "video/mp4"
        assert name == "video.mp4"
        return {}

    client._oauth1_form_request = fake_form  # type: ignore[method-assign]
    client._oauth1_multipart_request = fake_multipart  # type: ignore[method-assign]

    assert asyncio.run(client.upload_video(media)) == "123"
    assert [call["command"] for call in form_calls] == ["INIT", "FINALIZE", "STATUS"]
    assert append_calls == [{"command": "APPEND", "media_id": "123", "segment_index": 0}]


def test_oauth1_v1_1_post_uses_status_update_form() -> None:
    client = XClient(
        "https://api.x.com",
        auth_mode="oauth1",
        post_api_version="v1.1",
        consumer_key="ck",
        consumer_secret="cs",
        resource_owner_key="tk",
        resource_owner_secret="ts",
    )
    calls: list[tuple[str, str, dict[str, object]]] = []

    async def fake_form(
        method: str, url: str, form: dict[str, object]
    ) -> dict[str, object]:
        calls.append((method, url, form))
        return {"id_str": "456"}

    client._oauth1_form_request = fake_form  # type: ignore[method-assign]

    assert asyncio.run(client.create_post("hello", ["123", "124"], reply_to="99")) == "456"
    assert calls == [
        (
            "POST",
            "https://api.x.com/1.1/statuses/update.json",
            {
                "status": "hello",
                "media_ids": "123,124",
                "in_reply_to_status_id": "99",
            },
        )
    ]


def test_http_error_message_keeps_safe_x_error_message() -> None:
    response = httpx.Response(
        401,
        json={"errors": [{"code": 89, "message": "Invalid or expired token."}]},
    )
    assert XClient._http_error_message(response) == "HTTP 401: Invalid or expired token."


def test_empty_legacy_append_response_is_successful() -> None:
    assert XClient._payload(httpx.Response(204)) == {}
    assert XClient._payload(httpx.Response(200, content=b"")) == {}
