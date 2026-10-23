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
    # History lives in a .NET List[String] in the AppDomain under ipy.
    approval.record(u"Create walls", approval.outcome_text(approval.ALLOW),
                    approval.request_summary({u"walls": [1, 2], u"nome": u"Cozinha"}))
    assert u"permitido  Create walls  nome=Cozinha, walls=2 itens" in approval.history()[0]


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


def audit_helpers():
    from revit_mcp import audit
    options, error = audit.parse_request({"sections": [u"families", u"views"], "limit": long(5),
                                          "naming": {u"views": u"(PB|CT) - .+"}})
    assert error is None and options["sections"] == ["views", "families"] and options["limit"] == 5
    assert "worksets" in audit.parse_request({"sections": [u"worksets"]})[1]
    assert "invalid regex" in audit.parse_request({"naming": {u"views": u"([A"}})[1]
    pattern, regex = options["naming"][u"views"]
    result = audit.naming_check([(1, u"PB - Térreo"), (2, u"Térreo"), (3, None)], pattern, regex, 1)
    assert result["violations_count"] == 2 and result["violations"] == [{"id": 2, "name": u"Térreo"}]
    assert result["truncated"]
    items = audit.unused_first([{"name": u"b", "unused": False}, {"name": u"Área", "unused": True}])
    assert items[0]["name"] == u"Área"
    summary = audit.summarize(items, 1, rule="x")
    assert summary["count"] == 2 and summary["unused_count"] == 1 and summary["truncated"]
    assert audit.summarize([], 5, tracks_unused=False)["unused_count"] is None
    assert audit.feet_to_m(10.0) == 3.048 and audit.rgb_hex(255, 0, long(128)) == "#FF0080"
    entries = audit.checklist(["views"], True)
    assert len(entries) == 19 and entries[15]["sections"] == ["naming"]


def sheet_check_helpers():
    from revit_mcp import sheet_checks as checks
    rule = {u"name": u"Planta de layout", u"match": {u"number_prefix": u"A-1", u"name_regex": u"layout"},
            u"required_views": [{u"view_type": u"floorplan", u"name_regex": u"layout", u"min": long(1)}],
            u"required_schedules": [u"esquadrias"], u"allowed_scales": [long(50)],
            u"title_block_params": [u"Desenhado por"], u"require_revision": True,
            u"manual": [u"cotas conferidas"]}
    assert checks.validate_rule(rule) is None
    assert u"invalid regex" in checks.validate_rule({u"match": {u"name_regex": u"("}})
    assert checks.canonical_view_type(u"floorplan") == u"FloorPlan"
    assert checks.rule_matches(rule, u"a-101", u"Planta de Layout Térreo")
    info = {"views": [{"name": u"Layout Térreo", "type": "FloorPlan", "scale": long(75), "empty": True}],
            "schedules": [u"Tabela de Esquadrias"], "has_title_block": True,
            "params": {u"Desenhado por": "empty"}, "revision": None}
    problems, manual, status = checks.evaluate_sheet(info, rule, True)
    found = dict((p["check"], p["detail"]) for p in problems)
    assert status == "missing" and manual == [u"cotas conferidas"]
    assert found["scale"] == u"vista 'Layout Térreo' em 1:75 (permitidas: 1:50)"
    assert found["title_block_param"] == u"parâmetro 'Desenhado por' vazio"


def material_check_helpers():
    from revit_mcp import material_checks as m
    assert m.strip_accents(u"POLTRONA_AÇO_PÉ_01") == u"POLTRONA_ACO_PE_01"
    regex, error = m.compile_pattern(None)
    assert error is None
    assert m.name_ok(u"POLTRONA_MADEIRA_PÉ_03", regex)
    assert not m.name_ok(u"Madeira", regex)
    groups = m.duplicate_groups([u"Madeira Carvalho", u"MADEIRA_CARVALHO",
                                 u"TECIDO_SOFA_ASSENTO_01", u"TECIDO_SOFA_ASSENTO_02", u"Vidro"])
    kinds = dict((g["key"], g["kind"]) for g in groups)
    assert kinds == {u"madeiracarvalho": "duplicate", u"tecidosofaassento": "similar"}, kinds


