# -*- coding: utf-8 -*-
"""Route logic of revit_mcp/modeling.py (spec 011) against a fake Revit API:
geometry helpers, input validation, all-or-nothing batches, dry-run rollback
through a TransactionGroup and failure handling. The module is IronPython
code; py2 builtins are injected for CPython."""
import builtins
import importlib
import math
import sys
from types import SimpleNamespace

import pytest

LOG = []


# ---- fake Revit API ----

class FakeId:
    def __init__(self, value):
        self.Value = value

    def __eq__(self, other):
        return isinstance(other, FakeId) and other.Value == self.Value

    def __hash__(self):
        return hash(self.Value)


class XYZ:
    def __init__(self, x, y, z):
        self.X, self.Y, self.Z = x, y, z

    def DistanceTo(self, other):
        return math.dist((self.X, self.Y, self.Z), (other.X, other.Y, other.Z))


XYZ.BasisZ = XYZ(0, 0, 1)


class UV:
    def __init__(self, u, v):
        self.U, self.V = u, v


class Line:
    def __init__(self, a, b):
        self.a, self.b = a, b
        self.Length = a.DistanceTo(b)

    @staticmethod
    def CreateBound(a, b):
        return Line(a, b)

    def GetEndPoint(self, index):
        return self.a if index == 0 else self.b

    def Tessellate(self):
        return [self.a, self.b]

    def Evaluate(self, t, normalized):
        return XYZ(self.a.X + t * (self.b.X - self.a.X), self.a.Y + t * (self.b.Y - self.a.Y), self.a.Z)

    def Project(self, p):
        dx, dy = self.b.X - self.a.X, self.b.Y - self.a.Y
        t = max(0.0, min(1.0, ((p.X - self.a.X) * dx + (p.Y - self.a.Y) * dy) / (dx * dx + dy * dy)))
        point = self.Evaluate(t, True)
        return SimpleNamespace(XYZPoint=point, Distance=point.DistanceTo(p))


class Element:
    def __init__(self, doc, name=None, **attrs):
        self.Id = doc.add(self)
        self.Name = name
        self.params = {}
        self.__dict__.update(attrs)

    def get_Parameter(self, builtin):
        return self.params.setdefault(builtin, FakeParameter())


class FakeParameter:
    def __init__(self, value=None, read_only=False):
        self.value, self.IsReadOnly = value, read_only

    def Set(self, value):
        self.value = value
        return True

    def AsString(self):
        return self.value


class Level(Element):
    pass


class WallType(Element):
    pass


class ViewPlan(Element):
    pass


class FamilySymbol(Element):
    pass


class Wall(Element):
    @staticmethod
    def Create(doc, curve, type_id, level_id, height, offset, flip, structural):
        LOG.append(("Wall.Create", round(curve.Length, 4), type_id.Value, level_id.Value,
                    round(height, 4), structural))
        return Wall(doc, Location=SimpleNamespace(Curve=curve), LevelId=level_id)


class Room(Element):
    pass


class FakeCreate:
    def __init__(self, doc):
        self.doc = doc

    def NewRoom(self, level, uv):
        area = self.doc.area_at(uv)
        return Room(self.doc, Area=area, uv=uv)

    def NewRoomTag(self, link_id, uv, view_id):
        LOG.append(("NewRoomTag", view_id.Value))
        return Element(self.doc)

    def NewRoomBoundaryLines(self, sketch, curves, view):
        LOG.append(("NewRoomBoundaryLines", len(curves), view.Id.Value))
        self.doc.enclosed.append([(c.a.X, c.a.Y) for c in curves])
        return [Element(self.doc) for _ in curves]

    def NewFamilyInstance(self, point, symbol, host, level, structural):
        LOG.append(("NewFamilyInstance", symbol.Name, host.Id.Value))
        return Element(self.doc, Host=host if symbol.hosted else None, CanFlipHand=True,
                       CanFlipFacing=True, flipHand=lambda: LOG.append("flipHand"),
                       flipFacing=lambda: LOG.append("flipFacing"))


