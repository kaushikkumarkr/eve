"""Return a short-lived Entra bearer token for Codex's Eve MCP connection.

Usage: python3 codex_azure_mcp_headers.py <tenant-id> <fully-qualified-scope>

The token is written only to stdout for Codex's http_headers_helper. Never log
or persist its output.
"""

from __future__ import annotations

import json
import subprocess
import sys


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: codex_azure_mcp_headers.py <tenant-id> <scope>", file=sys.stderr)
        return 2

    tenant_id, scope = sys.argv[1:]
    result = subprocess.run(
        [
            "az",
            "account",
            "get-access-token",
            "--tenant",
            tenant_id,
            "--scope",
            scope,
            "--only-show-errors",
            "--query",
            "accessToken",
            "-o",
            "tsv",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    token = result.stdout.strip()
    if result.returncode or not token or "\n" in token:
        print(
            "Could not obtain an Eve Entra token. Sign in with the invited tenant account "
            "and grant the Eve operator scope.",
            file=sys.stderr,
        )
        return 1

    print(json.dumps({"Authorization": f"Bearer {token}"}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
