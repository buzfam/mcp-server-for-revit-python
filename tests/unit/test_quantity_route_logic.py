# -*- coding: utf-8 -*-
"""Pure measurement logic of revit_mcp/quantities.py (spec 015): rules,
opening deduction, skirting, rounding, waste and grouping. The module is
IronPython code; py2 builtins are injected for CPython."""
import builtins
import importlib
import sys
from types import SimpleNamespace

import pytest

MODULES = ("revit_mcp.utils", "revit_mcp.security", "revit_mcp.exports", "revit_mcp.quantities")


def eid(value):
    return SimpleNamespace(Value=value)


INVALID = eid(-1)


class CurveElement:
    pass


@pytest.fixture
def q(monkeypatch):
    DB = SimpleNamespace(ElementId=SimpleNamespace(InvalidElementId=INVALID),
                         CurveElement=CurveElement)
    pyrevit = SimpleNamespace(DB=DB, routes=SimpleNamespace(make_response=lambda **kw: kw),
                              forms=SimpleNamespace(alert=lambda *a, **k: True))
    monkeypatch.setitem(sys.modules, "pyrevit", pyrevit)
    for name in ("long", "basestring", "unicode"):
        monkeypatch.setattr(builtins, name, {"long": int, "basestring": str, "unicode": str}[name],
                            raising=False)
    for name in MODULES:
        sys.modules.pop(name, None)
    yield importlib.import_module("revit_mcp.quantities")
    for name in MODULES:
        sys.modules.pop(name, None)


def rules(q, **overrides):
    result, error = q.parse_rules(overrides or None)
    assert error is None
    return result


# ---- rules ----

def test_parse_rules_defaults(q):
    result, error = q.parse_rules(None)
    assert error is None
    assert result == {"deduct_openings": "over_threshold", "deduct_openings_over_m2": 2.0,
                      "skirting_subtract_doors": True, "wall_height_m": None,
                      "use_modeled_elements": True, "rounding": 2, "waste_percent": 0.0}


def test_parse_rules_merges_and_normalizes(q):
    result, error = q.parse_rules({"deduct_openings": "ALL", "rounding": 3, "waste_percent": 10,
                                   "wall_height_m": 2.7, "skirting_subtract_doors": None})
    assert error is None
    assert result["deduct_openings"] == "all" and result["rounding"] == 3
    assert result["waste_percent"] == 10.0 and result["wall_height_m"] == 2.7
    assert result["skirting_subtract_doors"] is True  # None keeps the default


@pytest.mark.parametrize("raw, message", [
    ("x", "object"),
    ({"bogus": 1}, "unknown rules: bogus"),
    ({"deduct_openings": "some"}, "deduct_openings"),
    ({"deduct_openings_over_m2": -1}, "deduct_openings_over_m2"),
    ({"deduct_openings_over_m2": True}, "deduct_openings_over_m2"),
    ({"skirting_subtract_doors": "yes"}, "skirting_subtract_doors"),
    ({"use_modeled_elements": 1}, "use_modeled_elements"),
    ({"wall_height_m": 0}, "wall_height_m"),
    ({"wall_height_m": 25}, "wall_height_m"),
    ({"rounding": 5}, "rounding"),
    ({"rounding": 1.5}, "rounding"),
    ({"waste_percent": 101}, "waste_percent"),
    ({"waste_percent": float("nan")}, "waste_percent"),
])
def test_parse_rules_rejects(q, raw, message):
    result, error = q.parse_rules(raw)
    assert result is None and message in error


def test_parse_groups(q):
    assert q.parse_groups(None) == (list(q.GROUPS), None)
    assert q.parse_groups(["pintura", " Esquadrias "]) == (["esquadrias", "pintura"], None)
    assert "non-empty" in q.parse_groups([])[1]
    assert "unknown group" in q.parse_groups(["marcenaria"])[1]


# ---- opening deduction ----

@pytest.mark.parametrize("mode, area, expected", [
    ("over_threshold", 1.68, False),     # door 0.80 x 2.10
    ("over_threshold", 2.0, False),      # exactly the threshold: not deducted
    ("over_threshold", 2.0000000001, False),  # float noise from feet
    ("over_threshold", 2.4, True),
    ("all", 0.5, True),
    ("none", 9.0, False),
    ("over_threshold", None, False),     # unknown size is never deducted
])
def test_opening_deducted(q, mode, area, expected):
    assert q.opening_deducted(area, rules(q, deduct_openings=mode)) is expected


