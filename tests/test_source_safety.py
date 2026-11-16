from app.url_utils import public_source_url


def test_source_url_drops_access_tokens_before_persistence() -> None:
    safe = public_source_url(
        "https://www.rednote.com/discovery/item/abc?xsec_token=secret&xsec_source=app_share&foo=bar"
    )
    assert "xsec_token" not in safe
    assert "secret" not in safe
    assert "foo=bar" in safe
