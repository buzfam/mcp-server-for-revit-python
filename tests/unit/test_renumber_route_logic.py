# -*- coding: utf-8 -*-
"""Route logic of revit_mcp/renumber.py (spec 018) against a fake Revit API:
pure helpers (natural sort, pattern, reading order, grouping, clashes), target
selection, room lookup through ToRoom/FromRoom, swaps of unique numbers via
temporary values, all-or-nothing and dry-run rollback. The module is IronPython
code; py2 builtins are injected for CPython."""
import builtins
import importlib
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

LOG = []
FT = 1 / 0.3048


# ---- fake Revit API ----

class FakeId:
    def __init__(self, value):
        self.Value = value

    def __eq__(self, other):
        return isinstance(other, FakeId) and other.Value == self.Value

    def __hash__(self):
        return hash(self.Value)


INVALID = FakeId(-1)


class FakeParameter:
    def __init__(self, owner, builtin, value=u"", read_only=False, storage="String"):
        self.owner, self.value, self.IsReadOnly, self.StorageType = owner, value, read_only, storage
        self.Definition = SimpleNamespace(BuiltInParameter=builtin)
        self.builtin = builtin

    def AsString(self):
        return self.value

    def AsElementId(self):
        return self.value

    def Set(self, value):
        doc = self.owner.doc
        if self.builtin == "SHEET_NUMBER":
            for other in doc.elements.values():
                if other is not self.owner and isinstance(other, ViewSheet) and \
                        other.params["SHEET_NUMBER"].value.lower() == value.lower():
                    raise Exception("Sheet number is already in use")
        if value in doc.rejected:
            return False
        LOG.append(("set", self.owner.Id.Value, value))
        self.value = value
        return True


class Element:
    def __init__(self, doc, name=None, category=None, level=None, point=None, **attrs):
        self.doc = doc
        self.Id = doc.add(self)
        self.Name = name
        self.category = category
        self.LevelId = level.Id if level is not None else INVALID
        self.Location = SimpleNamespace(Point=SimpleNamespace(X=point[0] * FT, Y=point[1] * FT)) \
            if point is not None else None
        self.params = {}
        self.named = {}
        self.__dict__.update(attrs)

    def param(self, builtin, value=u"", label=None, **kw):
        parameter = FakeParameter(self, builtin, value, **kw)
        self.params[builtin] = parameter
        if label:
            self.named.setdefault(label, []).append(parameter)
        return parameter

    def get_Parameter(self, builtin):
        return self.params.get(builtin)

    def GetParameters(self, name):
        return self.named.get(name, [])

    def get_BoundingBox(self, view):
        return None


class Level(Element):
    pass


class View(Element):
    IsTemplate = False
    ViewType = "FloorPlan"


class Phase(Element):
    pass


class ViewSheet(Element):
    IsPlaceholder = False


class Room(Element):
    Area = 10.0


class Door(Element):
    def get_ToRoom(self, phase):
        LOG.append(("ToRoom", phase))
        return self.to_room

    def get_FromRoom(self, phase):
        return self.from_room


class FakeDoc:
    def __init__(self):
        self.elements = {}
        self.Phases = []
        self.rejected = set()
        self.failures = []
        self.Settings = SimpleNamespace(Categories=[])

    def add(self, element):
        new_id = FakeId(100 + len(self.elements))
        self.elements[new_id.Value] = element
        return new_id

    def GetElement(self, element_id):
        value = element_id.Value if isinstance(element_id, FakeId) else element_id
        return self.elements.get(value)


class FilteredElementCollector:
    def __init__(self, doc, view_id=None):
        self.items = list(doc.elements.values())
        if view_id is not None:
            view = doc.GetElement(view_id)
            self.items = [e for e in self.items if e.Id.Value in view.visible]

    def OfClass(self, cls):
        self.items = [e for e in self.items if isinstance(e, cls)]
        return self

    def OfCategory(self, category):
        self.items = [e for e in self.items if e.category == category]
        return self

    def OfCategoryId(self, category_id):
        self.items = [e for e in self.items if e.category == category_id.Value]
        return self

    def WhereElementIsNotElementType(self):
        return self

    def ToElements(self):
        return self.items


