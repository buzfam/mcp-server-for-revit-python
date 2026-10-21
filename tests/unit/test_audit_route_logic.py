# -*- coding: utf-8 -*-
"""Route logic of revit_mcp/audit.py against a fake Revit API. The module is
IronPython code; py2 builtins are injected for CPython."""
import builtins
import importlib
import sys
from types import SimpleNamespace

import pytest

MODULES = ("revit_mcp.utils", "revit_mcp.security", "revit_mcp.health", "revit_mcp.exports",
           "revit_mcp.audit")


def eid(value):
    return SimpleNamespace(Value=value)


INVALID = eid(-1)


class ElementId:
    InvalidElementId = INVALID

    def __new__(cls, value):
        return eid(int(value))


class Element:
    def __init__(self, element_id, name, **attributes):
        self.Id = eid(element_id)
        self.name = name
        self.__dict__.update(attributes)

    def GetTypeId(self):
        return getattr(self, "type_id", INVALID)


class ElementType(Element):
    pass


class View(Element):
    IsTemplate = False
    ViewTemplateId = INVALID
    ViewType = "FloorPlan"
    CanBePrinted = True
    Scale = 50

    def GetFilters(self):
        return [eid(i) for i in getattr(self, "filters", [])]


class ViewSchedule(View):
    ViewType = "Schedule"
    IsTitleblockRevisionSchedule = False


class ParameterFilterElement(Element):
    def GetCategories(self):
        return [1, 2]


class SelectionFilterElement(Element):
    pass


class Viewport(Element):
    pass


class ScheduleSheetInstance(Element):
    IsTitleblockRevisionSchedule = False


class Family(Element):
    IsInPlace = False

    def GetFamilySymbolIds(self):
        return [eid(i) for i in self.symbols]


class FamilySymbol(ElementType):
    pass


class FamilyInstance(Element):
    pass


class Material(Element):
    MaterialClass = u"Genérico"
    MaterialCategory = None


class FakeCollector:
    def __init__(self, doc):
        self.items = list(doc.elements)

    def OfClass(self, cls):
        self.items = [e for e in self.items if isinstance(e, cls)]
        return self

    def WhereElementIsNotElementType(self):
        self.items = [e for e in self.items if not isinstance(e, ElementType)]
        return self

    def ToElements(self):
        return list(self.items)

    def __iter__(self):
        return iter(self.items)


class FakeDoc:
    Title = u"Template CVArq"
    PathName = u"C:\\Templates\\CVArq 2024.rte"
    Application = SimpleNamespace(VersionNumber="2024")

    def __init__(self, elements, unused=None):
        self.elements = elements
        self.unused = unused

    def GetElement(self, element_id):
        for element in self.elements:
            if element.Id.Value == element_id.Value:
                return element
        return None

    def GetUnusedElements(self, categories):
        if self.unused is None:
            raise AttributeError("GetUnusedElements")
        return [eid(i) for i in self.unused]


@pytest.fixture
def audit(monkeypatch):
    DB = SimpleNamespace(
        ElementId=ElementId, Element=SimpleNamespace(Name=SimpleNamespace(GetValue=lambda e: e.name)),
        FilteredElementCollector=FakeCollector, View=View, ViewSchedule=ViewSchedule,
        ParameterFilterElement=ParameterFilterElement,
        SelectionFilterElement=SelectionFilterElement, Viewport=Viewport,
        ScheduleSheetInstance=ScheduleSheetInstance, Family=Family, FamilySymbol=FamilySymbol,
        Material=Material)
    pyrevit = SimpleNamespace(DB=DB, routes=SimpleNamespace(make_response=lambda **kw: kw),
                              forms=SimpleNamespace(alert=lambda *a, **k: True))
    monkeypatch.setitem(sys.modules, "pyrevit", pyrevit)
    for name in ("long", "basestring", "unicode"):
        monkeypatch.setattr(builtins, name, {"long": int, "basestring": str, "unicode": str}[name],
                            raising=False)
    for name in MODULES:
        sys.modules.pop(name, None)
    module = importlib.import_module("revit_mcp.audit")
    yield module
    for name in MODULES:
        sys.modules.pop(name, None)


