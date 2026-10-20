# -*- coding: utf-8 -*-
"""Tests for selection and parameter tool wrappers."""
import pytest
from tools.selection_tools import register_selection_tools


class TestSelectionTools:
    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, mock_revit_get, mock_revit_post):
        mock_revit_post.return_value = {"status": "success", "total": 0, "elements": []}
        register_selection_tools(mock_mcp, mock_revit_get, mock_revit_post)
        self.tools = mock_mcp.tools
        self.mock_post = mock_revit_post

    async def test_get_selected_elements_default_limit(self):
        await self.tools["get_selected_elements"](ctx=None)
        self.mock_post.assert_called_once_with("/selection/", {"limit": 200}, None)

    async def test_get_selected_elements_custom_limit(self):
        await self.tools["get_selected_elements"](limit=1000, ctx=None)
        self.mock_post.assert_called_once_with("/selection/", {"limit": 1000}, None)

    @pytest.mark.parametrize("limit", [0, 1001])
    async def test_get_selected_elements_rejects_out_of_range_limit(self, limit):
        result = await self.tools["get_selected_elements"](limit=limit, ctx=None)
        assert result.startswith("Error:")
        self.mock_post.assert_not_called()

    async def test_get_element_parameters_defaults_to_selection(self):
        await self.tools["get_element_parameters"](ctx=None)
        self.mock_post.assert_called_once_with(
            "/element_parameters/", {"include_type": False}, None
        )

    async def test_get_element_parameters_full_payload(self):
        await self.tools["get_element_parameters"](
            element_ids=[1, 2], include_type=True, name_filter="Mark", ctx=None
        )
        self.mock_post.assert_called_once_with(
            "/element_parameters/",
            {"include_type": True, "element_ids": [1, 2], "name_filter": "Mark"},
            None,
        )

    async def test_get_element_parameters_omits_null_arguments(self):
        await self.tools["get_element_parameters"](
            element_ids=None, name_filter=None, ctx=None
        )
        payload = self.mock_post.call_args.args[1]
        assert "element_ids" not in payload
        assert "name_filter" not in payload

    async def test_get_element_parameters_rejects_more_than_50_ids(self):
        result = await self.tools["get_element_parameters"](
            element_ids=list(range(51)), ctx=None
        )
        assert result.startswith("Error:")
        self.mock_post.assert_not_called()

    async def test_success_is_formatted_as_json(self):
        self.mock_post.return_value = {
            "status": "success", "total": 1, "returned": 1, "truncated": False,
            "elements": [{"element_id": 7, "category": "Doors"}],
        }
        result = await self.tools["get_selected_elements"](ctx=None)
        assert '"element_id": 7' in result
