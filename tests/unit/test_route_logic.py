# -*- coding: utf-8 -*-
"""Route logic of revit_mcp/parameters.py and revit_mcp/health.py against a
fake Revit API, so all-or-nothing, dry-run rollback and health flags are
tested without Revit. The modules are IronPython code; py2 builtins are
injected for CPython."""
import builtins
import importlib
import sys
from types import SimpleNamespace

import pytest


class StorageType:
    String, Integer, Double, ElementId, None_ = "String", "Integer", "Double", "ElementId", "None"


class Transaction:
    log = []

    def __init__(self, doc, name):
        self.doc, self.name, self.started, self.ended = doc, name, False, False

    def Start(self):
        self.started = True
        self.doc.snapshot = {key: p.value for key, p in self.doc.all_parameters()}

    def Commit(self):
        self.ended = True
        Transaction.log.append("commit")
        return "Committed"

    def RollBack(self):
        self.ended = True
        Transaction.log.append("rollback")
        for key, parameter in self.doc.all_parameters():
            parameter.value = self.doc.snapshot[key]

    def HasStarted(self):
        return self.started

    def HasEnded(self):
        return self.ended


class FakeParameter:
    def __init__(self, storage, value, read_only=False, reject=False):
        self.StorageType, self.value, self.IsReadOnly, self.reject = storage, value, read_only, reject

    def AsString(self):
        return self.value

    def AsValueString(self):
        return None if self.value is None else str(self.value)

    def Set(self, value):
        if self.reject:
            return False
        self.value = value
        return True

    def SetValueString(self, text):
        if self.reject or not text[0].isdigit():
            return False
        self.value = text
        return True


class FakeElement:
    def __init__(self, params, type_id=None):
        self.params = params  # name -> list of FakeParameter
        self.type_id = type_id

    def GetParameters(self, name):
        return self.params.get(name, [])

    def get_Parameter(self, builtin):
        found = self.params.get(builtin, [])
        return found[0] if found else None

    def GetTypeId(self):
        return self.type_id if self.type_id is not None else "invalid"


class FakeDoc:
    def __init__(self, elements):
        self.elements = elements

    def GetElement(self, element_id):
        return self.elements.get(element_id)

    def all_parameters(self):
        for eid, element in self.elements.items():
            for name, params in element.params.items():
                for i, parameter in enumerate(params):
                    yield (eid, name, i), parameter


@pytest.fixture
def revit_modules(monkeypatch):
    DB = SimpleNamespace(
        StorageType=StorageType, Transaction=Transaction,
        TransactionStatus=SimpleNamespace(Committed="Committed"),
        ElementId=lambda value: value,
        BuiltInParameter=SimpleNamespace(INVALID="INVALID",
                                         ALL_MODEL_INSTANCE_COMMENTS="bip:comments"))
    DB.ElementId.InvalidElementId = "invalid"
    pyrevit = SimpleNamespace(DB=DB, routes=SimpleNamespace(make_response=lambda **kw: kw),
                              forms=SimpleNamespace(alert=lambda *a, **k: True))
    monkeypatch.setitem(sys.modules, "pyrevit", pyrevit)
    for name in ("long", "basestring", "unicode"):
        monkeypatch.setattr(builtins, name, {"long": int, "basestring": str, "unicode": str}[name],
                            raising=False)
    for name in ("revit_mcp.utils", "revit_mcp.security", "revit_mcp.parameters", "revit_mcp.health"):
        sys.modules.pop(name, None)
    parameters = importlib.import_module("revit_mcp.parameters")
    health = importlib.import_module("revit_mcp.health")
    Transaction.log = []
    yield parameters, health
    for name in ("revit_mcp.utils", "revit_mcp.security", "revit_mcp.parameters", "revit_mcp.health"):
        sys.modules.pop(name, None)


def doc_with(**params):
    return FakeDoc({1: FakeElement(params, type_id=2),
                    2: FakeElement({"Width": [FakeParameter(StorageType.Double, "80")]})})


# ---- bulk_set_parameters ----

@pytest.mark.parametrize("value, expected", [
    (True, True), (0, False), ("sim", True), (u"não", False), ("No", False), ("talvez", None), (2, None)])
def test_parse_bool(revit_modules, value, expected):
    parameters, _ = revit_modules
    assert parameters.parse_bool(value) is expected


def test_commit_applies_all_changes(revit_modules):
    parameters, _ = revit_modules
    doc = doc_with(Mark=[FakeParameter(StorageType.String, "P1")],
                   Fire=[FakeParameter(StorageType.Integer, 0)])
    result = parameters.apply_changes(doc, [
        {"element_id": 1, "parameter": "Mark", "value": "P01"},
        {"element_id": 1, "parameter": "Fire", "value": "sim"},
    ], use_type=False, commit=True)
    assert result["status"] == "success" and result["applied"] == 2
    assert result["results"][0]["before"] == "P1" and result["results"][0]["after"] == "P01"
    assert doc.elements[1].params["Fire"][0].value == 1
    assert Transaction.log == ["commit"]


