# -*- coding: utf-8 -*-
"""Wrapper for audit_template (012)."""
import json

import pytest
from tools.audit_tools import register_audit_tools


class TestAuditTemplate:
    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, mock_revit_get, mock_revit_post):
        mock_revit_post.return_value = {"status": "success", "sections": {}}
        register_audit_tools(mock_mcp, mock_revit_get, mock_revit_post)
        self.tool = mock_mcp.tools["audit_template"]
        self.mock_post = mock_revit_post

    async def test_defaults_send_only_limit(self):
        await self.tool(ctx=None)
        self.mock_post.assert_called_once_with("/audit_template/", {"limit": 100}, None,
                                               timeout=300.0)

    async def test_sections_and_naming_are_sent_when_given(self):
        naming = {"views": u"^(PB|CT)-.+$", "sheet_numbers": r"^A\d{2}$"}
        await self.tool(sections=["views", "families"], limit=20, naming=naming, ctx=None)
        assert self.mock_post.call_args.args[1] == {
            "limit": 20, "sections": ["views", "families"], "naming": naming}

    @pytest.mark.parametrize("kwargs, message", [
        ({"limit": 0}, "limit"),
        ({"limit": 1001}, "limit"),
        ({"limit": True}, "limit"),
        ({"sections": []}, "sections"),
        ({"sections": ["views", "worksets"]}, "worksets"),
        ({"naming": {}}, "naming"),
        ({"naming": {"rooms": ".*"}}, "unknown kind"),
        ({"naming": {"views": " "}}, "non-empty"),
        ({"naming": {"views": "([A-Z"}}, "invalid regex"),
    ])
    async def test_rejects_bad_input_without_calling_revit(self, kwargs, message):
        result = await self.tool(ctx=None, **kwargs)
        assert result.startswith("Error:") and message in result
        self.mock_post.assert_not_called()

    async def test_success_returns_the_whole_report(self):
        self.mock_post.return_value = {
            "status": "success", "document": "Template",
            "sections": {"view_templates": {"count": 2, "unused_count": 1, "items": [],
                                            "truncated": False}},
            "checklist": [{"item": 5, "title": "Modelos de vista"}]}
        result = await self.tool(sections=["view_templates"], ctx=None)
        report = json.loads(result)
        assert report["sections"]["view_templates"]["unused_count"] == 1
        assert report["checklist"][0]["item"] == 5

    async def test_route_error_is_reported(self):
        self.mock_post.return_value = {"error": "limit must be an integer between 1 and 1000"}
        result = await self.tool(ctx=None)
        assert "limit must be" in result