def test_net_wall_area_over_threshold(q):
    walls = q.net_wall_area(30.0, [1.68, 2.4, None], rules(q))
    assert walls == {"gross": 30.0, "net": 27.6, "deducted": 2.4,
                     "deducted_count": 1, "kept_count": 2}


def test_net_wall_area_custom_threshold_and_floor_at_zero(q):
    walls = q.net_wall_area(1.0, [1.68, 2.4], rules(q, deduct_openings_over_m2=1.0))
    assert walls["deducted_count"] == 2 and walls["net"] == 0.0


def test_net_wall_area_without_height(q):
    walls = q.net_wall_area(None, [3.0], rules(q))
    assert walls["gross"] is None and walls["net"] is None and walls["deducted"] == 3.0


# ---- skirting, height, area ----

def test_skirting_length(q):
    assert q.skirting_length(14.0, [0.8, 0.9, None], True) == pytest.approx((12.3, 1.7))
    assert q.skirting_length(14.0, [0.8], False) == (14.0, 0.0)
    assert q.skirting_length(1.0, [0.8, 0.9], True)[0] == 0.0


def test_choose_height(q):
    assert q.choose_height(2.7, 2.5, 3.0) == (2.7, "rule:wall_height_m")
    assert q.choose_height(None, 2.5, 3.0) == (2.5, "ceiling_height")
    assert q.choose_height(None, None, 3.0) == (3.0, "room_unbounded_height")
    assert q.choose_height(None, 0.0, 0.0) == (None, "unknown")


def test_choose_area(q):
    assert q.choose_area([5.0, 4.5], 10.0, True) == (9.5, "modeled_elements", None)
    area, source, note = q.choose_area([], 10.0, True)
    assert (area, source) == (10.0, "room_area") and note
    area, source, note = q.choose_area([40.0], 10.0, True)  # slab across several rooms
    assert (area, source) == (10.0, "room_area") and "40.00" in note
    assert q.choose_area([9.0], 10.0, False) == (10.0, "room_area", None)


def test_pick_positive_and_size_source(q):
    value, label = q.pick_positive([("instance:DOOR_WIDTH", None), ("type:DOOR_WIDTH", 0.0),
                                    ("type:FAMILY_WIDTH_PARAM", 2.62)])
    assert (value, label) == (2.62, "type:FAMILY_WIDTH_PARAM")
    assert q.pick_positive([("a", True), ("b", -1)]) == (None, None)
    assert q.size_source("a", "b") == "width=a; height=b"
    assert q.size_source(None, "b") == "missing"


# ---- rounding and waste ----

def test_round_value(q):
    assert q.round_value(None, 2) is None
    assert q.round_value(1.005 + 1e-9, 2) == 1.01
    assert q.round_value(-0.001, 2) == 0.0 and str(q.round_value(-0.001, 2)) == "0.0"
    assert q.round_value(12.3456, 0) == 12.0


def test_make_line_waste_skips_units(q):
    r = rules(q, waste_percent=10, rounding=1)
    line = q.make_line("pisos", "Piso A", "area", "m2", 10.04, "rule", r, {"count": 2})
    assert line == {"group": "pisos", "item": "Piso A", "measure": "area", "unit": "m2",
                    "quantity": 10.0, "quantity_with_waste": 11.0, "rule": "rule", "count": 2}
    count = q.make_line("esquadrias", "P", "count", "un", 3, "rule", r)
    assert count["quantity"] == 3 and "quantity_with_waste" not in count
    assert "quantity_with_waste" not in q.make_line("pisos", "x", "area", "m2", 1.0, "r", rules(q))


# ---- rooms ----

