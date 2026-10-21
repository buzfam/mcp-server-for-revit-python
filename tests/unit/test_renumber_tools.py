# -*- coding: utf-8 -*-
"""Wrapper for renumber_elements (spec 018)."""
import pytest
from tools.renumber_tools import register_renumber_tools, pattern_error


@pytest.fixture
def tools(mock_mcp, mock_revit_get, mock_revit_post):
    mock_revit_post.return_value = {"status": "dry_run", "changed": 1, "results": []}
    register_renumber_tools(mock_mcp, mock_revit_get, mock_revit_post)
    return mock_mcp.tools["renumber_elements"], mock_revit_post


async def test_defaults_and_no_none_keys(tools):
    tool, post = tools
    await tool(target="doors", ctx=None)
    post.assert_called_once_with(
        "/renumber_elements/",
        {"target": "doors", "pattern": "{prefix}{seq}", "start": 1, "step": 1, "dry_run": True},
        None, timeout=120.0)


async def test_all_options_are_sent(tools):
    tool, post = tools
    await tool(target="Portas", pattern="P-{room_number}-{seq:02}", parameter="Marca",
               prefix="P-", start=10, step=2, order="location", group_by="room",
               level=u"Térreo", view_name="Planta", element_ids=[1, 2], phase="Existente",
               dry_run=False, ctx=None)
    payload = post.call_args.args[1]
    assert payload == {"target": "Portas", "pattern": "P-{room_number}-{seq:02}",
                       "parameter": "Marca", "prefix": "P-", "start": 10, "step": 2,
                       "order": "location", "group_by": "room", "level": u"Térreo",
                       "view_name": "Planta", "element_ids": [1, 2], "phase": "Existente",
                       "dry_run": False}


async def test_sheets_with_current_order(tools):
    tool, post = tools
    await tool(target="sheets", pattern="A-{seq}", start=101, order="current",
               element_ids=[5, 6], ctx=None)
    payload = post.call_args.args[1]
    assert payload["order"] == "current" and payload["element_ids"] == [5, 6]


@pytest.mark.parametrize("kwargs, message", [
    ({"target": " "}, "target is required"),
    ({"target": "doors", "pattern": "P-{room_number}"}, "must contain {seq}"),
    ({"target": "doors", "pattern": "{seq}-{floor}"}, "unknown token"),
    ({"target": "doors", "pattern": "{seq:2}"}, "unknown token"),
    ({"target": "doors", "pattern": "{seq}}"}, "unmatched brace"),
    ({"target": "doors", "start": -1}, "start"),
    ({"target": "doors", "step": 0}, "step"),
    ({"target": "doors", "order": "random"}, "order"),
    ({"target": "doors", "group_by": "type"}, "group_by"),
    ({"target": "doors", "view_name": "A", "view_id": 3}, "not both"),
    ({"target": "doors", "element_ids": []}, "at least one"),
    ({"target": "doors", "element_ids": list(range(201))}, "at most 200"),
    ({"target": "sheets", "pattern": "{level}-{seq}"}, "no room or level"),
    ({"target": "pranchas", "pattern": "{ room_number }{seq}"}, "no room or level"),
    ({"target": "sheets", "phase": "Existente"}, "not available for sheets"),
    ({"target": "doors", "element_ids": [1, "2"]}, "integers"),
    ({"target": "sheets", "order": "location"}, "no plan location"),
    ({"target": "Folhas", "group_by": "level"}, "not available for sheets"),
    ({"target": "sheets", "level": "Térreo"}, "not available for sheets"),
    ({"target": "rooms", "group_by": "room"}, "use level for rooms"),
])
async def test_rejects_bad_input_without_calling_revit(tools, kwargs, message):
    tool, post = tools
    result = await tool(ctx=None, **kwargs)
    assert result.startswith("Error:") and message in result
    post.assert_not_called()


def test_pattern_error_accepts_every_token():
    assert pattern_error("{prefix}{level}-{room_number}-{room_name}-{seq:03}") is None
    assert pattern_error("{ seq }") is None
