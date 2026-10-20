"""Authenticate MCP clients connecting over the network.

Token storage lives in revit_mcp/remote_token.py so the Revit ribbon buttons
(IronPython) can show, rotate and revoke the same token.
"""
import hmac

from revit_mcp.remote_token import (  # noqa: F401  (re-exported for main.py)
    RemoteAccessError,
    create_remote_token,
    get_remote_token,
    remote_token_path,
    revoke_remote_token,
)

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def validate_remote_request(headers):
    """Reject browser-originated requests and check the remote token."""
    normalized = {str(k).lower(): str(v) for k, v in headers.items()}
    if "origin" in normalized:
        return False
    try:
        expected = "Bearer " + get_remote_token()
    except RemoteAccessError:
        return False
    actual = normalized.get("authorization", "")
    try:
        return hmac.compare_digest(actual.encode("utf-8"), expected.encode("utf-8"))
    except UnicodeError:
        return False