class Transaction:
    def __init__(self, doc, name):
        self.doc, self.name, self.started, self.ended = doc, name, False, False
        self.options = SimpleNamespace(
            SetFailuresPreprocessor=lambda p: setattr(self, "preprocessor", p),
            SetClearAfterRollback=lambda value: None)

    def GetFailureHandlingOptions(self):
        return self.options

    def SetFailureHandlingOptions(self, options):
        pass

    def Start(self):
        self.started = True
        self.snapshot = dict((key, e.params and {b: p.value for b, p in e.params.items()})
                             for key, e in self.doc.elements.items())

    def restore(self):
        for key, values in self.snapshot.items():
            for builtin, value in (values or {}).items():
                self.doc.elements[key].params[builtin].value = value

    def Commit(self):
        self.ended = True
        if self.doc.failures:
            accessor = SimpleNamespace(
                GetFailureMessages=lambda: [
                    SimpleNamespace(GetSeverity=lambda s=s: s, GetDescriptionText=lambda t=t: t)
                    for s, t in self.doc.failures],
                DeleteWarning=lambda message: None)
            if self.preprocessor.PreprocessFailures(accessor) == "ProceedWithRollBack":
                self.restore()
                LOG.append("t.rollback")
                return "RolledBack"
        LOG.append("t.commit")
        return "Committed"

    def RollBack(self):
        self.ended = True
        self.restore()
        LOG.append("t.rollback")

    def HasStarted(self):
        return self.started

    def HasEnded(self):
        return self.ended


class TransactionGroup(Transaction):
    def Assimilate(self):
        self.ended = True
        LOG.append("group.assimilate")

    def RollBack(self):
        self.ended = True
        self.restore()
        LOG.append("group.rollback")


class BuiltInParameter:
    INVALID = "INVALID"

    def __getattr__(self, name):
        return name


@pytest.fixture
def renumber(monkeypatch):
    DB = SimpleNamespace(
        ElementId=SimpleNamespace(InvalidElementId=INVALID),
        Element=SimpleNamespace(Name=SimpleNamespace(GetValue=lambda e: e.Name)),
        FilteredElementCollector=FilteredElementCollector,
        Transaction=Transaction, TransactionGroup=TransactionGroup,
        TransactionStatus=SimpleNamespace(Committed="Committed"),
        IFailuresPreprocessor=object,
        FailureSeverity=SimpleNamespace(Warning="Warning", Error="Error"),
        FailureProcessingResult=SimpleNamespace(Continue="Continue",
                                                ProceedWithRollBack="ProceedWithRollBack"),
        BuiltInParameter=BuiltInParameter(),
        BuiltInCategory=SimpleNamespace(OST_Doors="doors", OST_Windows="windows",
                                        OST_Rooms="rooms"),
        StorageType=SimpleNamespace(String="String", Double="Double"),
        Level=Level, View=View, ViewSheet=ViewSheet,
    )
    pyrevit = SimpleNamespace(DB=DB, routes=SimpleNamespace(make_response=lambda **kw: kw),
                              forms=SimpleNamespace(alert=lambda *a, **k: True))
    monkeypatch.setitem(sys.modules, "pyrevit", pyrevit)
    for name in ("long", "basestring", "unicode"):
        monkeypatch.setattr(builtins, name, {"long": int, "basestring": str, "unicode": str}[name],
                            raising=False)
    names = ("revit_mcp.utils", "revit_mcp.security", "revit_mcp.parameters",
             "revit_mcp.modeling", "revit_mcp.transactions", "revit_mcp.renumber")
    for name in names:
        sys.modules.pop(name, None)
    module = importlib.import_module("revit_mcp.renumber")
    LOG.clear()
    yield module
    for name in names:
        sys.modules.pop(name, None)


