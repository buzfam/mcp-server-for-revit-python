# -*- coding: UTF-8 -*-
"""
Modeling tools: walls, rooms and hosted families (doors/windows)
Each route creates a batch of elements in one all-or-nothing transaction.
dry_run (the default) runs the same transaction and rolls it back, so Revit
itself checks the batch without changing the model.

Coordinates arrive in meters, relative to the project internal origin
(X east, Y north in an unrotated plan); Revit works in feet internally.
"""

from pyrevit import routes, DB
import json
import logging
import math
from .security import authorize_request
from .utils import normalize_string, element_id_from_int, element_id_value
from .transactions import Batch, failure_collector

logger = logging.getLogger(__name__)

MAX_ITEMS = 200
FEET_PER_METER = 1.0 / 0.3048
SQM_PER_SQFT = 0.09290304
DEFAULT_WALL_HEIGHT_M = 2.8
MIN_WALL_LENGTH_M = 0.01
MIN_ROOM_AREA_M2 = 0.01
MAX_WALL_DISTANCE_M = 0.5
MAX_WARNINGS = 50
MAX_LISTED_NAMES = 50
# WALL_KEY_REF_PARAM values (Revit "Location Line").
LOCATION_LINES = {
    "wall_centerline": 0,
    "core_centerline": 1,
    "finish_exterior": 2,
    "finish_interior": 3,
    "core_exterior": 4,
    "core_interior": 5,
}
# Keys that only make sense when the batch is committed.
ID_KEYS = ("element_id", "tag_id", "separation_line_ids")
NO_DOOR_WINDOW_FAMILIES = (
    "No door or window families are loaded in this model. Load one via "
    "Insert > Load Family (e.g. from the Revit library: Doors / Windows) and try again.")


class ItemError(Exception):
    """A problem with one batch item; reported with its index."""

    def __init__(self, text):
        Exception.__init__(self, text)
        self.text = text


# ---------------------------------------------------------------------------
# Pure helpers (no Revit API; checked under IronPython by scripts/ironpython_check.py)
# ---------------------------------------------------------------------------

def to_feet(meters):
    return float(meters) * FEET_PER_METER


def to_meters(feet):
    return float(feet) / FEET_PER_METER


def sqft_to_sqm(square_feet):
    return float(square_feet) * SQM_PER_SQFT


def is_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, long, float)):
        return False
    return not (math.isnan(value) or math.isinf(value))


def is_point(value):
    """True for [x, y]: a list/tuple of two finite numbers."""
    return (isinstance(value, (list, tuple)) and len(value) == 2
            and is_number(value[0]) and is_number(value[1]))


def same_name(a, b):
    """Case- and whitespace-insensitive name comparison (user-supplied names)."""
    if a is None or b is None:
        return False
    return u" ".join(normalize_string(a).lower().split()) == \
        u" ".join(normalize_string(b).lower().split())


def find_named(named, wanted):
    """First object whose name matches. named: list of (name, obj)."""
    for name, obj in named:
        if same_name(name, wanted):
            return obj
    return None


def distance(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def polygon_area(points):
    """Signed area (shoelace); positive when counter-clockwise."""
    total = 0.0
    count = len(points)
    for i in range(count):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % count]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def polygon_centroid(points):
    """Area centroid of a simple polygon; vertex average when degenerate."""
    area = polygon_area(points)
    count = len(points)
    if abs(area) < 1e-12:
        return (sum(p[0] for p in points) / float(count),
                sum(p[1] for p in points) / float(count))
    cx = cy = 0.0
    for i in range(count):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % count]
        cross = x1 * y2 - x2 * y1
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    return (cx / (6.0 * area), cy / (6.0 * area))


def point_in_polygon(point, points):
    """Ray casting; points on the edge may go either way."""
    x, y = point
    inside = False
    count = len(points)
    for i in range(count):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % count]
        if (y1 > y) != (y2 > y):
            cross_x = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < cross_x:
                inside = not inside
    return inside


def polygon_edges(points):
    """Closed list of (start, end) edges."""
    return [(points[i], points[(i + 1) % len(points)]) for i in range(len(points))]


