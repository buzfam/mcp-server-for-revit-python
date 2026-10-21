# -*- coding: utf-8 -*-
"""Route logic of revit_mcp/sheets.py against a fake Revit API: all-or-nothing,
dry-run rollback, name matching, title block default and the authorization
order of the routes. The module is IronPython code; py2 builtins are injected
for CPython."""
import builtins
import importlib
import sys
from types import SimpleNamespace

import pytest

MODULES = ("revit_mcp.utils", "revit_mcp.security", "revit_mcp.sheets")


class Eid(int):
    @property
    def Value(self):
        return int(self)


INVALID = Eid(-1)


class XYZ:
    def __init__(self, x, y, z):
        self.X, self.Y, self.Z = x, y, z


class Element:
    category = None
    is_type = False
    owner_view = None

    def __init__(self, doc, name=None):
        self.doc = doc
        self.Id = doc.add(self)
        self._name = name


class Param:
    IsReadOnly = False

    def __init__(self, owner, attribute):
        self.owner, self.attribute = owner, attribute

    def Set(self, value):
        if value.startswith("!"):
            return False
        setattr(self.owner, self.attribute, value)
        return True


class View(Element):
    IsTemplate = False

    def __init__(self, doc, name, view_type="FloorPlan", template=False, empty=False):
        super().__init__(doc, name)
        self.ViewType, self.IsTemplate, self.empty = view_type, template, empty
        self.ViewTemplateId = INVALID

    def IsValidViewTemplate(self, template_id):
        return self.doc.elements[template_id].ViewType == self.ViewType


class ViewSchedule(View):
    IsTitleblockRevisionSchedule = False

    def __init__(self, doc, name):
        super().__init__(doc, name, "Schedule")


class ViewSheet(View):
    def __init__(self, doc, number, name, block_id=INVALID):
        super().__init__(doc, name, "DrawingSheet")
        self._number = number
        self.Outline = SimpleNamespace(Min=SimpleNamespace(U=0.0, V=0.0),
                                       Max=SimpleNamespace(U=2.0, V=1.0))
        if block_id != INVALID:
            TitleBlock(doc, self)

    @property
    def SheetNumber(self):
        return self._number

    @SheetNumber.setter
    def SheetNumber(self, value):
        if any(isinstance(e, ViewSheet) and e is not self and e._number == value
               for e in self.doc.elements.values()):
            raise ValueError("Sheet number already in use")
        self._number = value

    def get_Parameter(self, builtin):
        return Param(self, "_name") if builtin == "SHEET_NAME" else None

    @staticmethod
    def Create(doc, block_id):
        return ViewSheet(doc, "tmp{}".format(doc.next_id), "Sheet", block_id)


class TitleSymbol(Element):
    category, is_type = "tb", True

    def __init__(self, doc, family, name):
        super().__init__(doc, name)
        self.FamilyName = family


class TitleBlock(Element):
    category = "tb"

    def __init__(self, doc, sheet):
        super().__init__(doc)
        self.owner_view = sheet.Id

    def get_BoundingBox(self, view):
        return SimpleNamespace(Min=XYZ(0.1, 0.1, 0), Max=XYZ(2.7, 1.9, 0))


class Viewport(Element):
    def __init__(self, doc, sheet_id, view_id, point):
        super().__init__(doc)
        self.sheet_id, self.view_id, self.point = sheet_id, view_id, point

    @staticmethod
    def CanAddViewToSheet(doc, sheet_id, view_id):
        view = doc.elements[view_id]
        if isinstance(view, (ViewSheet, ViewSchedule)) or view.empty:
            return False
        if view.ViewType == "Legend":
            return True
        return not any(isinstance(e, Viewport) and e.view_id == view_id
                       for e in doc.elements.values())

    @staticmethod
    def Create(doc, sheet_id, view_id, point):
        return Viewport(doc, sheet_id, view_id, point)


