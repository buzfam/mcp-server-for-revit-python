# -*- coding: utf-8 -*-
"""Phase tools: phases report and batch phase assignment (spec 016)"""

from mcp.server.fastmcp import Context
from typing import Any, Dict, List, Optional, Union
from .utils import format_response

MAX_ITEMS = 200
MAX_LIMIT = 1000
SELECTOR_KEYS = ("category", "level", "view_id", "selection")


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _phase_error(value, key):
    if _is_int(value):
        return None if value > 0 else "{} must be a positive phase id".format(key)
    if isinstance(value, str) and value.strip():
        return None
    return "{} must be a phase name or id".format(key)


def _phases_error(created, demolished, where=""):
    if created is None and demolished is None:
        return "{}give created_phase and/or demolished_phase".format(where)
    for key, value in (("created_phase", created), ("demolished_phase", demolished)):
        if value is not None:
            error = _phase_error(value, key)
            if error:
                return where + error
    if isinstance(created, str) and created.strip().lower() in ("none", "nenhum", "nenhuma"):
        return where + "created_phase cannot be cleared"
    return None


def _change_error(index, change):
    if not isinstance(change, dict):
        return "change {} must be an object".format(index)
    if not _is_int(change.get("element_id")):
        return "change {}: element_id must be an integer".format(index)
    unknown = sorted(set(change) - {"element_id", "created_phase", "demolished_phase"})
    if unknown:
        return "change {}: unknown keys: {}".format(index, ", ".join(unknown))
    return _phases_error(change.get("created_phase"), change.get("demolished_phase"),
                         "change {}: ".format(index))


def _selector_error(selector):
    if not isinstance(selector, dict):
        return "selector must be an object"
    unknown = sorted(set(selector) - set(SELECTOR_KEYS))
    if unknown:
        return "unknown selector keys: {} (known: {})".format(
            ", ".join(unknown), ", ".join(SELECTOR_KEYS))
    for key in ("category", "level"):
        value = selector.get(key)
        if value is not None and not (isinstance(value, str) and value.strip()):
            return "selector.{} must be a non-empty name".format(key)
    if selector.get("view_id") is not None and not _is_int(selector["view_id"]):
        return "selector.view_id must be an integer"
    if selector.get("selection") is not None and not isinstance(selector["selection"], bool):
        return "selector.selection must be true or false"
    if not selector.get("category") and selector.get("selection") is not True:
        return "selector needs a category or selection=true"
    return None


