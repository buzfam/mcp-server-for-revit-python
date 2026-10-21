# -*- coding: UTF-8 -*-
"""
Quantity takeoff routes (read only): room finishes and quantities by group.

Every number carries the measurement rule used (spec 015, espec. §12.3), so a
comparison with an engineering budget can be traced back to its criterion.
Revit works in feet; everything leaves here in m, m2 and m3.
"""

from pyrevit import routes, DB
import logging
import math
from .security import authorize_request
from .utils import element_id_value, normalize_string
from .exports import (csv_text, room_status, _parse_body, _safe_name, _parameter_text, _key,
                      SQFT_TO_M2, FT_TO_M, CUFT_TO_M3)

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 200
MAX_LIMIT = 1000
MAX_WALL_HEIGHT_M = 20.0
MAX_ROUNDING = 4
DEDUCT_MODES = ("over_threshold", "excess_over_threshold", "all", "none")
DEFAULT_RULES = {
    "deduct_openings": "over_threshold",
    "deduct_openings_over_m2": 2.0,
    "skirting_subtract_doors": True,
    "wall_height_m": None,
    "use_modeled_elements": True,
    "rounding": 2,
    "waste_percent": 0.0,
}
GROUPS = ("esquadrias", "loucas_metais", "alvenaria", "pisos", "forros", "rodapes", "pintura")
FORMATS = ("json", "csv")
LINE_COLUMNS = ["group", "item", "measure", "unit", "quantity", "quantity_with_waste", "rule"]
# Area tolerance: 2.0 m2 computed from feet may come out as 2.0000000001.
AREA_EPSILON = 1e-6
# A modeled floor/ceiling bigger than this spans other rooms: use the room area.
SPAN_FACTOR = 1.05
SPAN_MARGIN_M2 = 0.5
UNITS = {"length": "m", "area": "m2", "volume": "m3", "count": "un"}
# Width/height parameters, in order of preference; each tried on the instance, then on the type.
DOOR_SIZE_PARAMS = (("DOOR_WIDTH", "DOOR_HEIGHT"),
                    ("FAMILY_WIDTH_PARAM", "FAMILY_HEIGHT_PARAM"),
                    ("FAMILY_ROUGH_WIDTH_PARAM", "FAMILY_ROUGH_HEIGHT_PARAM"))
WINDOW_SIZE_PARAMS = (("WINDOW_WIDTH", "WINDOW_HEIGHT"),
                      ("FAMILY_WIDTH_PARAM", "FAMILY_HEIGHT_PARAM"),
                      ("FAMILY_ROUGH_WIDTH_PARAM", "FAMILY_ROUGH_HEIGHT_PARAM"))
KIND_LABELS = {"door": u"Porta", "window": u"Janela"}
# Phase status of an element in the measured phase that is counted (demolished,
# temporary and future elements are not part of the final building).
KEPT_PHASE_STATUSES = ("New", "Existing")
# Test point height above the room location point, in feet (internal units).
POINT_Z_OFFSET_FT = 1.0


# ---------------------------------------------------------------------------
# Pure helpers (no Revit API; checked under IronPython by scripts/ironpython_check.py)
# ---------------------------------------------------------------------------

def is_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, long, float)):
        return False
    return not (math.isnan(value) or math.isinf(value))


def parse_rules(raw):
    """Defaults merged with the request's rules. Returns (rules, error)."""
    rules = dict(DEFAULT_RULES)
    if raw is None:
        return rules, None
    if not isinstance(raw, dict):
        return None, "rules must be an object"
    unknown = sorted(key for key in raw if key not in DEFAULT_RULES)
    if unknown:
        return None, "unknown rules: {} (known: {})".format(
            ", ".join(unknown), ", ".join(sorted(DEFAULT_RULES)))
    for key in raw:
        if raw[key] is not None:
            rules[str(key)] = raw[key]
    mode = rules["deduct_openings"]
    mode = mode.lower() if isinstance(mode, basestring) else mode
    if mode not in DEDUCT_MODES:
        return None, "deduct_openings must be one of: {}".format(", ".join(DEDUCT_MODES))
    rules["deduct_openings"] = str(mode)
    threshold = rules["deduct_openings_over_m2"]
    if not is_number(threshold) or threshold < 0:
        return None, "deduct_openings_over_m2 must be a number >= 0"
    rules["deduct_openings_over_m2"] = float(threshold)
    for key in ("skirting_subtract_doors", "use_modeled_elements"):
        if not isinstance(rules[key], bool):
            return None, "{} must be true or false".format(key)
    height = rules["wall_height_m"]
    if height is not None:
        if not is_number(height) or height <= 0 or height > MAX_WALL_HEIGHT_M:
            return None, "wall_height_m must be a number between 0 and {}".format(MAX_WALL_HEIGHT_M)
        rules["wall_height_m"] = float(height)
    digits = rules["rounding"]
    if not isinstance(digits, (int, long)) or isinstance(digits, bool) \
            or digits < 0 or digits > MAX_ROUNDING:
        return None, "rounding must be an integer between 0 and {}".format(MAX_ROUNDING)
    rules["rounding"] = int(digits)
    waste = rules["waste_percent"]
    if not is_number(waste) or waste < 0 or waste > 100:
        return None, "waste_percent must be a number between 0 and 100"
    rules["waste_percent"] = float(waste)
    return rules, None


def parse_groups(raw):
    """Requested groups in canonical order. Returns (groups, error)."""
    if raw is None:
        return list(GROUPS), None
    if not isinstance(raw, (list, tuple)) or not raw:
        return None, "groups must be a non-empty list"
    wanted = set()
    for group in raw:
        key = group.strip().lower() if isinstance(group, basestring) else None
        if key not in GROUPS:
            return None, "unknown group {!r}; use: {}".format(group, ", ".join(GROUPS))
        wanted.add(key)
    return [group for group in GROUPS if group in wanted], None


def round_value(value, digits):
    """Round for output; None stays None and -0.0 becomes 0.0."""
    if value is None:
        return None
    result = round(float(value), digits)
    return 0.0 if result == 0 else result


def with_waste(value, percent):
    if value is None:
        return None
    return value * (1.0 + percent / 100.0)


