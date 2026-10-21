# -*- coding: utf-8 -*-
"""Route logic of revit_mcp/finish_elements.py (spec 020) against a fake Revit
API: request validation, boundary repair, room selection and skips, existing
floors/ceilings, ceiling height and the all-or-nothing dry-run batch. The
module is IronPython code; py2 builtins are injected for CPython."""
import builtins
import importlib
import math
import sys
from types import ModuleType, SimpleNamespace

import pytest

LOG = []
FT = 1 / 0.3048
MODULES = ("revit_mcp.utils", "revit_mcp.security", "revit_mcp.exports",
           "revit_mcp.modeling", "revit_mcp.quantities", "revit_mcp.finish_elements")


# ---- fake Revit API ----

class FakeId:
    def __init__(self, value):
        self.Value = value

    def __eq__(self, other):
        return isinstance(other, FakeId) and other.Value == self.Value

    def __hash__(self):
        return hash(self.Value)


INVALID = FakeId(-1)


class XYZ:
    def __init__(self, x, y, z):
        self.X, self.Y, self.Z = x, y, z


class Line:
    def __init__(self, a, b):
        self.a, self.b = a, b
        self.Length = math.dist((a.X, a.Y, a.Z), (b.X, b.Y, b.Z))

    @staticmethod
    def CreateBound(a, b):
        LOG.append(("Line.CreateBound", round(a.X, 4), round(a.Y, 4)))
        return Line(a, b)

    def GetEndPoint(self, index):
        return self.a if index == 0 else self.b


class CurveLoop:
    def __init__(self):
        self.curves = []

    def Append(self, curve):
        if self.curves:
            last = self.curves[-1].GetEndPoint(1)
            first = curve.GetEndPoint(0)
            if math.hypot(last.X - first.X, last.Y - first.Y) > 1e-6:
                raise Exception("curves are not contiguous")
        self.curves.append(curve)

    def IsOpen(self):
        a = self.curves[0].GetEndPoint(0)
        b = self.curves[-1].GetEndPoint(1)
        return math.hypot(a.X - b.X, a.Y - b.Y) > 1e-6


class NetList(list):
    def Add(self, item):
        self.append(item)


class FakeParameter:
    def __init__(self, value=None, read_only=False, accepts=True):
        self.value, self.IsReadOnly, self.accepts = value, read_only, accepts
        self.StorageType = "Double" if isinstance(value, float) else "String"

    @property
    def HasValue(self):
        return self.value is not None

    def Set(self, value):
        if self.accepts:
            self.value = value
        return self.accepts

    def AsString(self):
        return self.value if isinstance(self.value, str) else None

    def AsDouble(self):
        return self.value

    def AsElementId(self):
        return self.value if isinstance(self.value, FakeId) else INVALID


class Element:
    def __init__(self, doc, name=None, **attrs):
        self.Id = doc.add(self)
        self.Name = name
        self.params = {}
        self.__dict__.update(attrs)

    def get_Parameter(self, builtin):
        return self.params.get(builtin)

    def GetTypeId(self):
        return getattr(self, "type_id", INVALID)


class Level(Element):
    pass


class Phase(Element):
    pass


class FloorType(Element):
    pass


class CeilingType(Element):
    pass


def rectangle(x0, y0, x1, y1, z=0.0):
    """Closed rectangle as Lines (feet)."""
    points = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return [Line(XYZ(a[0], a[1], z), XYZ(b[0], b[1], z))
            for a, b in zip(points, points[1:] + points[:1])]


def loop_area(curves):
    xs = [c.GetEndPoint(0).X for c in curves]
    ys = [c.GetEndPoint(0).Y for c in curves]
    return (max(xs) - min(xs)) * (max(ys) - min(ys))


