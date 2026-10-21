# -*- coding: utf-8 -*-
"""Route logic of revit_mcp/material_checks.py (spec 014) against a fake
Revit API. The module is IronPython code; py2 builtins are injected for
CPython."""
import builtins
import importlib
import sys
from types import SimpleNamespace

import pytest

MODULES = ("revit_mcp.utils", "revit_mcp.security", "revit_mcp.material_checks")


def eid(value):
    return SimpleNamespace(Value=value)


INVALID = eid(-1)


class ElementId:
    InvalidElementId = INVALID

    def __new__(cls, value):
        return eid(int(value))


class Solid:
    def __init__(self, volume):
        self.Volume = volume


class Mesh:
    pass


class GeometryInstance:
    def __init__(self, items):
        self.items = items

    def GetInstanceGeometry(self):
        return self.items


class View:
    def __init__(self, view_id, name, template=False):
        self.Id, self.name, self.IsTemplate = eid(view_id), name, template


class Collector:
    """FilteredElementCollector over doc.elements (or doc.visible[view])."""

    def __init__(self, doc, view_id=None):
        self.doc, self.view_id, self.items = doc, view_id, None

    def OfCategoryId(self, category_id):
        source = (self.doc.elements if self.view_id is None
                  else [e for e in self.doc.elements if e.Id.Value in self.doc.visible])
        self.items = [e for e in source if e.category_id == category_id.Value]
        return self

    def WhereElementIsNotElementType(self):
        return self

    def OfClass(self, cls):
        self.items = [v for v in self.doc.views]
        return self

    def __iter__(self):
        return iter(self.items)


class Param:
    def __init__(self, value):
        self.value = value

    def AsString(self):
        return self.value

    def AsValueString(self):
        return None


class FakeType:
    def __init__(self, type_id, family, name):
        self.Id, self.name = eid(type_id), name
        self.params = {"SYMBOL_FAMILY_NAME_PARAM": family, "SYMBOL_NAME_PARAM": name}

    def get_Parameter(self, builtin):
        value = self.params.get(builtin)
        return Param(value) if value is not None else None


class FakeElement:
    def __init__(self, element_id, category_id, type_id, materials=(), paint=(), geometry=(),
                 subs=()):
        self.Id, self.category_id, self.type_id = eid(element_id), category_id, eid(type_id)
        self.materials, self.paint, self.geometry = materials, paint, geometry
        self.subs = subs
        self.name = u"inst"

    def GetTypeId(self):
        return self.type_id

    def GetMaterialIds(self, paint):
        return [eid(m) for m in (self.paint if paint else self.materials)]

    def get_Geometry(self, options):
        return [GeometryInstance(list(self.geometry))]

    def GetSubComponentIds(self):
        return [eid(i) for i in self.subs]

    def get_Parameter(self, builtin):
        return None


@pytest.fixture
def revit_modules(monkeypatch):
    categories = {
        "OST_Furniture": SimpleNamespace(Id=eid(-2000080), Name=u"Mobiliário", Material=None,
                                         BuiltInCategory="OST_Furniture"),
        "OST_Walls": SimpleNamespace(Id=eid(-2000011), Name=u"Paredes",
                                     Material=SimpleNamespace(Id=eid(900)),
                                     BuiltInCategory="OST_Walls"),
    }
    DB = SimpleNamespace(
        ElementId=ElementId,
        Element=SimpleNamespace(Name=SimpleNamespace(GetValue=lambda e: e.name)),
        BuiltInCategory=SimpleNamespace(**{name: name for name in (
            "OST_Furniture", "OST_FurnitureSystems", "OST_Casework", "OST_GenericModel",
            "OST_PlumbingFixtures", "OST_LightingFixtures", "OST_SpecialityEquipment",
            "OST_Walls")}),
        Category=SimpleNamespace(GetCategory=lambda doc, bic: categories.get(bic)),
        BuiltInParameter=SimpleNamespace(
            SYMBOL_FAMILY_NAME_PARAM="SYMBOL_FAMILY_NAME_PARAM",
            SYMBOL_NAME_PARAM="SYMBOL_NAME_PARAM", ALL_MODEL_FAMILY_NAME="ALL_MODEL_FAMILY_NAME"),
        FilteredElementCollector=lambda doc, view_id=None: Collector(doc, view_id),
        Options=lambda: object(), Solid=Solid, Mesh=Mesh, GeometryInstance=GeometryInstance,
        View=View)
    pyrevit = SimpleNamespace(DB=DB, routes=SimpleNamespace(make_response=lambda **kw: kw),
                              forms=SimpleNamespace(alert=lambda *a, **k: True))
    monkeypatch.setitem(sys.modules, "pyrevit", pyrevit)
    for name in ("long", "basestring", "unicode"):
        monkeypatch.setattr(builtins, name, {"long": int, "basestring": str, "unicode": str}[name],
                            raising=False)
    for name in MODULES:
        sys.modules.pop(name, None)
    module = importlib.import_module("revit_mcp.material_checks")
    module.categories = categories
    yield module
    for name in MODULES:
        sys.modules.pop(name, None)


