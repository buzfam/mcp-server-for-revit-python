# -*- coding: UTF-8 -*-
"""
Renumbering (spec 018)
Renumbers doors, windows, rooms, sheets or any category's Mark from a pattern
such as "P-{room_number}-{seq:02}", in reading order on plan or by the current
value, with the sequence restarting per room or per level.

All or nothing: every element is resolved and checked for clashes before the
transaction. Unique numbers (sheet and room numbers) are first set to temporary
values and then to the final ones inside the same transaction, so swapping
A-101 and A-102 works. dry_run (the default) runs the same transaction inside a
TransactionGroup and rolls it back.
"""

from pyrevit import routes, DB
import json
import logging
import re
from .security import authorize_request
from .utils import normalize_string, element_id_from_int, element_id_value, find_category
from .parameters import _builtin_parameter
from .transactions import Batch
# Shared helpers still live in modeling; one import line so they can move later.
from .modeling import element_name, error_text, same_name

logger = logging.getLogger(__name__)

MAX_ITEMS = 200
MAX_ERRORS = 100
MAX_WARNINGS = 50
FT_TO_M = 0.3048
ROW_TOLERANCE_M = 0.5
DEFAULT_PATTERN = u"{prefix}{seq}"
ORDERS = ("location", "current", "id")
GROUPS = ("room", "level")
ROOM_TOKENS = ("room_number", "room_name")
TOKENS = ("seq", "prefix", "level") + ROOM_TOKENS
TEMP_PREFIX = u"MCP-TMP-"
# View types that show model elements (the view filter rejects the others).
GRAPHICAL_VIEW_TYPES = ("FloorPlan", "CeilingPlan", "EngineeringPlan", "AreaPlan", "Section",
                        "Elevation", "Detail", "ThreeD")
# Accepted spellings of the special targets (English, Portuguese, API name).
TARGET_ALIASES = {
    "doors": "doors", "door": "doors", "portas": "doors", "porta": "doors",
    "ost_doors": "doors",
    "windows": "windows", "window": "windows", "janelas": "windows", "janela": "windows",
    "ost_windows": "windows",
    "rooms": "rooms", "room": "rooms", "ambientes": "rooms", "ambiente": "rooms",
    "salas": "rooms", "ost_rooms": "rooms",
    "sheets": "sheets", "sheet": "sheets", "folhas": "sheets", "folha": "sheets",
    "pranchas": "sheets", "prancha": "sheets", "ost_sheets": "sheets",
}
DEFAULT_PARAMETERS = {"rooms": "ROOM_NUMBER", "sheets": "SHEET_NUMBER"}
DEFAULT_ELEMENT_PARAMETER = "ALL_MODEL_MARK"
UNIQUE_PARAMETERS = ("SHEET_NUMBER", "ROOM_NUMBER")
TOKEN_RE = re.compile(r"\{([^{}]*)\}")
SEQ_WIDTH_RE = re.compile(r"^seq:0([1-9])$")


# ---------------------------------------------------------------------------
# Pure helpers (no Revit API; checked under IronPython by scripts/ironpython_check.py)
# ---------------------------------------------------------------------------

def natural_key(text):
    """Sort key where digit runs compare as numbers: "A-2" < "A-10".

    Each chunk is a (kind, number, text) triple so the key compares the same
    way in Python 2 and 3. Empty values sort last.
    """
    if text is None:
        return ((2, 0, u""),)
    value = normalize_string(text).lower()
    if not value:
        return ((2, 0, u""),)
    key = []
    # [0-9], not \d or isdigit(): a superscript two passes isdigit() but not int().
    for chunk in re.findall(u"[0-9]+|[^0-9]+", value):
        if chunk[0] in u"0123456789":
            key.append((0, int(chunk), chunk))
        else:
            key.append((1, 0, chunk))
    return tuple(key)


def target_kind(target):
    """"doors", "windows", "rooms", "sheets" or "category" (any other name)."""
    key = u"".join(normalize_string(target).lower().split())
    return TARGET_ALIASES.get(key, "category")


