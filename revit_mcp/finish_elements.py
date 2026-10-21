# -*- coding: UTF-8 -*-
"""
Finish floors and ceilings from room boundaries (spec 020).

One floor (or ceiling) per room, drawn on the room's boundary loops (finish
face by default), so room_finishes / quantities_by_rule can measure modeled
elements instead of room outlines. Same batch rules as modeling.py: dry_run
(the default) runs the batch inside a TransactionGroup and rolls it back; a
real run is approved once in Revit and is a single undo step.

Rooms that cannot get an element (unplaced, not enclosed, already covered,
broken boundary, no usable ceiling height) are reported in "skipped" before
any transaction; the remaining rooms are created all or nothing.
Revit works in feet internally; inputs and outputs are in m and m2.
"""

from pyrevit import routes, DB
import json
import logging
import math
from .security import authorize_request
from .utils import normalize_string, element_id_from_int, element_id_value
# Shared batch machinery (spec 011) and the existing-host detection of
# room_finishes (spec 015). Only these are imported; tests/unit/
# test_finish_element_route_logic.py calls them with the exact arguments used here.
from .modeling import (ItemError, run_batch, error_text, element_name, find_named,
                       is_number, to_feet, to_meters, sqft_to_sqm, MAX_ITEMS, MAX_LISTED_NAMES)
from .quantities import collect_hosts, _hosts_in_room

logger = logging.getLogger(__name__)

KINDS = {
    "floor": {
        "noun": "floors", "action": "Create floors from rooms",
        "transaction": "MCP: create floors from rooms", "category": "OST_Floors",
        "api_class": "Floor", "type_class": "FloorType", "type_group": "FloorType",
        "type_key": "floor_type", "offset_param": "FLOOR_HEIGHTABOVELEVEL_PARAM",
        "existing": "existing_floor",
    },
    "ceiling": {
        "noun": "ceilings", "action": "Create ceilings from rooms",
        "transaction": "MCP: create ceilings from rooms", "category": "OST_Ceilings",
        "api_class": "Ceiling", "type_class": "CeilingType", "type_group": "CeilingType",
        "type_key": "ceiling_type", "offset_param": "CEILING_HEIGHTABOVELEVEL_PARAM",
        "existing": "existing_ceiling",
    },
}
BOUNDARY_LOCATIONS = {"finish": "Finish", "center": "Center"}
MAX_FLOOR_OFFSET_M = 5.0
MAX_CEILING_HEIGHT_M = 10.0
# Without height_m a ceiling goes at the room height (upper limit + offset), but
# only when that looks like a ceiling height; otherwise the room is skipped.
MAX_DEFAULT_CEILING_HEIGHT_M = 6.0
# Boundary repair, in feet: segments under 1 mm are dropped and the gap they
# leave (up to 10 mm) is closed by moving the end of a neighbouring line.
# A modeled element bigger than room area x SPAN_FACTOR + SPAN_MARGIN_M2 spans
# other rooms (a slab under several rooms): not this room's finish. Same rule
# as quantities.choose_area.
SPAN_FACTOR = 1.05
SPAN_MARGIN_M2 = 0.5
MAX_PREVIEW_ROOMS = 50
MIN_SEGMENT_FT = 0.001 / 0.3048
MAX_GAP_FT = 0.01 / 0.3048
JOIN_EPSILON_FT = 1e-6


class RequestError(Exception):
    """A problem with the whole request (HTTP 400)."""

    def __init__(self, text, extra=None):
        Exception.__init__(self, text)
        self.text = text
        self.extra = extra or {}


# ---------------------------------------------------------------------------
# Pure helpers (no Revit API; checked under IronPython by scripts/ironpython_check.py)
# ---------------------------------------------------------------------------

def _text_of(value):
    if value is None:
        return u""
    return normalize_string(value)


def _key(value):
    """Case- and whitespace-insensitive comparison key (None -> u"")."""
    return u" ".join(_text_of(value).lower().split())


def room_status(has_location, area):
    """"unplaced" (no location), "not_enclosed" (placed, zero area) or "placed"."""
    if not has_location:
        return "unplaced"
    if not area or area <= 0:
        return "not_enclosed"
    return "placed"


