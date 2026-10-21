# -*- coding: utf-8 -*-
"""Route logic of revit_mcp/phases.py (spec 016) against a fake Revit API:
phase helpers, the phases report, and set_element_phases validation,
all-or-nothing batches and dry-run rollback through a TransactionGroup.
The module is IronPython code; py2 builtins are injected for CPython."""
import builtins
import importlib
import json
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

    def __ne__(self, other):
        return not self == other

    def __hash__(self):
        return hash(self.Value)


FakeId.InvalidElementId = FakeId(-1)
INVALID = FakeId.InvalidElementId


class FakeParameter:
    def __init__(self, read_only=False, value=None):
        self.IsReadOnly, self.value = read_only, value

    def AsElementId(self):
        return self.value


class Element:
    Category = None

    def __init__(self, doc, name=None, **attrs):
        self.Id = doc.add(self)
        self.Name = name
        self.params = {}
        self.__dict__.update(attrs)

    def get_Parameter(self, builtin):
        return self.params.get(builtin)


class Phase(Element):
    pass


class Level(Element):
    pass


class View(Element):
    IsTemplate = False
    ViewType = "FloorPlan"


class PhaseFilter(Element):
    def GetPhaseStatusPresentation(self, status):
        return self.shown[status]


class Category:
    def __init__(self, value, name, builtin, model=True):
        self.Id, self.Name, self.BuiltInCategory = FakeId(value), name, builtin
        self.CategoryType = "Model" if model else "Annotation"


class Modelled(Element):
    """Model element with phases; the setters enforce Revit's order rule."""
    ViewSpecific = False

    def __init__(self, doc, category, created, demolished=None, level=None,
                 read_only=False, modifiable=True, phased=True):
        super().__init__(doc, "elem", Category=category, LevelId=level.Id if level else INVALID)
        self._created = created.Id if created else INVALID
        self._demolished = demolished.Id if demolished else INVALID
        self.modifiable, self.phased = modifiable, phased
        self.params = {"PHASE_CREATED": FakeParameter(read_only),
                       "PHASE_DEMOLISHED": FakeParameter(read_only)}
        self.doc = doc

    def HasPhases(self):
        return self.phased

    def ArePhasesModifiable(self):
        return self.modifiable

    def IsPhaseCreatedValid(self, phase_id):
        LOG.append(("created_valid", phase_id.Value))
        return phase_id.Value not in self.doc.refused

    def IsPhaseDemolishedValid(self, phase_id):
        LOG.append(("demolished_valid", phase_id.Value))
        return phase_id.Value not in self.doc.refused

    def _check(self, created, demolished):
        order = self.doc.phase_order
        if demolished != INVALID and order[demolished.Value] < order[created.Value]:
            raise Exception(u"Fase de demolição inválida")

    @property
    def CreatedPhaseId(self):
        return self._created if self.phased else INVALID

    @CreatedPhaseId.setter
    def CreatedPhaseId(self, value):
        self._check(value, self._demolished)
        LOG.append(("created", self.Id.Value, value.Value))
        self._created = value

    @property
    def DemolishedPhaseId(self):
        return self._demolished

    @DemolishedPhaseId.setter
    def DemolishedPhaseId(self, value):
        self._check(self._created, value)
        LOG.append(("demolished", self.Id.Value, value.Value))
        self._demolished = value


class FakeDoc:
    def __init__(self):
        self.elements = {}
        self.Phases = []
        self.failures = []
        self.categories = []
        self.refused = set()  # phase ids rejected by IsPhase*Valid
        self.Settings = SimpleNamespace(Categories=self.categories)

    @property
    def phase_order(self):
        return dict((p.Id.Value, i) for i, p in enumerate(self.Phases))

    def add(self, element):
        new_id = FakeId(1000 + len(self.elements))
        self.elements[new_id.Value] = element
        return new_id

    def GetElement(self, element_id):
        return self.elements.get(element_id.Value)


