import json
from pathlib import Path

from app.models import ProfileCandidate
from app.profile_discovery import select_latest
from app.xhs_cli import _records, parse_candidate, parse_note

FIXTURES = Path(__file__).parent / "fixtures"


def test_xhs_jsonl_and_candidate_parser() -> None:
    text = (FIXTURES / "xhs_profile_notes.jsonl").read_text()
    records = _records(text)
    candidates = [parse_candidate(record) for record in records]
    assert len(candidates) == 2
    assert candidates[0].xsec_token == "secret-token-1"
    assert candidates[0].is_pinned is None
    assert "xsec_token" not in candidates[0].source_url


def test_latest_uses_detail_time_when_pin_is_unknown() -> None:
    candidates = [
        ProfileCandidate("old", "old", "https://example/old", "2020-01-01", None),
        ProfileCandidate("new", "new", "https://example/new", "2026-11-15", None),
    ]
    assert select_latest(candidates).note_id == "new"


def test_parse_image_note_has_all_images_and_tags() -> None:
    note = parse_note(json.loads((FIXTURES / "xhs_image_note.json").read_text()))
    assert note.note_type == "image"
    assert len(note.image_urls) == 2
    assert note.cover_urls == []
    assert note.tags == ["摄影"]
    assert "secret" not in json.dumps(note.raw)


def test_parse_video_note_separates_cover_from_video() -> None:
    note = parse_note(json.loads((FIXTURES / "xhs_video_note.json").read_text()))
    assert note.note_type == "video"
    assert note.image_urls == []
    assert note.cover_urls == ["https://example.invalid/cover.jpg"]
    assert note.video_urls == ["https://example.invalid/video.mp4"]
