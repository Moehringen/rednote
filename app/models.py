from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class Note:
    note_id: str
    author: str
    title: str
    body: str
    note_type: str
    published_at: str | None
    image_urls: list[str] = field(default_factory=list)
    video_urls: list[str] = field(default_factory=list)
    cover_urls: list[str] = field(default_factory=list)
    source_url: str = ""
    final_url: str = ""
    author_user_id: str | None = None
    tags: list[str] = field(default_factory=list)
    keywords: str = ""
    engagement: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ProfileCandidate:
    note_id: str
    title: str
    source_url: str
    published_at: str | None = None
    is_pinned: bool | None = None
    author: str | None = None
    author_user_id: str | None = None
    xsec_token: str = field(default="", repr=False)
    note_type: str = ""


@dataclass(slots=True)
class ProfileIdentity:
    user_id: str
    nickname: str
    red_id: str = ""
    profile_url: str = ""
    final_url: str = ""


@dataclass(slots=True)
class Translation:
    title: str
    body: str

    def as_text(self) -> str:
        if self.title and self.body:
            return f"{self.title}\n\n{self.body}"
        return self.title or self.body