def opening_deduction(area_m2, rules):
    """m2 deducted from the wall for one opening of this area.

    over_threshold: the whole opening when its area is above the threshold;
    excess_over_threshold: only the part above the threshold (SINAPI/TCPO-like);
    all: every opening; none: nothing. Openings without a size: nothing."""
    mode = rules["deduct_openings"]
    if mode == "none" or area_m2 is None:
        return 0.0
    if mode == "all":
        return area_m2
    threshold = rules["deduct_openings_over_m2"]
    if area_m2 <= threshold + AREA_EPSILON:
        return 0.0
    if mode == "excess_over_threshold":
        return area_m2 - threshold
    return area_m2


def opening_deducted(area_m2, rules):
    """True when an opening of this area is (partly) deducted from the wall area."""
    if rules["deduct_openings"] == "all":
        return True
    return opening_deduction(area_m2, rules) > 0


def net_wall_area(gross_m2, opening_areas, rules):
    """Wall area after the opening rule. Openings without a size are never deducted."""
    deducted = 0.0
    deducted_count = 0
    kept_count = 0
    for area in opening_areas:
        if opening_deducted(area, rules):
            deducted += opening_deduction(area, rules)
            deducted_count += 1
        else:
            kept_count += 1
    if gross_m2 is None:
        return {"gross": None, "net": None, "deducted": deducted,
                "deducted_count": deducted_count, "kept_count": kept_count}
    return {"gross": gross_m2, "net": max(0.0, gross_m2 - deducted), "deducted": deducted,
            "deducted_count": deducted_count, "kept_count": kept_count}


def skirting_length(wall_length_m, door_widths_m, subtract_doors):
    """(skirting length, door width subtracted)."""
    subtracted = sum(width for width in door_widths_m if width) if subtract_doors else 0.0
    return max(0.0, wall_length_m - subtracted), subtracted


def pick_positive(candidates):
    """First (value, label) whose value is a positive number, else (None, None)."""
    for label, value in candidates:
        if is_number(value) and value > 0:
            return value, label
    return None, None


def size_source(width_label, height_label):
    if width_label is None or height_label is None:
        return "missing"
    return "width={}; height={}".format(width_label, height_label)


def choose_height(rule_height_m, ceiling_height_m, room_height_m):
    """(height in m, source): rule override, then modeled ceiling, then room height."""
    if rule_height_m:
        return rule_height_m, "rule:wall_height_m"
    if ceiling_height_m and ceiling_height_m > 0:
        return ceiling_height_m, "ceiling_height"
    if room_height_m and room_height_m > 0:
        return room_height_m, "room_unbounded_height"
    return None, "unknown"


def choose_area(modeled_areas_m2, room_area_m2, use_modeled):
    """(area, source, note): modeled elements when they fit the room, else the room area."""
    if not use_modeled:
        return room_area_m2, "room_area", None
    if not modeled_areas_m2:
        return room_area_m2, "room_area", u"nenhum elemento modelado no ambiente"
    total = sum(modeled_areas_m2)
    if room_area_m2 and total > room_area_m2 * SPAN_FACTOR + SPAN_MARGIN_M2:
        return room_area_m2, "room_area", (
            u"elemento modelado maior que o ambiente ({:.2f} m²): usada a área do ambiente"
            .format(total))
    return total, "modeled_elements", None


def room_measures(raw, rules):
    """Unrounded wall, skirting and painting values of one room (raw values in m)."""
    height = raw.get("height_m")
    gross = raw["wall_length_m"] * height if height else None
    walls = net_wall_area(gross, [o["area_m2"] for o in raw["openings"]], rules)
    door_widths = [o["width_m"] for o in raw["openings"] if o["kind"] == "door"]
    skirting, subtracted = skirting_length(
        raw["wall_length_m"], door_widths, rules["skirting_subtract_doors"])
    ceiling = raw.get("ceiling_m2") or 0.0
    painting = walls["net"] + ceiling if walls["net"] is not None else None
    return {"walls": walls, "skirting": skirting, "skirting_doors": subtracted,
            "painting": painting, "painting_walls": walls["net"], "painting_ceiling": ceiling}


def rule_texts(rules, phase_name=None):
    """Measurement criteria in pt-BR, one per measured value."""
    mode = rules["deduct_openings"]
    if mode == "all":
        openings = u"todos os vãos de portas e janelas descontados"
    elif mode == "none":
        openings = u"nenhum vão descontado (área bruta)"
    elif mode == "excess_over_threshold":
        openings = (u"descontado só o que excede {:.2f} m² em cada vão (vão menor ou igual: "
                    u"nada descontado)").format(rules["deduct_openings_over_m2"])
    else:
        openings = u"descontados só os vãos com área acima de {:.2f} m²".format(
            rules["deduct_openings_over_m2"])
    if rules["wall_height_m"]:
        height = u"altura fixa de {:.2f} m (regra wall_height_m)".format(rules["wall_height_m"])
    elif rules["use_modeled_elements"]:
        height = (u"altura do forro modelado no ambiente; sem forro, altura do ambiente "
                  u"(limite superior + deslocamento)")
    else:
        height = u"altura do ambiente (limite superior + deslocamento)"
    if rules["use_modeled_elements"]:
        modeled = (u"soma dos {} modelados cujo centro está no ambiente; sem elemento modelado "
                   u"(ou maior que o ambiente), área do ambiente")
    else:
        modeled = u"área do ambiente ({} modelados ignorados)"
    digits = rules["rounding"]
    waste = rules["waste_percent"]
    texts = {
        "paredes": (u"área de parede = comprimento do contorno do ambiente na face de acabamento "
                    u"(só trechos com parede ou pilar; paredes cortina fora) × altura; altura: {}; "
                    u"vãos: {} (portas e janelas que limitam o ambiente, pela fase do ambiente)"
                    ).format(height, openings),
        "rodape": (u"rodapé = comprimento do contorno com parede (sem paredes cortina)" + (
            u" menos a largura das portas do ambiente" if rules["skirting_subtract_doors"]
            else u", sem descontar portas")),
        "piso": u"área de piso = " + modeled.format(u"pisos"),
        "forro": u"área de forro = " + modeled.format(u"forros"),
        "pintura": u"pintura = área líquida de paredes (regra acima) + área de forro",
        "alvenaria": (u"área bruta = comprimento da linha de localização × altura desconectada, "
                      u"uma face; área líquida: {}; volume calculado pelo Revit; paredes cortina "
                      u"e paredes no local excluídas; parede empilhada contada uma vez (a "
                      u"empilhada, não as subparedes)").format(openings),
        "esquadrias": (u"contagem por família, tipo e dimensões; largura × altura pelos parâmetros "
                       u"da instância ou do tipo (ver size_source); área = largura × altura × "
                       u"quantidade"),
        "loucas_metais": u"contagem de instâncias por família e tipo",
        "arredondamento": (u"{} casas decimais, só no valor final (somas com valores sem "
                           u"arredondar)").format(digits),
        "perdas": (u"sem perdas" if not waste else
                   u"perdas de {:g}% só em quantity_with_waste (não aplicadas a unidades)"
                   .format(waste)),
        "unidades": u"m, m², m³ e un (convertidos das unidades internas do Revit)",
    }
    if phase_name is not None:
        texts["fase"] = (u"ambientes da fase {0}; demais elementos novos e existentes na fase {0}; "
                         u"demolidos excluídos").format(phase_name)
    return texts


