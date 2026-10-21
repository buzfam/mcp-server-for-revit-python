# -*- coding: utf-8 -*-
"""Route logic of revit_mcp/sheet_checks.py (013) against a fake Revit API:
rule matching, per-sheet problems and status, title block parameters by
localized or BuiltInParameter name, generic checks, views off sheets and the
read-only route. The module is IronPython code; py2 builtins are injected for
CPython."""
import builtins
import importlib
import sys
from types import SimpleNamespace

import pytest

MODULES = ("revit_mcp.utils", "revit_mcp.security", "revit_mcp.sheets", "revit_mcp.health",
           "revit_mcp.parameters", "revit_mcp.exports", "revit_mcp.sheet_checks")


class Eid(int):
    @property
    def Value(self):
        return int(self)


INVALID = Eid(-1)


class Param:
    def __init__(self, value, storage="String"):
        self.value, self.StorageType = value, storage
        self.HasValue = value is not None

    def AsString(self):
        return self.value

    def AsValueString(self):
        return None if self.value is None else str(self.value)


class Element:
    category = None
    is_type = False
    view_independent = True

    def __init__(self, doc, name=None, params=None):
        self.doc = doc
        self.Id = doc.add(self)
        self._name = name
        self.params = params or {}
        self.visible_in = ()

    def GetParameters(self, name):
        return [self.params[name]] if name in self.params else []

    def get_Parameter(self, builtin):
        return self.params.get("builtin:" + builtin)


class View(Element):
    def __init__(self, doc, name, view_type="FloorPlan", scale=50, template=False, printable=True):
        super().__init__(doc, name)
        self.ViewType, self.Scale, self.IsTemplate, self.CanBePrinted = \
            view_type, scale, template, printable


class ViewSchedule(View):
    IsTitleblockRevisionSchedule = False
    IsInternalKeynoteSchedule = False

    def __init__(self, doc, name):
        super().__init__(doc, name, "Schedule", scale=0)


class ViewSheet(View):
    def __init__(self, doc, number, name, revision=None, placeholder=False, params=None):
        super().__init__(doc, name, "DrawingSheet", scale=0)
        self.SheetNumber, self.IsPlaceholder = number, placeholder
        self.params = dict(params or {})
        self.params["builtin:SHEET_CURRENT_REVISION"] = Param(revision)


class Viewport(Element):
    def __init__(self, doc, sheet, view):
        super().__init__(doc)
        self.SheetId, self.ViewId = sheet.Id, view.Id


class ScheduleSheetInstance(Element):
    def __init__(self, doc, sheet, schedule, revision_schedule=False):
        super().__init__(doc)
        self.OwnerViewId, self.ScheduleId = sheet.Id, schedule.Id
        self.IsTitleblockRevisionSchedule = revision_schedule


class TitleBlock(Element):
    category = "tb"

    def __init__(self, doc, sheet, params=None):
        super().__init__(doc, params=params)
        self.OwnerViewId = sheet.Id


class Wall(Element):
    def __init__(self, doc, *views):
        super().__init__(doc)
        self.visible_in = tuple(v.Id for v in views)


class Collector:
    def __init__(self, doc, view_id=None):
        if view_id is None:
            self.items = list(doc.elements.values())
        else:
            self.items = [e for e in doc.elements.values() if view_id in e.visible_in]

    def _keep(self, test):
        self.items = [e for e in self.items if test(e)]
        return self

    def OfClass(self, cls):
        return self._keep(lambda e: isinstance(e, cls))

    def OfCategory(self, category):
        return self._keep(lambda e: e.category == category)

    def WhereElementIsNotElementType(self):
        return self._keep(lambda e: not e.is_type)

    def WhereElementIsViewIndependent(self):
        return self._keep(lambda e: e.view_independent)

    def GetElementCount(self):
        raise AssertionError("empty check must not count every element")

    def FirstElementId(self):
        return self.items[0].Id if self.items else INVALID

    def __iter__(self):
        return iter(list(self.items))


class Doc:
    def __init__(self):
        self.elements, self.next_id = {}, 100
        self.ProjectInformation = None

    def add(self, element):
        self.next_id += 1
        element_id = Eid(self.next_id)
        self.elements[element_id] = element
        return element_id

    def GetElement(self, element_id):
        return self.elements.get(element_id)


class BuiltInParameters:
    def __getattr__(self, name):
        return name


def _name(element):
    if element._name is None:
        raise AttributeError("Name")
    return element._name