class FakeDoc:
    def __init__(self):
        self.elements = {}
        self.Create = FakeCreate(self)
        self.enclosed = []  # polygons in feet that make rooms with area
        self.failures = []  # (severity, text) posted on Commit
        self.ActiveView = None
        self.default_wall_type = None
        self.existing_room = None

    def add(self, element):
        new_id = FakeId(1000 + len(self.elements))
        self.elements[new_id.Value] = element
        return new_id

    def GetElement(self, element_id):
        value = element_id.Value if isinstance(element_id, FakeId) else element_id
        return self.elements.get(value)

    def Regenerate(self):
        LOG.append("regenerate")

    def GetDefaultElementTypeId(self, group):
        return self.default_wall_type.Id if self.default_wall_type else None

    def GetRoomAtPoint(self, point):
        return self.existing_room

    def area_at(self, uv):
        for polygon in self.enclosed:
            xs = [p[0] for p in polygon]
            ys = [p[1] for p in polygon]
            if min(xs) < uv.U < max(xs) and min(ys) < uv.V < max(ys):
                return (max(xs) - min(xs)) * (max(ys) - min(ys))
        return 0.0


class FilteredElementCollector:
    def __init__(self, doc):
        self.items = list(doc.elements.values())

    def OfClass(self, cls):
        self.items = [e for e in self.items if isinstance(e, cls)]
        return self

    def OfCategory(self, category):
        self.items = [e for e in self.items if getattr(e, "category", None) == category]
        return self

    def ToElements(self):
        return self.items


class Accessor:
    def __init__(self, failures):
        self.failures = failures
        self.deleted = []

    def GetFailureMessages(self):
        return [SimpleNamespace(GetSeverity=lambda s=s: s, GetDescriptionText=lambda t=t: t)
                for s, t in self.failures]

    def DeleteWarning(self, message):
        self.deleted.append(message.GetDescriptionText())


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

    def Commit(self):
        self.ended = True
        if self.doc.failures:
            result = self.preprocessor.PreprocessFailures(Accessor(self.doc.failures))
            if result == "ProceedWithRollBack":
                LOG.append("t.rollback")
                return "RolledBack"
        LOG.append("t.commit")
        return "Committed"

    def RollBack(self):
        self.ended = True
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
        LOG.append("group.rollback")


class BuiltInParameter:
    def __getattr__(self, name):
        return name


@pytest.fixture
def modeling(monkeypatch):
    DB = SimpleNamespace(
        XYZ=XYZ, UV=UV, Line=Line, Level=Level, WallType=WallType, Wall=Wall,
        ViewPlan=ViewPlan, FamilySymbol=FamilySymbol, ElementId=FakeId,
        Element=SimpleNamespace(Name=SimpleNamespace(GetValue=lambda e: e.Name)),
        FilteredElementCollector=FilteredElementCollector,
        Transaction=Transaction, TransactionGroup=TransactionGroup,
        TransactionStatus=SimpleNamespace(Committed="Committed"),
        IFailuresPreprocessor=object,
        FailureSeverity=SimpleNamespace(Warning="Warning", Error="Error"),
        FailureProcessingResult=SimpleNamespace(Continue="Continue",
                                                ProceedWithRollBack="ProceedWithRollBack"),
        BuiltInParameter=BuiltInParameter(),
        BuiltInCategory=SimpleNamespace(OST_Doors="doors", OST_Windows="windows"),
        ElementTypeGroup=SimpleNamespace(WallType="WallType"),
        WallUtils=SimpleNamespace(DisallowWallJoinAtEnd=lambda w, end: LOG.append(("nojoin", end))),
        Plane=SimpleNamespace(CreateByNormalAndOrigin=lambda n, o: "plane"),
        SketchPlane=SimpleNamespace(Create=lambda doc, plane: "sketch"),
        CurveArray=list, LinkElementId=lambda element_id: element_id,
        Structure=SimpleNamespace(StructuralType=SimpleNamespace(NonStructural="NonStructural")),
    )
    DB.CurveArray = type("CurveArray", (list,), {"Append": list.append})
    pyrevit = SimpleNamespace(DB=DB, routes=SimpleNamespace(make_response=lambda **kw: kw),
                              forms=SimpleNamespace(alert=lambda *a, **k: True))
    monkeypatch.setitem(sys.modules, "pyrevit", pyrevit)
    for name in ("long", "basestring", "unicode"):
        monkeypatch.setattr(builtins, name, {"long": int, "basestring": str, "unicode": str}[name],
                            raising=False)
    names = ("revit_mcp.utils", "revit_mcp.security", "revit_mcp.modeling")
    for name in names:
        sys.modules.pop(name, None)
    module = importlib.import_module("revit_mcp.modeling")
    LOG.clear()
    yield module
    for name in names:
        sys.modules.pop(name, None)


