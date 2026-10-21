# -*- coding: utf-8 -*-
"""Template audit tool (read-only inventory of the open project or template)"""

import re
from typing import Dict, List, Optional

from mcp.server.fastmcp import Context
from .utils import format_response

DEFAULT_LIMIT = 100
MAX_LIMIT = 1000
AUDIT_SECTIONS = (
    "browser_organization", "levels_grids", "phases", "view_templates", "filters",
    "views", "line_styles", "annotation_types", "families", "shared_parameters",
    "project_parameters", "materials", "schedules", "sheets", "export_settings",
    "purge_preview",
)
NAMING_KINDS = ("views", "view_templates", "sheets", "sheet_numbers", "schedules",
                "materials", "families", "levels")


def _sections_error(sections):
    if not isinstance(sections, list) or not sections:
        return "sections must be a non-empty list"
    unknown = [str(name) for name in sections if name not in AUDIT_SECTIONS]
    if unknown:
        return "unknown sections: {}; valid: {}".format(
            ", ".join(unknown), ", ".join(AUDIT_SECTIONS))
    return None


def _naming_error(naming):
    if not isinstance(naming, dict) or not naming:
        return 'naming must be an object like {"views": "^[A-Z]{2} - .+$"}'
    for kind, pattern in naming.items():
        if kind not in NAMING_KINDS:
            return "naming: unknown kind '{}'; valid: {}".format(kind, ", ".join(NAMING_KINDS))
        if not isinstance(pattern, str) or not pattern.strip():
            return "naming: the pattern for '{}' must be a non-empty text".format(kind)
        try:
            re.compile(pattern)
        except re.error as e:
            return "naming: invalid regex for '{}': {}".format(kind, e)
    return None


def register_audit_tools(mcp, revit_get, revit_post):
    """Register the template audit tool with the MCP server."""

    @mcp.tool()
    async def audit_template(
        sections: Optional[List[str]] = None,
        limit: int = DEFAULT_LIMIT,
        naming: Optional[Dict[str, str]] = None,
        ctx: Context = None,
    ) -> str:
        """
        Audit the open Revit project or template (.rte) for the office's
        template checklist. Read-only: nothing is changed or deleted.

        Returns JSON with one entry per section, each with count, unused_count,
        items (capped by limit, unused items first) and truncated:
        browser_organization, levels_grids (elevation in m, plan views),
        phases (order, phase filters and their presentation), view_templates
        (views using each), filters (views/templates using each), views (type,
        scale, template, on sheet), line_styles (weight, colour, pattern, line
        and fill patterns), annotation_types (dimension, text, tag/symbol
        types), families (instances per family, by category),
        shared_parameters (GUID, bound categories), project_parameters,
        materials, schedules, sheets (title block per sheet, title block
        usage), export_settings (DWG/PDF/print setups) and purge_preview (what
        Revit's Purge Unused would remove, plus unused templates and filters).
        "checklist" maps the 19 checklist items to sections and says what must
        be checked by eye ("conferir a olho").

        "unused" comes from Revit's Purge Unused list when available (Revit
        2024+), otherwise from placed instance counts; each section states its
        unused_rule. Large models can take minutes: after about 4 minutes the
        remaining sections are skipped (listed in section_errors and
        time_budget.skipped_sections); call again with only those sections.

        Args:
            sections: Sections to include (default: all). Valid names are listed above.
            limit: Maximum items per list (1-1000, default 100).
            naming: Optional naming rules, a regex per kind that must match the
                whole name, e.g. {"views": "^(PB|CT|EL)-.+$", "sheet_numbers": "^A\\d{2}$"}.
                Kinds: views, view_templates, sheets, sheet_numbers, schedules,
                materials, families, levels. Violations are listed under "naming".
        """
        if sections is not None:
            error = _sections_error(sections)
            if error:
                return "Error: " + error
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
            return "Error: limit must be between 1 and {}".format(MAX_LIMIT)
        if naming is not None:
            error = _naming_error(naming)
            if error:
                return "Error: " + error

        payload = {"limit": limit}
        if sections is not None:
            payload["sections"] = sections
        if naming is not None:
            payload["naming"] = naming
        if ctx:
            await ctx.info("Auditing the Revit template...")
        response = await revit_post("/audit_template/", payload, ctx, timeout=300.0)
        return format_response(response)