def make_room(doc, level, number, name, point, phase=None):
    room = Room(doc, category="rooms", level=level, point=point)
    room.param("ROOM_NUMBER", number)
    room.param("ROOM_NAME", name)
    room.param("ROOM_PHASE", (phase or doc.new).Id)
    return room


def make_door(doc, level, point, to_room=None, from_room=None, mark=u"", category="doors"):
    door = Door(doc, category=category, level=level, point=point,
                to_room=to_room, from_room=from_room)
    door.param("ALL_MODEL_MARK", mark, label="Marca")
    return door


def make_sheet(doc, number, name="Planta"):
    sheet = ViewSheet(doc, name=name)
    sheet.param("SHEET_NUMBER", number)
    return sheet


@pytest.fixture
def doc():
    d = FakeDoc()
    d.l0 = Level(d, u"Térreo", Elevation=0.0)
    d.l1 = Level(d, "Pavimento 1", Elevation=3.0 * FT)
    d.old = Phase(d, "Existente")
    d.new = Phase(d, u"Nova construção")
    d.Phases = [d.old, d.new]
    return d


def options(module, **data):
    result, error = module.validate_request(data)
    assert error is None, error
    return result


def run(module, doc, authorize=None, **data):
    return module.renumber(doc, options(module, **data), authorize or (lambda: None))


def values(doc, builtin):
    return {key: e.params[builtin].value for key, e in doc.elements.items()
            if builtin in e.params}


# ---- pure helpers ----

def test_natural_key(renumber):
    items = ["A-10", "A-2", "a-1", "", "B-1", "A-2b"]
    assert sorted(items, key=renumber.natural_key) == ["a-1", "A-2", "A-2b", "A-10", "B-1", ""]
    assert renumber.natural_key(None) == renumber.natural_key("")


def test_parse_and_format_pattern(renumber):
    parts, error = renumber.parse_pattern(u"P-{room_number}-{seq:02}")
    assert error is None
    assert parts == [("text", u"P-"), ("token", "room_number", 0), ("text", u"-"),
                     ("token", "seq", 2)]
    assert renumber.format_pattern(parts, {"seq": 3, "room_number": u"101"}) == u"P-101-03"
    assert renumber.format_pattern(renumber.parse_pattern("{seq:03}")[0], {"seq": 1234}) == "1234"
    with pytest.raises(KeyError):
        renumber.format_pattern(parts, {"seq": 1})


@pytest.mark.parametrize("pattern, message", [
    ("", "non-empty"),
    ("P-{room_number}", "must contain {seq}"),
    ("{seq}{x}", "Unknown token"),
    ("{seq:2}", "Unknown token"),
    ("{seq}{", "unmatched"),
    ("}{seq}", "unmatched"),
])
def test_parse_pattern_errors(renumber, pattern, message):
    parts, error = renumber.parse_pattern(pattern)
    assert parts is None and message in error


def test_reading_order_rows_and_levels(renumber):
    records = [
        {"element_id": 1, "elevation": 3.0, "x": 0.0, "y": 10.0},
        {"element_id": 2, "elevation": 0.0, "x": 5.0, "y": 10.2},   # same row as 3
        {"element_id": 3, "elevation": 0.0, "x": 1.0, "y": 10.0},
        {"element_id": 4, "elevation": 0.0, "x": 0.0, "y": 2.0},
        {"element_id": 5, "elevation": 0.0, "x": None, "y": None},
        {"element_id": 6, "elevation": None, "x": 0.0, "y": 0.0},
    ]
    ordered = [r["element_id"] for r in renumber.reading_order(records)]
    assert ordered == [3, 2, 4, 5, 1, 6]