def point_segment_distance(point, a, b):
    """Distance from point to segment ab and the 0..1 parameter of the closest point."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return distance(point, a), 0.0
    t = ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length_sq
    t = max(0.0, min(1.0, t))
    closest = (a[0] + t * dx, a[1] + t * dy)
    return distance(point, closest), t


def keys_within(point, segments, max_distance):
    """Distinct keys (first-seen order) of the segments [(key, a, b)] whose
    distance to point is <= max_distance."""
    keys = []
    for key, a, b in segments:
        gap, _ = point_segment_distance(point, a, b)
        if gap <= max_distance and key not in keys:
            keys.append(key)
    return keys


def nearest_segment(point, segments, max_distance):
    """Closest (key, distance) among segments [(key, a, b)] within max_distance, else None.

    A curved wall is passed as several segments with the same key.
    """
    best = None
    for key, a, b in segments:
        gap, _ = point_segment_distance(point, a, b)
        if gap <= max_distance and (best is None or gap < best[1]):
            best = (key, gap)
    return best


def merged(item, defaults, keys):
    """Item values with batch-wide defaults for the keys the item leaves out."""
    result = dict(item)
    for key in keys:
        if result.get(key) is None and defaults.get(key) is not None:
            result[key] = defaults[key]
    return result


def wall_settings(item, defaults):
    """Effective wall item. An item's own height_m or top_level wins over both
    batch-wide values, so a batch top_level does not override an item height."""
    result = merged(item, defaults,
                    ("level", "wall_type", "structural", "location_line", "join"))
    if item.get("height_m") is None and item.get("top_level") is None:
        if defaults.get("top_level") is not None:
            result["top_level"] = defaults["top_level"]
        else:
            result["height_m"] = defaults.get("height_m", DEFAULT_WALL_HEIGHT_M)
    if result.get("top_level") is None and result.get("height_m") is None:
        result["height_m"] = DEFAULT_WALL_HEIGHT_M
    return result


def _optional_name(item, key):
    value = item.get(key)
    if value is not None and (not isinstance(value, basestring) or not value.strip()):
        return "{} must be a non-empty name".format(key)
    return None


def _optional_bool(item, key):
    value = item.get(key)
    if value is not None and not isinstance(value, bool):
        return "{} must be true or false".format(key)
    return None


def validate_wall(item):
    """Shape check of one wall (after defaults). Error message or None."""
    if not isinstance(item, dict):
        return "Each wall must be an object"
    if not is_point(item.get("start")) or not is_point(item.get("end")):
        return "start and end must be [x, y] in meters"
    if distance(item["start"], item["end"]) < MIN_WALL_LENGTH_M:
        return "Wall is shorter than {} m".format(MIN_WALL_LENGTH_M)
    height = item.get("height_m")
    if height is not None and (not is_number(height) or height <= 0):
        return "height_m must be a positive number"
    for key in ("level", "top_level", "wall_type"):
        error = _optional_name(item, key)
        if error:
            return error
    for key in ("structural", "join"):
        error = _optional_bool(item, key)
        if error:
            return error
    line = item.get("location_line")
    if line is not None and line not in LOCATION_LINES:
        return "location_line must be one of: {}".format(", ".join(sorted(LOCATION_LINES)))
    return None


def validate_room(item):
    """Shape check of one room. Error message or None."""
    if not isinstance(item, dict):
        return "Each room must be an object"
    point = item.get("point")
    boundary = item.get("boundary")
    if point is None and boundary is None:
        return "Each room needs point [x, y] or boundary [[x, y], ...]"
    if point is not None and not is_point(point):
        return "point must be [x, y] in meters"
    if boundary is not None:
        if not isinstance(boundary, list) or len(boundary) < 3:
            return "boundary must be a list of at least 3 points"
        if not all(is_point(p) for p in boundary):
            return "boundary points must be [x, y] in meters"
        for a, b in polygon_edges(boundary):
            if distance(a, b) < MIN_WALL_LENGTH_M:
                return "boundary has repeated or too close points"
        if abs(polygon_area(boundary)) < MIN_ROOM_AREA_M2:
            return "boundary has no area"
        if point is not None and not point_in_polygon(point, boundary):
            return "point must be inside boundary"
        if point is None and not point_in_polygon(polygon_centroid(boundary), boundary):
            return "boundary centroid is outside the polygon (concave shape); add point inside it"
    for key in ("name", "level"):
        error = _optional_name(item, key)
        if error:
            return error
    number = item.get("number")
    if number is not None and not (isinstance(number, basestring) and number.strip()) \
            and not (is_number(number) and int(number) == number):
        return "number must be text or a whole number"
    return _optional_bool(item, "tag")


def validate_hosted(item):
    """Shape check of one hosted family instance. Error message or None."""
    if not isinstance(item, dict):
        return "Each instance must be an object"
    if not isinstance(item.get("family"), basestring) or not item["family"].strip():
        return "family must be a non-empty name"
    for key in ("type", "level"):
        error = _optional_name(item, key)
        if error:
            return error
    wall_id = item.get("wall_id")
    if wall_id is not None and (isinstance(wall_id, bool) or not isinstance(wall_id, (int, long))):
        return "wall_id must be an integer"
    point = item.get("point")
    if point is not None and not is_point(point):
        return "point must be [x, y] in meters"
    offset = item.get("offset_m")
    if offset is not None and (not is_number(offset) or offset < 0):
        return "offset_m must be a number >= 0"
    if wall_id is None and point is None:
        return "Each instance needs wall_id or point"
    if wall_id is not None and point is None and offset is None:
        return "With wall_id, give offset_m (along the wall) or point"
    sill = item.get("sill_height_m")
    if sill is not None and not is_number(sill):
        return "sill_height_m must be a number"
    for key in ("flip_hand", "flip_facing"):
        error = _optional_bool(item, key)
        if error:
            return error
    return None


def match_symbol(entries, family, type_name=None):
    """Pick a family type. entries: [(family_name, type_name, obj)]. Returns (obj, error)."""
    in_family = [e for e in entries if same_name(e[0], family)]
    if not in_family:
        return None, "Family '{}' is not loaded".format(family)
    if type_name is None:
        return sorted(in_family, key=lambda e: normalize_string(e[1]).lower())[0][2], None
    for entry in in_family:
        if same_name(entry[1], type_name):
            return entry[2], None
    return None, "Type '{}' not found in family '{}'. Types: {}".format(
        type_name, family, ", ".join(sorted(normalize_string(e[1]) for e in in_family)))


def shape_errors(items, validate, noun):
    """400-level checks shared by the routes. Returns (message, errors) or None."""
    if not isinstance(items, list) or not items:
        return "{} must be a non-empty list".format(noun), []
    if len(items) > MAX_ITEMS:
        return "At most {} {} per call, got {}".format(MAX_ITEMS, noun, len(items)), []
    errors = []
    for index, item in enumerate(items):
        error = validate(item)
        if error:
            errors.append({"index": index, "error": error})
    if errors:
        return "Invalid {}".format(noun), errors
    return None


# ---------------------------------------------------------------------------
# Revit helpers
# ---------------------------------------------------------------------------

def element_name(element):
    """Element name; Element.Name raises AttributeError on some elements in IronPython."""
    try:
        return normalize_string(DB.Element.Name.GetValue(element))
    except Exception:
        pass
    try:
        return normalize_string(element.Name)
    except Exception:
        return u""


def error_text(error):
    """Exception message as unicode (Revit messages can be accented)."""
    message = getattr(error, "Message", None)
    if message:
        return normalize_string(message)
    try:
        return normalize_string(unicode(error))
    except Exception:
        return u"{}".format(type(error).__name__)


def _parameter_text(element, builtin):
    try:
        parameter = element.get_Parameter(builtin)
        value = parameter.AsString() if parameter is not None else None
        return normalize_string(value) if value is not None else None
    except Exception:
        return None


def _collect(doc, cls=None, category=None):
    collector = DB.FilteredElementCollector(doc)
    if category is not None:
        collector = collector.OfCategory(category)
    if cls is not None:
        collector = collector.OfClass(cls)
    return list(collector.ToElements())


def level_z(level):
    """Z of a level in internal coordinates (feet), for geometry.

    Level.Elevation is measured from the Elevation Base (Project Base Point or
    Survey Point); ProjectElevation is always from the internal origin. Elevation
    is still fine for ordering levels (same offset for all)."""
    return level.ProjectElevation


def levels_by_elevation(doc):
    return sorted(_collect(doc, DB.Level), key=lambda level: level.Elevation)


def resolve_level(levels, name):
    """Level by name (case-insensitive), or the lowest level when name is None."""
    if not levels:
        raise ItemError("The model has no levels")
    if name is None:
        return levels[0]
    level = find_named([(element_name(l), l) for l in levels], name)
    if level is None:
        raise ItemError("Level '{}' not found. Levels: {}".format(
            name, ", ".join(element_name(l) for l in levels[:MAX_LISTED_NAMES])))
    return level


def _wall_type_name(wall_type):
    return _parameter_text(wall_type, DB.BuiltInParameter.SYMBOL_NAME_PARAM) or \
        element_name(wall_type)


def resolve_wall_type(doc, wall_types, name):
    if name is None:
        type_id = doc.GetDefaultElementTypeId(DB.ElementTypeGroup.WallType)
        wall_type = doc.GetElement(type_id) if type_id is not None else None
        if wall_type is None:
            raise ItemError("The model has no default wall type; pass wall_type")
        return wall_type
    wall_type = find_named([(_wall_type_name(t), t) for t in wall_types], name)
    if wall_type is None:
        raise ItemError("Wall type '{}' not found. Wall types: {}".format(
            name, ", ".join(sorted(_wall_type_name(t) for t in wall_types)[:MAX_LISTED_NAMES])))
    return wall_type


def plan_view_of(view, level):
    """True when view is a non-template floor plan (not ceiling, area or
    structural plan) generated by level."""
    try:
        if view is None or view.IsTemplate or not isinstance(view, DB.ViewPlan):
            return False
        if view.ViewType != DB.ViewType.FloorPlan:
            return False
        gen_level = view.GenLevel
        return gen_level is not None and \
            element_id_value(gen_level.Id) == element_id_value(level.Id)
    except Exception:
        return False


def find_plan_view(doc, level):
    """Active view when it is a floor plan of level, else the first floor plan of level."""
    if plan_view_of(doc.ActiveView, level):
        return doc.ActiveView
    for view in _collect(doc, DB.ViewPlan):
        if plan_view_of(view, level):
            return view
    return None


def _symbol_names(symbol):
    family = _parameter_text(symbol, DB.BuiltInParameter.SYMBOL_FAMILY_NAME_PARAM)
    if not family:
        try:
            family = normalize_string(symbol.FamilyName)
        except Exception:
            family = element_name(symbol.Family)
    type_name = _parameter_text(symbol, DB.BuiltInParameter.SYMBOL_NAME_PARAM) or \
        element_name(symbol)
    return family, type_name


def symbol_entries(doc, category=None):
    entries = []
    for symbol in _collect(doc, DB.FamilySymbol, category):
        try:
            family, type_name = _symbol_names(symbol)
            entries.append((family, type_name, symbol))
        except Exception:
            continue
    return entries


def wall_segments(walls):
    """[(wall, (x, y), (x, y))] in meters from wall location curves (curves tessellated)."""
    segments = []
    for wall in walls:
        try:
            curve = wall.Location.Curve
            points = [(to_meters(p.X), to_meters(p.Y)) for p in curve.Tessellate()]
        except Exception:
            continue
        for a, b in zip(points, points[1:]):
            segments.append((wall, a, b))
    return segments


# Kept importable from this module (revit_mcp.phases uses it).
_failure_collector = failure_collector


def run_batch(doc, name, plans, build, commit, noun, prepare=None):
    """Build every plan inside one transaction, all or nothing.

    prepare(doc, index, plan), when given, runs for every plan first and its
    work is committed (Batch.checkpoint) before the builds, in the same group.

    plans: [(index, plan)]. build(doc, index, plan) returns (result, after); after
    is None or a function(result) called after regeneration that returns an error
    message or None. A TransactionGroup wraps the transaction so a dry run still
    goes through Revit's failure processing on Commit and is then rolled back;
    a real run is assimilated into a single undo step.
    """
    batch = Batch(doc, name)
    collector = batch.collector
    results = []
    errors = []
    batch.start()
    try:
        if prepare is not None:
            for index, plan in plans:
                try:
                    prepare(doc, index, plan)
                except ItemError as e:
                    errors.append({"index": index, "error": e.text})
                except Exception as e:
                    errors.append({"index": index, "error": error_text(e)})
            if errors:
                batch.rollback()
                return {"status": "error",
                        "error": "{} of {} {} failed in Revit; nothing was created".format(
                            len(errors), len(plans), noun),
                        "created": 0, "errors": errors,
                        "warnings": collector.warnings[:MAX_WARNINGS]}
            if not batch.checkpoint():
                return {"status": "error",
                        "error": "Revit did not commit the {} ({}); nothing was created".format(
                            noun, batch.status),
                        "created": 0, "errors": [{"error": e} for e in collector.errors],
                        "warnings": collector.warnings[:MAX_WARNINGS]}
        checks = []
        for index, plan in plans:
            try:
                result, after = build(doc, index, plan)
                results.append(result)
                if after is not None:
                    checks.append((index, result, after))
            except ItemError as e:
                errors.append({"index": index, "error": e.text})
            except Exception as e:
                errors.append({"index": index, "error": error_text(e)})
        if not errors:
            doc.Regenerate()
            for index, result, after in checks:
                error = after(result)
                if error:
                    errors.append({"index": index, "error": error})

        if errors:
            batch.rollback()
            return {"status": "error",
                    "error": "{} of {} {} failed in Revit; nothing was created".format(
                        len(errors), len(plans), noun),
                    "created": 0, "errors": errors, "warnings": collector.warnings[:MAX_WARNINGS]}

        if not batch.finish(commit):
            return {"status": "error",
                    "error": "Revit did not commit the {} ({}); nothing was created".format(
                        noun, batch.status),
                    "created": 0, "errors": [{"error": e} for e in collector.errors],
                    "warnings": collector.warnings[:MAX_WARNINGS]}
    except Exception:
        batch.abort()
        raise

    warnings = collector.warnings[:MAX_WARNINGS]
    if not commit:
        for result in results:
            for key in ID_KEYS:
                result.pop(key, None)
        return {"status": "dry_run",
                "message": "Dry run: Revit accepted all {} {}. Nothing was changed; call again "
                           "with dry_run=false to apply.".format(len(results), noun),
                "created": 0, "results": results, "warnings": warnings}
    return {"status": "success", "created": len(results),
            "element_ids": [r["element_id"] for r in results],
            "results": results, "warnings": warnings}


def _resolve_all(items, resolve):
    """Resolve each item before any transaction. Returns (plans, errors)."""
    plans = []
    errors = []
    for index, item in enumerate(items):
        try:
            plans.append((index, resolve(index, item)))
        except ItemError as e:
            errors.append({"index": index, "error": e.text})
    return plans, errors


def _invalid(errors, total, noun, extra=None):
    result = {"status": "error",
              "error": "{} of {} {} are invalid; nothing was created".format(
                  len(errors), total, noun),
              "created": 0, "errors": errors}
    if extra:
        result.update(extra)
    return result


# ---------------------------------------------------------------------------
# create_walls
# ---------------------------------------------------------------------------

def create_walls(doc, walls, defaults, commit):
    levels = levels_by_elevation(doc)
    wall_types = _collect(doc, DB.WallType)

    def resolve(index, raw):
        item = wall_settings(raw, defaults)
        level = resolve_level(levels, item.get("level"))
        top_level = None
        if item.get("top_level") is not None:
            top_level = resolve_level(levels, item["top_level"])
            if top_level.Elevation <= level.Elevation:
                raise ItemError("top_level '{}' must be above level '{}'".format(
                    element_name(top_level), element_name(level)))
        return {"item": item, "level": level, "top_level": top_level,
                "wall_type": resolve_wall_type(doc, wall_types, item.get("wall_type"))}

    plans, errors = _resolve_all(walls, resolve)
    if errors:
        return _invalid(errors, len(walls), "walls")

    def build(doc, index, plan):
        item, level = plan["item"], plan["level"]
        z = level_z(level)
        start = DB.XYZ(to_feet(item["start"][0]), to_feet(item["start"][1]), z)
        end = DB.XYZ(to_feet(item["end"][0]), to_feet(item["end"][1]), z)
        line = DB.Line.CreateBound(start, end)
        height = to_feet(item.get("height_m") or DEFAULT_WALL_HEIGHT_M)
        wall = DB.Wall.Create(doc, line, plan["wall_type"].Id, level.Id, height, 0.0,
                              False, bool(item.get("structural", False)))
        if item.get("location_line") is not None:
            wall.get_Parameter(DB.BuiltInParameter.WALL_KEY_REF_PARAM).Set(
                LOCATION_LINES[item["location_line"]])
            # The wall stays put when the location line changes; move it back
            # so the chosen reference lies on the requested line.
            wall.Location.Curve = line
        if plan["top_level"] is not None:
            wall.get_Parameter(DB.BuiltInParameter.WALL_HEIGHT_TYPE).Set(plan["top_level"].Id)
        if item.get("join", True) is False:
            DB.WallUtils.DisallowWallJoinAtEnd(wall, 0)
            DB.WallUtils.DisallowWallJoinAtEnd(wall, 1)
        result = {"index": index, "element_id": element_id_value(wall.Id),
                  "length_m": round(distance(item["start"], item["end"]), 3),
                  "level": element_name(level),
                  "wall_type": _wall_type_name(plan["wall_type"])}
        if plan["top_level"] is not None:
            result["top_level"] = element_name(plan["top_level"])
        else:
            result["height_m"] = item.get("height_m")
        return result, None

    return run_batch(doc, "MCP: create walls", plans, build, commit, "walls")


# ---------------------------------------------------------------------------
# create_rooms
# ---------------------------------------------------------------------------

def _room_label(room):
    number = _parameter_text(room, DB.BuiltInParameter.ROOM_NUMBER) or u""
    name = _parameter_text(room, DB.BuiltInParameter.ROOM_NAME) or u""
    return u"{} {}".format(number, name).strip()


def _existing_room(doc, level, point):
    try:
        room = doc.GetRoomAtPoint(DB.XYZ(to_feet(point[0]), to_feet(point[1]),
                                         level_z(level) + 1.0))
    except Exception:
        return None
    return room


def create_rooms(doc, rooms, defaults, commit):
    levels = levels_by_elevation(doc)
    active_view = doc.ActiveView

    def resolve(index, raw):
        item = merged(raw, defaults, ROOM_DEFAULT_KEYS)
        level = resolve_level(levels, item.get("level"))
        point = item.get("point") or polygon_centroid(item["boundary"])
        existing = _existing_room(doc, level, point)
        if existing is not None:
            raise ItemError("Room '{}' (id {}) already occupies this point".format(
                _room_label(existing), element_id_value(existing.Id)))
        separation_view = None
        if item.get("boundary") is not None:
            separation_view = find_plan_view(doc, level)
            if separation_view is None:
                raise ItemError("No floor plan of level '{}' to draw room separation "
                                "lines in".format(element_name(level)))
        tag_view = None
        if item.get("tag", True) is not False and plan_view_of(active_view, level) \
                and room_tag_loaded:
            tag_view = active_view
        return {"item": item, "level": level, "point": point,
                "separation_view": separation_view, "tag_view": tag_view}

    # Without a room tag family Revit still creates typeless, invisible tags.
    room_tag_loaded = DB.FilteredElementCollector(doc) \
        .OfCategory(DB.BuiltInCategory.OST_RoomTags) \
        .WhereElementIsElementType().GetElementCount() > 0

    plans, errors = _resolve_all(rooms, resolve)
    if errors:
        return _invalid(errors, len(rooms), "rooms")

    def prepare(doc, index, plan):
        # Separation lines bound rooms only once their transaction commits, so
        # run_batch commits them (same group, same undo step) before the rooms.
        item, level = plan["item"], plan["level"]
        if item.get("boundary") is None:
            return
        z = level_z(level)
        plane = DB.Plane.CreateByNormalAndOrigin(DB.XYZ.BasisZ, DB.XYZ(0, 0, z))
        sketch = DB.SketchPlane.Create(doc, plane)
        curves = DB.CurveArray()
        for a, b in polygon_edges(item["boundary"]):
            curves.Append(DB.Line.CreateBound(DB.XYZ(to_feet(a[0]), to_feet(a[1]), z),
                                              DB.XYZ(to_feet(b[0]), to_feet(b[1]), z)))
        lines = doc.Create.NewRoomBoundaryLines(sketch, curves, plan["separation_view"])
        plan["separation_line_ids"] = [element_id_value(c.Id) for c in lines]

    def build(doc, index, plan):
        item, level, point = plan["item"], plan["level"], plan["point"]
        result = {"index": index, "level": element_name(level),
                  "point": [round(point[0], 3), round(point[1], 3)]}
        if plan.get("separation_line_ids") is not None:
            result["separation_line_ids"] = plan["separation_line_ids"]
        uv = DB.UV(to_feet(point[0]), to_feet(point[1]))
        room = doc.Create.NewRoom(level, uv)
        result["element_id"] = element_id_value(room.Id)
        if item.get("name") is not None:
            room.get_Parameter(DB.BuiltInParameter.ROOM_NAME).Set(normalize_string(item["name"]))
        if item.get("number") is not None:
            number = item["number"]
            if not isinstance(number, basestring):
                number = u"{}".format(int(number))
            room.get_Parameter(DB.BuiltInParameter.ROOM_NUMBER).Set(normalize_string(number))
        if plan["tag_view"] is not None:
            # A tag is a convenience: if Revit refuses it, the room is still created.
            try:
                tag = doc.Create.NewRoomTag(DB.LinkElementId(room.Id), uv, plan["tag_view"].Id)
                if tag is None:
                    raise ValueError("Revit returned no tag")
                result["tag_id"] = element_id_value(tag.Id)
            except Exception as e:
                result["tag"] = u"failed: {}".format(error_text(e))
        elif item.get("tag", True) is not False and not room_tag_loaded:
            result["tag"] = ("skipped: no room tag family is loaded "
                             "(Insert > Load Family > Annotations)")
        elif item.get("tag", True) is not False:
            result["tag"] = "skipped: the active view is not a floor plan of this level"

        def after(result):
            area = room.Area
            if not area or area <= 0:
                return "Room is not in an enclosed region (area 0); add walls or a boundary"
            result["area_m2"] = round(sqft_to_sqm(area), 2)
            result["name"] = _parameter_text(room, DB.BuiltInParameter.ROOM_NAME)
            result["number"] = _parameter_text(room, DB.BuiltInParameter.ROOM_NUMBER)
            return None

        return result, after

    needs_lines = any(plan["item"].get("boundary") is not None for _, plan in plans)
    return run_batch(doc, "MCP: create rooms", plans, build, commit, "rooms",
                     prepare=prepare if needs_lines else None)


# ---------------------------------------------------------------------------
# place_hosted_families
# ---------------------------------------------------------------------------

def _door_window_entries(doc):
    entries = []
    for category in (DB.BuiltInCategory.OST_Doors, DB.BuiltInCategory.OST_Windows):
        entries.extend(symbol_entries(doc, category))
    return entries


def _family_list(entries):
    return sorted(set(e[0] for e in entries))[:MAX_LISTED_NAMES]


def _wall_location(wall, item):
    """Point on the wall's location curve (XYZ) and offset from its start in meters."""
    curve = wall.Location.Curve
    length = curve.Length
    if item.get("offset_m") is not None:
        offset = to_feet(item["offset_m"])
        if offset > length + 1e-6:
            raise ItemError("offset_m {} is beyond the wall length {:.3f} m".format(
                item["offset_m"], to_meters(length)))
        return curve.Evaluate(offset / length if length else 0.0, True), item["offset_m"]
    z = curve.GetEndPoint(0).Z
    projection = curve.Project(DB.XYZ(to_feet(item["point"][0]), to_feet(item["point"][1]), z))
    if projection is None:
        raise ItemError("point cannot be projected onto the wall")
    if to_meters(projection.Distance) > MAX_WALL_DISTANCE_M:
        raise ItemError("point is {:.2f} m from the wall (max {} m)".format(
            to_meters(projection.Distance), MAX_WALL_DISTANCE_M))
    location = projection.XYZPoint
    start = curve.GetEndPoint(0)
    return location, round(to_meters(location.DistanceTo(start)), 3)


