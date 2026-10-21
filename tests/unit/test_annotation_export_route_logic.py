# -*- coding: utf-8 -*-
"""Route logic of revit_mcp/annotation.py and revit_mcp/exports.py against a
fake Revit API. The modules are IronPython code; py2 builtins are injected
for CPython."""
import builtins
import importlib
import sys
from types import SimpleNamespace

import pytest

MODULES = ("revit_mcp.utils", "revit_mcp.security", "revit_mcp.annotation", "revit_mcp.exports")


class Transaction:
    log = []
    commit_status = "Committed"

    def __init__(self, doc, name):
        self.name, self.started, self.ended = name, False, False

    def Start(self):
        self.started = True

    def Commit(self):
        self.ended = True
        Transaction.log.append("commit")
        return Transaction.commit_status

    def RollBack(self):
        self.ended = True
        Transaction.log.append("rollback")

    def HasStarted(self):
        return self.started

    def HasEnded(self):
        return self.ended


def eid(value):
    return SimpleNamespace(Value=value)


INVALID = eid(-1)


@pytest.fixture
def revit_modules(monkeypatch):
    DB = SimpleNamespace(
        Transaction=Transaction,
        TransactionStatus=SimpleNamespace(Committed="Committed"),
        ElementId=SimpleNamespace(InvalidElementId=INVALID),
        SectionType=SimpleNamespace(Body="Body"),
        Element=SimpleNamespace(Name=SimpleNamespace(GetValue=lambda e: e.name)),
        BuiltInParameter=SimpleNamespace(
            ROOM_NUMBER="ROOM_NUMBER", ROOM_NAME="ROOM_NAME", ROOM_DEPARTMENT="ROOM_DEPARTMENT",
            ALL_MODEL_INSTANCE_COMMENTS="COMMENTS"))
    pyrevit = SimpleNamespace(DB=DB, routes=SimpleNamespace(make_response=lambda **kw: kw),
                              forms=SimpleNamespace(alert=lambda *a, **k: True))
    monkeypatch.setitem(sys.modules, "pyrevit", pyrevit)
    for name in ("long", "basestring", "unicode"):
        monkeypatch.setattr(builtins, name, {"long": int, "basestring": str, "unicode": str}[name],
                            raising=False)
    for name in MODULES:
        sys.modules.pop(name, None)
    annotation = importlib.import_module("revit_mcp.annotation")
    exports = importlib.import_module("revit_mcp.exports")
    Transaction.log = []
    Transaction.commit_status = "Committed"
    yield annotation, exports
    for name in MODULES:
        sys.modules.pop(name, None)


# ---- tag_elements ----

@pytest.mark.parametrize("builtin, first", [
    ("OST_Doors", "OST_DoorTags"),
    ("OST_Walls", "OST_WallTags"),
    ("OST_GenericModel", "OST_GenericModelTags"),
    ("OST_StructuralColumns", "OST_StructuralColumnTags"),
    ("OST_Stairs", "OST_StairsTags"),
])
def test_tag_category_candidates(revit_modules, builtin, first):
    annotation, _ = revit_modules
    names = annotation.tag_category_candidates(builtin)
    assert names[0] == first and names[-1] == "OST_MultiCategoryTags"
    assert len(names) == len(set(names))


def test_tag_category_candidates_without_builtin(revit_modules):
    annotation, _ = revit_modules
    assert annotation.tag_category_candidates(None) == ["OST_MultiCategoryTags"]


@pytest.mark.parametrize("requested, expected", [
    ("Etiqueta de porta", True),
    ("padrão", True),
    ("etiqueta de porta:  Padrão", True),
    ("Etiqueta de porta : padrão", True),
    ("Outra", False),
    ("", False),
])
def test_tag_type_matches(revit_modules, requested, expected):
    annotation, _ = revit_modules
    assert annotation.tag_type_matches(requested, u"Etiqueta de porta", u"Padrão") is expected


def test_bbox_centre(revit_modules):
    annotation, _ = revit_modules
    assert annotation.bbox_centre((0, 2, -1), (4, 6, 1)) == (2.0, 4.0, 0.0)