def rules_block(rules, phase_name=None):
    block = {"applied": dict(rules), "criteria": rule_texts(rules, phase_name),
             "units": dict(UNITS)}
    if phase_name is not None:
        block["phase"] = phase_name
    return block


def phase_ref_error(value):
    """Shape check of the phase input (name or id); None is the last phase."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "phase must be a phase name or id"
    if isinstance(value, (int, long)):
        return None if value > 0 else "phase must be a positive phase id"
    if isinstance(value, basestring) and value.strip():
        return None
    return "phase must be a phase name or id"


def resolve_phase(phases, value):
    """Index in phases [(id, name)] (project order) of the phase given by id or
    name; None means the last phase. Returns (index, error)."""
    if not phases:
        return None, "the project has no phases"
    if value is None:
        return len(phases) - 1, None
    wanted_id = None
    if isinstance(value, (int, long)) and not isinstance(value, bool):
        wanted_id = int(value)
    else:
        wanted = _key(value)
        for index, (_, name) in enumerate(phases):
            if _key(name) == wanted:
                return index, None
        if value.strip().isdigit():
            wanted_id = int(value.strip())
    if wanted_id is not None:
        for index, (phase_id, _) in enumerate(phases):
            if phase_id == wanted_id:
                return index, None
    return None, u"phase '{}' not found; phases: {}".format(
        normalize_string(u"{}".format(value)), u", ".join(name or u"?" for _, name in phases))


def phase_status_kept(status_name):
    """True for the ElementOnPhaseStatus names that are measured."""
    return status_name in KEPT_PHASE_STATUSES


def stacked_volume(own_volume, member_volumes):
    """Volume of a stacked wall: its own when Revit gives one, else the members' sum."""
    if own_volume:
        return own_volume
    known = [v for v in member_volumes if v]
    return sum(known) if known else None


def in_box(point, box):
    """point (x, y) inside box (min x, min y, max x, max y); no box: True."""
    if box is None:
        return True
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def room_point_z(location_z, level_elevation):
    """Z (ft) of the point tested with IsPointInRoom: room location point + 1 ft,
    else the level's ProjectElevation + 1 ft, else 1 ft."""
    if is_number(location_z):
        return location_z + POINT_Z_OFFSET_FT
    if is_number(level_elevation):
        return level_elevation + POINT_Z_OFFSET_FT
    return POINT_Z_OFFSET_FT


def make_line(group, item, measure, unit, quantity, rule, rules, extra=None):
    """One quantity line, rounded, with quantity_with_waste when waste applies."""
    digits = rules["rounding"]
    line = {"group": group, "item": item, "measure": measure, "unit": unit,
            "quantity": quantity if unit == "un" else round_value(quantity, digits),
            "rule": rule}
    if rules["waste_percent"] and unit != "un":
        line["quantity_with_waste"] = round_value(
            with_waste(quantity, rules["waste_percent"]), digits)
    if extra:
        line.update(extra)
    return line


def group_records(records, key_fields):
    """Records grouped by key_fields, as [(key tuple, [records])] sorted by key."""
    groups = {}
    for record in records:
        key = tuple(record.get(field) for field in key_fields)
        groups.setdefault(key, []).append(record)
    return sorted(groups.items(), key=lambda item: tuple(_sort_part(part) for part in item[0]))


def _sort_part(part):
    """Numbers sort numerically, text case-insensitively, None first among texts."""
    if is_number(part):
        return (0, part, u"")
    return (1, 0, _key(part))


def _type_label(family, type_name):
    if family and type_name:
        return u"{}: {}".format(family, type_name)
    return family or type_name or u"(sem tipo)"


def opening_lines(records, rules):
    """esquadrias: count per kind, family, type and size."""
    lines = []
    digits = rules["rounding"]
    rule = rule_texts(rules)["esquadrias"]
    for (kind, family, type_name, width, height), items in group_records(
            records, ("kind", "family", "type", "width_key", "height_key")):
        first = items[0]
        area = sum(item["area_m2"] for item in items if item["area_m2"])
        lines.append(make_line(
            "esquadrias", u"{} - {}".format(KIND_LABELS.get(kind, kind), _type_label(family, type_name)),
            "count", "un", len(items), rule, rules, {
                "width_m": round_value(first["width_m"], digits),
                "height_m": round_value(first["height_m"], digits),
                "area_m2": round_value(area, digits) if first["area_m2"] else None,
                "size_source": first["size_source"]}))
    return lines


def count_lines(records, group, rule, rules):
    """Instances counted per category, family and type."""
    return [make_line(group, u"{} - {}".format(category, _type_label(family, type_name))
                      if category else _type_label(family, type_name),
                      "count", "un", len(items), rule, rules)
            for (category, family, type_name), items in group_records(
                records, ("category", "family", "type"))]


def host_lines(records, group, rule, rules):
    """Floors or ceilings: area per type."""
    return [make_line(group, _type_label(None, type_name), "area", "m2",
                      sum(item["area_m2"] for item in items), rule, rules, {"count": len(items)})
            for (type_name,), items in group_records(records, ("type",))]


def wall_lines(records, rules):
    """alvenaria: length, gross area, net area and volume per wall type."""
    rule = rule_texts(rules)["alvenaria"]
    lines = []
    for (type_name,), items in group_records(records, ("type",)):
        length = gross = net = volume = 0.0
        for item in items:
            wall_gross = item["length_m"] * item["height_m"]
            walls = net_wall_area(wall_gross, item["opening_areas"], rules)
            length += item["length_m"]
            gross += wall_gross
            net += walls["net"]
            volume += item["volume_m3"] or 0.0
        extra = {"count": len(items)}
        label = _type_label(None, type_name)
        lines.append(make_line("alvenaria", label, "length", "m", length, rule, rules, extra))
        lines.append(make_line("alvenaria", label, "gross_area", "m2", gross, rule, rules, extra))
        lines.append(make_line("alvenaria", label, "net_area", "m2", net, rule, rules, extra))
        lines.append(make_line("alvenaria", label, "volume", "m3", volume, rule, rules, extra))
    return lines


