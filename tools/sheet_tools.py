# -*- coding: utf-8 -*-
"""Sheet set-up tools: create sheets, place views on sheets, apply view templates"""

from mcp.server.fastmcp import Context
from typing import Any, Dict, List, Optional, Union
from .utils import format_response

MAX_ITEMS = 200


def _text(value):
    return isinstance(value, str) and value.strip() != ""


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _sheet_error(index, sheet):
    if not isinstance(sheet, dict):
        return "sheet {} must be an object".format(index)
    for key in ("number", "name"):
        if not _text(sheet.get(key)):
            return "sheet {}: {} must be a non-empty text".format(index, key)
    return None


def _placement_error(index, item):
    if not isinstance(item, dict):
        return "placement {} must be an object".format(index)
    if not _text(item.get("sheet_number")):
        return "placement {}: sheet_number must be a non-empty text".format(index)
    view_id = item.get("view_id")
    if view_id is not None and (not isinstance(view_id, int) or isinstance(view_id, bool)):
        return "placement {}: view_id must be an integer".format(index)
    if _text(item.get("view")) == (view_id is not None):
        return "placement {}: give exactly one of view (name) or view_id".format(index)
    x, y = item.get("x"), item.get("y")
    if (x is None) != (y is None):
        return "placement {}: give both x and y, or neither".format(index)
    if x is not None and not (_number(x) and _number(y)):
        return "placement {}: x and y must be numbers in meters".format(index)
    return None


def _clean(item):
    """Drop keys whose value is None so the route only sees what was given."""
    return {key: value for key, value in item.items() if value is not None}


def _batch_error(items, noun, check):
    if not items:
        return "Error: {} must contain at least one item".format(noun)
    if len(items) > MAX_ITEMS:
        return "Error: at most {} {} per call, got {}".format(MAX_ITEMS, noun, len(items))
    for index, item in enumerate(items):
        error = check(index, item)
        if error:
            return "Error: " + error
    return None


def register_sheet_tools(mcp, revit_get, revit_post):
    """Register sheet set-up tools with the MCP server."""

    @mcp.tool()
    async def create_sheets(
        sheets: List[Dict[str, Any]],
        title_block: Optional[str] = None,
        dry_run: bool = True,
        ctx: Context = None,
    ) -> str:
        """
        Create many Revit sheets in one step, all or nothing.

        Call with dry_run=true (the default) first: it validates every sheet
        inside a transaction that is rolled back and reports what would be
        created, WITHOUT changing the model. Show that plan to the user, then
        call again with dry_run=false. The real run asks for approval in Revit
        once for the whole batch and is a single undo step.

        Sheet numbers must be unique: a number that already exists in the
        model or repeats in the batch (case-insensitive) makes the whole call
        fail with an "errors" list (index, number, reason).

        Args:
            sheets: 1-200 items like {"number": "A101", "name": "Planta Térreo"}.
            title_block: Title block as "Family : Type", or just the type or family
                name (case-insensitive). Default: the first loaded title block in
                alphabetical order; if none is loaded, sheets are created without one.
            dry_run: Validate and preview only (default true). Use false to create.
        """
        error = _batch_error(sheets, "sheets", _sheet_error)
        if error:
            return error
        if title_block is not None and not _text(title_block):
            return "Error: title_block must be a non-empty text"

        if ctx:
            await ctx.info("{} {} sheets...".format(
                "Validating" if dry_run else "Creating", len(sheets)))
        data = {"sheets": [{"number": s["number"], "name": s["name"]} for s in sheets],
                "dry_run": dry_run}
        if title_block is not None:
            data["title_block"] = title_block
        response = await revit_post("/create_sheets/", data, ctx, timeout=120.0)
        return format_response(response)

    @mcp.tool()
    async def place_views_on_sheets(
        placements: List[Dict[str, Any]],
        dry_run: bool = True,
        ctx: Context = None,
    ) -> str:
        """
        Place many views and schedules on sheets in one step, all or nothing.

        Call with dry_run=true (the default) first: it validates every
        placement (sheet exists, view exists, view not already on a sheet) and
        reports the positions, WITHOUT changing the model. Show that plan to
        the user, then call again with dry_run=false. The real run asks for
        approval in Revit once for the whole batch and is a single undo step.
        Revit warnings are returned in "warnings" instead of dialogs.

        A model view (plan, section, 3D...) can be on one sheet only; schedules
        and legends can go on several. Use list_revit_views to find view names;
        when two views share a name (e.g. floor plan and ceiling plan), pass
        view_id instead.

        Args:
            placements: 1-200 items like {"sheet_number": "A101", "view": "Térreo"}
                or {"sheet_number": "A101", "view_id": 123456, "x": 0.42, "y": 0.30}.
                x/y in meters from the sheet origin (both or neither): the centre
                of a view, but the TOP-LEFT corner of a schedule (Revit places
                schedules by that corner). Default: views at the centre of the
                title block; schedules at its top-left corner, 2 cm inside.
                Each result reports "anchor": "centre" or "top_left".
            dry_run: Validate and preview only (default true). Use false to place.
        """
        error = _batch_error(placements, "placements", _placement_error)
        if error:
            return error

        if ctx:
            await ctx.info("{} {} placements...".format(
                "Validating" if dry_run else "Placing", len(placements)))
        data = {"placements": [_clean(item) for item in placements], "dry_run": dry_run}
        response = await revit_post("/place_views_on_sheets/", data, ctx, timeout=120.0)
        return format_response(response)

    @mcp.tool()
    async def apply_view_template(
        template: str,
        views: Optional[List[Union[int, str]]] = None,
        view_type: Optional[str] = None,
        dry_run: bool = True,
        ctx: Context = None,
    ) -> str:
        """
        Apply one view template to many views in one step, all or nothing.

        Choose the views either by name/element id (views) or by type
        (view_type, e.g. "FloorPlan", "CeilingPlan", "Section", "Elevation",
        "ThreeD"; at most 200 matching views). Templates themselves and views
        that cannot take this template (different view type) are reported as
        errors and nothing is applied.

        Call with dry_run=true (the default) first: it reports the template
        name before and after for each view WITHOUT changing the model. Show
        that plan to the user, then call again with dry_run=false. The real run
        asks for approval in Revit once and is a single undo step.

        Args:
            template: View template name (case-insensitive).
            views: 1-200 view names or element ids. Give this or view_type.
            view_type: Revit ViewType name such as "FloorPlan". Give this or views.
            dry_run: Validate and preview only (default true). Use false to apply.
        """
        if not _text(template):
            return "Error: template must be a non-empty name"
        if (views is None) == (view_type is None):
            return "Error: give exactly one of views or view_type"
        data: Dict[str, Any] = {"template": template, "dry_run": dry_run}
        if views is not None:
            if not views:
                return "Error: views must contain at least one item"
            if len(views) > MAX_ITEMS:
                return "Error: at most {} views per call, got {}".format(MAX_ITEMS, len(views))
            for index, ref in enumerate(views):
                if isinstance(ref, bool) or not (isinstance(ref, int) or _text(ref)):
                    return "Error: view {} must be a name or an element id".format(index)
            data["views"] = views
        else:
            if not _text(view_type):
                return "Error: view_type must be a non-empty text such as FloorPlan"
            data["view_type"] = view_type

        if ctx:
            await ctx.info("{} view template '{}'...".format(
                "Validating" if dry_run else "Applying", template))
        response = await revit_post("/apply_view_template/", data, ctx, timeout=120.0)
        return format_response(response)