class Room(Element):
    def __init__(self, doc, number, name, level, box, height_m=2.8, placed=True, curves=None,
                 phase=None, upper_level=None, lower_offset_m=0.0):
        x0, y0, x1, y1 = box
        Element.__init__(self, doc, name, LevelId=level.Id, category="OST_Rooms",
                         Location=object() if placed else None,
                         Area=(x1 - x0) * (y1 - y0) if placed else 0.0,
                         UnboundedHeight=height_m * FT if height_m else 0.0, box=box)
        phase = phase or getattr(doc, "phase_new", None)
        self.params = {"ROOM_NUMBER": FakeParameter(number), "ROOM_NAME": FakeParameter(name),
                       "ROOM_PHASE": FakeParameter(phase.Id if phase else None),
                       "ROOM_UPPER_LEVEL": FakeParameter((upper_level or level).Id),
                       "ROOM_LOWER_OFFSET": FakeParameter(float(lower_offset_m * FT))}
        self.curves = curves if curves is not None else rectangle(*box)

    def GetBoundarySegments(self, options):
        LOG.append(("boundary", options.SpatialElementBoundaryLocation))
        return [[SimpleNamespace(GetCurve=lambda c=c: c) for c in self.curves]]

    def IsPointInRoom(self, point):
        x0, y0, x1, y1 = self.box
        return x0 < point.X < x1 and y0 < point.Y < y1


class Host(Element):
    """Floor or ceiling, existing or created by Create()."""

    def __init__(self, doc, category, type_id, level_id, curves, created=None, demolished=None):
        Element.__init__(self, doc, None, category=category, type_id=type_id, LevelId=level_id,
                         curves=curves)
        offset = "FLOOR_HEIGHTABOVELEVEL_PARAM" if category == "OST_Floors" \
            else "CEILING_HEIGHTABOVELEVEL_PARAM"
        self.params = {"HOST_AREA_COMPUTED": FakeParameter(float(loop_area(curves))),
                       offset: FakeParameter(0.0, accepts=not doc.refuse_offset)}
        # New elements go in the "active view" phase, like Revit.
        self.CreatedPhaseId = (created or doc.active_phase).Id
        self.DemolishedPhaseId = demolished.Id if demolished else INVALID

    def ArePhasesModifiable(self):
        return True

    def IsPhaseCreatedValid(self, phase_id):
        return True

    def get_BoundingBox(self, view):
        xs = [c.GetEndPoint(0).X for c in self.curves]
        ys = [c.GetEndPoint(0).Y for c in self.curves]
        return SimpleNamespace(Min=XYZ(min(xs), min(ys), 0), Max=XYZ(max(xs), max(ys), 0))


def host_class(category):
    class Kind(Host):
        @staticmethod
        def Create(doc, loops, type_id, level_id):
            LOG.append((category + ".Create", len(loops), type_id.Value, level_id.Value))
            if doc.create_error:
                raise Exception(doc.create_error)
            return Host(doc, category, type_id, level_id, loops[0].curves)
    return Kind


Floor = host_class("OST_Floors")
Ceiling = host_class("OST_Ceilings")


class FakeDoc:
    def __init__(self):
        self.elements = {}
        self.failures = []
        self.defaults = {}
        self.create_error = None
        self.refuse_offset = False
        self.Phases = []
        self.phase_existing = Phase(self, "Existente")
        self.phase_new = Phase(self, "Nova construção")
        self.Phases = [self.phase_existing, self.phase_new]
        self.active_phase = self.phase_new

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
        element = self.defaults.get(group)
        return element.Id if element is not None else None


class FilteredElementCollector:
    def __init__(self, doc):
        self.items = list(doc.elements.values())

    def OfClass(self, cls):
        self.items = [e for e in self.items if isinstance(e, cls)]
        return self

    def OfCategory(self, category):
        self.items = [e for e in self.items if getattr(e, "category", None) == category]
        return self

    def WhereElementIsNotElementType(self):
        return self

    def ToElements(self):
        return self.items

    def __iter__(self):
        return iter(self.items)


class Accessor:
    def __init__(self, failures):
        self.failures = failures

    def GetFailureMessages(self):
        return [SimpleNamespace(GetSeverity=lambda s=s: s, GetDescriptionText=lambda t=t: t)
                for s, t in self.failures]

    def DeleteWarning(self, message):
        pass


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


class BuiltInCategory:
    def __getattr__(self, name):
        return name


class CurveElement:
    pass