@pytest.fixture
def checks(monkeypatch):
    DB = SimpleNamespace(
        Element=SimpleNamespace(Name=SimpleNamespace(GetValue=_name)),
        View=View, ViewSheet=ViewSheet, ViewSchedule=ViewSchedule, Viewport=Viewport,
        ScheduleSheetInstance=ScheduleSheetInstance, FilteredElementCollector=Collector,
        BuiltInCategory=SimpleNamespace(OST_TitleBlocks="tb"),
        BuiltInParameter=BuiltInParameters(),
        StorageType=SimpleNamespace(String="String", ElementId="ElementId", Integer="Integer"),
        ElementId=Eid)
    DB.ElementId.InvalidElementId = INVALID
    pyrevit = SimpleNamespace(DB=DB, routes=SimpleNamespace(make_response=lambda **kw: kw),
                              forms=SimpleNamespace(alert=lambda *a, **k: True))
    monkeypatch.setitem(sys.modules, "pyrevit", pyrevit)
    for name, value in (("long", int), ("basestring", str), ("unicode", str)):
        monkeypatch.setattr(builtins, name, value, raising=False)
    for name in MODULES:
        sys.modules.pop(name, None)
    yield importlib.import_module("revit_mcp.sheet_checks")
    for name in MODULES:
        sys.modules.pop(name, None)


LAYOUT = {"name": "Planta de layout", "match": {"number_prefix": "A-1"},
          "required_views": [{"view_type": "FloorPlan", "name_regex": "layout"}],
          "required_schedules": ["esquadrias"], "required_legends": ["legenda"],
          "allowed_scales": [50], "title_block_params": ["Desenhado por", "SHEET_ISSUE_DATE"],
          "require_revision": True, "max_viewports": 3, "manual": ["cotas conferidas"]}
CORTES = {"match": {"name_regex": "^cortes?"},
          "required_views": [{"view_type": "Section", "min": 2}]}


@pytest.fixture
def doc():
    doc = Doc()
    doc.ProjectInformation = Element(doc, "Info", params={"Cliente": Param(u"Carol")})
    # A-101: complete layout sheet
    doc.layout = ViewSheet(doc, "A-101", u"Planta de Layout", revision="R01",
                           params={"Desenhado por": Param("CFG")})
    TitleBlock(doc, doc.layout, {"builtin:SHEET_ISSUE_DATE": Param("2026-10-21")})
    doc.plan = View(doc, "Layout - Térreo")
    Wall(doc, doc.plan)
    Viewport(doc, doc.layout, doc.plan)
    doc.legend = View(doc, "Legenda de layout", "Legend", scale=1)
    Viewport(doc, doc.layout, doc.legend)
    doc.schedule = ViewSchedule(doc, "Tabela de Esquadrias")
    ScheduleSheetInstance(doc, doc.layout, doc.schedule)
    # A-102: layout sheet with problems
    doc.bad = ViewSheet(doc, "a-102", u"Planta de layout superior",
                        params={"Desenhado por": Param("  ")})
    doc.upper = View(doc, "Superior", scale=75)
    Viewport(doc, doc.bad, doc.upper)          # empty: no model element visible
    revisions = ViewSchedule(doc, "Revisões")
    revisions.IsTitleblockRevisionSchedule = True
    ScheduleSheetInstance(doc, doc.bad, revisions, revision_schedule=True)
    # A-201: cortes, one section only
    doc.cuts = ViewSheet(doc, "A-201", "Cortes")
    doc.section = View(doc, "Corte AA", "Section", scale=50)
    Wall(doc, doc.section)
    Viewport(doc, doc.cuts, doc.section)
    TitleBlock(doc, doc.cuts)
    # A-900: no rule, nothing on it, no title block
    doc.empty = ViewSheet(doc, "A-900", "Folha vazia")
    ViewSheet(doc, "X-1", "Placeholder", placeholder=True)
    # views off sheets
    View(doc, "Estudo", "FloorPlan")
    View(doc, "3D Vista", "ThreeD")
    View(doc, "Template", template=True)
    View(doc, "Navegador", "ProjectBrowser")
    ViewSchedule(doc, "Quantitativo de pisos")
    return doc


def options(checks, **data):
    parsed, error, errors = checks.parse_options(data)
    assert error is None, (error, errors)
    return parsed


def by_number(report):
    return {s["number"]: s for s in report["sheets"]}


# ---- pure helpers ----

