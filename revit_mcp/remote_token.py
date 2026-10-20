# -*- coding: utf-8 -*-
"""Remote access token storage, shared by CPython (main.py) and IronPython
(ribbon buttons).

Separate from the pairing secret: the pairing secret never leaves this PC,
while this token is copied to remote MCP clients and can be rotated or revoked
without touching local pairing. Encrypted with Windows DPAPI CurrentUser.
"""
import binascii
import os

from .pairing import PairingError, _dpapi


class RemoteAccessError(RuntimeError):
    pass


def remote_token_path():
    root = os.environ.get("LOCALAPPDATA")
    if not root:
        raise RemoteAccessError("Windows LOCALAPPDATA is unavailable.")
    return os.path.join(root, "RevitMCPPython", "remote.dpapi")


def create_remote_token():
    """Create (or rotate) the remote access token and return it."""
    token = binascii.hexlify(os.urandom(32))
    encrypted = _dpapi(token, True)
    path = remote_token_path()
    directory = os.path.dirname(path)
    if not os.path.isdir(directory):
        os.makedirs(directory)
    with open(path, "wb") as stream:
        stream.write(encrypted)
    return token.decode("ascii")


def revoke_remote_token():
    path = remote_token_path()
    if os.path.isfile(path):
        os.remove(path)


def has_remote_token():
    try:
        return os.path.isfile(remote_token_path())
    except RemoteAccessError:
        return False


def get_remote_token():
    try:
        with open(remote_token_path(), "rb") as stream:
            token = _dpapi(stream.read(), False).decode("ascii")
        if len(token) != 64 or any(c not in "0123456789abcdef" for c in token):
            raise ValueError("Invalid remote token data")
        return token
    except (OSError, IOError, ValueError, PairingError):
        raise RemoteAccessError(
            "No remote access token. Create one in Revit: MCP > Remote > Novo token.")
