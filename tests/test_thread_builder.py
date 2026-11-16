from pathlib import Path

from app.models import Note, Translation
from app.thread_builder import build_thread


def test_more_than_four_images_becomes_thread() -> None:
    note = Note("000000000000000000000001", "Author", "Title", "Body", "image", None)
    images = [Path(f"data/images/{index}.jpg") for index in range(5)]
    posts = build_thread(note, Translation("Title", "Body"), images, "https://xhslink.cn/o/example")
    assert len(posts) == 2
    assert len(posts[0].image_paths) == 4
    assert len(posts[1].image_paths) == 1
    assert all(len(post.text) <= 280 for post in posts)


def test_long_text_is_split_without_source_attribution() -> None:
    note = Note("000000000000000000000001", "Author", "Title", "Body", "image", None)
    posts = build_thread(
        note,
        Translation("Title", "word " * 200),
        [],
        "https://xhslink.cn/o/example",
        max_chars=100,
    )
    assert len(posts) > 2
    assert all(len(post.text) <= 100 for post in posts)
    assert "Source:" not in posts[0].text
    assert "xhslink.cn" not in posts[0].text


def test_configured_english_tags_are_added_to_first_post() -> None:
    note = Note("000000000000000000000001", "Author", "Title", "Body", "video", None)
    posts = build_thread(
        note,
        Translation("Title", "Body"),
        [Path("data/video.mp4")],
        "https://xhslink.cn/o/example",
        tags=("#Fashion", "StyleInspiration", "#Fashion"),
    )
    assert "#Fashion #StyleInspiration" in posts[0].text


def test_tags_are_stable_filtered_and_limited() -> None:
    note = Note("stable-note-id", "Author", "Title", "Body", "video", None)
    tags = (
        "#Xiaohongshu",
        "#PortraitPhotography",
        "#BeautyPortrait",
        "#ModelPhotography",
        "#WomenInPhotography",
        "#FashionPortrait",
    )
    first = build_thread(
        note,
        Translation("Title", "Body"),
        [Path("data/video.mp4")],
        "https://xhslink.cn/o/example",
        tags=tags,
        tag_count=3,
    )[0].text
    retry = build_thread(
        note,
        Translation("Title", "Body"),
        [Path("data/video.mp4")],
        "https://xhslink.cn/o/example",
        tags=tags,
        tag_count=3,
    )[0].text
    assert first == retry
    assert "#Xiaohongshu" not in first
    assert first.count("#") == 3
    assert "Source:" not in first