def quantity_helpers():
    from revit_mcp import quantities as q
    rules, error = q.parse_rules({u"deduct_openings": u"ALL", u"rounding": long(1)})
    assert error is None and rules["deduct_openings"] == "all" and rules["rounding"] == 1, error
    rules, _ = q.parse_rules(None)
    assert not q.opening_deducted(2.0000000001, rules) and q.opening_deducted(2.4, rules)
    walls = q.net_wall_area(30.0, [1.68, 2.4, None], rules)
    assert abs(walls["net"] - 27.6) < 1e-9 and walls["kept_count"] == 2
    assert q.skirting_length(14.0, [0.8], True) == (13.2, 0.8)
    texts = q.rule_texts(rules)
    assert isinstance(texts["paredes"], unicode) and u"2.00 m²" in texts["paredes"]
    line = q.make_line("pisos", u"Cerâmica", "area", "m2", 10.004, texts["piso"],
                       q.parse_rules({"waste_percent": 10})[0])
    assert line["quantity"] == 10.0 and line["quantity_with_waste"] == 11.0


def review_fix_helpers():
    from revit_mcp import transactions, modeling, sheets, exports, annotation
    assert transactions.add_unique([u"a"], u"a") == [u"a"]
    assert transactions.add_unique([u"a"], u"á") == [u"a", u"á"]
    assert modeling._failure_collector is transactions.failure_collector
    assert modeling.keys_within((2, 0.2), [("a", (0, 0), (5, 0)), ("b", (0, 0), (5, 0)),
                                           ("c", (0, 3), (5, 3))], 0.2 + 1e-6) == ["a", "b"]
    assert modeling.validate_hosted({"family": u"Porta", "wall_id": 5.0, "offset_m": 1}) \
        == "wall_id must be an integer"
    assert modeling.validate_hosted({"family": u"Porta", "wall_id": long(5), "offset_m": 1}) is None
    assert sheets.sheet_anchor(0, 0, 4, 2, "centre") == (2.0, 1.0)
    assert sheets.sheet_anchor(0, 0, 4, 2, "top_left", 0.1) == (0.1, 1.9)
    assert exports.visible_columns([False, True, False], 3) == [0, 2]
    assert exports.visible_columns([False, True, False], 2) == [0, 1]

    class V(object):
        def __init__(self, view_type):
            self.ViewType = view_type
    plan, ceiling = V("FloorPlan"), V("CeilingPlan")
    assert annotation.pick_named_view([plan], u"Térreo") == (plan, None)
    view, error = annotation.pick_named_view([plan, ceiling], u"Térreo")
    assert view is None and u"use view_id" in error


def renumber_helpers():
    from revit_mcp import renumber as rn
    assert sorted([u"A-10", u"A-2", u"a-1", u""], key=rn.natural_key) == [u"a-1", u"A-2", u"A-10", u""]
    parts, error = rn.parse_pattern(u"P-{room_number}-{seq:02}")
    assert error is None
    assert rn.format_pattern(parts, {"seq": 3, "room_number": u"101"}) == u"P-101-03"
    assert rn.parse_pattern(u"{room_name}")[1] is not None
    assert rn.parse_pattern(u"{seq}{")[1] is not None
    options, error = rn.validate_request({"target": u"Portas", "pattern": u"{level}-{seq}",
                                          "group_by": "level", "start": 101})
    assert error is None and options["kind"] == "doors" and options["order"] == "location"
    assert rn.validate_request({"target": "sheets", "order": "location"})[1] is not None
    assert rn.validate_request({"target": "sheets", "phase": u"Existente"})[1] is not None
    assert "'A-1'" in rn.find_conflicts([(1, u"A-1"), (2, u"a-1")], [], True)[0][0]["error"]
    records = [{"element_id": 1, "elevation": 0.0, "x": 5.0, "y": 0.0, "level": u"Térreo"},
               {"element_id": 2, "elevation": 0.0, "x": 0.0, "y": 0.2, "level": u"Térreo"},
               {"element_id": 3, "elevation": 3.0, "x": 0.0, "y": 0.0, "level": u"P1"}]
    planned = rn.plan_numbers(records, options)
    assert [(r["element_id"], v) for r, v in planned] == [
        (2, u"Térreo-101"), (1, u"Térreo-102"), (3, u"P1-101")]
    errors, warnings = rn.find_conflicts([(1, u"A-1"), (2, u"a-1")], [u"B"], True)
    assert len(errors) == 2 and not warnings
    assert rn.temp_value(7, set([u"mcp-tmp-7"])) == u"MCP-TMP-7-2"