def test_plan_numbers_groups_restart_sequence(renumber):
    opts = options(renumber, target="doors", pattern="P-{room_number}-{seq:02}",
                   group_by="room", start=1)
    records = [
        {"element_id": 1, "elevation": 0.0, "x": 5.0, "y": 0.0, "room_id": 20,
         "room_number": "10", "current": ""},
        {"element_id": 2, "elevation": 0.0, "x": 0.0, "y": 0.0, "room_id": 21,
         "room_number": "9", "current": ""},
        {"element_id": 3, "elevation": 0.0, "x": 1.0, "y": 0.0, "room_id": 20,
         "room_number": "10", "current": ""},
    ]
    planned = [(r["element_id"], v) for r, v in renumber.plan_numbers(records, opts)]
    assert planned == [(2, "P-9-01"), (3, "P-10-01"), (1, "P-10-02")]


def test_plan_numbers_current_order_with_step(renumber):
    opts = options(renumber, target="sheets", pattern="A-{seq}", start=101, step=10)
    records = [{"element_id": 1, "current": "A-10"}, {"element_id": 2, "current": "A-2"}]
    planned = [(r["element_id"], v) for r, v in renumber.plan_numbers(records, opts)]
    assert planned == [(2, "A-101"), (1, "A-111")]


def test_find_conflicts(renumber):
    errors, warnings = renumber.find_conflicts(
        [(1, "A-1"), (2, "a-1"), (3, "A-3"), (4, " ")], ["A-3"], unique=True)
    assert [e["element_id"] for e in errors] == [4, 1, 2, 3]
    assert "'A-1' is repeated" in errors[1]["error"]  # original value, not the key
    assert warnings == []
    errors, warnings = renumber.find_conflicts([(3, "M1")], ["M1"], unique=False)
    assert errors == [] and len(warnings) == 1


def test_temp_value_avoids_taken(renumber):
    assert renumber.temp_value(7, set()) == "MCP-TMP-7"
    assert renumber.temp_value(7, {"mcp-tmp-7"}) == "MCP-TMP-7-2"


@pytest.mark.parametrize("data, message", [
    ({}, "target is required"),
    ({"target": "doors", "start": -1}, "start"),
    ({"target": "doors", "start": True}, "start"),
    ({"target": "doors", "step": 0}, "step"),
    ({"target": "doors", "order": "x"}, "order"),
    ({"target": "doors", "group_by": "x"}, "group_by"),
    ({"target": "doors", "parameter": " "}, "parameter"),
    ({"target": "doors", "view_id": "x"}, "view_id"),
    ({"target": "doors", "view_id": 1, "view_name": "A"}, "not both"),
    ({"target": "doors", "element_ids": []}, "non-empty"),
    ({"target": "doors", "element_ids": [1, "a"]}, "integers"),
    ({"target": "doors", "element_ids": list(range(201))}, "At most 200"),
    ({"target": "doors", "phase": " "}, "phase"),
    ({"target": "sheets", "phase": "Existente"}, "not available for sheets"),
    ({"target": "sheets", "order": "location"}, "no plan location"),
    ({"target": "folhas", "pattern": "{level}-{seq}"}, "no room or level"),
    ({"target": "sheets", "view_name": "A"}, "not available for sheets"),
    ({"target": "Ambientes", "group_by": "room"}, "use level for rooms"),
])
def test_validate_request_errors(renumber, data, message):
    result, error = renumber.validate_request(data)
    assert result is None and message in error


def test_validate_request_defaults(renumber):
    doors = options(renumber, target="Portas")
    assert doors["kind"] == "doors" and doors["order"] == "location"
    assert doors["pattern"] == "{prefix}{seq}" and doors["dry_run"] is True
    sheets = options(renumber, target="sheets")
    assert sheets["order"] == "current"
    assert options(renumber, target="Mobiliário")["kind"] == "category"


# ---- Revit flow ----

