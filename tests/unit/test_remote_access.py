# -*- coding: utf-8 -*-
"""Network (remote) mode: separate token, Host allowlist, CLI validation.

DPAPI only exists on Windows, so the stored token is monkeypatched here; the
DPAPI round trip is covered on Windows by test_remote_token_dpapi_round_trip.
"""
import os

import httpx
import pytest

import main
from tools import remote_access

TOKEN = "a" * 64
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-11-25", "capabilities": {},
    "clientInfo": {"name": "remote-test", "version": "1.0"}}}
ACCEPT = "application/json, text/event-stream"


@pytest.fixture
def remote_token(monkeypatch):
    monkeypatch.setattr(remote_access, "get_remote_token", lambda: TOKEN)
    return TOKEN


def test_validate_remote_request(remote_token):
    assert remote_access.validate_remote_request({"Authorization": "Bearer " + TOKEN})
    assert not remote_access.validate_remote_request({})
    assert not remote_access.validate_remote_request({"Authorization": "Bearer wrong"})
    assert not remote_access.validate_remote_request(
        {"Authorization": "Bearer " + TOKEN, "Origin": "http://evil.example"})


def test_validate_remote_request_without_token(monkeypatch):
    def missing():
        raise remote_access.RemoteAccessError("none")
    monkeypatch.setattr(remote_access, "get_remote_token", missing)
    assert not remote_access.validate_remote_request({"Authorization": "Bearer " + TOKEN})


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI required")
def test_remote_token_dpapi_round_trip(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    token = remote_access.create_remote_token()
    assert token.encode() not in open(remote_access.remote_token_path(), "rb").read()
    assert remote_access.get_remote_token() == token
    assert remote_access.create_remote_token() != token
    remote_access.revoke_remote_token()
    with pytest.raises(remote_access.RemoteAccessError):
        remote_access.get_remote_token()


class TestParseArgs:
    def test_defaults_to_local_stdio(self):
        args = main.parse_args([])
        assert (args.transport, args.host, args.remote) == ("stdio", "127.0.0.1", False)

    def test_network_host_enables_remote_mode(self):
        args = main.parse_args(["--http", "--host", "0.0.0.0", "--port", "9000"])
        assert (args.transport, args.remote, args.port) == ("streamable-http", True, 9000)

    def test_network_host_requires_http_transport(self):
        with pytest.raises(SystemExit):
            main.parse_args(["--host", "0.0.0.0"])

    def test_tls_files_must_come_together(self):
        with pytest.raises(SystemExit):
            main.parse_args(["--http", "--host", "0.0.0.0", "--ssl-certfile", "c.pem"])

    def test_remote_token_command(self):
        assert main.parse_args(["--remote-token", "new"]).remote_token == "new"


def test_network_host_names_for_specific_ip():
    assert main.network_host_names("10.0.0.12", ["revit-pc01"]) == ["10.0.0.12", "revit-pc01"]


def test_network_host_names_for_all_interfaces_skip_loopback():
    names = main.network_host_names("0.0.0.0", ["revit-pc01", "localhost"])
    assert "revit-pc01" in names
    assert not set(names) & remote_access.LOOPBACK_HOSTS


@pytest.fixture
def fresh_server(monkeypatch):
    from mcp.server.fastmcp import FastMCP
    from tools import register_tools
    server = FastMCP("Revit MCP Server", host="127.0.0.1", port=8000,
                     stateless_http=True, json_response=True)
    register_tools(server, main.revit_get, main.revit_post, main.revit_image)
    monkeypatch.setattr(main, "mcp", server)
    return server


async def test_remote_http_app_accepts_lan_host_with_remote_token(fresh_server, remote_token):
    main.allow_network_hosts(main.network_host_names("10.0.0.12"))
    app = main.build_http_app("streamable-http", remote=True)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://10.0.0.12:8000") as client:
            assert (await client.post("/mcp")).status_code == 401
            bad = {"Authorization": "Bearer " + "b" * 64, "Accept": ACCEPT}
            assert (await client.post("/mcp", headers=bad, json=INIT)).status_code == 401
            good = {"Authorization": "Bearer " + TOKEN, "Accept": ACCEPT}
            response = await client.post("/mcp", headers=good, json=INIT)
            assert response.status_code == 200
            assert response.json()["result"]["protocolVersion"] == "2025-11-25"


async def test_remote_http_app_rejects_unlisted_host(fresh_server, remote_token):
    app = main.build_http_app("streamable-http", remote=True)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://10.0.0.99:8000") as client:
            good = {"Authorization": "Bearer " + TOKEN, "Accept": ACCEPT}
            response = await client.post("/mcp", headers=good, json=INIT)
            assert response.status_code == 421  # DNS-rebinding protection


def test_revit_bridge_stays_loopback():
    assert main.REVIT_HOST in remote_access.LOOPBACK_HOSTS
