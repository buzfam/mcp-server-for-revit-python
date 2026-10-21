# -*- coding: utf-8 -*-
"""Sheet checklist before issuing (check_sheets)"""

import re
from mcp.server.fastmcp import Context
from typing import Any, Dict, List, Optional
from .utils import format_response

MAX_RULES = 50
MAX_SHEET_FILTER = 500
MAX_LIMIT = 1000
MAX_UNPLACED_LIMIT = 200

RULE_KEYS = ("match", "name", "required_views", "required_schedules", "required_legends",
             "allowed_scales", "title_block_params", "require_revision",
             "max_viewports", "min_viewports", "manual")
MATCH_KEYS = ("number_prefix", "number_regex", "name_regex")
VIEW_SPEC_KEYS = ("view_type", "name_regex", "min")
PLACEABLE_VIEW_TYPES = ("FloorPlan", "CeilingPlan", "Elevation", "ThreeD", "DraftingView",
                        "Legend", "EngineeringPlan", "AreaPlan", "Section", "Detail",
                        "Walkthrough", "Rendering")


def _text(value):
    return isinstance(value, str) and value.strip() != ""


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _clean(value):
    """Drop None values from nested dicts so the route only sees what was given."""
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def _regex_error(pattern):
    try:
        re.compile(pattern)
    except re.error as error:
        return "invalid regex '{}': {}".format(pattern, error)
    return None


def _text_list_error(value, key, regex):
    if not isinstance(value, list) or not value:
        return "{} must be a non-empty list of texts".format(key)
    for index, item in enumerate(value):
        if not _text(item):
            return "{}[{}] must be a non-empty text".format(key, index)
        if regex and _regex_error(item):
            return "{}[{}]: {}".format(key, index, _regex_error(item))
    return None


def _view_spec_error(spec):
    if not isinstance(spec, dict):
        return "must be an object"
    unknown = sorted(set(spec) - set(VIEW_SPEC_KEYS))
    if unknown:
        return "unknown keys {}; use {}".format(", ".join(unknown), ", ".join(VIEW_SPEC_KEYS))
    if spec.get("view_type") is None and spec.get("name_regex") is None:
        return "give view_type and/or name_regex"
    view_type = spec.get("view_type")
    if view_type is not None and (not isinstance(view_type, str) or view_type.lower()
                                  not in [t.lower() for t in PLACEABLE_VIEW_TYPES]):
        return "view_type must be one of {}".format(", ".join(PLACEABLE_VIEW_TYPES))
    if spec.get("name_regex") is not None:
        if not _text(spec["name_regex"]):
            return "name_regex must be a non-empty text"
        if _regex_error(spec["name_regex"]):
            return _regex_error(spec["name_regex"])
    if spec.get("min") is not None and (not _integer(spec["min"]) or spec["min"] < 1):
        return "min must be an integer >= 1"
    return None


def _rule_error(rule):
    """Same shape rules as revit_mcp/sheet_checks.validate_rule."""
    if not isinstance(rule, dict):
        return "must be an object"
    unknown = sorted(set(rule) - set(RULE_KEYS))
    if unknown:
        return "unknown keys {}; use {}".format(", ".join(unknown), ", ".join(RULE_KEYS))
    match = rule.get("match")
    if not isinstance(match, dict) or not match:
        return "match must be an object with {}".format(" and/or ".join(MATCH_KEYS))
    unknown = sorted(set(match) - set(MATCH_KEYS))
    if unknown:
        return "match: unknown keys {}".format(", ".join(unknown))
    for key, value in match.items():
        if not _text(value):
            return "match.{} must be a non-empty text".format(key)
        if key != "number_prefix" and _regex_error(value):
            return "match.{}: {}".format(key, _regex_error(value))
    if rule.get("name") is not None and not _text(rule["name"]):
        return "name must be a non-empty text"
    views = rule.get("required_views")
    if views is not None:
        if not isinstance(views, list) or not views:
            return "required_views must be a non-empty list"
        for index, spec in enumerate(views):
            error = _view_spec_error(spec)
            if error:
                return "required_views[{}]: {}".format(index, error)
    for key, regex in (("required_schedules", True), ("required_legends", True),
                       ("title_block_params", False), ("manual", False)):
        if rule.get(key) is not None:
            error = _text_list_error(rule[key], key, regex)
            if error:
                return error
    scales = rule.get("allowed_scales")
    if scales is not None and (not isinstance(scales, list) or not scales or
                               not all(_integer(s) and s > 0 for s in scales)):
        return "allowed_scales must be a non-empty list of positive integers (50 = 1:50)"
    if rule.get("require_revision") is not None and not isinstance(rule["require_revision"], bool):
        return "require_revision must be true or false"
    for key in ("min_viewports", "max_viewports"):
        if rule.get(key) is not None and (not _integer(rule[key]) or rule[key] < 0):
            return "{} must be an integer >= 0".format(key)
    if rule.get("min_viewports") is not None and rule.get("max_viewports") is not None \
            and rule["min_viewports"] > rule["max_viewports"]:
        return "min_viewports is greater than max_viewports"
    return None


