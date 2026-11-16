import asyncio

from app.models import Note
from app.translate import FakeTranslator, _clean_provider_text, parse_translation


def test_translation_parser_keeps_sections() -> None:
    result = parse_translation("TITLE:\nA title\nBODY:\nA body")
    assert result.title == "A title"
    assert result.body == "A body"


def test_fake_translator_is_offline_only() -> None:
    note = Note("1", "A", "标题", "正文", "image", None)
    result = asyncio.run(FakeTranslator().translate(note))
    assert result.title.endswith("[EN]")


def test_provider_markup_is_removed_without_changing_text() -> None:
    assert _clean_provider_text("<g id=\"1\">Hello</g> &amp; world") == "Hello & world"