def parse_pattern(pattern):
    """Split a pattern into parts. Returns (parts, error).

    parts: ("text", u"P-") or ("token", name, width); width is the zero padding
    of {seq:0N} and 0 otherwise.
    """
    if not isinstance(pattern, basestring) or not pattern.strip():
        return None, "pattern must be a non-empty text"
    parts = []
    position = 0
    for match in TOKEN_RE.finditer(pattern):
        text = pattern[position:match.start()]
        if u"{" in text or u"}" in text:
            return None, "pattern has an unmatched brace"
        if text:
            parts.append(("text", text))
        name = match.group(1).strip()
        width = 0
        padded = SEQ_WIDTH_RE.match(name)
        if padded:
            name, width = "seq", int(padded.group(1))
        if name not in TOKENS:
            return None, u"Unknown token {{{}}} in pattern; use {}".format(
                match.group(1), ", ".join("{" + t + "}" for t in TOKENS + ("seq:02",)))
        parts.append(("token", name, width))
        position = match.end()
    rest = pattern[position:]
    if u"{" in rest or u"}" in rest:
        return None, "pattern has an unmatched brace"
    if rest:
        parts.append(("text", rest))
    if not any(part[0] == "token" and part[1] == "seq" for part in parts):
        return None, "pattern must contain {seq} (or {seq:02}) so every number is different"
    return parts, None


def pattern_tokens(parts):
    return set(part[1] for part in parts if part[0] == "token")


def format_pattern(parts, values):
    """Text for one element. values: {"seq": 3, "prefix": u"P-", "level": ...}.
    Raises KeyError naming the token when a value is missing."""
    output = []
    for part in parts:
        if part[0] == "text":
            output.append(part[1])
            continue
        name, width = part[1], part[2]
        value = values.get(name)
        if value is None:
            raise KeyError(name)
        if name == "seq":
            text = unicode(int(value))
            if width:
                text = text.zfill(width)
            output.append(text)
        else:
            output.append(normalize_string(value))
    return u"".join(output)


def _whole_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, long)):
        return int(value)
    if isinstance(value, float) and value == int(value):
        return int(value)
    return None


def validate_request(data):
    """Shape check of the request body. Returns (options, error message)."""
    if not isinstance(data, dict):
        return None, "Request body must be a JSON object"
    target = data.get("target")
    if not isinstance(target, basestring) or not target.strip():
        return None, "target is required: doors, windows, rooms, sheets or a category name"
    kind = target_kind(target)

    pattern = data.get("pattern")
    if pattern is None:
        pattern = DEFAULT_PATTERN
    parts, error = parse_pattern(pattern)
    if error:
        return None, error
    tokens = pattern_tokens(parts)

    prefix = data.get("prefix")
    if prefix is None:
        prefix = u""
    if not isinstance(prefix, basestring):
        return None, "prefix must be text"

    start = _whole_number(data.get("start", 1))
    if start is None or start < 0:
        return None, "start must be a whole number >= 0"
    step = _whole_number(data.get("step", 1))
    if step is None or step < 1:
        return None, "step must be a whole number >= 1"

    order = data.get("order")
    if order is None:
        order = "current" if kind == "sheets" else "location"
    if order not in ORDERS:
        return None, "order must be one of: {}".format(", ".join(ORDERS))
    group_by = data.get("group_by")
    if group_by is not None and group_by not in GROUPS:
        return None, "group_by must be room, level or omitted"

    parameter = data.get("parameter")
    if parameter is not None and (not isinstance(parameter, basestring)
                                  or not parameter.strip()):
        return None, "parameter must be a non-empty name"

    level = data.get("level")
    if level is not None and (not isinstance(level, basestring) or not level.strip()):
        return None, "level must be a level name"
    view_id = data.get("view_id")
    view_name = data.get("view_name")
    if view_id is not None and _whole_number(view_id) is None:
        return None, "view_id must be an integer"
    if view_name is not None and (not isinstance(view_name, basestring)
                                  or not view_name.strip()):
        return None, "view_name must be a view name"
    if view_id is not None and view_name is not None:
        return None, "Pass view_name or view_id, not both"
    phase = data.get("phase")
    if phase is not None and (not isinstance(phase, basestring) or not phase.strip()):
        return None, "phase must be a phase name"

    element_ids = data.get("element_ids")
    if element_ids is not None:
        if not isinstance(element_ids, list) or not element_ids:
            return None, "element_ids must be a non-empty list"
        if len(element_ids) > MAX_ITEMS:
            return None, "At most {} element_ids per call, got {}".format(
                MAX_ITEMS, len(element_ids))
        if any(_whole_number(value) is None for value in element_ids):
            return None, "element_ids must be integers"
        element_ids = [int(value) for value in element_ids]

    if kind == "sheets":
        if order == "location":
            return None, "Sheets have no plan location; use order current or id"
        if group_by is not None:
            return None, "group_by is not available for sheets"
        if tokens & set(ROOM_TOKENS + ("level",)):
            return None, "Sheets have no room or level; use {prefix} and {seq}"
        if level is not None or view_id is not None or view_name is not None:
            return None, "level and view filters are not available for sheets; use element_ids"
        if phase is not None:
            return None, "phase is not available for sheets"
    if kind == "rooms" and group_by == "room":
        return None, "group_by room is for doors, windows and other elements; use level for rooms"

    return {
        "target": normalize_string(target), "kind": kind, "pattern": pattern, "parts": parts,
        "tokens": tokens, "prefix": prefix, "start": start, "step": step, "order": order,
        "group_by": group_by, "parameter": parameter, "level": level,
        "view_id": None if view_id is None else int(view_id), "view_name": view_name,
        "phase": phase, "element_ids": element_ids, "dry_run": data.get("dry_run", True) is not False,
    }, None