@pytest.mark.parametrize("rule, fragment", [
    ("x", "object"),
    ({}, "match"),
    ({"match": {}}, "match"),
    ({"match": {"prefix": "A"}}, "unknown keys prefix"),
    ({"match": {"number_regex": "("}}, "invalid regex"),
    ({"match": {"number_prefix": "A"}, "required_view": []}, "unknown keys required_view"),
    ({"match": {"number_prefix": "A"}, "required_views": [{"min": 1}]}, "view_type and/or"),
    ({"match": {"number_prefix": "A"}, "required_views": [{"view_type": "Planta"}]}, "view_type must"),
    ({"match": {"number_prefix": "A"}, "required_views": [{"view_type": "Section", "min": 0}]}, "min"),
    ({"match": {"number_prefix": "A"}, "required_schedules": ["["]}, "invalid regex"),
    ({"match": {"number_prefix": "A"}, "allowed_scales": [50, True]}, "allowed_scales"),
    ({"match": {"number_prefix": "A"}, "require_revision": "sim"}, "require_revision"),
    ({"match": {"number_prefix": "A"}, "min_viewports": 3, "max_viewports": 1}, "greater"),
    ({"match": {"number_prefix": "A"}, "manual": [""]}, "manual[0]"),
])
def test_validate_rule_rejects(checks, rule, fragment):
    error = checks.validate_rule(rule)
    assert error and fragment in error


def test_validate_rule_accepts_examples(checks):
    assert checks.validate_rule(LAYOUT) is None
    assert checks.validate_rule(CORTES) is None
    assert checks.validate_rule({"match": {"number_prefix": "A"},
                                 "required_views": [{"view_type": "floorplan"}]}) is None


@pytest.mark.parametrize("data, fragment", [
    ({"generic": False}, "Nothing to check"),
    ({"checklists": "x"}, "must be a list"),
    ({"checklists": [CORTES] * 51}, "At most 50"),
    ({"sheets": []}, "sheets"),
    ({"sheets": ["A", ""]}, "sheet number"),
    ({"limit": 0}, "limit"),
    ({"limit": True}, "limit"),
    ({"unplaced_limit": 201}, "unplaced_limit"),
    ({"only_problems": "yes"}, "only_problems"),
])
def test_parse_options_errors(checks, data, fragment):
    parsed, error, _ = checks.parse_options(data)
    assert parsed is None and fragment in error


def test_parse_options_defaults_and_rule_errors(checks):
    parsed, error, _ = checks.parse_options({})
    assert error is None and parsed["generic"] is True and parsed["limit"] == 200
    assert parsed["unplaced_limit"] == 50 and parsed["rules"] == []
    _, error, errors = checks.parse_options({"checklists": [CORTES, {"match": {}}]})
    assert error == "Invalid checklists" and [e["index"] for e in errors] == [1]


def test_rule_matching(checks):
    assert checks.rule_matches(LAYOUT, "a-105", "x")
    assert not checks.rule_matches(LAYOUT, "A-201", "x")
    assert checks.rule_matches(CORTES, "A-201", "Cortes AA e BB")
    assert not checks.rule_matches(CORTES, "A-201", "Planta e cortes")
    both = {"match": {"number_prefix": "A-", "name_regex": "forro"}}
    assert checks.rule_matches(both, "A-301", "Planta de Forro")
    assert not checks.rule_matches(both, "E-301", "Planta de Forro")
    assert checks.pick_rule([CORTES, LAYOUT], "A-101", "Planta") == (1, LAYOUT)
    assert checks.pick_rule([CORTES], "A-101", "Planta") == (None, None)
    assert checks.rule_label(CORTES, 0) == "#0" and checks.rule_label(None, None) is None


def test_evaluate_sheet_status(checks):
    info = {"views": [{"name": "P", "type": "FloorPlan", "scale": 100, "empty": False}],
            "schedules": [], "has_title_block": True, "params": {}, "revision": None}
    rule = {"match": {"number_prefix": "A"}, "allowed_scales": [50, 100]}
    assert checks.evaluate_sheet(info, rule, True) == ([], [], "ok")
    rule["allowed_scales"] = [50]
    problems, _, status = checks.evaluate_sheet(info, rule, True)
    assert status == "review" and problems[0]["detail"] == u"vista 'P' em 1:100 (permitidas: 1:50)"
    rule["manual"] = ["notas"]
    rule["allowed_scales"] = [100]
    assert checks.evaluate_sheet(info, rule, True)[1:] == (["notas"], "review")
    rule["require_revision"] = True
    assert checks.evaluate_sheet(info, rule, True)[2] == "missing"


def test_summarize_unplaced_caps_list(checks):
    entries = [{"id": i, "name": "V{}".format(i), "type": "FloorPlan" if i % 2 else "Section"}
               for i in range(5)]
    summary = checks.summarize_unplaced(entries, 2)
    assert summary["count"] == 5 and summary["by_type"] == {"FloorPlan": 2, "Section": 3}
    assert len(summary["views"]) == 2 and summary["truncated"] is True
    assert checks.summarize_unplaced(entries, 0)["views"] == []