class FilteredElementCollector:
    def __init__(self, doc, view_id=None):
        self.items = list(doc.elements.values())
        if view_id is not None:
            self.items = [e for e in self.items if view_id.Value in getattr(e, "views", ())]

    def WherePasses(self, element_filter):
        self.items = [e for e in self.items if e.Category is not None
                      and e.Category.Id.Value in element_filter.ids]
        self.filtered = True
        return self

    def OfClass(self, cls):
        self.items = [e for e in self.items if isinstance(e, cls)]
        return self

    def WhereElementIsNotElementType(self):
        return self

    def ToElements(self):
        return self.items


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


def make_response(data=None, status=200):
    return SimpleNamespace(data=data, status=status)


@pytest.fixture
def phases(monkeypatch):
    DB = SimpleNamespace(
        ElementMulticategoryFilter=lambda ids: SimpleNamespace(ids=[i.Value for i in ids]),
        ElementId=FakeId, View=View, Level=Level, PhaseFilter=PhaseFilter,
        Element=SimpleNamespace(Name=SimpleNamespace(GetValue=lambda e: e.Name)),
        FilteredElementCollector=FilteredElementCollector,
        Transaction=Transaction, TransactionGroup=TransactionGroup,
        TransactionStatus=SimpleNamespace(Committed="Committed"),
        IFailuresPreprocessor=object,
        FailureSeverity=SimpleNamespace(Warning="Warning", Error="Error"),
        FailureProcessingResult=SimpleNamespace(Continue="Continue",
                                                ProceedWithRollBack="ProceedWithRollBack"),
        BuiltInParameter=BuiltInParameter(),
        CategoryType=SimpleNamespace(Model="Model"),
        ElementOnPhaseStatus=SimpleNamespace(New="New", Existing="Existing",
                                             Demolished="Demolished", Temporary="Temporary"),
    )
    pyrevit = SimpleNamespace(DB=DB, routes=SimpleNamespace(make_response=make_response),
                              forms=SimpleNamespace(alert=lambda *a, **k: True))
    monkeypatch.setitem(sys.modules, "pyrevit", pyrevit)
    for name in ("long", "basestring", "unicode"):
        monkeypatch.setattr(builtins, name, {"long": int, "basestring": str, "unicode": str}[name],
                            raising=False)
    names = ("revit_mcp.utils", "revit_mcp.security", "revit_mcp.modeling", "revit_mcp.phases")
    for name in names:
        sys.modules.pop(name, None)
    module = importlib.import_module("revit_mcp.phases")
    LOG.clear()
    yield module
    for name in names:
        sys.modules.pop(name, None)


@pytest.fixture
def doc():
    d = FakeDoc()
    d.existing = Phase(d, "Existente")
    d.new = Phase(d, u"Nova construção")
    d.Phases.extend([d.existing, d.new])
    d.walls = Category(1, "Paredes", "OST_Walls")
    d.doors = Category(2, "Portas", "OST_Doors")
    d.tags = Category(3, "Identificadores", "OST_Tags", model=False)
    d.categories.extend([d.walls, d.doors, d.tags])
    d.ground = Level(d, u"Térreo")
    d.first = Level(d, "Pavimento 1")
    return d


# ---- pure helpers ----

PH = [(10, "Existente"), (11, "Demolir"), (12, u"Nova construção")]


def test_resolve_phase_by_name_or_id(phases):
    assert phases.resolve_phase(PH, u"nova  CONSTRUÇÃO") == (2, None)
    assert phases.resolve_phase(PH, 11) == (1, None)
    assert phases.resolve_phase(PH, "10") == (0, None)
    position, error = phases.resolve_phase(PH, "Futura")
    assert position is None and u"Existente, Demolir, Nova construção" in error


def test_order_error(phases):
    assert phases.order_error(0, 2, PH) is None
    assert phases.order_error(1, 1, PH) is None  # temporary
    assert phases.order_error(2, None, PH) is None
    assert "comes before" in phases.order_error(2, 0, PH)


@pytest.mark.parametrize("created, demolished, phase, status", [
    (0, None, 2, "existing"), (0, 2, 2, "demolished"), (2, None, 2, "new"),
    (2, 2, 2, "temporary"), (0, 1, 2, "past"), (2, None, 0, "future"),
    (0, 2, 1, "existing"), (None, None, 2, None),
])
def test_status_in_phase(phases, created, demolished, phase, status):
    assert phases.status_in_phase(created, demolished, phase) == status


