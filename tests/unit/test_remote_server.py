# -*- coding: utf-8 -*-
"""Pure helpers behind the Revit ribbon's Remote button (no .NET needed)."""
import ntpath

import pytest

from revit_mcp import remote_server as server


@pytest.fixture
def windows_paths(monkeypatch):
    # The helpers build Windows paths; use ntpath so tests pass on any OS.
    monkeypatch.setattr(server.os, "path", ntpath)
    monkeypatch.setattr(server.os, "pathsep", ";")


def test_find_uv_prefers_path(windows_paths):
    env = {"PATH": r"C:\tools;C:\uvdir", "USERPROFILE": r"C:\Users\ana"}
    found = server.find_uv(env, isfile=lambda p: p == r"C:\uvdir\uv.exe")
    assert found == r"C:\uvdir\uv.exe"


def test_find_uv_falls_back_to_installer_folder(windows_paths):
    env = {"PATH": r"C:\Windows", "USERPROFILE": r"C:\Users\ana"}
    target = r"C:\Users\ana\.local\bin\uv.exe"
    assert server.find_uv(env, isfile=lambda p: p == target) == target


def test_find_uv_missing(windows_paths):
    assert server.find_uv({"PATH": r"C:\Windows"}, isfile=lambda p: False) is None


def test_server_arguments_listen_on_network_with_port():
    assert server.server_arguments(8000) == \
        "run --frozen main.py --http --host 0.0.0.0 --port 8000"


def test_cmd_arguments_quote_paths_and_redirect_output(windows_paths):
    line = server.cmd_arguments(r"C:\Users\ana b\.local\bin\uv.exe", 8000,
                                r"C:\Users\ana b\AppData\Local\RevitMCPPython\remote-server.log")
    assert line.startswith('/c ""C:\\Users\\ana b\\.local\\bin\\uv.exe" run --frozen')
    assert line.endswith('remote-server.log" 2>&1"')


def test_connection_command():
    command = server.connection_command("revit-pc01", "192.168.0.20", 8000, "a" * 64)
    assert command == ('claude mcp add --transport http revit-pc01 '
                       'http://192.168.0.20:8000/mcp --header "Authorization: Bearer '
                       + "a" * 64 + '"')


def test_client_name_is_safe_for_cli():
    assert server.client_name("ARQ-PC 01_x") == "revit-arq-pc01x"


def test_state_round_trip(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert server.read_state() is None
    server.write_state({"pid": 42, "port": 8000})
    assert server.read_state() == {"pid": 42, "port": 8000}
    server.clear_state()
    assert server.read_state() is None


def test_ready_detection():
    assert server.log_shows_ready("INFO:     Uvicorn running on http://0.0.0.0:8000")
    assert not server.log_shows_ready("Resolved 40 packages")