class ScheduleSheetInstance(Viewport):
    @staticmethod
    def Create(doc, sheet_id, view_id, point):
        return ScheduleSheetInstance(doc, sheet_id, view_id, point)


class Collector:
    def __init__(self, doc, view_id=None):
        self.items = [e for e in doc.elements.values()
                      if view_id is None or e.owner_view == view_id]

    def _keep(self, test):
        self.items = [e for e in self.items if test(e)]
        return self

    def OfClass(self, cls):
        return self._keep(lambda e: isinstance(e, cls))

    def OfCategory(self, category):
        return self._keep(lambda e: e.category == category)

    def WhereElementIsElementType(self):
        return self._keep(lambda e: e.is_type)

    def WhereElementIsNotElementType(self):
        return self._keep(lambda e: not e.is_type)

    def __iter__(self):
        return iter(list(self.items))


class Transaction:
    log = []

    def __init__(self, doc, name):
        self.doc, self.name, self.started, self.ended = doc, name, False, False

    def Start(self):
        self.started = True
        self.saved = (dict(self.doc.elements),
                      {k: dict(e.__dict__) for k, e in self.doc.elements.items()})

    def Commit(self):
        self.ended = True
        Transaction.log.append("commit")
        return "Committed"

    def RollBack(self):
        self.ended = True
        Transaction.log.append("rollback")
        elements, states = self.saved
        self.doc.elements = elements
        for key, element in elements.items():
            element.__dict__.clear()
            element.__dict__.update(states[key])

    def HasStarted(self):
        return self.started

    def HasEnded(self):
        return self.ended


class Doc:
    def __init__(self):
        self.elements, self.next_id = {}, 100

    def add(self, element):
        self.next_id += 1
        element_id = Eid(self.next_id)
        self.elements[element_id] = element
        return element_id

    def GetElement(self, element_id):
        return self.elements.get(element_id)


def _name(element):
    if element._name is None:
        raise AttributeError("Name")
    return element._name


@pytest.fixture
def sheets(monkeypatch):
    DB = SimpleNamespace(
        Element=SimpleNamespace(Name=SimpleNamespace(GetValue=_name)),
        View=View, ViewSheet=ViewSheet, ViewSchedule=ViewSchedule, Viewport=Viewport,
        ScheduleSheetInstance=ScheduleSheetInstance, XYZ=XYZ,
        FilteredElementCollector=Collector, Transaction=Transaction,
        TransactionStatus=SimpleNamespace(Committed="Committed"),
        BuiltInCategory=SimpleNamespace(OST_TitleBlocks="tb"),
        BuiltInParameter=SimpleNamespace(SHEET_NAME="SHEET_NAME"),
        ElementId=Eid)
    DB.ElementId.InvalidElementId = INVALID
    pyrevit = SimpleNamespace(DB=DB, routes=SimpleNamespace(make_response=lambda **kw: kw),
                              forms=SimpleNamespace(alert=lambda *a, **k: True))
    monkeypatch.setitem(sys.modules, "pyrevit", pyrevit)
    for name, value in (("long", int), ("basestring", str), ("unicode", str)):
        monkeypatch.setattr(builtins, name, value, raising=False)
    for name in MODULES:
        sys.modules.pop(name, None)
    module = importlib.import_module("revit_mcp.sheets")
    Transaction.log = []
    yield module
    for name in MODULES:
        sys.modules.pop(name, None)


@pytest.fixture
def doc():
    doc = Doc()
    doc.a1 = TitleSymbol(doc, "Carimbo A1", u"Padrão")
    doc.a0 = TitleSymbol(doc, "A0 Folha", "Executivo")
    doc.sheet = ViewSheet(doc, "A100", "Capa", block_id=doc.a1.Id)
    doc.plan = View(doc, u"Térreo")
    doc.ceiling = View(doc, "Forro", "CeilingPlan")
    doc.dup_plan = View(doc, "Cobertura")
    doc.dup_ceiling = View(doc, "Cobertura", "CeilingPlan")
    doc.legend = View(doc, "Legenda", "Legend")
    doc.schedule = ViewSchedule(doc, "Portas")
    doc.template = View(doc, "Planta Executivo", template=True)
    doc.ceiling_template = View(doc, "Forro Executivo", "CeilingPlan", template=True)
    return doc


