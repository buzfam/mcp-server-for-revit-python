# -*- coding: utf-8 -*-
"""Finish floors and ceilings from room boundaries (spec 020)"""

import math
from mcp.server.fastmcp import Context
from typing import List, Optional
from .utils import format_response

MAX_ROOMS = 200
BOUNDARY_LOCATIONS = ("finish", "center")
MAX_FLOOR_OFFSET_M = 5.0
MAX_CEILING_HEIGHT_M = 10.0


def _is_number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _selection_payload(room_numbers, room_ids, level):
    """(payload, error) for the room selection shared by both tools."""
    if room_numbers is not None and room_ids is not None:
        return None, "Error: give room_numbers or room_ids, not both"
    if room_numbers is None and room_ids is None and level is None:
        return None, "Error: give room_numbers, room_ids or level to choose the rooms"
    data = {}
    if room_numbers is not None:
        if not room_numbers:
            return None, "Error: room_numbers must not be empty"
        if len(room_numbers) > MAX_ROOMS:
            return None, "Error: at most {} rooms per call, got {}".format(
                MAX_ROOMS, len(room_numbers))
        numbers = []
        for number in room_numbers:
            if isinstance(number, int) and not isinstance(number, bool):
                number = str(number)
            if not isinstance(number, str) or not number.strip():
                return None, "Error: room_numbers must be room numbers (text)"
            numbers.append(number)
        data["room_numbers"] = numbers
    if room_ids is not None:
        if not room_ids:
            return None, "Error: room_ids must not be empty"
        if len(room_ids) > MAX_ROOMS:
            return None, "Error: at most {} rooms per call, got {}".format(
                MAX_ROOMS, len(room_ids))
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in room_ids):
            return None, "Error: room_ids must be integers"
        data["room_ids"] = list(room_ids)
    if level is not None:
        if not isinstance(level, str) or not level.strip():
            return None, "Error: level must be a non-empty name"
        data["level"] = level
    return data, None


def _common_error(type_key, type_name, boundary_location, expected_count):
    if expected_count is not None and (not isinstance(expected_count, int)
                                       or isinstance(expected_count, bool) or expected_count < 0):
        return "Error: expected_count must be a whole number >= 0"
    if type_name is not None and (not isinstance(type_name, str) or not type_name.strip()):
        return "Error: {} must be a non-empty name".format(type_key)
    if boundary_location not in BOUNDARY_LOCATIONS:
        return "Error: boundary_location must be one of {}".format(", ".join(BOUNDARY_LOCATIONS))
    return None


