# Xiaohongshu → X 自动化

这是一个独立于 Yaobi 主应用的 Python 3.12 项目，用只读的
[`tamnd/xiaohongshu-cli`](https://github.com/tamnd/xiaohongshu-cli) 监测小红书主页，保存图文/视频原始素材，调用公开翻译 API 生成英文内容，并默认生成 X 草稿。

项目不会自动公开转载第三方内容。只有 `MODE=publish`、`REPOST_AUTHORIZED=true` 和完整的 X 用户授权凭证同时满足时，才会尝试发布。

## 当前工作流

1. 解析主页分享链接并跟随重定向。
2. 用 `xhs user <id>` 实际读取并验证 user_id、昵称和公开主页信息。
3. 用 `xhs user <id> --notes` 获取候选笔记。
4. 对候选笔记读取详情，按详情中的真实发布时间排序；`xhs-cli` 未暴露置顶字段，因此报告会明确标记置顶状态未验证。
5. 下载图文笔记全部图片，或下载视频笔记的可播放视频 URL；视频封面不会被当成完整图文。
6. 默认通过 MyMemory 翻译标题和正文，也可切换 LibreTranslate。
7. 保存来源 URL 到元数据和报告（不写入推文正文），从可配置的人像/女性摄影标签池中为每篇笔记稳定随机抽取标签，生成单条推文草稿。
8. SQLite 记录 baseline、处理状态、tweet_id、媒体 ID 和错误原因，避免重启重复发布。

## 本机持久浏览器后端

如果 Cookie 文件经常过期，可切换到可见的持久浏览器会话。浏览器使用独立用户目录，首次运行时在窗口中登录小红书；程序不会导出或打印 Cookie。登录状态由浏览器自己保存，失效时程序暂停并等待手工登录。

```sh
python3 -m venv /path/to/rednote-browser-venv
/path/to/rednote-browser-venv/bin/pip install -r requirements-browser.txt
cp .env.example .env
# 将 XHS_BACKEND 改成 browser，并设置 BROWSER_EXECUTABLE_PATH（macOS 可留空使用 Playwright Chromium）
/path/to/rednote-browser-venv/bin/python -m app --run-once
```

浏览器模式只读取主页、笔记详情和媒体 URL，不点赞、不评论、不发布。首次初始化时仍遵循 SQLite baseline 规则；未完成登录或页面被风控时会生成失败报告，不会伪造“没有新帖”。

## 本地运行

```sh
cd rednote_to_x
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env

# 检查目录和 SQLite
python -m app --healthcheck

# 启动仅监听本机的 X 凭证配置页面
python -m app --config-ui
# 浏览器打开 http://127.0.0.1:8787/

# 单篇端到端草稿测试。不会发布到 X。
python -m app --test-latest --note-url 'https://xhslink.cn/o/46BbiPl8OPJ'

# 每日主页检查；首次运行只建立 baseline，不发布历史笔记。
python -m app --run-once
```

本机需要安装 `xhs` 0.2.0 并让它位于 PATH：

```sh
go install github.com/tamnd/xiaohongshu-cli/cmd/xhs@v0.2.0
```

v0.2.0 的 Go module 要求 Go 1.26；没有 Go 环境时可直接使用 Docker Compose，它会在构建阶段安装固定版本。

配置页面只绑定 127.0.0.1，保存目标是当前目录的 .env，写入使用原子替换并设置为 600 权限。页面不会回显已经保存的 secret，也不会记录 POST 内容。当前发布客户端支持两种用户授权：

- `X_AUTH_MODE=oauth2`：使用 X Developer 页面为目标账号生成的 OAuth 2.0 User Access Token；App-only Bearer Token 不能代表账号发帖，图片/视频上传需要相应的媒体权限。
- `X_AUTH_MODE=oauth1`：使用旧 App 的 `X_API_KEY`、`X_API_SECRET`、`X_ACCESS_TOKEN`、`X_ACCESS_TOKEN_SECRET`。OAuth1 媒体走 `upload.x.com/1.1/media/upload.json` 的用户上下文分段上传。
- `X_POST_API_VERSION=v2`（默认）：发帖走当前 `POST /2/tweets`，需要当前 Project 权限。
- `X_POST_API_VERSION=v1.1`：免费旧 App 兼容路径，发帖走 `POST /1.1/statuses/update.json`，仅允许和 OAuth1 一起使用。该接口属于旧版接口，可能被 X 限制或下线；系统仍不使用已废弃的 `update_with_media`。

配置页按版本分区保存凭证：

- V1 区域是 OAuth 1.0a 的四项用户凭证：Consumer/API Key、API Secret、OAuth1 User Access Token、OAuth1 User Access Token Secret。
- V2 区域是 OAuth 2.0 的 Client ID、Client Secret、User Access Token，以及可选的 App-only Bearer Token。Bearer Token 只能用于不需要用户身份的公开数据只读请求，不能用于 `POST /2/tweets`；V2 发帖必须使用 User Access Token，并申请 `tweet.write`、`users.read` 和媒体所需的 `media.write` 权限。

可以先做不发帖的鉴权检查：

```sh
python -m app --check-x-auth
```

首次配置建议保持 `MODE=draft`，确认译文、标签和转载授权后再单独打开发布门禁。

主页长期轮询可能需要 Cookie。不要把 Cookie 写进 Git 或命令历史；可将浏览器导出的 Cookie 文件（请求头格式或 Netscape 格式）放在 `.env` 指定的 `XHS_COOKIE_FILE` 路径。xhs CLI 的 profile 页面会受到限流，配置了登录 Cookie 后再运行：

```sh
xhs user "$XHS_EXPECTED_USER_ID" --notes -n 10 -o jsonl
```

当前固定的 `xhs v0.2.0` CLI 没有二维码登录子命令；请通过浏览器开发者工具导出 Cookie 文件，设置权限为 `600`，再让 `XHS_COOKIE_FILE` 指向它。可以用 `xhs me` 检查登录状态，`xhs config show` 只显示脱敏后的配置。程序不会打印 Cookie、xsec_token 或 X 凭证。

## 输出

单篇测试输出到 `data/test_latest/`：

- `note.json`：note_id、来源 URL、最终 URL、作者、发布时间、标题、正文、类型、图片/视频 URL 和清理后的原始字段；
- `images/`：图文原始图片；
- `video.mp4`：视频原始文件；
- `translated_en.txt`：英文译文；
- `draft.json`：单条 X 推文草稿；
- `report.md`：身份匹配、发布时间、媒体数量、翻译状态和错误信息。

注意：`xsec_token` 用于当前详情读取，但不会写进报告、草稿或日志。

## 翻译

默认使用公开 MyMemory API：

```dotenv
TRANSLATION_PROVIDER=mymemory
```

也支持：

```dotenv
TRANSLATION_PROVIDER=libretranslate
LIBRETRANSLATE_BASE_URL=https://libretranslate.com
LIBRETRANSLATE_API_KEY=
```

翻译模块会保护原文中的 hashtag，不会自动添加原文没有的事实。翻译失败时不会进入发布流程。

### X 标签池

`X_TAG_POOL` 使用空格分隔，默认预留了一组人像摄影、女性肖像、时尚和视觉叙事标签；`X_TAG_COUNT=4` 控制每篇抽取数量。抽取以 `note_id` 为种子，因此同一篇笔记重试时标签保持一致。系统会过滤 `#Xiaohongshu`、`#Rednote` 和 `#小红书` 等来源平台标签。来源链接仍保存在 `note.json`、`report.md` 和 SQLite 中，但不会出现在推文文本中。

## X 发布门禁

```dotenv
MODE=draft
REPOST_AUTHORIZED=false
X_AUTH_MODE=oauth1
X_POST_API_VERSION=v1.1
X_API_KEY=
X_API_SECRET=
X_ACCESS_TOKEN=
X_ACCESS_TOKEN_SECRET=
X_USER_ACCESS_TOKEN=
```

草稿模式不调用 X API。发布模式还需要用户确认转载授权。普通账号始终只发布一条推文：正文超过 `X_MAX_CHARS` 时在可读边界自动截断并加省略号，图片最多附加四张，超出的原图仍保存在本地但不附加到 X；视频作为单独媒体发布。

OAuth2 模式使用当前 v2 媒体分段流程并需要 `media.write`。OAuth1 + `X_POST_API_VERSION=v1.1` 使用旧 App 的 user-context 分段媒体上传，再把 media_id 交给 `POST /1.1/statuses/update.json`。官方文档说明 `update_with_media` 已废弃，替代方式是先上传媒体再调用 `statuses/update`；旧版接口是否仍可用以实际账号响应为准。

## Docker Compose

```sh
cd rednote_to_x
cp .env.example .env
mkdir -p data/cookies data/xhs-config data/xhs-cache
chmod 700 data data/cookies data/xhs-config data/xhs-cache
sudo chown -R 10001:10001 data
chmod 600 .env
docker compose up -d --build
docker compose logs -f rednote-to-x
```

VPS 注意事项：容器以 UID/GID `10001:10001` 非 root 用户运行，因此宿主机首次部署必须执行上面的 `chown`。`.env`、Cookie 文件和 xhs 缓存都只放在 VPS 的受控目录，不提交 Git；建议 VPS 防火墙只开放 SSH 和你明确需要的端口，不需要把此 worker 暴露到公网。

数据、SQLite、xhs CLI 配置/缓存和 Cookie 文件都持久化在 `./data`。容器根文件系统只读，只有 `/app/data` 和 `/tmp` 可写；使用 `Asia/Singapore` 时区，进程启动后立即检查一次，之后每天 09:00 检查。Compose 提供健康检查、非 root 运行、自动重启和 JSON 日志轮转。

## GitHub Actions 自动部署 VPS

仓库的 `.github/workflows/deploy.yml` 会在 `main` 分支通过测试后，使用 Google Cloud Workload Identity Federation 执行部署。需要在 GitHub Actions Secrets 配置：

- `GCP_WORKLOAD_IDENTITY_PROVIDER`
- `GCP_SERVICE_ACCOUNT`
- `GCP_PROJECT_ID`
- `GCP_ZONE`
- `GCP_INSTANCE`

部署脚本把代码 checkout 到 `/opt/rednote_to_x/source`，把 `.env`、`data/`、SQLite、Cookie 和浏览器资料保留在 `/opt/rednote_to_x/`。首次部署前，在 VPS 手动创建 `/opt/rednote_to_x/.env`，并至少设置：

```dotenv
XHS_BACKEND=browser
BROWSER_HEADLESS=false
BROWSER_LOGIN_TIMEOUT_SECONDS=600
MODE=draft
REPOST_AUTHORIZED=false
HOST_DATA_DIR=/opt/rednote_to_x/data
```

镜像内已经包含 Playwright Chromium 和 Xvfb；不需要 VPS 有真实桌面。首次登录仍需通过临时 noVNC/SSH 隧道完成一次，后续由容器每天 09:00 自动运行。CI 不读取、不上传 `.env`、Cookie、SQLite 或浏览器资料。

远端首次登录测试时，部署脚本默认开启仅绑定 VPS 本机的 noVNC：

```sh
gcloud compute ssh --zone "$GCP_ZONE" "$GCP_INSTANCE" --project "$GCP_PROJECT_ID" \
  -- -L 6080:127.0.0.1:6080
```

然后在本机打开 `http://127.0.0.1:6080/vnc.html?autoconnect=true`。登录完成并关闭浏览器后，持久化资料会留在 `data/browser-profile`；noVNC 端口不会暴露到公网。

在容器内验证持久化的 Cookie 文件时，可以执行：

```sh
docker compose run --rm rednote-to-x xhs me
```

## 测试

```sh
ruff check app tests
python -m pytest -q
```

测试使用 JSON fixtures、Mock HTTP 和 fake translator，不依赖登录、真实翻译 API 或 X 凭证。真实主页、媒体和翻译测试会把网络/登录限制写进 `report.md`，不会伪造成功结果。
