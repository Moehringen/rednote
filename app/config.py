from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _bool(value: str, default: bool = False) -> bool:
    if not value:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _tags(value: str) -> tuple[str, ...]:
    result: list[str] = []
    for item in value.split():
        tag = item.strip()
        if not tag:
            continue
        result.append(tag if tag.startswith("#") else f"#{tag}")
    return tuple(dict.fromkeys(result))


@dataclass(frozen=True, slots=True)
class Settings:
    xhs_backend: str
    profile_url: str
    note_url: str
    target_nickname: str
    target_english_name: str
    expected_user_id: str
    xhs_cli_path: str
    xhs_config_dir: Path
    xhs_cache_dir: Path
    xhs_cookie_file: Path
    xhs_rate: str
    profile_candidate_limit: int
    mode: str
    repost_authorized: bool
    x_api_base_url: str
    x_user_access_token: str
    x_max_chars: int
    x_tags: tuple[str, ...]
    x_media_upload_path: str
    translation_provider: str
    mymemory_base_url: str
    mymemory_email: str
    libretranslate_base_url: str
    libretranslate_api_key: str
    openai_base_url: str
    openai_api_key: str
    openai_model: str
    data_dir: Path
    db_path: Path
    request_timeout_seconds: float
    retry_attempts: int
    log_level: str
    timezone: str
    browser_profile_dir: Path
    browser_executable_path: str
    browser_headless: bool
    browser_login_timeout_seconds: float

    @classmethod
    def from_env(cls, env_file: str | Path | None = ".env") -> Settings:
        if env_file and Path(env_file).exists():
            load_dotenv(env_file, override=False)
        data_dir = Path(os.getenv("DATA_DIR", "./data"))
        db_path = Path(os.getenv("DB_PATH", str(data_dir / "rednote_to_x.sqlite3")))
        mode = os.getenv("MODE", "draft").strip().lower()
        if mode not in {"draft", "publish"}:
            raise ValueError("MODE must be draft or publish")
        provider = os.getenv("TRANSLATION_PROVIDER", "mymemory").strip().lower()
        if provider not in {"mymemory", "libretranslate", "openai"}:
            raise ValueError(
                "TRANSLATION_PROVIDER must be mymemory, libretranslate, or openai"
            )
        xhs_backend = os.getenv("XHS_BACKEND", "cli").strip().lower()
        if xhs_backend not in {"cli", "browser"}:
            raise ValueError("XHS_BACKEND must be cli or browser")
        return cls(
            xhs_backend=xhs_backend,
            profile_url=os.getenv("XHS_PROFILE_URL", "https://xhslink.cn/o/Dfkx6Lhilv"),
            note_url=os.getenv("XHS_NOTE_URL", "https://xhslink.cn/o/46BbiPl8OPJ"),
            target_nickname=os.getenv("XHS_NICKNAME", "琢磨鸟"),
            target_english_name=os.getenv("XHS_ENGLISH_NAME", "thewoodpecker"),
            expected_user_id=os.getenv("XHS_EXPECTED_USER_ID", "67d21d20000000000e01d41e"),
            xhs_cli_path=os.getenv("XHS_CLI_PATH", "xhs"),
            xhs_config_dir=Path(os.getenv("XHS_CONFIG_DIR", str(data_dir / "xhs-config"))),
            xhs_cache_dir=Path(os.getenv("XHS_CACHE_DIR", str(data_dir / "xhs-cache"))),
            xhs_cookie_file=Path(
                os.getenv("XHS_COOKIE_FILE", str(data_dir / "cookies" / "xhs.cookie"))
            ),
            xhs_rate=os.getenv("XHS_RATE", "2s"),
            profile_candidate_limit=max(1, int(os.getenv("XHS_PROFILE_CANDIDATE_LIMIT", "10"))),
            mode=mode,
            repost_authorized=_bool(os.getenv("REPOST_AUTHORIZED", "false")),
            x_api_base_url=os.getenv("X_API_BASE_URL", "https://api.x.com").rstrip("/"),
            x_user_access_token=os.getenv("X_USER_ACCESS_TOKEN", ""),
            x_max_chars=int(os.getenv("X_MAX_CHARS", "280")),
            x_tags=_tags(os.getenv("X_TAGS", "#Xiaohongshu #Photography")),
            x_media_upload_path=os.getenv("X_MEDIA_UPLOAD_PATH", "/2/media/upload"),
            translation_provider=provider,
            mymemory_base_url=os.getenv(
                "MYMEMORY_BASE_URL", "https://api.mymemory.translated.net"
            ).rstrip("/"),
            mymemory_email=os.getenv("MYMEMORY_EMAIL", ""),
            libretranslate_base_url=os.getenv(
                "LIBRETRANSLATE_BASE_URL", "https://libretranslate.com"
            ).rstrip("/"),
            libretranslate_api_key=os.getenv("LIBRETRANSLATE_API_KEY", ""),
            openai_base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            data_dir=data_dir,
            db_path=db_path,
            request_timeout_seconds=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "45")),
            retry_attempts=max(1, int(os.getenv("RETRY_ATTEMPTS", "3"))),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            timezone=os.getenv("TZ", "Asia/Singapore"),
            browser_profile_dir=Path(
                os.getenv("BROWSER_PROFILE_DIR", "./data/browser-profile")
            ),
            browser_executable_path=os.getenv("BROWSER_EXECUTABLE_PATH", ""),
            browser_headless=_bool(os.getenv("BROWSER_HEADLESS", "false")),
            browser_login_timeout_seconds=float(
                os.getenv("BROWSER_LOGIN_TIMEOUT_SECONDS", "180")
            ),
        )

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "test_latest").mkdir(parents=True, exist_ok=True)
        self.xhs_config_dir.mkdir(parents=True, exist_ok=True)
        self.xhs_cache_dir.mkdir(parents=True, exist_ok=True)
        self.xhs_cookie_file.parent.mkdir(parents=True, exist_ok=True)

    @property
    def publish_ready(self) -> tuple[bool, str]:
        if self.mode != "publish":
            return False, "MODE is draft"
        if not self.repost_authorized:
            return False, "REPOST_AUTHORIZED is false"
        if not self.x_user_access_token:
            return False, "X_USER_ACCESS_TOKEN is missing"
        return True, "publish gates passed"