def room_total_lines(raws, rules, groups):
    """rodapes and pintura totals from the room finishes."""
    texts = rule_texts(rules)
    skirting = walls = ceiling = 0.0
    missing_height = 0
    for raw in raws:
        measures = room_measures(raw, rules)
        skirting += measures["skirting"]
        ceiling += measures["painting_ceiling"]
        if measures["painting_walls"] is None:
            missing_height += 1
        else:
            walls += measures["painting_walls"]
    extra = {"count": len(raws)}
    if missing_height:
        extra["rooms_without_height"] = missing_height
    lines = []
    if "rodapes" in groups:
        lines.append(make_line("rodapes", u"Rodapé (todos os ambientes)", "length", "m",
                               skirting, texts["rodape"], rules, extra))
    if "pintura" in groups:
        lines.append(make_line("pintura", u"Pintura de paredes", "area", "m2", walls,
                               texts["paredes"], rules, extra))
        lines.append(make_line("pintura", u"Pintura de forro/teto", "area", "m2", ceiling,
                               texts["forro"], rules, extra))
        lines.append(make_line("pintura", u"Pintura total", "area", "m2", walls + ceiling,
                               texts["pintura"], rules, extra))
    return lines


def room_output(raw, rules):
    """One room as returned by room_finishes, every value with its source."""
    digits = rules["rounding"]
    r = lambda value: round_value(value, digits)
    measures = room_measures(raw, rules)
    walls = measures["walls"]
    notes = list(raw.get("notes") or [])
    if raw["separation_length_m"] > 0:
        notes.append(u"{:.2f} m do contorno são linhas separadoras (sem parede): fora de "
                     u"paredes, pintura e rodapé".format(raw["separation_length_m"]))
    if raw.get("curtain_length_m"):
        notes.append(u"{:.2f} m do contorno são paredes cortina: fora de paredes, pintura e "
                     u"rodapé".format(raw["curtain_length_m"]))
    if walls["gross"] is None:
        notes.append(u"altura desconhecida: informe rules.wall_height_m")
    openings = []
    for opening in raw["openings"]:
        openings.append({
            "element_id": opening["element_id"], "kind": opening["kind"],
            "family": opening["family"], "type": opening["type"],
            "width_m": r(opening["width_m"]), "height_m": r(opening["height_m"]),
            "area_m2": r(opening["area_m2"]), "size_source": opening["size_source"],
            "deducted": opening_deducted(opening["area_m2"], rules),
            "deducted_m2": r(opening_deduction(opening["area_m2"], rules))})
    return {
        "element_id": raw["element_id"], "number": raw["number"], "name": raw["name"],
        "level": raw["level"], "phase": raw["phase"],
        "area_m2": r(raw["room_area_m2"]),
        "perimeter_m": r(raw["perimeter_m"]),
        "height": {"value_m": r(raw["height_m"]), "source": raw["height_source"]},
        "floor": {"area_m2": r(raw["floor_m2"]), "source": raw["floor_source"],
                  "element_ids": raw["floor_ids"], "note": raw["floor_note"]},
        "ceiling": {"area_m2": r(raw["ceiling_m2"]), "source": raw["ceiling_source"],
                    "element_ids": raw["ceiling_ids"], "note": raw["ceiling_note"]},
        "walls": {"boundary_length_m": r(raw["wall_length_m"]),
                  "curtain_length_m": r(raw.get("curtain_length_m") or 0.0),
                  "height_m": r(raw["height_m"]),
                  "gross_m2": r(walls["gross"]), "net_m2": r(walls["net"]),
                  "deducted_m2": r(walls["deducted"]),
                  "openings_deducted": walls["deducted_count"],
                  "openings_not_deducted": walls["kept_count"],
                  "source": "boundary_length x height - openings by rule"},
        "skirting": {"length_m": r(measures["skirting"]),
                     "doors_subtracted_m": r(measures["skirting_doors"]),
                     "source": "wall boundary length - door widths"
                     if rules["skirting_subtract_doors"] else "wall boundary length"},
        "painting": {"area_m2": r(measures["painting"]),
                     "source": "walls.net_m2 + ceiling.area_m2"},
        "openings": openings,
        "notes": notes,
    }


def room_totals(raws, rules):
    digits = rules["rounding"]
    totals = {"floor_m2": 0.0, "ceiling_m2": 0.0, "walls_gross_m2": 0.0, "walls_net_m2": 0.0,
              "skirting_m": 0.0, "painting_m2": 0.0, "perimeter_m": 0.0}
    for raw in raws:
        measures = room_measures(raw, rules)
        totals["floor_m2"] += raw["floor_m2"] or 0.0
        totals["ceiling_m2"] += raw["ceiling_m2"] or 0.0
        totals["walls_gross_m2"] += measures["walls"]["gross"] or 0.0
        totals["walls_net_m2"] += measures["walls"]["net"] or 0.0
        totals["skirting_m"] += measures["skirting"]
        totals["painting_m2"] += measures["painting"] or 0.0
        totals["perimeter_m"] += raw["perimeter_m"]
    return dict((key, round_value(value, digits)) for key, value in totals.items())


def parse_limit(data):
    try:
        limit = int(data.get("limit", DEFAULT_LIMIT))
    except (TypeError, ValueError):
        limit = 0
    if limit < 1 or limit > MAX_LIMIT:
        return None, "limit must be between 1 and {}".format(MAX_LIMIT)
    return limit, None


# ---------------------------------------------------------------------------
# Revit API readers
# ---------------------------------------------------------------------------

def _invalid(element_id):
    return element_id is None or element_id == DB.ElementId.InvalidElementId


def _level_name(doc, level_id, cache):
    if _invalid(level_id):
        return None
    key = element_id_value(level_id)
    if key not in cache:
        cache[key] = _safe_name(doc.GetElement(level_id))
    return cache[key]


def _double(element, builtin_name):
    """A Double parameter in internal units, or None."""
    builtin = getattr(DB.BuiltInParameter, builtin_name, None)
    if element is None or builtin is None:
        return None
    try:
        parameter = element.get_Parameter(builtin)
        if parameter is None or not parameter.HasValue:
            return None
        if parameter.StorageType != DB.StorageType.Double:
            return None
        return parameter.AsDouble()
    except Exception:
        return None