@pytest.fixture
def fe(monkeypatch):
    DB = SimpleNamespace(
        XYZ=XYZ, Line=Line, CurveLoop=CurveLoop, Floor=Floor, Ceiling=Ceiling,
        FloorType=FloorType, CeilingType=CeilingType, Level=Level,
        Architecture=SimpleNamespace(Room=Room), ElementId=FakeId,
        Element=SimpleNamespace(Name=SimpleNamespace(GetValue=lambda e: e.Name)),
        FilteredElementCollector=FilteredElementCollector,
        Transaction=Transaction, TransactionGroup=TransactionGroup,
        TransactionStatus=SimpleNamespace(Committed="Committed"),
        IFailuresPreprocessor=object,
        FailureSeverity=SimpleNamespace(Warning="Warning", Error="Error"),
        FailureProcessingResult=SimpleNamespace(Continue="Continue",
                                                ProceedWithRollBack="ProceedWithRollBack"),
        BuiltInParameter=BuiltInParameter(), BuiltInCategory=BuiltInCategory(),
        ElementTypeGroup=SimpleNamespace(FloorType="FloorType", CeilingType="CeilingType"),
        SpatialElementBoundaryOptions=lambda: SimpleNamespace(SpatialElementBoundaryLocation=None),
        SpatialElementBoundaryLocation=SimpleNamespace(Finish="Finish", Center="Center"),
        StorageType=SimpleNamespace(Double="Double"), Phase=Phase,
        CurveElement=CurveElement,
    )
    DB.ElementId.InvalidElementId = INVALID
    pyrevit = SimpleNamespace(DB=DB, routes=SimpleNamespace(make_response=lambda **kw: kw),
                              forms=SimpleNamespace(alert=lambda *a, **k: True))
    monkeypatch.setitem(sys.modules, "pyrevit", pyrevit)
    system = ModuleType("System")
    collections = ModuleType("System.Collections")
    generic = ModuleType("System.Collections.Generic")
    generic.List = {CurveLoop: NetList}
    monkeypatch.setitem(sys.modules, "System", system)
    monkeypatch.setitem(sys.modules, "System.Collections", collections)
    monkeypatch.setitem(sys.modules, "System.Collections.Generic", generic)
    for name in ("long", "basestring", "unicode"):
        monkeypatch.setattr(builtins, name, {"long": int, "basestring": str, "unicode": str}[name],
                            raising=False)
    for name in MODULES:
        sys.modules.pop(name, None)
    module = importlib.import_module("revit_mcp.finish_elements")
    LOG.clear()
    yield module
    for name in MODULES:
        sys.modules.pop(name, None)


@pytest.fixture
def doc():
    d = FakeDoc()
    d.level0 = Level(d, u"Térreo", ProjectElevation=0.0)
    d.level1 = Level(d, "Pavimento 1", ProjectElevation=10.0)
    d.porcelain = FloorType(d, "Porcelanato")
    d.slab = FloorType(d, "Laje 12", IsFoundationSlab=True)
    d.gypsum = CeilingType(d, "Forro gesso")
    d.defaults = {"FloorType": d.porcelain, "CeilingType": d.gypsum}
    d.sala = Room(d, "101", "Sala", d.level0, (0, 0, 20, 10))
    d.cozinha = Room(d, "102", "Cozinha", d.level0, (20, 0, 30, 10))
    d.quarto = Room(d, "201", "Quarto", d.level1, (0, 0, 10, 10))
    return d


def segment(a, b, line=True):
    return {"start": a, "end": b, "length": math.dist(a, b), "line": line}


def run(fe, doc, kind, **data):
    return fe._finish_route(doc, SimpleNamespace(data=data, headers={}), kind)


@pytest.fixture
def approvals(fe, monkeypatch):
    class Calls(list):
        seen = None  # request.data as the dialog would show it

    calls = Calls()
    calls.seen = []

    def authorize(request, action=None):
        calls.append(action)
        calls.seen.append(dict(request.data))

    monkeypatch.setattr(fe, "authorize_request", authorize)
    return calls


# ---- pure helpers ----