@pytest.fixture
def doc():
    d = FakeDoc()
    d.level0 = Level(d, u"Térreo", Elevation=0.0)
    d.level1 = Level(d, "Pavimento 1", Elevation=10.0)
    d.generic = WallType(d, "Generic - 200mm")
    d.brick = WallType(d, "Tijolo 15")
    d.default_wall_type = d.generic
    return d


FT = 1 / 0.3048


# ---- pure helpers ----

def test_unit_conversions(modeling):
    assert modeling.to_feet(0.3048) == pytest.approx(1.0)
    assert modeling.to_meters(1.0) == pytest.approx(0.3048)
    assert modeling.sqft_to_sqm(100) == pytest.approx(9.290304)


def test_polygon_centroid_and_area(modeling):
    square = [[0, 0], [4, 0], [4, 2], [0, 2]]
    assert modeling.polygon_area(square) == pytest.approx(8)
    assert modeling.polygon_area(list(reversed(square))) == pytest.approx(-8)
    assert modeling.polygon_centroid(square) == pytest.approx((2, 1))
    l_shape = [[0, 0], [4, 0], [4, 1], [1, 1], [1, 4], [0, 4]]
    cx, cy = modeling.polygon_centroid(l_shape)
    assert cx == pytest.approx(cy) and cx < 2
    assert modeling.polygon_centroid([[0, 0], [1, 1], [2, 2]]) == pytest.approx((1, 1))


def test_point_in_polygon(modeling):
    square = [[0, 0], [4, 0], [4, 2], [0, 2]]
    assert modeling.point_in_polygon((1, 1), square)
    assert not modeling.point_in_polygon((5, 1), square)
    u_shape = [[0, 0], [3, 0], [3, 3], [2, 3], [2, 1], [1, 1], [1, 3], [0, 3]]
    assert not modeling.point_in_polygon(modeling.polygon_centroid(u_shape), u_shape)


def test_nearest_segment(modeling):
    segments = [("a", (0, 0), (5, 0)), ("b", (5, 0), (5, 5)),
                ("arc", (0, 5), (1, 6)), ("arc", (1, 6), (2, 6.2))]
    assert modeling.nearest_segment((2, 0.3), segments, 0.5) == ("a", pytest.approx(0.3))
    assert modeling.nearest_segment((4.9, 2), segments, 0.5)[0] == "b"
    assert modeling.nearest_segment((1.5, 6.4), segments, 0.5)[0] == "arc"
    assert modeling.nearest_segment((2, 2), segments, 0.5) is None
    distance, t = modeling.point_segment_distance((-1, 0), (0, 0), (5, 0))
    assert distance == 1 and t == 0


def test_match_symbol(modeling):
    entries = [("Porta Simples", "90x210", "p90"), ("Porta Simples", "80x210", "p80"),
               ("Janela", "120x100", "j")]
    assert modeling.match_symbol(entries, "porta  simples", None) == ("p80", None)
    assert modeling.match_symbol(entries, "PORTA SIMPLES", "90X210") == ("p90", None)
    symbol, error = modeling.match_symbol(entries, "Porta Simples", "70x210")
    assert symbol is None and "80x210, 90x210" in error
    assert modeling.match_symbol(entries, "Porta Dupla")[1].endswith("is not loaded")


def test_wall_settings_precedence(modeling):
    defaults = {"level": "L1", "height_m": 3.0, "top_level": "L2", "join": False}
    assert modeling.wall_settings({"start": 0}, defaults)["top_level"] == "L2"
    own_height = modeling.wall_settings({"height_m": 2.5}, defaults)
    assert own_height["height_m"] == 2.5 and "top_level" not in own_height
    assert own_height["level"] == "L1" and own_height["join"] is False
    assert modeling.wall_settings({}, {})["height_m"] == 2.8
    assert modeling.wall_settings({}, {"height_m": 3.1})["height_m"] == 3.1


