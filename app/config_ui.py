from __future__ import annotations

import html
import json
import os
import re
import secrets
import tempfile
import tomllib
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .accounts import AccountConfigError, AccountDefinition
from .config import Settings

SECRET_KEYS = (
    "X_API_KEY",
    "X_API_SECRET",
    "X_ACCESS_TOKEN",
    "X_ACCESS_TOKEN_SECRET",
    "X_USER_ACCESS_TOKEN",
    "X_BEARER_TOKEN",
    "X_CLIENT_ID",
    "X_CLIENT_SECRET",
)
SECRET_LABELS = {
    "X_API_KEY": "Consumer Key / Consumer API Key（V1 OAuth1）",
    "X_API_SECRET": "Consumer Secret / API Key Secret（V1 OAuth1）",
    "X_ACCESS_TOKEN": "OAuth1 User Access Token（OAuth1）",
    "X_ACCESS_TOKEN_SECRET": "OAuth1 Access Token Secret（OAuth1）",
    "X_USER_ACCESS_TOKEN": "OAuth2 User Access Token（V2 用户授权，可发帖）",
    "X_BEARER_TOKEN": "OAuth2 App-only Bearer Token（V2 只读）",
    "X_CLIENT_ID": "OAuth2 Client ID（OAuth2 PKCE）",
    "X_CLIENT_SECRET": "OAuth2 Client Secret（OAuth2 PKCE）",
}
PUBLIC_KEYS = (
    "MODE",
    "REPOST_AUTHORIZED",
    "X_TAG_POOL",
    "X_TAG_COUNT",
    "X_API_BASE_URL",
    "X_AUTH_MODE",
    "X_POST_API_VERSION",
    "X_TOKEN_SOURCE",
    "XURL_APP",
    "X_MEDIA_UPLOAD_BASE_URL",
    "X_MEDIA_UPLOAD_PATH",
    "X_MAX_CHARS",
)
DEFAULTS = {
    "MODE": "draft",
    "REPOST_AUTHORIZED": "false",
    "X_TAG_POOL": (
        "#PortraitPhotography #Portraits #PortraitMood #PortraitInspiration "
        "#PortraitArt #PortraitStyle #BeautyPortrait #BeautyPhotography "
        "#WomensPortraits #WomenInPhotography #FemalePortrait #ModelPhotography "
        "#ModelPortrait #FashionPortrait #FashionPhotography #EditorialPortrait "
        "#EditorialFashion #StreetPortrait #StreetPhotography #LifestylePhotography "
        "#CinematicPortrait #CinematicPhotography #MoodyPortraits #NaturalLightPortrait "
        "#GoldenHourPortrait #SoftLight #CreativePortrait #FineArtPortrait "
        "#VisualStorytelling #PhotoInspiration #PhotographyLovers #PhotographyDaily "
        "#PhotoOfTheDay #Portraiture #BeautifulWomen #BeautyInspiration "
        "#StyleInspiration #OutfitInspiration #FilmLook #AestheticPhotography "
        "#ArtPhotography #PhotographyCommunity #BehindThePortrait"
    ),
    "X_TAG_COUNT": "4",
    "X_API_BASE_URL": "https://api.x.com",
    "X_AUTH_MODE": "oauth2",
    "X_POST_API_VERSION": "v2",
    "X_TOKEN_SOURCE": "env",
    "XURL_APP": "",
    "X_MEDIA_UPLOAD_BASE_URL": "https://upload.x.com",
    "X_MEDIA_UPLOAD_PATH": "/2/media/upload",
    "X_MAX_CHARS": "280",
}
_ENV_KEY = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")

ACCOUNT_TEXT_KEYS = (
    "data_subdir",
    "xhs_profile_url",
    "xhs_note_url",
    "xhs_nickname",
    "xhs_english_name",
    "xhs_expected_user_id",
    "xurl_app",
    "x_username",
    "x_expected_user_id",
    "x_tag_pool",
)
ACCOUNT_BOOL_KEYS = ("enabled", "repost_authorized")
ACCOUNT_INT_KEYS = ("x_tag_count", "x_max_chars")
ACCOUNT_DEFAULTS: dict[str, object] = {
    "enabled": False,
    "data_subdir": "accounts/new-account",
    "xhs_profile_url": "",
    "xhs_note_url": "",
    "xhs_nickname": "",
    "xhs_english_name": "",
    "xhs_expected_user_id": "",
    "xurl_app": "",
    "x_username": "",
    "x_expected_user_id": "",
    "repost_authorized": False,
    "mode": "draft",
    "x_tag_pool": "",
    "x_tag_count": 4,
    "x_max_chars": 280,
}