def _is_name(value):
    return isinstance(value, basestring) and bool(value.strip())


def _is_int(value):
    return isinstance(value, (int, long)) and not isinstance(value, bool)


def validate_request(data, kind):
    """Shape check of the request body. Error message or None."""
    if not isinstance(data, dict):
        return "Request body must be an object"
    room_ids = data.get("room_ids")
    numbers = data.get("room_numbers")
    level = data.get("level")
    if room_ids is not None:
        if not isinstance(room_ids, list) or not room_ids \
                or not all(_is_int(value) for value in room_ids):
            return "room_ids must be a non-empty list of integers"
        if len(room_ids) > MAX_ITEMS:
            return "At most {} rooms per call, got {}".format(MAX_ITEMS, len(room_ids))
    if numbers is not None:
        if not isinstance(numbers, list) or not numbers \
                or not all(_is_name(value) for value in numbers):
            return "room_numbers must be a non-empty list of room numbers (text)"
        if len(numbers) > MAX_ITEMS:
            return "At most {} rooms per call, got {}".format(MAX_ITEMS, len(numbers))
    if room_ids is not None and numbers is not None:
        return "Give room_ids or room_numbers, not both"
    if level is not None and not _is_name(level):
        return "level must be a non-empty name"
    if room_ids is None and numbers is None and level is None:
        return "Give room_ids, room_numbers or level to choose the rooms"
    type_key = KINDS[kind]["type_key"]
    if data.get(type_key) is not None and not _is_name(data[type_key]):
        return "{} must be a non-empty name".format(type_key)
    location = data.get("boundary_location", "finish")
    if location not in BOUNDARY_LOCATIONS:
        return "boundary_location must be one of: {}".format(
            ", ".join(sorted(BOUNDARY_LOCATIONS)))
    for key in ("skip_existing", "dry_run"):
        if data.get(key) is not None and not isinstance(data[key], bool):
            return "{} must be true or false".format(key)
    expected = data.get("expected_count")
    if expected is not None and (not _is_int(expected) or expected < 0):
        return "expected_count must be a whole number >= 0"
    offset = data.get("offset_m")
    height = data.get("height_m")
    if kind == "floor":
        if height is not None:
            return "height_m is for ceilings; use offset_m for floors"
        if offset is not None and (not is_number(offset) or abs(offset) > MAX_FLOOR_OFFSET_M):
            return "offset_m must be a number between -{0} and {0}".format(MAX_FLOOR_OFFSET_M)
    else:
        if offset is not None:
            return "offset_m is for floors; use height_m for ceilings"
        if height is not None and (not is_number(height) or height <= 0
                                   or height > MAX_CEILING_HEIGHT_M):
            return "height_m must be a number above 0 and up to {}".format(MAX_CEILING_HEIGHT_M)
    return None


def ceiling_height(requested_m, upper_is_own_level, lower_offset_m, unbounded_m):
    """(height above the room's level in m, source, error).

    height_m wins. Otherwise the room's top is used, but only when the room's
    upper limit is its own level: the top is then lower offset + unbounded
    height above that level (CEILING_HEIGHTABOVELEVEL_PARAM is measured from
    the level, Room.UnboundedHeight from the room base). Any other case asks
    for height_m.
    """
    if requested_m is not None:
        return float(requested_m), "height_m", None
    if not upper_is_own_level:
        return None, None, (u"room upper limit is another level; pass height_m")
    if not unbounded_m or unbounded_m <= 0:
        return None, None, u"room has no height; pass height_m"
    height = (lower_offset_m or 0.0) + unbounded_m
    if height <= 0 or height > MAX_DEFAULT_CEILING_HEIGHT_M:
        return None, None, (u"room top at {:.2f} m above the level is outside 0-{} m; "
                            u"pass height_m".format(height, MAX_DEFAULT_CEILING_HEIGHT_M))
    return height, "room_top", None


def exists_in_phase(created, demolished, phase):
    """True when an element with these phase positions (None = unknown/never)
    exists in phase position `phase`: created at or before it and not
    demolished at or before it. Unknown positions count as existing."""
    if phase is None:
        return True
    if created is not None and created > phase:
        return False
    if demolished is not None and demolished <= phase:
        return False
    return True