@pytest.mark.parametrize("item, message", [
    ({"start": [0, 0], "end": [5, 0]}, None),
    ({"start": [0, 0], "end": [5, 0], "location_line": "finish_interior", "join": False}, None),
    ({"start": [0, 0], "end": [0, 0.001]}, "shorter"),
    ({"start": [0, 0], "end": [5]}, "[x, y]"),
    ({"start": [0, 0], "end": [True, 0]}, "[x, y]"),
    ({"start": [0, 0], "end": [5, 0], "height_m": 0}, "height_m"),
    ({"start": [0, 0], "end": [5, 0], "location_line": "middle"}, "location_line"),
    ({"start": [0, 0], "end": [5, 0], "structural": "yes"}, "structural"),
    ({"start": [0, 0], "end": [5, 0], "wall_type": " "}, "wall_type"),
    ("wall", "object"),
])
def test_validate_wall(modeling, item, message):
    error = modeling.validate_wall(item)
    assert (error is None) if message is None else (message in error)


@pytest.mark.parametrize("item, message", [
    ({"point": [1, 1], "name": u"Sala de estar", "number": 101}, None),
    ({"boundary": [[0, 0], [3, 0], [3, 3], [0, 3]], "number": "A1"}, None),
    ({"name": "x"}, "point"),
    ({"point": [1]}, "point"),
    ({"boundary": [[0, 0], [1, 0]]}, "at least 3"),
    ({"boundary": [[0, 0], [1, 0], [2, 0]]}, "no area"),
    ({"boundary": [[0, 0], [3, 0], [3, 3], [0, 3], [0, 0]]}, "repeated"),
    ({"boundary": [[0, 0], [3, 0], [3, 3], [2, 3], [2, 1], [1, 1], [1, 3], [0, 3]]}, "concave"),
    ({"boundary": [[0, 0], [3, 0], [3, 3], [0, 3]], "point": [5, 5]}, "inside"),
    ({"point": [1, 1], "number": 1.5}, "number"),
    ({"point": [1, 1], "tag": "no"}, "tag"),
])
def test_validate_room(modeling, item, message):
    error = modeling.validate_room(item)
    assert (error is None) if message is None else (message in error)


@pytest.mark.parametrize("item, message", [
    ({"family": "Porta", "wall_id": 5, "offset_m": 1.0}, None),
    ({"family": "Janela", "point": [1, 0], "sill_height_m": 1.1, "flip_hand": True}, None),
    ({"family": "Porta", "wall_id": 5, "point": [1, 0]}, None),
    ({"family": "", "wall_id": 5, "offset_m": 1}, "family"),
    ({"family": "Porta"}, "wall_id or point"),
    ({"family": "Porta", "wall_id": 5}, "offset_m"),
    ({"family": "Porta", "wall_id": "x", "offset_m": 1}, "integer"),
    ({"family": "Porta", "wall_id": 5, "offset_m": -1}, ">= 0"),
    ({"family": "Porta", "point": [1, 0], "flip_facing": 1}, "flip_facing"),
])
def test_validate_hosted(modeling, item, message):
    error = modeling.validate_hosted(item)
    assert (error is None) if message is None else (message in error)


def test_shape_errors(modeling):
    wall = {"start": [0, 0], "end": [1, 0]}
    assert modeling.shape_errors([wall], modeling.validate_wall, "walls") is None
    assert "non-empty" in modeling.shape_errors([], modeling.validate_wall, "walls")[0]
    assert "At most 200" in modeling.shape_errors([wall] * 201, modeling.validate_wall, "walls")[0]
    message, errors = modeling.shape_errors([wall, "x"], modeling.validate_wall, "walls")
    assert errors == [{"index": 1, "error": "Each wall must be an object"}]


# ---- create_walls ----

WALLS = [{"start": [0, 0], "end": [5, 0]}, {"start": [5, 0], "end": [5, 4], "height_m": 3.0}]


def test_create_walls_dry_run_rolls_back_the_group(modeling, doc):
    result = modeling.create_walls(doc, WALLS, {}, commit=False)
    assert result["status"] == "dry_run", result
    assert all("element_id" not in r for r in result["results"])
    assert result["results"][0]["level"] == u"Térreo"
    assert result["results"][0]["wall_type"] == "Generic - 200mm"
    assert LOG[-2:] == ["t.commit", "group.rollback"]
    creates = [entry for entry in LOG if entry[0] == "Wall.Create"]
    assert creates[0][1] == pytest.approx(5 * FT, rel=1e-4)
    assert creates[0][4] == pytest.approx(2.8 * FT, rel=1e-4)
    assert creates[1][4] == pytest.approx(3.0 * FT, rel=1e-4)