def _legacy_account(settings: Settings) -> dict[str, object]:
    return {
        "enabled": True,
        "data_subdir": ".",
        "xhs_profile_url": settings.profile_url,
        "xhs_note_url": settings.note_url,
        "xhs_nickname": settings.target_nickname,
        "xhs_english_name": settings.target_english_name,
        "xhs_expected_user_id": settings.expected_user_id,
        "xurl_app": settings.xurl_app,
        "x_username": settings.x_username,
        "x_expected_user_id": settings.x_expected_user_id,
        "repost_authorized": settings.repost_authorized,
        "mode": settings.mode,
        "x_tag_pool": " ".join(settings.x_tag_pool),
        "x_tag_count": settings.x_tag_count,
        "x_max_chars": settings.x_max_chars,
    }


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


class AccountConfigStore:
    """Read and atomically write non-secret multi-account route settings."""

    def __init__(self, path: Path, legacy_settings: Settings) -> None:
        self.path = path
        self.legacy_settings = legacy_settings

    def _read(self) -> dict[str, dict[str, object]]:
        if not self.path.exists():
            return {"default": _legacy_account(self.legacy_settings)}
        try:
            document = tomllib.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise AccountConfigError(f"cannot read accounts config: {exc}") from exc
        raw = document.get("accounts", {})
        if not isinstance(raw, dict):
            raise AccountConfigError("accounts.toml [accounts] must contain account tables")
        return {str(account_id): dict(values) for account_id, values in raw.items()}

    def public_accounts(self) -> dict[str, dict[str, object]]:
        accounts = self._read()
        result: dict[str, dict[str, object]] = {}
        for account_id, values in accounts.items():
            merged = dict(ACCOUNT_DEFAULTS)
            merged.update(values)
            result[account_id] = merged
        return result

    def save(self, form: Mapping[str, list[str]]) -> str:
        account_id = (form.get("account_id", [""])[0] or "").strip()
        values: dict[str, object] = {
            "enabled": "enabled" in form,
            "repost_authorized": "repost_authorized" in form,
            "mode": (form.get("mode", ["draft"])[0] or "draft").strip().lower(),
        }
        for key in ACCOUNT_TEXT_KEYS:
            values[key] = (form.get(key, [""])[0] or "").strip()
        for key in ACCOUNT_INT_KEYS:
            raw = (form.get(key, [""])[0] or "").strip()
            try:
                values[key] = int(raw)
            except ValueError as exc:
                raise AccountConfigError(f"{key} must be an integer") from exc
        if values["mode"] not in {"draft", "publish"}:
            raise AccountConfigError("mode must be draft or publish")
        if values["mode"] == "publish" and not values["repost_authorized"]:
            raise AccountConfigError("publish requires the account authorization checkbox")
        if not 0 <= int(values["x_tag_count"]) <= 10:
            raise AccountConfigError("x_tag_count must be between 0 and 10")
        if not 1 <= int(values["x_max_chars"]) <= 280:
            raise AccountConfigError("x_max_chars must be between 1 and 280")
        AccountDefinition.from_mapping(account_id, values)
        data_subdir = Path(str(values["data_subdir"]))
        if data_subdir.is_absolute() or ".." in data_subdir.parts:
            raise AccountConfigError("data_subdir must stay below the data directory")
        if values["enabled"] and not values["xurl_app"]:
            raise AccountConfigError("an enabled account requires an xurl_app")

        accounts = self._read()
        accounts[account_id] = values
        lines = [
            "# Non-secret route settings. OAuth tokens stay in xurl auth.yml.",
            "",
        ]
        for current_id in sorted(accounts):
            lines.append(f"[accounts.{current_id}]")
            current = dict(ACCOUNT_DEFAULTS)
            current.update(accounts[current_id])
            for key in ACCOUNT_BOOL_KEYS + ACCOUNT_TEXT_KEYS + ("mode",) + ACCOUNT_INT_KEYS:
                lines.append(f"{key} = {_toml_value(current[key])}")
            lines.append("")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self.path.stat().st_mode & 0o777 != 0o600:
            os.chmod(self.path, 0o600)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write("\n".join(lines))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise
        return account_id