def needs_room(options):
    return options["kind"] != "rooms" and (
        options["group_by"] == "room" or bool(options["tokens"] & set(ROOM_TOKENS)))


def needs_level(options):
    return options["group_by"] == "level" or "level" in options["tokens"]


def reading_order(records, tolerance=ROW_TOLERANCE_M):
    """Plan reading order: by level elevation, then rows from top (north) to
    bottom, each row left to right. Points whose Y differs by at most
    tolerance (meters) from the first point of a row share that row.
    Records without a point go last in their level, by element id."""
    def level_key(record):
        elevation = record.get("elevation")
        return (1, 0.0) if elevation is None else (0, elevation)

    by_level = {}
    for record in records:
        by_level.setdefault(level_key(record), []).append(record)
    ordered = []
    for key in sorted(by_level):
        placed = [r for r in by_level[key] if r.get("x") is not None and r.get("y") is not None]
        loose = [r for r in by_level[key] if r.get("x") is None or r.get("y") is None]
        placed.sort(key=lambda r: (-r["y"], r["x"], r["element_id"]))
        rows = []
        for record in placed:
            if rows and rows[-1][0] - record["y"] <= tolerance:
                rows[-1][1].append(record)
            else:
                rows.append((record["y"], [record]))
        for _, row in rows:
            ordered.extend(sorted(row, key=lambda r: (r["x"], r["element_id"])))
        ordered.extend(sorted(loose, key=lambda r: r["element_id"]))
    return ordered


def sort_records(records, order):
    if order == "id":
        return sorted(records, key=lambda r: r["element_id"])
    if order == "current":
        return sorted(records, key=lambda r: (natural_key(r.get("current")), r["element_id"]))
    return reading_order(records)


def group_key(record, group_by):
    """Sort key of the record's group (rooms by number, levels by elevation)."""
    if group_by == "level":
        elevation = record.get("elevation")
        return ((1, 0.0) if elevation is None else (0, elevation),
                natural_key(record.get("level")))
    if group_by == "room":
        return (natural_key(record.get("room_number")), record.get("room_id") or 0)
    return None


def plan_numbers(records, options):
    """New value for every record, in the order they are numbered.

    Returns [(record, new_value)]. With group_by the sequence restarts in each
    group; groups follow the room number or the level elevation.
    """
    ordered = sort_records(records, options["order"])
    group_by = options["group_by"]
    if group_by:
        groups = {}
        keys = []
        for record in ordered:
            key = group_key(record, group_by)
            if key not in groups:
                groups[key] = []
                keys.append(key)
            groups[key].append(record)
        sequences = [groups[key] for key in sorted(keys)]
    else:
        sequences = [ordered]
    planned = []
    for sequence in sequences:
        for position, record in enumerate(sequence):
            values = {
                "seq": options["start"] + position * options["step"],
                "prefix": options["prefix"],
                "level": record.get("level"),
                "room_number": record.get("room_number"),
                "room_name": record.get("room_name"),
            }
            planned.append((record, format_pattern(options["parts"], values)))
    return planned