def count(doc, cls):
    return sum(1 for e in doc.elements.values() if type(e) is cls)


# ---- pure helpers ----

@pytest.mark.parametrize("text, expected", [
    ("A1 : Padrão", ("A1", "Padrão")), ("A1:B", ("A1", "B")), (" Padrão ", (None, "Padrão"))])
def test_split_title_block(sheets, text, expected):
    assert sheets.split_title_block(text) == expected


def test_duplicate_indexes_and_keys(sheets):
    keys = [sheets.name_key(n) for n in ["A101", "a101 ", "A102", None]]
    assert sheets.duplicate_indexes(keys) == [1]
    assert sheets.name_key(None) == ""
    assert sheets.meters_to_feet(0.3048) == pytest.approx(1.0)


@pytest.mark.parametrize("item, ok", [
    ({"sheet_number": "A1", "view": "x"}, True),
    ({"sheet_number": "A1", "view_id": 5, "x": 0, "y": 1.5}, True),
    ({"sheet_number": "A1", "view": "x", "view_id": 5}, False),
    ({"sheet_number": "A1", "view_id": True}, False),
    ({"sheet_number": "A1", "view": "x", "x": 1}, False),
    ({"sheet_number": "", "view": "x"}, False),
    ("x", False),
])
def test_validate_placement(sheets, item, ok):
    assert (sheets.validate_placement(item) is None) is ok


# ---- create_sheets ----

def test_create_sheets_commit_uses_first_title_block(sheets, doc):
    result = sheets.create_sheets(doc, [{"number": "A101", "name": u"Planta Térreo"},
                                        {"number": "A102", "name": "Forro"}], None, commit=True)
    assert result["status"] == "success" and result["applied"] == 2, result
    assert result["title_block"]["family"] == "A0 Folha"   # alphabetical first
    created = [e for e in doc.elements.values() if isinstance(e, ViewSheet) and e._number != "A100"]
    assert sorted(s._number for s in created) == ["A101", "A102"]
    assert result["results"][0]["name"] == u"Planta Térreo" and "sheet_id" in result["results"][0]
    assert Transaction.log == ["commit"]


def test_create_sheets_dry_run_rolls_back(sheets, doc):
    before = len(doc.elements)
    result = sheets.create_sheets(doc, [{"number": "A101", "name": "X"}], "carimbo a1", commit=False)
    assert result["status"] == "dry_run", result
    assert result["title_block"]["type"] == u"Padrão"
    assert "sheet_id" not in result["results"][0]
    assert len(doc.elements) == before and Transaction.log == ["rollback"]


def test_create_sheets_duplicates_open_no_transaction(sheets, doc):
    result = sheets.create_sheets(doc, [{"number": "A101", "name": "X"},
                                        {"number": "a101", "name": "Y"},
                                        {"number": "A100", "name": "Z"}], None, commit=True)
    assert result["status"] == "error" and result["applied"] == 0
    assert [(e["index"], e["error"]) for e in result["errors"]] == [
        (1, "Sheet number repeated in this batch"), (2, "A sheet with this number already exists")]
    assert Transaction.log == []


def test_create_sheets_revit_rejection_rolls_back_all(sheets, doc):
    before = len(doc.elements)
    result = sheets.create_sheets(doc, [{"number": "A101", "name": "ok"},
                                        {"number": "A102", "name": "!bad"}], None, commit=True)
    assert result["status"] == "error" and result["errors"][0]["index"] == 1
    assert len(doc.elements) == before and Transaction.log == ["rollback"]


@pytest.mark.parametrize("requested, fragment", [
    ("Inexistente", "not found"), ("Carimbo A1 : Outro", "not found")])