def sample_model():
    template_used = View(10, u"PB - Planta baixa", IsTemplate=True, filters=[30])
    template_unused = View(11, u"Corte antigo", IsTemplate=True, ViewType="Section")
    plan = View(20, u"Térreo", ViewTemplateId=eid(10), filters=[31])
    loose = View(21, u"rascunho 3D", ViewType="ThreeD", Scale=100)
    browser = View(22, u"Navegador", ViewType="ProjectBrowser")
    schedule = ViewSchedule(23, u"Tabela de portas")
    loose_schedule = ViewSchedule(24, u"Tabela de janelas")
    sheet_viewport = Viewport(40, None, ViewId=eid(20), SheetId=eid(90))
    sheet_schedule = ScheduleSheetInstance(41, None, ScheduleId=eid(23), OwnerViewId=eid(90))
    door = Family(50, u"Porta simples", FamilyCategory=SimpleNamespace(Name=u"Portas"),
                  symbols=[51, 52])
    chair = Family(53, u"Cadeira", FamilyCategory=SimpleNamespace(Name=u"Mobiliário"),
                   symbols=[54])
    symbols = [FamilySymbol(51, u"80x210", FamilyName=u"Porta simples", Category=None),
               FamilySymbol(52, u"90x210", FamilyName=u"Porta simples", Category=None),
               FamilySymbol(54, u"Padrão", FamilyName=u"Cadeira", Category=None)]
    instances = [FamilyInstance(60, u"p", type_id=eid(51)), FamilyInstance(61, u"p", type_id=eid(51))]
    filters = [ParameterFilterElement(30, u"Paredes demolidas"),
               ParameterFilterElement(31, u"Mobiliário existente"),
               SelectionFilterElement(32, u"Seleção velha")]
    materials = [Material(70, u"Concreto"), Material(71, u"Madeira sem uso")]
    return ([template_used, template_unused, plan, loose, browser, schedule, loose_schedule,
             sheet_viewport, sheet_schedule, door, chair] + symbols + instances + filters
            + materials)


# ---- pure helpers ----

def test_parse_request_defaults(audit):
    options, error = audit.parse_request(None)
    assert error is None
    assert options == {"sections": list(audit.SECTIONS), "limit": 100, "naming": {}}


def test_parse_request_keeps_canonical_section_order(audit):
    options, _ = audit.parse_request({"sections": ["families", "views"], "limit": 5})
    assert options["sections"] == ["views", "families"] and options["limit"] == 5


@pytest.mark.parametrize("data, message", [
    ([], "JSON object"),
    ({"sections": "views"}, "non-empty list"),
    ({"sections": []}, "non-empty list"),
    ({"sections": ["views", "worksets"]}, "worksets"),
    ({"limit": 0}, "limit"),
    ({"limit": 1001}, "limit"),
    ({"limit": True}, "limit"),
    ({"limit": "10"}, "limit"),
    ({"naming": {"rooms": ".*"}}, "unknown kind"),
    ({"naming": {"views": ""}}, "non-empty"),
    ({"naming": {"views": "([A-Z"}}, "invalid regex"),
    ({"naming": []}, "naming must be"),
])
def test_parse_request_rejects(audit, data, message):
    options, error = audit.parse_request(data)
    assert options is None and message in error


def test_naming_regex_must_match_whole_name(audit):
    compiled, error = audit.compile_naming({"views": u"(PB|CT) - .+", "sheet_numbers": r"A\d{2}"})
    assert error is None
    pattern, regex = compiled["sheet_numbers"]
    result = audit.naming_check([(1, u"A01"), (2, u"A010"), (3, None)], pattern, regex, 1)
    assert result["checked"] == 3 and result["violations_count"] == 2
    assert result["violations"] == [{"id": 2, "name": u"A010"}] and result["truncated"]
    _, views = compiled["views"]
    assert views.match(u"PB - Térreo") and not views.match(u"Térreo PB - x")


def test_summarize_and_unused_first(audit):
    items = audit.unused_first([{"name": u"b", "unused": False}, {"name": u"Á", "unused": True},
                                {"name": u"a", "unused": False}])
    assert [i["name"] for i in items] == [u"Á", u"a", u"b"]
    summary = audit.summarize(items, 2, rule="x")
    assert summary == {"count": 3, "unused_count": 1, "items": items[:2], "truncated": True,
                       "unused_rule": "x"}
    assert audit.summarize([], 5, tracks_unused=False)["unused_count"] is None
    assert audit.summarize([], 5)["unused_count"] == 0