def value_key(value):
    """Comparison key for clashes: trimmed and case-insensitive."""
    if value is None:
        return u""
    return normalize_string(value).lower()


def find_conflicts(planned, outside_values, unique):
    """Clashes of the new values. planned: [(element_id, new_value)];
    outside_values: values of the elements left out of the batch.

    Returns (errors, warnings). Repeated values inside the batch are always
    errors; a value already used outside the batch is an error for unique
    parameters (sheet/room number) and a warning otherwise (e.g. Mark).
    """
    errors = []
    warnings = []
    seen = {}
    shown = {}
    for element_id, value in planned:
        key = value_key(value)
        if not key:
            errors.append({"element_id": element_id, "error": "New value is empty"})
            continue
        seen.setdefault(key, []).append(element_id)
        shown.setdefault(key, normalize_string(value))
    for key in sorted(seen):
        ids = seen[key]
        if len(ids) > 1:
            for element_id in ids:
                errors.append({"element_id": element_id,
                               "error": u"New value '{}' is repeated in the batch "
                                        u"(elements {})".format(
                                            shown[key], ", ".join(str(i) for i in ids))})
    outside = set(value_key(v) for v in outside_values if value_key(v))
    for element_id, value in planned:
        key = value_key(value)
        if key and key in outside:
            if unique:
                errors.append({"element_id": element_id,
                               "error": u"'{}' is already used by an element outside the "
                                        u"batch".format(normalize_string(value))})
            else:
                warnings.append(u"'{}' is already used by another element (element {})".format(
                    normalize_string(value), element_id))
    return errors, warnings


def temp_value(element_id, taken):
    """Temporary unique value used while swapping numbers."""
    base = u"{}{}".format(TEMP_PREFIX, element_id)
    value = base
    count = 1
    while value_key(value) in taken:
        count += 1
        value = u"{}-{}".format(base, count)
    return value


# ---------------------------------------------------------------------------
# Revit helpers
# ---------------------------------------------------------------------------

class RenumberError(Exception):
    """A request-level problem reported as 400."""

    def __init__(self, text):
        Exception.__init__(self, text)
        self.text = text


def _is_invalid(element_id):
    try:
        return element_id is None or element_id == DB.ElementId.InvalidElementId \
            or element_id_value(element_id) < 0
    except Exception:
        return True


def _text(parameter):
    try:
        value = parameter.AsString()
    except Exception:
        value = None
    return normalize_string(value) if value is not None else u""


def _builtin_of(parameter):
    try:
        return str(parameter.Definition.BuiltInParameter)
    except Exception:
        return None


def find_parameter(element, name, default_builtin):
    """Parameter by name in the Revit language or BuiltInParameter name (default
    builtin when name is None). Returns (parameter, error)."""
    if name is None:
        builtin = getattr(DB.BuiltInParameter, default_builtin)
        parameter = element.get_Parameter(builtin)
        matches = [parameter] if parameter is not None else []
        name = default_builtin
    else:
        matches = list(element.GetParameters(name))
        if not matches:
            builtin = _builtin_parameter(name)
            parameter = element.get_Parameter(builtin) if builtin is not None else None
            if parameter is not None:
                matches = [parameter]
    if not matches:
        return None, u"Parameter '{}' not found".format(normalize_string(name))
    if len(matches) > 1:
        return None, u"{} parameters named '{}'; rename one to disambiguate".format(
            len(matches), normalize_string(name))
    return matches[0], None


def resolve_parameter(element, name, default_builtin):
    """Writable text parameter to renumber. Returns (parameter, error)."""
    parameter, error = find_parameter(element, name, default_builtin)
    if error:
        return None, error
    label = normalize_string(name if name is not None else default_builtin)
    if parameter.IsReadOnly:
        return None, u"Parameter '{}' is read-only".format(label)
    if parameter.StorageType != DB.StorageType.String:
        return None, u"Parameter '{}' is not a text parameter".format(label)
    return parameter, None


