# -*- coding: utf-8 -*-
"""Renumbering tool (spec 018)"""

import re
from mcp.server.fastmcp import Context
from typing import List, Optional
from .utils import format_response

MAX_ITEMS = 200
ORDERS = ("location", "current", "id")
GROUPS = ("room", "level")
TOKENS = ("seq", "prefix", "level", "room_number", "room_name")
TOKEN_RE = re.compile(r"\{([^{}]*)\}")
SEQ_WIDTH_RE = re.compile(r"^seq:0[1-9]$")
SHEET_WORDS = ("sheets", "sheet", "folhas", "folha", "pranchas", "prancha", "ost_sheets")
ROOM_WORDS = ("rooms", "room", "ambientes", "ambiente", "salas", "ost_rooms")


def pattern_error(pattern):
    """Error message for a malformed pattern, else None."""
    if not isinstance(pattern, str) or not pattern.strip():
        return "pattern must be a non-empty text"
    if "{" in TOKEN_RE.sub("", pattern) or "}" in TOKEN_RE.sub("", pattern):
        return "pattern has an unmatched brace"
    names = [name.strip() for name in TOKEN_RE.findall(pattern)]
    for name in names:
        if name not in TOKENS and not SEQ_WIDTH_RE.match(name):
            return "unknown token {{{}}} in pattern; use {}".format(
                name, ", ".join("{" + t + "}" for t in TOKENS + ("seq:02",)))
    if not any(name == "seq" or SEQ_WIDTH_RE.match(name) for name in names):
        return "pattern must contain {seq} (or {seq:02}) so every number is different"
    return None