@pytest.mark.parametrize("data, kind, message", [
    ({}, "floor", "Give room_ids, room_numbers or level"),
    ({"room_ids": []}, "floor", "room_ids"),
    ({"room_ids": [True]}, "floor", "room_ids"),
    ({"room_ids": list(range(201))}, "floor", "At most 200"),
    ({"room_numbers": [""]}, "floor", "room_numbers"),
    ({"room_numbers": ["1"], "room_ids": [1]}, "floor", "not both"),
    ({"level": ""}, "floor", "level"),
    ({"level": "T", "floor_type": 3}, "floor", "floor_type"),
    ({"level": "T", "boundary_location": "core"}, "floor", "boundary_location"),
    ({"level": "T", "dry_run": "no"}, "floor", "dry_run"),
    ({"level": "T", "offset_m": 9}, "floor", "offset_m"),
    ({"level": "T", "height_m": 2.6}, "floor", "height_m is for ceilings"),
    ({"level": "T", "offset_m": 0}, "ceiling", "offset_m is for floors"),
    ({"level": "T", "height_m": 0}, "ceiling", "height_m"),
    ({"level": "T", "ceiling_type": " "}, "ceiling", "ceiling_type"),
])
def test_validate_request_rejects(fe, data, kind, message):
    assert message in fe.validate_request(data, kind)


def test_validate_request_accepts(fe):
    assert fe.validate_request({"level": u"Térreo", "offset_m": -0.05}, "floor") is None
    assert fe.validate_request({"room_ids": [5], "height_m": 2.6, "ceiling_type": "Forro"},
                               "ceiling") is None


def test_ceiling_height(fe):
    assert fe.ceiling_height(2.6, False, 0.5, 3.0) == (2.6, "height_m", None)
    assert fe.ceiling_height(None, True, 0.0, 2.8) == (2.8, "room_top", None)
    # Unbounded height starts at the room base: level + lower offset.
    assert fe.ceiling_height(None, True, 0.1, 2.5) == (pytest.approx(2.6), "room_top", None)
    assert "another level" in fe.ceiling_height(None, False, 0.0, 2.8)[2]
    assert "outside 0-6" in fe.ceiling_height(None, True, 0.0, 8.0)[2]
    assert "pass height_m" in fe.ceiling_height(None, True, 0.0, None)[2]


@pytest.mark.parametrize("created, demolished, phase, expected", [
    (0, None, 1, True), (1, None, 1, True), (2, None, 1, False),
    (0, 1, 1, False), (0, 0, 1, False), (0, 2, 1, True), (None, None, 1, True),
    (5, 5, None, True),
])
def test_exists_in_phase(fe, created, demolished, phase, expected):
    assert fe.exists_in_phase(created, demolished, phase) is expected


def test_preview_rooms(fe):
    labels = [{"number": str(i), "room_id": i} for i in range(52)] + [{"number": None,
                                                                         "room_id": 7}]
    shown = fe.preview_rooms(labels)
    assert shown[:2] == ["0", "1"] and len(shown) == 51 and shown[-1] == "... +3"
    assert fe.preview_rooms([{"number": None, "room_id": 7}]) == ["id 7"]


def test_covering_hosts_ignores_slabs_bigger_than_the_room(fe):
    hosts = [{"area_m2": 10.0}, {"area_m2": 200.0}]
    assert fe.covering_hosts(hosts, 12.0) == [{"area_m2": 10.0}]


def test_clean_loop_keeps_a_closed_loop(fe):
    square = [segment((0, 0, 0), (1, 0, 0)), segment((1, 0, 0), (1, 1, 0)),
              segment((1, 1, 0), (0, 1, 0)), segment((0, 1, 0), (0, 0, 0))]
    cleaned, repaired, error = fe.clean_loop(square)
    assert error is None and repaired == 0 and len(cleaned) == 4
    assert not any(item["rebuilt"] for item in cleaned)


def test_clean_loop_drops_tiny_segments_and_bridges_the_gap(fe):
    tiny = 0.002  # 0.6 mm in feet
    square = [segment((0, 0, 0), (1, 0, 0)), segment((1, 0, 0), (1 + tiny, 0, 0)),
              segment((1 + tiny, 0, 0), (1 + tiny, 1, 0)), segment((1 + tiny, 1, 0), (0, 1, 0)),
              segment((0, 1, 0), (0, 0, 0))]
    cleaned, repaired, error = fe.clean_loop(square)
    assert error is None and len(cleaned) == 4 and repaired == 2
    assert cleaned[1]["rebuilt"] and cleaned[1]["start"] == (1, 0, 0)