def _resolve_view(doc, options):
    view = None
    if options["view_id"] is not None:
        view = doc.GetElement(element_id_from_int(options["view_id"]))
        if view is None or not isinstance(view, DB.View):
            raise RenumberError("View {} not found".format(options["view_id"]))
    elif options["view_name"]:
        for candidate in DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements():
            if not candidate.IsTemplate and \
                    same_name(element_name(candidate), options["view_name"]):
                view = candidate
                break
        if view is None:
            raise RenumberError(u"View '{}' not found".format(
                normalize_string(options["view_name"])))
    if view is not None:
        view_type = str(getattr(view, "ViewType", ""))
        if view.IsTemplate or view_type not in GRAPHICAL_VIEW_TYPES:
            raise RenumberError(
                u"View '{}' ({}) does not show model elements; use a plan, section, "
                u"elevation or 3D view".format(element_name(view), view_type))
    return view


def _resolve_level(doc, name):
    levels = list(DB.FilteredElementCollector(doc).OfClass(DB.Level).ToElements())
    for level in levels:
        if same_name(element_name(level), name):
            return level
    raise RenumberError(u"Level '{}' not found. Levels: {}".format(
        normalize_string(name), u", ".join(element_name(l) for l in levels[:50])))


def _phases(doc):
    try:
        return list(doc.Phases)
    except Exception:
        return []


def resolve_phase(doc, options, view):
    """(phase, source): the phase argument by name, else the view's phase,
    else the last project phase. source is "phase", "view" or "last"."""
    phases = _phases(doc)
    if options.get("phase"):
        for phase in phases:
            if same_name(element_name(phase), options["phase"]):
                return phase, "phase"
        raise RenumberError(u"Phase '{}' not found. Phases: {}".format(
            normalize_string(options["phase"]), u", ".join(element_name(p) for p in phases)))
    if view is not None:
        try:
            parameter = view.get_Parameter(DB.BuiltInParameter.VIEW_PHASE)
            phase = doc.GetElement(parameter.AsElementId()) if parameter is not None else None
            if phase is not None:
                return phase, "view"
        except Exception:
            pass
    return (phases[-1] if phases else None), "last"


def _same_id(a, b):
    try:
        return element_id_value(a) == element_id_value(b)
    except Exception:
        return False


def _room_phase_id(room):
    """Phase of a room (ROOM_PHASE), else the phase it was created in."""
    try:
        parameter = room.get_Parameter(DB.BuiltInParameter.ROOM_PHASE)
        if parameter is not None:
            return parameter.AsElementId()
    except Exception:
        pass
    return getattr(room, "CreatedPhaseId", None)


def _in_phase(room, phase):
    return phase is None or _same_id(_room_phase_id(room), phase.Id)


def _room_of(element, phase):
    """Room on the "to" side of a door/window, else the "from" side, else the
    room that contains a family instance."""
    if phase is None:
        return None
    for getter in ("get_ToRoom", "get_FromRoom", "get_Room"):
        method = getattr(element, getter, None)
        if method is None:
            continue
        try:
            room = method(phase)
        except Exception:
            room = None
        if room is not None:
            return room
    return None


def _plan_point(element):
    """(x, y) in meters of the element's location, or None."""
    point = None
    try:
        location = element.Location
        point = getattr(location, "Point", None)
        if point is None and getattr(location, "Curve", None) is not None:
            point = location.Curve.Evaluate(0.5, True)
    except Exception:
        point = None
    if point is None:
        try:
            box = element.get_BoundingBox(None)
            if box is not None:
                return ((box.Min.X + box.Max.X) / 2.0 * FT_TO_M,
                        (box.Min.Y + box.Max.Y) / 2.0 * FT_TO_M)
        except Exception:
            return None
        return None
    return (point.X * FT_TO_M, point.Y * FT_TO_M)


def _is_placed_room(room):
    try:
        return room.Location is not None and room.Area > 0
    except Exception:
        return False