def _integer_param(element, builtin_name):
    """An Integer parameter value, or None."""
    builtin = getattr(DB.BuiltInParameter, builtin_name, None)
    if element is None or builtin is None:
        return None
    try:
        parameter = element.get_Parameter(builtin)
        if parameter is None or not parameter.HasValue:
            return None
        return parameter.AsInteger()
    except Exception:
        return None


def on_phase(element, phase):
    """True when the element is New or Existing in the phase (no phase: True).
    Elements whose status cannot be read are kept."""
    if phase is None:
        return True
    try:
        return phase_status_kept(str(element.GetPhaseStatus(phase.Id)))
    except Exception:
        return True


def _family_and_type(doc, element):
    symbol = doc.GetElement(element.GetTypeId())
    family = None
    if symbol is not None:
        family = _parameter_text(symbol, DB.BuiltInParameter.SYMBOL_FAMILY_NAME_PARAM)
    if not family:
        family = _parameter_text(element, DB.BuiltInParameter.ELEM_FAMILY_PARAM)
    return symbol, family, _safe_name(symbol)


def _elements(doc, category_name):
    category = getattr(DB.BuiltInCategory, category_name, None)
    if category is None:
        return None
    return DB.FilteredElementCollector(doc).OfCategory(category).WhereElementIsNotElementType()


def _opening_record(doc, instance, kind, level_cache):
    symbol, family, type_name = _family_and_type(doc, instance)
    widths, heights = [], []
    for width_name, height_name in (DOOR_SIZE_PARAMS if kind == "door" else WINDOW_SIZE_PARAMS):
        for where, element in (("instance", instance), ("type", symbol)):
            widths.append(("{}:{}".format(where, width_name), _double(element, width_name)))
            heights.append(("{}:{}".format(where, height_name), _double(element, height_name)))
    width, width_label = pick_positive(widths)
    height, height_label = pick_positive(heights)
    width_m = width * FT_TO_M if width else None
    height_m = height * FT_TO_M if height else None
    host = None
    try:
        host = instance.Host
    except Exception:
        pass
    return {
        "element_id": element_id_value(instance.Id), "kind": kind,
        "family": family, "type": type_name,
        "width_m": width_m, "height_m": height_m,
        "area_m2": width_m * height_m if width_m and height_m else None,
        "width_key": round(width_m, 3) if width_m else None,
        "height_key": round(height_m, 3) if height_m else None,
        "size_source": size_source(width_label, height_label),
        "host_id": element_id_value(host.Id) if host is not None else None,
        "level": _level_name(doc, instance.LevelId, level_cache),
    }


def collect_openings(doc, level_cache, phase=None):
    """[(record, FamilyInstance)] for every door and window instance
    (only New/Existing in the phase when one is given)."""
    result = []
    for kind, category in (("door", "OST_Doors"), ("window", "OST_Windows")):
        for instance in _elements(doc, category):
            if not isinstance(instance, DB.FamilyInstance):
                continue
            if not on_phase(instance, phase):
                continue
            try:
                result.append((_opening_record(doc, instance, kind, level_cache), instance))
            except Exception as e:
                logger.warning("Could not read opening: %s", e)
    return result


def openings_by_room(openings, phase, stats=None):
    """{room id: [opening records]} from FromRoom/ToRoom in the given phase.
    stats["unreadable"] counts openings whose rooms could not be read."""
    mapping = {}
    for record, instance in openings:
        room_ids = set()
        failed = False
        for getter in ("get_FromRoom", "get_ToRoom"):
            try:
                room = getattr(instance, getter)(phase) if phase is not None \
                    else getattr(instance, getter[4:])
            except Exception:
                room = None
                failed = True
            if room is not None:
                room_ids.add(element_id_value(room.Id))
        if failed and not room_ids and stats is not None:
            stats["unreadable"] = stats.get("unreadable", 0) + 1
        for room_id in room_ids:
            mapping.setdefault(room_id, []).append(record)
    return mapping


def collect_hosts(doc, category_name, level_cache, phase=None):
    """Floors or ceilings with type, level, area, plan centre and structural flag
    (only New/Existing in the phase when one is given)."""
    result = []
    for element in _elements(doc, category_name) or []:
        try:
            if not on_phase(element, phase):
                continue
            area = _double(element, "HOST_AREA_COMPUTED")
            box = element.get_BoundingBox(None)
            if not area or box is None:
                continue
            result.append({
                "element_id": element_id_value(element.Id),
                "type": _safe_name(doc.GetElement(element.GetTypeId())),
                "level_key": None if _invalid(element.LevelId) else element_id_value(element.LevelId),
                "level": _level_name(doc, element.LevelId, level_cache),
                "area_m2": area * SQFT_TO_M2,
                "centre": ((box.Min.X + box.Max.X) / 2.0, (box.Min.Y + box.Max.Y) / 2.0),
                "offset_m": (_double(element, "CEILING_HEIGHTABOVELEVEL_PARAM") or 0.0) * FT_TO_M,
                "structural": _integer_param(element, "FLOOR_PARAM_IS_STRUCTURAL") == 1,
            })
        except Exception as e:
            logger.warning("Could not read %s element: %s", category_name, e)
    return result


class TakeoffContext(object):
    """Model data read once per request and shared by every room."""

    def __init__(self, doc, rules, phase=None, need_floors=True, need_ceilings=True,
                 need_openings=True):
        self.doc = doc
        self.rules = rules
        self.phase = phase
        self.level_cache = {}
        self.options = DB.SpatialElementBoundaryOptions()
        self.options.SpatialElementBoundaryLocation = DB.SpatialElementBoundaryLocation.Finish
        self.openings = collect_openings(doc, self.level_cache, phase) if need_openings else []
        self.by_phase = {}
        self.opening_stats = {}
        self.structural_floors = []
        self.floors, self.ceilings = [], []
        if rules["use_modeled_elements"] and need_floors:
            floors = collect_hosts(doc, "OST_Floors", self.level_cache, phase)
            self.floors = [f for f in floors if not f["structural"]]
            self.structural_floors = [f for f in floors if f["structural"]]
        if rules["use_modeled_elements"] and need_ceilings:
            self.ceilings = collect_hosts(doc, "OST_Ceilings", self.level_cache, phase)
        self._by_level = {}
        self._last_phase = None
        try:
            phases = list(doc.Phases)
            self._last_phase = phases[-1] if phases else None
        except Exception:
            pass

    def hosts_on_level(self, kind, level_key):
        """Floors or ceilings of one level (index built once per kind)."""
        if kind not in self._by_level:
            index = {}
            for host in getattr(self, kind):
                index.setdefault(host["level_key"], []).append(host)
            self._by_level[kind] = index
        return self._by_level[kind].get(level_key, [])

    def room_phase(self, room):
        phase = None
        try:
            parameter = room.get_Parameter(DB.BuiltInParameter.ROOM_PHASE)
            if parameter is not None:
                phase = self.doc.GetElement(parameter.AsElementId())
        except Exception:
            phase = None
        return phase or self.phase or self._last_phase

    def room_openings(self, room, phase):
        key = element_id_value(phase.Id) if phase is not None else None
        if key not in self.by_phase:
            stats = {}
            self.by_phase[key] = openings_by_room(self.openings, phase, stats)
            self.opening_stats[key] = stats
        return self.by_phase[key].get(element_id_value(room.Id), [])

    def unreadable_openings(self):
        return max([stats.get("unreadable", 0) for stats in self.opening_stats.values()] or [0])