def _parse_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        match = _ENV_KEY.match(raw_line)
        if not match:
            continue
        key = match.group(1)
        value = raw_line[match.end() :].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _env_value(value: str) -> str:
    """Quote values so spaces, hashes and secrets are not parsed as comments."""
    return json.dumps(value, ensure_ascii=False)


class ConfigStore:
    """Atomically update the local .env without exposing secret values."""

    def __init__(self, env_path: Path) -> None:
        self.env_path = env_path

    def public_state(self) -> dict[str, object]:
        values = _parse_env(self.env_path)
        return {
            "values": {key: values.get(key, DEFAULTS[key]) for key in PUBLIC_KEYS},
            "secret_status": {key: bool(values.get(key, "")) for key in SECRET_KEYS},
        }

    def save(self, form: Mapping[str, list[str]]) -> dict[str, object]:
        mode = (form.get("MODE", ["draft"])[0] or "draft").strip().lower()
        authorized = "REPOST_AUTHORIZED" in form
        confirmation = (form.get("PUBLISH_CONFIRM", [""])[0] or "").strip()
        if mode not in {"draft", "publish"}:
            raise ValueError("MODE must be draft or publish")
        if mode == "publish" and (not authorized or confirmation != "I UNDERSTAND"):
            raise ValueError(
                'publish requires the authorization checkbox and confirmation text "I UNDERSTAND"'
            )

        values: dict[str, str] = {
            "MODE": mode,
            "REPOST_AUTHORIZED": "true" if authorized else "false",
        }
        for key in PUBLIC_KEYS:
            if key in {"MODE", "REPOST_AUTHORIZED"}:
                continue
            values[key] = (form.get(key, [DEFAULTS[key]])[0] or DEFAULTS[key]).strip()
        for key in SECRET_KEYS:
            value = (form.get(key, [""])[0] or "").strip()
            if value:
                if len(value) > 4096:
                    raise ValueError(f"{key} is too long")
                values[key] = value

        if self.env_path.is_symlink():
            raise ValueError("refusing to write a symlinked .env file")
        existing = _parse_env(self.env_path)
        updates = {**values}
        lines = (
            self.env_path.read_text(encoding="utf-8").splitlines()
            if self.env_path.exists()
            else []
        )
        seen: set[str] = set()
        output: list[str] = []
        for line in lines:
            match = _ENV_KEY.match(line)
            if not match or match.group(1) not in updates:
                output.append(line)
                continue
            key = match.group(1)
            output.append(f"{key}={_env_value(updates[key])}")
            seen.add(key)
        for key, value in updates.items():
            if key not in seen and (key not in SECRET_KEYS or value):
                output.append(f"{key}={_env_value(value)}")

        self.env_path.parent.mkdir(parents=True, exist_ok=True)
        if self.env_path.exists() and self.env_path.stat().st_mode & 0o777 != 0o600:
            os.chmod(self.env_path, 0o600)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self.env_path.name}.", dir=self.env_path.parent
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write("\n".join(output).rstrip() + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.env_path)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise
        return {
            "mode": mode,
            "authorized": authorized,
            "user_access_token_saved": bool(
                values.get("X_USER_ACCESS_TOKEN") or existing.get("X_USER_ACCESS_TOKEN")
            ),
        }


