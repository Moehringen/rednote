from __future__ import annotations

import html
import json
import os
import re
import secrets
import tempfile
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

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
    "X_MEDIA_UPLOAD_BASE_URL": "https://upload.x.com",
    "X_MEDIA_UPLOAD_PATH": "/2/media/upload",
    "X_MAX_CHARS": "280",
}
_ENV_KEY = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


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
    <label>发帖接口<select name="X_POST_API_VERSION"><option value="v2"{' selected' if str(values.get('X_POST_API_VERSION', 'v2')) == 'v2' else ''}>V2 当前接口（推荐）</option><option value="v1.1"{' selected' if str(values.get('X_POST_API_VERSION', 'v2')) == 'v1.1' else ''}>V1.1 旧接口（OAuth1，已废弃）</option></select></label>
    <p class="hint">V2 发帖使用 <code>POST /2/tweets</code>；V1.1 仅用于兼容旧 App。OAuth1 媒体接口不使用已废弃的 <code>update_with_media</code>。</p>
    <label>OAuth1 媒体上传 Base URL<input name="X_MEDIA_UPLOAD_BASE_URL" value="{value('X_MEDIA_UPLOAD_BASE_URL')}"></label>
    <label>媒体上传路径<input name="X_MEDIA_UPLOAD_PATH" value="{value('X_MEDIA_UPLOAD_PATH')}"></label>
    <label>单条推文最大字符数<input name="X_MAX_CHARS" value="{value('X_MAX_CHARS')}" inputmode="numeric"></label>
    <button type="submit">安全保存配置</button>
  </form>
</main></body></html>"""


def _handler(store: ConfigStore, csrf_token: str):
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
            if urlsplit(self.path).path not in {"/", "/index.html"}:
                self._send("Not found", 404)
                return
            self._send(render_config_page(csrf_token, store.public_state()))

        def do_POST(self) -> None:  # noqa: N802
            if urlsplit(self.path).path != "/save":
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
    host: str = "127.0.0.1", port: int = 8787, env_path: Path | str = ".env"
) -> int:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("config UI must bind to localhost only")
    csrf_token = secrets.token_urlsafe(24)
    store = ConfigStore(Path(env_path))
    server = ThreadingHTTPServer((host, port), _handler(store, csrf_token))
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