# ---- check_sheets against the fake model ----

def test_full_report(checks, doc):
    report = checks.check_sheets(doc, options(checks, checklists=[LAYOUT, CORTES]))
    assert report["status"] == "success"
    sheets = by_number(report)
    assert list(sheets) == ["A-101", "a-102", "A-201", "A-900"]   # placeholder skipped

    good = sheets["A-101"]
    assert good["rule"] == "Planta de layout" and good["problems"] == [], good
    assert good["status"] == "review" and good["manual_checks"] == ["cotas conferidas"]
    assert good["revision"] == "R01" and good["viewports"] == 2 and good["schedules"] == 1

    bad = {(p["check"], p["detail"]) for p in sheets["a-102"]["problems"]}
    assert sheets["a-102"]["status"] == "missing"
    assert ("title_block", u"folha sem carimbo") in bad
    assert ("empty_viewport", u"vista 'Superior' sem elementos de modelo visíveis") in bad
    assert ("scale", u"vista 'Superior' em 1:75 (permitidas: 1:50)") in bad
    assert ("required_view", u"faltando vista FloorPlan nome ~ 'layout' (0 de 1)") in bad
    assert ("required_schedule", u"faltando tabela ~ 'esquadrias'") in bad  # revision schedule ignored
    assert ("required_legend", u"faltando legenda ~ 'legenda'") in bad
    assert ("title_block_param", u"parâmetro 'Desenhado por' vazio") in bad
    assert any(c == "title_block_param" and "SHEET_ISSUE_DATE" in d and "não encontrado" in d
               for c, d in bad)
    assert ("revision", u"folha sem revisão atual") in bad

    cuts = sheets["A-201"]
    assert cuts["rule"] == "#1" and cuts["status"] == "missing"
    assert [p["detail"] for p in cuts["problems"]] == [u"faltando vista Section (1 de 2)"]

    empty = sheets["A-900"]
    assert empty["rule"] is None
    assert [p["check"] for p in empty["problems"]] == ["no_views", "title_block"]

    summary = report["summary"]
    assert summary["sheets_checked"] == 4 and summary["sheets_total"] == 4
    assert (summary["ok"], summary["missing"], summary["review"]) == (0, 3, 1)
    assert summary["placeholders_skipped"] == 1 and summary["sheets_without_rule"] == 1
    assert summary["problems_by_check"]["title_block"] == 2 and summary["truncated"] is False

    unplaced = report["unplaced_views"]
    assert unplaced["count"] == 2 and unplaced["by_type"] == {"FloorPlan": 1, "ThreeD": 1}
    assert {v["name"] for v in unplaced["views"]} == {"Estudo", "3D Vista"}


def test_params_fall_back_to_project_information(checks, doc):
    rule = {"match": {"number_prefix": "A-101"}, "title_block_params": ["Cliente", "Ausente"]}
    sheet = by_number(checks.check_sheets(doc, options(checks, checklists=[rule])))["A-101"]
    assert [p["detail"] for p in sheet["problems"]] == [
        u"parâmetro 'Ausente' não encontrado na folha, no carimbo (instância ou tipo) nem nas "
        u"informações do projeto"]


def test_sheet_filter_limit_and_only_problems(checks, doc):
    report = checks.check_sheets(doc, options(
        checks, sheets=["a-101", "A-201", "Z-9", "A-101"], limit=1, generic=False,
        checklists=[CORTES]))
    summary = report["summary"]
    assert summary["not_found"] == ["Z-9"] and summary["sheets_total"] == 2
    assert summary["truncated"] is True and summary["sheets_checked"] == 1
    assert [s["number"] for s in report["sheets"]] == ["A-101"]
    assert report["sheets"][0]["status"] == "ok" and "unplaced_views" not in report

    report = checks.check_sheets(doc, options(checks, only_problems=True, generic=False,
                                              checklists=[CORTES]))
    assert [s["number"] for s in report["sheets"]] == ["A-201"]
    assert report["summary"]["ok"] == 3


def test_unplaced_schedules_on_request(checks, doc):
    report = checks.check_sheets(doc, options(checks, unplaced_include_schedules=True))
    assert report["unplaced_views"]["by_type"].get("Schedule") == 1


# ---- route ----

class Api:
    def __init__(self):
        self.handlers = {}

    def route(self, path, methods=None):
        def decorator(function):
            self.handlers[path] = function
            return function
        return decorator


