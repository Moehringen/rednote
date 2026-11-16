from __future__ import annotations

import asyncio
from pathlib import Path

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