def _is_curtain_wall(doc, element):
    wall_class = getattr(DB, "Wall", None)
    curtain = getattr(getattr(DB, "WallKind", None), "Curtain", None)
    if wall_class is None or curtain is None or not isinstance(element, wall_class):
        return False
    try:
        wall_type = doc.GetElement(element.GetTypeId())
        return wall_type is not None and wall_type.Kind == curtain
    except Exception:
        return False


def room_boundary(doc, room, options):
    """(perimeter, wall length, separation length, curtain wall length) in m on
    the finish faces. Curtain walls are not walls to paint or skirt."""
    perimeter = wall = separation = curtain = 0.0
    loops = room.GetBoundarySegments(options)
    for loop in loops or []:
        for segment in loop:
            length = segment.GetCurve().Length * FT_TO_M
            perimeter += length
            element = None if _invalid(segment.ElementId) else doc.GetElement(segment.ElementId)
            if element is None or isinstance(element, DB.CurveElement):
                separation += length
            elif _is_curtain_wall(doc, element):
                curtain += length
            else:
                wall += length
    return perimeter, wall, separation, curtain


def _room_box(room):
    """(min x, min y, max x, max y) of the room in feet, or None."""
    try:
        box = room.get_BoundingBox(None)
    except Exception:
        return None
    if box is None:
        return None
    return (box.Min.X, box.Min.Y, box.Max.X, box.Max.Y)


def _hosts_in_room(room, hosts, level_key, z, room_box=None):
    """Hosts on the level whose plan centre is in the room. room_box (optional)
    skips IsPointInRoom for centres outside the room's bounding box."""
    found = []
    for host in hosts:
        if host["level_key"] != level_key:
            continue
        if not in_box(host["centre"], room_box):
            continue
        try:
            if room.IsPointInRoom(DB.XYZ(host["centre"][0], host["centre"][1], z)):
                found.append(host)
        except Exception:
            pass
    return found


def room_raw(context, room, level, number):
    """Unrounded measures of one room (m, m2), see room_output."""
    doc = context.doc
    rules = context.rules
    perimeter, wall_length, separation, curtain = room_boundary(doc, room, context.options)
    level_key = None if _invalid(room.LevelId) else element_id_value(room.LevelId)
    level_element = doc.GetElement(room.LevelId) if level_key is not None else None
    # Internal coordinates, like the bounding boxes: room location point + 1 ft
    # (fallback: the level's ProjectElevation, never Level.Elevation).
    location_z = None
    try:
        location_z = room.Location.Point.Z
    except Exception:
        pass
    level_z = None
    try:
        level_z = level_element.ProjectElevation if level_element is not None else None
    except Exception:
        pass
    z = room_point_z(location_z, level_z)
    room_area = room.Area * SQFT_TO_M2
    notes = []

    box = _room_box(room)
    floors = _hosts_in_room(room, context.hosts_on_level("floors", level_key), level_key, z, box)
    ceilings = _hosts_in_room(room, context.hosts_on_level("ceilings", level_key), level_key, z,
                              box)
    floor_m2, floor_source, floor_note = choose_area(
        [f["area_m2"] for f in floors], room_area, rules["use_modeled_elements"])
    ceiling_m2, ceiling_source, ceiling_note = choose_area(
        [c["area_m2"] for c in ceilings], room_area, rules["use_modeled_elements"])

    ceiling_height = None
    if ceilings:
        main = max(ceilings, key=lambda c: c["area_m2"])
        ceiling_height = main["offset_m"] or None
        if len(set(round(c["offset_m"], 2) for c in ceilings)) > 1:
            notes.append(u"forros com alturas diferentes: usada a do maior")
    room_height = None
    try:
        room_height = room.UnboundedHeight * FT_TO_M
    except Exception:
        pass
    height, height_source = choose_height(rules["wall_height_m"], ceiling_height, room_height)

    phase = context.room_phase(room)
    return {
        "element_id": element_id_value(room.Id), "number": number,
        "name": _parameter_text(room, DB.BuiltInParameter.ROOM_NAME),
        "level": level, "phase": _safe_name(phase),
        "room_area_m2": room_area, "perimeter_m": perimeter,
        "wall_length_m": wall_length, "separation_length_m": separation,
        "curtain_length_m": curtain,
        "height_m": height, "height_source": height_source,
        "floor_m2": floor_m2, "floor_source": floor_source, "floor_note": floor_note,
        "floor_ids": [f["element_id"] for f in floors],
        "ceiling_m2": ceiling_m2, "ceiling_source": ceiling_source, "ceiling_note": ceiling_note,
        "ceiling_ids": [c["element_id"] for c in ceilings],
        "openings": context.room_openings(room, phase),
        "notes": notes,
    }


def _room_phase_id(room):
    try:
        parameter = room.get_Parameter(DB.BuiltInParameter.ROOM_PHASE)
        if parameter is None:
            return None
        element_id = parameter.AsElementId()
        return None if _invalid(element_id) else element_id_value(element_id)
    except Exception:
        return None


