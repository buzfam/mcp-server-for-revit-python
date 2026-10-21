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
        data = {u"changes": [{u"element_id": 2745, u"parameter": u"Coment\u00e1rios"}]}
        assert u"Coment\u00e1rios" in security.preview_text(data)
        assert json.loads(security.preview_text(data)) == data
        try:
            json.dumps(data, indent=2, ensure_ascii=True)
            print("info  json.dumps with accents works on this IronPython")
        except Exception as error:
            print("info  json.dumps with accents fails here: {!r}".format(error))
    finally:
        pairing.get_token = original


def approval_helpers():
    from revit_mcp import approval
    assert approval.decision(approval.ALLOW_SESSION) == (True, True)
    assert approval.decision(None) == (False, False)
    assert not approval.is_auto_approved()
    approval.set_auto_approved(True)  # real .NET AppDomain under ipy
    assert approval.is_auto_approved()
    approval.set_auto_approved(False)
    assert not approval.is_auto_approved()


def self_update_helpers():
    from revit_mcp import self_update
    assert self_update.commit_lines([u"feat: \u00e1\nbody"]) == u"\u2022 feat: \u00e1"
    assert "1234567" in self_update.result_message("1234567890", "abcdefabc", True, True, "")
    commit = self_update.installed_commit(ROOT)
    assert commit is None or len(commit) == 40, commit
    assert self_update.server_is_stale({"pid": 1}, "abc")


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


def sheet_helpers():
    from revit_mcp import sheets
    assert sheets.name_key(u" Planta  TÉRREO ") == u"planta térreo"
    assert sheets.name_key(None) == u""
    assert sheets.split_title_block(u"Carimbo A1 : Padrão") == (u"Carimbo A1", u"Padrão")
    assert sheets.split_title_block(u"Padrão") == (None, u"Padrão")
    assert abs(sheets.meters_to_feet(0.3048) - 1.0) < 1e-9
    assert sheets.is_number(long(2)) and not sheets.is_number(True)
    assert sheets.duplicate_indexes([u"a101", u"a102", u"a101", None, None]) == [2]
    assert sheets.validate_sheet_item({u"number": u"A101", u"name": u"Planta Térreo"}) is None
    assert sheets.validate_sheet_item({u"number": u"", u"name": u"x"})
    assert sheets.validate_placement({u"sheet_number": u"A101", u"view": u"Térreo"}) is None
    assert sheets.validate_placement({u"sheet_number": u"A101", u"view_id": long(5),
                                      u"x": 0.4, u"y": 1}) is None
    assert sheets.validate_placement({u"sheet_number": u"A101", u"view": u"a", u"view_id": 5})
    assert sheets.validate_placement({u"sheet_number": u"A101", u"view": u"a", u"x": 1})
    assert sheets.validate_view_ref(long(12345)) is None
    assert sheets.validate_view_ref(u"Térreo") is None
    assert sheets.validate_view_ref(True)


def annotation_export_helpers():
    from revit_mcp import annotation, exports
    assert annotation.tag_category_candidates("OST_Doors")[0] == "OST_DoorTags"
    assert annotation.tag_category_candidates(None) == ["OST_MultiCategoryTags"]
    assert annotation.tag_type_matches(u"etiqueta de porta: padrão", u"Etiqueta de porta", u"Padrão")
    assert not annotation.tag_type_matches(u"", u"A", u"B")
    assert annotation.bbox_centre((0, 0, 0), (2, 4, 6)) == (1.0, 2.0, 3.0)
    to_tag, skipped, truncated = annotation.plan_candidates([(1, None), (2, None), (3, None)], set([1]), True, 1)
    assert [i for i, _ in to_tag] == [2] and skipped == {"already_tagged": 1} and truncated
    csv = exports.csv_text([u"Nome", u"Área"], [[u"Sala, estar", 12.5], [u'Hall "A"', None], [u"Ação", long(3)]])
    assert isinstance(csv, unicode)
    assert csv == u'Nome,Área\n"Sala, estar",12.5\n"Hall ""A""",\nAção,3\n'
    assert exports.room_status(False, 0) == "unplaced"
    assert exports.room_status(True, 0) == "not_enclosed"
    assert exports.room_status(True, 1.5) == "placed"
    assert exports.rounded(100.0, exports.SQFT_TO_M2) == 9.29
    assert exports.header_row_index([[u"P1"], [u"Marca"]], [u"marca "]) == 1
    assert exports.parse_options({"format": "CSV", "limit": long(5)}) == (5, "csv", None)
    returned, total, counts, area, truncated = exports.summarize_rooms(
        [{"number": u"1", "level": u"Térreo", "status": "placed", "area_m2": 10.0}],
        u"térreo", True, 200)
    assert total == 1 and counts["placed"] == 1 and area == 10.0 and not truncated


def modeling_helpers():
    from revit_mcp import modeling
    assert abs(modeling.to_feet(0.3048) - 1.0) < 1e-9
    assert abs(modeling.sqft_to_sqm(100) - 9.290304) < 1e-9
    square = [[0, 0], [4, 0], [4, 2], [0, 2]]
    assert modeling.polygon_centroid(square) == (2.0, 1.0)
    assert modeling.point_in_polygon((1, 1), square)
    assert modeling.is_point([long(1), 2.5]) and not modeling.is_point([True, 0])
    segments = [("a", (0, 0), (5, 0)), ("b", (5, 0), (5, 5))]
    assert modeling.nearest_segment((2, 0.3), segments, 0.5)[0] == "a"
    assert modeling.nearest_segment((2, 2), segments, 0.5) is None
    assert modeling.same_name(u"Térreo ", u"térreo")
    entries = [(u"Porta Simples", u"90x210", "p90"), (u"Porta Simples", u"80x210", "p80")]
    assert modeling.match_symbol(entries, u"porta simples", None) == ("p80", None)
    assert modeling.validate_wall({"start": [0, 0], "end": [5, 0], "location_line": u"finish_interior"}) is None
    assert modeling.validate_room({"point": [1, 1], "name": u"Cozinha", "number": u"01"}) is None
    assert modeling.validate_room({"boundary": [[0, 0], [1, 0], [2, 0]]})
    assert modeling.validate_hosted({"family": u"Janela", "wall_id": long(5), "offset_m": 1}) is None
    assert modeling.wall_settings({}, {"top_level": u"Nível 2"})["top_level"] == u"Nível 2"


print("IronPython {}".format(sys.version))
compile_all()
stub_pyrevit()
check("remote_server helpers", remote_server_helpers)
check("remote_token DPAPI round trip", remote_token_dpapi)
check("parameters helpers", parameter_helpers)
check("health helpers", health_helpers)
check("body token pairing", body_token_pairing)
check("approval helpers", approval_helpers)
check("self_update helpers", self_update_helpers)
check("utils unicode", utils_unicode)
check("sheet helpers", sheet_helpers)
check("annotation/export helpers", annotation_export_helpers)
check("modeling helpers", modeling_helpers)

if failures:
    print("\n{} failure(s): {}".format(len(failures), ", ".join(failures)))
    sys.exit(1)
print("\nAll IronPython checks passed.")
