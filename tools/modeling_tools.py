# -*- coding: utf-8 -*-
"""Modeling tools: walls, rooms and hosted families (doors/windows)"""

import math
from mcp.server.fastmcp import Context
from typing import Any, Dict, List, Optional
from .utils import format_response

MAX_ITEMS = 200
LOCATION_LINES = ("wall_centerline", "core_centerline", "finish_exterior",
                  "finish_interior", "core_exterior", "core_interior")


def _is_number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _is_point(value):
    return isinstance(value, (list, tuple)) and len(value) == 2 and all(
        _is_number(v) for v in value)


def _name_error(item, key):
    value = item.get(key)
    if value is not None and (not isinstance(value, str) or not value.strip()):
        return "{} must be a non-empty name".format(key)
    return None


def _bool_error(item, key):
    value = item.get(key)
    if value is not None and not isinstance(value, bool):
        return "{} must be true or false".format(key)
    return None


def _batch_error(items, noun):
    if not items:
        return "Error: {} must contain at least one item".format(noun)
    if len(items) > MAX_ITEMS:
        return "Error: at most {} {} per call, got {}".format(MAX_ITEMS, noun, len(items))
    return None


def _wall_error(index, wall):
    if not isinstance(wall, dict):
        return "wall {} must be an object".format(index)
    if not _is_point(wall.get("start")) or not _is_point(wall.get("end")):
        return "wall {}: start and end must be [x, y] in meters".format(index)
    if math.dist(wall["start"], wall["end"]) < 0.01:
        return "wall {}: shorter than 0.01 m".format(index)
    height = wall.get("height_m")
    if height is not None and (not _is_number(height) or height <= 0):
        return "wall {}: height_m must be a positive number".format(index)
    for key in ("level", "top_level", "wall_type"):
        error = _name_error(wall, key)
        if error:
            return "wall {}: {}".format(index, error)
    for key in ("structural", "join"):
        error = _bool_error(wall, key)
        if error:
            return "wall {}: {}".format(index, error)
    line = wall.get("location_line")
    if line is not None and line not in LOCATION_LINES:
        return "wall {}: location_line must be one of {}".format(index, ", ".join(LOCATION_LINES))
    return None


def _room_error(index, room):
    if not isinstance(room, dict):
        return "room {} must be an object".format(index)
    point, boundary = room.get("point"), room.get("boundary")
    if point is None and boundary is None:
        return "room {}: needs point [x, y] or boundary [[x, y], ...]".format(index)
    if point is not None and not _is_point(point):
        return "room {}: point must be [x, y] in meters".format(index)
    if boundary is not None and (not isinstance(boundary, list) or len(boundary) < 3
                                 or not all(_is_point(p) for p in boundary)):
        return "room {}: boundary must be a list of at least 3 [x, y] points".format(index)
    for key in ("name", "level"):
        error = _name_error(room, key)
        if error:
            return "room {}: {}".format(index, error)
    number = room.get("number")
    if number is not None and not (isinstance(number, str) and number.strip()) and not (
            isinstance(number, int) and not isinstance(number, bool)):
        return "room {}: number must be text or a whole number".format(index)
    error = _bool_error(room, "tag")
    return "room {}: {}".format(index, error) if error else None


def _instance_error(index, item):
    if not isinstance(item, dict):
        return "instance {} must be an object".format(index)
    if not isinstance(item.get("family"), str) or not item["family"].strip():
        return "instance {}: family must be a non-empty name".format(index)
    for key in ("type", "level"):
        error = _name_error(item, key)
        if error:
            return "instance {}: {}".format(index, error)
    wall_id, point, offset = item.get("wall_id"), item.get("point"), item.get("offset_m")
    if wall_id is not None and (not isinstance(wall_id, int) or isinstance(wall_id, bool)):
        return "instance {}: wall_id must be an integer".format(index)
    if point is not None and not _is_point(point):
        return "instance {}: point must be [x, y] in meters".format(index)
    if offset is not None and (not _is_number(offset) or offset < 0):
        return "instance {}: offset_m must be a number >= 0".format(index)
    if wall_id is None and point is None:
        return "instance {}: needs wall_id or point".format(index)
    if wall_id is not None and point is None and offset is None:
        return "instance {}: with wall_id, give offset_m or point".format(index)
    sill = item.get("sill_height_m")
    if sill is not None and not _is_number(sill):
        return "instance {}: sill_height_m must be a number".format(index)
    for key in ("flip_hand", "flip_facing"):
        error = _bool_error(item, key)
        if error:
            return "instance {}: {}".format(index, error)
    return None