def register_phase_tools(mcp, revit_get, revit_post):
    """Register phase tools with the MCP server."""

    @mcp.tool()
    async def phases_report(
        categories: Optional[List[str]] = None,
        by_category: bool = False,
        limit: int = 200,
        ctx: Context = None,
    ) -> str:
        """
        Report the project phases and how the model is organized by them,
        without changing anything. Use it before set_element_phases and to
        review a renovation plan (existing / demolish / new construction).

        Returns:
            phases: in project order (id, name, sequence).
            phase_filters: name and how each status is shown (new, existing,
                demolished, temporary): "by_category", "overridden" or
                "not_displayed".
            combinations: count of model elements per (created_phase,
                demolished_phase) pair, with status_in_last_phase (existing,
                demolished, new, temporary, past) as seen in a view of the
                last phase; with by_category, the top 10 categories of each.
            views: views grouped by (phase, phase_filter) with counts; view
                names listed up to `limit` in total (views_truncated).

        Args:
            categories: Only count these categories (name as shown in Revit,
                e.g. "Paredes", or English/API name "Walls", "OST_Walls").
            by_category: Break each combination down by category.
            limit: Maximum number of view names listed (1-1000, default 200).
        """
        if not 1 <= limit <= MAX_LIMIT:
            return "Error: limit must be between 1 and {}".format(MAX_LIMIT)
        if categories is not None and (
                not categories or not all(isinstance(c, str) and c.strip() for c in categories)):
            return "Error: categories must be a non-empty list of category names"

        data = {"by_category": by_category, "limit": limit}
        if categories:
            data["categories"] = categories
        if ctx:
            await ctx.info("Reading phases...")
        response = await revit_post("/phases_report/", data, ctx, timeout=120.0)
        return format_response(response)

    @mcp.tool()
    async def set_element_phases(
        changes: Optional[List[Dict[str, Any]]] = None,
        selector: Optional[Dict[str, Any]] = None,
        created_phase: Optional[Union[str, int]] = None,
        demolished_phase: Optional[Union[str, int]] = None,
        dry_run: bool = True,
        expected_count: Optional[int] = None,
        ctx: Context = None,
    ) -> str:
        """
        Set the created and/or demolished phase of many model elements, all or
        nothing (e.g. mark the surveyed building as existing and the walls to
        be removed as demolished in the new-construction phase).

        Workflow: call phases_report first to learn the phase names and their
        order. Then call this tool with dry_run=true (the default): Revit
        validates every element and the response lists before/after phases
        per element WITHOUT changing the model. Show that plan to the user and
        only then call again with dry_run=false; the real run asks for
        approval in Revit once and is a single undo step.

        Give exactly one of:
            changes: 1-200 items like {"element_id": 123, "created_phase":
                "Existente", "demolished_phase": "Nova construção"}.
            selector + created_phase/demolished_phase: apply the same phases to
                every model element matched by {"category": "Paredes",
                "level": "Térreo", "view_id": 456, "selection": true}
                (category or selection=true required; level, view_id narrow
                it). More than 200 matches is an error: narrow the selector.

        Phases are given by name (as shown in Revit) or id.
        demolished_phase "none" clears the demolition. The demolished phase
        cannot come before the created phase (same phase = temporary). An
        element without phase parameters, or with read-only phases, fails the
        whole batch with the reason in "errors". Each element_id may appear
        only once in changes.

        On the real run, pass expected_count = the dry run's "matched" count:
        if the elements matched now differ (e.g. the Revit selection changed
        in between), Revit refuses with HTTP 409 and nothing is changed.

        Args:
            changes: Per-element phases (see above).
            selector: Which elements to change (see above).
            created_phase: Phase applied with selector.
            demolished_phase: Demolished phase applied with selector ("none" clears).
            dry_run: Validate and preview only (default true). Use false to apply.
            expected_count: Number of elements the dry run matched (recommended on the real run).
        """
        if (changes is None) == (selector is None):
            return "Error: give either changes or selector (not both)"
        if expected_count is not None and (not _is_int(expected_count) or expected_count < 1):
            return "Error: expected_count must be a positive integer"
        data = {"dry_run": dry_run}
        if expected_count is not None:
            data["expected_count"] = expected_count
        if changes is not None:
            if created_phase is not None or demolished_phase is not None:
                return "Error: with changes, give the phases inside each change"
            if not changes:
                return "Error: changes must contain at least one item"
            if len(changes) > MAX_ITEMS:
                return "Error: at most {} changes per call, got {}".format(MAX_ITEMS, len(changes))
            seen = {}
            for index, change in enumerate(changes):
                error = _change_error(index, change)
                if error:
                    return "Error: " + error
                if change["element_id"] in seen:
                    return "Error: change {}: element_id {} repeats change {}".format(
                        index, change["element_id"], seen[change["element_id"]])
                seen[change["element_id"]] = index
            data["changes"] = [{key: value for key, value in change.items() if value is not None}
                               for change in changes]
        else:
            error = _selector_error(selector) or _phases_error(created_phase, demolished_phase)
            if error:
                return "Error: " + error
            data["selector"] = {key: value for key, value in selector.items() if value is not None}
            if created_phase is not None:
                data["created_phase"] = created_phase
            if demolished_phase is not None:
                data["demolished_phase"] = demolished_phase

        if ctx:
            await ctx.info("{} element phases...".format("Validating" if dry_run else "Applying"))
        response = await revit_post("/set_element_phases/", data, ctx, timeout=120.0)
        return format_response(response)
