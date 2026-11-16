from app.models import ProfileCandidate
from app.profile_discovery import rank_candidates, select_latest


def test_latest_ignores_pinned_old_note() -> None:
    candidates = [
        ProfileCandidate(
            "000000000000000000000001", "pinned", "https://example/1", "2020-01-01", True
        ),
        ProfileCandidate(
            "000000000000000000000002", "new", "https://example/2", "2026-11-15", False
        ),
        ProfileCandidate(
            "000000000000000000000003", "old", "https://example/3", "2026-11-01", False
        ),
    ]
    assert select_latest(candidates).note_id == "000000000000000000000002"


def test_latest_falls_back_to_pinned_when_all_are_pinned() -> None:
    candidates = [
        ProfileCandidate(
            "000000000000000000000001", "old", "https://example/1", "2020-01-01", True
        ),
        ProfileCandidate(
            "000000000000000000000002", "newer", "https://example/2", "2021-01-01", True
        ),
    ]
    assert select_latest(candidates).note_id == "000000000000000000000002"


def test_rank_excludes_pinned_and_returns_second_newest() -> None:
    candidates = [
        ProfileCandidate("pinned", "Pinned", "u/pinned", "2026-11-16T00:00:00+00:00", True),
        ProfileCandidate("new", "New", "u/new", "2026-11-15T00:00:00+00:00", False),
        ProfileCandidate("second", "Second", "u/second", "2026-11-14T00:00:00+00:00", False),
    ]
    assert [item.note_id for item in rank_candidates(candidates)] == ["new", "second"]