@pytest.fixture
def route(checks, monkeypatch):
    calls = []

    def authorize(request, action=None):
        calls.append(action)
        return None

    monkeypatch.setattr(checks, "authorize_request", authorize)
    api = Api()
    checks.register_sheet_check_routes(api)
    return api.handlers["/check_sheets/"], calls


def test_route_is_read_only(route, doc):
    handler, calls = route
    response = handler(doc, SimpleNamespace(data={"checklists": [CORTES]}))
    assert calls == [None] and response["data"]["status"] == "success"


def test_route_bad_payload_is_400(route, doc):
    handler, calls = route
    response = handler(doc, SimpleNamespace(data={"checklists": [{"match": {}}]}))
    assert response["status"] == 400 and response["data"]["errors"][0]["index"] == 0
    assert calls == [None]


def test_route_without_document_is_503(route):
    handler, _ = route
    assert handler(None, SimpleNamespace(data={}))["status"] == 503


# ---- review fixes ----

class TitleBlockWithType(TitleBlock):
    def __init__(self, doc, sheet, type_params):
        super().__init__(doc, sheet)
        self.block_type = Element(doc, "A1", params=type_params)
        self.block_type.is_type = True

    def GetTypeId(self):
        return self.block_type.Id


def test_title_block_params_read_from_the_type(checks):
    doc = Doc()
    sheet = ViewSheet(doc, "A-1", "Planta")
    TitleBlockWithType(doc, sheet, {"Escritório": Param(u"CVArq")})
    rule = {"match": {"number_prefix": "A-"}, "title_block_params": [u"Escritório"]}
    report = checks.check_sheets(doc, options(checks, checklists=[rule], generic=False))
    assert report["sheets"][0]["problems"] == []


def test_perspective_3d_views_skip_the_scale_check(checks):
    doc = Doc()
    sheet = ViewSheet(doc, "A-1", "Perspectivas")
    TitleBlock(doc, sheet)
    camera = View(doc, "Perspectiva sala", "ThreeD", scale=1)
    camera.IsPerspective = True
    axo = View(doc, "Isométrica", "ThreeD", scale=1)
    axo.IsPerspective = False
    for view in (camera, axo):
        Wall(doc, view)
        Viewport(doc, sheet, view)
    rule = {"match": {"number_prefix": "A-"}, "allowed_scales": [50]}
    report = checks.check_sheets(doc, options(checks, checklists=[rule]))
    details = [p["detail"] for p in report["sheets"][0]["problems"]]
    assert details == [u"vista 'Isométrica' em 1:1 (permitidas: 1:50)"]


def test_evaluate_sheet_ignores_scale_of_perspective(checks):
    rule = {"match": {"number_prefix": "A"}, "allowed_scales": [50]}
    info = {"views": [{"name": "P", "type": "ThreeD", "scale": 1, "perspective": True}],
            "has_title_block": True}
    assert checks.evaluate_sheet(info, rule, False)[0] == []


def test_empty_checker_caches_and_respects_budget(checks, doc):
    calls = []
    original = checks._is_empty

    def counting(d, view, datum_filter):
        calls.append(view.Id)
        return original(d, view, datum_filter)

    checks._is_empty = counting
    try:
        ticks = iter([0.0, 1.0, 2.0, 100.0])
        checker = checks.EmptyChecker(doc, None, budget_s=60.0, clock=lambda: next(ticks))
        assert checker.check(doc.plan) is False
        assert checker.check(doc.plan) is False          # cached: no clock, no collector
        assert checker.check(doc.upper) is True
        assert checker.check(doc.section) is False
        assert checker.check(doc.cuts) is None and checker.skipped == 1
        assert calls == [doc.plan.Id, doc.upper.Id, doc.section.Id]
    finally:
        checks._is_empty = original


def test_summary_counts_skipped_empty_checks(checks, doc, monkeypatch):
    monkeypatch.setattr(checks.EmptyChecker.__init__, "__defaults__", (-1.0, None))
    report = checks.check_sheets(doc, options(checks))
    assert report["summary"]["empty_check_skipped"] >= 1
    assert all(p["check"] != "empty_viewport" for s in report["sheets"] for p in s["problems"])


def test_datum_categories_include_elevation_markers(checks):
    assert "OST_ElevationMarks" in checks.DATUM_CATEGORIES and "OST_Elev" in checks.DATUM_CATEGORIES


@pytest.mark.parametrize("body", [None, "", b""])
def test_route_empty_body_uses_defaults(route, doc, body):
    handler, calls = route
    response = handler(doc, SimpleNamespace(data=body))
    assert calls == [None] and response["data"]["status"] == "success"