def select_rooms(doc, level_filter, numbers, level_cache, phase_id=None):
    """Placed rooms matching the filters (and of the phase, when phase_id is
    given), sorted by level and number.
    Returns (rooms [(level, number, room)], skipped counts, numbers found)."""
    wanted_level = _key(level_filter) if level_filter else None
    wanted_numbers = set(_key(n) for n in numbers) if numbers else None
    rooms = []
    skipped = {"unplaced": 0, "not_enclosed": 0}
    if phase_id is not None:
        skipped["other_phase"] = 0
    found = set()
    for room in _elements(doc, "OST_Rooms"):
        try:
            level = _level_name(doc, room.LevelId, level_cache)
            if wanted_level and _key(level) != wanted_level:
                continue
            number = _parameter_text(room, DB.BuiltInParameter.ROOM_NUMBER)
            if wanted_numbers is not None and _key(number) not in wanted_numbers:
                continue
            if phase_id is not None:
                room_phase = _room_phase_id(room)
                if room_phase is not None and room_phase != phase_id:
                    skipped["other_phase"] += 1
                    continue
            status = room_status(room.Location is not None, room.Area)
            if status != "placed":
                skipped[status] += 1
                continue
            found.add(_key(number))
            rooms.append((level, number, room))
        except Exception as e:
            logger.warning("Could not read room: %s", e)
    rooms.sort(key=lambda item: (_key(item[0]), _key(item[1])))
    return rooms, skipped, found


def _plumbing_records(doc, category_name, label, level_filter, level_cache, phase=None):
    records = []
    for instance in _elements(doc, category_name) or []:
        if not on_phase(instance, phase):
            continue
        level = _level_name(doc, instance.LevelId, level_cache)
        if level_filter and _key(level) != _key(level_filter):
            continue
        _, family, type_name = _family_and_type(doc, instance)
        records.append({"category": label, "family": family, "type": type_name})
    return records


def _stacked_members(wall):
    try:
        return list(wall.GetStackedWallMemberIds())
    except Exception:
        return []


def _wall_records(doc, openings, level_filter, level_cache, phase=None):
    """(records, skipped counts) of basic and stacked walls. A stacked wall is
    measured once: its members are skipped and their openings and volume go to it."""
    by_host = {}
    for record, _ in openings:
        if record["host_id"] is not None:
            by_host.setdefault(record["host_id"], []).append(record["area_m2"])
    records = []
    skipped = {"curtain": 0, "no_length_or_height": 0, "stacked_member": 0, "not_in_phase": 0}
    for wall in DB.FilteredElementCollector(doc).OfClass(DB.Wall):
        try:
            level = _level_name(doc, wall.LevelId, level_cache)
            if level_filter and _key(level) != _key(level_filter):
                continue
            if getattr(wall, "IsStackedWallMember", False):
                skipped["stacked_member"] += 1
                continue
            if not on_phase(wall, phase):
                skipped["not_in_phase"] += 1
                continue
            wall_type = doc.GetElement(wall.GetTypeId())
            if wall_type is not None and wall_type.Kind == DB.WallKind.Curtain:
                skipped["curtain"] += 1
                continue
            length = _double(wall, "CURVE_ELEM_LENGTH")
            height = _double(wall, "WALL_USER_HEIGHT_PARAM")
            if not length or not height:
                skipped["no_length_or_height"] += 1
                continue
            members = _stacked_members(wall)
            volume = stacked_volume(
                _double(wall, "HOST_VOLUME_COMPUTED"),
                [_double(doc.GetElement(member), "HOST_VOLUME_COMPUTED") for member in members])
            opening_areas = list(by_host.get(element_id_value(wall.Id), []))
            for member in members:
                opening_areas.extend(by_host.get(element_id_value(member), []))
            records.append({
                "type": _safe_name(wall_type), "length_m": length * FT_TO_M,
                "height_m": height * FT_TO_M,
                "volume_m3": volume * CUFT_TO_M3 if volume else None,
                "opening_areas": opening_areas,
            })
        except Exception as e:
            logger.warning("Could not read wall: %s", e)
    return records, skipped


def _level_filtered(records, level_filter):
    if not level_filter:
        return records
    return [r for r in records if _key(r.get("level")) == _key(level_filter)]


def find_phase(doc, value):
    """(phase element, error) for the phase input (name or id; None = last phase)."""
    error = phase_ref_error(value)
    if error:
        return None, error
    phases = list(doc.Phases)
    index, error = resolve_phase(
        [(element_id_value(phase.Id), _safe_name(phase)) for phase in phases], value)
    if error:
        return None, error
    return phases[index], None


def phase_notes(context):
    notes = []
    unreadable = context.unreadable_openings()
    if unreadable:
        notes.append(u"{} vãos sem ambiente legível (FromRoom/ToRoom falhou): fora das paredes "
                     u"dos ambientes".format(unreadable))
    return notes