def pdf_export_helpers():
    import datetime
    from collections import OrderedDict
    from revit_mcp import pdf_export as pdf
    values = {"project_number": u"P-07", "sheet_number": u"A-101", "revision": u"",
              "date": u"2026-10-21", "sheet_name": u"Planta: Térreo"}
    assert pdf.build_file_name(pdf.DEFAULT_PATTERN, values) == u"P-07_A-101_2026-10-21"
    assert pdf.build_file_name(u"{sheet_number} {sheet_name}", values) == u"A-101 Planta- Térreo"
    assert pdf.build_file_name(u"{revision}", {}) == u"Emissao"
    assert pdf.pattern_error(pdf.DEFAULT_PATTERN) is None
    assert pdf.pattern_error(u"{folha}_{date}")
    assert pdf.dedupe_names([u"A", u"a"]) == ([u"A", u"a_2"], [1])
    assert pdf.validate_folder(u"d:/Projetos/Emissão/") == (u"D:\\Projetos\\Emissão", None)
    assert pdf.validate_folder(u"\\\\nas\\x")[0] is None
    assert pdf.validate_folder(u"D:\\a\\..\\b")[0] is None
    assert pdf.validate_folder(u"C:\\Windows\\Temp")[0] is None
    assert pdf.validate_folder(u"D:\\PROJET~1")[0] is None
    assert pdf.validate_folder(u"D:\\Projetos\\nul.txt")[0] is None
    now = datetime.datetime(2026, 10, 21, 9, 5, 7)
    assert pdf.default_folder(u"C:\\Docs", u"P-07", now) == \
        u"C:\\Docs\\RevitMCP\\Emissoes\\P-07\\2026-10-21_090507"
    assert pdf.parse_issued_at(u"2026-10-21T09:05:07") == (now, None)
    assert pdf.parse_issued_at(u"21/10/2026")[1]
    assert pdf.backup_name(u"A.pdf", now) == u"A.pdf.bak-20261021_090507"
    assert pdf.paper_format_name(u"iso_a1", [u"Default", u"ISO_A1"]) == u"ISO_A1"
    assert pdf.approval_action(2, u"D:\\Emissão", 1) == \
        u"Export 2 PDF(s) to D:\\Emissão and REPLACE 1 existing file(s)"
    assert pdf.disk_folder_error(u"D:\\a\\b", lambda root: u"Fixed", lambda path: None) is None
    assert pdf.disk_folder_error(u"Z:\\a", lambda root: u"Network", lambda path: None)
    assert pdf.disk_folder_error(u"D:\\a", lambda root: u"Fixed",
                                 lambda path: u"Directory, ReparsePoint")
    assert u"\n" not in pdf.json_text({u"a": [1, u"é"]}, compact=True)
    assert pdf.common_revision([u"R01", u"R01"]) == u"R01"
    assert pdf.common_revision([u"R01", u""]) == u""
    record = OrderedDict([("nome", u"Árvore \"A\""), ("n", long(3)), ("ok", True), ("x", None)])
    text = pdf.json_text(record)
    text.encode("ascii")
    assert u"\\u00c1rvore \\\"A\\\"" in text and text.index(u"nome") < text.index(u"ok")
    assert pdf.validate_options({u"sheets": [u"A-101"], u"color": u"grayscale"}) is None
    assert pdf.validate_options({u"zoom_percent": 5})
    planned, renamed = pdf.plan_files(
        [{"sheet_number": u"A-1", "revision": u"R1"}, {"sheet_number": u"A-2", "revision": u"R1"}],
        pdf.DEFAULT_PATTERN, {"number": u"P-07"}, u"2026-10-21", True)
    assert planned == [(u"P-07_R1_2026-10-21", [0, 1])] and renamed == []