def test_doors_by_room_dry_run_rolls_back(renumber, doc):
    r101 = make_room(doc, doc.l0, "101", "Sala", (0, 0))
    r102 = make_room(doc, doc.l0, "102", "Quarto", (10, 0))
    d1 = make_door(doc, doc.l0, (2, 0), to_room=r101)
    d2 = make_door(doc, doc.l0, (1, 0), to_room=None, from_room=r101)
    d3 = make_door(doc, doc.l0, (9, 0), to_room=r102, mark="X")
    authorize = Mock(return_value=None)
    body, status = run(renumber, doc, authorize, target="doors",
                       pattern="P-{room_number}-{seq:02}", group_by="room")
    assert status == 200 and body["status"] == "dry_run", body
    assert [(r["element_id"], r["before"], r["after"]) for r in body["results"]] == [
        (d2.Id.Value, "", "P-101-01"), (d1.Id.Value, "", "P-101-02"),
        (d3.Id.Value, "X", "P-102-01")]
    assert body["changed"] == 3
    authorize.assert_not_called()
    assert d1.params["ALL_MODEL_MARK"].value == "" and d3.params["ALL_MODEL_MARK"].value == "X"
    assert LOG[-1] == "group.rollback"
    assert ("ToRoom", doc.new) in LOG  # last phase without a view
    assert body["phase"] == u"Nova construção"


def test_real_run_asks_approval_and_assimilates(renumber, doc):
    r101 = make_room(doc, doc.l0, "101", "Sala", (0, 0))
    d1 = make_door(doc, doc.l0, (2, 0), to_room=r101)
    authorize = Mock(return_value=None)
    body, _ = run(renumber, doc, authorize, target="doors", pattern="{room_number}{seq}",
                  dry_run=False)
    assert body["status"] == "success" and body["changed"] == 1
    authorize.assert_called_once()
    assert d1.params["ALL_MODEL_MARK"].value == "1011"
    assert LOG[-1] == "group.assimilate"


def test_denied_approval_is_returned(renumber, doc):
    make_door(doc, doc.l0, (2, 0))
    denial = {"status": 403}
    assert run(renumber, doc, lambda: denial, target="doors", dry_run=False) is denial
    assert not any(isinstance(entry, tuple) and entry[0] == "set" for entry in LOG)


def test_door_without_room_is_an_error_only_when_room_is_needed(renumber, doc):
    door = make_door(doc, doc.l0, (2, 0))
    body, _ = run(renumber, doc, target="doors", pattern="P-{room_number}-{seq}")
    assert body["status"] == "error" and body["errors"][0]["element_id"] == door.Id.Value
    assert "No room" in body["errors"][0]["error"]
    body, _ = run(renumber, doc, target="doors", pattern="P{seq:02}")
    assert body["status"] == "dry_run" and body["results"][0]["after"] == "P01"


def test_windows_grouped_by_level(renumber, doc):
    w0 = make_door(doc, doc.l0, (0, 0), category="windows")
    w1 = make_door(doc, doc.l1, (0, 0), category="windows")
    w2 = make_door(doc, doc.l1, (1, 0), category="windows")
    body, _ = run(renumber, doc, target="janelas", pattern="J{level}-{seq}", group_by="level")
    assert [(r["element_id"], r["after"]) for r in body["results"]] == [
        (w0.Id.Value, u"JTérreo-1"), (w1.Id.Value, "JPavimento 1-1"),
        (w2.Id.Value, "JPavimento 1-2")]


def test_rooms_by_level_skip_unplaced_and_check_unique(renumber, doc):
    a = make_room(doc, doc.l1, "201", "Sala", (0, 0))
    b = make_room(doc, doc.l1, "202", "Quarto", (5, 0))
    unplaced = make_room(doc, doc.l1, "301", "Depósito", None)
    unplaced.Area = 0
    make_room(doc, doc.l0, "101", "Hall", (0, 0))
    body, _ = run(renumber, doc, target="rooms", pattern="{seq}", start=400,
                  level="Pavimento 1", dry_run=False)
    assert body["status"] == "success", body
    assert body["skipped"] == {"unplaced_rooms": 1}
    assert (a.params["ROOM_NUMBER"].value, b.params["ROOM_NUMBER"].value) == ("400", "401")
    # 301 is taken by the unplaced room outside the batch: clash.
    body, _ = run(renumber, doc, target="rooms", pattern="{seq}", start=300,
                  level="Pavimento 1")
    assert body["status"] == "error" and "already used" in body["errors"][0]["error"]


