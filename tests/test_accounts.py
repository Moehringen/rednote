from __future__ import annotations

from pathlib import Path

import pytest

from app.accounts import AccountConfigError, load_account_settings, select_account
from app.config import Settings


def _base_settings(tmp_path: Path, monkeypatch) -> Settings:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "data" / "rednote_to_x.sqlite3"))
    monkeypatch.setenv("X_TOKEN_SOURCE", "xurl")
    monkeypatch.setenv("XURL_APP", "girlinrednote")
    return Settings.from_env(None)


def test_accounts_keep_legacy_data_and_isolate_second_route(tmp_path: Path, monkeypatch) -> None:
    base = _base_settings(tmp_path, monkeypatch)
    config = tmp_path / "accounts.toml"
    config.write_text(
        """
[accounts.woodpecker]
enabled = true
data_subdir = "."
xhs_profile_url = "https://example.test/profile/one"
xhs_nickname = "one"
xhs_expected_user_id = "red-one"
xurl_app = "girlinrednote"
x_username = "first"
x_expected_user_id = "100"

[accounts.second]
enabled = true
data_subdir = "accounts/second"
xhs_profile_url = "https://example.test/profile/two"
xhs_nickname = "two"
xhs_expected_user_id = "red-two"
xurl_app = "girlinrednote2"
x_username = "second"
x_expected_user_id = "200"
""",
        encoding="utf-8",
    )

    accounts = load_account_settings(base, config)

    assert [item.account_id for item in accounts] == ["woodpecker", "second"]
    assert accounts[0].db_path == base.db_path
    assert accounts[1].db_path == tmp_path / "data" / "accounts/second/rednote_to_x.sqlite3"
    assert accounts[0].xurl_app == "girlinrednote"
    assert accounts[1].xurl_app == "girlinrednote2"
    assert accounts[0].x_expected_user_id == "100"
    assert accounts[1].x_expected_user_id == "200"
    assert accounts[0].profile_url != accounts[1].profile_url


def test_disabled_account_is_not_loaded(tmp_path: Path, monkeypatch) -> None:
    base = _base_settings(tmp_path, monkeypatch)
    config = tmp_path / "accounts.toml"
    config.write_text(
        """
[accounts.disabled]
enabled = false
xurl_app = "unused"
""",
        encoding="utf-8",
    )

    with pytest.raises(AccountConfigError, match="no enabled account"):
        load_account_settings(base, config)


def test_multiple_accounts_cannot_share_static_env_token(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "data" / "state.sqlite3"))
    monkeypatch.setenv("X_USER_ACCESS_TOKEN", "static-token")
    monkeypatch.delenv("X_TOKEN_SOURCE", raising=False)
    monkeypatch.delenv("XURL_APP", raising=False)
    base = Settings.from_env(None)
    config = tmp_path / "accounts.toml"
    config.write_text(
        """
[accounts.one]
enabled = true
xurl_app = ""

[accounts.two]
enabled = true
xurl_app = ""
""",
        encoding="utf-8",
    )

    with pytest.raises(AccountConfigError, match="must use xurl"):
        load_account_settings(base, config)


def test_select_account_requires_explicit_id_for_multiple_accounts(tmp_path: Path, monkeypatch) -> None:
    base = _base_settings(tmp_path, monkeypatch)
    config = tmp_path / "accounts.toml"
    config.write_text(
        """
[accounts.one]
enabled = true
xurl_app = "one"

[accounts.two]
enabled = true
xurl_app = "two"
""",
        encoding="utf-8",
    )
    accounts = load_account_settings(base, config)

    with pytest.raises(AccountConfigError, match="multiple accounts"):
        select_account(accounts, None)
    assert select_account(accounts, "two").account_id == "two"