# ---- pure helpers ----

def test_strip_accents(revit_modules):
    m = revit_modules
    assert m.strip_accents(u"POLTRONA_TECIDO_AÇÃO_01") == u"POLTRONA_TECIDO_ACAO_01"
    assert m.strip_accents(u"Mármore") == u"Marmore"
    assert m.strip_accents(None) == u""


def test_strip_accents_fallback_without_unicodedata(revit_modules, monkeypatch):
    monkeypatch.setattr(revit_modules, "unicodedata", None)
    assert revit_modules.strip_accents(u"Ação Mármore") == u"Acao Marmore"


@pytest.mark.parametrize("name, ok", [
    (u"POLTRONA_TECIDO_ASSENTO_01", True),
    (u"POLTRONA_MADEIRA_PÉ_03", True),
    (u"MESA_VIDRO_TAMPO_1", False),
    (u"Poltrona_Tecido_Assento_01", False),
    (u"TECIDO_01", False),
    (u"Madeira", False),
    (u"POLTRONA TECIDO ASSENTO 01", False),
])
def test_default_name_pattern(revit_modules, name, ok):
    regex, error = revit_modules.compile_pattern(None)
    assert error is None and revit_modules.name_ok(name, regex) is ok


def test_compile_pattern_errors(revit_modules):
    assert revit_modules.compile_pattern("(")[1]
    assert revit_modules.compile_pattern("  ")[1]
    regex, error = revit_modules.compile_pattern("^MAT_")
    assert error is None and revit_modules.name_ok(u"MAT_x", regex)


def test_duplicate_groups(revit_modules):
    groups = revit_modules.duplicate_groups([
        u"Madeira Carvalho", u"MADEIRA_CARVALHO", u"madeira-carvalho 02",
        u"POLTRONA_TECIDO_ASSENTO_01", u"POLTRONA_TECIDO_ASSENTO_02",
        u"POLTRONA_TECIDO_ENCOSTO_02", u"Vidro", u"123"])
    by_key = dict((g["key"], g) for g in groups)
    assert set(by_key) == {u"madeiracarvalho", u"poltronatecidoassento"}
    assert by_key[u"madeiracarvalho"]["kind"] == "duplicate"
    assert len(by_key[u"madeiracarvalho"]["materials"]) == 3
    assert by_key[u"poltronatecidoassento"]["kind"] == "similar"


def test_shared_materials(revit_modules):
    records = [{"family": u"Poltrona", "material_ids": [1, 2]},
               {"family": u"Sofá", "material_ids": [1]},
               {"family": u"Mesa", "material_ids": [], "paint_ids": [1]},
               {"family": u"Mesa", "material_ids": [2]}]
    assert revit_modules.shared_materials(records, 2) == [(1, [u"Mesa", u"Poltrona", u"Sofá"])]
    assert revit_modules.shared_materials(records, 3) == []


def _record(**kw):
    record = {"family": u"Poltrona", "type": u"Padrão", "category": u"Mobiliário",
              "element_id": 10, "material_ids": [], "paint_ids": [],
              "category_material_id": None, "is_furniture": True, "pieces": 3}
    record.update(kw)
    return record


NAMES = {1: u"POLTRONA_TECIDO_ASSENTO_01", 2: u"POLTRONA_MADEIRA_PE_02", 9: u"Mobiliário padrão"}


@pytest.mark.parametrize("record, rules", [
    (_record(), ["no_material"]),
    (_record(category_material_id=9), ["category_material_only"]),
    (_record(category_material_id=9, material_ids=[9]), ["category_material_only"]),
    (_record(material_ids=[1]), ["single_material"]),
    (_record(material_ids=[1], pieces=1), []),
    (_record(material_ids=[1], pieces=None), ["single_material"]),
    (_record(material_ids=[1], is_furniture=False), []),
    (_record(material_ids=[1, 2]), []),
    (_record(paint_ids=[1, 2]), []),
    (_record(category_material_id=9, material_ids=[9], paint_ids=[1]), []),
])
def test_type_problems(revit_modules, record, rules):
    problems = revit_modules.type_problems(record, 2, NAMES)
    assert [p["rule"] for p in problems] == rules
    for problem in problems:
        assert problem["family"] == u"Poltrona" and problem["type"] == u"Padrão"


