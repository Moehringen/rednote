from __future__ import annotations

import os
import stat
from dataclasses import replace
from pathlib import Path

from app.config import Settings
from app.config_ui import AccountConfigStore, ConfigStore, render_config_page


def test_config_store_saves_secrets_atomically_and_masks_ui(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("MODE=draft\nX_TAG_POOL=#old\n", encoding="utf-8")
    store = ConfigStore(env_path)

    result = store.save(
        {
            "MODE": ["draft"],
            "X_TAG_POOL": ["#PortraitPhotography #BeautyPhotography"],
            "X_TAG_COUNT": ["4"],
            "X_USER_ACCESS_TOKEN": ["secret-user-token"],
            "X_BEARER_TOKEN": ["secret-bearer-token"],
        }
    )

    assert result["user_access_token_saved"] is True
    assert "X_USER_ACCESS_TOKEN=\"secret-user-token\"" in env_path.read_text(encoding="utf-8")
    assert "X_BEARER_TOKEN=\"secret-bearer-token\"" in env_path.read_text(encoding="utf-8")
    assert stat.S_IMODE(os.stat(env_path).st_mode) == 0o600
    page = render_config_page("csrf", store.public_state())
    assert "secret-user-token" not in page
    assert "secret-bearer-token" not in page
    assert "已保存，留空保持不变" in page
    assert "V1 · OAuth 1.0a" in page
    assert "V2 · OAuth 2.0" in page
    assert "X_BEARER_TOKEN" in page
    assert "X_TAG_POOL" in page
    assert "X_TAG_COUNT" in page


def test_blank_secret_keeps_existing_value_and_publish_requires_confirmation(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    store = ConfigStore(env_path)
    store.save({"X_USER_ACCESS_TOKEN": ["keep-me"]})
    store.save({"MODE": ["draft"], "X_USER_ACCESS_TOKEN": [""]})
    assert "keep-me" in env_path.read_text(encoding="utf-8")

    try:
        store.save({"MODE": ["publish"], "REPOST_AUTHORIZED": ["on"]})
    except ValueError as exc:
        assert "I UNDERSTAND" in str(exc)
    else:
        raise AssertionError("publish must require explicit confirmation")


def test_legacy_blocked_only_tag_pool_falls_back_to_curated_defaults(monkeypatch) -> None:
    monkeypatch.setenv("X_TAG_POOL", "#Xiaohongshu")
    settings = Settings.from_env(None)

    assert len(settings.x_tag_pool) >= 4
    assert all(
        tag.casefold() not in {"#xiaohongshu", "#rednote", "#小红书"}
        for tag in settings.x_tag_pool
    )


def test_account_config_store_shows_legacy_route_and_saves_non_secret_route(tmp_path: Path) -> None:
    settings = replace(
        Settings.from_env(None),
        profile_url="https://example.test/profile",
        target_nickname="legacy",
        expected_user_id="red-1",
        xurl_app="first-app",
    )
    accounts_path = tmp_path / "accounts.toml"
    store = AccountConfigStore(accounts_path, settings)

    assert store.public_accounts()["default"]["xhs_nickname"] == "legacy"
    store.save(
        {
            "account_id": ["second"],
            "enabled": ["on"],
            "data_subdir": ["accounts/second"],
            "xhs_profile_url": ["https://example.test/second"],
            "xhs_nickname": ["second"],
            "xhs_expected_user_id": ["red-2"],
            "xurl_app": ["second-app"],
            "x_username": ["second"],
            "x_expected_user_id": ["200"],
            "mode": ["draft"],
            "x_tag_count": ["4"],
            "x_max_chars": ["280"],
        }
    )

    saved = store.public_accounts()
    assert saved["second"]["xurl_app"] == "second-app"
    assert saved["second"]["enabled"] is True
    saved_text = accounts_path.read_text(encoding="utf-8")
    assert "X_USER_ACCESS_TOKEN" not in saved_text
    assert "refresh_token" not in saved_text