def raw_room(**overrides):
    raw = {"element_id": 1, "number": "101", "name": u"Sala", "level": u"Térreo",
           "phase": u"Nova construção", "room_area_m2": 12.0, "perimeter_m": 14.0,
           "wall_length_m": 14.0, "separation_length_m": 0.0, "height_m": 2.5,
           "height_source": "ceiling_height", "floor_m2": 12.0, "floor_source": "room_area",
           "floor_note": None, "floor_ids": [], "ceiling_m2": 12.0,
           "ceiling_source": "modeled_elements", "ceiling_note": None, "ceiling_ids": [7],
           "openings": [
               {"element_id": 10, "kind": "door", "family": "Porta", "type": "80x210",
                "width_m": 0.8, "height_m": 2.1, "area_m2": 1.68, "size_source": "x"},
               {"element_id": 11, "kind": "window", "family": "Janela", "type": "200x120",
                "width_m": 2.0, "height_m": 1.2, "area_m2": 2.4, "size_source": "x"}],
           "notes": []}
    raw.update(overrides)
    return raw


def test_room_measures(q):
    measures = q.room_measures(raw_room(), rules(q))
    assert measures["walls"]["gross"] == 35.0
    assert measures["walls"]["net"] == pytest.approx(32.6)  # only the 2.4 m2 window
    assert measures["skirting"] == pytest.approx(13.2)      # minus the door only
    assert measures["painting"] == pytest.approx(44.6)


def test_room_measures_deduct_all_and_keep_doors(q):
    measures = q.room_measures(raw_room(), rules(q, deduct_openings="all",
                                                  skirting_subtract_doors=False))
    assert measures["walls"]["net"] == pytest.approx(30.92)
    assert measures["skirting"] == 14.0


def test_room_output_sources_and_notes(q):
    out = q.room_output(raw_room(separation_length_m=3.0), rules(q))
    assert out["walls"]["gross_m2"] == 35.0 and out["walls"]["net_m2"] == 32.6
    assert out["walls"]["openings_deducted"] == 1 and out["walls"]["openings_not_deducted"] == 1
    assert out["height"] == {"value_m": 2.5, "source": "ceiling_height"}
    assert out["ceiling"]["source"] == "modeled_elements"
    assert [o["deducted"] for o in out["openings"]] == [False, True]
    assert out["skirting"]["doors_subtracted_m"] == 0.8
    assert any(u"separadoras" in note for note in out["notes"])


def test_room_output_without_height(q):
    out = q.room_output(raw_room(height_m=None, height_source="unknown"), rules(q))
    assert out["walls"]["gross_m2"] is None and out["painting"]["area_m2"] is None
    assert any("wall_height_m" in note for note in out["notes"])


def test_room_totals_sum_unrounded(q):
    r = rules(q, rounding=0)
    raws = [raw_room(floor_m2=0.4), raw_room(floor_m2=0.4)]
    assert q.room_totals(raws, r)["floor_m2"] == 1.0  # 0.8, not 0 + 0


def test_rule_texts_follow_rules(q):
    texts = q.rule_texts(rules(q, deduct_openings_over_m2=1.5, wall_height_m=2.8,
                               skirting_subtract_doors=False, waste_percent=5))
    assert u"1.50 m²" in texts["paredes"] and u"2.80 m" in texts["paredes"]
    assert u"sem descontar portas" in texts["rodape"]
    assert u"5%" in texts["perdas"]
    assert u"nenhum vão" in q.rule_texts(rules(q, deduct_openings="none"))["alvenaria"]
    block = q.rules_block(rules(q))
    assert block["applied"]["deduct_openings"] == "over_threshold"
    assert block["units"]["area"] == "m2"


# ---- grouping ----

def test_group_records_sorted_case_insensitive(q):
    records = [{"type": "b"}, {"type": "A"}, {"type": "b"}]
    groups = q.group_records(records, ("type",))
    assert [(key, len(items)) for key, items in groups] == [(("A",), 1), (("b",), 2)]
    widths = q.group_records([{"w": 10.0}, {"w": 9.0}, {"w": None}], ("w",))
    assert [key for key, _ in widths] == [(9.0,), (10.0,), (None,)]


def test_opening_lines_group_by_type_and_size(q):
    def door(width, area=True):
        return {"kind": "door", "family": "Porta", "type": "P1", "width_m": width,
                "height_m": 2.1, "area_m2": width * 2.1 if area else None,
                "width_key": round(width, 3), "height_key": 2.1, "size_source": "s"}
    lines = q.opening_lines([door(0.9), door(0.8), door(0.8)], rules(q))
    assert [(l["item"], l["quantity"], l["width_m"], l["area_m2"]) for l in lines] == [
        (u"Porta - Porta: P1", 2, 0.8, 3.36), (u"Porta - Porta: P1", 1, 0.9, 1.89)]
    assert lines[0]["unit"] == "un" and lines[0]["rule"]