def register_quantity_routes(api):
    """Register quantity takeoff routes with the API"""

    @api.route("/room_finishes/", methods=["POST"])
    def room_finishes(doc, request):
        """
        Floor, ceiling, wall (gross/net), skirting and painting per room.

        Expected JSON payload (all optional):
        {
            "level": "Térreo",
            "room_numbers": ["101", "102"],
            "phase": "Construção nova",      // name or id; default: last phase
            "rules": {"deduct_openings": "over_threshold", "deduct_openings_over_m2": 2.0, ...},
            "limit": 200
        }
        """
        denial = authorize_request(request, action=None)
        if denial is not None:
            return denial
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503)
            data = _parse_body(request) or {}
            limit, error = parse_limit(data)
            rules, rules_error = parse_rules(data.get("rules"))
            error = error or rules_error
            numbers = data.get("room_numbers")
            if not error and numbers is not None and (
                    not isinstance(numbers, list)
                    or not all(isinstance(n, basestring) and n.strip() for n in numbers)):
                error = "room_numbers must be a list of room numbers (text)"
            error = error or phase_ref_error(data.get("phase"))
            if error:
                return routes.make_response(data={"error": error}, status=400)
            phase, error = find_phase(doc, data.get("phase"))
            if error:
                return routes.make_response(data={"error": error}, status=404)
            phase_name = _safe_name(phase)

            context = TakeoffContext(doc, rules, phase)
            rooms, skipped, found = select_rooms(
                doc, data.get("level"), numbers, context.level_cache,
                element_id_value(phase.Id))
            raws = []
            failed = []
            for level, number, room in rooms[:limit]:
                try:
                    raws.append(room_raw(context, room, level, number))
                except Exception as e:
                    logger.warning("Could not measure room: %s", e)
                    failed.append({"element_id": element_id_value(room.Id), "number": number,
                                   "error": str(e)})
            result = {
                "status": "success",
                "rules": rules_block(rules, phase_name),
                "phase": phase_name,
                "notes": phase_notes(context),
                "total": len(rooms),
                "returned": len(raws),
                "truncated": len(rooms) > limit,
                "skipped": skipped,
                "totals_of_returned": room_totals(raws, rules),
                "rooms": [room_output(raw, rules) for raw in raws],
            }
            if failed:
                result["failed"] = failed
            if numbers:
                missing = [n for n in numbers if _key(n) not in found]
                if missing:
                    result["numbers_not_found"] = missing
            return routes.make_response(data=result)

        except Exception as e:
            logger.error("Error in room_finishes route: %s", e)
            return routes.make_response(data={"error": str(e)}, status=500)

    @api.route("/quantities_by_rule/", methods=["POST"])
    def quantities_by_rule(doc, request):
        """
        Quantity lines per group (espec. §12.2), each with its measurement rule.

        Expected JSON payload (all optional):
        {
            "groups": ["esquadrias", "pintura"],
            "level": "Térreo",
            "include_plumbing_equipment": false,
            "phase": "Construção nova",      // name or id; default: last phase
            "rules": {...},
            "limit": 200,
            "format": "json"
        }
        """
        denial = authorize_request(request, action=None)
        if denial is not None:
            return denial
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503)
            data = _parse_body(request) or {}
            limit, error = parse_limit(data)
            rules, rules_error = parse_rules(data.get("rules"))
            groups, groups_error = parse_groups(data.get("groups"))
            output = data.get("format", "json")
            output = output.lower() if isinstance(output, basestring) else output
            error = error or rules_error or groups_error or (
                None if output in FORMATS else "format must be 'json' or 'csv'") \
                or phase_ref_error(data.get("phase"))
            if error:
                return routes.make_response(data={"error": error}, status=400)
            phase, error = find_phase(doc, data.get("phase"))
            if error:
                return routes.make_response(data={"error": error}, status=404)
            phase_name = _safe_name(phase)

            level_filter = data.get("level")
            by_room = "rodapes" in groups or "pintura" in groups
            context = TakeoffContext(
                doc, rules, phase, need_floors="pisos" in groups,
                need_ceilings="forros" in groups or by_room,
                need_openings="esquadrias" in groups or "alvenaria" in groups or by_room)
            texts = rule_texts(rules)
            lines = []
            notes = []
            if "esquadrias" in groups:
                records = _level_filtered([r for r, _ in context.openings], level_filter)
                lines.extend(opening_lines(records, rules))
                missing = len([r for r in records if r["area_m2"] is None])
                if missing:
                    notes.append(u"{} esquadrias sem largura/altura legíveis".format(missing))
            if "loucas_metais" in groups:
                records = _plumbing_records(doc, "OST_PlumbingFixtures", u"Peças sanitárias",
                                            level_filter, context.level_cache, phase)
                if data.get("include_plumbing_equipment"):
                    if getattr(DB.BuiltInCategory, "OST_PlumbingEquipment", None) is None:
                        notes.append(u"categoria Equipamentos hidráulicos indisponível "
                                     u"nesta versão do Revit")
                    else:
                        records.extend(_plumbing_records(
                            doc, "OST_PlumbingEquipment", u"Equipamentos hidráulicos",
                            level_filter, context.level_cache, phase))
                lines.extend(count_lines(records, "loucas_metais", texts["loucas_metais"], rules))
            if "alvenaria" in groups:
                records, skipped = _wall_records(doc, context.openings, level_filter,
                                                 context.level_cache, phase)
                lines.extend(wall_lines(records, rules))
                if any(skipped.values()):
                    notes.append(u"paredes ignoradas: {} cortina, {} sem comprimento/altura, "
                                 u"{} subparedes de paredes empilhadas (contadas na empilhada), "
                                 u"{} demolidas ou fora da fase"
                                 .format(skipped["curtain"], skipped["no_length_or_height"],
                                         skipped["stacked_member"], skipped["not_in_phase"]))
            if "pisos" in groups:
                if rules["use_modeled_elements"]:
                    hosts, structural = context.floors, context.structural_floors
                else:
                    every = collect_hosts(doc, "OST_Floors", context.level_cache, phase)
                    hosts = [h for h in every if not h["structural"]]
                    structural = [h for h in every if h["structural"]]
                lines.extend(host_lines(_level_filtered(hosts, level_filter), "pisos",
                                        u"área dos pisos modelados (HOST_AREA_COMPUTED) por tipo; "
                                        u"pisos estruturais (lajes) excluídos", rules))
                structural = _level_filtered(structural, level_filter)
                if structural:
                    notes.append(u"{} pisos estruturais (lajes) fora de pisos: {:.2f} m²".format(
                        len(structural), sum(h["area_m2"] for h in structural)))
            if "forros" in groups:
                hosts = context.ceilings if rules["use_modeled_elements"] else \
                    collect_hosts(doc, "OST_Ceilings", context.level_cache, phase)
                lines.extend(host_lines(_level_filtered(hosts, level_filter), "forros",
                                        u"área dos forros modelados (HOST_AREA_COMPUTED) por tipo",
                                        rules))
            if by_room:
                rooms, room_skipped, _ = select_rooms(doc, level_filter, None, context.level_cache,
                                                      element_id_value(phase.Id))
                if room_skipped.get("other_phase"):
                    notes.append(u"{} ambientes de outras fases ignorados".format(
                        room_skipped["other_phase"]))
                raws = []
                for level, number, room in rooms:
                    try:
                        raws.append(room_raw(context, room, level, number))
                    except Exception as e:
                        logger.warning("Could not measure room: %s", e)
                        notes.append(u"ambiente {} não medido: {}".format(number, e))
                lines.extend(room_total_lines(raws, rules, groups))
                notes.extend(phase_notes(context))

            result = {
                "status": "success",
                "rules": rules_block(rules, phase_name),
                "phase": phase_name,
                "groups": groups,
                "format": output,
                "total": len(lines),
                "returned": min(len(lines), limit),
                "truncated": len(lines) > limit,
                "notes": notes,
            }
            lines = lines[:limit]
            if output == "csv":
                result["csv"] = csv_text(
                    LINE_COLUMNS, [[line.get(column) for column in LINE_COLUMNS] for line in lines])
            else:
                result["lines"] = lines
            return routes.make_response(data=result)

        except Exception as e:
            logger.error("Error in quantities_by_rule route: %s", e)
            return routes.make_response(data={"error": str(e)}, status=500)
