from pathlib import Path

from app.storage import NoteStore


def test_store_preserves_publish_progress(tmp_path: Path) -> None:
    store = NoteStore(tmp_path / "state.sqlite3")
    store.upsert_discovered("note-1", "https://example/note")
    store.set_status("note-1", "publishing", tweet_ids=["100"], media_ids={"0": ["200"]})
    assert store.get_publish_state("note-1") == (["100"], {"0": ["200"]})
    assert store.get("note-1")["tweet_id"] == "100"


def test_test_latest_state_does_not_skip_first_baseline(tmp_path: Path) -> None:
    store = NoteStore(tmp_path / "state.sqlite3")
    store.upsert_discovered("note-1", "https://example/note")
    assert store.baseline_initialized() is False
    store.mark_baseline(["note-1"])
    assert store.baseline_initialized() is True