def test_plan_candidates_skips_tagged_unplaced_and_cuts_at_limit(revit_modules):
    annotation, _ = revit_modules
    elements = [(i, SimpleNamespace(placed=i != 3)) for i in range(1, 7)]
    to_tag, skipped, truncated = annotation.plan_candidates(
        elements, tagged_ids={2}, skip_tagged=True, limit=2,
        unplaced=lambda e: not e.placed)
    assert [i for i, _ in to_tag] == [1, 4]
    assert skipped == {"already_tagged": 1, "not_placed": 1}
    assert truncated is True


def test_plan_candidates_can_retag(revit_modules):
    annotation, _ = revit_modules
    elements = [(1, None), (2, None)]
    to_tag, skipped, truncated = annotation.plan_candidates(elements, {1}, False, 200)
    assert len(to_tag) == 2 and skipped == {} and truncated is False


def _creator(refuse=()):
    def create(element):
        if element in refuse:
            raise Exception(u"Não foi possível etiquetar")
        return SimpleNamespace(Id=eid(1000 + element))
    return create


def test_run_tagging_commits_and_keeps_partial_success(revit_modules):
    annotation, _ = revit_modules
    tagged, tag_ids, failed, error = annotation.run_tagging(
        None, [(1, 1), (2, 2), (3, 3)], _creator(refuse=(2,)), commit=True)
    assert tagged == [1, 3] and tag_ids == [1001, 1003] and error is None
    assert failed == [{"element_id": 2, "error": u"Não foi possível etiquetar"}]
    assert Transaction.log == ["commit"]


def test_run_tagging_dry_run_rolls_back(revit_modules):
    annotation, _ = revit_modules
    tagged, tag_ids, failed, error = annotation.run_tagging(
        None, [(1, 1)], _creator(), commit=False)
    assert tagged == [1] and tag_ids == [] and failed == []
    assert Transaction.log == ["rollback"]


def test_run_tagging_nothing_tagged_rolls_back(revit_modules):
    annotation, _ = revit_modules
    tagged, _, failed, _ = annotation.run_tagging(None, [(1, 1)], _creator(refuse=(1,)), True)
    assert tagged == [] and len(failed) == 1
    assert Transaction.log == ["rollback"]


def test_run_tagging_reports_failed_commit(revit_modules):
    annotation, _ = revit_modules
    Transaction.commit_status = "RolledBack"
    tagged, tag_ids, _, error = annotation.run_tagging(None, [(1, 1)], _creator(), True)
    assert tagged == [] and tag_ids == [] and "did not commit" in error


# ---- exports: CSV ----

def test_csv_text_quotes_and_accents(revit_modules):
    _, exports = revit_modules
    csv = exports.csv_text(
        ["Nome", u"Área"],
        [[u"Sala, estar", 12.5], [u'Hall "A"', None], [u"Cozinha\nnova", True], [u"Ação", 0]])
    assert csv == (u'Nome,Área\n"Sala, estar",12.5\n"Hall ""A""",\n'
                   u'"Cozinha\nnova",true\nAção,0\n')


def test_csv_text_empty(revit_modules):
    _, exports = revit_modules
    assert exports.csv_text(["a", "b"], []) == u"a,b\n"


@pytest.mark.parametrize("data, error", [
    ({}, None),
    ({"limit": 1000, "format": "CSV"}, None),
    ({"limit": 0}, "limit"),
    ({"limit": "x"}, "limit"),
    ({"format": "xml"}, "format"),
])
def test_parse_options(revit_modules, data, error):
    _, exports = revit_modules
    limit, output, message = exports.parse_options(data)
    if error is None:
        assert message is None and output in ("json", "csv")
    else:
        assert error in message


# ---- exports: schedules ----

class FakeSchedule:
    def __init__(self, cells, headings, hidden=()):
        self.cells = cells
        fields = [SimpleNamespace(ColumnHeading=h, IsHidden=h in hidden) for h in headings]
        self.Definition = SimpleNamespace(GetFieldCount=lambda: len(fields),
                                          GetField=lambda i: fields[i])
        section = SimpleNamespace(NumberOfRows=len(cells),
                                  NumberOfColumns=len(cells[0]) if cells else 0)
        self.GetTableData = lambda: SimpleNamespace(GetSectionData=lambda kind: section)

    def GetCellText(self, kind, row, column):
        return self.cells[row][column]


