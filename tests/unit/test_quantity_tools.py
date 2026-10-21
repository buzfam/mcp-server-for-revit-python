# -*- coding: utf-8 -*-
"""Wrappers for room_finishes and quantities_by_rule (015)."""
import pytest
from tools.quantity_tools import register_quantity_tools

BAD_RULES = [
    ({"rules": "x"}, "rules must be an object"),
    ({"rules": {"bogus": 1}}, "unknown rules"),
    ({"rules": {"deduct_openings": "some"}}, "deduct_openings"),
    ({"rules": {"deduct_openings_over_m2": -1}}, "deduct_openings_over_m2"),
    ({"rules": {"skirting_subtract_doors": "yes"}}, "skirting_subtract_doors"),
    ({"rules": {"wall_height_m": 0}}, "wall_height_m"),
    ({"rules": {"rounding": 5}}, "rounding"),
    ({"rules": {"rounding": True}}, "rounding"),
    ({"rules": {"waste_percent": 150}}, "waste_percent"),
    ({"limit": 0}, "limit"),
    ({"limit": 1001}, "limit"),
]


class TestRoomFinishes:
    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, mock_revit_get, mock_revit_post):
        mock_revit_post.return_value = {"status": "success", "rooms": []}
        register_quantity_tools(mock_mcp, mock_revit_get, mock_revit_post)
        self.tool = mock_mcp.tools["room_finishes"]
        self.mock_post = mock_revit_post

    async def test_defaults(self):
        await self.tool(ctx=None)
        self.mock_post.assert_called_once_with("/room_finishes/", {"limit": 200}, None,
                                               timeout=120.0)

    async def test_filters_and_rules_without_none_values(self):
        await self.tool(level=u"Térreo", room_numbers=["101", "102"],
                        rules={"deduct_openings": "all", "wall_height_m": None, "rounding": 1},
                        limit=10, ctx=None)
        payload = self.mock_post.call_args.args[1]
        assert payload == {"limit": 10, "level": u"Térreo", "room_numbers": ["101", "102"],
                           "rules": {"deduct_openings": "all", "rounding": 1}}

    @pytest.mark.parametrize("kwargs, message", BAD_RULES + [
        ({"room_numbers": []}, "room_numbers"),
        ({"room_numbers": ["101", " "]}, "room_numbers"),
    ])
    async def test_rejects_bad_input_without_calling_revit(self, kwargs, message):
        result = await self.tool(ctx=None, **kwargs)
        assert result.startswith("Error:") and message in result
        self.mock_post.assert_not_called()

    async def test_response_keeps_rules(self):
        self.mock_post.return_value = {
            "status": "success", "rules": {"criteria": {"paredes": u"área de parede"}},
            "rooms": [{"number": "101", "walls": {"net_m2": 32.6}}]}
        result = await self.tool(ctx=None)
        assert "ERROR" not in result and "32.6" in result and "criteria" in result


class TestQuantitiesByRule:
    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, mock_revit_get, mock_revit_post):
        mock_revit_post.return_value = {"status": "success", "lines": []}
        register_quantity_tools(mock_mcp, mock_revit_get, mock_revit_post)
        self.tool = mock_mcp.tools["quantities_by_rule"]
        self.mock_post = mock_revit_post

    async def test_defaults_all_groups(self):
        await self.tool(ctx=None)
        self.mock_post.assert_called_once_with(
            "/quantities_by_rule/",
            {"include_plumbing_equipment": False, "limit": 200, "format": "json"},
            None, timeout=120.0)

    async def test_groups_level_rules_csv(self):
        await self.tool(groups=["esquadrias", "pintura"], level="Superior",
                        include_plumbing_equipment=True, rules={"waste_percent": 10},
                        limit=50, format="csv", ctx=None)
        assert self.mock_post.call_args.args[1] == {
            "include_plumbing_equipment": True, "limit": 50, "format": "csv",
            "groups": ["esquadrias", "pintura"], "level": "Superior",
            "rules": {"waste_percent": 10}}

    @pytest.mark.parametrize("kwargs, message", BAD_RULES + [
        ({"groups": []}, "groups"),
        ({"groups": ["marcenaria"]}, "unknown groups: marcenaria"),
        ({"format": "xlsx"}, "format"),
    ])
    async def test_rejects_bad_input_without_calling_revit(self, kwargs, message):
        result = await self.tool(ctx=None, **kwargs)
        assert result.startswith("Error:") and message in result
        self.mock_post.assert_not_called()
