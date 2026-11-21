from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import sys
from pathlib import Path

from .accounts import load_account_settings, select_account
from .config import Settings
from .config_ui import serve_config_ui
from .pipeline import Pipeline, collector_lock, run_daemon
from .x_client import XAPIError, XClient


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Monitor a Xiaohongshu profile, save media, translate, and draft single X posts"
    )
    result.add_argument(
        "--test-latest", action="store_true", help="process exactly one supplied note URL"
    )
    result.add_argument(
        "--test-profile-latest",
        action="store_true",
        help="process the newest verified note from the target profile",
    )
    result.add_argument(
        "--publish-profile-rank",
        type=int,
        metavar="N",
        help="publish the Nth newest verified non-pinned profile note once",
    )
    result.add_argument(
        "--publish-note-url",
        help="publish one explicitly supplied, author-verified note URL once",
    )
    result.add_argument(
        "--republish-note-url",
        help="explicitly publish a corrected copy of one already-processed note URL",
    )
    result.add_argument(
        "--run-once",
        action="store_true",
        help="discover profile notes and process at most one new note",
    )
    result.add_argument(
        "--daemon",
        action="store_true",
        help="run immediately, then every day at 09:00 Asia/Singapore",
    )
    result.add_argument(
        "--healthcheck", action="store_true", help="verify data directory and SQLite can be opened"
    )
    result.add_argument(
        "--config-ui",
        action="store_true",
        help="start the localhost-only X credential configuration page",
    )
    result.add_argument(
        "--check-x-auth",
        action="store_true",
        help="run a read-only X /2/users/me authentication check",
    )
    result.add_argument(
        "--account",
        help="select one configured account; required when multiple accounts are enabled",
    )
    result.add_argument(
        "--accounts-config",
        help="override the multi-account TOML path (default: ACCOUNTS_CONFIG or ./accounts.toml)",
    )
    result.add_argument(
        "--list-accounts",
        action="store_true",
        help="list enabled account IDs and exit without contacting external services",
    )
    result.add_argument(
        "--config-ui-port",
        type=int,
        default=8787,
        help="localhost config page port (default: 8787)",
    )
    result.add_argument("--note-url", help="override XHS_NOTE_URL for --test-latest")
    return result


async def main_async(args: argparse.Namespace) -> int:
    if args.config_ui:
        base_settings = Settings.from_env()
        accounts_path = Path(args.accounts_config) if args.accounts_config else base_settings.accounts_config
        return serve_config_ui("127.0.0.1", args.config_ui_port, ".env", accounts_path)
    base_settings = Settings.from_env()
    accounts = load_account_settings(
        base_settings, Path(args.accounts_config) if args.accounts_config else None
    )
    if args.list_accounts:
        for account in accounts:
            print(account.account_id)
        return 0
    if args.daemon:
        pipelines = [Pipeline(account) for account in accounts]
        try:
            await run_daemon(pipelines)
        finally:
            for pipeline in pipelines:
                await pipeline.close()
        return 0

    settings = select_account(accounts, args.account)
    settings.ensure_directories()
    if args.healthcheck:
        if settings.xhs_backend == "cli" and not shutil.which(settings.xhs_cli_path):
            logging.error("xhs CLI is not installed: %s", settings.xhs_cli_path)
            return 1
        from .storage import NoteStore

        NoteStore(settings.db_path)
        return 0
    if args.check_x_auth:
        ready, reason = settings.publish_ready
        if not ready and settings.mode == "publish":
            logging.error("X auth check blocked by publish configuration: %s", reason)
            return 1
        client = XClient(
            settings.x_api_base_url,
            settings.x_user_access_token,
            settings.x_media_upload_path,
            settings.request_timeout_seconds,
            settings.retry_attempts,
            settings.x_max_chars,
            auth_mode=settings.x_auth_mode,
            post_api_version=settings.x_post_api_version,
            token_source=settings.x_token_source,
            xurl_app=settings.xurl_app,
            xurl_path=settings.xurl_path,
            consumer_key=settings.x_api_key,
            consumer_secret=settings.x_api_secret,
            resource_owner_key=settings.x_access_token,
            resource_owner_secret=settings.x_access_token_secret,
            media_base_url=settings.x_media_upload_base_url,
        )
        try:
            payload = await client.verify_credentials()
            user_id, username = client.validate_identity(
                payload, settings.x_expected_user_id, settings.x_username
            )
        except XAPIError as exc:
            logging.error("X auth check failed: %s", exc)
            return 1
        print(
            "X auth ok: auth_mode={} user_id={} username={}".format(
                client.auth_mode,
                user_id or "unknown",
                username or "unknown",
            )
        )
        return 0
    pipeline = Pipeline(settings)
    try:
        if args.test_latest:
            with collector_lock(settings.data_dir / ".collector.lock"):
                report = await pipeline.test_latest(args.note_url)
            print(report)
            return 0
        if args.publish_note_url:
            with collector_lock(settings.data_dir / ".collector.lock"):
                report = await pipeline.publish_note_url(args.publish_note_url)
            print(report)
            return 0
        if args.republish_note_url:
            with collector_lock(settings.data_dir / ".collector.lock"):
                report = await pipeline.republish_note_url(args.republish_note_url)
            print(report)
            return 0
        if args.test_profile_latest:
            with collector_lock(settings.data_dir / ".collector.lock"):
                report = await pipeline.test_profile_latest()
            print(report)
            return 0
        if args.publish_profile_rank is not None:
            with collector_lock(settings.data_dir / ".collector.lock"):
                report = await pipeline.publish_profile_rank(args.publish_profile_rank)
            print(report)
            return 0
        report = await pipeline.run_once()
        print(report)
        return 0
    finally:
        await pipeline.close()


def main() -> int:
    args = parser().parse_args()
    settings = Settings.from_env()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        logging.error("run failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
