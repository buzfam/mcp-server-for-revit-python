# -*- coding: utf-8 -*-
"""Data export tools (schedules and rooms)"""

from mcp.server.fastmcp import Context
from typing import Optional
from .utils import format_response

MAX_LIMIT = 1000
FORMATS = ("json", "csv")


def _options_error(limit, format):
    if not 1 <= limit <= MAX_LIMIT:
        return "Error: limit must be between 1 and {}".format(MAX_LIMIT)
    if format not in FORMATS:
        return "Error: format must be 'json' or 'csv'"
    return None


def register_export_tools(mcp, revit_get, revit_post):
    """Register data export tools with the MCP server."""

    @mcp.tool()
    async def export_schedule(
        schedule_name: Optional[str] = None,
        schedule_id: Optional[int] = None,
        limit: int = 200,
        format: str = "json",
        ctx: Context = None,
    ) -> str:
        """
        Read the rows of a Revit schedule (quantity table) exactly as shown in
        Revit, as JSON rows or CSV text.

        Use list_revit_views to find schedule names (the "schedules" group).
        Values are the formatted cell texts, in the project's units. Blank
        grouping rows are dropped. If the schedule is not found, "available"
        lists schedule names.

        Response: columns + rows (format="json") or csv (format="csv"),
        returned, body_rows and truncated (more rows than limit).

        Args:
            schedule_name: Schedule name as shown in the Project Browser.
            schedule_id: Schedule view id (alternative to schedule_name).
            limit: Maximum number of rows (1-1000, default 200).
            format: "json" (default) or "csv".
        """
        if not schedule_name and schedule_id is None:
            return "Error: schedule_name or schedule_id is required"
        if schedule_name and schedule_id is not None:
            return "Error: pass schedule_name or schedule_id, not both"
        error = _options_error(limit, format)
        if error:
            return error

        data = {"limit": limit, "format": format}
        if schedule_name:
            data["schedule_name"] = schedule_name
        if schedule_id is not None:
            data["schedule_id"] = schedule_id
        if ctx:
            await ctx.info("Reading schedule...")
        response = await revit_post("/export_schedule/", data, ctx, timeout=60.0)
        return format_response(response)

    @mcp.tool()
    async def export_rooms(
        level: Optional[str] = None,
        include_unplaced: bool = True,
        include_details: bool = False,
        limit: int = 200,
        format: str = "json",
        ctx: Context = None,
    ) -> str:
        """
        List the rooms of the model with number, name, level, area (m²),
        perimeter (m), volume (m³, only when volume computation is on) and
        status, as JSON or CSV.

        status is "placed", "unplaced" (deleted from the plan but still in the
        model) or "not_enclosed" (placed but with zero area). counts and
        total_area_m2 summarize all matching rooms, not only the returned ones.

        Args:
            level: Only rooms on this level (name as shown in Revit).
            include_unplaced: Include unplaced and not enclosed rooms (default true).
            include_details: Also return department and comments.
            limit: Maximum number of rooms (1-1000, default 200).
            format: "json" (default) or "csv".
        """
        error = _options_error(limit, format)
        if error:
            return error

        data = {"include_unplaced": include_unplaced, "include_details": include_details,
                "limit": limit, "format": format}
        if level:
            data["level"] = level
        if ctx:
            await ctx.info("Reading rooms...")
        response = await revit_post("/export_rooms/", data, ctx, timeout=60.0)
        return format_response(response)