def place_hosted_families(doc, instances, commit):
    door_windows = _door_window_entries(doc)
    cache = {}

    def cached(key, load):
        if key not in cache:
            cache[key] = load()
        return cache[key]

    def resolve(index, item):
        symbol, error = match_symbol(door_windows, item["family"], item.get("type"))
        if symbol is None and error.endswith("is not loaded"):
            # Any other hosted family (e.g. a wall-based generic model).
            entries = cached("symbols", lambda: symbol_entries(doc))
            symbol, other_error = match_symbol(entries, item["family"], item.get("type"))
            if symbol is None and not other_error.endswith("is not loaded"):
                error = other_error
        if symbol is None:
            if not door_windows:
                raise ItemError("{}. {}".format(error, NO_DOOR_WINDOW_FAMILIES))
            raise ItemError("{}. Loaded door/window families: {}".format(
                error, ", ".join(_family_list(door_windows))))

        distance_m = None
        if item.get("wall_id") is not None:
            wall = doc.GetElement(element_id_from_int(int(item["wall_id"])))
            if wall is None or not isinstance(wall, DB.Wall):
                raise ItemError("wall_id {} is not a wall".format(item["wall_id"]))
        else:
            candidates = cached("walls", lambda: _collect(doc, DB.Wall))
            if item.get("level") is not None:
                level = resolve_level(cached("levels", lambda: levels_by_elevation(doc)),
                                      item["level"])
                candidates = [w for w in candidates
                              if element_id_value(w.LevelId) == element_id_value(level.Id)]
            segments = wall_segments(candidates)
            found = nearest_segment(item["point"], segments, MAX_WALL_DISTANCE_M)
            if found is None:
                raise ItemError("No wall within {} m of point {}".format(
                    MAX_WALL_DISTANCE_M, item["point"]))
            wall, distance_m = found
            if item.get("level") is None:
                # Walls stacked on several floors share the same plan position:
                # do not pick a floor arbitrarily.
                tied = keys_within(item["point"], segments, distance_m + 1e-6)
                level_ids = []
                for other in tied:
                    key = element_id_value(other.LevelId)
                    if key not in level_ids:
                        level_ids.append(key)
                if len(level_ids) > 1:
                    names = [element_name(doc.GetElement(element_id_from_int(key)))
                             for key in level_ids]
                    raise ItemError(u"Walls on {} levels ({}) are equally close to point {}; "
                                    u"pass level or wall_id".format(
                                        len(level_ids), u", ".join(names), item["point"]))
        location, offset_m = _wall_location(wall, item)
        family, type_name = _symbol_names(symbol)
        return {"item": item, "symbol": symbol, "wall": wall,
                "level": doc.GetElement(wall.LevelId), "location": location,
                "offset_m": offset_m, "family": family, "type": type_name,
                "distance_m": distance_m}

    plans, errors = _resolve_all(instances, resolve)
    if errors:
        extra = {"loaded_door_window_families": _family_list(door_windows)}
        if not door_windows:
            extra["hint"] = NO_DOOR_WINDOW_FAMILIES
        return _invalid(errors, len(instances), "instances", extra)

    def build(doc, index, plan):
        item, symbol = plan["item"], plan["symbol"]
        if not symbol.IsActive:
            symbol.Activate()
            doc.Regenerate()
        instance = doc.Create.NewFamilyInstance(
            plan["location"], symbol, plan["wall"], plan["level"],
            DB.Structure.StructuralType.NonStructural)
        if instance.Host is None:
            raise ItemError("Family '{}' is not wall-hosted".format(plan["family"]))
        if item.get("sill_height_m") is not None:
            sill = instance.get_Parameter(DB.BuiltInParameter.INSTANCE_SILL_HEIGHT_PARAM)
            if sill is None or sill.IsReadOnly:
                raise ItemError("This family has no editable sill height")
            sill.Set(to_feet(item["sill_height_m"]))
        if item.get("flip_hand"):
            if not instance.CanFlipHand:
                raise ItemError("This family cannot flip hand")
            instance.flipHand()
        if item.get("flip_facing"):
            if not instance.CanFlipFacing:
                raise ItemError("This family cannot flip facing")
            instance.flipFacing()
        result = {"index": index, "element_id": element_id_value(instance.Id),
                  "family": plan["family"], "type": plan["type"],
                  "wall_id": element_id_value(plan["wall"].Id),
                  "level": element_name(plan["level"]) if plan["level"] is not None else None,
                  "offset_m": plan["offset_m"],
                  "point": [round(to_meters(plan["location"].X), 3),
                            round(to_meters(plan["location"].Y), 3)]}
        if plan["distance_m"] is not None:
            result["distance_to_wall_m"] = round(plan["distance_m"], 3)
        if item.get("sill_height_m") is not None:
            result["sill_height_m"] = item["sill_height_m"]
            return result, None

        def after(result):
            # No sill_height_m: nothing is set, so the family keeps its default
            # sill. Report what Revit applied so a 0 sill is visible to the caller.
            try:
                sill = instance.get_Parameter(DB.BuiltInParameter.INSTANCE_SILL_HEIGHT_PARAM)
                if sill is not None:
                    result["sill_height_m"] = round(to_meters(sill.AsDouble()), 3)
            except Exception:
                pass
            return None

        return result, after

    return run_batch(doc, "MCP: place hosted families", plans, build, commit, "instances")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _payload(request):
    data = json.loads(request.data) if isinstance(request.data, str) else request.data
    return data or {}


