# -*- coding: utf-8 -*-
"""Render material check tool (spec 014)"""

import re
from typing import List, Optional, Union

from mcp.server.fastmcp import Context
from .utils import format_response

MAX_LIMIT = 1000


def register_material_check_tools(mcp, revit_get, revit_post):
    """Register the render material check tool with the MCP server."""

    @mcp.tool()
    async def check_render_materials(
        categories: Optional[List[str]] = None,
        name_pattern: Optional[str] = None,
        max_families_per_material: int = 2,
        min_materials_per_family: int = 2,
        only_visible_in_view: Optional[Union[int, str]] = None,
        limit: int = 200,
        ctx: Context = None,
    ) -> str:
        """
        Check the materials of the objects that will be rendered (Lumion,
        Enscape...) before exporting, against the rule "one distinct,
        well-named material per finish, really applied to the geometry".
        Read-only.

        Rules (problem "rule" field):
        - name_pattern: material name does not match the pattern (default
          OBJETO_MATERIAL_PARTE_NN, e.g. POLTRONA_TECIDO_ASSENTO_01; accents
          are ignored, uppercase required).
        - duplicate_names: names equal once case/accents/spaces/underscores
          are ignored ("duplicate", error) or that only differ in the
          trailing number ("similar", review).
        - generic_shared: one material used by more than
          max_families_per_material different families.
        - single_material (review): a furniture-like family type with several
          geometry pieces but fewer than min_materials_per_family materials.
        - category_material_only: the geometry only gets the category
          material, so the finishes are not separated in the family.
        - no_material: no material at all (<By Category> and the category has
          none).
        Painted faces (Paint tool) count as materials and are listed in
        "painted".

        Materials are read per family type from one representative instance,
        including its nested shared families (their materials count for the
        parent; solids, meshes and nested families count as pieces). Types
        whose geometry is only meshes (imported DWG/SKP/OBJ-like) get a
        "note" and are counted in summary.mesh_only_types.
        Default categories: furniture, furniture systems, casework, generic
        models, plumbing fixtures, lighting fixtures, specialty equipment.

        Response: summary (count per rule), problems [{rule, severity
        "error"|"review", material, family, type, category, element_id,
        detail}], problems_total, truncated, types_checked, materials_checked.
        Use element_id with get_element_parameters to inspect a type.

        Args:
            categories: Category names to check ("Mobiliário", "Furniture",
                "OST_Casework"). Default: the furniture-like categories above.
            name_pattern: Regular expression for material names (matched
                after removing accents). Default: OBJETO_MATERIAL_PARTE_NN.
            max_families_per_material: Families allowed to share one
                material (default 2).
            min_materials_per_family: Materials expected per furniture type
                with 2+ geometry pieces (default 2; 1 disables the check).
            only_visible_in_view: Only elements visible in this view (name or
                id), e.g. the 3D view exported to the renderer.
            limit: Maximum number of problems returned (1-1000, default 200).
        """
        if not 1 <= limit <= MAX_LIMIT:
            return "Error: limit must be between 1 and {}".format(MAX_LIMIT)
        if max_families_per_material < 1:
            return "Error: max_families_per_material must be at least 1"
        if min_materials_per_family < 1:
            return "Error: min_materials_per_family must be at least 1"
        if categories is not None:
            categories = [c for c in categories if c and c.strip()]
            if not categories:
                return "Error: categories must list at least one category name"
        if name_pattern is not None:
            if not name_pattern.strip():
                return "Error: name_pattern must be a non-empty regular expression"
            try:
                re.compile(name_pattern)
            except re.error as e:
                return "Error: name_pattern is not a valid regular expression: {}".format(e)
        if isinstance(only_visible_in_view, str) and not only_visible_in_view.strip():
            only_visible_in_view = None

        data = {"max_families_per_material": max_families_per_material,
                "min_materials_per_family": min_materials_per_family,
                "limit": limit}
        if categories is not None:
            data["categories"] = categories
        if name_pattern is not None:
            data["name_pattern"] = name_pattern
        if only_visible_in_view is not None:
            data["only_visible_in_view"] = only_visible_in_view
        if ctx:
            await ctx.info("Checking render materials...")
        response = await revit_post("/check_render_materials/", data, ctx, timeout=120.0)
        return format_response(response)
