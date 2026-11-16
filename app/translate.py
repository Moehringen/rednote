from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from abc import ABC, abstractmethod

import httpx

from .models import Note, Translation

LOGGER = logging.getLogger(__name__)
_HASHTAG = re.compile(r"#[\w\u4e00-\u9fff-]+")
_PROVIDER_MARKUP = re.compile(r"<[^>]+>")
_MAX_PUBLIC_QUERY = 450


class TranslationError(RuntimeError):
    """Translation could not be completed without inventing content."""


def parse_translation(text: str) -> Translation:
    title_marker, body_marker = "TITLE:", "BODY:"
    if title_marker not in text or body_marker not in text:
        raise TranslationError("translation provider did not return TITLE/BODY sections")
    title_part, body_part = text.split(body_marker, 1)
    title = title_part.split(title_marker, 1)[1].strip()
    body = body_part.strip()
    if not title and not body:
        raise TranslationError("translation provider returned empty content")
    return Translation(title=title, body=body)


def _protect_tags(text: str) -> tuple[str, dict[str, str]]:
    replacements: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        key = f"__XHS_TAG_{len(replacements)}__"
        replacements[key] = match.group(0)
        return key

    return _HASHTAG.sub(replace, text), replacements


def _restore_tags(text: str, replacements: dict[str, str]) -> str:
    for placeholder, value in replacements.items():
        text = text.replace(placeholder, value)
    return text


def _clean_provider_text(text: str) -> str:
    return _PROVIDER_MARKUP.sub("", html.unescape(text)).strip()


def _chunks(text: str, maximum: int = _MAX_PUBLIC_QUERY) -> list[str]:
    if not text:
        return [""]
    return [text[index : index + maximum] for index in range(0, len(text), maximum)]


class Translator(ABC):
    @abstractmethod
    async def translate(self, note: Note) -> Translation:
        raise NotImplementedError


class MyMemoryTranslator(Translator):
    def __init__(
        self,
        base_url: str,
        email: str,
        timeout: float,
        attempts: int,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.email = email
        self.timeout = timeout
        self.attempts = max(1, attempts)

    async def _translate_text(self, text: str) -> str:
        if not text.strip():
            return ""
        protected, replacements = _protect_tags(text)
        translated_parts: list[str] = []
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for part in _chunks(protected):
                params = {"q": part, "langpair": "zh-CN|en"}
                if self.email:
                    params["de"] = self.email
                last_error: Exception | None = None
                for attempt in range(1, self.attempts + 1):
                    try:
                        response = await client.get(f"{self.base_url}/get", params=params)
                        response.raise_for_status()
                        payload = response.json()
                        translated = _clean_provider_text(
                            str(payload.get("responseData", {}).get("translatedText", ""))
                        )
                        if not translated:
                            raise TranslationError("MyMemory returned an empty translation")
                        translated_parts.append(_restore_tags(translated, replacements))
                        break
                    except (httpx.HTTPError, KeyError, TypeError, ValueError, TranslationError) as exc:
                        last_error = exc
                        if attempt < self.attempts:
                            await asyncio.sleep(min(2**attempt, 8))
                else:
                    raise TranslationError(
                        f"MyMemory translation failed: {type(last_error).__name__ if last_error else 'unknown'}"
                    )
        return "".join(translated_parts)

    async def translate(self, note: Note) -> Translation:
        return Translation(
            title=await self._translate_text(note.title),
            body=await self._translate_text(note.body),
        )


class LibreTranslateTranslator(Translator):
    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float,
        attempts: int,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.attempts = max(1, attempts)

    async def _translate_text(self, text: str) -> str:
        if not text.strip():
            return ""
        protected, replacements = _protect_tags(text)
        result: list[str] = []
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for part in _chunks(protected):
                payload = {"q": part, "source": "zh", "target": "en", "format": "text"}
                if self.api_key:
                    payload["api_key"] = self.api_key
                last_error: Exception | None = None
                for attempt in range(1, self.attempts + 1):
                    try:
                        response = await client.post(
                            f"{self.base_url}/translate", json=payload
                        )
                        response.raise_for_status()
                        translated = _clean_provider_text(
                            str(response.json().get("translatedText", ""))
                        )
                        if not translated:
                            raise TranslationError("LibreTranslate returned an empty translation")
                        result.append(_restore_tags(translated, replacements))
                        break
                    except (httpx.HTTPError, KeyError, TypeError, ValueError, TranslationError) as exc:
                        last_error = exc
                        if attempt < self.attempts:
                            await asyncio.sleep(min(2**attempt, 8))
                else:
                    raise TranslationError(
                        f"LibreTranslate failed: {type(last_error).__name__ if last_error else 'unknown'}"
                    )
        return "".join(result)

    async def translate(self, note: Note) -> Translation:
        return Translation(
            title=await self._translate_text(note.title),
            body=await self._translate_text(note.body),
        )


class OpenAITranslator(Translator):
    """Optional compatibility provider; it is never selected by default."""

    def __init__(
        self, base_url: str, api_key: str, model: str, timeout: float, attempts: int
    ) -> None:
        if not api_key:
            raise TranslationError("OPENAI_API_KEY is missing")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.attempts = max(1, attempts)

    async def translate(self, note: Note) -> Translation:
        prompt = (
            "Translate this Chinese note faithfully into natural English. Do not add facts. "
            "Return JSON with only title and body strings.\n"
            f"title: {note.title}\nbody: {note.body}"
        )
        payload = {
            "model": self.model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": "You are a precise Chinese-to-English translator."},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
        }
        last_error: Exception | None = None
        for attempt in range(1, self.attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(
                        f"{self.base_url}/chat/completions",
                        json=payload,
                        headers={"Authorization": f"Bearer {self.api_key}"},
                    )
                    response.raise_for_status()
                    result = response.json()
                content = result["choices"][0]["message"]["content"]
                decoded = json.loads(content)
                return Translation(str(decoded.get("title", "")), str(decoded.get("body", "")))
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
                last_error = exc
                if attempt < self.attempts:
                    await asyncio.sleep(min(2**attempt, 8))
        raise TranslationError(
            f"OpenAI translation failed: {type(last_error).__name__ if last_error else 'unknown'}"
        )


class FakeTranslator(Translator):
    """Offline fixture translator used only by tests."""

    async def translate(self, note: Note) -> Translation:
        return Translation(title=f"{note.title} [EN]", body=f"{note.body} [EN]")


def make_translator(settings: object) -> Translator:
    provider = str(settings.translation_provider)
    if provider == "mymemory":
        return MyMemoryTranslator(
            settings.mymemory_base_url,
            settings.mymemory_email,
            settings.request_timeout_seconds,
            settings.retry_attempts,
        )
    if provider == "libretranslate":
        return LibreTranslateTranslator(
            settings.libretranslate_base_url,
            settings.libretranslate_api_key,
            settings.request_timeout_seconds,
            settings.retry_attempts,
        )
    if provider == "openai":
        return OpenAITranslator(
            settings.openai_base_url,
            settings.openai_api_key,
            settings.openai_model,
            settings.request_timeout_seconds,
            settings.retry_attempts,
        )
    raise TranslationError(f"unsupported TRANSLATION_PROVIDER={provider!r}")
