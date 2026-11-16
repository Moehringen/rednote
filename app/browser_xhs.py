from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import httpx

from .models import Note, ProfileCandidate, ProfileIdentity
from .url_utils import public_source_url
from .xhs_cli import XhsCLIError


class BrowserLoginRequired(XhsCLIError):
    """The visible persistent browser needs the user to log in again."""


def _published_at(value: str, timezone: str) -> str | None:
    text = " ".join(value.split())
    if not text:
        return None
    zone = ZoneInfo(timezone)
    now = datetime.now(zone)
    match = re.match(r"(\d+)\s*d\s+ago", text, re.I)
    if match:
        return (now - timedelta(days=int(match.group(1)))).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
    match = re.match(r"(\d+)\s*w\s+ago", text, re.I)
    if match:
        return (now - timedelta(weeks=int(match.group(1)))).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
    match = re.search(r"(?:today|今天)[,， ]*(\d{1,2}):(\d{2})", text, re.I)
    if match:
        return now.replace(
            hour=int(match.group(1)), minute=int(match.group(2)), second=0, microsecond=0
        ).isoformat()
    match = re.search(r"(?:yesterday|昨天)[,， ]*(\d{1,2}):(\d{2})", text, re.I)
    if match:
        return (now - timedelta(days=1)).replace(
            hour=int(match.group(1)), minute=int(match.group(2)), second=0, microsecond=0
        ).isoformat()
    for fmt in ("%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%b %d, %Y", "%b %d %Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=zone).isoformat()
        except ValueError:
            continue
    match = re.search(r"\b([A-Z][a-z]{2})\s+(\d{1,2})(?:,\s*(\d{4}))?\b", text)
    if match:
        year = int(match.group(3) or now.year)
        try:
            month = datetime.strptime(match.group(1), "%b").month
            return datetime(year, month, int(match.group(2)), tzinfo=zone).isoformat()
        except ValueError:
            pass
    return text


_PROFILE_ROWS_SCRIPT = r"""
sections => sections.map(section => {
  const links = Array.from(section.querySelectorAll("a[href]"));
  const href = links
    .map(a => (a.getAttribute("href") || "").split("?")[0])
    .find(value => /\/user\/profile\/[^/]+\/[A-Za-z0-9]+$/i.test(value)) || "";
  const match = href.match(/\/user\/profile\/[^/]+\/([A-Za-z0-9]+)$/i);
  const title = links
    .map(a => (a.textContent || "").trim())
    .find(value => value && value !== "琢磨鸟" && !/^Pinned$/i.test(value)) || "";
  return {
    note_id: match ? match[1] : "",
    title,
    href,
    is_pinned: /Pinned/i.test(section.textContent || ""),
  };
}).filter(row => row.note_id)
"""


_PROFILE_READY_SCRIPT = r"""
links => links.some(link => {
  const href = (link.getAttribute("href") || "").split("?")[0];
  return /\/user\/profile\/[^/]+\/[A-Za-z0-9]+$/i.test(href);
})
"""


_DETAIL_SCRIPT = r"""
() => {
  const noteContent = document.querySelector(".note-content");
  const bodyNode = noteContent?.querySelector(".note-text > span") ||
    noteContent?.querySelector(".note-text");
  const tags = Array.from(noteContent?.querySelectorAll("a[href*='/search_result']") || [])
    .map(a => (a.textContent || "").trim())
    .filter(Boolean);
  const images = Array.from(document.querySelectorAll(".note-container img"))
    .filter(img => {
      const cls = String(img.className || "").toLowerCase();
      return img.src && !/avatar|emoji|logo|user-image|author-avatar|reds-img/.test(cls);
    })
    .map(img => img.currentSrc || img.src)
    .filter(Boolean);
  const videos = Array.from(document.querySelectorAll(".note-container video"))
    .map(video => video.currentSrc || video.src || video.querySelector("source")?.src || "")
    .filter(Boolean);
  const covers = Array.from(document.querySelectorAll(".note-container video[poster]"))
    .map(video => video.poster)
    .filter(Boolean);
  return {
    title: noteContent?.querySelector(".title")?.textContent?.trim() || "",
    body: bodyNode?.textContent?.trim() || "",
    published_at: noteContent?.querySelector(".date, .bottom-container .date")?.textContent?.trim() || "",
    tags,
    images: Array.from(new Set(images)),
    videos: Array.from(new Set(videos)),
    covers: Array.from(new Set(covers)),
    url: location.href.split("?")[0],
  };
}
"""


class BrowserXhsClient:
    """Read-only XHS collector backed by a visible persistent browser profile."""

    def __init__(
        self,
        profile_url: str,
        expected_user_id: str,
        expected_nickname: str,
        expected_red_id: str,
        browser_profile_dir: Path,
        executable_path: str = "",
        headless: bool = False,
        login_timeout: float = 180,
        timezone: str = "Asia/Singapore",
    ) -> None:
        self.profile_url = profile_url
        self.expected_user_id = expected_user_id
        self.expected_nickname = expected_nickname
        self.expected_red_id = expected_red_id
        self.browser_profile_dir = browser_profile_dir
        self.executable_path = executable_path
        self.headless = headless
        self.login_timeout = login_timeout
        self.timezone = timezone
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._browser_process: subprocess.Popen[bytes] | None = None
        self.attach_existing = os.getenv("BROWSER_ATTACH_EXISTING", "false").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.remote_debugging_port = int(os.getenv("BROWSER_REMOTE_DEBUGGING_PORT", "9222"))
        self._identity: ProfileIdentity | None = None

    async def _start(self) -> None:
        if self._page is not None:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise XhsCLIError(
                "browser backend requires requirements-browser.txt; install Playwright first"
            ) from exc
        self.browser_profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()
        endpoint = f"http://127.0.0.1:{self.remote_debugging_port}"
        if not self.attach_existing:
            executable = self._browser_executable()
            command = [
                str(executable),
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-breakpad",
                "--disable-crash-reporter",
                "--no-first-run",
                "--no-default-browser-check",
                f"--user-data-dir={self.browser_profile_dir}",
                "--remote-debugging-address=127.0.0.1",
                f"--remote-debugging-port={self.remote_debugging_port}",
                "--remote-allow-origins=*",
                "--window-size=1440,1000",
            ]
            if self.headless:
                command.append("--headless=new")
            command.append("about:blank")
            browser_env = os.environ.copy()
            browser_env["CHROME_CRASHPAD_DISABLE"] = "1"
            self._browser_process = subprocess.Popen(
                command,
                env=browser_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        try:
            await self._wait_for_cdp(endpoint)
        except XhsCLIError:
            await self._stop_browser_process()
            raise
        self._browser = await self._playwright.chromium.connect_over_cdp(endpoint)
        self._context = self._browser.contexts[0] if self._browser.contexts else None
        if self._context is None:
            raise XhsCLIError("browser remote debugging returned no persistent context")
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        self._page.set_default_timeout(15_000)

    async def _wait_for_cdp(self, endpoint: str) -> None:
        for _ in range(150):
            if self._browser_process is not None and self._browser_process.poll() is not None:
                raise XhsCLIError(
                    "browser process exited before its remote debugging endpoint became ready"
                )
            try:
                async with httpx.AsyncClient(timeout=1) as client:
                    response = await client.get(f"{endpoint}/json/version")
                if response.is_success:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
        raise XhsCLIError("browser remote debugging endpoint did not become ready")

    async def _stop_browser_process(self) -> None:
        process = self._browser_process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
        try:
            await asyncio.to_thread(process.wait, 5)
        except subprocess.TimeoutExpired:
            process.kill()
            await asyncio.to_thread(process.wait, 5)
        self._browser_process = None

    def _browser_executable(self) -> Path:
        if self.executable_path:
            executable = Path(self.executable_path).expanduser()
            if not executable.exists():
                raise XhsCLIError(f"browser executable not found: {executable}")
            return executable
        for name in ("chromium", "chromium-browser", "google-chrome"):
            executable = shutil.which(name)
            if executable:
                return Path(executable)
        if self._playwright is None:
            raise XhsCLIError("browser runtime is not initialized")
        return Path(self._playwright.chromium.executable_path)

    async def close(self) -> None:
        if self._browser is not None and not self.attach_existing:
            await self._browser.close()
        if self._context is not None:
            self._context = None
        if self._playwright is not None:
            await self._playwright.stop()
        await self._stop_browser_process()
        self._browser = None
        self._page = None
        self._playwright = None

    async def _navigate(self, url: str) -> None:
        await self._start()
        try:
            await self._page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        except Exception as exc:
            raise XhsCLIError(f"browser navigation failed: {type(exc).__name__}") from exc
        await self._page.wait_for_timeout(1_200)

    async def _open_profile(self) -> None:
        await self._navigate(self.profile_url)
        note_links = self._page.locator("section.note-item a[href]")
        if await note_links.count() and await note_links.evaluate_all(_PROFILE_READY_SCRIPT):
            return
        body = (await self._page.locator("body").inner_text()).lower()
        login_page = any(
            marker in body
            for marker in ("scan via rednote", "sign in", "log in", "登录", "扫码")
        )
        if not self.headless:
            try:
                deadline = self.login_timeout
                while deadline > 0:
                    note_links = self._page.locator("section.note-item a[href]")
                    if await note_links.count() and await note_links.evaluate_all(
                        _PROFILE_READY_SCRIPT
                    ):
                        return
                    await self._page.wait_for_timeout(1_000)
                    deadline -= 1
            except Exception as exc:
                raise BrowserLoginRequired(
                    "persistent browser session needs login; log in in the visible browser and retry"
                ) from exc
            if login_page:
                raise BrowserLoginRequired(
                    "persistent browser session needs login; log in in the visible browser and retry"
                )
        raise BrowserLoginRequired(
            "persistent browser did not expose profile note links; login or complete verification in the browser"
        )

    async def resolve_profile_identity(
        self, profile_url: str, expected_user_id: str, expected_nickname: str
    ) -> ProfileIdentity:
        self.profile_url = profile_url
        await self._open_profile()
        current_url = self._page.url
        match = re.search(r"/user/profile/([^/?#]+)", current_url)
        user_id = match.group(1) if match else ""
        body = await self._page.locator("body").inner_text()
        lines = [item.strip() for item in body.splitlines() if item.strip()]
        line = next((item for item in lines if "rednote ID:" in item), "")
        if not user_id or user_id != expected_user_id:
            raise XhsCLIError("browser profile URL did not verify the configured user_id")
        if not line:
            raise XhsCLIError("browser profile did not expose a rednote ID")
        nickname = next((item for item in lines if item == expected_nickname), "")
        red_id = line.split("rednote ID:", 1)[1].strip()
        red_id = red_id.strip().split()[0] if red_id.strip() else ""
        if nickname != expected_nickname:
            raise XhsCLIError("browser profile nickname did not match the configured target")
        if self.expected_red_id and red_id != self.expected_red_id:
            raise XhsCLIError("browser profile rednote ID did not match the configured target")
        self._identity = ProfileIdentity(
            user_id=user_id,
            nickname=nickname,
            red_id=red_id,
            profile_url=public_source_url(current_url),
            final_url=public_source_url(current_url),
        )
        # After the first short-link resolution, keep using the verified
        # canonical profile URL.  Re-opening the share short link for every
        # candidate can leave the attached browser on its interstitial page
        # and unnecessarily wait for the login timeout.
        self.profile_url = self._identity.final_url
        return self._identity

    async def identity(self, user_id: str) -> ProfileIdentity:
        if self._identity and self._identity.user_id == user_id:
            return self._identity
        return await self.resolve_profile_identity(
            self.profile_url, user_id, self.expected_nickname
        )

    async def list_user_notes(self, user_id: str, limit: int) -> list[ProfileCandidate]:
        identity = await self.identity(user_id)
        rows = await self._page.locator("section.note-item").evaluate_all(_PROFILE_ROWS_SCRIPT)
        result: list[ProfileCandidate] = []
        for row in rows[: max(1, limit)]:
            note_id = str(row.get("note_id", ""))
            if not note_id:
                continue
            result.append(
                ProfileCandidate(
                    note_id=note_id,
                    title=str(row.get("title", "")),
                    source_url=public_source_url(
                        urljoin(self._page.url, str(row.get("href", "")))
                    ),
                    is_pinned=bool(row.get("is_pinned")),
                    author=identity.nickname,
                    author_user_id=identity.user_id,
                )
            )
        return result

    async def _open_note_from_profile(self, note_id: str) -> None:
        await self._open_profile()
        selector = f"section.note-item a[href*='/{note_id}']"
        link = self._page.locator(selector).last
        if await link.count() == 0:
            for _ in range(3):
                await self._page.mouse.wheel(0, 900)
                await self._page.wait_for_timeout(700)
                link = self._page.locator(selector).last
                if await link.count():
                    break
        if await link.count() == 0:
            raise XhsCLIError(f"browser profile did not expose note {note_id}")
        # The profile cards use a client-side click handler which can create a
        # short-lived target when attached to an already-running Chromium over
        # CDP.  Navigating to the public href directly is deterministic and
        # keeps the persistent page alive for the next candidate.
        href = await link.get_attribute("href")
        if not href:
            raise XhsCLIError(f"browser note link had no href for {note_id}")
        await self._navigate(urljoin(self._page.url, href))
        if not re.search(
            rf"/(?:discovery/item|explore|user/profile/[^/]+)/{re.escape(note_id)}(?:$|[/?#])",
            self._page.url,
        ):
            raise XhsCLIError(f"browser did not open note detail {note_id}")

    async def note(self, note_id: str, token: str = "", source_url: str = "") -> Note:
        await self._open_note_from_profile(note_id)
        return await self._extract_note(note_id, source_url)

    async def note_from_url(self, source_url: str) -> Note:
        await self._navigate(source_url)
        match = re.search(
            r"/(?:discovery/item|explore|user/profile/[^/]+)/([A-Za-z0-9]+)", self._page.url
        )
        if not match:
            raise XhsCLIError("browser note URL did not resolve to a note detail page")
        return await self._extract_note(match.group(1), source_url)

    async def _extract_note(self, note_id: str, source_url: str) -> Note:
        raw = await self._page.evaluate(_DETAIL_SCRIPT)
        images = [str(item) for item in raw.get("images", []) if item]
        videos = [str(item) for item in raw.get("videos", []) if item]
        covers = [str(item) for item in raw.get("covers", []) if item]
        note_type = "video" if videos else ("image" if images else "unsupported")
        current_url = public_source_url(str(raw.get("url", "")))
        return Note(
            note_id=note_id,
            author=self._identity.nickname if self._identity else "",
            title=str(raw.get("title", "")),
            body=str(raw.get("body", "")),
            note_type=note_type,
            published_at=_published_at(str(raw.get("published_at", "")), self.timezone),
            image_urls=[] if note_type == "video" else images,
            video_urls=videos,
            cover_urls=covers,
            source_url=public_source_url(source_url or current_url),
            final_url=current_url,
            author_user_id=self._identity.user_id if self._identity else None,
            tags=[str(item) for item in raw.get("tags", []) if item],
            raw={"collector": "persistent_browser", "browser_url": current_url},
        )