def register_finish_element_tools(mcp, revit_get, revit_post):
    """Register floor/ceiling-from-room tools with the MCP server."""

    @mcp.tool()
    async def create_floors_from_rooms(
        room_numbers: Optional[List[str]] = None,
        room_ids: Optional[List[int]] = None,
        level: Optional[str] = None,
        floor_type: Optional[str] = None,
        offset_m: float = 0.0,
        boundary_location: str = "finish",
        skip_existing: bool = True,
        dry_run: bool = True,
        expected_count: Optional[int] = None,
        ctx: Context = None,
    ) -> str:
        """
        Create one finish floor per room, drawn on the room boundary, so
        room_finishes and quantities_by_rule measure modeled floors.

        Choose the rooms with room_numbers (optionally with level), room_ids,
        or level alone (every placed room on that level); at most 200 rooms.
        Rooms that cannot get a floor are listed in "skipped" with a reason:
        unplaced, not_enclosed, existing_floor (a floor whose centre lies in
        the room and is not much bigger than it; a slab under several rooms
        does not count), invalid_boundary, not_found, not_a_room, other_level.
        The remaining rooms are created all or nothing.

        Workflow: call with dry_run=true (the default): Revit creates the
        floors inside a transaction and rolls it back, so the result shows the
        real area of each floor (area_m2) and Revit warnings WITHOUT changing
        the model. Show that plan to the user, and only then call again with
        dry_run=false. The real run asks for approval in Revit once and is a
        single undo step. Pass the dry run's result count as expected_count so
        the real run is refused if the selection changed in between. Each
        floor is put in its room's phase ("phase"; "phase_note" if Revit did
        not allow it), and only floors that exist in that phase count as
        existing.

        Args:
            room_numbers: Room numbers (text), e.g. ["101", "102"].
            room_ids: Room element ids, instead of room_numbers.
            level: Level name (case-insensitive); alone, selects every placed room on it.
            floor_type: Floor type name as shown in Revit. Default: the model's default floor type.
            offset_m: Height offset from the room's level in meters (default 0, range -5..5).
            boundary_location: "finish" (wall finish faces, default) or "center" (wall centerlines).
            skip_existing: Skip rooms that already have a floor (default true).
            dry_run: Validate and preview only (default true). Use false to apply.
            expected_count: With dry_run=false, the number of elements the dry run
                planned; Revit refuses (409) if the model changed and the count differs.
        """
        data, error = _selection_payload(room_numbers, room_ids, level)
        error = error or _common_error("floor_type", floor_type, boundary_location, expected_count)
        if not error and (not _is_number(offset_m) or abs(offset_m) > MAX_FLOOR_OFFSET_M):
            error = "Error: offset_m must be a number between -{0} and {0}".format(
                MAX_FLOOR_OFFSET_M)
        if error:
            return error

        if ctx:
            await ctx.info("{} floors from rooms...".format(
                "Validating" if dry_run else "Creating"))
        data.update({"offset_m": offset_m, "boundary_location": boundary_location,
                     "skip_existing": skip_existing, "dry_run": dry_run})
        if floor_type is not None:
            data["floor_type"] = floor_type
        if expected_count is not None:
            data["expected_count"] = expected_count
        response = await revit_post("/create_floors_from_rooms/", data, ctx, timeout=120.0)
        return format_response(response)

    @mcp.tool()
    async def create_ceilings_from_rooms(
        room_numbers: Optional[List[str]] = None,
        room_ids: Optional[List[int]] = None,
        level: Optional[str] = None,
        ceiling_type: Optional[str] = None,
        height_m: Optional[float] = None,
        boundary_location: str = "finish",
        skip_existing: bool = True,
        dry_run: bool = True,
        expected_count: Optional[int] = None,
        ctx: Context = None,
    ) -> str:
        """
        Create one ceiling per room, drawn on the room boundary, so
        room_finishes and quantities_by_rule measure modeled ceilings (and
        use the ceiling height for wall areas).

        Choose the rooms with room_numbers (optionally with level), room_ids,
        or level alone (every placed room on that level); at most 200 rooms.
        Pass height_m (ceiling height above the room's level, e.g. 2.6). Without
        it each ceiling goes at the room top (only when the room's upper limit
        is its own level and the top is at most 6 m above it); otherwise the
        room is skipped with reason no_ceiling_height. Other skip reasons: unplaced, not_enclosed,
        existing_ceiling, invalid_boundary, not_found, not_a_room, other_level.
        The remaining rooms are created all or nothing.

        Workflow: call with dry_run=true (the default): Revit creates the
        ceilings inside a transaction and rolls it back, so the result shows
        the real area of each ceiling (area_m2) and Revit warnings WITHOUT
        changing the model. Show that plan to the user, and only then call
        again with dry_run=false. The real run asks for approval in Revit once
        and is a single undo step. Pass the dry run's result count as
        expected_count. Each ceiling is put in its room's phase.

        Args:
            room_numbers: Room numbers (text), e.g. ["101", "102"].
            room_ids: Room element ids, instead of room_numbers.
            level: Level name (case-insensitive); alone, selects every placed room on it.
            ceiling_type: Ceiling type name as shown in Revit. Default: the model's default ceiling type.
            height_m: Ceiling height above the room's level in meters (0..10). Recommended.
            boundary_location: "finish" (wall finish faces, default) or "center" (wall centerlines).
            skip_existing: Skip rooms that already have a ceiling (default true).
            dry_run: Validate and preview only (default true). Use false to apply.
            expected_count: With dry_run=false, the number of elements the dry run
                planned; Revit refuses (409) if the model changed and the count differs.
        """
        data, error = _selection_payload(room_numbers, room_ids, level)
        error = error or _common_error("ceiling_type", ceiling_type, boundary_location,
                                     expected_count)
        if not error and height_m is not None and (
                not _is_number(height_m) or height_m <= 0 or height_m > MAX_CEILING_HEIGHT_M):
            error = "Error: height_m must be a number above 0 and up to {}".format(
                MAX_CEILING_HEIGHT_M)
        if error:
            return error

        if ctx:
            await ctx.info("{} ceilings from rooms...".format(
                "Validating" if dry_run else "Creating"))
        data.update({"boundary_location": boundary_location,
                     "skip_existing": skip_existing, "dry_run": dry_run})
        if ceiling_type is not None:
            data["ceiling_type"] = ceiling_type
        if height_m is not None:
            data["height_m"] = height_m
        if expected_count is not None:
            data["expected_count"] = expected_count
        response = await revit_post("/create_ceilings_from_rooms/", data, ctx, timeout=120.0)
        return format_response(response)