def _batch_route(doc, request, key, noun, action, validator_for, run):
    """Shared flow after the route authenticated the request: validate shapes
    (400), ask for approval when not a dry run, then run the batch."""
    try:
        if not doc:
            return routes.make_response(data={"error": "No active Revit document"}, status=503)
        data = _payload(request)
        dry_run = data.get("dry_run", True) is not False
        items = data.get(key)
        problem = shape_errors(items, validator_for(data), noun)
        if problem is not None:
            message, errors = problem
            body = {"error": message}
            if errors:
                body["errors"] = errors
            return routes.make_response(data=body, status=400)
        if not dry_run:
            denial = authorize_request(request, action=action)
            if denial is not None:
                return denial
        return routes.make_response(data=run(data, items, not dry_run))
    except Exception as e:
        logger.error("Error in %s route: %s", noun, e)
        return routes.make_response(data={"error": error_text(e)}, status=500)


WALL_DEFAULT_KEYS = ("level", "top_level", "wall_type", "height_m", "structural",
                     "location_line", "join")
ROOM_DEFAULT_KEYS = ("level", "tag")


def batch_defaults(data, keys):
    return dict((key, data[key]) for key in keys if data.get(key) is not None)


def wall_validator(data):
    defaults = batch_defaults(data, WALL_DEFAULT_KEYS)
    return lambda item: validate_wall(
        wall_settings(item, defaults) if isinstance(item, dict) else item)