def test_sheets_swap_numbers_inside_the_batch(renumber, doc):
    s1 = make_sheet(doc, "A-102")
    s2 = make_sheet(doc, "A-101")
    s3 = make_sheet(doc, "A-110")
    body, _ = run(renumber, doc, target="sheets", pattern="A-{seq}", start=101, order="id",
                  dry_run=False)
    assert body["status"] == "success", body
    assert body["unique_parameter"] is True
    assert [s.params["SHEET_NUMBER"].value for s in (s1, s2, s3)] == ["A-101", "A-102", "A-103"]
    temps = [entry for entry in LOG if isinstance(entry, tuple) and entry[2].startswith("MCP-TMP")]
    assert len(temps) == 3


def test_sheets_clash_with_sheet_outside_batch(renumber, doc):
    s1 = make_sheet(doc, "A-101")
    make_sheet(doc, "A-102")
    body, _ = run(renumber, doc, target="sheets", pattern="A-{seq}", start=102,
                  element_ids=[s1.Id.Value])
    assert body["status"] == "error" and "already used" in body["errors"][0]["error"]
    assert s1.params["SHEET_NUMBER"].value == "A-101"


def test_placeholder_sheets_are_skipped(renumber, doc):
    make_sheet(doc, "A-1")
    placeholder = make_sheet(doc, "X-1")
    placeholder.IsPlaceholder = True
    body, _ = run(renumber, doc, target="sheets", pattern="B-{seq}")
    assert body["skipped"] == {"placeholder_sheets": 1} and len(body["results"]) == 1


def test_revit_rejection_rolls_back_everything(renumber, doc):
    d1 = make_door(doc, doc.l0, (0, 0))
    d2 = make_door(doc, doc.l0, (1, 0))
    doc.rejected.add("P2")
    body, _ = run(renumber, doc, target="doors", pattern="P{seq}", dry_run=False)
    assert body["status"] == "error" and body["changed"] == 0
    assert body["errors"][0]["element_id"] == d2.Id.Value
    assert d1.params["ALL_MODEL_MARK"].value == "" and LOG[-1] == "group.rollback"


def test_revit_error_on_commit(renumber, doc):
    make_door(doc, doc.l0, (0, 0))
    doc.failures = [("Error", "Boom")]
    body, _ = run(renumber, doc, target="doors", dry_run=False)
    assert body["status"] == "error" and body["errors"] == [{"error": "Boom"}]


def test_mark_repeated_outside_batch_is_a_warning(renumber, doc):
    make_door(doc, doc.l0, (0, 0), mark="1")
    other = make_door(doc, doc.l1, (0, 0), mark="5")
    body, _ = run(renumber, doc, target="doors", level=u"Térreo", pattern="{seq}", start=5)
    assert body["status"] == "dry_run" and body["warnings"]
    assert other.params["ALL_MODEL_MARK"].value == "5"


def test_unchanged_batch_skips_transaction(renumber, doc):
    make_door(doc, doc.l0, (0, 0), mark="1")
    authorize = Mock()
    body, _ = run(renumber, doc, authorize, target="doors", pattern="{seq}", dry_run=False)
    assert body["status"] == "success" and body["changed"] == 0 and body["unchanged"] == 1
    authorize.assert_not_called()
    assert "t.commit" not in LOG


