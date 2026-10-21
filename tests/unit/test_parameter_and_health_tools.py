# -*- coding: utf-8 -*-
"""Wrappers for bulk_set_parameters (003) and model_health_report (004)."""
import pytest
from tools.health_tools import register_health_tools
from tools.parameter_tools import register_parameter_tools

CHANGE = {"element_id": 1, "parameter": "Mark", "value": "P01"}


class TestBulkSetParameters:
    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, mock_revit_get, mock_revit_post):
        mock_revit_post.return_value = {"status": "success", "applied": 1, "results": []}
        register_parameter_tools(mock_mcp, mock_revit_get, mock_revit_post)
        self.tool = mock_mcp.tools["bulk_set_parameters"]
        self.mock_post = mock_revit_post

    async def test_dry_run_is_the_default(self):
        await self.tool(changes=[CHANGE], ctx=None)
        self.mock_post.assert_called_once_with(
            "/bulk_set_parameters/",
            {"changes": [CHANGE], "dry_run": True, "use_type": False},
            None, timeout=120.0)

    async def test_real_run_on_types(self):
        await self.tool(changes=[CHANGE], dry_run=False, use_type=True, ctx=None)
        payload = self.mock_post.call_args.args[1]
        assert payload["dry_run"] is False and payload["use_type"] is True

    async def test_normalize_numbers_is_sent_only_when_on(self):
        await self.tool(changes=[CHANGE], normalize_numbers=True, ctx=None)
        assert self.mock_post.call_args.args[1]["normalize_numbers"] is True
        self.mock_post.reset_mock()
        await self.tool(changes=[CHANGE], normalize_numbers=False, ctx=None)
        assert "normalize_numbers" not in self.mock_post.call_args.args[1]

    @pytest.mark.parametrize("changes, message", [
        ([], "at least one"),
        ([CHANGE] * 501, "at most 500"),
        (["x"], "must be an object"),
        ([{"element_id": "1", "parameter": "Mark", "value": "a"}], "element_id"),
        ([{"element_id": True, "parameter": "Mark", "value": "a"}], "element_id"),
        ([{"element_id": 1, "parameter": " ", "value": "a"}], "parameter"),
        ([{"element_id": 1, "parameter": "Mark"}], "value is required"),
    ])
    async def test_rejects_malformed_changes_without_calling_revit(self, changes, message):
        result = await self.tool(changes=changes, ctx=None)
        assert result.startswith("Error:") and message in result
        self.mock_post.assert_not_called()

    async def test_dry_run_response_is_not_formatted_as_error(self):
        self.mock_post.return_value = {
            "status": "dry_run", "message": "Dry run: 1 changes are valid.",
            "applied": 0, "results": [{"element_id": 1, "before": "P1", "after": "P01"}]}
        result = await self.tool(changes=[CHANGE], ctx=None)
        assert "ERROR" not in result
        assert '"after": "P01"' in result


class TestModelHealthReport:
    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, mock_revit_get, mock_revit_post):
        mock_revit_post.return_value = {"status": "success", "flags": []}
        register_health_tools(mock_mcp, mock_revit_get, mock_revit_post)
        self.tool = mock_mcp.tools["model_health_report"]
        self.mock_post = mock_revit_post

    async def test_default_payload(self):
        await self.tool(ctx=None)
        self.mock_post.assert_called_once_with(
            "/model_health/", {"sample_ids": 5}, None, timeout=120.0)

    @pytest.mark.parametrize("value", [-1, 21])
    async def test_rejects_out_of_range_samples(self, value):
        result = await self.tool(sample_ids=value, ctx=None)
        assert result.startswith("Error:")
        self.mock_post.assert_not_called()
