from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .config import Settings, _tags


class AccountConfigError(ValueError):
    """The multi-account configuration is invalid or unsafe to load."""


_ACCOUNT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


@dataclass(frozen=True, slots=True)
class AccountDefinition:
    """Non-secret configuration for one Xiaohongshu -> X route."""

    account_id: str
    enabled: bool = True
    data_subdir: str = "."
    xhs_backend: str | None = None
    xhs_profile_url: str | None = None
    xhs_note_url: str | None = None
    xhs_nickname: str | None = None
    xhs_english_name: str | None = None
    xhs_expected_user_id: str | None = None
    xhs_config_subdir: str | None = None
    xhs_cache_subdir: str | None = None
    xhs_cookie_subpath: str | None = None
    browser_profile_subdir: str | None = None
    mode: str | None = None
    repost_authorized: bool | None = None
    x_auth_mode: str | None = None
    x_post_api_version: str | None = None
    x_token_source: str | None = None
    xurl_app: str | None = None
    x_username: str | None = None
    x_expected_user_id: str | None = None
    x_tag_pool: str | None = None
    x_tag_count: int | None = None
    x_max_chars: int | None = None

    @classmethod
    def from_mapping(cls, account_id: str, values: dict[str, Any]) -> AccountDefinition:
        if not _ACCOUNT_ID.fullmatch(account_id):
            raise AccountConfigError(
                f"invalid account id {account_id!r}; use letters, numbers, '_' or '-'"
            )
        if not isinstance(values, dict):
            raise AccountConfigError(f"accounts.{account_id} must be a TOML table")

        def optional_text(key: str) -> str | None:
            value = values.get(key)
            if value is None:
                return None
            if not isinstance(value, str):
                raise AccountConfigError(f"accounts.{account_id}.{key} must be a string")
            return value.strip()

        def optional_bool(key: str) -> bool | None:
            value = values.get(key)
            if value is None:
                return None
            if not isinstance(value, bool):
                raise AccountConfigError(f"accounts.{account_id}.{key} must be true or false")
            return value

        def optional_int(key: str) -> int | None:
            value = values.get(key)
            if value is None:
                return None
            if isinstance(value, bool) or not isinstance(value, int):
                raise AccountConfigError(f"accounts.{account_id}.{key} must be an integer")
            return value

        enabled = values.get("enabled", True)
        if not isinstance(enabled, bool):
            raise AccountConfigError(f"accounts.{account_id}.enabled must be true or false")
        return cls(
            account_id=account_id,
            enabled=enabled,
            data_subdir=optional_text("data_subdir") or ".",
            xhs_backend=optional_text("xhs_backend"),
            xhs_profile_url=optional_text("xhs_profile_url"),
            xhs_note_url=optional_text("xhs_note_url"),
            xhs_nickname=optional_text("xhs_nickname"),
            xhs_english_name=optional_text("xhs_english_name"),
            xhs_expected_user_id=optional_text("xhs_expected_user_id"),
            xhs_config_subdir=optional_text("xhs_config_subdir"),
            xhs_cache_subdir=optional_text("xhs_cache_subdir"),
            xhs_cookie_subpath=optional_text("xhs_cookie_subpath"),
            browser_profile_subdir=optional_text("browser_profile_subdir"),
            mode=optional_text("mode"),
            repost_authorized=optional_bool("repost_authorized"),
            x_auth_mode=optional_text("x_auth_mode"),
            x_post_api_version=optional_text("x_post_api_version"),
            x_token_source=optional_text("x_token_source"),
            xurl_app=optional_text("xurl_app"),
            x_username=optional_text("x_username"),
            x_expected_user_id=optional_text("x_expected_user_id"),
            x_tag_pool=optional_text("x_tag_pool"),
            x_tag_count=optional_int("x_tag_count"),
            x_max_chars=optional_int("x_max_chars"),
        )


def _subpath(root: Path, value: str | None, default: str) -> Path:
    path = Path(value or default)
    if path.is_absolute() or ".." in path.parts:
        raise AccountConfigError(f"account path must stay below its data directory: {path}")
    return root / path