def test_clean_loop_bridges_with_the_previous_line_before_an_arc(fe):
    loop = [segment((0, 0, 0), (1, 0, 0)), segment((1.001, 0, 0), (1, 1, 0), line=False),
            segment((1, 1, 0), (0, 0, 0))]
    cleaned, _, error = fe.clean_loop(loop)
    assert error is None and cleaned[0]["rebuilt"] and cleaned[0]["end"] == (1.001, 0, 0)


@pytest.mark.parametrize("loop, message", [
    ([segment((0, 0, 0), (0.001, 0, 0))], "fewer than 2"),
    ([segment((0, 0, 0), (1, 0, 0)), segment((1.5, 0, 0), (0, 0, 0))], "open"),
    ([segment((0, 0, 0), (1, 0, 0), False), segment((1.01, 0, 0), (0, 0, 0), False)],
     "curved"),
])
def test_clean_loop_errors(fe, loop, message):
    cleaned, _, error = fe.clean_loop(loop)
    assert cleaned is None and message in error


# ---- routes ----

def test_floor_dry_run_by_level_rolls_back_and_reports_areas(fe, doc, approvals):
    result = run(fe, doc, "floor", level=u"térreo")
    assert result["data"]["status"] == "dry_run"
    data = result["data"]
    assert [r["number"] for r in data["results"]] == ["101", "102"]
    assert data["results"][0]["area_m2"] == pytest.approx(200 * 0.09290304, abs=0.01)
    assert "element_id" not in data["results"][0]
    assert data["type"] == "Porcelanato" and data["skipped"] == []
    assert data["total_area_m2"] == pytest.approx(300 * 0.09290304, abs=0.01)
    assert ("boundary", "Finish") in LOG and "group.rollback" in LOG
    assert approvals == [], "dry run must not open the approval dialog"


def test_floor_real_run_asks_approval_and_assimilates(fe, doc, approvals):
    result = run(fe, doc, "floor", room_numbers=["101"], offset_m=0.02, floor_type="porcelanato",
                 boundary_location="center", dry_run=False)
    data = result["data"]
    assert data["status"] == "success" and data["created"] == 1
    assert approvals == ["Create floors from rooms"]
    created = doc.GetElement(data["element_ids"][0])
    assert created.params["FLOOR_HEIGHTABOVELEVEL_PARAM"].value == pytest.approx(0.02 * FT)
    assert data["results"][0]["offset_m"] == 0.02
    assert ("boundary", "Center") in LOG and "group.assimilate" in LOG


def test_existing_floor_is_skipped_but_a_slab_under_all_rooms_is_not(fe, doc, approvals):
    Host(doc, "OST_Floors", doc.porcelain.Id, doc.level0.Id, rectangle(1, 1, 19, 9))
    Host(doc, "OST_Floors", doc.slab.Id, doc.level0.Id, rectangle(0, 0, 30, 10))
    data = run(fe, doc, "floor", level=u"Térreo")["data"]
    assert [r["number"] for r in data["results"]] == ["102"]
    assert data["skipped"][0]["reason"] == "existing_floor"
    assert data["skipped"][0]["number"] == "101" and len(data["skipped"][0]["existing_ids"]) == 1


def test_skip_existing_false_creates_anyway(fe, doc, approvals):
    Host(doc, "OST_Floors", doc.porcelain.Id, doc.level0.Id, rectangle(1, 1, 19, 9))
    data = run(fe, doc, "floor", room_numbers=["101"], skip_existing=False)["data"]
    assert data["status"] == "dry_run" and len(data["results"]) == 1


def test_selection_by_ids_reports_unknown_and_unplaced_rooms(fe, doc, approvals):
    unplaced = Room(doc, "103", "Depósito", doc.level0, (0, 0, 1, 1), placed=False)
    data = run(fe, doc, "floor",
               room_ids=[doc.sala.Id.Value, unplaced.Id.Value, doc.level0.Id.Value, 99999])["data"]
    reasons = sorted(s["reason"] for s in data["skipped"])
    assert reasons == ["not_a_room", "not_found", "unplaced"]
    assert [r["number"] for r in data["results"]] == ["101"]