def test_read_schedule_uses_heading_row_and_drops_blank_rows(revit_modules):
    _, exports = revit_modules
    schedule = FakeSchedule([
        [u"Marca", u"Largura"],
        [u"", u""],
        [u"P1", u"80"],
        [u"", u""],
        [u"P2", u"90"],
        [u"P3", None],
    ], headings=[u"Marca", u"Largura", u"Oculto"], hidden=(u"Oculto",))
    columns, rows, body_rows, truncated = exports.read_schedule(schedule, limit=2)
    assert columns == [u"Marca", u"Largura"]
    assert rows == [[u"P1", u"80"], [u"P2", u"90"]]
    assert body_rows == 5 and truncated is True


def test_read_schedule_without_heading_row(revit_modules):
    _, exports = revit_modules
    schedule = FakeSchedule([[u"P1", u"80"]], headings=[u"Marca", u"Largura"])
    columns, rows, _, truncated = exports.read_schedule(schedule, limit=200)
    assert columns == [u"Marca", u"Largura"] and rows == [[u"P1", u"80"]]
    assert truncated is False


def test_read_schedule_generic_columns(revit_modules):
    _, exports = revit_modules
    schedule = FakeSchedule([[u"a", u"b", u"c"]], headings=[u"X"])
    columns, rows, _, _ = exports.read_schedule(schedule, limit=200)
    assert columns == [u"Column 1", u"Column 2", u"Column 3"] and rows == [[u"a", u"b", u"c"]]


# ---- exports: rooms ----

@pytest.mark.parametrize("has_location, area, status", [
    (False, 0, "unplaced"), (True, 0, "not_enclosed"), (True, 12.0, "placed")])
def test_room_status(revit_modules, has_location, area, status):
    _, exports = revit_modules
    assert exports.room_status(has_location, area) == status


class FakeParam:
    def __init__(self, value):
        self.value = value

    def AsString(self):
        return self.value

    def AsValueString(self):
        return None


class FakeRoom:
    """room.Name is deliberately missing, as in IronPython."""

    def __init__(self, room_id, number, name, level_id, area, perimeter=0.0, volume=0.0,
                 placed=True, department=None):
        self.Id = eid(room_id)
        self.LevelId = level_id
        self.Location = object() if placed else None
        self.Area, self.Perimeter, self.Volume = area, perimeter, volume
        self.params = {"ROOM_NUMBER": number, "ROOM_NAME": name, "ROOM_DEPARTMENT": department}

    def get_Parameter(self, builtin):
        return FakeParam(self.params.get(builtin))


def test_room_record_converts_units(revit_modules):
    _, exports = revit_modules
    level = eid(10)
    doc = SimpleNamespace(GetElement=lambda i: SimpleNamespace(name=u"Térreo"))
    room = FakeRoom(5, u"101", u"Sala de reunião", level, area=100.0, perimeter=40.0,
                    volume=1000.0, department=u"Adm")
    record = exports.room_record(doc, room, {}, include_details=True)
    assert record == {
        "element_id": 5, "number": u"101", "name": u"Sala de reunião", "level": u"Térreo",
        "area_m2": 9.29, "perimeter_m": 12.19, "volume_m3": 28.32, "status": "placed",
        "department": u"Adm", "comments": None}


def test_room_record_unplaced_without_level_or_volume(revit_modules):
    _, exports = revit_modules
    doc = SimpleNamespace(GetElement=lambda i: None)
    record = exports.room_record(doc, FakeRoom(6, u"102", u"Depósito", INVALID, 0.0, placed=False),
                                 {}, include_details=False)
    assert record["status"] == "unplaced" and record["level"] is None
    assert record["volume_m3"] is None and "department" not in record


def _record(number, level, status="placed", area=10.0):
    return {"number": number, "level": level, "status": status, "area_m2": area}


def test_summarize_rooms_filters_sorts_and_counts(revit_modules):
    _, exports = revit_modules
    records = [_record(u"201", u"Superior"), _record(u"102", u"Térreo"),
               _record(u"101", u"térreo "), _record(u"103", u"Térreo", "not_enclosed", 0.0),
               _record(u"104", None, "unplaced", 0.0)]
    returned, total, counts, area, truncated = exports.summarize_rooms(
        records, u"TÉRREO", include_unplaced=True, limit=2)
    assert [r["number"] for r in returned] == [u"101", u"102"]
    assert total == 3 and truncated is True
    assert counts == {"placed": 2, "unplaced": 0, "not_enclosed": 1} and area == 20.0

    returned, total, counts, _, _ = exports.summarize_rooms(records, None, False, 200)
    assert total == 3 and counts["unplaced"] == 0 and counts["not_enclosed"] == 0
