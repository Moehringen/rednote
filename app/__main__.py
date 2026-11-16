from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import sys

from .config import Settings
from .pipeline import Pipeline, collector_lock, run_daemon


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Monitor a Xiaohongshu profile, save media, translate, and draft X threads"
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
    result.add_argument("--note-url", help="override XHS_NOTE_URL for --test-latest")
    return result


async def main_async(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    settings.ensure_directories()
    if args.healthcheck:
        if settings.xhs_backend == "cli" and not shutil.which(settings.xhs_cli_path):
            logging.error("xhs CLI is not installed: %s", settings.xhs_cli_path)
            return 1
        from .storage import NoteStore

        NoteStore(settings.db_path)
        return 0
    pipeline = Pipeline(settings)
    try:
        if args.test_latest:
            with collector_lock(settings.data_dir / ".collector.lock"):
                report = await pipeline.test_latest(args.note_url)
            print(report)
            return 0
        if args.test_profile_latest:
            with collector_lock(settings.data_dir / ".collector.lock"):
                report = await pipeline.test_profile_latest()
            print(report)
            return 0
        if args.daemon:
            await run_daemon(pipeline)
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