def test_numbers_not_found_are_listed(fe, doc, approvals):
    data = run(fe, doc, "floor", room_numbers=["101", "999"])["data"]
    assert data["numbers_not_found"] == ["999"]


def test_invalid_boundary_is_skipped_not_fatal(fe, doc, approvals):
    doc.cozinha.curves = doc.cozinha.curves[:3]  # open loop: last edge missing
    data = run(fe, doc, "floor", level=u"Térreo")["data"]
    assert [r["number"] for r in data["results"]] == ["101"]
    assert data["skipped"][0]["reason"] == "invalid_boundary"
    assert "open" in data["skipped"][0]["detail"]


def test_nothing_to_create_does_not_ask_approval(fe, doc, approvals):
    Host(doc, "OST_Floors", doc.porcelain.Id, doc.level1.Id, rectangle(1, 1, 9, 9))
    data = run(fe, doc, "floor", level="Pavimento 1", dry_run=False)["data"]
    assert data["status"] == "nothing_to_create" and data["created"] == 0
    assert approvals == []


def test_revit_error_rolls_back_everything_and_names_the_room(fe, doc, approvals):
    doc.create_error = "Boundary lines intersect"
    data = run(fe, doc, "floor", level=u"Térreo", dry_run=False)["data"]
    assert data["status"] == "error" and data["created"] == 0
    assert data["errors"][0]["number"] == "101"
    assert "group.rollback" in LOG and "group.assimilate" not in LOG


def test_unknown_type_is_a_400_with_the_available_types(fe, doc, approvals):
    result = run(fe, doc, "floor", level=u"Térreo", floor_type="Madeira")
    assert result["status"] == 400 and result["data"]["available_types"] == ["Porcelanato"]


def test_too_many_rooms_is_a_400(fe, doc, approvals, monkeypatch):
    monkeypatch.setattr(fe, "MAX_ITEMS", 1)
    result = run(fe, doc, "floor", level=u"Térreo")
    assert result["status"] == 400 and "at most 1" in result["data"]["error"]


def test_ceiling_height_from_request_or_room(fe, doc, approvals):
    doc.cozinha.UnboundedHeight = 9.0 * FT
    data = run(fe, doc, "ceiling", level=u"Térreo")["data"]
    assert [r["number"] for r in data["results"]] == ["101"]
    assert data["results"][0]["height_m"] == 2.8
    assert data["results"][0]["height_source"] == "room_top"
    assert data["skipped"][0]["reason"] == "no_ceiling_height"

    LOG.clear()
    # The fake does not undo the dry run's ceiling, so do not skip it as existing.
    data = run(fe, doc, "ceiling", level=u"Térreo", height_m=2.6, skip_existing=False,
               dry_run=False)["data"]
    assert data["created"] == 2 and approvals == ["Create ceilings from rooms"]
    created = doc.GetElement(data["element_ids"][0])
    assert created.params["CEILING_HEIGHTABOVELEVEL_PARAM"].value == pytest.approx(2.6 * FT)
    assert ("OST_Ceilings.Create", 1, doc.gypsum.Id.Value, doc.level0.Id.Value) in LOG


def test_no_document_and_bad_body(fe, doc):
    assert run(fe, None, "floor", level="T")["status"] == 503
    assert run(fe, doc, "floor")["status"] == 400


# ---- review fixes: phases, room top, approval preview, Set result, coupling ----

def test_new_element_goes_to_the_room_phase(fe, doc, approvals):
    old = Room(doc, "001", u"Depósito", doc.level1, (20, 0, 30, 10), phase=doc.phase_existing)
    data = run(fe, doc, "floor", room_ids=[old.Id.Value], dry_run=False)["data"]
    created = doc.GetElement(data["element_ids"][0])
    assert created.CreatedPhaseId == doc.phase_existing.Id
    assert data["results"][0]["phase"] == "Existente" and "phase_note" not in data["results"][0]


def test_phase_note_when_revit_refuses_the_phase(fe, doc, approvals, monkeypatch):
    monkeypatch.setattr(Host, "IsPhaseCreatedValid", lambda self, phase_id: False)
    old = Room(doc, "001", u"Depósito", doc.level1, (20, 0, 30, 10), phase=doc.phase_existing)
    data = run(fe, doc, "floor", room_ids=[old.Id.Value])["data"]
    assert "phase not set" in data["results"][0]["phase_note"]