def test_count_lines(q):
    records = [{"category": "Pecas", "family": "Bacia", "type": "Padrao"}] * 3 + [
        {"category": "Pecas", "family": "Lavatorio", "type": None}]
    lines = q.count_lines(records, "loucas_metais", "rule", rules(q))
    assert [(l["item"], l["quantity"]) for l in lines] == [
        ("Pecas - Bacia: Padrao", 3), ("Pecas - Lavatorio", 1)]


def test_host_lines(q):
    lines = q.host_lines([{"type": "Forro gesso", "area_m2": 10.111},
                          {"type": "Forro gesso", "area_m2": 5.0}], "forros", "rule", rules(q))
    assert lines == [{"group": "forros", "item": "Forro gesso", "measure": "area", "unit": "m2",
                      "quantity": 15.11, "rule": "rule", "count": 2}]


def test_wall_lines_apply_opening_rule(q):
    records = [{"type": "Alvenaria 14", "length_m": 5.0, "height_m": 2.8, "volume_m3": 1.9,
                "opening_areas": [1.68, 2.4]},
               {"type": "Alvenaria 14", "length_m": 3.0, "height_m": 2.8, "volume_m3": None,
                "opening_areas": []}]
    lines = q.wall_lines(records, rules(q))
    values = dict((l["measure"], l["quantity"]) for l in lines)
    assert values == {"length": 8.0, "gross_area": 22.4, "net_area": 20.0, "volume": 1.9}
    assert all(l["count"] == 2 for l in lines)


def test_room_total_lines(q):
    raws = [raw_room(), raw_room(height_m=None)]
    lines = q.room_total_lines(raws, rules(q), ["rodapes", "pintura"])
    values = dict((l["item"], l["quantity"]) for l in lines)
    assert values[u"Rodapé (todos os ambientes)"] == 26.4
    assert values[u"Pintura de paredes"] == 32.6
    assert values[u"Pintura de forro/teto"] == 24.0
    assert values[u"Pintura total"] == 56.6
    assert lines[1]["rooms_without_height"] == 1
    assert [l["group"] for l in q.room_total_lines(raws, rules(q), ["rodapes"])] == ["rodapes"]


def test_parse_limit(q):
    assert q.parse_limit({}) == (200, None)
    assert q.parse_limit({"limit": 0})[1]
    assert q.parse_limit({"limit": "x"})[1]


# ---- Revit readers against small fakes ----

class FakeRoom:
    def __init__(self, room_id):
        self.Id = eid(room_id)


class FakeOpening:
    def __init__(self, from_room, to_room):
        self.from_room, self.to_room = from_room, to_room

    def get_FromRoom(self, phase):
        return self.from_room

    def get_ToRoom(self, phase):
        return self.to_room


def test_openings_by_room_counts_both_sides_once(q):
    a, b = FakeRoom(1), FakeRoom(2)
    openings = [({"element_id": 10}, FakeOpening(a, b)), ({"element_id": 11}, FakeOpening(a, None)),
                ({"element_id": 12}, FakeOpening(a, a))]
    mapping = q.openings_by_room(openings, phase=object())
    assert [o["element_id"] for o in mapping[1]] == [10, 11, 12]
    assert [o["element_id"] for o in mapping[2]] == [10]


def test_room_boundary_separates_room_separation_lines(q):
    class Segment:
        def __init__(self, length_ft, element_id):
            self.ElementId = element_id
            self.length = length_ft

        def GetCurve(self):
            return SimpleNamespace(Length=self.length)

    wall, line = object(), CurveElement()
    doc = SimpleNamespace(GetElement=lambda i: {5: wall, 6: line}[i.Value])
    room = SimpleNamespace(GetBoundarySegments=lambda options: [
        [Segment(10.0, eid(5)), Segment(5.0, eid(6)), Segment(2.0, INVALID)]])
    perimeter, wall_length, separation = q.room_boundary(doc, room, None)
    assert perimeter == pytest.approx(17 * 0.3048)
    assert wall_length == pytest.approx(10 * 0.3048)
    assert separation == pytest.approx(7 * 0.3048)