def _scope_elements(doc, kind, category, view=None):
    """Every element the renumbering can touch (no level/id filter)."""
    collector = DB.FilteredElementCollector(doc, view.Id) if view is not None \
        else DB.FilteredElementCollector(doc)
    if kind == "sheets":
        return list(collector.OfClass(DB.ViewSheet).ToElements())
    if kind == "doors":
        collector = collector.OfCategory(DB.BuiltInCategory.OST_Doors)
    elif kind == "windows":
        collector = collector.OfCategory(DB.BuiltInCategory.OST_Windows)
    elif kind == "rooms":
        collector = collector.OfCategory(DB.BuiltInCategory.OST_Rooms)
    else:
        collector = collector.OfCategoryId(category.Id)
    return list(collector.WhereElementIsNotElementType().ToElements())


def _count(skipped, key):
    skipped[key] = skipped.get(key, 0) + 1


def select_targets(doc, options, category, view, level, phase=None):
    """(targets, errors, skipped). errors: element_ids that are not targets.
    Rooms outside phase are skipped (room numbers are compared per phase)."""
    kind = options["kind"]
    candidates = _scope_elements(doc, kind, category, view)
    skipped = {}
    targets = []
    errors = []
    wanted = set(options["element_ids"]) if options["element_ids"] else None
    found = set()
    for element in candidates:
        element_id = element_id_value(element.Id)
        if wanted is not None and element_id not in wanted:
            continue
        found.add(element_id)
        if kind == "sheets" and wanted is None and getattr(element, "IsPlaceholder", False):
            _count(skipped, "placeholder_sheets")
            continue
        if kind == "rooms" and not _in_phase(element, phase):
            if wanted is not None:
                errors.append({"element_id": element_id,
                               "error": u"Room is not in phase '{}'; pass phase".format(
                                   element_name(phase))})
            else:
                _count(skipped, "other_phase_rooms")
            continue
        if kind == "rooms" and not _is_placed_room(element):
            _count(skipped, "unplaced_rooms")
            continue
        if level is not None and (_is_invalid(getattr(element, "LevelId", None)) or
                                  element_id_value(element.LevelId) != element_id_value(level.Id)):
            continue
        targets.append(element)
    if wanted is not None:
        for element_id in options["element_ids"]:
            if element_id not in found:
                errors.append({"element_id": element_id,
                               "error": u"Not a {} element{}".format(
                                   options["target"],
                                   " visible in the view" if view is not None else "")})
    return targets, errors, skipped


def _in_group(element):
    return not _is_invalid(getattr(element, "GroupId", None))


def _created_phase(doc, element):
    try:
        created = getattr(element, "CreatedPhaseId", None)
        return doc.GetElement(created) if not _is_invalid(created) else None
    except Exception:
        return None


def build_records(doc, targets, options, phase, phase_source="last"):
    """Read each target. Returns (records, parameters by id, errors).

    When the phase is only the default (last phase) and an element has no room
    in it, the element's creation phase is tried too (record "room_phase")."""
    kind = options["kind"]
    default_builtin = DEFAULT_PARAMETERS.get(kind, DEFAULT_ELEMENT_PARAMETER)
    room_needed = needs_room(options)
    level_needed = needs_level(options)
    level_cache = {}
    records = []
    parameters = {}
    errors = []
    for element in targets:
        element_id = element_id_value(element.Id)
        if _in_group(element):
            errors.append({"element_id": element_id,
                           "error": "Element is inside a model group; edit the group in Revit"})
            continue
        parameter, error = resolve_parameter(element, options["parameter"], default_builtin)
        if error:
            errors.append({"element_id": element_id, "error": error})
            continue
        record = {"element_id": element_id, "current": _text(parameter)}
        if kind == "sheets":
            record["name"] = element_name(element)
        else:
            level_id = getattr(element, "LevelId", None)
            if not _is_invalid(level_id):
                key = element_id_value(level_id)
                if key not in level_cache:
                    level_element = doc.GetElement(level_id)
                    level_cache[key] = (element_name(level_element),
                                        level_element.Elevation * FT_TO_M) \
                        if level_element is not None else (None, None)
                record["level"], record["elevation"] = level_cache[key]
            if level_needed and not record.get("level"):
                errors.append({"element_id": element_id, "error": "Element has no level"})
                continue
            point = _plan_point(element)
            if point is not None:
                record["x"], record["y"] = point
            room = None
            if kind == "rooms":
                room = element
            elif room_needed:
                room = _room_of(element, phase)
                if room is None and phase_source == "last":
                    created = _created_phase(doc, element)
                    if created is not None and (phase is None or
                                                not _same_id(created.Id, phase.Id)):
                        room = _room_of(element, created)
                        if room is not None:
                            record["room_phase"] = element_name(created)
            if room is not None:
                record["room_id"] = element_id_value(room.Id)
                number = room.get_Parameter(DB.BuiltInParameter.ROOM_NUMBER)
                name = room.get_Parameter(DB.BuiltInParameter.ROOM_NAME)
                record["room_number"] = _text(number) if number is not None else u""
                record["room_name"] = _text(name) if name is not None else u""
            elif room_needed:
                errors.append({"element_id": element_id,
                               "error": "No room on either side of the element in the phase "
                                        "used (place the element inside or next to a room, "
                                        "or pass phase)"})
                continue
        records.append(record)
        parameters[element_id] = parameter
    return records, parameters, errors