def test_dry_run_rolls_back(revit_modules):
    parameters, _ = revit_modules
    doc = doc_with(Mark=[FakeParameter(StorageType.String, "P1")])
    result = parameters.apply_changes(
        doc, [{"element_id": 1, "parameter": "Mark", "value": "P01"}], False, commit=False)
    assert result["status"] == "dry_run"
    assert result["results"][0]["after"] == "P01"
    assert doc.elements[1].params["Mark"][0].value == "P1"
    assert Transaction.log == ["rollback"]


def test_validation_error_opens_no_transaction(revit_modules):
    parameters, _ = revit_modules
    doc = doc_with(Mark=[FakeParameter(StorageType.String, "P1")],
                   Area=[FakeParameter(StorageType.Double, "10", read_only=True)])
    result = parameters.apply_changes(doc, [
        {"element_id": 1, "parameter": "Mark", "value": "P01"},
        {"element_id": 1, "parameter": "Area", "value": "20"},
        {"element_id": 99, "parameter": "Mark", "value": "x"},
        {"element_id": 1, "parameter": "Missing", "value": "x"},
    ], False, commit=True)
    assert result["status"] == "error" and result["applied"] == 0
    assert [(e["index"], e["error"]) for e in result["errors"]] == [
        (1, "Parameter is read-only"), (2, "Element not found"),
        (3, "Parameter not found on the element")]
    assert Transaction.log == []
    assert doc.elements[1].params["Mark"][0].value == "P1"


def test_value_rejected_by_revit_rolls_back_everything(revit_modules):
    parameters, _ = revit_modules
    doc = doc_with(Mark=[FakeParameter(StorageType.String, "P1")],
                   Height=[FakeParameter(StorageType.Double, "100")])
    result = parameters.apply_changes(doc, [
        {"element_id": 1, "parameter": "Mark", "value": "P01"},
        {"element_id": 1, "parameter": "Height", "value": "abc"},
    ], False, commit=True)
    assert result["status"] == "error"
    assert result["errors"][0]["index"] == 1
    assert doc.elements[1].params["Mark"][0].value == "P1"
    assert Transaction.log == ["rollback"]


def test_ambiguous_and_unsupported_parameters(revit_modules):
    parameters, _ = revit_modules
    doc = doc_with(Mark=[FakeParameter(StorageType.String, "a"), FakeParameter(StorageType.String, "b")],
                   Level=[FakeParameter(StorageType.ElementId, 5)],
                   Count=[FakeParameter(StorageType.Integer, 1)])
    result = parameters.apply_changes(doc, [
        {"element_id": 1, "parameter": "Mark", "value": "x"},
        {"element_id": 1, "parameter": "Level", "value": 7},
        {"element_id": 1, "parameter": "Count", "value": "muitos"},
    ], False, commit=True)
    errors = [e["error"] for e in result["errors"]]
    assert "2 parameters named 'Mark'" in errors[0]
    assert "ElementId" in errors[1]
    assert "whole number" in errors[2]


def test_use_type_targets_the_type(revit_modules):
    parameters, _ = revit_modules
    doc = doc_with()
    result = parameters.apply_changes(
        doc, [{"element_id": 1, "parameter": "Width", "value": "90 cm"}], True, commit=True)
    assert result["status"] == "success"
    assert doc.elements[2].params["Width"][0].value == "90 cm"


@pytest.mark.parametrize("change, ok", [
    ({"element_id": 1, "parameter": "Mark", "value": "a"}, True),
    ({"element_id": "12", "parameter": "Mark", "value": 0}, True),
    ({"element_id": "x", "parameter": "Mark", "value": "a"}, False),
    ({"element_id": 1, "parameter": "", "value": "a"}, False),
    ({"element_id": 1, "parameter": "Mark", "value": None}, False),
    ("nope", False),
])
def test_validate_change(revit_modules, change, ok):
    parameters, _ = revit_modules
    assert (parameters.validate_change(change) is None) is ok