def test_presentation_name(phases):
    assert phases.presentation_name("ShowOverriden") == "overridden"
    assert phases.presentation_name("PhaseStatusPresentation.DontShow") == "not_displayed"
    assert phases.presentation_name("ShowByCategory") == "by_category"
    assert phases.presentation_name("Other") == "Other"


def test_combination_rows_order_and_top_categories(phases):
    counts = {(2, None): {"count": 3, "categories": {"Portas": 3}},
              (0, 2): {"count": 2, "categories": {"Paredes": 2}},
              (0, None): {"count": 12, "categories": dict(("c{:02d}".format(i), 12 - i)
                                                          for i in range(12))}}
    rows = phases.combination_rows(counts, PH, True)
    assert [(r["created_phase"], r["demolished_phase"]) for r in rows] == [
        ("Existente", u"Nova construção"), ("Existente", None), (u"Nova construção", None)]
    assert [r["status_in_last_phase"] for r in rows] == ["demolished", "existing", "new"]
    assert len(rows[1]["categories"]) == 10 and rows[1]["categories"][0] == {"category": "c00", "count": 12}
    assert rows[1]["other_categories"] == 2 + 1
    assert "categories" not in phases.combination_rows(counts, PH, False)[0]


def test_group_views_caps_listed_names(phases):
    rows = [("Existente", "Mostrar tudo", "B"), ("Existente", "Mostrar tudo", "A"),
            (u"Nova construção", None, "C")]
    groups, truncated = phases.group_views(rows, 2)
    assert groups[0] == {"phase": "Existente", "phase_filter": "Mostrar tudo", "count": 2,
                         "views": ["A", "B"]}
    assert groups[1]["count"] == 1 and groups[1]["views"] == [] and truncated
    assert phases.group_views(rows, 10)[1] is False


@pytest.mark.parametrize("item, message", [
    ({"element_id": 1, "created_phase": "Existente"}, None),
    ({"element_id": 1, "demolished_phase": "none"}, None),
    ({"element_id": 1, "demolished_phase": u"Nenhum"}, None),
    ({"element_id": 1, "created_phase": 5}, None),
    ({"element_id": "1", "created_phase": "A"}, "element_id"),
    ({"element_id": 1}, "and/or"),
    ({"element_id": 1, "created_phase": "none"}, "cannot be cleared"),
    ({"element_id": 1, "created_phase": True}, "phase name or id"),
    ({"element_id": 1, "demolished_phase": -3}, "positive"),
    ("x", "object"),
])
def test_validate_change(phases, item, message):
    error = phases.validate_change(item)
    assert (error is None) if message is None else (message in error)


@pytest.mark.parametrize("selector, message", [
    ({"category": "Paredes"}, None),
    ({"selection": True}, None),
    ({"category": "Paredes", "level": u"Térreo", "view_id": 9}, None),
    ({"level": "L1"}, "category or selection"),
    ({"category": "Paredes", "x": 1}, "Unknown"),
    ({"category": "Paredes", "view_id": "9"}, "view_id"),
    ({"category": " "}, "category"),
    ([], "object"),
])
def test_validate_selector(phases, selector, message):
    error = phases.validate_selector(selector)
    assert (error is None) if message is None else (message in error)


def test_duplicate_errors(phases):
    changes = [{"element_id": 1, "created_phase": "A"}, {"element_id": 2, "created_phase": "A"},
               {"element_id": 1, "demolished_phase": "B"}, "x", {"element_id": "1"}]
    assert phases.duplicate_errors(changes) == [{"index": 2, "error": "element_id 1 repeats change 0"}]


def test_expected_count_error(phases):
    assert phases.expected_count_error(None) is None and phases.expected_count_error(3) is None
    assert phases.expected_count_error(0) and phases.expected_count_error(True)
    assert phases.expected_count_error("3")


