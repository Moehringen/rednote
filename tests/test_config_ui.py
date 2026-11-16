from __future__ import annotations

import os
import stat
from pathlib import Path

from app.config_ui import ConfigStore, render_config_page


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