def test_use_type_rows_sharing_a_type(revit_modules):
    parameters, _ = revit_modules
    doc = doc_with()
    doc.elements[3] = FakeElement({}, type_id=2)
    same = parameters.apply_changes(doc, [
        {"element_id": 1, "parameter": "Width", "value": "90 cm"},
        {"element_id": 3, "parameter": "Width", "value": " 90 cm"},
    ], True, commit=False)
    assert same["status"] == "dry_run" and len(same["results"]) == 1
    assert same["collapsed"] == [{"index": 1, "same_as": 0}]
    Transaction.log = []
    clash = parameters.apply_changes(doc, [
        {"element_id": 1, "parameter": "Width", "value": "90 cm"},
        {"element_id": 3, "parameter": "Width", "value": "80 cm"},
    ], True, commit=True)
    assert clash["status"] == "error" and Transaction.log == []
    assert [e["index"] for e in clash["errors"]] == [0, 1]
    assert "[0, 1]" in clash["errors"][0]["error"] and "type" in clash["errors"][0]["error"]
    assert doc.elements[2].params["Width"][0].value == "80"


def test_instances_are_not_grouped_across_elements(revit_modules):
    parameters, _ = revit_modules
    doc = doc_with(Mark=[FakeParameter(StorageType.String, "a")])
    doc.elements[3] = FakeElement({"Mark": [FakeParameter(StorageType.String, "b")]})
    result = parameters.apply_changes(doc, [
        {"element_id": 1, "parameter": "Mark", "value": "x"},
        {"element_id": 3, "parameter": "Mark", "value": "y"},
    ], False, commit=True)
    assert result["status"] == "success" and result["applied"] == 2
    assert "collapsed" not in result and "warnings" not in result


def test_empty_value_skipped_for_non_text_parameters(revit_modules):
    parameters, _ = revit_modules
    doc = doc_with(Mark=[FakeParameter(StorageType.String, "P1")],
                   Height=[FakeParameter(StorageType.Double, "100")],
                   Count=[FakeParameter(StorageType.Integer, 1)])
    result = parameters.apply_changes(doc, [
        {"element_id": 1, "parameter": "Mark", "value": ""},
        {"element_id": 1, "parameter": "Height", "value": ""},
        {"element_id": 1, "parameter": "Count", "value": "  "},
    ], False, commit=True)
    assert result["status"] == "success" and result["applied"] == 1
    assert [w["index"] for w in result["warnings"]] == [1, 2]
    assert doc.elements[1].params["Mark"][0].value == ""
    assert doc.elements[1].params["Height"][0].value == "100"


@pytest.mark.parametrize("units, value, sent", [
    ("Dot", "2,40", "2.40"), ("Comma", "2.40", "2,40"), ("Comma", "1.234,5 cm", "1234,5 cm"),
    ("Dot", "1,234.5", "1234.5"), ("Dot", "110 cm", None),
])
def test_normalize_numbers_only_for_double(revit_modules, units, value, sent):
    parameters, _ = revit_modules
    doc = doc_with(Mark=[FakeParameter(StorageType.String, "P1")],
                   Height=[FakeParameter(StorageType.Double, "100")])
    doc.GetUnits = lambda: SimpleNamespace(DecimalSymbol=units)
    result = parameters.apply_changes(doc, [
        {"element_id": 1, "parameter": "Mark", "value": value},
        {"element_id": 1, "parameter": "Height", "value": value},
    ], False, commit=False, normalize_numbers=True)
    assert result["status"] == "dry_run", result
    assert "sent" not in result["results"][0]
    assert result["results"][1].get("sent") == sent
    assert result["number_format"] == {"decimal_symbol": "," if units == "Comma" else ".",
                                       "normalized": 1 if sent else 0}


def test_number_format_absent_by_default(revit_modules):
    parameters, _ = revit_modules
    doc = doc_with(Height=[FakeParameter(StorageType.Double, "100")])
    result = parameters.apply_changes(
        doc, [{"element_id": 1, "parameter": "Height", "value": "2,40"}], False, commit=False)
    assert "number_format" not in result
    assert result["results"][0]["after"] == "2,40" and "sent" not in result["results"][0]


@pytest.mark.parametrize("value, symbol, expected", [
    ("2,40", ".", "2.40"), ("2.40", ",", "2,40"), ("-0,5", ".", "-0.5"),
    ("1.234,56", ".", "1234.56"), ("1,234.56", ",", "1234,56"), ("1.234.567", ",", "1234567"),
    ("2,40 m", ".", "2.40 m"), ("12,5 m\u00b2", ".", "12.5 m\u00b2"), ("110 cm", ",", "110 cm"),
    ("2.40", ".", "2.40"), ("1.2.3,4,5", ".", "1.2.3,4,5"), ("3' 6\"", ".", "3' 6\""),
    ("Sala 1,2", ".", "Sala 1,2"), ("12,5x30", ".", "12,5x30"), ("", ".", ""), (2.4, ",", 2.4),
])
def test_normalize_number_text(revit_modules, value, symbol, expected):
    parameters, _ = revit_modules
    assert parameters.normalize_number_text(value, symbol) == expected


