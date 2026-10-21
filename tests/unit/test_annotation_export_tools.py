# -*- coding: utf-8 -*-
"""Wrappers for tag_elements (009), export_schedule and export_rooms (010)."""
import pytest
from tools.annotation_tools import register_annotation_tools
from tools.export_tools import register_export_tools


class TestTagElements:
    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, mock_revit_get, mock_revit_post):
        mock_revit_post.return_value = {"status": "success", "tagged": 2}
        register_annotation_tools(mock_mcp, mock_revit_get, mock_revit_post)
        self.tool = mock_mcp.tools["tag_elements"]
        self.mock_post = mock_revit_post

    async def test_dry_run_in_active_view_is_the_default(self):
        await self.tool(category="Doors", ctx=None)
        self.mock_post.assert_called_once_with(
            "/tag_elements/",
            {"category": "Doors", "add_leader": False, "skip_tagged": True,
             "limit": 200, "dry_run": True},
            None, timeout=120.0)

    async def test_optional_fields_are_sent_only_when_given(self):
        await self.tool(category="Portas", view_name=u"Térreo", tag_type="Door Tag: 1",
                        add_leader=True, skip_tagged=False, limit=10, dry_run=False, ctx=None)
        payload = self.mock_post.call_args.args[1]
        assert payload == {"category": "Portas", "view_name": u"Térreo", "tag_type": "Door Tag: 1",
                           "add_leader": True, "skip_tagged": False, "limit": 10,
                           "dry_run": False}

    async def test_view_id(self):
        await self.tool(category="Rooms", view_id=42, ctx=None)
        payload = self.mock_post.call_args.args[1]
        assert payload["view_id"] == 42 and "view_name" not in payload

    @pytest.mark.parametrize("kwargs, message", [
        ({"category": " "}, "category"),
        ({"category": "Doors", "limit": 0}, "limit"),
        ({"category": "Doors", "limit": 1001}, "limit"),
        ({"category": "Doors", "view_name": "A", "view_id": 1}, "not both"),
    ])
    async def test_rejects_bad_input_without_calling_revit(self, kwargs, message):
        result = await self.tool(ctx=None, **kwargs)
        assert result.startswith("Error:") and message in result
        self.mock_post.assert_not_called()

    async def test_dry_run_response_keeps_counts(self):
        self.mock_post.return_value = {
            "status": "dry_run", "message": "Dry run: 3 elements can be tagged.",
            "tagged": 3, "skipped": {"already_tagged": 1}, "failed": []}
        result = await self.tool(category="Doors", ctx=None)
        assert "ERROR" not in result
        assert '"already_tagged": 1' in result


class TestExportSchedule:
    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, mock_revit_get, mock_revit_post):
        mock_revit_post.return_value = {"status": "success", "columns": ["A"], "rows": [["1"]]}
        register_export_tools(mock_mcp, mock_revit_get, mock_revit_post)
        self.tool = mock_mcp.tools["export_schedule"]
        self.mock_post = mock_revit_post

    async def test_by_name_default_json(self):
        await self.tool(schedule_name="Tabela de portas", ctx=None)
        self.mock_post.assert_called_once_with(
            "/export_schedule/",
            {"limit": 200, "format": "json", "schedule_name": "Tabela de portas"},
            None, timeout=60.0)

    async def test_by_id_csv(self):
        await self.tool(schedule_id=7, format="csv", limit=5, ctx=None)
        assert self.mock_post.call_args.args[1] == {"limit": 5, "format": "csv", "schedule_id": 7}

    @pytest.mark.parametrize("kwargs, message", [
        ({}, "required"),
        ({"schedule_name": "A", "schedule_id": 1}, "not both"),
        ({"schedule_name": "A", "limit": 0}, "limit"),
        ({"schedule_name": "A", "format": "xlsx"}, "format"),
    ])
    async def test_rejects_bad_input_without_calling_revit(self, kwargs, message):
        result = await self.tool(ctx=None, **kwargs)
        assert result.startswith("Error:") and message in result
        self.mock_post.assert_not_called()

    async def test_success_returns_the_rows(self):
        result = await self.tool(schedule_name="A", ctx=None)
        assert '"rows"' in result


class TestExportRooms:
    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, mock_revit_get, mock_revit_post):
        mock_revit_post.return_value = {"status": "success", "rooms": [], "total": 0}
        register_export_tools(mock_mcp, mock_revit_get, mock_revit_post)
        self.tool = mock_mcp.tools["export_rooms"]
        self.mock_post = mock_revit_post

    async def test_default_payload(self):
        await self.tool(ctx=None)
        self.mock_post.assert_called_once_with(
            "/export_rooms/",
            {"include_unplaced": True, "include_details": False, "limit": 200, "format": "json"},
            None, timeout=60.0)

    async def test_level_filter_and_csv(self):
        await self.tool(level=u"Térreo", format="csv", include_details=True, ctx=None)
        payload = self.mock_post.call_args.args[1]
        assert payload["level"] == u"Térreo" and payload["format"] == "csv"
        assert payload["include_details"] is True

    @pytest.mark.parametrize("kwargs", [{"limit": 0}, {"limit": 1001}, {"format": "XML"}])
    async def test_rejects_bad_options(self, kwargs):
        result = await self.tool(ctx=None, **kwargs)
        assert result.startswith("Error:")
        self.mock_post.assert_not_called()
