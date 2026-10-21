# -*- coding: utf-8 -*-
"""Wrapper for check_render_materials (spec 014)."""
import pytest
from tools.material_check_tools import register_material_check_tools


class TestCheckRenderMaterials:
    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, mock_revit_get, mock_revit_post):
        mock_revit_post.return_value = {"status": "success", "summary": {}, "problems": []}
        register_material_check_tools(mock_mcp, mock_revit_get, mock_revit_post)
        self.tool = mock_mcp.tools["check_render_materials"]
        self.mock_post = mock_revit_post

    async def test_defaults_send_no_optional_keys(self):
        await self.tool(ctx=None)
        self.mock_post.assert_called_once_with(
            "/check_render_materials/",
            {"max_families_per_material": 2, "min_materials_per_family": 2, "limit": 200},
            None, timeout=120.0)

    async def test_optional_fields_are_sent_only_when_given(self):
        await self.tool(categories=[u"Mobiliário", "OST_Casework"], name_pattern="^[A-Z_0-9]+$",
                        max_families_per_material=3, min_materials_per_family=1,
                        only_visible_in_view=u"3D Render", limit=10, ctx=None)
        assert self.mock_post.call_args.args[1] == {
            "categories": [u"Mobiliário", "OST_Casework"], "name_pattern": "^[A-Z_0-9]+$",
            "max_families_per_material": 3, "min_materials_per_family": 1,
            "only_visible_in_view": u"3D Render", "limit": 10}

    async def test_view_id(self):
        await self.tool(only_visible_in_view=42, ctx=None)
        assert self.mock_post.call_args.args[1]["only_visible_in_view"] == 42

    async def test_blank_view_is_dropped(self):
        await self.tool(only_visible_in_view="  ", ctx=None)
        assert "only_visible_in_view" not in self.mock_post.call_args.args[1]

    @pytest.mark.parametrize("kwargs, message", [
        ({"limit": 0}, "limit"),
        ({"limit": 1001}, "limit"),
        ({"max_families_per_material": 0}, "max_families_per_material"),
        ({"min_materials_per_family": 0}, "min_materials_per_family"),
        ({"categories": [" "]}, "categories"),
        ({"name_pattern": "("}, "name_pattern"),
        ({"name_pattern": " "}, "name_pattern"),
    ])
    async def test_rejects_bad_input_without_calling_revit(self, kwargs, message):
        result = await self.tool(ctx=None, **kwargs)
        assert result.startswith("Error:") and message in result
        self.mock_post.assert_not_called()

    async def test_response_keeps_problems(self):
        self.mock_post.return_value = {
            "status": "success", "summary": {"no_material": 1}, "truncated": False,
            "problems": [{"rule": "no_material", "severity": "error", "family": u"Poltrona",
                          "detail": "No material"}]}
        result = await self.tool(ctx=None)
        assert "ERROR DETAILS" not in result
        assert '"no_material"' in result and "Poltrona" in result
