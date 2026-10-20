# -*- coding: utf-8 -*-
"""Bulk parameter editing tools"""

from mcp.server.fastmcp import Context
from typing import Any, Dict, List
from .utils import format_response

MAX_CHANGES = 500


def _change_error(index, change):
    if not isinstance(change, dict):
        return "change {} must be an object".format(index)
    if not isinstance(change.get("element_id"), int) or isinstance(change.get("element_id"), bool):
        return "change {}: element_id must be an integer".format(index)
    if not isinstance(change.get("parameter"), str) or not change["parameter"].strip():
        return "change {}: parameter must be a non-empty name".format(index)
    if change.get("value") is None:
        return "change {}: value is required".format(index)
    return None


def register_parameter_tools(mcp, revit_get, revit_post):
    """Register bulk parameter tools with the MCP server."""

    @mcp.tool()
    async def bulk_set_parameters(
        changes: List[Dict[str, Any]],
        dry_run: bool = True,
        use_type: bool = False,
        ctx: Context = None,
    ) -> str:
        """
        Set parameter values on many Revit elements in one step, all or nothing.

        Workflow: call get_element_parameters first to learn exact parameter
        names, storage types and whether they are editable. Then call this
        tool with dry_run=true (the default): it validates every change and
        returns before/after values WITHOUT changing the model. Show that plan
        to the user, and only then call again with dry_run=false. The real run
        asks for approval in Revit once for the whole batch and is a single
        undo step.

        If any change is invalid or rejected, nothing is applied and "errors"
        lists the index, element and reason of each problem.

        Values:
            - Text parameters: any string.
            - Integer parameters: whole number; Yes/No parameters also accept
              true/false, yes/no, sim/não, 1/0.
            - Length, area and other numeric parameters: text in the project's
              units, e.g. "110 cm", "2.40", "3' 6\"". Prefer the same format
              as the parameter's "display" value.
            - Element reference (ElementId) parameters are not supported yet.

        Args:
            changes: 1-500 items like {"element_id": 123456, "parameter": "Mark", "value": "P01"}.
            dry_run: Validate and preview only (default true). Use false to apply.
            use_type: Apply to each element's type instead of the instance.
        """
        if not changes:
            return "Error: changes must contain at least one item"
        if len(changes) > MAX_CHANGES:
            return "Error: at most {} changes per call, got {}".format(MAX_CHANGES, len(changes))
        for index, change in enumerate(changes):
            error = _change_error(index, change)
            if error:
                return "Error: " + error

        if ctx:
            await ctx.info("{} {} parameter changes...".format(
                "Validating" if dry_run else "Applying", len(changes)))
        data = {"changes": changes, "dry_run": dry_run, "use_type": use_type}
        response = await revit_post("/bulk_set_parameters/", data, ctx, timeout=120.0)
        return format_response(response)