def outside_values(doc, options, category, batch_ids, phase=None):
    """Current values of the elements of the same kind left out of the batch
    (for rooms, only the rooms of the same phase)."""
    kind = options["kind"]
    default_builtin = DEFAULT_PARAMETERS.get(kind, DEFAULT_ELEMENT_PARAMETER)
    values = []
    for element in _scope_elements(doc, kind, category):
        if element_id_value(element.Id) in batch_ids:
            continue
        if kind == "rooms" and not _in_phase(element, phase):
            continue
        try:
            parameter, _ = find_parameter(element, options["parameter"], default_builtin)
            if parameter is not None:
                values.append(_text(parameter))
        except Exception:
            continue
    return values


def _result(record, after):
    result = {"element_id": record["element_id"], "before": record["current"], "after": after}
    for key in ("name", "level", "room_number", "room_phase"):
        if record.get(key):
            result[key] = record[key]
    return result


def apply_numbers(doc, planned, parameters, unique, taken, commit):
    """Write the new values in one transaction (two passes for unique
    parameters). planned: [(record, new_value)] of changed elements only.
    Returns (errors, warnings, results)."""
    batch = Batch(doc, "MCP: renumber elements")
    errors = []
    batch.start()
    try:
        passes = []
        if unique:
            passes.append([(record, temp_value(record["element_id"], taken))
                           for record, _ in planned])
        passes.append(planned)
        for writes in passes:
            for record, value in writes:
                try:
                    accepted = parameters[record["element_id"]].Set(value)
                except Exception as e:
                    errors.append({"element_id": record["element_id"], "error": error_text(e)})
                    continue
                if not accepted:
                    errors.append({"element_id": record["element_id"],
                                   "error": u"Revit rejected the value '{}'".format(value)})
            if errors:
                break
        if errors:
            batch.rollback()
            return errors, batch.warnings, None
        results = [_result(record, _text(parameters[record["element_id"]]))
                   for record, _ in planned]
        if not batch.finish(commit):
            errors = [{"error": e} for e in batch.errors] or \
                [{"error": "Revit did not commit the transaction ({})".format(
                    getattr(batch, "status", None))}]
            return errors, batch.warnings, None
    except Exception:
        batch.abort()
        raise
    return [], batch.warnings, results


def _error_body(message, errors, extra=None):
    body = {"status": "error", "error": message, "changed": 0,
            "errors": errors[:MAX_ERRORS], "error_count": len(errors)}
    if extra:
        body.update(extra)
    return body


