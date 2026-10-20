# -*- coding: utf-8 -*-
"""Selection and parameter inspection tools"""

from mcp.server.fastmcp import Context
from typing import List, Optional
from .utils import format_response

MAX_SELECTION_LIMIT = 1000
MAX_PARAMETER_ELEMENTS = 50


def register_selection_tools(mcp, revit_get, revit_post):
    """Register selection and parameter tools with the MCP server."""

    @mcp.tool()
    async def get_selected_elements(limit: int = 200, ctx: Context = None) -> str:
        """
        Get the elements the user currently has selected in Revit.

        Use this when the user refers to "these", "the selected elements" or
        "what I picked". Returns element_id, name, category, family, type,
        level and view_specific per element. An empty selection is not an
        error: total is 0.

        If truncated=true, more elements are selected than were returned;
        increase limit (max 1000) if needed.

        Args:
            limit: Maximum number of elements to describe (1-1000, default 200).
        """
        if not 1 <= limit <= MAX_SELECTION_LIMIT:
            return "Error: limit must be between 1 and {}".format(MAX_SELECTION_LIMIT)
        if ctx:
            await ctx.info("Reading current selection...")
        response = await revit_post("/selection/", {"limit": limit}, ctx)
        return format_response(response)

    @mcp.tool()
    async def get_element_parameters(
        element_ids: Optional[List[int]] = None,
        include_type: bool = False,
        name_filter: Optional[str] = None,
        ctx: Context = None,
    ) -> str:
        """
        Read the parameters of Revit elements, with values, units and whether
        each one can be edited.

        Each parameter has: name, value (raw; lengths in feet), display (as
        shown in Revit, in project units), storage (String, Integer, Double,
        ElementId), read_only, shared and builtin. Read "display" when talking
        to the user. IDs that do not exist are listed in not_found.

        Args:
            element_ids: Element IDs to read (max 50). If omitted, uses the
                current selection.
            include_type: Also return the parameters of each element's type.
            name_filter: Only parameters whose name contains this text
                (case-insensitive), e.g. "fire" or "Mark".
        """
        if element_ids and len(element_ids) > MAX_PARAMETER_ELEMENTS:
            return "Error: at most {} elements per call, got {}".format(
                MAX_PARAMETER_ELEMENTS, len(element_ids))
        data = {"include_type": include_type}
        if element_ids:
            data["element_ids"] = element_ids
        if name_filter:
            data["name_filter"] = name_filter
        if ctx:
            await ctx.info("Reading element parameters...")
        response = await revit_post("/element_parameters/", data, ctx)
        return format_response(response)