def register_renumber_tools(mcp, revit_get, revit_post):
    """Register the renumbering tool with the MCP server."""

    @mcp.tool()
    async def renumber_elements(
        target: str,
        pattern: str = "{prefix}{seq}",
        parameter: Optional[str] = None,
        prefix: Optional[str] = None,
        start: int = 1,
        step: int = 1,
        order: Optional[str] = None,
        group_by: Optional[str] = None,
        level: Optional[str] = None,
        view_name: Optional[str] = None,
        view_id: Optional[int] = None,
        element_ids: Optional[List[int]] = None,
        phase: Optional[str] = None,
        dry_run: bool = True,
        ctx: Context = None,
    ) -> str:
        """
        Renumber doors, windows, rooms, sheets or any category's Mark from a
        pattern, all or nothing, in one undo step.

        Examples:
            - Doors by room: target="doors", pattern="P-{room_number}-{seq:02}",
              group_by="room" -> P-101-01, P-101-02, P-102-01...
            - Windows per level: target="windows", pattern="J{seq:02}", group_by="level".
            - Rooms in sequence per level: target="rooms", pattern="{seq}",
              start=101, level="Pavimento 1".
            - Sheets: target="sheets", pattern="A-{seq}", start=101,
              element_ids=[...] keeps their current order (A-101..A-110).
            - Any category's Mark: target="Mobiliário" (or "Furniture"),
              pattern="M{seq:03}".

        Workflow: call with dry_run=true (the default). It checks every element
        and runs the changes in a transaction that is rolled back, and returns
        "results" with before/after values. Show that list to the user, then
        call again with dry_run=false; the real run asks for approval in Revit
        once.

        Pattern tokens: {seq} (the sequence; {seq:02} or {seq:03} pads with
        zeros), {prefix}, {level} (level name), {room_number}, {room_name}
        (for doors/windows: the room on the "to" side, else the "from" side;
        for rooms: the room itself). Phase used: the phase argument, else the
        view's phase, else the last phase (for doors/windows without a room
        there, also the element's creation phase); returned as "phase".
        The pattern must contain {seq}.

        Order: "location" (default; plan reading order: by level, then rows
        from top to bottom, left to right), "current" (natural order of the
        current values, "A-2" before "A-10"; default for sheets) or "id".
        group_by "room" or "level" restarts the sequence in each group.

        Sheet and room numbers must be unique (room numbers within the phase:
        only rooms of that phase are renumbered and compared): a new value
        already used outside the batch is an error, while swapping numbers
        inside the batch (A-101 <-> A-102) works. For Mark, repeats outside the
        batch are only warnings. Unplaced rooms, rooms of other phases and
        placeholder sheets are skipped; elements inside model groups are
        errors. At most 200 elements per call: filter by level, view or
        element_ids. "results" lists only the elements that change; the rest
        are counted in "unchanged".

        Args:
            target: "doors", "windows", "rooms", "sheets" or a category name
                ("Portas", "Furniture", "OST_Columns"...).
            pattern: Text with tokens, e.g. "P-{room_number}-{seq:02}". Default "{prefix}{seq}".
            parameter: Text parameter to write. Default: Mark (ALL_MODEL_MARK),
                ROOM_NUMBER for rooms, SHEET_NUMBER for sheets. Accepts the
                Revit-language name or the BuiltInParameter name.
            prefix: Value of {prefix}. Default empty.
            start: First sequence number (default 1).
            step: Increment (default 1).
            order: "location", "current" or "id".
            group_by: "room" (doors, windows, other instances) or "level".
            level: Only elements on this level.
            view_name: Only elements visible in this view (a plan, section,
                elevation or 3D view; its phase is used when phase is omitted).
            view_id: View id (alternative to view_name).
            element_ids: Only these elements (1-200).
            phase: Phase name for room lookup and for which rooms are renumbered.
            dry_run: Preview only (default true). Use false to apply.
        """
        if not isinstance(target, str) or not target.strip():
            return "Error: target is required (doors, windows, rooms, sheets or a category name)"
        error = pattern_error(pattern)
        if error:
            return "Error: " + error
        if start < 0:
            return "Error: start must be >= 0"
        if step < 1:
            return "Error: step must be >= 1"
        if order is not None and order not in ORDERS:
            return "Error: order must be one of: {}".format(", ".join(ORDERS))
        if group_by is not None and group_by not in GROUPS:
            return "Error: group_by must be room, level or omitted"
        if view_name is not None and view_id is not None:
            return "Error: pass view_name or view_id, not both"
        if element_ids is not None:
            if not element_ids:
                return "Error: element_ids must contain at least one id"
            if len(element_ids) > MAX_ITEMS:
                return "Error: at most {} element_ids, got {}".format(MAX_ITEMS, len(element_ids))
            if any(not isinstance(i, int) or isinstance(i, bool) for i in element_ids):
                return "Error: element_ids must be integers"
        kind = "".join(target.lower().split())
        if kind in SHEET_WORDS:
            if order == "location":
                return "Error: sheets have no plan location; use order current or id"
            if group_by is not None:
                return "Error: group_by is not available for sheets"
            if level is not None or view_name is not None or view_id is not None:
                return "Error: level and view filters are not available for sheets; use element_ids"
            if phase is not None:
                return "Error: phase is not available for sheets"
            if re.search(r"\{\s*(level|room_number|room_name)\s*\}", pattern):
                return "Error: sheets have no room or level; use {prefix} and {seq}"
        if kind in ROOM_WORDS and group_by == "room":
            return "Error: group_by room is for doors, windows and other elements; use level for rooms"

        data = {"target": target, "pattern": pattern, "start": start, "step": step,
                "dry_run": dry_run}
        optional = {"parameter": parameter, "prefix": prefix, "order": order,
                    "group_by": group_by, "level": level, "view_name": view_name,
                    "view_id": view_id, "element_ids": element_ids, "phase": phase}
        data.update((key, value) for key, value in optional.items() if value is not None)
        if ctx:
            await ctx.info("{} renumbering of {}...".format(
                "Previewing" if dry_run else "Applying", target))
        response = await revit_post("/renumber_elements/", data, ctx, timeout=120.0)
        return format_response(response)