def test_type_problems_lists_paint_and_severity(revit_modules):
    problems = revit_modules.type_problems(_record(paint_ids=[1]), 2, NAMES)
    assert problems[0]["rule"] == "single_material" and problems[0]["severity"] == "review"
    assert problems[0]["painted"] == [u"POLTRONA_TECIDO_ASSENTO_01"]
    assert problems[0]["material"] == u"POLTRONA_TECIDO_ASSENTO_01"


def test_check_records_summary_and_order(revit_modules):
    m = revit_modules
    names = {1: u"Tecido", 2: u"TECIDO 02", 3: u"POLTRONA_MADEIRA_PE_03"}
    records = [_record(family=u"Poltrona", material_ids=[1, 3]),
               _record(family=u"Sofá", material_ids=[1]),
               _record(family=u"Banco", material_ids=[1, 2])]
    regex, _ = m.compile_pattern(None)
    summary, problems = m.check_records(records, names, regex, 2, 2)
    assert summary == {"name_pattern": 2, "duplicate_names": 1, "generic_shared": 1,
                       "single_material": 1, "category_material_only": 0, "no_material": 0,
                       "painted_types": 0, "mesh_only_types": 0}
    assert [p["severity"] for p in problems] == ["error"] * 3 + ["review"] * 2
    assert problems[0]["rule"] == "name_pattern"
    shared = [p for p in problems if p["rule"] == "generic_shared"][0]
    assert shared["material"] == u"Tecido" and shared["families"] == [u"Banco", u"Poltrona", u"Sofá"]
    duplicate = [p for p in problems if p["rule"] == "duplicate_names"][0]
    assert duplicate["severity"] == "review" and duplicate["materials"] == [u"TECIDO 02", u"Tecido"]


def test_cap(revit_modules):
    assert revit_modules.cap([1, 2, 3], 2) == ([1, 2], True)
    assert revit_modules.cap([1], 2) == ([1], False)


@pytest.mark.parametrize("data, error", [
    ({}, None),
    ({"limit": 1000, "categories": [u"Mobiliário"]}, None),
    ({"limit": 0}, "limit"),
    ({"limit": "5"}, "limit"),
    ({"limit": True}, "limit"),
    ({"max_families_per_material": 0}, "max_families_per_material"),
    ({"min_materials_per_family": 0}, "min_materials_per_family"),
    ({"name_pattern": "["}, "name_pattern"),
    ({"categories": []}, "categories"),
    ({"categories": "Mobiliário"}, "categories"),
])
def test_parse_options(revit_modules, data, error):
    options, message = revit_modules.parse_options(data)
    if error is None:
        assert message is None and options["limit"] >= 1
    else:
        assert error in message


# ---- route ----

class FakeDoc:
    Title = u"Apartamento Térreo"

    def __init__(self, categories):
        self.types = {100: FakeType(100, u"Poltrona", u"Padrão"),
                      200: FakeType(200, u"Sofá", u"3 lugares"),
                      300: FakeType(300, u"Parede básica", u"Alvenaria")}
        self.materials = {1: u"POLTRONA_TECIDO_ASSENTO_01", 2: u"POLTRONA_MADEIRA_PE_02",
                          900: u"Padrão de parede"}
        furniture = categories["OST_Furniture"].Id.Value
        walls = categories["OST_Walls"].Id.Value
        self.elements = [
            FakeElement(10, furniture, 100, materials=[1, 2], geometry=[Solid(1), Solid(2)]),
            FakeElement(11, furniture, 100, materials=[1, 2]),
            FakeElement(20, furniture, 200, materials=[1], geometry=[Solid(1), Solid(1)]),
            FakeElement(30, walls, 300, materials=[900]),
        ]
        self.visible = {10, 11, 30}
        self.views = [View(5, u"3D Render")]
        self.Settings = SimpleNamespace(Categories=list(categories.values()))

    def GetElement(self, element_id):
        value = element_id.Value
        nested = getattr(self, "nested", {})
        if value in nested:
            return nested[value]
        if value in self.types:
            return self.types[value]
        if value in self.materials:
            return SimpleNamespace(name=self.materials[value])
        return next((v for v in self.views if v.Id.Value == value), None)