def register_sheet_check_tools(mcp, revit_get, revit_post):
    """Register the sheet checklist tool with the MCP server."""

    @mcp.tool()
    async def check_sheets(
        checklists: Optional[List[Dict[str, Any]]] = None,
        sheets: Optional[List[str]] = None,
        generic: bool = True,
        only_problems: bool = False,
        limit: int = 200,
        unplaced_limit: int = 50,
        unplaced_include_schedules: bool = False,
        ctx: Context = None,
    ) -> str:
        """
        Check every sheet against the checklist of its sheet type before issuing,
        and report what is missing ("faltando X na folha A-03"). Read-only.

        Each sheet takes the FIRST checklist whose "match" fits it. A checklist:
          {"name": "Planta de layout",
           "match": {"number_prefix": "A-1", "number_regex": "...", "name_regex": "layout"},
           "required_views": [{"view_type": "FloorPlan", "name_regex": "layout", "min": 1}],
           "required_schedules": ["esquadrias"], "required_legends": ["legenda.*layout"],
           "allowed_scales": [50, 100],
           "title_block_params": ["Desenhado por", "SHEET_ISSUE_DATE"],
           "require_revision": true, "min_viewports": 1, "max_viewports": 4,
           "manual": ["cotas conferidas", "notas gerais atualizadas"]}
        All keys except "match" are optional; every key given in "match" must fit
        (prefix is case-insensitive; regexes are case-insensitive searches, use
        ^ and $ to anchor). view_type is a Revit ViewType name: FloorPlan,
        CeilingPlan, Section, Elevation, Detail, DraftingView, Legend, ThreeD,
        AreaPlan, EngineeringPlan. title_block_params are looked up on the sheet,
        then its title block, then Project Information, by the name shown in
        Revit (Portuguese) or by BuiltInParameter name, and must be non-empty.

        Generic checks (generic=true): sheet without views or schedules, sheet
        without title block, viewport whose plan/section/elevation/3D shows no
        model element (approximate), and views not placed on any sheet (count by
        type plus up to unplaced_limit names). Placeholder sheets are skipped.

        Each sheet returns status "missing" (something required is absent),
        "review" (scale out of the list, empty viewport, viewport count, or
        manual items that only a person can verify: "manual_checks", conferir
        a olho) or "ok", with problems [{check, detail}].

        Args:
            checklists: 0-50 checklists as above (the office's own checklists).
            sheets: Only these sheet numbers (default: all sheets).
            generic: Run the generic checks (default true).
            only_problems: Leave "ok" sheets out of the list (still counted).
            limit: At most this many sheets checked, by sheet number (1-1000, default 200).
            unplaced_limit: Views not on sheets listed by name (0-200, default 50).
            unplaced_include_schedules: Also report schedules not on any sheet.
        """
        rules = checklists or []
        if len(rules) > MAX_RULES:
            return "Error: at most {} checklists, got {}".format(MAX_RULES, len(rules))
        for index, rule in enumerate(rules):
            error = _rule_error(_clean(rule) if isinstance(rule, dict) else rule)
            if error:
                return "Error: checklist {}: {}".format(index, error)
        if sheets is not None:
            if not sheets:
                return "Error: sheets must contain at least one sheet number"
            if len(sheets) > MAX_SHEET_FILTER:
                return "Error: at most {} sheet numbers, got {}".format(MAX_SHEET_FILTER, len(sheets))
            if not all(_text(number) for number in sheets):
                return "Error: each sheet number must be a non-empty text"
        if not 1 <= limit <= MAX_LIMIT:
            return "Error: limit must be between 1 and {}".format(MAX_LIMIT)
        if not 0 <= unplaced_limit <= MAX_UNPLACED_LIMIT:
            return "Error: unplaced_limit must be between 0 and {}".format(MAX_UNPLACED_LIMIT)
        if not rules and not generic:
            return "Error: nothing to check; give checklists or keep generic=true"

        data: Dict[str, Any] = {"generic": generic, "only_problems": only_problems,
                                "limit": limit, "unplaced_limit": unplaced_limit,
                                "unplaced_include_schedules": unplaced_include_schedules}
        if rules:
            data["checklists"] = [_clean(rule) for rule in rules]
        if sheets is not None:
            data["sheets"] = sheets
        if ctx:
            await ctx.info("Checking sheets against {} checklist(s)...".format(len(rules)))
        response = await revit_post("/check_sheets/", data, ctx, timeout=120.0)
        return format_response(response)