def phase_helpers():
    from revit_mcp import phases as ph
    order = [(long(10), u"Existente"), (long(11), u"Nova construção")]
    assert ph.resolve_phase(order, u"NOVA construção") == (1, None)
    assert ph.resolve_phase(order, long(10)) == (0, None)
    position, error = ph.resolve_phase(order, u"Demolição")
    assert position is None and isinstance(error, unicode) and u"Nova construção" in error
    assert u"comes before" in ph.order_error(1, 0, order) and ph.order_error(0, 0, order) is None
    assert ph.status_in_phase(0, 1, 1) == "demolished" and ph.status_in_phase(1, None, 1) == "new"
    assert ph.is_clear(u"Nenhum") and not ph.is_clear(u"Existente")
    assert ph.validate_change({u"element_id": long(5), u"demolished_phase": u"nenhum"}) is None
    assert "cannot be cleared" in ph.validate_change({"element_id": 5, "created_phase": u"none"})
    assert ph.validate_selector({u"category": u"Paredes", u"level": u"Térreo"}) is None
    assert ph.presentation_name(u"ShowOverriden") == "overridden"
    rows = ph.combination_rows({(0, 1): {"count": 2, "categories": {u"Paredes": 2}}}, order, True)
    assert rows[0]["demolished_phase"] == u"Nova construção" and rows[0]["categories"][0]["count"] == 2
    groups, truncated = ph.group_views([(u"Existente", None, u"Planta térreo")], 1)
    assert groups[0]["views"] == [u"Planta térreo"] and not truncated
    dupes = ph.duplicate_errors([{u"element_id": long(7), u"created_phase": u"A"},
                                 {u"element_id": long(7), u"demolished_phase": u"B"}])
    assert dupes == [{"index": 1, "error": "element_id 7 repeats change 0"}], dupes
    assert ph.expected_count_error(long(3)) is None and ph.expected_count_error(0)
    assert u"Schedule" in ph.view_kind_error(u"Schedule", False)
    assert ph.view_kind_error(u"FloorPlan", False) is None and ph.view_kind_error(u"FloorPlan", True)
    assert ph.preview_ids(list(range(60))) == list(range(50))


def audit_review_helpers():
    from revit_mcp import audit
    assert audit.purge_result([], 2) is None and audit.purge_result([], 0) == set()
    assert audit.purge_result([long(5)], 1) == set([5])


def sheet_check_review_helpers():
    from revit_mcp import sheet_checks as checks
    rule = {u"match": {u"number_prefix": u"A"}, u"allowed_scales": [long(50)]}
    info = {"views": [{"name": u"Perspectiva", "type": "ThreeD", "scale": long(1),
                       "perspective": True}], "has_title_block": True}
    assert checks.evaluate_sheet(info, rule, False)[0] == []
    assert "OST_ElevationMarks" in checks.DATUM_CATEGORIES


def material_check_review_helpers():
    from revit_mcp import material_checks as m
    assert m.merge_ids([long(1), long(2)], [long(2), long(3)]) == [1, 2, 3]
    assert m.piece_count({"solids": 2, "meshes": 1}, 2) == 5 and m.piece_count(None, 1) is None
    assert m.mesh_only({"solids": 0, "meshes": 2}) and not m.mesh_only({"solids": 1, "meshes": 2})


def quantity_review_helpers():
    from revit_mcp import quantities as q
    rules, error = q.parse_rules({u"deduct_openings": u"excess_over_threshold"})
    assert error is None
    assert abs(q.opening_deduction(3.5, rules) - 1.5) < 1e-9 and q.opening_deduction(1.68, rules) == 0.0
    assert u"excede 2.00 m²" in q.rule_texts(rules)["paredes"]
    phases = [(long(10), u"Existente"), (long(12), u"Construção nova")]
    assert q.resolve_phase(phases, None) == (1, None)
    assert q.resolve_phase(phases, u"construção NOVA") == (1, None)
    assert q.resolve_phase(phases, long(10)) == (0, None)
    assert u"Existente" in q.resolve_phase(phases, u"Futura")[1]
    assert q.phase_ref_error(True) and q.phase_ref_error(None) is None
    block = q.rules_block(rules, u"Construção nova")
    assert block["phase"] == u"Construção nova" and u"demolidos excluídos" in block["criteria"]["fase"]
    assert q.phase_status_kept("Existing") and not q.phase_status_kept("Demolished")
    assert q.stacked_volume(None, [1.0, None, 2.0]) == 3.0
    assert q.in_box((1.0, 1.0), (0.0, 0.0, 2.0, 2.0)) and q.room_point_z(None, 3.0) == 4.0


