"""Safe error summaries for persisted records and agent-visible interfaces."""

from urllib.error import HTTPError


def safe_error_summary(exc: Exception) -> str:
    """Expose safe transport metadata without echoing provider/user content."""
    if isinstance(exc, HTTPError):
        # Numeric HTTP status is useful for diagnosing auth, entitlement, and
        # throttling failures. Never include the URL, headers, or response body.
        return f"Ads API request failed with HTTP {exc.code}; response details withheld."
    error_type = type(exc).__name__
    if not error_type.isidentifier():
        error_type = "OperationError"
    return f"Operation failed ({error_type}); details withheld to protect credentials and client data."