def test_skip_existing_only_counts_hosts_present_in_the_room_phase(fe, doc, approvals):
    # Demolished in the room's phase: gone, the room needs a new floor.
    Host(doc, "OST_Floors", doc.porcelain.Id, doc.level0.Id, rectangle(1, 1, 19, 9),
         created=doc.phase_existing, demolished=doc.phase_new)
    # Created in a later phase than the existing-phase room: not there yet.
    old = Room(doc, "001", u"Depósito", doc.level1, (20, 0, 30, 10), phase=doc.phase_existing)
    Host(doc, "OST_Floors", doc.porcelain.Id, doc.level1.Id, rectangle(21, 1, 29, 9),
         created=doc.phase_new)
    # Existing and kept: the room is skipped.
    Host(doc, "OST_Floors", doc.porcelain.Id, doc.level0.Id, rectangle(21, 1, 29, 9),
         created=doc.phase_existing)
    data = run(fe, doc, "floor", room_ids=[doc.sala.Id.Value, doc.cozinha.Id.Value,
                                           old.Id.Value])["data"]
    assert sorted(r["number"] for r in data["results"]) == ["001", "101"]
    assert [(s["number"], s["reason"]) for s in data["skipped"]] == [("102", "existing_floor")]


def test_ceiling_default_height_uses_the_room_top_on_its_own_level(fe, doc, approvals):
    doc.sala.params["ROOM_LOWER_OFFSET"] = FakeParameter(0.1 * FT)
    doc.sala.UnboundedHeight = 2.5 * FT
    doc.cozinha.params["ROOM_UPPER_LEVEL"] = FakeParameter(doc.level1.Id)
    data = run(fe, doc, "ceiling", level=u"Térreo")["data"]
    assert data["results"][0]["height_m"] == pytest.approx(2.6)
    assert data["skipped"][0]["number"] == "102"
    assert "another level" in data["skipped"][0]["detail"]


def test_approval_dialog_shows_the_rooms_and_count(fe, doc, approvals):
    run(fe, doc, "floor", level=u"Térreo", dry_run=False)
    assert approvals == ["Create floors from rooms"]
    assert approvals.seen[0]["_count"] == 2 and approvals.seen[0]["_rooms"] == ["101", "102"]


def test_expected_count_mismatch_is_409_without_approval(fe, doc, approvals):
    result = run(fe, doc, "floor", level=u"Térreo", expected_count=3, dry_run=False)
    assert result["status"] == 409 and result["data"]["count"] == 2
    assert approvals == []
    result = run(fe, doc, "floor", level=u"Térreo", expected_count=2, dry_run=False)
    assert result["data"]["status"] == "success"


def test_refused_offset_rolls_back(fe, doc, approvals):
    doc.refuse_offset = True
    data = run(fe, doc, "floor", room_numbers=["101"], offset_m=0.02, dry_run=False)["data"]
    assert data["status"] == "error" and "refused" in data["errors"][0]["error"]
    assert "group.assimilate" not in LOG


def test_shared_helpers_keep_the_signatures_used_here(fe, doc):
    """finish_elements imports these from modules other specs edit; call them
    exactly as finish_elements does so a signature change fails here."""
    import inspect
    from revit_mcp import modeling, quantities
    Host(doc, "OST_Floors", doc.porcelain.Id, doc.level0.Id, rectangle(1, 1, 19, 9))
    hosts = quantities.collect_hosts(doc, "OST_Floors", {})
    assert hosts and {"element_id", "level_key", "area_m2", "centre"} <= set(hosts[0])
    found = quantities._hosts_in_room(doc.sala, hosts, doc.level0.Id.Value, 1.0)
    assert [h["element_id"] for h in found] == [hosts[0]["element_id"]]
    inspect.signature(modeling.run_batch).bind(doc, "name", [], None, False, "floors")
    assert issubclass(fe.ItemError, Exception) and fe.ItemError is modeling.ItemError
    result = modeling.run_batch(doc, "MCP: test", [], lambda d, i, p: ({}, None), False, "floors")
    assert result["status"] == "dry_run" and result["results"] == []