def _first_error(items, check):
    for index, item in enumerate(items):
        error = check(index, item)
        if error:
            return "Error: " + error
    return None


def register_modeling_tools(mcp, revit_get, revit_post):
    """Register modeling tools with the MCP server."""

    @mcp.tool()
    async def create_walls(
        walls: List[Dict[str, Any]],
        level: Optional[str] = None,
        height_m: float = 2.8,
        top_level: Optional[str] = None,
        wall_type: Optional[str] = None,
        structural: bool = False,
        location_line: Optional[str] = None,
        join: bool = True,
        dry_run: bool = True,
        ctx: Context = None,
    ) -> str:
        """
        Create straight walls in Revit in one step, all or nothing.

        Coordinates are [x, y] in METERS in the Revit project's internal
        coordinates (internal origin; X points east/right and Y north/up in an
        unrotated plan). Heights are in meters too.

        Workflow: call list_levels first if you need a level other than the
        lowest one. Then call this tool with dry_run=true (the default): Revit
        creates the walls inside a transaction and rolls it back, so the result
        shows real problems (unknown type or level, Revit errors and warnings)
        WITHOUT changing the model. Show that plan to the user, and only then
        call again with dry_run=false. The real run asks for approval in Revit
        once for the whole batch and is a single undo step. It returns the new
        wall element_ids, which place_hosted_families accepts as wall_id.

        Each wall may override the batch-wide settings below with its own
        "level", "height_m", "top_level", "wall_type", "structural",
        "location_line" and "join". An item's own height_m or top_level wins
        over both batch-wide values.

        Args:
            walls: 1-200 items like {"start": [0, 0], "end": [5, 0]} (meters).
            level: Base level name (case-insensitive). Default: the lowest level.
            height_m: Unconnected height in meters (default 2.8); ignored with top_level.
            top_level: Level name for the wall top constraint, instead of height_m.
            wall_type: Wall type name as shown in Revit. Default: the model's default wall type.
            structural: Create structural walls (default false).
            location_line: Which wall reference lies on start-end: wall_centerline
                (Revit default), core_centerline, finish_exterior, finish_interior,
                core_exterior or core_interior.
            join: Let Revit join wall ends automatically (default true).
            dry_run: Validate and preview only (default true). Use false to apply.
        """
        error = _batch_error(walls, "walls") or _first_error(walls, _wall_error)
        if not error and (not _is_number(height_m) or height_m <= 0):
            error = "Error: height_m must be a positive number"
        if not error and location_line is not None and location_line not in LOCATION_LINES:
            error = "Error: location_line must be one of {}".format(", ".join(LOCATION_LINES))
        if error:
            return error

        if ctx:
            await ctx.info("{} {} walls...".format(
                "Validating" if dry_run else "Creating", len(walls)))
        data = {"walls": walls, "dry_run": dry_run, "height_m": height_m,
                "structural": structural, "join": join}
        for key, value in (("level", level), ("top_level", top_level),
                           ("wall_type", wall_type), ("location_line", location_line)):
            if value is not None:
                data[key] = value
        response = await revit_post("/create_walls/", data, ctx, timeout=120.0)
        return format_response(response)

    @mcp.tool()
    async def create_rooms(
        rooms: List[Dict[str, Any]],
        level: Optional[str] = None,
        tag: bool = True,
        dry_run: bool = True,
        ctx: Context = None,
    ) -> str:
        """
        Create Revit rooms in one step, all or nothing, and report their areas.

        Coordinates are [x, y] in METERS in the Revit project's internal
        coordinates (internal origin; X points east/right and Y north/up in an
        unrotated plan). Areas are returned in square meters (area_m2).

        Each room is either:
            - {"point": [x, y]}: placed in the region enclosed by walls (or
              room separation lines) around that point; or
            - {"boundary": [[x, y], ...]}: new room separation lines are drawn
              along the closed polygon (3+ points, do not repeat the first
              point) and the room is placed at its centroid, or at "point"
              when given (needed for concave shapes).
        Optional per room: "name", "number" (text), "level", "tag".

        A room whose point is not enclosed (area 0) or that falls where a room
        already exists is an error, and then nothing is created.

        Workflow: call with dry_run=true (the default): Revit creates the rooms
        inside a transaction and rolls it back, so the result shows the real
        area of each room WITHOUT changing the model. Show that plan to the
        user, and only then call again with dry_run=false. The real run asks
        for approval in Revit once for the whole batch and is a single undo step.

        Tags are placed only when the active view is a floor plan of the
        room's level; otherwise the result says the tag was skipped.

        Args:
            rooms: 1-200 items like {"point": [2.5, 2.0], "name": "Sala", "number": "101"}.
            level: Level name (case-insensitive) for rooms without their own. Default: lowest level.
            tag: Tag each room in the active view (default true).
            dry_run: Validate and preview only (default true). Use false to apply.
        """
        error = _batch_error(rooms, "rooms") or _first_error(rooms, _room_error)
        if error:
            return error

        if ctx:
            await ctx.info("{} {} rooms...".format(
                "Validating" if dry_run else "Creating", len(rooms)))
        data = {"rooms": rooms, "dry_run": dry_run, "tag": tag}
        if level is not None:
            data["level"] = level
        response = await revit_post("/create_rooms/", data, ctx, timeout=120.0)
        return format_response(response)

    @mcp.tool()
    async def place_hosted_families(
        instances: List[Dict[str, Any]],
        dry_run: bool = True,
        ctx: Context = None,
    ) -> str:
        """
        Place doors, windows or other wall-hosted families in existing walls, all or nothing.

        Coordinates are [x, y] in METERS in the Revit project's internal
        coordinates (internal origin; X points east/right and Y north/up in an
        unrotated plan). offset_m and sill_height_m are in meters too.

        Each instance needs "family" (family name, case-insensitive; "type" is
        optional, default the first type) and a position:
            - {"wall_id": 123, "offset_m": 1.2}: distance from the wall's start
              point along its location line; or
            - {"point": [x, y]}: the nearest wall whose location line is within
              0.5 m is used (optionally only walls on "level"), and the
              instance goes to the closest point on it; or
            - {"wall_id": 123, "point": [x, y]}: that wall, closest point.
        Optional: "sill_height_m" (windows), "flip_hand", "flip_facing".

        Use list_families with contains="door"/"porta" or "window"/"janela" to
        find loaded family and type names. If no door or window family is
        loaded, the error says so: load one in Revit via Insert > Load Family.

        Workflow: call with dry_run=true (the default): Revit places the
        instances inside a transaction and rolls it back, so the result shows
        real problems WITHOUT changing the model. Show that plan to the user,
        and only then call again with dry_run=false. The real run asks for
        approval in Revit once for the whole batch and is a single undo step.

        Args:
            instances: 1-200 items like {"family": "Porta", "type": "80x210", "wall_id": 123, "offset_m": 1.2}.
            dry_run: Validate and preview only (default true). Use false to apply.
        """
        error = _batch_error(instances, "instances") or _first_error(instances, _instance_error)
        if error:
            return error

        if ctx:
            await ctx.info("{} {} hosted instances...".format(
                "Validating" if dry_run else "Placing", len(instances)))
        data = {"instances": instances, "dry_run": dry_run}
        response = await revit_post("/place_hosted_families/", data, ctx, timeout=120.0)
        return format_response(response)