def test_create_walls_commit_is_one_undo_step(modeling, doc):
    result = modeling.create_walls(doc, WALLS, {"wall_type": "tijolo 15", "join": False,
                                                "structural": True}, commit=True)
    assert result["status"] == "success" and result["created"] == 2
    assert len(result["element_ids"]) == 2
    assert LOG[-2:] == ["t.commit", "group.assimilate"]
    assert ("nojoin", 0) in LOG and ("nojoin", 1) in LOG
    assert all(entry[2] == doc.brick.Id.Value and entry[5] is True
               for entry in LOG if entry[0] == "Wall.Create")


def test_create_walls_top_level_and_location_line(modeling, doc):
    result = modeling.create_walls(doc, [{"start": [0, 0], "end": [5, 0], "top_level": "pavimento 1",
                                          "location_line": "finish_exterior"}], {}, commit=True)
    assert result["status"] == "success", result
    wall = doc.GetElement(result["element_ids"][0])
    assert wall.params["WALL_HEIGHT_TYPE"].value == doc.level1.Id
    assert wall.params["WALL_KEY_REF_PARAM"].value == 2
    assert result["results"][0]["top_level"] == "Pavimento 1"


def test_create_walls_unknown_names_open_no_transaction(modeling, doc):
    result = modeling.create_walls(doc, [
        {"start": [0, 0], "end": [5, 0], "level": "Cobertura"},
        {"start": [0, 0], "end": [5, 0], "wall_type": "Concreto"},
        {"start": [0, 0], "end": [5, 0], "level": "Pavimento 1", "top_level": u"Térreo"},
    ], {}, commit=True)
    assert result["status"] == "error" and result["created"] == 0
    errors = [e["error"] for e in result["errors"]]
    assert "Level 'Cobertura' not found" in errors[0] and u"Térreo" in errors[0]
    assert "Wall type 'Concreto' not found" in errors[1] and "Tijolo 15" in errors[1]
    assert "must be above" in errors[2]
    assert LOG == []


def test_revit_warnings_are_reported_and_errors_roll_back(modeling, doc):
    doc.failures = [("Warning", "Paredes se sobrepõem")]
    result = modeling.create_walls(doc, WALLS, {}, commit=True)
    assert result["status"] == "success"
    assert result["warnings"] == [u"Paredes se sobrepõem"]

    LOG.clear()
    doc.failures = [("Warning", "w"), ("Error", "Não é possível criar a parede")]
    result = modeling.create_walls(doc, WALLS, {}, commit=True)
    assert result["status"] == "error" and result["created"] == 0
    assert result["errors"] == [{"error": u"Não é possível criar a parede"}]
    assert LOG[-2:] == ["t.rollback", "group.rollback"]


def test_exception_in_one_item_rolls_back_all(modeling, doc, monkeypatch):
    calls = []
    original = Wall.Create

    def create(*args):
        calls.append(1)
        if len(calls) == 2:
            raise Exception("Curve too short")
        return original(*args)

    monkeypatch.setattr(modeling.DB.Wall, "Create", staticmethod(create), raising=False)
    result = modeling.create_walls(doc, WALLS, {}, commit=True)
    assert result["status"] == "error"
    assert result["errors"] == [{"index": 1, "error": "Curve too short"}]
    assert LOG[-2:] == ["t.rollback", "group.rollback"]


# ---- create_rooms ----

def test_create_rooms_by_point_reports_area_and_tags(modeling, doc):
    doc.enclosed.append([(0, 0), (4 * FT, 0), (4 * FT, 3 * FT), (0, 3 * FT)])
    doc.ActiveView = ViewPlan(doc, "Planta", IsTemplate=False, GenLevel=doc.level0)
    result = modeling.create_rooms(doc, [{"point": [1, 1], "name": u"Cozinha", "number": 3}],
                                   {}, commit=True)
    assert result["status"] == "success", result
    room = result["results"][0]
    assert room["area_m2"] == pytest.approx(12.0)
    assert room["name"] == u"Cozinha" and room["number"] == "3"
    assert "tag_id" in room
    assert ("NewRoomTag", doc.ActiveView.Id.Value) in LOG


