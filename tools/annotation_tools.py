# -*- coding: utf-8 -*-
"""Annotation tools (tagging)"""

from mcp.server.fastmcp import Context
from typing import Optional
from .utils import format_response

MAX_LIMIT = 1000


def register_annotation_tools(mcp, revit_get, revit_post):
    """Register annotation tools with the MCP server."""

    @mcp.tool()
    async def tag_elements(
        category: str,
        view_name: Optional[str] = None,
        view_id: Optional[int] = None,
        tag_type: Optional[str] = None,
        add_leader: bool = False,
        skip_tagged: bool = True,
        limit: int = 200,
        dry_run: bool = True,
        ctx: Context = None,
    ) -> str:
        """
        Tag every element of a category in a Revit view (doors, windows,
        rooms, walls, furniture...), in one step and one undo.

        Workflow: call with dry_run=true (the default) first. It creates the
        tags inside a transaction that is rolled back, so the counts are real
        but the model is not changed. Show the counts to the user, then call
        again with dry_run=false; the real run asks for approval in Revit once.

        The category accepts the Revit-language name ("Portas"), the English
        name ("Doors") or the API name ("OST_Doors"). Rooms get room tags and
        areas get area tags (area plans only). Elements already tagged in the
        view are skipped by default; unplaced or not enclosed rooms are always
        skipped. Elements Revit refuses to tag are listed in "failed"; the
        others are still tagged.

        Response: tagged, skipped (counts by reason), failed_count, failed,
        truncated (more elements than limit), tag_type used and tag_ids.

        Args:
            category: Category to tag, e.g. "Doors", "Portas", "Rooms".
            view_name: View to tag in. Default: the active view.
            view_id: View id (alternative to view_name).
            tag_type: Tag type name, "Type" or "Family: Type". Default: the
                category's default tag type, else the first loaded one.
            add_leader: Draw a leader line (not used for room/area tags).
            skip_tagged: Skip elements that already have a tag in the view.
            limit: Maximum number of elements to tag (1-1000, default 200).
            dry_run: Count and test only (default true). Use false to tag.
        """
        if not category or not category.strip():
            return "Error: category is required"
        if not 1 <= limit <= MAX_LIMIT:
            return "Error: limit must be between 1 and {}".format(MAX_LIMIT)
        if view_name is not None and view_id is not None:
            return "Error: pass view_name or view_id, not both"

        data = {"category": category, "add_leader": add_leader,
                "skip_tagged": skip_tagged, "limit": limit, "dry_run": dry_run}
        if view_name:
            data["view_name"] = view_name
        if view_id is not None:
            data["view_id"] = view_id
        if tag_type:
            data["tag_type"] = tag_type
        if ctx:
            await ctx.info("{} {} tags...".format(
                "Testing" if dry_run else "Creating", category))
        response = await revit_post("/tag_elements/", data, ctx, timeout=120.0)
        return format_response(response)
