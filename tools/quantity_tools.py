# -*- coding: utf-8 -*-
"""Quantity takeoff tools: room finishes and quantities by group (spec 015)"""

import math
from mcp.server.fastmcp import Context
from typing import Any, Dict, List, Optional, Union
from .utils import format_response

MAX_LIMIT = 1000
FORMATS = ("json", "csv")
DEDUCT_MODES = ("over_threshold", "excess_over_threshold", "all", "none")
GROUPS = ("esquadrias", "loucas_metais", "alvenaria", "pisos", "forros", "rodapes", "pintura")
RULE_KEYS = ("deduct_openings", "deduct_openings_over_m2", "skirting_subtract_doors",
             "wall_height_m", "use_modeled_elements", "rounding", "waste_percent")


def _number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and not math.isnan(value) and not math.isinf(value))


def _rules_error(rules):
    if rules is None:
        return None
    if not isinstance(rules, dict):
        return "Error: rules must be an object"
    unknown = sorted(set(rules) - set(RULE_KEYS))
    if unknown:
        return "Error: unknown rules: {} (known: {})".format(", ".join(unknown), ", ".join(RULE_KEYS))
    mode = rules.get("deduct_openings")
    if mode is not None and mode not in DEDUCT_MODES:
        return "Error: deduct_openings must be one of: {}".format(", ".join(DEDUCT_MODES))
    threshold = rules.get("deduct_openings_over_m2")
    if threshold is not None and (not _number(threshold) or threshold < 0):
        return "Error: deduct_openings_over_m2 must be a number >= 0"
    for key in ("skirting_subtract_doors", "use_modeled_elements"):
        if rules.get(key) is not None and not isinstance(rules[key], bool):
            return "Error: {} must be true or false".format(key)
    height = rules.get("wall_height_m")
    if height is not None and (not _number(height) or not 0 < height <= 20):
        return "Error: wall_height_m must be a number between 0 and 20"
    digits = rules.get("rounding")
    if digits is not None and (not isinstance(digits, int) or isinstance(digits, bool)
                               or not 0 <= digits <= 4):
        return "Error: rounding must be an integer between 0 and 4"
    waste = rules.get("waste_percent")
    if waste is not None and (not _number(waste) or not 0 <= waste <= 100):
        return "Error: waste_percent must be a number between 0 and 100"
    return None


def _phase_error(phase):
    if phase is None:
        return None
    if isinstance(phase, bool):
        return "Error: phase must be a phase name or id"
    if isinstance(phase, int):
        return None if phase > 0 else "Error: phase must be a positive phase id"
    if isinstance(phase, str) and phase.strip():
        return None
    return "Error: phase must be a phase name or id"


def _clean_rules(rules):
    return {key: value for key, value in rules.items() if value is not None}


def _limit_error(limit):
    if not 1 <= limit <= MAX_LIMIT:
        return "Error: limit must be between 1 and {}".format(MAX_LIMIT)
    return None