def test_small_helpers(audit):
    assert audit.feet_to_m(10.0) == 3.048 and audit.feet_to_m(None) is None
    assert audit.rgb_hex(255, 0, 128) == "#FF0080"
    grouped = audit.group_by_kind([{"kind": "Material", "name": u"b"},
                                   {"kind": "FamilySymbol", "name": u"a"},
                                   {"kind": "Material", "name": u"a"}], 2)
    assert grouped["by_kind"] == {"Material": 2, "FamilySymbol": 1}
    assert [i["kind"] for i in grouped["items"]] == ["FamilySymbol", "Material"]
    assert grouped["truncated"]


def test_checklist_has_19_items_and_marks_manual_ones(audit):
    entries = audit.checklist(["views"], naming_requested=False)
    assert [e["item"] for e in entries] == list(range(1, 20))
    assert entries[5]["sections"] == ["views"] and entries[0]["sections"] == []
    assert entries[6]["coverage"] == u"conferir a olho" and "check_manually" in entries[6]
    assert "naming" in audit.checklist([], naming_requested=True)[15]["sections"]
    keys = set(audit.SECTIONS) | {"naming"}
    assert all(set(k) <= keys for _, _, k, _, _ in audit.CHECKLIST)


# ---- sections against a fake model ----

def build(audit, sections, unused=None, naming=None, limit=100):
    options, error = audit.parse_request({"sections": sections, "limit": limit,
                                          **({"naming": naming} if naming else {})})
    assert error is None
    return audit.build_audit(FakeDoc(sample_model(), unused), options)


def test_view_templates_and_filters_usage(audit):
    report = build(audit, ["view_templates", "filters"])
    templates = report["sections"]["view_templates"]
    assert templates["count"] == 2 and templates["unused_count"] == 1
    assert templates["items"][0]["name"] == u"Corte antigo" and templates["items"][0]["unused"]
    assert templates["items"][1]["views"] == 1 and templates["items"][1]["filters"] == 1
    filters = {i["name"]: i for i in report["sections"]["filters"]["items"]}
    assert filters[u"Paredes demolidas"]["templates"] == 1
    assert filters[u"Mobiliário existente"]["views"] == 1
    assert filters[u"Seleção velha"]["unused"] and filters[u"Seleção velha"]["kind"] == "selection"
    assert filters[u"Paredes demolidas"]["categories"] == 2
    assert report["is_template_file"] and report["document"] == u"Template CVArq"
    assert "unused_source" not in report


def test_views_and_schedules_on_sheets(audit):
    report = build(audit, ["views", "schedules"])
    views = report["sections"]["views"]
    assert views["count"] == 2  # browser and schedules are not views here
    assert views["items"][0] == {"id": 21, "name": u"rascunho 3D", "view_type": "ThreeD",
                                 "scale": u"1:100", "template": None, "on_sheet": False,
                                 "unused": True}
    assert views["items"][1]["template"] == u"PB - Planta baixa"
    assert views["by_type"] == {"ThreeD": 1, "FloorPlan": 1} and views["without_template"] == 1
    schedules = report["sections"]["schedules"]
    assert schedules["unused_count"] == 1 and schedules["items"][0]["name"] == u"Tabela de janelas"


def test_families_by_instance_count_without_purge_api(audit):
    report = build(audit, ["families", "materials", "purge_preview"])
    families = {i["name"]: i for i in report["sections"]["families"]["items"]}
    assert families[u"Porta simples"]["instances"] == 2
    assert families[u"Porta simples"]["unused_types"] == 1 and not families[u"Porta simples"]["unused"]
    assert families[u"Cadeira"]["unused"]
    assert report["sections"]["families"]["by_category"][u"Mobiliário"] == {"families": 1, "unused": 1}
    assert report["sections"]["materials"]["unused_count"] is None
    purge = report["sections"]["purge_preview"]
    assert purge["source"] == "instance_count"
    assert {i["name"] for i in purge["items"]} == {u"Porta simples: 90x210", u"Cadeira: Padrão"}
    assert purge["not_in_revit_purge"]["view_templates"]["items"] == [
        {"id": 11, "name": u"Corte antigo"}]
    assert report["unused_source"] == "instance_count"