def apply_account(base: Settings, definition: AccountDefinition) -> Settings:
    """Create isolated runtime settings for one account from legacy base settings."""
    data_dir = _subpath(base.data_dir, definition.data_subdir, ".")
    if definition.data_subdir == ".":
        db_path = base.db_path
    else:
        db_path = data_dir / "rednote_to_x.sqlite3"

    xhs_backend = definition.xhs_backend or base.xhs_backend
    if xhs_backend not in {"cli", "browser"}:
        raise AccountConfigError(f"account {definition.account_id}: invalid xhs_backend")
    token_source = definition.x_token_source or base.x_token_source
    xurl_app = definition.xurl_app if definition.xurl_app is not None else base.xurl_app
    if token_source == "xurl" and not xurl_app:
        raise AccountConfigError(
            f"account {definition.account_id}: xurl_app is required when x_token_source=xurl"
        )
    tag_pool = base.x_tag_pool
    if definition.x_tag_pool is not None:
        tag_pool = _tags(definition.x_tag_pool) or base.x_tag_pool

    return replace(
        base,
        account_id=definition.account_id,
        data_dir=data_dir,
        db_path=db_path,
        xhs_backend=xhs_backend,
        profile_url=definition.xhs_profile_url or base.profile_url,
        note_url=definition.xhs_note_url or base.note_url,
        target_nickname=definition.xhs_nickname or base.target_nickname,
        target_english_name=definition.xhs_english_name or base.target_english_name,
        expected_user_id=definition.xhs_expected_user_id or base.expected_user_id,
        xhs_config_dir=_subpath(data_dir, definition.xhs_config_subdir, "xhs-config"),
        xhs_cache_dir=_subpath(data_dir, definition.xhs_cache_subdir, "xhs-cache"),
        xhs_cookie_file=_subpath(
            data_dir, definition.xhs_cookie_subpath, "cookies/xhs.cookie"
        ),
        browser_profile_dir=_subpath(
            data_dir, definition.browser_profile_subdir, "browser-profile"
        ),
        mode=definition.mode or base.mode,
        repost_authorized=(
            definition.repost_authorized
            if definition.repost_authorized is not None
            else base.repost_authorized
        ),
        x_auth_mode=definition.x_auth_mode or base.x_auth_mode,
        x_post_api_version=definition.x_post_api_version or base.x_post_api_version,
        x_token_source=token_source,
        xurl_app=xurl_app,
        x_username=(definition.x_username or base.x_username).lstrip("@"),
        x_expected_user_id=definition.x_expected_user_id or base.x_expected_user_id,
        x_tag_pool=tag_pool,
        x_tag_count=(
            definition.x_tag_count if definition.x_tag_count is not None else base.x_tag_count
        ),
        x_max_chars=(
            definition.x_max_chars if definition.x_max_chars is not None else base.x_max_chars
        ),
    )


def load_account_settings(
    base: Settings, config_path: Path | None = None
) -> list[Settings]:
    """Load enabled routes, preserving the existing single-account .env mode."""
    path = config_path or base.accounts_config
    if not path.exists():
        return [base]
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AccountConfigError(f"cannot read accounts config {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise AccountConfigError(f"invalid accounts config {path}: {exc}") from exc

    raw_accounts = document.get("accounts")
    if not isinstance(raw_accounts, dict) or not raw_accounts:
        raise AccountConfigError(f"{path} must contain at least one [accounts.<id>] table")
    definitions = [
        AccountDefinition.from_mapping(str(account_id), values)
        for account_id, values in raw_accounts.items()
    ]
    enabled = [definition for definition in definitions if definition.enabled]
    if not enabled:
        raise AccountConfigError("accounts config has no enabled account")
    if len(enabled) > 1 and any(
        (definition.x_token_source or base.x_token_source) == "env" for definition in enabled
    ):
        raise AccountConfigError(
            "multiple enabled accounts must use xurl token_source so X credentials cannot cross"
        )
    return [apply_account(base, definition) for definition in enabled]


def select_account(settings: list[Settings], account_id: str | None) -> Settings:
    if account_id:
        for item in settings:
            if item.account_id == account_id:
                return item
        available = ", ".join(item.account_id for item in settings)
        raise AccountConfigError(f"unknown account {account_id!r}; available: {available}")
    if len(settings) == 1:
        return settings[0]
    available = ", ".join(item.account_id for item in settings)
    raise AccountConfigError(
        f"multiple accounts are configured ({available}); pass --account ACCOUNT_ID"
    )
