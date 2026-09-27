"""Admin CLI transport to the private shared-deployment secret-intake route."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


def validate_remote_secret_destination(url: str | None, scope: str | None) -> None:
    if not url or not scope:
        raise RuntimeError("Remote secret intake URL and Entra scope are required")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise RuntimeError("Remote secret intake URL must be HTTPS without userinfo or fragment")


def remote_secret_request(
    *,
    url: str | None,
    scope: str | None,
    method: str,
    payload: dict[str, str] | None = None,
    query: dict[str, str] | None = None,
) -> dict:
    validate_remote_secret_destination(url, scope)
    assert url is not None and scope is not None
    if method not in {"GET", "POST", "DELETE"}:
        raise RuntimeError("Unsupported remote secret operation")
    request_url = url
    if query:
        separator = "&" if urlsplit(url).query else "?"
        request_url = f"{url}{separator}{urlencode(query)}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None

    try:
        from azure.identity import DefaultAzureCredential

        credential = DefaultAzureCredential()
    except ImportError as exc:
        raise RuntimeError("Install Eve's Azure extra for shared secret intake") from exc
    try:
        access_token = credential.get_token(scope).token
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(request_url, data=data, headers=headers, method=method)
        with urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
        if not isinstance(result, dict):
            raise RuntimeError("Remote secret intake returned an unexpected response")
        return result
    except HTTPError as exc:
        raise RuntimeError(f"Remote secret intake failed (HTTP {exc.code}); details withheld") from None
    except (URLError, TimeoutError):
        raise RuntimeError("Remote secret intake connection failed; details withheld") from None
    except RuntimeError:
        raise
    except Exception:
        raise RuntimeError("Remote secret intake failed; details withheld") from None
    finally:
        credential.close()