def finish_element_helpers():
    from revit_mcp import finish_elements as fe
    assert fe.validate_request({u"level": u"Térreo", u"offset_m": long(0)}, "floor") is None
    assert fe.validate_request({u"room_ids": [long(5)], u"height_m": 2.6}, "ceiling") is None
    assert "not both" in fe.validate_request({u"room_ids": [1], u"room_numbers": [u"1"]}, "floor")
    assert "offset_m is for floors" in fe.validate_request({u"level": u"T", u"offset_m": 0}, "ceiling")
    assert fe.ceiling_height(None, True, 0.0, 2.8) == (2.8, "room_top", None)
    assert u"another level" in fe.ceiling_height(None, False, 0.0, 2.8)[2]
    assert fe.exists_in_phase(0, None, 1) and not fe.exists_in_phase(0, 1, 1)
    assert not fe.exists_in_phase(2, None, 1) and fe.exists_in_phase(None, None, None)
    assert fe.preview_rooms([{"number": None, "room_id": long(7)}]) == [u"id 7"]
    assert fe._key(u"  Térreo ") == u"térreo" and fe.room_status(True, 0) == "not_enclosed"
    assert u"expected_count" in fe.validate_request({u"level": u"T", u"expected_count": -1}, "floor")
    assert fe.covering_hosts([{"area_m2": 10.0}, {"area_m2": 200.0}], 12.0) == [{"area_m2": 10.0}]
    seg = lambda a, b: {"start": a, "end": b, "length": ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5, "line": True}
    t = 0.002
    loop = [seg((0, 0, 0), (1, 0, 0)), seg((1, 0, 0), (1 + t, 0, 0)), seg((1 + t, 0, 0), (1 + t, 1, 0)),
            seg((1 + t, 1, 0), (0, 1, 0)), seg((0, 1, 0), (0, 0, 0))]
    cleaned, repaired, error = fe.clean_loop(loop)
    assert error is None and len(cleaned) == 4 and repaired == 2 and cleaned[1]["rebuilt"], error
    assert u"open" in fe.clean_loop([seg((0, 0, 0), (1, 0, 0)), seg((1.5, 0, 0), (0, 0, 0))])[2]
    assert u"skipped" in fe.summary_message("dry_run", 2, 1, "floors")


def parameter_number_helpers():
    from revit_mcp import parameters
    assert parameters.normalize_number_text(u"2,40", ".") == u"2.40"
    assert parameters.normalize_number_text(u"1.234,5 m²", ",") == u"1234,5 m²"
    assert parameters.normalize_number_text(u"110 cm", ",") == u"110 cm"
    assert parameters.normalize_number_text(u"Sala 1,2", ".") == u"Sala 1,2"
    assert parameters.is_blank(u"  ") and not parameters.is_blank(0)
    keep, collapsed, conflicts = parameters.group_changes([
        (0, (long(2), u"w"), u"1"), (1, (long(2), u"w"), u" 1"),
        (2, (long(3), u"w"), u"a"), (3, (long(3), u"w"), u"b")])
    assert keep == set([0]) and collapsed == [{"index": 1, "same_as": 0}]
    assert conflicts == [[2, 3]]
    assert parameters.project_decimal_symbol(object()) == "."


def ribbon_report_helpers():
    import io
    from revit_mcp import ribbon_reports as rr
    from revit_mcp import sheet_checks
    path = os.path.join(ROOT, "config", "checklists_pranchas.exemplo.json")
    with io.open(path, encoding="utf-8-sig") as stream:
        body, error = rr.load_checklists(stream.read())
    assert error is None, error
    options, error, errors = sheet_checks.parse_options(body)
    assert error is None, (error, errors)
    assert rr.load_checklists(u"{x")[1]
    text = rr.as_text(rr.sheet_report({
        "summary": {"sheets_checked": 1, "sheets_total": 1, "missing": 1},
        "sheets": [{"id": long(7), "number": u"A-1", "name": u"Planta T\u00e9rreo", "rule": None,
                    "status": "missing", "problems": [{"check": "scale", "detail": u"1:75"}],
                    "manual_checks": [u"cotas"]}]}, link=lambda i: u"#{}".format(i)))
    assert u"#7 | A-1 | Planta T\u00e9rreo" in text and u"Escala | 1:75" in text
    message, details = rr.pdf_plan_text({"folder": u"C:\\x", "files": [
        {"file": u"a.pdf", "sheets": [u"A-1"], "exists": True}]})
    assert message == u"Emitir 1 PDF de 1 folha?" and u"J\u00c1 EXISTE" in details
    plan = {"status": "dry_run", "folder": u"C:\\x",
            "files": [{"file": u"a.pdf", "sheets": [u"A-1"], "exists": True}]}
    assert rr.is_plan(plan) and rr.needs_overwrite(plan)
    approve = rr.replacement_guard(rr.replace_paths(plan))
    assert approve(u"Export", {"_will_replace": rr.replace_paths(plan)}) is None
    assert approve(u"Export", {"_will_replace": [u"C:\\x\\b.pdf"]}).status == 409
    assert rr.is_run_result({"status": "success", "files": [], "exported": long(0)})
    assert u"bloqueado" in rr.pdf_error_text({"error": u"file in use or locked: a.pdf"})
    assert rr.material_view_error("DrawingSheet", False) and \
        rr.material_view_error("ThreeD", False) is None
    folder = tempfile.mkdtemp()
    try:
        path, created, error = rr.prepare_checklists(folder, ROOT)
        assert error is None and created and os.path.isfile(path), error
        assert rr.prepare_checklists(folder, ROOT)[1] is False
    finally:
        shutil.rmtree(folder)