def test_create_sheets_unknown_title_block(sheets, doc, requested, fragment):
    result = sheets.create_sheets(doc, [{"number": "A101", "name": "X"}], requested, commit=True)
    assert result["status"] == "error" and fragment in result["error"]
    assert "Carimbo A1 : Padrão" in result["available"]


def test_create_sheets_ambiguous_title_block(sheets, doc):
    TitleSymbol(doc, "Carimbo A1", "Outro")
    result = sheets.create_sheets(doc, [{"number": "A101", "name": "X"}], "Carimbo A1", commit=True)
    assert "2 title blocks" in result["error"] and len(result["matches"]) == 2


def test_create_sheets_without_title_blocks(sheets):
    empty = Doc()
    result = sheets.create_sheets(empty, [{"number": "1", "name": "X"}], None, commit=True)
    assert result["status"] == "success" and result["title_block"] is None


# ---- place_views_on_sheets ----

def test_place_views_commit_centres_on_title_block(sheets, doc):
    result = sheets.place_views(doc, [
        {"sheet_number": "a100", "view": u"térreo"},
        {"sheet_number": "A100", "view_id": int(doc.schedule.Id), "x": 0.3048, "y": 0.6096},
    ], commit=True)
    assert result["status"] == "success", result
    first, second = result["results"]
    assert first["kind"] == "viewport" and first["position_m"] == {
        "x": round(1.4 * 0.3048, 4), "y": round(1.0 * 0.3048, 4)}
    assert second["kind"] == "schedule" and second["position_m"] == {"x": 0.3048, "y": 0.6096}
    assert count(doc, Viewport) == 1 and count(doc, ScheduleSheetInstance) == 1
    assert Transaction.log == ["commit"]


def test_place_views_dry_run_rolls_back(sheets, doc):
    result = sheets.place_views(doc, [{"sheet_number": "A100", "view": "Forro"}], commit=False)
    assert result["status"] == "dry_run" and "viewport_id" not in result["results"][0]
    assert count(doc, Viewport) == 0 and Transaction.log == ["rollback"]


def test_place_views_errors(sheets, doc):
    Viewport(doc, doc.sheet.Id, doc.ceiling.Id, XYZ(0, 0, 0))   # Forro already placed
    result = sheets.place_views(doc, [
        {"sheet_number": "X9", "view": "Forro"},
        {"sheet_number": "A100", "view": "Cobertura"},
        {"sheet_number": "A100", "view": "Nada"},
        {"sheet_number": "A100", "view": "Forro"},
        {"sheet_number": "A100", "view_id": int(doc.template.Id)},
        {"sheet_number": "A100", "view": u"Térreo"},
        {"sheet_number": "A100", "view": u"Térreo"},
        {"sheet_number": "A100", "view": "Legenda"},
        {"sheet_number": "A100", "view": "Legenda"},
    ], commit=True)
    errors = {e["index"]: e["error"] for e in result["errors"]}
    assert errors[0] == "Sheet not found"
    assert "2 views named 'Cobertura'" in errors[1] and "view_id" in errors[1]
    assert errors[2] == "View not found"
    assert "cannot be added" in errors[3]
    assert errors[4] == "View is a view template"
    assert "placed twice" in errors[6]
    assert set(errors) == {0, 1, 2, 3, 4, 6}   # legends may repeat
    assert Transaction.log == []


# ---- apply_view_template ----

def test_apply_template_by_names_and_ids(sheets, doc):
    doc.dup_plan.ViewTemplateId = doc.template.Id
    result = sheets.apply_template(doc, "planta executivo",
                                   [u"Térreo", int(doc.dup_plan.Id)], None, commit=True)
    assert result["status"] == "success", result
    assert [(r["before"], r["after"]) for r in result["results"]] == [
        (None, "Planta Executivo"), ("Planta Executivo", "Planta Executivo")]
    assert doc.plan.ViewTemplateId == doc.template.Id
    assert result["template"]["name"] == "Planta Executivo"