@pytest.mark.parametrize("view_type, template, message", [
    ("FloorPlan", False, None), ("ThreeD", False, None), ("Section", False, None),
    ("ViewType.Schedule", False, "Schedule"), ("DrawingSheet", False, "DrawingSheet"),
    ("Legend", False, "Legend"), ("FloorPlan", True, "template"),
])
def test_view_kind_error(phases, view_type, template, message):
    error = phases.view_kind_error(view_type, template)
    assert (error is None) if message is None else (message in error)


def test_preview_ids(phases):
    assert phases.preview_ids(list(range(80))) == list(range(50))


def test_parse_limit(phases):
    assert phases.parse_limit(None) == (200, None)
    assert phases.parse_limit(0)[1] and phases.parse_limit("x")[1]


# ---- phases_report ----

def test_phases_report(phases, doc):
    Modelled(doc, doc.walls, doc.existing)
    Modelled(doc, doc.walls, doc.existing, doc.new)
    Modelled(doc, doc.doors, doc.existing, doc.new)
    Modelled(doc, doc.doors, doc.new)
    Modelled(doc, doc.tags, doc.new)  # annotation: not counted
    Modelled(doc, doc.walls, None, phased=False)  # no phases: not counted
    show_new = PhaseFilter(doc, "Mostrar novo", shown={"New": "ShowByCategory", "Existing": "DontShow",
                                            "Demolished": "DontShow", "Temporary": "ShowOverriden"})
    plan = View(doc, u"Planta reforma")
    plan.params = {"VIEW_PHASE": FakeParameter(value=doc.new.Id),
                   "VIEW_PHASE_FILTER": FakeParameter(value=show_new.Id)}
    template = View(doc, "Modelo", IsTemplate=True)
    template.params = {"VIEW_PHASE": FakeParameter(value=doc.new.Id)}
    View(doc, "Sem fase")  # schedules/legends without VIEW_PHASE

    result = phases.phases_report(doc, None, True, 200)
    assert result["phases"] == [{"id": doc.existing.Id.Value, "name": "Existente", "sequence": 1},
                                {"id": doc.new.Id.Value, "name": u"Nova construção", "sequence": 2}]
    assert result["phase_filters"][0]["presentation"] == {
        "new": "by_category", "existing": "not_displayed", "demolished": "not_displayed",
        "temporary": "overridden"}
    assert result["elements_total"] == 4
    combos = result["combinations"]
    assert [(c["created_phase"], c["demolished_phase"], c["count"], c["status_in_last_phase"])
            for c in combos] == [("Existente", u"Nova construção", 2, "demolished"),
                                 ("Existente", None, 1, "existing"),
                                 (u"Nova construção", None, 1, "new")]
    assert combos[0]["categories"] == [{"category": "Paredes", "count": 1},
                                       {"category": "Portas", "count": 1}]
    assert result["views"] == [{"phase": u"Nova construção", "phase_filter": "Mostrar novo",
                                "count": 1, "views": [u"Planta reforma"]}]
    json.dumps(result)


def test_phases_report_category_filter(phases, doc):
    Modelled(doc, doc.walls, doc.existing)
    Modelled(doc, doc.doors, doc.new)
    used = []
    original = FilteredElementCollector.WherePasses

    def spy(self, element_filter):
        used.append(element_filter.ids)
        return original(self, element_filter)
    FilteredElementCollector.WherePasses = spy
    try:
        result = phases.phases_report(doc, ["Walls"], False, 200)
    finally:
        FilteredElementCollector.WherePasses = original
    assert used == [[doc.walls.Id.Value]]
    assert result["elements_total"] == 1 and result["categories"] == ["Walls"]
    with pytest.raises(phases.ItemError, match="not found"):
        phases.phases_report(doc, ["Telhados"], False, 200)


# ---- set_element_phases ----

def items_for(elements, created=None, demolished=None):
    return [{"index": i, "element_id": e.Id.Value if e else 99, "element": e,
             "created": created, "demolished": demolished} for i, e in enumerate(elements)]


