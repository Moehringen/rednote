import asyncio
from pathlib import Path

import httpx

from app.media import download_media_urls


def test_download_media_preserves_bytes_and_referer(tmp_path: Path) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["referer"] = request.headers.get("referer", "")
        return httpx.Response(
            200,
            headers={"content-type": "image/jpeg"},
            content=b"\xff\xd8\xff\xe0jpeg-bytes",
        )

    paths = asyncio.run(
        download_media_urls(
            ["https://cdn.example/image"],
            tmp_path,
            "image",
            transport=httpx.MockTransport(handler),
        )
    )
    assert paths[0].read_bytes() == b"\xff\xd8\xff\xe0jpeg-bytes"
    assert seen["referer"] == "https://www.xiaohongshu.com/"