def renumber(doc, options, authorize):
    """Plan, check and apply. authorize() returns a denial response or None;
    it is called only for a real run with a valid plan. Returns (body, status)
    or a denial response."""
    kind = options["kind"]
    category = None
    if kind == "category":
        category = find_category(doc, options["target"])
        if category is None:
            raise RenumberError(u"Category '{}' not found".format(options["target"]))
    view = _resolve_view(doc, options) if kind != "sheets" else None
    level = _resolve_level(doc, options["level"]) if options["level"] else None
    phase, phase_source = None, None
    if kind == "rooms" or needs_room(options):
        phase, phase_source = resolve_phase(doc, options, view)

    targets, errors, skipped = select_targets(doc, options, category, view, level, phase)
    if not targets and not errors:
        raise RenumberError(u"No {} elements match the filters".format(options["target"]))
    if len(targets) > MAX_ITEMS:
        raise RenumberError(
            "{} elements match; at most {} per call. Filter by level, view or "
            "element_ids".format(len(targets), MAX_ITEMS))

    records, parameters, read_errors = build_records(
        doc, targets, options, phase, phase_source)
    errors.extend(read_errors)
    extra = {"target": options["target"], "skipped": skipped}
    if phase is not None:
        extra["phase"] = element_name(phase)
    if errors:
        return _error_body("{} elements cannot be renumbered; nothing was changed".format(
            len(errors)), errors, extra), 200

    planned = plan_numbers(records, options)
    default_builtin = DEFAULT_PARAMETERS.get(kind, DEFAULT_ELEMENT_PARAMETER)
    unique = False
    if records:
        builtin = _builtin_of(parameters[records[0]["element_id"]])
        unique = builtin in UNIQUE_PARAMETERS or (
            options["parameter"] is None and default_builtin in UNIQUE_PARAMETERS)
    batch_ids = set(record["element_id"] for record in records)
    others = outside_values(doc, options, category, batch_ids, phase)
    conflicts, warnings = find_conflicts(
        [(record["element_id"], value) for record, value in planned], others, unique)
    if conflicts:
        return _error_body("{} of {} new values clash; nothing was changed".format(
            len(conflicts), len(planned)), conflicts, extra), 200

    changed = [(record, value) for record, value in planned if value != record["current"]]
    summary = dict(extra)
    summary.update({"pattern": options["pattern"], "order": options["order"],
                    "group_by": options["group_by"], "unique_parameter": unique,
                    "unchanged": len(planned) - len(changed)})
    if not changed:
        summary.update({"status": "success", "changed": 0, "results": [],
                        "warnings": warnings[:MAX_WARNINGS],
                        "message": "Every element already has the requested value"})
        return summary, 200

    if not options["dry_run"]:
        denial = authorize()
        if denial is not None:
            return denial

    taken = set(value_key(v) for v in others) | \
        set(value_key(record["current"]) for record in records) | \
        set(value_key(value) for _, value in planned)
    write_errors, revit_warnings, written = apply_numbers(
        doc, changed, parameters, unique, taken, commit=not options["dry_run"])
    warnings = warnings + [w for w in revit_warnings if w not in warnings]
    if write_errors:
        return _error_body("Revit rejected the renumbering; nothing was changed",
                           write_errors, dict(extra, warnings=warnings[:MAX_WARNINGS])), 200
    summary.update({"changed": len(changed), "results": written,
                    "warnings": warnings[:MAX_WARNINGS]})
    if options["dry_run"]:
        summary["status"] = "dry_run"
        summary["message"] = ("Dry run: Revit accepted all {} new values. Nothing was changed; "
                              "call again with dry_run=false to apply.".format(len(changed)))
    else:
        summary["status"] = "success"
    return summary, 200


def _payload(request):
    data = json.loads(request.data) if isinstance(request.data, str) else request.data
    return data or {}


def register_renumber_routes(api):
    """Register the renumbering route with the API"""

    @api.route("/renumber_elements/", methods=["POST"])
    def renumber_elements(doc, request):
        """
        Renumber doors, windows, rooms, sheets or a category's Mark.

        Expected JSON payload:
        {
            "target": "doors",
            "pattern": "P-{room_number}-{seq:02}",
            "group_by": "room",
            "order": "location",
            "level": "Térreo",
            "phase": "Nova construção",
            "dry_run": true
        }
        """
        # Authenticate first; the review dialog is shown below only for real writes.
        denial = authorize_request(request, action=None)
        if denial is not None:
            return denial
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503)
            options, error = validate_request(_payload(request))
            if error:
                return routes.make_response(data={"error": error}, status=400)
            outcome = renumber(
                doc, options,
                lambda: authorize_request(request, action="Renumber elements"))
            if not isinstance(outcome, tuple):
                return outcome
            body, status = outcome
            return routes.make_response(data=body, status=status)
        except RenumberError as e:
            return routes.make_response(data={"error": e.text}, status=400)
        except Exception as e:
            logger.error("Error in renumber_elements route: %s", e)
            return routes.make_response(data={"error": error_text(e)}, status=500)
