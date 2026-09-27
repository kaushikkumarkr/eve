from urllib.error import HTTPError

from agency_mcp.errors import safe_error_summary


def test_http_error_summary_exposes_status_but_never_url_or_body():
    error = HTTPError(
        "https://api.example.test/ad_account?token=url-secret",
        403,
        "account denied; body-secret",
        hdrs=None,
        fp=None,
    )

    summary = safe_error_summary(error)

    assert "HTTP 403" in summary
    assert "url-secret" not in summary
    assert "body-secret" not in summary
    assert "api.example.test" not in summary
