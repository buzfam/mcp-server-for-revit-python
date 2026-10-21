# -*- coding: utf-8 -*-
"""Wrapper for check_sheets (013)."""
import json

import pytest
from tools.sheet_check_tools import register_sheet_check_tools

RULE = {"name": "Planta de layout", "match": {"number_prefix": "A-1"},
        "required_views": [{"view_type": "FloorPlan", "name_regex": "layout"}],
        "allowed_scales": [50], "require_revision": True, "manual": ["cotas conferidas"]}
DEFAULTS = {"generic": True, "only_problems": False, "limit": 200, "unplaced_limit": 50,
            "unplaced_include_schedules": False}


@pytest.fixture
def tools(mock_mcp, mock_revit_get, mock_revit_post):
    mock_revit_post.return_value = {"status": "success", "summary": {"sheets_checked": 0},
                                    "sheets": []}
    register_sheet_check_tools(mock_mcp, mock_revit_get, mock_revit_post)
    return mock_mcp.tools["check_sheets"], mock_revit_post


async def test_generic_only_default_payload(tools):
    tool, post = tools
    result = await tool(ctx=None)
    post.assert_called_once_with("/check_sheets/", DEFAULTS, None, timeout=120.0)
    assert json.loads(result)["status"] == "success"   # summary is not dropped


async def test_checklists_and_sheets_are_sent_without_none(tools):
    tool, post = tools
    rule = dict(RULE, min_viewports=None,
                required_views=[{"view_type": "Section", "name_regex": None, "min": 2}])
    await tool(checklists=[rule], sheets=["A-101"], generic=False, only_problems=True,
               limit=10, unplaced_limit=0, unplaced_include_schedules=True, ctx=None)
    payload = post.call_args.args[1]
    assert payload["checklists"] == [dict(RULE, required_views=[{"view_type": "Section", "min": 2}])]
    assert payload["sheets"] == ["A-101"] and payload["limit"] == 10
    assert payload["generic"] is False and payload["only_problems"] is True
    assert payload["unplaced_limit"] == 0 and payload["unplaced_include_schedules"] is True


@pytest.mark.parametrize("kwargs, message", [
    ({"generic": False}, "nothing to check"),
    ({"checklists": [RULE] * 51}, "at most 50"),
    ({"checklists": ["x"]}, "checklist 0: must be an object"),
    ({"checklists": [{"name": "x"}]}, "match must be"),
    ({"checklists": [{"match": {"number_prefix": ""}}]}, "match.number_prefix"),
    ({"checklists": [{"match": {"prefix": "A"}}]}, "unknown keys prefix"),
    ({"checklists": [{"match": {"name_regex": "("}}]}, "invalid regex"),
    ({"checklists": [dict(RULE, required_view=[])]}, "unknown keys required_view"),
    ({"checklists": [dict(RULE, required_views=[{"view_type": "Planta"}])]}, "view_type must"),
    ({"checklists": [dict(RULE, required_views=[{"min": 1}])]}, "view_type and/or"),
    ({"checklists": [dict(RULE, required_views=[{"view_type": "Section", "min": 0}])]}, "min must"),
    ({"checklists": [dict(RULE, required_schedules=["["])]}, "invalid regex"),
    ({"checklists": [dict(RULE, allowed_scales=["1:50"])]}, "allowed_scales"),
    ({"checklists": [dict(RULE, require_revision="sim")]}, "require_revision"),
    ({"checklists": [dict(RULE, min_viewports=4, max_viewports=2)]}, "greater"),
    ({"checklists": [dict(RULE, title_block_params=[])]}, "title_block_params"),
    ({"sheets": []}, "at least one"),
    ({"sheets": ["A-1", " "]}, "non-empty"),
    ({"limit": 0}, "limit"),
    ({"limit": 1001}, "limit"),
    ({"unplaced_limit": 201}, "unplaced_limit"),
])
async def test_rejects_bad_input_without_calling_revit(tools, kwargs, message):
    tool, post = tools
    result = await tool(ctx=None, **kwargs)
    assert result.startswith("Error:") and message in result, result
    post.assert_not_called()