def test_element_ids_and_view_filters(renumber, doc):
    d1 = make_door(doc, doc.l0, (0, 0))
    d2 = make_door(doc, doc.l0, (1, 0))
    view = View(doc, name="Planta Térreo", visible={d2.Id.Value})
    view.param("VIEW_PHASE", "")
    body, _ = run(renumber, doc, target="doors", view_name=u"planta térreo")
    assert [r["element_id"] for r in body["results"]] == [d2.Id.Value]
    body, _ = run(renumber, doc, target="doors", element_ids=[d1.Id.Value, 99999])
    assert body["status"] == "error" and body["errors"][0]["element_id"] == 99999
    with pytest.raises(renumber.RenumberError):
        run(renumber, doc, target="doors", view_name="Nope")
    with pytest.raises(renumber.RenumberError):
        run(renumber, doc, target="doors", level="Nope")


def test_category_target_and_named_parameter(renumber, doc):
    doc.Settings.Categories = [SimpleNamespace(Name=u"Mobiliário", Id=FakeId("furniture"),
                                               BuiltInCategory="OST_Furniture")]
    chair = Element(doc, category="furniture", level=doc.l0, point=(0, 0))
    chair.param("ALL_MODEL_MARK", "", label="Marca")
    chair.param("CODE", "", label="Código")
    body, _ = run(renumber, doc, target="Furniture", parameter=u"Código", pattern="M{seq:03}")
    assert body["status"] == "dry_run" and body["results"][0]["after"] == "M001"
    assert body["unique_parameter"] is False
    with pytest.raises(renumber.RenumberError):
        run(renumber, doc, target="Nada")


def test_read_only_or_numeric_parameter_is_an_error(renumber, doc):
    door = make_door(doc, doc.l0, (0, 0))
    door.param("LOCKED", "", label="Travado", read_only=True)
    door.param("WIDTH", "", label="Largura", storage="Double")
    body, _ = run(renumber, doc, target="doors", parameter="Travado")
    assert "read-only" in body["errors"][0]["error"]
    body, _ = run(renumber, doc, target="doors", parameter="Largura")
    assert "not a text parameter" in body["errors"][0]["error"]


def test_too_many_elements(renumber, doc, monkeypatch):
    monkeypatch.setattr(renumber, "MAX_ITEMS", 1)
    make_door(doc, doc.l0, (0, 0))
    make_door(doc, doc.l0, (1, 0))
    with pytest.raises(renumber.RenumberError):
        run(renumber, doc, target="doors")


def test_route_flow(renumber, doc, monkeypatch):
    routes = {}
    api = SimpleNamespace(route=lambda path, methods: lambda f: routes.setdefault(path, f))
    calls = []
    monkeypatch.setattr(renumber, "authorize_request",
                        lambda request, action=None: calls.append(action))
    renumber.register_renumber_routes(api)
    handler = routes["/renumber_elements/"]
    make_door(doc, doc.l0, (0, 0))
    request = SimpleNamespace(data=json.dumps({"target": "doors", "dry_run": False}))
    response = handler(doc, request)
    assert response["data"]["status"] == "success" and calls == [None, "Renumber elements"]
    bad = handler(doc, SimpleNamespace(data={"target": "doors", "pattern": "x"}))
    assert bad["status"] == 400
    assert handler(None, request)["status"] == 503
    missing = handler(doc, SimpleNamespace(data={"target": "doors", "level": "Nope"}))
    assert missing["status"] == 400


def test_results_list_only_changed_elements(renumber, doc):
    make_door(doc, doc.l0, (0, 0), mark="1")
    second = make_door(doc, doc.l0, (1, 0), mark="9")
    body, _ = run(renumber, doc, target="doors", pattern="{seq}")
    assert body["unchanged"] == 1 and body["changed"] == 1
    assert body["results"] == [{"element_id": second.Id.Value, "before": "9", "after": "2",
                                "level": u"Térreo"}]