def test_dry_run_reports_before_after_and_rolls_back(phases, doc):
    wall = Modelled(doc, doc.walls, doc.new)
    result = phases.set_element_phases(doc, items_for([wall], "Existente", u"nova construção"), False)
    assert result["status"] == "dry_run" and result["changed"] == 0 and result["would_change"] == 1
    row = result["results"][0]
    assert row["before"] == {"created_phase": u"Nova construção", "demolished_phase": None}
    assert row["after"] == {"created_phase": "Existente", "demolished_phase": u"Nova construção"}
    assert LOG[-2:] == ["t.commit", "group.rollback"]


def test_commit_assimilates(phases, doc):
    wall = Modelled(doc, doc.walls, doc.existing, doc.new)
    same = Modelled(doc, doc.walls, doc.existing)
    result = phases.set_element_phases(doc, items_for([wall, same], None, "none"), True)
    assert result["status"] == "success" and result["changed"] == 1 and result["unchanged"] == 1
    assert wall.DemolishedPhaseId == INVALID
    assert LOG[-1] == "group.assimilate"


def test_moving_creation_after_old_demolition_clears_it_first(phases, doc):
    wall = Modelled(doc, doc.walls, doc.existing, doc.existing)  # temporary in Existente
    result = phases.set_element_phases(doc, items_for([wall], u"Nova construção", u"Nova construção"), True)
    assert result["status"] == "success"
    assert (wall.CreatedPhaseId, wall.DemolishedPhaseId) == (doc.new.Id, doc.new.Id)


@pytest.mark.parametrize("kwargs, element_kwargs, message", [
    ({"created": u"Nova construção", "demolished": "Existente"}, {}, "comes before"),
    ({"created": u"Nova construção"}, {"demolished": "existing"}, "comes before"),
    ({"created": "Futura"}, {}, "not found"),
    ({"created": "Existente"}, {"read_only": True}, "read-only"),
    ({"created": "Existente"}, {"modifiable": False}, "cannot be modified"),
    ({"created": "Existente"}, {"phased": False}, "has no phases"),
])
def test_invalid_items_change_nothing(phases, doc, kwargs, element_kwargs, message):
    if element_kwargs.get("demolished") == "existing":
        element_kwargs = dict(element_kwargs, demolished=doc.existing)
    good = Modelled(doc, doc.walls, doc.existing)
    bad = Modelled(doc, doc.walls, doc.existing, **element_kwargs)
    items = items_for([good, bad], "Existente")
    items[1].update(kwargs)
    result = phases.set_element_phases(doc, items, True)
    assert result["status"] == "error" and result["changed"] == 0
    assert len(result["errors"]) == 1
    assert result["errors"][0]["index"] == 1 and message in result["errors"][0]["error"]
    assert not LOG  # no transaction was opened


def test_revit_validity_checks_before_the_transaction(phases, doc):
    wall = Modelled(doc, doc.walls, doc.existing)
    doc.refused.add(doc.new.Id.Value)
    result = phases.set_element_phases(doc, items_for([wall], u"Nova construção"), True)
    assert "does not accept created phase" in result["errors"][0]["error"]
    result = phases.set_element_phases(doc, items_for([wall], None, u"Nova construção"), True)
    assert "does not accept demolished phase" in result["errors"][0]["error"]
    assert "t.commit" not in LOG and "group.rollback" not in LOG


def test_validity_checks_skipped_when_both_phases_move(phases, doc):
    wall = Modelled(doc, doc.walls, doc.existing, doc.existing)
    phases.set_element_phases(doc, items_for([wall], u"Nova construção", u"Nova construção"), False)
    assert not [entry for entry in LOG if isinstance(entry, tuple) and entry[0].endswith("_valid")]


def test_validity_check_missing_in_api_is_ignored(phases, doc):
    class Old(Modelled):
        IsPhaseCreatedValid = None
    wall = Old(doc, doc.walls, doc.new)
    result = phases.set_element_phases(doc, items_for([wall], "Existente"), False)
    assert result["status"] == "dry_run"


def test_missing_element(phases, doc):
    result = phases.set_element_phases(doc, items_for([None], "Existente"), True)
    assert result["errors"] == [{"index": 0, "element_id": 99, "error": "Element not found"}]