def register_quantity_tools(mcp, revit_get, revit_post):
    """Register quantity takeoff tools with the MCP server."""

    @mcp.tool()
    async def room_finishes(
        level: Optional[str] = None,
        room_numbers: Optional[List[str]] = None,
        phase: Optional[Union[str, int]] = None,
        rules: Optional[Dict[str, Any]] = None,
        limit: int = 200,
        ctx: Context = None,
    ) -> str:
        """
        Finish quantities per room, each value with the rule and source used:
        floor area (m², modeled floors or room area), ceiling area (m²),
        wall area for painting/covering gross and net (m², finish boundary
        length × height, minus door/window openings by rule), skirting (m,
        wall boundary minus door widths) and painting (m², net walls + ceiling).

        Only placed rooms of the phase are measured; "skipped" counts unplaced,
        not enclosed and other-phase rooms. Room separation lines and curtain
        walls do not count as wall (curtain length reported apart). Openings
        are the New/Existing doors/windows of the phase whose FromRoom/ToRoom
        is the room; demolished ones are excluded. Structural floors (slabs)
        are not finish floors.
        Never quote a number without its rule (rules.criteria).

        Measurement rules (all optional; the response echoes the applied rules
        and a pt-BR criterion text for each value under "rules"):
            deduct_openings: "over_threshold" (default: openings above the
                threshold are deducted whole), "excess_over_threshold" (only
                the area above the threshold is deducted, SINAPI/TCPO-like),
                "all" or "none". Confirm the criterion with the office.
            deduct_openings_over_m2: the threshold of both threshold modes
                (default 2.0, a common Brazilian criterion; confirm with the office).
            skirting_subtract_doors: subtract door widths from skirting (default true).
            wall_height_m: fixed wall height for every room (default: modeled
                ceiling height, else room height).
            use_modeled_elements: use modeled floors/ceilings inside the room
                (default true); otherwise the room area.
            rounding: decimal places of the output (0-4, default 2).
            waste_percent: losses, reported separately as quantity_with_waste (0-100, default 0).

        Args:
            level: Only rooms on this level (name as shown in Revit).
            room_numbers: Only these room numbers.
            phase: Phase to measure, name or id (default: the last phase).
            rules: Measurement rules (see above).
            limit: Maximum number of rooms (1-1000, default 200).
        """
        error = _limit_error(limit) or _rules_error(rules) or _phase_error(phase)
        if error:
            return error
        if room_numbers is not None and (
                not room_numbers
                or not all(isinstance(n, str) and n.strip() for n in room_numbers)):
            return "Error: room_numbers must be a non-empty list of room numbers"

        data = {"limit": limit}
        if level:
            data["level"] = level
        if room_numbers:
            data["room_numbers"] = room_numbers
        if phase is not None:
            data["phase"] = phase
        if rules:
            data["rules"] = _clean_rules(rules)
        if ctx:
            await ctx.info("Measuring room finishes...")
        response = await revit_post("/room_finishes/", data, ctx, timeout=120.0)
        return format_response(response)

    @mcp.tool()
    async def quantities_by_rule(
        groups: Optional[List[str]] = None,
        level: Optional[str] = None,
        include_plumbing_equipment: bool = False,
        phase: Optional[Union[str, int]] = None,
        rules: Optional[Dict[str, Any]] = None,
        limit: int = 200,
        format: str = "json",
        ctx: Context = None,
    ) -> str:
        """
        Quantity takeoff lines by group, for comparison with engineering
        budgets. Each line: group, item, measure, unit, quantity
        (+ quantity_with_waste when waste_percent > 0) and the rule used.

        Groups: "esquadrias" (doors/windows count per family, type and size,
        with width × height and area), "loucas_metais" (plumbing fixtures
        count per family/type), "alvenaria" (walls per type: length, gross
        and net area by the opening rule, volume), "pisos" and "forros"
        (modeled area per type), "rodapes" (skirting total) and "pintura"
        (net walls + ceilings, from the room finishes). Default: all.

        Phase: only elements New or Existing in the phase (default: last
        phase) and rooms of that phase; demolished elements are excluded.
        Stacked walls are measured once (members skipped); structural floors
        (slabs) are left out of "pisos" and reported in notes.

        Measurement rules (all optional; the response echoes the applied rules
        and a pt-BR criterion text for each value under "rules"):
            deduct_openings: "over_threshold" (default: openings above the
                threshold are deducted whole), "excess_over_threshold" (only
                the area above the threshold is deducted, SINAPI/TCPO-like),
                "all" or "none". Confirm the criterion with the office.
            deduct_openings_over_m2: the threshold of both threshold modes
                (default 2.0, a common Brazilian criterion; confirm with the office).
            skirting_subtract_doors: subtract door widths from skirting (default true).
            wall_height_m: fixed wall height for every room (default: modeled
                ceiling height, else room height).
            use_modeled_elements: use modeled floors/ceilings inside the room
                (default true); otherwise the room area.
            rounding: decimal places of the output (0-4, default 2).
            waste_percent: losses, reported separately as quantity_with_waste (0-100, default 0).

        Args:
            groups: Groups to compute (default all, see above).
            level: Only elements/rooms on this level (name as shown in Revit).
            include_plumbing_equipment: Also count plumbing equipment in loucas_metais.
            phase: Phase to measure, name or id (default: the last phase).
            rules: Measurement rules (see above).
            limit: Maximum number of lines (1-1000, default 200).
            format: "json" (default) or "csv".
        """
        error = _limit_error(limit) or _rules_error(rules) or _phase_error(phase)
        if error:
            return error
        if format not in FORMATS:
            return "Error: format must be 'json' or 'csv'"
        if groups is not None:
            if not groups:
                return "Error: groups must be a non-empty list"
            unknown = [g for g in groups if g not in GROUPS]
            if unknown:
                return "Error: unknown groups: {} (use: {})".format(
                    ", ".join(map(str, unknown)), ", ".join(GROUPS))

        data = {"include_plumbing_equipment": include_plumbing_equipment,
                "limit": limit, "format": format}
        if groups:
            data["groups"] = groups
        if level:
            data["level"] = level
        if phase is not None:
            data["phase"] = phase
        if rules:
            data["rules"] = _clean_rules(rules)
        if ctx:
            await ctx.info("Computing quantities...")
        response = await revit_post("/quantities_by_rule/", data, ctx, timeout=120.0)
        return format_response(response)