def test_group_changes(revit_modules):
    parameters, _ = revit_modules
    keep, collapsed, conflicts = parameters.group_changes([
        (0, ("t", "w"), "1"), (1, ("t", "w"), " 1"), (2, ("t", "h"), "a"),
        (3, ("u", "w"), "x"), (4, ("u", "w"), "y")])
    assert keep == {0, 2} and collapsed == [{"index": 1, "same_as": 0}] and conflicts == [[3, 4]]


def test_project_decimal_symbol(revit_modules):
    parameters, _ = revit_modules
    assert parameters.project_decimal_symbol(SimpleNamespace()) == "."
    assert parameters.project_decimal_symbol(
        SimpleNamespace(GetUnits=lambda: SimpleNamespace(DecimalSymbol="Comma"))) == ","


# ---- model_health_report ----

@pytest.mark.parametrize("total, flag", [(0, "ok"), (199, "ok"), (200, "medium"), (499, "medium"), (500, "high")])
def test_warning_flag(revit_modules, total, flag):
    _, health = revit_modules
    assert health.warning_flag(total) == flag


def test_health_flags(revit_modules):
    _, health = revit_modules
    report = {
        "warnings": {"flag": "high"},
        "imported_cad": {"count": 2},
        "in_place_families": {"count": 0},
        "views": {"placeable": 100, "not_on_sheets": 60, "unused_templates": 3},
        "revit_links": [{"name": "Estrutura.rvt", "status": "Loaded"},
                        {"name": "MEP.rvt", "status": "NotFound"}],
    }
    assert health.health_flags(report) == [
        "warnings_high", "imported_cad", "many_views_not_on_sheets",
        "unused_view_templates", "revit_links_not_loaded"]


def test_health_flags_clean_model(revit_modules):
    _, health = revit_modules
    report = {"warnings": {"flag": "ok"}, "imported_cad": {"count": 0},
              "in_place_families": {"count": 0},
              "views": {"placeable": 10, "not_on_sheets": 2, "unused_templates": 0},
              "revit_links": [{"name": "A.rvt", "status": "Loaded"}]}
    assert health.health_flags(report) == []


# ---- utils: localized names ----

@pytest.mark.parametrize("raw, expected", [
    (u"N\xc3\xadvel 1", u"N\xedvel 1"),   # UTF-8 read as Latin-1 by IronPython 2 unquote
    (u"N\xedvel 1", u"N\xedvel 1"),       # already right
    (u"Level 1", u"Level 1"),
    (u"平面", u"平面"),   # outside Latin-1: unchanged
])
def test_repair_utf8(revit_modules, raw, expected):
    utils = importlib.import_module("revit_mcp.utils")
    assert utils.repair_utf8(raw) == expected


@pytest.mark.parametrize("requested, localized, builtin, expected", [
    ("Paredes", "Paredes", "OST_Walls", True),
    ("paredes ", "Paredes", "OST_Walls", True),
    ("Walls", "Paredes", "OST_Walls", True),
    ("OST_Walls", "Paredes", "OST_Walls", True),
    ("Generic Models", "Modelos genéricos", "OST_GenericModel", True),
    ("Structural Columns", "Pilares estruturais", "OST_StructuralColumns", True),
    ("Doors", "Paredes", "OST_Walls", False),
    ("Walls", "Paredes", None, False),
    ("", "Paredes", "OST_Walls", False),
])
def test_category_matches(revit_modules, requested, localized, builtin, expected):
    utils = importlib.import_module("revit_mcp.utils")
    assert utils.category_matches(requested, localized, builtin) is expected


def test_find_category_prefers_exact_localized_name(revit_modules):
    utils = importlib.import_module("revit_mcp.utils")
    walls = SimpleNamespace(Name="Paredes", BuiltInCategory="OST_Walls")
    doors = SimpleNamespace(Name="Portas", BuiltInCategory="OST_Doors")
    doc = SimpleNamespace(Settings=SimpleNamespace(Categories=[walls, doors]))
    assert utils.find_category(doc, "Walls") is walls
    assert utils.find_category(doc, "Portas") is doors
    assert utils.find_category(doc, "Windows") is None


def test_parameter_by_builtin_name_works_in_any_language(revit_modules):
    parameters, _ = revit_modules
    doc = doc_with(**{u"Coment\u00e1rios": [], "bip:comments": [FakeParameter(StorageType.String, "")]})
    result = parameters.apply_changes(
        doc, [{"element_id": 1, "parameter": "ALL_MODEL_INSTANCE_COMMENTS", "value": "x"}],
        use_type=False, commit=False)
    assert result["status"] == "dry_run", result
    missing = parameters.apply_changes(
        doc, [{"element_id": 1, "parameter": "NOT_A_BUILTIN", "value": "x"}], use_type=False, commit=False)
    assert missing["status"] == "error"