def test_revit_rejection_rolls_back_everything(phases, doc):
    wall = Modelled(doc, doc.walls, doc.existing)
    wall2 = Modelled(doc, doc.walls, doc.existing)

    def refuse(created, demolished):
        raise Exception(u"Revit recusou")
    wall2._check = refuse
    result = phases.set_element_phases(doc, items_for([wall, wall2], None, u"Nova construção"), True)
    assert result["status"] == "error" and result["errors"][0]["index"] == 1
    assert u"Revit recusou" in result["errors"][0]["error"]
    assert LOG[-2:] == ["t.rollback", "group.rollback"]


def test_commit_failure_reports_errors(phases, doc):
    wall = Modelled(doc, doc.walls, doc.existing)
    doc.failures = [("Error", u"Elemento inválido"), ("Warning", "Aviso")]
    result = phases.set_element_phases(doc, items_for([wall], None, u"Nova construção"), True)
    assert result["status"] == "error" and result["errors"] == [{"error": u"Elemento inválido"}]
    assert LOG[-1] == "group.rollback"


# ---- selector ----

def test_select_elements(phases, doc):
    a = Modelled(doc, doc.walls, doc.existing, level=doc.ground)
    b = Modelled(doc, doc.walls, doc.existing, level=doc.first)
    Modelled(doc, doc.doors, doc.existing, level=doc.ground)
    assert phases.select_elements(doc, None, {"category": "Walls"})[0] == [a, b]
    assert phases.select_elements(doc, None, {"category": "paredes", "level": u"térreo"})[0] == [a]
    uidoc = SimpleNamespace(Selection=SimpleNamespace(GetElementIds=lambda: [b.Id, doc.ground.Id]))
    matched, skipped = phases.select_elements(doc, uidoc, {"selection": True})
    assert matched == [b] and skipped == 1
    with pytest.raises(phases.ItemError, match="Levels:"):
        phases.select_elements(doc, None, {"category": "Paredes", "level": "Cobertura"})
    with pytest.raises(phases.ItemError, match="Category"):
        phases.select_elements(doc, None, {"category": "Telhados"})
    with pytest.raises(phases.ItemError, match="View 5 not found"):
        phases.select_elements(doc, None, {"category": "Paredes", "view_id": 5})
    plan = View(doc, "Planta")
    a.views = (plan.Id.Value,)
    assert phases.select_elements(doc, None, {"category": "Paredes", "view_id": plan.Id.Value})[0] == [a]
    schedule = View(doc, "Tabela", ViewType="Schedule")
    with pytest.raises(phases.ItemError, match="Schedule view"):
        phases.select_elements(doc, None, {"category": "Paredes", "view_id": schedule.Id.Value})
    template = View(doc, "Modelo", IsTemplate=True)
    with pytest.raises(phases.ItemError, match="template"):
        phases.select_elements(doc, None, {"category": "Paredes", "view_id": template.Id.Value})


# ---- routes ----

class FakeApi:
    def __init__(self):
        self.routes = {}

    def route(self, path, methods=None):
        def decorator(func):
            self.routes[path] = func
            return func
        return decorator


@pytest.fixture
def api(phases, monkeypatch):
    calls = []

    def authorize(request, action=None):
        calls.append(action)
        if action:
            calls.append(dict(request.data))
        return None
    monkeypatch.setattr(phases, "authorize_request", authorize)
    registry = FakeApi()
    phases.register_phase_routes(registry)
    return registry.routes, calls


def test_route_dry_run_does_not_ask_for_approval(api, doc):
    routes, calls = api
    wall = Modelled(doc, doc.walls, doc.existing)
    request = SimpleNamespace(data={"changes": [{"element_id": wall.Id.Value,
                                                 "demolished_phase": u"Nova construção"}]})
    response = routes["/set_element_phases/"](doc, None, request)
    assert response.status == 200 and response.data["status"] == "dry_run"
    assert calls == [None]


