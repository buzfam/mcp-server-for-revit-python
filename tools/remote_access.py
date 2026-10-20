"""Bearer token for MCP clients connecting over the network.

Separate from the Revit pairing secret: the pairing secret never leaves this
PC, while this token is copied to remote MCP clients and can be rotated or
revoked without touching local pairing. Stored with Windows DPAPI CurrentUser.
"""
import binascii
import hmac
import os

from revit_mcp.pairing import PairingError, _dpapi

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class RemoteAccessError(RuntimeError):
    pass


def remote_token_path():
    root = os.environ.get("LOCALAPPDATA")
    if not root:
        raise RemoteAccessError("Windows LOCALAPPDATA is unavailable.")
    return os.path.join(root, "RevitMCPPython", "remote.dpapi")


def create_remote_token():
    """Create (or rotate) the remote access token and return it."""
    token = binascii.hexlify(os.urandom(32)).decode("ascii")
    path = remote_token_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as stream:
        stream.write(_dpapi(token.encode("ascii"), True))
    return token


def revoke_remote_token():
    path = remote_token_path()
    if os.path.isfile(path):
        os.remove(path)


def get_remote_token():
    try:
        with open(remote_token_path(), "rb") as stream:
            token = _dpapi(stream.read(), False).decode("ascii")
        if len(token) != 64 or any(c not in "0123456789abcdef" for c in token):
            raise ValueError("Invalid remote token data")
        return token
    except (OSError, ValueError, PairingError):
        raise RemoteAccessError(
            "No remote access token. Run: uv run main.py --remote-token new")


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