def test_room_not_enclosed_is_an_item_error(modeling, doc):
    doc.enclosed.append([(0, 0), (4 * FT, 0), (4 * FT, 3 * FT), (0, 3 * FT)])
    result = modeling.create_rooms(doc, [{"point": [1, 1]}, {"point": [20, 20]}], {}, commit=True)
    assert result["status"] == "error"
    assert [e["index"] for e in result["errors"]] == [1]
    assert "not in an enclosed region" in result["errors"][0]["error"]
    assert LOG[-2:] == ["t.rollback", "group.rollback"]


def test_room_by_boundary_draws_separation_lines(modeling, doc):
    plan = ViewPlan(doc, "Planta", IsTemplate=False, GenLevel=doc.level0)
    result = modeling.create_rooms(doc, [{"boundary": [[0, 0], [2, 0], [2, 2], [0, 2]]}],
                                   {"tag": False}, commit=False)
    assert result["status"] == "dry_run", result
    assert ("NewRoomBoundaryLines", 4, plan.Id.Value) in LOG
    room = result["results"][0]
    assert room["point"] == [1.0, 1.0] and room["area_m2"] == pytest.approx(4.0)
    assert "separation_line_ids" not in room and "tag" not in room


def test_room_tag_skipped_outside_plan_of_level(modeling, doc):
    doc.enclosed.append([(0, 0), (4 * FT, 0), (4 * FT, 3 * FT), (0, 3 * FT)])
    doc.ActiveView = ViewPlan(doc, "Planta 1", IsTemplate=False, GenLevel=doc.level1)
    result = modeling.create_rooms(doc, [{"point": [1, 1]}], {}, commit=True)
    assert result["results"][0]["tag"].startswith("skipped")


def test_existing_room_and_missing_plan_are_rejected(modeling, doc):
    doc.existing_room = Room(doc, params={})
    result = modeling.create_rooms(doc, [{"point": [1, 1]}], {}, commit=True)
    assert "already occupies" in result["errors"][0]["error"]
    doc.existing_room = None
    result = modeling.create_rooms(doc, [{"boundary": [[0, 0], [2, 0], [2, 2]]}], {}, commit=True)
    assert "No floor plan" in result["errors"][0]["error"]
    assert LOG == []


# ---- place_hosted_families ----

def _wall(doc, start, end, level):
    line = Line(XYZ(start[0] * FT, start[1] * FT, 0), XYZ(end[0] * FT, end[1] * FT, 0))
    return Wall(doc, Location=SimpleNamespace(Curve=line), LevelId=level.Id)


def test_no_door_or_window_family_loaded(modeling, doc):
    _wall(doc, (0, 0), (5, 0), doc.level0)
    result = modeling.place_hosted_families(
        doc, [{"family": "Porta", "point": [1, 0]}], commit=True)
    assert result["status"] == "error"
    assert "Insert > Load Family" in result["errors"][0]["error"]
    assert result["loaded_door_window_families"] == []
    assert "Insert > Load Family" in result["hint"]


def test_unknown_family_lists_loaded_doors_and_windows(modeling, doc):
    FamilySymbol(doc, "80x210", category="doors", hosted=True,
                 params={"SYMBOL_FAMILY_NAME_PARAM": FakeParameter("Porta Simples")})
    _wall(doc, (0, 0), (5, 0), doc.level0)
    result = modeling.place_hosted_families(
        doc, [{"family": "Porta Dupla", "point": [1, 0]}], commit=True)
    assert "Loaded door/window families: Porta Simples" in result["errors"][0]["error"]


def _symbol(doc, family, type_name, category="doors", hosted=True):
    return FamilySymbol(doc, type_name, category=category, hosted=hosted, IsActive=False,
                        Activate=lambda: LOG.append("activate"),
                        params={"SYMBOL_FAMILY_NAME_PARAM": FakeParameter(family),
                                "SYMBOL_NAME_PARAM": FakeParameter(type_name)})