def audit_export_helpers():
    import io
    import zipfile
    import xml.etree.ElementTree as ET
    from revit_mcp import audit_export as ae
    name = u"Montante de canto trap\u00e9zoide"
    report = {"document": u"Template <CV> & Cia", "is_template_file": True, "revit_version": "2024",
              "limit": long(1000), "sections": {
                  "families": {"count": long(1), "unused_count": long(1), "truncated": False,
                               "items": [{"id": long(801), "name": name, "category": u"Montantes",
                                          "in_place": False, "instances": long(0), "unused": True}]},
                  "levels_grids": {"levels": {"count": 1, "unused_count": None, "truncated": False,
                                              "items": [{"id": long(5), "name": u"T\u00e9rreo",
                                                         "elevation_m": 3.048}]}},
                  "purge_preview": {"count": 1, "by_kind": {"FamilySymbol": 1}, "source": "revit_purge",
                                    "items": [{"id": long(9), "kind": "FamilySymbol",
                                               "category": u"Portas", "name": name}]}},
              "checklist": [{"item": 7, "title": u"Categorias", "coverage": u"conferir a olho",
                             "sections": [], "check_manually": u"V/G"}]}
    data = ae.audit_xlsx(report, today=u"2026-10-22")
    # IronPython's zipfile cannot read from io.BytesIO; read the bytes back
    # from disk, as Excel will.
    zip_folder = tempfile.mkdtemp()
    zip_path = os.path.join(zip_folder, "check.xlsx")
    with open(zip_path, "wb") as stream:
        stream.write(data)
    archive = zipfile.ZipFile(zip_path)
    workbook = archive.read("xl/workbook.xml").decode("utf-8")
    assert u'name="Fam\u00edlias"' in workbook and u'name="Limpeza (pr\u00e9via)"' in workbook
    for part in archive.namelist():
        ET.fromstring(archive.read(part))
    assert name in archive.read("xl/worksheets/sheet4.xml").decode("utf-8")  # Famílias
    assert ae.clean_xml(u"a\x01b") == u"ab" and ae.escape(u"<&>") == u"&lt;&amp;&gt;"
    assert ae.sheet_name(u"a/b", set()) == u"a b"
    assert ae.sheet_name(u"History", set()) == u"History (2)"
    assert ae.sheet_name(u"x" * 30 + u"'y", set()) == u"x" * 30
    assert ae.element_kind("FamilySymbol") == u"Tipo de fam\u00edlia"
    assert ae.cell_xml(u"A1", float("nan"), False).endswith(u">nan</t></is></c>")
    page = ae.audit_html(report, today=u"2026-10-22")
    assert name in page and u"Template &lt;CV&gt; &amp; Cia" in page
    folder = tempfile.mkdtemp()
    try:
        xlsx, html = ae.write_audit_files(os.path.join(folder, u"sa\u00edda"), report)
        assert zipfile.is_zipfile(xlsx) and os.path.isfile(html)
        assert ae.existing_outputs(os.path.join(folder, u"sa\u00edda")) == [xlsx, html]
        xlsx, html = ae.write_audit_files(xlsx, report)  # replaces both files
        assert not [n for n in os.listdir(folder) if n.endswith(".tmp")]
    finally:
        shutil.rmtree(folder)


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
check("audit helpers", audit_helpers)
check("sheet check helpers", sheet_check_helpers)
check("material check helpers", material_check_helpers)
check("quantity helpers", quantity_helpers)
check("review fix helpers", review_fix_helpers)
check("renumber helpers", renumber_helpers)
check("pdf export helpers", pdf_export_helpers)
check("phase helpers", phase_helpers)
check("audit review helpers", audit_review_helpers)
check("sheet check review helpers", sheet_check_review_helpers)
check("material check review helpers", material_check_review_helpers)
check("quantity review helpers", quantity_review_helpers)
check("finish element helpers", finish_element_helpers)
check("parameters number helpers", parameter_number_helpers)
check("ribbon report helpers", ribbon_report_helpers)
check("audit export helpers", audit_export_helpers)

if failures:
    print("\n{} failure(s): {}".format(len(failures), ", ".join(failures)))
    sys.exit(1)
print("\nAll IronPython checks passed.")
