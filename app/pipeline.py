from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import shutil
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .browser_xhs import BrowserXhsClient
from .config import Settings
from .media import MediaDownloadError, download_note_media
from .models import Note, ProfileCandidate, Translation
from .profile_discovery import rank_candidates, select_latest
from .storage import NoteStore
from .thread_builder import DraftPost, build_thread, draft_to_dict
from .translate import TranslationError, Translator, make_translator
from .x_client import XAPIError, XClient
from .xhs_cli import XhsCLIClient, XhsCLIError

LOGGER = logging.getLogger(__name__)


@contextmanager
def collector_lock(path: Path):
    """Serialize browser-backed jobs that share the persistent Chromium page."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        xhs_client: XhsCLIClient | None = None,
        translator: Translator | None = None,
        store: NoteStore | None = None,
    ) -> None:
        self.settings = settings
        settings.ensure_directories()
        if xhs_client:
            self.xhs = xhs_client
        elif settings.xhs_backend == "browser":
            self.xhs = BrowserXhsClient(
                settings.profile_url,
                settings.expected_user_id,
                settings.target_nickname,
                settings.target_english_name,
                settings.browser_profile_dir,
                settings.browser_executable_path,
                settings.browser_headless,
                settings.browser_login_timeout_seconds,
                settings.timezone,
            )
        else:
            self.xhs = XhsCLIClient(
                settings.xhs_cli_path,
                settings.xhs_config_dir,
                settings.xhs_cache_dir,
                settings.xhs_cookie_file,
                settings.xhs_rate,
                settings.request_timeout_seconds,
            )
        self.translator = translator
        self.store = store or NoteStore(settings.db_path)

    async def close(self) -> None:
        close = getattr(self.xhs, "close", None)
        if close:
            await close()

    async def test_latest(self, source_url: str | None = None) -> Path:
        source_url = source_url or self.settings.note_url
        output_dir = self.settings.data_dir / "test_latest"
        self._reset_test_output(output_dir)
        return await self._fetch_process_report(source_url, output_dir, "test-latest")

    async def test_profile_latest(self) -> Path:
        """Process the newest verified profile note, including baseline notes."""
        output_dir = self.settings.data_dir / "test_latest"
        self._reset_test_output(output_dir)
        errors: list[str] = []
        identity = None
        try:
            identity = await self.xhs.resolve_profile_identity(
                self.settings.profile_url,
                self.settings.expected_user_id,
                self.settings.target_nickname,
            )
            candidates = await self.xhs.list_user_notes(
                identity.user_id, self.settings.profile_candidate_limit
            )
            detailed: list[tuple[ProfileCandidate, Note]] = []
            for candidate in candidates:
                try:
                    note = await self.xhs.note(
                        candidate.note_id, candidate.xsec_token, candidate.source_url
                    )
                except XhsCLIError as exc:
                    errors.append(f"detail {candidate.note_id}: {exc}")
                    continue
                if note.author_user_id != identity.user_id or note.author != identity.nickname:
                    errors.append(f"author mismatch for note {candidate.note_id}; skipped")
                    continue
                candidate.published_at = note.published_at
                detailed.append((candidate, note))
            latest = select_latest([candidate for candidate, _ in detailed])
            if latest is None:
                raise XhsCLIError("verified target profile returned no dated note candidates")
            latest_note = next(note for candidate, note in detailed if candidate.note_id == latest.note_id)
            return await self._fetch_process_report(
                latest_note.source_url,
                output_dir,
                "test-profile-latest",
                prefetched_note=latest_note,
                prefetched_identity=identity,
            )
        except (XhsCLIError, OSError, ValueError) as exc:
            errors.append(str(exc))
            return self._write_report(
                output_dir, None, errors, [], None, "test-profile-latest", identity
            )

    async def publish_profile_rank(self, rank: int) -> Path:
        """Publish one explicitly requested non-pinned profile rank."""
        report_dir = self.settings.data_dir / "daily"
        report_dir.mkdir(parents=True, exist_ok=True)
        errors: list[str] = []
        identity = None
        if rank < 1:
            return self._write_report(
                report_dir,
                None,
                ["profile rank must be at least 1"],
                [],
                None,
                "publish-profile-rank",
                None,
            )
        try:
            identity = await self.xhs.resolve_profile_identity(
                self.settings.profile_url,
                self.settings.expected_user_id,
                self.settings.target_nickname,
            )
            candidates = await self.xhs.list_user_notes(
                identity.user_id, self.settings.profile_candidate_limit
            )
            detailed: list[tuple[ProfileCandidate, Note]] = []
            for candidate in candidates:
                try:
                    note = await self.xhs.note(
                        candidate.note_id, candidate.xsec_token, candidate.source_url
                    )
                except XhsCLIError as exc:
                    errors.append(f"detail {candidate.note_id}: {exc}")
                    continue
                if note.author_user_id != identity.user_id or note.author != identity.nickname:
                    errors.append(f"author mismatch for note {candidate.note_id}; skipped")
                    continue
                candidate.published_at = note.published_at
                candidate.author = note.author
                candidate.author_user_id = note.author_user_id
                detailed.append((candidate, note))
            ranked = rank_candidates([candidate for candidate, _ in detailed])
            if len(ranked) < rank:
                raise XhsCLIError(
                    f"only {len(ranked)} verified non-pinned note candidates are available; rank {rank} not found"
                )
            target = ranked[rank - 1]
            note = next(note for candidate, note in detailed if candidate.note_id == target.note_id)
            row = self.store.get(note.note_id)
            if row and row["status"] in {"published", "publishing", "publish_unknown"}:
                raise XhsCLIError(
                    f"note {note.note_id} already has publication state {row['status']}; refusing duplicate post"
                )
            return await self._process_note(
                note, report_dir, f"publish-profile-rank-{rank}", identity, errors
            )
        except (XhsCLIError, OSError, ValueError) as exc:
            errors.append(str(exc))
            return self._write_report(
                report_dir,
                None,
                errors,
                [],
                None,
                f"publish-profile-rank-{rank}",
                identity,
            )

    async def _fetch_process_report(
        self,
        source_url: str,
        output_dir: Path,
        operation: str,
        prefetched_note: Note | None = None,
        prefetched_identity: object | None = None,
    ) -> Path:
        errors: list[str] = []
        note: Note | None = None
        translation: Translation | None = None
        media_paths: list[Path] = []
        identity = None
        try:
            identity = prefetched_identity or await self.xhs.resolve_profile_identity(
                self.settings.profile_url,
                self.settings.expected_user_id,
                self.settings.target_nickname,
            )
            note = prefetched_note or await self.xhs.note_from_url(source_url)
            if note.author_user_id != identity.user_id:
                errors.append(
                    "target author mismatch: note user_id did not match the verified profile user_id"
                )
            if note.author != identity.nickname:
                errors.append(
                    f"target author nickname mismatch: expected {identity.nickname!r}, got {note.author!r}"
                )
            note.raw["target_profile_final_url"] = identity.final_url
            note.raw["target_profile_red_id"] = identity.red_id
            note.raw["pinned_status"] = "not_exposed_by_browser_profile"
            _write_json(output_dir / "note.json", note.to_dict())
            try:
                media_paths = await download_note_media(
                    note,
                    output_dir,
                    self.settings.request_timeout_seconds,
                    self.settings.retry_attempts,
                )
            except MediaDownloadError as exc:
                errors.append(str(exc))
            try:
                translation = await (self.translator or make_translator(self.settings)).translate(
                    note
                )
                (output_dir / "translated_en.txt").write_text(
                    translation.as_text() + "\n", encoding="utf-8"
                )
            except TranslationError as exc:
                errors.append(str(exc))
                (output_dir / "translated_en.txt").write_text(
                    "Translation unavailable; see report.md.\n", encoding="utf-8"
                )
            if translation and not errors:
                posts = build_thread(
                    note,
                    translation,
                    media_paths,
                    note.source_url,
                    self.settings.x_max_chars,
                    self.settings.x_tags,
                )
                _write_json(output_dir / "draft.json", draft_to_dict(posts))
            elif translation:
                # A draft is still useful for manual review when media or identity
                # verification failed, but it is never publishable in this state.
                try:
                    posts = build_thread(
                        note,
                        translation,
                        media_paths,
                        note.source_url,
                        self.settings.x_max_chars,
                        self.settings.x_tags,
                    )
                    _write_json(output_dir / "draft.json", draft_to_dict(posts))
                except ValueError as exc:
                    errors.append(str(exc))
            if note.note_id:
                self.store.upsert_discovered(note.note_id, note.source_url)
                self.store.set_status(
                    note.note_id,
                    "drafted" if translation and not errors else "failed",
                    "; ".join(errors) or None,
                )
        except (XhsCLIError, OSError, ValueError) as exc:
            errors.append(str(exc))
            (output_dir / "translated_en.txt").write_text(
                "Translation unavailable; see report.md.\n", encoding="utf-8"
            )
        return self._write_report(
            output_dir, note, errors, media_paths, translation, operation, identity
        )

    @staticmethod
    def _reset_test_output(output_dir: Path) -> None:
        """Remove only generated single-note test artifacts before a new run."""
        output_dir.mkdir(parents=True, exist_ok=True)
        for child in output_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()

    async def run_once(self) -> Path:
        report_dir = self.settings.data_dir / "daily"
        report_dir.mkdir(parents=True, exist_ok=True)
        errors: list[str] = []
        try:
            identity = await self.xhs.resolve_profile_identity(
                self.settings.profile_url,
                self.settings.expected_user_id,
                self.settings.target_nickname,
            )
            candidates = await self.xhs.list_user_notes(
                identity.user_id, self.settings.profile_candidate_limit
            )
            if not candidates:
                raise XhsCLIError("verified target profile returned no note candidates")
            detailed: list[tuple[ProfileCandidate, Note]] = []
            for candidate in candidates:
                try:
                    note = await self.xhs.note(
                        candidate.note_id, candidate.xsec_token, candidate.source_url
                    )
                except XhsCLIError as exc:
                    errors.append(f"detail {candidate.note_id}: {exc}")
                    continue
                if note.author_user_id != identity.user_id or note.author != identity.nickname:
                    errors.append(f"author mismatch for note {candidate.note_id}; skipped")
                    continue
                candidate.published_at = note.published_at
                candidate.author = note.author
                candidate.author_user_id = note.author_user_id
                detailed.append((candidate, note))

            if not detailed:
                return self._write_report(
                    report_dir,
                    None,
                    errors or ["no candidate passed target author verification"],
                    [],
                    None,
                    "run-once",
                    identity,
                )

            was_empty = not self.store.baseline_initialized()
            for candidate, _ in detailed:
                self.store.upsert_discovered(candidate.note_id, candidate.source_url)
            if was_empty:
                self.store.mark_baseline([candidate.note_id for candidate, _ in detailed])
                return self._write_report(
                    report_dir,
                    None,
                    [
                        f"baseline initialized with {len(detailed)} verified notes; no historical post will be published",
                        *errors,
                    ],
                    [],
                    None,
                    "run-once",
                    identity,
                )

            ordered = [candidate for candidate, _ in detailed]
            remaining = [
                candidate
                for candidate in ordered
                if not self._is_terminal(self.store.get(candidate.note_id))
            ]
            latest = select_latest(remaining)
            if latest is None:
                return self._write_report(
                    report_dir,
                    None,
                    ["no new verified note; existing notes are already processed", *errors],
                    [],
                    None,
                    "run-once",
                    identity,
                )
            if not latest.published_at:
                return self._write_report(
                    report_dir,
                    None,
                    [
                        "candidate details did not expose published_at; refusing to infer latest from profile order",
                        *errors,
                    ],
                    [],
                    None,
                    "run-once",
                    identity,
                )
            note = next(note for candidate, note in detailed if candidate.note_id == latest.note_id)
            return await self._process_note(
                note,
                report_dir,
                "run-once",
                identity,
                errors,
            )
        except (XhsCLIError, OSError, ValueError) as exc:
            errors.append(str(exc))
            return self._write_report(
                report_dir, None, errors, [], None, "run-once", None
            )

    @staticmethod
    def _is_terminal(row: object) -> bool:
        if row is None:
            return False
        status = row["status"]
        return status in {"baseline", "drafted", "published", "publish_unknown"}

    async def _process_note(
        self,
        note: Note,
        output_dir: Path,
        operation: str,
        identity: object,
        errors: list[str] | None = None,
    ) -> Path:
        errors = list(errors or [])
        translation: Translation | None = None
        media_paths: list[Path] = []
        note_dir = output_dir / note.note_id
        note_dir.mkdir(parents=True, exist_ok=True)
        _write_json(note_dir / "note.json", note.to_dict())
        try:
            media_paths = await download_note_media(
                note,
                note_dir,
                self.settings.request_timeout_seconds,
                self.settings.retry_attempts,
            )
        except MediaDownloadError as exc:
            errors.append(str(exc))
        try:
            translation = await (self.translator or make_translator(self.settings)).translate(note)
            (note_dir / "translated_en.txt").write_text(
                translation.as_text() + "\n", encoding="utf-8"
            )
        except TranslationError as exc:
            errors.append(str(exc))
            (note_dir / "translated_en.txt").write_text(
                "Translation unavailable; see report.md.\n", encoding="utf-8"
            )
        posts: list[DraftPost] = []
        if translation:
            try:
                posts = build_thread(
                    note,
                    translation,
                    media_paths,
                    note.source_url,
                    self.settings.x_max_chars,
                    self.settings.x_tags,
                )
                _write_json(note_dir / "draft.json", draft_to_dict(posts))
            except ValueError as exc:
                errors.append(str(exc))
        if posts:
            ready, reason = self.settings.publish_ready
            if not ready:
                errors.append(f"publish blocked: {reason}")
            elif not errors:
                await self._publish(note, posts, errors)
        if note.note_id:
            self.store.upsert_discovered(note.note_id, note.source_url)
            current = self.store.get(note.note_id)
            status = (
                current["status"]
                if current and current["status"] in {"published", "publish_unknown"}
                else ("drafted" if translation and not errors else "failed")
            )
            self.store.set_status(note.note_id, status, "; ".join(errors) or None)
        return self._write_report(
            note_dir, note, errors, media_paths, translation, operation, identity
        )

    async def _publish(self, note: Note, posts: list[DraftPost], errors: list[str]) -> None:
        client = XClient(
            self.settings.x_api_base_url,
            self.settings.x_user_access_token,
            self.settings.x_media_upload_path,
            self.settings.request_timeout_seconds,
            self.settings.retry_attempts,
            self.settings.x_max_chars,
            auth_mode=self.settings.x_auth_mode,
            consumer_key=self.settings.x_api_key,
            consumer_secret=self.settings.x_api_secret,
            resource_owner_key=self.settings.x_access_token,
            resource_owner_secret=self.settings.x_access_token_secret,
            media_base_url=self.settings.x_media_upload_base_url,
        )
        saved_tweet_ids, saved_media_ids = self.store.get_publish_state(note.note_id)

        def progress(tweet_ids: list[str], media_ids: dict[str, list[str]]) -> None:
            self.store.set_status(
                note.note_id, "publishing", tweet_ids=tweet_ids, media_ids=media_ids
            )

        try:
            tweet_ids = await client.publish_thread(
                posts, saved_tweet_ids, saved_media_ids, progress
            )
            self.store.set_status(
                note.note_id, "published", tweet_ids=tweet_ids, media_ids=saved_media_ids
            )
        except XAPIError as exc:
            self.store.set_status(
                note.note_id,
                "publish_unknown",
                error_reason=str(exc),
                tweet_ids=saved_tweet_ids,
                media_ids=saved_media_ids,
            )
            errors.append(f"publish stopped for reconciliation: {exc}")

    def _write_report(
        self,
        directory: Path,
        note: Note | None,
        errors: list[str],
        media_paths: list[Path],
        translation: Translation | None,
        operation: str,
        identity: object,
    ) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        identity_user_id = getattr(identity, "user_id", "")
        lines = [
            "# Xiaohongshu → X report",
            "",
            f"- operation: `{operation}`",
            f"- checked_at: `{datetime.now().astimezone().isoformat()}`",
            f"- collector: `{self.settings.xhs_backend}`",
            f"- mode: `{self.settings.mode}`",
            f"- publish_gate: `{self.settings.publish_ready[1]}`",
            f"- verified_profile_user_id: `{identity_user_id or 'not verified'}`",
        ]
        if note:
            author_match = bool(
                identity_user_id
                and note.author_user_id == identity_user_id
                and note.author == self.settings.target_nickname
            )
            lines.extend(
                [
                    f"- note_id: `{note.note_id or 'unknown'}`",
                    f"- title: {note.title or '(empty)'}",
                    f"- published_at: `{note.published_at or 'not provided'}`",
                    f"- author: `{note.author or 'not provided'}`",
                    f"- author_user_id: `{note.author_user_id or 'not provided'}`",
                    f"- target_author_match: `{author_match}`",
                    f"- note_type: `{note.note_type}`",
                    f"- image_urls: `{len(note.image_urls)}`",
                    f"- video_urls: `{len(note.video_urls)}`",
                    f"- cover_urls: `{len(note.cover_urls)}`",
                    f"- downloaded_media: `{len(media_paths)}`",
                    f"- body_summary: {note.body[:160].replace(chr(10), ' ') if note.body else '(empty)'}",
                    "- pinned_status: `not exposed by xhs-cli; latest selected by verified published_at`",
                ]
            )
        else:
            lines.append("- note: no note detail was obtained")
        lines.append(f"- translation_generated: `{translation is not None}`")
        lines.extend(["", "## Errors / limitations"])
        lines.extend(f"- {error}" for error in errors) if errors else lines.append("- none")
        lines.extend(
            [
                "",
                "## Evidence boundary",
                "- xhs-cli output and note detail were used; access tokens are not persisted in this report.",
                "- No X post is created unless MODE=publish, REPOST_AUTHORIZED=true, and credentials are present.",
            ]
        )
        report = directory / "report.md"
        report.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return report


async def run_daemon(pipeline: Pipeline) -> None:
    zone = ZoneInfo(pipeline.settings.timezone)
    lock_path = pipeline.settings.data_dir / ".collector.lock"
    while True:
        with collector_lock(lock_path):
            await pipeline.run_once()
        now = datetime.now(zone)
        next_run = now.replace(hour=9, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        await asyncio.sleep(max(1, (next_run - now).total_seconds()))