@pytest.fixture
def route(revit_modules, monkeypatch):
    monkeypatch.setattr(revit_modules, "authorize_request", lambda request, action=None: None)
    handlers = {}

    class Api:
        def route(self, path, methods):
            def decorator(function):
                handlers[path] = function
                return function
            return decorator

    revit_modules.register_material_check_routes(Api())
    handler = handlers["/check_render_materials/"]
    doc = FakeDoc(revit_modules.categories)
    return lambda body, d=doc: handler(d, SimpleNamespace(data=body))


def test_route_default_categories_per_type(route):
    response = route({})
    data = response["data"]
    assert data["status"] == "success" and "status" not in response
    assert data["elements_checked"] == 3 and data["types_checked"] == 2
    assert data["materials_checked"] == 2
    assert data["categories"] == [u"Mobiliário"]
    assert data["summary"]["single_material"] == 1
    problem = data["problems"][0]
    assert problem["rule"] == "single_material" and problem["family"] == u"Sofá"
    assert problem["type"] == u"3 lugares" and problem["element_id"] == 20
    assert data["truncated"] is False


def test_route_visible_in_view_and_category_material(route):
    data = route({"only_visible_in_view": u"3d  render", "categories": ["Walls", u"Mobiliário"]})["data"]
    assert data["view"] == {"id": 5, "name": u"3D Render"}
    assert data["types_checked"] == 2
    rules = sorted(p["rule"] for p in data["problems"])
    assert rules == ["category_material_only", "name_pattern"]
    wall = [p for p in data["problems"] if p["rule"] == "category_material_only"][0]
    assert wall["family"] == u"Parede básica" and wall["material"] == u"Padrão de parede"


def test_route_view_by_id_and_limit(route):
    data = route({"only_visible_in_view": 5, "categories": ["OST_Walls"], "limit": 1})["data"]
    assert data["returned"] == 1 and data["problems_total"] == 2 and data["truncated"] is True


@pytest.mark.parametrize("body, status", [
    ({"categories": ["Inexistente"]}, 400),
    ({"limit": 0}, 400),
    ({"only_visible_in_view": u"Nenhuma"}, 404),
    ({"only_visible_in_view": 77}, 404),
])
def test_route_errors(route, body, status):
    response = route(body)
    assert response["status"] == status and "error" in response["data"]


def test_route_without_document(revit_modules, route):
    handler_doc = None
    assert route({}, d=handler_doc)["status"] == 503


# ---- review fixes ----

def test_piece_helpers(revit_modules):
    m = revit_modules
    assert m.merge_ids([1, 2], [2, 3]) == [1, 2, 3] and m.merge_ids(None, [4]) == [4]
    assert m.piece_count({"solids": 2, "meshes": 1}, 2) == 5
    assert m.piece_count(None, 3) is None
    assert m.mesh_only({"solids": 0, "meshes": 3}) and not m.mesh_only({"solids": 1, "meshes": 3})
    assert not m.mesh_only(None) and not m.mesh_only({"solids": 0, "meshes": 0})


def test_type_problems_note_for_mesh_only(revit_modules):
    problems = revit_modules.type_problems(_record(material_ids=[1], mesh_only=True), 2, NAMES)
    assert problems[0]["rule"] == "single_material" and "meshes" in problems[0]["note"]


def test_route_counts_nested_materials_and_meshes(revit_modules, route):
    categories = revit_modules.categories
    furniture = categories["OST_Furniture"].Id.Value
    doc = FakeDoc(categories)
    # Chair with one solid in its own geometry; the leg is a nested shared family
    # with its own material; the cushion is a mesh (imported geometry).
    leg = FakeElement(81, furniture, 100, materials=[2], subs=[82])
    foot = FakeElement(82, furniture, 100, materials=[1])
    doc.nested = {81: leg, 82: foot}
    doc.elements = [FakeElement(10, furniture, 100, materials=[1],
                                geometry=[Solid(1), Mesh()], subs=[81])]
    data = route({}, d=doc)["data"]
    assert data["materials_checked"] == 2           # parent 1 + nested 2 (and 1 again)
    assert data["summary"]["single_material"] == 0
    assert data["summary"]["mesh_only_types"] == 0


def test_route_flags_mesh_only_geometry(revit_modules, route):
    categories = revit_modules.categories
    furniture = categories["OST_Furniture"].Id.Value
    doc = FakeDoc(categories)
    doc.elements = [FakeElement(10, furniture, 100, materials=[1], geometry=[Mesh(), Mesh()])]
    data = route({}, d=doc)["data"]
    assert data["summary"]["mesh_only_types"] == 1
    problem = data["problems"][0]
    assert problem["rule"] == "single_material" and "meshes" in problem["note"]