def test_apply_template_by_view_type_dry_run(sheets, doc):
    result = sheets.apply_template(doc, "Forro Executivo", None, "ceilingplan", commit=False)
    assert result["status"] == "dry_run", result
    assert sorted(r["view"] for r in result["results"]) == ["Cobertura", "Forro"]
    assert doc.ceiling.ViewTemplateId == INVALID and Transaction.log == ["rollback"]


def test_apply_template_errors_apply_nothing(sheets, doc):
    result = sheets.apply_template(doc, "Planta Executivo", [
        u"Térreo", "Forro", int(doc.ceiling_template.Id), "Nada", u"térreo"],
        None, commit=True)
    errors = {e["index"]: e["error"] for e in result["errors"]}
    assert "view type mismatch" in errors[1]
    assert errors[2] == "View is a view template"
    assert errors[3] == "View not found"
    assert errors[4] == "View repeated in this batch"
    assert 0 not in errors
    assert doc.plan.ViewTemplateId == INVALID and Transaction.log == []


def test_apply_template_missing_template_or_type(sheets, doc):
    assert "not found" in sheets.apply_template(doc, "Nope", ["Forro"], None, True)["error"]
    assert "No views of type" in sheets.apply_template(doc, "Planta Executivo", None,
                                                       "Walkthrough", True)["error"]


# ---- routes: authorization order ----

class Api:
    def __init__(self):
        self.handlers = {}

    def route(self, path, methods=None):
        def decorator(function):
            self.handlers[path] = function
            return function
        return decorator


@pytest.fixture
def routes(sheets, monkeypatch):
    calls = []

    def authorize(request, action=None):
        calls.append(action)
        return None

    monkeypatch.setattr(sheets, "authorize_request", authorize)
    api = Api()
    sheets.register_sheet_routes(api)
    return api.handlers, calls


def test_dry_run_route_never_opens_the_review_dialog(routes, doc):
    handlers, calls = routes
    request = SimpleNamespace(data={"sheets": [{"number": "A101", "name": "X"}]})
    response = handlers["/create_sheets/"](doc, request)
    assert response["data"]["status"] == "dry_run" and calls == [None]


@pytest.mark.parametrize("path, data, action", [
    ("/create_sheets/", {"sheets": [{"number": "A101", "name": "X"}]}, "Create sheets"),
    ("/place_views_on_sheets/", {"placements": [{"sheet_number": "A100", "view": "Forro"}]},
     "Place views on sheets"),
    ("/apply_view_template/", {"template": "Planta Executivo", "views": ["Cobertura"]},
     "Apply view template"),
])
def test_real_run_asks_for_review(routes, doc, path, data, action):
    handlers, calls = routes
    response = handlers[path](doc, SimpleNamespace(data=dict(data, dry_run=False)))
    assert calls == [None, action]
    assert response["data"]["status"] in ("success", "error"), response


@pytest.mark.parametrize("path, data", [
    ("/create_sheets/", {"sheets": []}),
    ("/create_sheets/", {"sheets": [{"number": "A1", "name": "X"}] * 201}),
    ("/create_sheets/", {"sheets": [{"number": "A1", "name": "X"}], "title_block": 3}),
    ("/place_views_on_sheets/", {"placements": [{"sheet_number": "A1"}]}),
    ("/apply_view_template/", {"template": "T"}),
    ("/apply_view_template/", {"template": "T", "views": ["a"], "view_type": "FloorPlan"}),
    ("/apply_view_template/", {"template": "", "views": ["a"]}),
])
def test_bad_payload_is_400_without_review(routes, doc, path, data):
    handlers, calls = routes
    response = handlers[path](doc, SimpleNamespace(data=dict(data, dry_run=False)))
    assert response["status"] == 400 and calls == [None]


def test_no_document_is_503(routes):
    handlers, _ = routes
    response = handlers["/create_sheets/"](None, SimpleNamespace(data={}))
    assert response["status"] == 503
