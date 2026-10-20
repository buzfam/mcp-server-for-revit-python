# -*- coding: utf-8 -*-
"""Model health tools"""

from mcp.server.fastmcp import Context
from .utils import format_response

MAX_SAMPLE_IDS = 20


def register_health_tools(mcp, revit_get, revit_post):
    """Register model health tools with the MCP server."""

    @mcp.tool()
    async def model_health_report(sample_ids: int = 5, ctx: Context = None) -> str:
        """
        Diagnose why a Revit model is slow, heavy or messy, and where to act.

        Returns: warnings grouped by type (most frequent first, with sample
        element IDs), imported CAD (should usually be linked instead),
        in-place families by category, views not placed on sheets, unused view
        templates, Revit link status, model groups, design options, file size,
        and a "flags" list summarizing the problems found. Read-only.

        Use the sample element IDs with get_element_parameters to inspect the
        offending elements.

        Args:
            sample_ids: Element IDs to include per warning type / item (0-20, default 5).
        """
        if not 0 <= sample_ids <= MAX_SAMPLE_IDS:
            return "Error: sample_ids must be between 0 and {}".format(MAX_SAMPLE_IDS)
        if ctx:
            await ctx.info("Building model health report...")
        response = await revit_post("/model_health/", {"sample_ids": sample_ids}, ctx,
                                    timeout=120.0)
        return format_response(response)