def preview_rooms(labels, limit=MAX_PREVIEW_ROOMS):
    """Room numbers (or ids when a room has no number) for the approval dialog."""
    shown = []
    for label in labels[:limit]:
        shown.append(label.get("number") or u"id {}".format(label.get("room_id")))
    if len(labels) > limit:
        shown.append(u"... +{}".format(len(labels) - limit))
    return shown


def covering_hosts(hosts, room_area_m2):
    """Existing elements that are this room's own finish: a modeled element much
    bigger than the room (a slab under several rooms) does not count."""
    if not room_area_m2:
        return list(hosts)
    limit = room_area_m2 * SPAN_FACTOR + SPAN_MARGIN_M2
    return [host for host in hosts if host["area_m2"] <= limit]


def _gap(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _length(a, b):
    return math.sqrt(sum((b[i] - a[i]) ** 2 for i in range(3)))


def clean_loop(segments, min_length=MIN_SEGMENT_FT, max_gap=MAX_GAP_FT,
               join_epsilon=JOIN_EPSILON_FT):
    """Make one boundary loop buildable as a CurveLoop.

    segments: [{"start": (x, y, z), "end": (x, y, z), "length": L, "line": bool}]
    in loop order (feet). Drops segments shorter than min_length and closes the
    gaps (up to max_gap) by moving the end of a neighbouring straight segment.
    Returns (segments, repaired count, error); kept segments get "rebuilt": True
    when their end points changed.
    """
    kept = []
    for segment in segments:
        if segment["length"] >= min_length:
            item = dict(segment)
            item["rebuilt"] = False
            kept.append(item)
    repaired = len(segments) - len(kept)
    if len(kept) < 2:
        return None, repaired, u"boundary has fewer than 2 segments longer than 1 mm"
    count = len(kept)
    for index in range(count):
        current = kept[index]
        following = kept[(index + 1) % count]
        gap = _gap(current["end"], following["start"])
        if gap <= join_epsilon:
            continue
        if gap > max_gap:
            return None, repaired, u"boundary is open: gap of {:.1f} mm".format(gap * 304.8)
        if following["line"]:
            following["start"] = current["end"]
            following["rebuilt"] = True
        elif current["line"]:
            current["end"] = following["start"]
            current["rebuilt"] = True
        else:
            return None, repaired, u"gap of {:.1f} mm between two curved segments".format(
                gap * 304.8)
        repaired += 1
    for item in kept:
        if item["rebuilt"]:
            item["length"] = _length(item["start"], item["end"])
            if item["length"] < min_length:
                return None, repaired, u"boundary has segments too short to build"
    return kept, repaired, None


def summary_message(status, created, skipped, noun):
    if status == "dry_run":
        return (u"Dry run: Revit accepted {} {}; {} rooms skipped. Nothing was changed; call "
                u"again with dry_run=false to apply.".format(created, noun, skipped))
    return u"{} {} created; {} rooms skipped.".format(created, noun, skipped)


# ---------------------------------------------------------------------------
# Revit helpers
# ---------------------------------------------------------------------------

def _text(element, builtin):
    try:
        parameter = element.get_Parameter(builtin)
        value = parameter.AsString() if parameter is not None else None
        return normalize_string(value) if value else None
    except Exception:
        return None


def _parse_body(request):
    if not request or not request.data:
        return {}
    if isinstance(request.data, basestring):
        return json.loads(request.data)
    return request.data


def _elements(doc, category_name):
    category = getattr(DB.BuiltInCategory, category_name)
    return DB.FilteredElementCollector(doc).OfCategory(category).WhereElementIsNotElementType()


def _invalid_id(element_id):
    return element_id is None or element_id == DB.ElementId.InvalidElementId


def _level_name(doc, level_id, cache):
    if _invalid_id(level_id):
        return None
    key = element_id_value(level_id)
    if key not in cache:
        level = doc.GetElement(level_id)
        cache[key] = element_name(level) if level is not None else None
    return cache[key]


def _id_param(element, builtin_name):
    """An ElementId parameter, or None when missing/invalid."""
    try:
        parameter = element.get_Parameter(getattr(DB.BuiltInParameter, builtin_name))
        value = parameter.AsElementId() if parameter is not None else None
    except Exception:
        return None
    return None if _invalid_id(value) else value


def _double_param(element, builtin_name):
    try:
        parameter = element.get_Parameter(getattr(DB.BuiltInParameter, builtin_name))
        return parameter.AsDouble() if parameter is not None and parameter.HasValue else None
    except Exception:
        return None


def phase_positions(doc):
    """{phase id value: position} in project order (doc.Phases)."""
    try:
        return dict((element_id_value(phase.Id), index)
                    for index, phase in enumerate(doc.Phases))
    except Exception:
        return {}


def _position(positions, element_id):
    if _invalid_id(element_id):
        return None
    return positions.get(element_id_value(element_id))


def host_exists_in_phase(doc, host, positions, phase_position):
    """Existing-host record (collect_hosts) present in the room's phase."""
    if phase_position is None:
        return True
    try:
        element = doc.GetElement(element_id_from_int(host["element_id"]))
        created = _position(positions, element.CreatedPhaseId)
        demolished = _position(positions, element.DemolishedPhaseId)
    except Exception:
        return True
    return exists_in_phase(created, demolished, phase_position)


def _type_name(element_type):
    return _text(element_type, DB.BuiltInParameter.SYMBOL_NAME_PARAM) or element_name(element_type)


def _room_label(doc, room, level_cache):
    label = {"room_id": element_id_value(room.Id),
             "number": _text(room, DB.BuiltInParameter.ROOM_NUMBER),
             "name": _text(room, DB.BuiltInParameter.ROOM_NAME),
             "level": _level_name(doc, room.LevelId, level_cache)}
    phase_id = _id_param(room, "ROOM_PHASE")
    if phase_id is not None:
        phase = doc.GetElement(phase_id)
        label["phase"] = element_name(phase) if phase is not None else None
    return label


def _is_room(element):
    room_class = getattr(getattr(DB, "Architecture", None), "Room", None)
    return room_class is not None and isinstance(element, room_class)


def select_rooms(doc, data, level_cache):
    """(rooms [(label, room)], skipped [label + reason]) for the requested rooms."""
    level = data.get("level")
    rooms = []
    skipped = []
    if data.get("room_ids") is not None:
        candidates = []
        for value in data["room_ids"]:
            element = doc.GetElement(element_id_from_int(int(value)))
            if element is None:
                skipped.append({"room_id": value, "reason": "not_found"})
            elif not _is_room(element):
                skipped.append({"room_id": value, "reason": "not_a_room"})
            else:
                candidates.append(element)
    else:
        candidates = list(_elements(doc, "OST_Rooms"))
    wanted = None
    if data.get("room_numbers") is not None:
        wanted = set(_key(number) for number in data["room_numbers"])
    found = set()
    for room in candidates:
        label = _room_label(doc, room, level_cache)
        if level is not None and _key(label["level"]) != _key(level):
            if data.get("room_ids") is not None:
                skipped.append(dict(label, reason="other_level"))
            continue
        if wanted is not None:
            if _key(label["number"]) not in wanted:
                continue
            found.add(_key(label["number"]))
        status = room_status(room.Location is not None, room.Area)
        if status != "placed":
            skipped.append(dict(label, reason=status))
            continue
        rooms.append((label, room))
    rooms.sort(key=lambda item: (_key(item[0]["level"]), _key(item[0]["number"])))
    missing = []
    if wanted is not None:
        missing = [number for number in data["room_numbers"] if _key(number) not in found]
    return rooms, skipped, missing


def resolve_type(doc, info, name):
    """Floor or ceiling type by name; default: the model's default type, else the first."""
    types = list(DB.FilteredElementCollector(doc).OfClass(
        getattr(DB, info["type_class"])).ToElements())
    if info["type_class"] == "FloorType":
        types = [t for t in types if not getattr(t, "IsFoundationSlab", False)]
    if not types:
        raise RequestError("The model has no {} types".format(info["noun"][:-1]))
    if name is not None:
        found = find_named([(_type_name(t), t) for t in types], name)
        if found is None:
            raise RequestError("{} '{}' not found".format(info["type_key"], name), {
                "available_types": sorted(_type_name(t) for t in types)[:MAX_LISTED_NAMES]})
        return found
    try:
        type_id = doc.GetDefaultElementTypeId(getattr(DB.ElementTypeGroup, info["type_group"]))
        default = doc.GetElement(type_id) if type_id is not None else None
    except Exception:
        default = None
    if default is not None:
        return default
    return sorted(types, key=lambda t: _key(_type_name(t)))[0]


def _segment_record(curve):
    start = curve.GetEndPoint(0)
    end = curve.GetEndPoint(1)
    return {"curve": curve, "start": (start.X, start.Y, start.Z),
            "end": (end.X, end.Y, end.Z), "length": curve.Length,
            "line": isinstance(curve, DB.Line)}


def _net_list(cls, items):
    from System.Collections.Generic import List
    result = List[cls]()
    for item in items:
        result.Add(item)
    return result


def build_loops(room, options):
    """CurveLoops of the room boundary (first = outer, others = holes) and the
    number of repaired segments. Raises ItemError when a loop cannot be built."""
    boundary = room.GetBoundarySegments(options)
    if not boundary or len(boundary) == 0:
        raise ItemError(u"room has no boundary")
    loops = []
    repaired_total = 0
    for number, segments in enumerate(boundary):
        records = [_segment_record(segment.GetCurve()) for segment in segments]
        cleaned, repaired, error = clean_loop(records)
        if error:
            raise ItemError(u"loop {}: {}".format(number + 1, error))
        loop = DB.CurveLoop()
        try:
            for item in cleaned:
                if item["rebuilt"]:
                    curve = DB.Line.CreateBound(DB.XYZ(*item["start"]), DB.XYZ(*item["end"]))
                else:
                    curve = item["curve"]
                loop.Append(curve)
        except Exception as e:
            raise ItemError(u"loop {}: {}".format(number + 1, error_text(e)))
        if loop.IsOpen():
            raise ItemError(u"loop {} is open".format(number + 1))
        loops.append(loop)
        repaired_total += repaired
    validation = getattr(DB, "BoundaryValidation", None)
    if validation is not None:
        try:
            valid = validation.IsValidHorizontalBoundary(_net_list(DB.CurveLoop, loops))
        except Exception:
            valid = True  # the check itself is optional; Floor.Create still validates
        if not valid:
            raise ItemError(u"boundary is not a valid horizontal profile")
    return loops, repaired_total


def room_top(room):
    """(upper limit is the room's own level, lower offset m, unbounded height m)."""
    upper = _id_param(room, "ROOM_UPPER_LEVEL")
    own = upper is not None and element_id_value(upper) == element_id_value(room.LevelId)
    lower = _double_param(room, "ROOM_LOWER_OFFSET")
    try:
        unbounded = room.UnboundedHeight
    except Exception:
        unbounded = None
    return (own, to_meters(lower) if lower else 0.0,
            to_meters(unbounded) if unbounded else None)


def prepare(doc, data, kind):
    """Resolve everything before any transaction: (plans, skipped, element type, missing)."""
    info = KINDS[kind]
    api_class = getattr(DB, info["api_class"], None)
    if api_class is None or not hasattr(api_class, "Create"):
        raise RequestError("{}.Create is not available: this tool needs Revit 2022 or later"
                           .format(info["api_class"]))
    level_cache = {}
    rooms, skipped, missing = select_rooms(doc, data, level_cache)
    if len(rooms) > MAX_ITEMS:
        raise RequestError("{} rooms match; at most {} per call. Narrow with level or "
                           "room_numbers".format(len(rooms), MAX_ITEMS))
    element_type = resolve_type(doc, info, data.get(info["type_key"]))
    skip_existing = data.get("skip_existing", True) is not False
    # Exact call shape checked by test_shared_helpers_keep_the_signatures_used_here.
    hosts = collect_hosts(doc, info["category"], level_cache) if skip_existing and rooms else []
    positions = phase_positions(doc)
    options = DB.SpatialElementBoundaryOptions()
    options.SpatialElementBoundaryLocation = getattr(
        DB.SpatialElementBoundaryLocation, BOUNDARY_LOCATIONS[data.get("boundary_location",
                                                                       "finish")])
    plans = []
    for label, room in rooms:
        room_area = sqft_to_sqm(room.Area)
        phase_id = _id_param(room, "ROOM_PHASE")
        if hosts:
            level_key = element_id_value(room.LevelId)
            level = doc.GetElement(room.LevelId)
            z = (level.ProjectElevation if level is not None else 0.0) + 1.0
            phase_position = _position(positions, phase_id)
            in_room = [host for host in _hosts_in_room(room, hosts, level_key, z)
                       if host_exists_in_phase(doc, host, positions, phase_position)]
            existing = covering_hosts(in_room, room_area)
            if existing:
                skipped.append(dict(label, reason=info["existing"],
                                    existing_ids=[h["element_id"] for h in existing]))
                continue
        plan = {"label": label, "room": room, "room_area_m2": room_area, "phase_id": phase_id}
        if kind == "floor":
            plan["offset_m"] = float(data.get("offset_m") or 0.0)
        else:
            own_level, lower_m, unbounded_m = room_top(room)
            height, source, error = ceiling_height(data.get("height_m"), own_level, lower_m,
                                                   unbounded_m)
            if error:
                skipped.append(dict(label, reason="no_ceiling_height", detail=error))
                continue
            plan["height_m"], plan["height_source"] = height, source
        try:
            plan["loops"], plan["repaired"] = build_loops(room, options)
        except ItemError as e:
            skipped.append(dict(label, reason="invalid_boundary", detail=e.text))
            continue
        plans.append((len(plans), plan))
    return plans, skipped, element_type, missing


def _assign_phase(element, phase_id):
    """Put the new element in the room's phase. Returns a note when it could not."""
    if phase_id is None:
        return None
    try:
        if element_id_value(element.CreatedPhaseId) == element_id_value(phase_id):
            return None
        if element.ArePhasesModifiable() and element.IsPhaseCreatedValid(phase_id):
            element.CreatedPhaseId = phase_id
            return None
    except Exception as e:
        return u"phase not set: {}".format(error_text(e))
    return u"phase not set: Revit does not allow the room's phase for this element"


def create_from_rooms(doc, data, kind, plans, element_type, commit):
    info = KINDS[kind]
    api_class = getattr(DB, info["api_class"])
    type_name = _type_name(element_type)

    def build(doc, index, plan):
        room = plan["room"]
        element = api_class.Create(doc, _net_list(DB.CurveLoop, plan["loops"]),
                                   element_type.Id, room.LevelId)
        offset_m = plan["offset_m"] if kind == "floor" else plan["height_m"]
        parameter = element.get_Parameter(getattr(DB.BuiltInParameter, info["offset_param"]))
        if parameter is None or parameter.IsReadOnly:
            raise ItemError(u"the new {} has no editable height offset".format(kind))
        if parameter.Set(to_feet(offset_m)) is False:
            raise ItemError(u"Revit refused the height offset {} m on the new {}".format(
                offset_m, kind))
        phase_note = _assign_phase(element, plan["phase_id"])
        result = dict(plan["label"])
        result.update({"index": index, "element_id": element_id_value(element.Id),
                       "type": type_name, "loops": len(plan["loops"]),
                       "room_area_m2": round(plan["room_area_m2"], 2)})
        if kind == "floor":
            result["offset_m"] = round(offset_m, 3)
        else:
            result["height_m"] = round(offset_m, 3)
            result["height_source"] = plan["height_source"]
        if plan["repaired"]:
            result["repaired_segments"] = plan["repaired"]
        if phase_note:
            result["phase_note"] = phase_note

        def after(result):
            try:
                area = element.get_Parameter(DB.BuiltInParameter.HOST_AREA_COMPUTED).AsDouble()
            except Exception:
                area = None
            if not area or area <= 0:
                return u"the new {} has no area".format(kind)
            result["area_m2"] = round(sqft_to_sqm(area), 2)
            return None

        return result, after

    return run_batch(doc, info["transaction"], plans, build, commit, info["noun"])


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _finish_route(doc, request, kind):
    """Shared flow after the route authenticated the request."""
    info = KINDS[kind]
    try:
        if not doc:
            return routes.make_response(data={"error": "No active Revit document"}, status=503)
        data = _parse_body(request) or {}
        error = validate_request(data, kind)
        if error:
            return routes.make_response(data={"error": error}, status=400)
        dry_run = data.get("dry_run", True) is not False
        try:
            plans, skipped, element_type, missing = prepare(doc, data, kind)
        except RequestError as e:
            body = {"error": e.text}
            body.update(e.extra)
            return routes.make_response(data=body, status=400)

        extra = {"type": _type_name(element_type),
                 "boundary_location": data.get("boundary_location", "finish"),
                 "skipped": skipped}
        if missing:
            extra["numbers_not_found"] = missing
        if not plans:
            result = {"status": "nothing_to_create", "created": 0, "results": [],
                      "message": u"No {} to create: every selected room was skipped "
                                 u"(see skipped).".format(info["noun"])}
            result.update(extra)
            return routes.make_response(data=result)

        expected = data.get("expected_count")
        if expected is not None and expected != len(plans):
            body = {"error": u"The dry run planned {} {} but {} would be created now; run the "
                             u"dry run again".format(expected, info["noun"], len(plans)),
                    "expected_count": expected, "count": len(plans)}
            body.update(extra)
            return routes.make_response(data=body, status=409)
        if not dry_run:
            # What the approval dialog shows: the rooms that will get an element.
            preview = {"_count": len(plans),
                       "_rooms": preview_rooms([plan["label"] for _, plan in plans])}
            if isinstance(request.data, dict):
                request.data.update(preview)
            else:
                request.data = dict(data, **preview)
            denial = authorize_request(request, action=info["action"])
            if denial is not None:
                return denial
        result = create_from_rooms(doc, data, kind, plans, element_type, not dry_run)
        labels = dict((index, plan["label"]) for index, plan in plans)
        for item in result.get("errors") or []:
            if item.get("index") in labels:
                item.update(labels[item["index"]])
        if result.get("status") in ("dry_run", "success"):
            result["total_area_m2"] = round(
                sum(r.get("area_m2") or 0.0 for r in result.get("results", [])), 2)
            result["message"] = summary_message(
                result["status"], len(result.get("results", [])), len(skipped), info["noun"])
        result.update(extra)
        return routes.make_response(data=result)
    except Exception as e:
        logger.error("Error in create %s from rooms route: %s", info["noun"], e)
        return routes.make_response(data={"error": error_text(e)}, status=500)


def register_finish_element_routes(api):
    """Register floor/ceiling-from-room routes with the API"""

    @api.route("/create_floors_from_rooms/", methods=["POST"])
    def create_floors_from_rooms_route(doc, request):
        """
        One floor per room on the room boundary (all or nothing for the rooms not skipped).

        Expected JSON payload:
        {
            "dry_run": true, "level": "Térreo", "room_numbers": ["101"],
            "floor_type": "Piso cerâmico 1cm", "offset_m": 0.0,
            "boundary_location": "finish", "skip_existing": true
        }
        """
        # Authenticate first; the review dialog is shown later only for real writes.
        denial = authorize_request(request, action=None)
        if denial is not None:
            return denial
        return _finish_route(doc, request, "floor")

    @api.route("/create_ceilings_from_rooms/", methods=["POST"])
    def create_ceilings_from_rooms_route(doc, request):
        """
        One ceiling per room on the room boundary (all or nothing for the rooms not skipped).

        Expected JSON payload:
        {
            "dry_run": true, "room_ids": [12345], "ceiling_type": "Forro gesso",
            "height_m": 2.6, "boundary_location": "finish", "skip_existing": true
        }
        """
        # Authenticate first; the review dialog is shown later only for real writes.
        denial = authorize_request(request, action=None)
        if denial is not None:
            return denial
        return _finish_route(doc, request, "ceiling")

    logger.info("Finish element routes registered successfully")
