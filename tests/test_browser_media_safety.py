from app.browser_xhs import _is_avatar_url


def test_avatar_urls_are_rejected() -> None:
    assert _is_avatar_url("https://cdn.example/avatar/user.webp")
    assert _is_avatar_url("https://cdn.example/profile-avatar/user.jpg")


def test_note_media_urls_are_not_rejected() -> None:
    assert not _is_avatar_url("https://cdn.example/sns-image/note-01.webp")