def test_rooms_are_scoped_to_the_phase(renumber, doc):
    a = make_room(doc, doc.l0, "1", "Sala", (0, 0))
    make_room(doc, doc.l0, "101", "Sala antiga", (0, 0), phase=doc.old)
    make_room(doc, doc.l1, "102", "Quarto", (0, 0))
    body, _ = run(renumber, doc, target="rooms", pattern="{seq}", start=101, level=u"Térreo")
    # 101 is used only in the other phase: no clash; the old room is skipped.
    assert body["status"] == "dry_run", body
    assert body["skipped"] == {"other_phase_rooms": 1} and body["phase"] == u"Nova construção"
    assert body["results"][0]["after"] == "101"
    # 102 is used in the same phase on another level: clash.
    body, _ = run(renumber, doc, target="rooms", pattern="{seq}", start=102, level=u"Térreo")
    assert body["status"] == "error" and "'102' is already used" in body["errors"][0]["error"]
    # phase argument by name renumbers the old room instead.
    body, _ = run(renumber, doc, target="rooms", pattern="{seq}", start=1, phase="existente")
    assert body["phase"] == "Existente" and body["results"][0]["before"] == "101"
    assert a.params["ROOM_NUMBER"].value == "1"
    with pytest.raises(renumber.RenumberError):
        run(renumber, doc, target="rooms", phase="Demolição")


def test_room_of_another_phase_listed_by_id_is_an_error(renumber, doc):
    old = make_room(doc, doc.l0, "5", "Sala", (0, 0), phase=doc.old)
    body, _ = run(renumber, doc, target="rooms", element_ids=[old.Id.Value])
    assert body["status"] == "error" and "not in phase" in body["errors"][0]["error"]


def test_view_phase_and_phase_argument_for_doors(renumber, doc):
    room = make_room(doc, doc.l0, "7", "Sala", (0, 0), phase=doc.old)
    door = make_door(doc, doc.l0, (0, 0), to_room=room)
    view = View(doc, name="Existente", visible={door.Id.Value})
    view.param("VIEW_PHASE", doc.old.Id)
    body, _ = run(renumber, doc, target="doors", pattern="{room_number}-{seq}",
                  view_name="Existente")
    assert body["phase"] == "Existente" and ("ToRoom", doc.old) in LOG
    LOG.clear()
    body, _ = run(renumber, doc, target="doors", pattern="{room_number}-{seq}",
                  view_name="Existente", phase=u"Nova construção")
    assert body["phase"] == u"Nova construção" and ("ToRoom", doc.new) in LOG


def test_door_falls_back_to_its_creation_phase(renumber, doc):
    room = make_room(doc, doc.l0, "8", "Sala", (0, 0), phase=doc.old)

    class OldDoor(Door):
        def get_ToRoom(self, phase):
            return room if phase is doc.old else None

        def get_FromRoom(self, phase):
            return None

    door = OldDoor(doc, category="doors", level=doc.l0, point=(0, 0), CreatedPhaseId=doc.old.Id)
    door.param("ALL_MODEL_MARK", "")
    body, _ = run(renumber, doc, target="doors", pattern="{room_number}-{seq}")
    assert body["status"] == "dry_run", body
    assert body["results"][0]["after"] == "8-1" and body["results"][0]["room_phase"] == "Existente"
    # An explicit phase does not fall back.
    body, _ = run(renumber, doc, target="doors", pattern="{room_number}-{seq}",
                  phase=u"Nova construção")
    assert body["status"] == "error"


def test_elements_in_model_groups_are_errors(renumber, doc):
    group = Element(doc, name="Grupo")
    door = make_door(doc, doc.l0, (0, 0))
    door.GroupId = group.Id
    body, _ = run(renumber, doc, target="doors")
    assert body["status"] == "error" and "model group" in body["errors"][0]["error"]
    assert not any(isinstance(entry, tuple) and entry[0] == "set" for entry in LOG)


@pytest.mark.parametrize("view_type", ["Schedule", "DrawingSheet", "Legend", "DraftingView"])
def test_non_graphical_views_are_rejected(renumber, doc, view_type):
    make_door(doc, doc.l0, (0, 0))
    View(doc, name="Tabela", ViewType=view_type, visible=set())
    with pytest.raises(renumber.RenumberError) as error:
        run(renumber, doc, target="doors", view_name="Tabela")
    assert "does not show model elements" in error.value.text