def render_config_page(
    csrf_token: str, state: Mapping[str, object], message: str = "", error: str = ""
) -> str:
    values = state.get("values", {})
    secret_status = state.get("secret_status", {})

    def value(key: str) -> str:
        return html.escape(str(values.get(key, DEFAULTS[key])), quote=True)

    def checked(key: str) -> str:
        return " checked" if str(values.get(key, "")).lower() == "true" else ""

    def secret_field(key: str) -> str:
        placeholder = "已保存，留空保持不变" if secret_status.get(key) else "未设置"
        return (
            f'<label>{html.escape(SECRET_LABELS[key])} '
            f'<code>{html.escape(key)}</code><input type="password" '
            f'name="{html.escape(key)}" autocomplete="new-password" '
            f'placeholder="{html.escape(placeholder, quote=True)}"></label>'
        )

    v1_secret_fields = "\n".join(
        secret_field(key)
        for key in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")
    )
    v2_secret_fields = "\n".join(
        secret_field(key)
        for key in ("X_CLIENT_ID", "X_CLIENT_SECRET", "X_USER_ACCESS_TOKEN", "X_BEARER_TOKEN")
    )
    status = (
        f'<p class="ok">{html.escape(message)}</p>'
        if message
        else f'<p class="error">{html.escape(error)}</p>' if error else ""
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Rednote → X 本地配置</title>
  <style>
    body {{ max-width: 760px; margin: 32px auto; padding: 0 18px; font: 16px/1.55 system-ui, sans-serif; color: #172033; background: #f6f8fb; }}
    main {{ background: white; padding: 28px; border-radius: 14px; box-shadow: 0 4px 20px #17203318; }}
    h1 {{ margin-top: 0; }} h2 {{ margin-top: 28px; font-size: 1.1rem; }}
    label {{ display: block; margin: 13px 0; font-weight: 600; }}
    input, select {{ display: block; width: 100%; box-sizing: border-box; margin-top: 5px; padding: 10px; border: 1px solid #cbd3df; border-radius: 7px; font: inherit; }}
    input[type=checkbox] {{ display: inline-block; width: auto; margin-right: 8px; }}
    .hint {{ color: #526174; font-size: .92rem; }} .warning {{ background: #fff6df; padding: 12px; border-radius: 8px; }}
    .credential-panel {{ margin-top: 18px; padding: 16px 18px; border: 1px solid #d7dee8; border-radius: 10px; }}
    .credential-panel h3 {{ margin: 0 0 6px; }} .credential-panel.v1 {{ border-left: 5px solid #7b8798; }}
    .credential-panel.v2 {{ border-left: 5px solid #2563eb; }} .credential-panel .hint {{ margin-top: 4px; }}
    .ok {{ color: #087443; background: #e9f8ef; padding: 10px; border-radius: 8px; }} .error {{ color: #a31b1b; background: #ffeded; padding: 10px; border-radius: 8px; }}
    button {{ margin-top: 20px; padding: 11px 18px; border: 0; border-radius: 7px; background: #111827; color: white; font: inherit; cursor: pointer; }}
    code {{ background: #eef1f5; padding: 2px 4px; border-radius: 4px; }}
  </style>
</head>
<body><main>
  <h1>小红书 → X 本地配置</h1>
  <p><a href="/accounts">管理多账号路线 →</a></p>
  <p class="hint">仅绑定在 127.0.0.1。页面不会显示已保存的 token，也不会把 token 写入日志、报告或 Git。</p>
  {status}
  <div class="warning"><strong>认证说明：</strong>V2 的 Consumer Key/Secret 和 Bearer Token 只能证明 App 身份；Bearer Token 是 App-only，只读，不能代表你的账号发帖。
  V2 发帖必须使用 OAuth2 User Access Token；V1 旧路线必须填写 OAuth1 四项用户凭证。</div>
  <form method="post" action="/save">
    <input type="hidden" name="csrf" value="{html.escape(csrf_token, quote=True)}">
    <h2>X 凭证（按版本分区）</h2>
    <p class="hint">留空会保留已保存值。页面不会回显任何 secret、token 或 Bearer Token。</p>
    <section class="credential-panel v1">
      <h3>V1 · OAuth 1.0a（旧版兼容）</h3>
      <p class="hint">旧 App 用户上下文。只有四项都齐全时，才能走 OAuth1；V1.1 接口已属于兼容路线。</p>
      {v1_secret_fields}
    </section>
    <section class="credential-panel v2">
      <h3>V2 · OAuth 2.0（当前推荐）</h3>
      <p class="hint">Consumer Key/Secret 与 Bearer Token 是 App-only 凭证，主要用于公开数据只读；真正发帖要填写 User Access Token，并在 X App 权限中启用 <code>tweet.write</code>、<code>users.read</code>、<code>media.write</code>。</p>
      {v2_secret_fields}
    </section>
    <h2>发布门禁</h2>
    <label>运行模式<select name="MODE"><option value="draft"{' selected' if str(values.get('MODE', 'draft')) == 'draft' else ''}>draft（只生成草稿）</option><option value="publish"{' selected' if str(values.get('MODE', 'draft')) == 'publish' else ''}>publish（允许发布）</option></select></label>
    <label><input type="checkbox" name="REPOST_AUTHORIZED" value="on"{checked('REPOST_AUTHORIZED')}>我确认已取得第三方内容的转载授权</label>
    <label>开启 publish 时输入 <code>I UNDERSTAND</code><input type="text" name="PUBLISH_CONFIRM" autocomplete="off" placeholder="默认不会开启 publish"></label>
    <h2>推文选项</h2>
    <label>标签池（空格分隔）<textarea name="X_TAG_POOL" rows="4" placeholder="#PortraitPhotography #BeautyPhotography #ModelPhotography">{value('X_TAG_POOL')}</textarea></label>
    <label>每篇随机标签数<input name="X_TAG_COUNT" value="{value('X_TAG_COUNT')}" inputmode="numeric" min="0" max="10"></label>
    <p class="hint">每篇会从标签池中按笔记 ID 稳定随机抽取；同一篇重试时标签不变。系统会自动排除 <code>#Xiaohongshu</code>、<code>#Rednote</code> 等来源平台标签。来源链接仍保存在元数据和报告中，但不会写入推文正文。</p>
    <label>X API Base URL<input name="X_API_BASE_URL" value="{value('X_API_BASE_URL')}"></label>
    <label>认证模式<select name="X_AUTH_MODE"><option value="oauth2"{' selected' if str(values.get('X_AUTH_MODE', 'oauth2')) == 'oauth2' else ''}>V2 OAuth 2.0 User Token</option><option value="oauth1"{' selected' if str(values.get('X_AUTH_MODE', 'oauth2')) == 'oauth1' else ''}>V1 OAuth 1.0a 旧 App</option><option value="auto"{' selected' if str(values.get('X_AUTH_MODE', 'oauth2')) == 'auto' else ''}>自动选择（完整 V1 OAuth1 优先）</option></select></label>
    <label>OAuth2 token 来源<select name="X_TOKEN_SOURCE"><option value="env"{' selected' if str(values.get('X_TOKEN_SOURCE', 'env')) == 'env' else ''}>.env 静态 User Access Token</option><option value="xurl"{' selected' if str(values.get('X_TOKEN_SOURCE', 'env')) == 'xurl' else ''}>xurl 自动刷新</option></select></label>
    <label>xurl App 名称<input name="XURL_APP" value="{value('XURL_APP')}" placeholder="例如 girlinrednote"></label>
    <p class="hint">选择 xurl 后，程序调用本机 <code>xurl token --app</code> 获取并自动刷新用户 token；不会在页面回显 token。</p>
    <label>发帖接口<select name="X_POST_API_VERSION"><option value="v2"{' selected' if str(values.get('X_POST_API_VERSION', 'v2')) == 'v2' else ''}>V2 当前接口（推荐）</option><option value="v1.1"{' selected' if str(values.get('X_POST_API_VERSION', 'v2')) == 'v1.1' else ''}>V1.1 旧接口（OAuth1，已废弃）</option></select></label>
    <p class="hint">V2 发帖使用 <code>POST /2/tweets</code>；V1.1 仅用于兼容旧 App。OAuth1 媒体接口不使用已废弃的 <code>update_with_media</code>。</p>
    <label>OAuth1 媒体上传 Base URL<input name="X_MEDIA_UPLOAD_BASE_URL" value="{value('X_MEDIA_UPLOAD_BASE_URL')}"></label>
    <label>媒体上传路径<input name="X_MEDIA_UPLOAD_PATH" value="{value('X_MEDIA_UPLOAD_PATH')}"></label>
    <label>单条推文最大字符数<input name="X_MAX_CHARS" value="{value('X_MAX_CHARS')}" inputmode="numeric"></label>
    <button type="submit">安全保存配置</button>
  </form>
</main></body></html>"""


def _account_input(
    values: Mapping[str, object], key: str, label: str, *, textarea: bool = False
) -> str:
    value = html.escape(str(values.get(key, ACCOUNT_DEFAULTS.get(key, ""))), quote=True)
    if textarea:
        return f'<label>{label}<textarea name="{key}" rows="3">{value}</textarea></label>'
    return f'<label>{label}<input name="{key}" value="{value}"></label>'


def render_accounts_page(
    csrf_token: str,
    store: AccountConfigStore,
    message: str = "",
    error: str = "",
) -> str:
    accounts = store.public_accounts()
    status = (
        f'<p class="ok">{html.escape(message)}</p>'
        if message
        else f'<p class="error">{html.escape(error)}</p>' if error else ""
    )

    def render_form(account_id: str, values: Mapping[str, object]) -> str:
        checked_enabled = " checked" if values.get("enabled") else ""
        checked_authorized = " checked" if values.get("repost_authorized") else ""
        fields = "\n".join(
            (
                _account_input(values, "data_subdir", "数据子目录"),
                _account_input(values, "xhs_profile_url", "小红书主页分享链接"),
                _account_input(values, "xhs_note_url", "示例笔记链接（可选）"),
                _account_input(values, "xhs_nickname", "小红书昵称"),
                _account_input(values, "xhs_english_name", "关联英文名"),
                _account_input(values, "xhs_expected_user_id", "小红书真实 user_id"),
                _account_input(values, "xurl_app", "xurl App 名称"),
                _account_input(values, "x_username", "X 用户名（不含 @）"),
                _account_input(values, "x_expected_user_id", "X 用户 ID"),
                _account_input(values, "x_tag_pool", "标签池（可选，空格分隔）", textarea=True),
                _account_input(values, "x_tag_count", "每篇标签数"),
                _account_input(values, "x_max_chars", "单条推文最大字符数"),
            )
        )
        return f"""
    <section class="account-card">
      <h2>{html.escape(account_id)}</h2>
      <form method="post" action="/accounts/save">
        <input type="hidden" name="csrf" value="{html.escape(csrf_token, quote=True)}">
        <input type="hidden" name="account_id" value="{html.escape(account_id, quote=True)}">
        <label><input type="checkbox" name="enabled" value="on"{checked_enabled}>启用此路线</label>
        <label>发布模式<select name="mode">
          <option value="draft"{' selected' if values.get('mode') == 'draft' else ''}>draft（只生成草稿）</option>
          <option value="publish"{' selected' if values.get('mode') == 'publish' else ''}>publish（允许发布）</option>
        </select></label>
        {fields}
        <label><input type="checkbox" name="repost_authorized" value="on"{checked_authorized}>我确认此路线已取得转载授权</label>
        <button type="submit">保存此路线</button>
      </form>
    </section>"""

    new_values = dict(ACCOUNT_DEFAULTS)
    new_values["enabled"] = False
    new_field_defs = (
        ("data_subdir", "数据子目录"),
        ("xhs_profile_url", "小红书主页分享链接"),
        ("xhs_nickname", "小红书昵称"),
        ("xhs_expected_user_id", "小红书真实 user_id"),
        ("xurl_app", "xurl App 名称"),
        ("x_username", "X 用户名（不含 @）"),
        ("x_expected_user_id", "X 用户 ID"),
        ("x_tag_count", "每篇标签数"),
        ("x_max_chars", "单条推文最大字符数"),
    )
    new_fields = "".join(_account_input(new_values, key, label) for key, label in new_field_defs)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>多账号路线配置</title>
<style>
body {{ max-width: 900px; margin: 32px auto; padding: 0 18px; font: 16px/1.55 system-ui,sans-serif; color:#172033; background:#f6f8fb; }}
main {{ background:#fff; padding:28px; border-radius:14px; box-shadow:0 4px 20px #17203318; }}
h1 {{ margin-top:0; }} h2 {{ margin-bottom:8px; }} label {{ display:block; margin:11px 0; font-weight:600; }}
input,select,textarea {{ display:block; width:100%; box-sizing:border-box; margin-top:5px; padding:9px; border:1px solid #cbd3df; border-radius:7px; font:inherit; }}
input[type=checkbox] {{ display:inline-block; width:auto; margin-right:8px; }}
.hint {{ color:#526174; font-size:.92rem; }} .warning {{ background:#fff6df; padding:12px; border-radius:8px; }}
.account-card {{ margin-top:22px; padding:18px; border:1px solid #d7dee8; border-radius:10px; }}
.ok {{ color:#087443; background:#e9f8ef; padding:10px; border-radius:8px; }} .error {{ color:#a31b1b; background:#ffeded; padding:10px; border-radius:8px; }}
button {{ margin-top:12px; padding:10px 16px; border:0; border-radius:7px; background:#111827; color:#fff; font:inherit; cursor:pointer; }}
</style></head><body><main>
<h1>小红书 → X 多账号路线</h1>
<p><a href="/">← 返回凭证配置</a></p>
{status}
<div class="warning"><strong>安全说明：</strong>这里仅保存路由和账号标识。Access Token、Refresh Token、Cookie 和 API Secret 不会写入此文件；每个 X 账号请使用不同的 xurl App，并在服务器上单独完成 xurl OAuth2 授权。</div>
<p class="hint">启用路线前必须填写 xurl App 名称；首次使用建议保持 draft。保存后用 <code>python -m app --check-x-auth --account 账号ID</code> 验证 X 身份。</p>
{''.join(render_form(account_id, values) for account_id, values in sorted(accounts.items()))}
<section class="account-card"><h2>新增路线</h2>
<form method="post" action="/accounts/save">
<input type="hidden" name="csrf" value="{html.escape(csrf_token, quote=True)}">
{_account_input(new_values, 'account_id', '新账号 ID（字母、数字、_、-）')}
{new_fields}
<button type="submit">新增路线</button>
</form></section>
</main></body></html>"""


def _handler(store: ConfigStore, account_store: AccountConfigStore, csrf_token: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "RednoteConfig/1.0"

        def _send(self, body: str, status: int = 200) -> None:
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'"
            )
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if path == "/accounts":
                try:
                    self._send(render_accounts_page(csrf_token, account_store))
                except (OSError, ValueError) as exc:
                    self._send(render_accounts_page(csrf_token, account_store, error=str(exc)), 400)
                return
            if path not in {"/", "/index.html"}:
                self._send("Not found", 404)
                return
            self._send(render_config_page(csrf_token, store.public_state()))

        def do_POST(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if path not in {"/save", "/accounts/save"}:
                self._send("Not found", 404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > 64 * 1024:
                self._send("Invalid request", 400)
                return
            raw_body = self.rfile.read(length)
            form = parse_qs(raw_body.decode("utf-8"), keep_blank_values=True)
            if form.get("csrf", [""])[0] != csrf_token:
                self._send("CSRF validation failed", 403)
                return
            if path == "/accounts/save":
                try:
                    account_id = account_store.save(form)
                except (OSError, ValueError) as exc:
                    self._send(render_accounts_page(csrf_token, account_store, error=str(exc)), 400)
                    return
                self._send(
                    render_accounts_page(
                        csrf_token, account_store, message=f"路线 {account_id} 已保存。"
                    )
                )
                return
            try:
                result = store.save(form)
            except (OSError, ValueError) as exc:
                self._send(
                    render_config_page(csrf_token, store.public_state(), error=str(exc)), 400
                )
                return
            message = (
                f"已保存。MODE={result['mode']}；转载授权={'已确认' if result['authorized'] else '未确认'}；"
                f"OAuth 2.0 User Access Token={'已设置' if result['user_access_token_saved'] else '未设置'}。"
            )
            self._send(render_config_page(csrf_token, store.public_state(), message=message))

        def log_message(self, format: str, *args: object) -> None:
            # Do not emit request headers, query strings, or form values.
            return

    return Handler


def serve_config_ui(
    host: str = "127.0.0.1",
    port: int = 8787,
    env_path: Path | str = ".env",
    accounts_path: Path | str = "./accounts.toml",
) -> int:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("config UI must bind to localhost only")
    csrf_token = secrets.token_urlsafe(24)
    store = ConfigStore(Path(env_path))
    legacy_settings = Settings.from_env(env_path)
    account_store = AccountConfigStore(Path(accounts_path), legacy_settings)
    server = ThreadingHTTPServer((host, port), _handler(store, account_store, csrf_token))
    shown_host = "127.0.0.1" if host == "localhost" else host
    print(f"Config UI listening at http://{shown_host}:{server.server_port}/")
    print("Press Ctrl-C to stop. Secrets are stored only in the local .env file.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0
