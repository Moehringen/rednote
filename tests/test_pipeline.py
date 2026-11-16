import asyncio
from pathlib import Path

from app.config import Settings
from app.models import Note, ProfileIdentity, Translation
from app.pipeline import Pipeline
from app.translate import Translator


class FakeXhs:
    async def resolve_profile_identity(self, profile_url: str, user_id: str, nickname: str):
        return ProfileIdentity(user_id, nickname, "thewoodpecker", profile_url, profile_url)

    async def note_from_url(self, source_url: str) -> Note:
        return Note(
            "000000000000000000000010",
            "琢磨鸟",
            "标题",
            "正文",
            "image",
            "2026-11-15T09:00:00+00:00",
            source_url=source_url,
            author_user_id="67d21d20000000000e01d41e",
        )


class FakeTranslator(Translator):
    async def translate(self, note: Note) -> Translation:
        return Translation("Title", "Body")


def test_test_latest_generates_note_translation_and_draft(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "data" / "state.sqlite3"))
    monkeypatch.setenv("TRANSLATION_PROVIDER", "mymemory")
    settings = Settings.from_env(None)

    async def fake_download(note, output_dir, timeout, attempts):
        image = output_dir / "images" / "01.jpg"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"fixture")
        return [image]

    monkeypatch.setattr("app.pipeline.download_note_media", fake_download)
    report = asyncio.run(
        Pipeline(settings, xhs_client=FakeXhs(), translator=FakeTranslator()).test_latest(
            "https://xhslink.cn/o/test"
        )
    )
    output = tmp_path / "data" / "test_latest"
    assert report == output / "report.md"
    assert (output / "note.json").exists()
    assert (output / "translated_en.txt").read_text().startswith("Title")
    assert (output / "draft.json").exists()
    assert "target_author_match: `True`" in (output / "report.md").read_text()