def test_point_picks_the_nearest_wall(modeling, doc):
    _symbol(doc, "Janela", "120x100", category="windows")
    south = _wall(doc, (0, 0), (5, 0), doc.level0)
    east = _wall(doc, (5, 0), (5, 4), doc.level0)
    result = modeling.place_hosted_families(doc, [
        {"family": "janela", "point": [4.8, 2.0], "sill_height_m": 1.1, "flip_facing": True},
        {"family": "Janela", "point": [2.0, 0.2]},
    ], commit=True)
    assert result["status"] == "success", result
    first, second = result["results"]
    assert first["wall_id"] == east.Id.Value and first["offset_m"] == pytest.approx(2.0)
    assert first["distance_to_wall_m"] == pytest.approx(0.2)
    assert second["wall_id"] == south.Id.Value and second["point"] == [2.0, 0.0]
    instance = doc.GetElement(first["element_id"])
    assert instance.params["INSTANCE_SILL_HEIGHT_PARAM"].value == pytest.approx(1.1 * FT)
    assert "activate" in LOG and "flipFacing" in LOG


def test_point_too_far_from_any_wall(modeling, doc):
    _symbol(doc, "Porta", "80x210")
    _wall(doc, (0, 0), (5, 0), doc.level0)
    result = modeling.place_hosted_families(doc, [{"family": "Porta", "point": [2, 1]}], commit=True)
    assert "No wall within 0.5 m" in result["errors"][0]["error"]


def test_wall_id_and_offset(modeling, doc):
    _symbol(doc, "Porta", "80x210")
    wall = _wall(doc, (0, 0), (5, 0), doc.level0)
    ok = modeling.place_hosted_families(
        doc, [{"family": "Porta", "wall_id": wall.Id.Value, "offset_m": 1.5}], commit=False)
    assert ok["status"] == "dry_run", ok
    assert ok["results"][0]["point"] == [1.5, 0.0]
    assert LOG[-2:] == ["t.commit", "group.rollback"]
    beyond = modeling.place_hosted_families(
        doc, [{"family": "Porta", "wall_id": wall.Id.Value, "offset_m": 6}], commit=False)
    assert "beyond the wall length" in beyond["errors"][0]["error"]
    not_wall = modeling.place_hosted_families(
        doc, [{"family": "Porta", "wall_id": doc.level0.Id.Value, "offset_m": 1}], commit=False)
    assert "is not a wall" in not_wall["errors"][0]["error"]


def test_non_hosted_family_is_rejected_in_revit(modeling, doc):
    _symbol(doc, "Mesa", "Padrao", category="furniture", hosted=False)
    _wall(doc, (0, 0), (5, 0), doc.level0)
    result = modeling.place_hosted_families(doc, [{"family": "Mesa", "point": [1, 0]}], commit=True)
    assert result["status"] == "error"
    assert "not wall-hosted" in result["errors"][0]["error"]
    assert LOG[-2:] == ["t.rollback", "group.rollback"]


# ---- route flow ----

class Request:
    def __init__(self, data):
        self.data = data


def test_batch_route_asks_for_approval_only_for_real_runs(modeling, doc, monkeypatch):
    actions = []
    monkeypatch.setattr(modeling, "authorize_request",
                        lambda request, action=None: actions.append(action))
    run = lambda data, items, commit: {"status": "ok", "commit": commit}
    validator = modeling.wall_validator
    wall = {"start": [0, 0], "end": [5, 0]}

    dry = modeling._batch_route(doc, Request({"walls": [wall]}), "walls", "walls",
                                "Create walls", validator, run)
    assert dry["data"]["commit"] is False and actions == []

    real = modeling._batch_route(doc, Request({"walls": [wall], "dry_run": False}), "walls",
                                 "walls", "Create walls", validator, run)
    assert real["data"]["commit"] is True and actions == ["Create walls"]

    bad = modeling._batch_route(doc, Request({"walls": [wall] * 201, "dry_run": False}),
                                "walls", "walls", "Create walls", validator, run)
    assert bad["status"] == 400 and actions == ["Create walls"]

    defaults = modeling._batch_route(
        doc, Request({"walls": [wall], "location_line": "nope"}), "walls", "walls",
        "Create walls", validator, run)
    assert defaults["status"] == 400 and "location_line" in defaults["data"]["errors"][0]["error"]

    missing = modeling._batch_route(None, Request({}), "walls", "walls", "Create walls",
                                    validator, run)
    assert missing["status"] == 503