def test_route_real_run_asks_for_approval(api, doc):
    routes, calls = api
    Modelled(doc, doc.walls, doc.existing)
    request = SimpleNamespace(data={"dry_run": False, "selector": {"category": "Paredes"},
                                    "demolished_phase": u"Nova construção"})
    response = routes["/set_element_phases/"](doc, None, request)
    assert response.data["status"] == "success" and response.data["changed"] == 1
    assert calls[:2] == [None, "Set element phases"]
    assert calls[2]["_resolved_count"] == 1 and calls[2]["_element_ids"] == [response.data["results"][0]["element_id"]]


def test_route_expected_count_mismatch_changes_nothing(api, doc):
    routes, calls = api
    wall = Modelled(doc, doc.walls, doc.existing)
    other = Modelled(doc, doc.walls, doc.existing)
    uidoc = SimpleNamespace(Selection=SimpleNamespace(GetElementIds=lambda: [wall.Id, other.Id]))
    request = SimpleNamespace(data={"dry_run": False, "selector": {"selection": True},
                                    "demolished_phase": u"Nova construção", "expected_count": 1})
    response = routes["/set_element_phases/"](doc, uidoc, request)
    assert response.status == 409 and response.data["matched"] == 2
    assert "Set element phases" not in calls
    assert all(isinstance(entry, tuple) and entry[0].endswith("_valid") for entry in LOG)
    assert wall.DemolishedPhaseId == INVALID
    request.data["expected_count"] = 2
    response = routes["/set_element_phases/"](doc, uidoc, request)
    assert response.status == 200 and response.data["changed"] == 2


def test_route_preview_ids_are_capped(api, doc):
    routes, calls = api
    for _ in range(60):
        Modelled(doc, doc.walls, doc.existing)
    request = SimpleNamespace(data={"dry_run": False, "selector": {"category": "Paredes"},
                                    "demolished_phase": u"Nova construção"})
    routes["/set_element_phases/"](doc, None, request)
    assert calls[2]["_resolved_count"] == 60 and len(calls[2]["_element_ids"]) == 50


@pytest.mark.parametrize("data, message", [
    ({}, "either changes or selector"),
    ({"changes": []}, "non-empty"),
    ({"changes": [{"element_id": 1}] * 201}, "At most 200"),
    ({"changes": [{"element_id": 1}]}, "Invalid changes"),
    ({"changes": [{"element_id": 1, "created_phase": "A"}] * 2}, "Invalid changes"),
    ({"changes": [{"element_id": 1, "created_phase": "A"}], "expected_count": 0}, "expected_count"),
    ({"selector": {"category": "Paredes", "view_id": 1}, "created_phase": "A"}, "not found"),
    ({"selector": {"category": "Paredes"}}, "and/or"),
    ({"selector": {"category": "Telhados"}, "created_phase": "Existente"}, "not found"),
    ({"selector": {"category": "Portas"}, "created_phase": "Existente"}, "No model element"),
])
def test_route_bad_requests(api, doc, data, message):
    routes, calls = api
    response = routes["/set_element_phases/"](doc, None, SimpleNamespace(data=data))
    assert response.status == 400 and message in response.data["error"]
    assert "Set element phases" not in calls


def test_route_selector_cap(api, doc):
    routes, _ = api
    for _ in range(201):
        Modelled(doc, doc.walls, doc.existing)
    request = SimpleNamespace(data={"selector": {"category": "Paredes"},
                                    "created_phase": "Existente"})
    response = routes["/set_element_phases/"](doc, None, request)
    assert response.status == 400 and "matches 201 elements" in response.data["error"]


def test_report_route(api, doc):
    routes, calls = api
    Modelled(doc, doc.walls, doc.existing)
    ok = routes["/phases_report/"](doc, SimpleNamespace(data={"by_category": True}))
    assert ok.status == 200 and ok.data["elements_total"] == 1 and calls == [None]
    assert routes["/phases_report/"](doc, SimpleNamespace(data={"limit": 0})).status == 400
    bad = routes["/phases_report/"](doc, SimpleNamespace(data={"categories": ["Telhados"]}))
    assert bad.status == 400
    assert routes["/phases_report/"](None, SimpleNamespace(data={})).status == 503