def test_purge_api_drives_unused_when_available(audit, monkeypatch):
    monkeypatch.setattr(audit, "_empty_id_set", lambda: set())
    report = build(audit, ["families", "materials", "purge_preview"], unused=[54, 53, 71])
    materials = {i["name"]: i["unused"] for i in report["sections"]["materials"]["items"]}
    assert materials == {u"Madeira sem uso": True, u"Concreto": False}
    assert report["sections"]["materials"]["unused_count"] == 1
    families = {i["name"]: i for i in report["sections"]["families"]["items"]}
    assert families[u"Cadeira"]["unused"] and not families[u"Porta simples"]["unused"]
    purge = report["sections"]["purge_preview"]
    assert purge["source"] == "revit_purge" and purge["count"] == 3
    assert purge["by_kind"] == {"FamilySymbol": 1, "Family": 1, "Material": 1}
    assert report["unused_source"] == "revit_purge"


def test_naming_and_section_errors(audit):
    report = build(audit, ["view_templates", "levels_grids"],
                   naming={"view_templates": u"PB - .+", "materials": u"[A-Z].*"})
    naming = report["sections"]["naming"]
    assert naming["view_templates"]["violations"] == [{"id": 11, "name": u"Corte antigo"}]
    assert naming["materials"]["violations_count"] == 0
    # The fake DB has no Level class: the section fails alone, the rest is returned.
    assert "levels_grids" in report["section_errors"]
    assert "view_templates" in report["sections"]


def test_route_validates_before_reading_the_model(audit, monkeypatch):
    monkeypatch.setattr(audit, "authorize_request", lambda request, action=None: None)

    class Api:
        def route(self, path, methods):
            def decorator(function):
                self.handler = function
                return function
            return decorator

    api = Api()
    audit.register_audit_routes(api)
    request = SimpleNamespace(data={"limit": 0}, headers={})
    assert api.handler(None, request)["status"] == 503
    response = api.handler(FakeDoc([]), request)
    assert response["status"] == 400 and "limit" in response["data"]["error"]
    ok = api.handler(FakeDoc(sample_model()), SimpleNamespace(data={"sections": ["filters"]}))
    assert ok["data"]["status"] == "success" and "filters" in ok["data"]["sections"]


# ---- review fixes ----

def test_purge_result_falls_back_when_api_answers_nothing(audit):
    assert audit.purge_result(None, 3) is None
    assert audit.purge_result([], 2) is None          # empty while instance counts find unused
    assert audit.purge_result([], 0) == set()         # really nothing to purge
    assert audit.purge_result([5, 6], 2) == {5, 6}


def test_empty_get_unused_elements_uses_instance_counts(audit, monkeypatch):
    monkeypatch.setattr(audit, "_empty_id_set", lambda: set())
    report = build(audit, ["families", "materials", "purge_preview"], unused=[])
    assert report["unused_source"] == "instance_count"
    assert "unused_source_note" in report
    assert report["sections"]["purge_preview"]["source"] == "instance_count"
    families = {i["name"]: i for i in report["sections"]["families"]["items"]}
    assert families[u"Cadeira"]["unused"] and not families[u"Porta simples"]["unused"]
    assert report["sections"]["materials"]["unused_count"] is None


def test_time_budget_skips_remaining_sections(audit):
    options, _ = audit.parse_request({"sections": ["view_templates", "filters", "views"],
                                      "naming": {"views": u".+"}})
    ticks = iter([0.0, 0.0, 10.0, 300.0, 300.0, 300.0])
    report = audit.build_audit(FakeDoc(sample_model()), options, budget_s=240.0,
                               clock=lambda: next(ticks))
    assert set(report["sections"]) == {"view_templates", "filters"}
    assert report["section_errors"] == {"views": u"skipped: time budget",
                                        "naming": u"skipped: time budget"}
    assert report["time_budget"]["skipped_sections"] == ["naming", "views"]


@pytest.mark.parametrize("body", [None, "", b""])
def test_route_empty_body_uses_defaults(audit, monkeypatch, body):
    monkeypatch.setattr(audit, "authorize_request", lambda request, action=None: None)

    class Api:
        def route(self, path, methods):
            def decorator(function):
                self.handler = function
                return function
            return decorator

    api = Api()
    audit.register_audit_routes(api)
    response = api.handler(FakeDoc(sample_model()), SimpleNamespace(data=body))
    assert "status" not in response or response.get("status") != 500
    assert response["data"]["status"] == "success"
