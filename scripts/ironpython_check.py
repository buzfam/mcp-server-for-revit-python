# -*- coding: utf-8 -*-
"""IronPython 2.7 compatibility check for code that runs inside Revit.

Run with ipy.exe (see .github/workflows/ironpython.yml), not CPython:
    ipy scripts/ironpython_check.py

1. Compiles every file under revit_mcp/, MCP.tab/ and startup.py.
2. Imports the modules that do not need Revit (pyrevit is stubbed) and runs
   their pure helpers under real Python 2 semantics (unicode/basestring/long).
3. Round-trips the remote token through Windows DPAPI via .NET, the same path
   the ribbon buttons use.
Exits 1 on any failure.
"""
import os
import shutil
import sys
import tempfile
import traceback
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
failures = []


def check(name, function):
    try:
        function()
        print("ok    " + name)
    except Exception:
        failures.append(name)
        print("FAIL  " + name)
        traceback.print_exc()


def ironpython_files():
    yield os.path.join(ROOT, "startup.py")
    for folder in ("revit_mcp", "MCP.tab"):
        for directory, _, files in os.walk(os.path.join(ROOT, folder)):
            for name in sorted(files):
                if name.endswith(".py"):
                    yield os.path.join(directory, name)


def compile_all():
    count = 0
    for path in ironpython_files():
        with open(path) as stream:
            source = stream.read()
        try:
            compile(source, path, "exec")
        except SyntaxError:
            failures.append("compile " + os.path.relpath(path, ROOT))
            print("FAIL  compile " + os.path.relpath(path, ROOT))
            traceback.print_exc()
        count += 1
    print("ok    compiled {} files".format(count))


def stub_pyrevit():
    class Anything(object):
        def __getattr__(self, name):
            return Anything()

        def __call__(self, *args, **kwargs):
            return Anything()

    pyrevit = types.ModuleType("pyrevit")
    pyrevit.DB = Anything()
    pyrevit.routes = Anything()
    pyrevit.forms = Anything()
    pyrevit.script = Anything()
    sys.modules["pyrevit"] = pyrevit


def remote_server_helpers():
    from revit_mcp import remote_server as server
    assert server.server_arguments(8000) == "run --frozen main.py --http --host 0.0.0.0 --port 8000"
    assert server.client_name(u"ARQ-PC 01") == "revit-arq-pc01"
    command = server.connection_command("revit-pc01", "10.0.0.5", 8000, "a" * 64)
    assert "http://10.0.0.5:8000/mcp" in command
    found = server.find_uv({"PATH": r"C:\x;C:\uv"}, isfile=lambda p: p == r"C:\uv\uv.exe")
    assert found == r"C:\uv\uv.exe", found
    folder = tempfile.mkdtemp()
    old = os.environ.get("LOCALAPPDATA")
    os.environ["LOCALAPPDATA"] = folder
    try:
        assert server.read_state() is None
        server.write_state({"pid": 1, "port": 8000})
        assert server.read_state()["port"] == 8000
        server.clear_state()
        assert server.read_state() is None
    finally:
        if old is not None:
            os.environ["LOCALAPPDATA"] = old
        shutil.rmtree(folder)


def remote_token_dpapi():
    from revit_mcp import remote_token
    folder = tempfile.mkdtemp()
    old = os.environ.get("LOCALAPPDATA")
    os.environ["LOCALAPPDATA"] = folder
    try:
        assert not remote_token.has_remote_token()
        token = remote_token.create_remote_token()
        assert len(token) == 64
        with open(remote_token.remote_token_path(), "rb") as stream:
            assert token not in stream.read()
        assert remote_token.get_remote_token() == token
        assert remote_token.create_remote_token() != token
        remote_token.revoke_remote_token()
        assert not remote_token.has_remote_token()
    finally:
        if old is not None:
            os.environ["LOCALAPPDATA"] = old
        shutil.rmtree(folder)


def parameter_helpers():
    from revit_mcp import parameters
    assert parameters.parse_bool(u"n\u00e3o") is False
    assert parameters.parse_bool("Sim") is True
    assert parameters.parse_bool(long(1)) is True
    assert parameters.parse_bool(2) is None
    assert parameters.validate_change({"element_id": 1, "parameter": u"Coment\u00e1rios", "value": 0}) is None
    assert parameters.validate_change({"element_id": "x", "parameter": "Mark", "value": 1})


def health_helpers():
    from revit_mcp import health
    assert health.warning_flag(500) == "high"
    report = {"warnings": {"flag": "medium"}, "imported_cad": {"count": 1},
              "views": {"placeable": 10, "not_on_sheets": 9, "unused_templates": 0},
              "revit_links": []}
    assert health.health_flags(report) == ["warnings_medium", "imported_cad",
                                           "many_views_not_on_sheets"]


def body_token_pairing():
    import json
    from revit_mcp import pairing, security

    class Request(object):
        def __init__(self, data):
            self.data = data
            self.headers = {}

    original = pairing.get_token
    pairing.get_token = lambda: u"a" * 64
    try:
        # pyRevit hands over json.loads output: unicode keys and values.
        request = Request(json.loads(json.dumps(pairing.authorization_body({"dry_run": True}))))
        token = security._pop_body_token(request)
        assert request.data == {u"dry_run": True}, request.data
        assert pairing.validate_request(request.headers, token)
        assert security._pop_body_token(request) == token
        assert not pairing.validate_request({}, u"b" * 64)
        assert not pairing.validate_request({u"Origin": u"null"}, token)
    finally:
        pairing.get_token = original


def utils_unicode():
    from revit_mcp.utils import normalize_string
    assert normalize_string(u"T\u00e9rreo ") == u"T\u00e9rreo"
    assert isinstance(normalize_string("abc"), unicode)
    from revit_mcp.utils import repair_utf8, category_matches
    try:
        from urllib import unquote
    except ImportError:
        from urllib.parse import unquote
    assert repair_utf8(unquote(u"N%C3%ADvel%201")) == u"N\u00edvel 1"
    assert repair_utf8(u"N\u00edvel 1") == u"N\u00edvel 1"
    assert category_matches(u"Walls", u"Paredes", "OST_Walls")
    assert category_matches(u"Generic Models", u"Modelos gen\u00e9ricos", "OST_GenericModel")


print("IronPython {}".format(sys.version))
compile_all()
stub_pyrevit()
check("remote_server helpers", remote_server_helpers)
check("remote_token DPAPI round trip", remote_token_dpapi)
check("parameters helpers", parameter_helpers)
check("health helpers", health_helpers)
check("body token pairing", body_token_pairing)
check("utils unicode", utils_unicode)

if failures:
    print("\n{} failure(s): {}".format(len(failures), ", ".join(failures)))
    sys.exit(1)
print("\nAll IronPython checks passed.")