def room_validator(data):
    defaults = batch_defaults(data, ROOM_DEFAULT_KEYS)
    return lambda item: validate_room(
        merged(item, defaults, ROOM_DEFAULT_KEYS) if isinstance(item, dict) else item)


def register_modeling_routes(api):
    """Register modeling routes with the API"""

    @api.route("/create_walls/", methods=["POST"])
    def create_walls_route(doc, request):
        """
        Create walls from lines in meters (all or nothing).

        Expected JSON payload:
        {
            "dry_run": true,
            "level": "Level 1", "height_m": 2.8, "wall_type": "Generic - 200mm",
            "structural": false, "join": true, "location_line": "wall_centerline",
            "walls": [{"start": [0, 0], "end": [5, 0], "height_m": 3.0}]
        }
        """
        # Authenticate first; the review dialog is shown later only for real writes.
        denial = authorize_request(request, action=None)
        if denial is not None:
            return denial

        def run(data, items, commit):
            return create_walls(doc, items, batch_defaults(data, WALL_DEFAULT_KEYS), commit)

        return _batch_route(doc, request, "walls", "walls", "Create walls",
                            wall_validator, run)

    @api.route("/create_rooms/", methods=["POST"])
    def create_rooms_route(doc, request):
        """
        Create rooms at points or inside new separation-line polygons (all or nothing).

        Expected JSON payload:
        {
            "dry_run": true, "level": "Level 1", "tag": true,
            "rooms": [{"point": [2.5, 2.0], "name": "Sala", "number": "101"},
                      {"boundary": [[0, 0], [3, 0], [3, 3], [0, 3]], "name": "Hall"}]
        }
        """
        # Authenticate first; the review dialog is shown later only for real writes.
        denial = authorize_request(request, action=None)
        if denial is not None:
            return denial

        def run(data, items, commit):
            return create_rooms(doc, items, batch_defaults(data, ROOM_DEFAULT_KEYS), commit)

        return _batch_route(doc, request, "rooms", "rooms", "Create rooms",
                            room_validator, run)

    @api.route("/place_hosted_families/", methods=["POST"])
    def place_hosted_families_route(doc, request):
        """
        Place doors, windows or other wall-hosted families in walls (all or nothing).

        Expected JSON payload:
        {
            "dry_run": true,
            "instances": [{"family": "Porta", "type": "80x210", "wall_id": 123, "offset_m": 1.2},
                          {"family": "Janela", "point": [4.0, 0.0], "sill_height_m": 1.1}]
        }
        """
        # Authenticate first; the review dialog is shown later only for real writes.
        denial = authorize_request(request, action=None)
        if denial is not None:
            return denial

        def run(data, items, commit):
            return place_hosted_families(doc, items, commit)

        return _batch_route(doc, request, "instances", "instances",
                            "Place doors/windows in walls", lambda data: validate_hosted, run)

    logger.info("Modeling routes registered successfully")
