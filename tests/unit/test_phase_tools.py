# -*- coding: utf-8 -*-
"""Wrappers for phases_report and set_element_phases (016)."""
import pytest
from tools.phase_tools import register_phase_tools

CHANGE = {"element_id": 123, "created_phase": "Existente", "demolished_phase": u"Nova construção"}


@pytest.fixture
def tools(mock_mcp, mock_revit_get, mock_revit_post):
    mock_revit_post.return_value = {"status": "success", "results": []}
    register_phase_tools(mock_mcp, mock_revit_get, mock_revit_post)
    return mock_mcp.tools, mock_revit_post


class TestPhasesReport:
    async def test_defaults(self, tools):
        registered, post = tools
        await registered["phases_report"](ctx=None)
        post.assert_called_once_with("/phases_report/", {"by_category": False, "limit": 200},
                                     None, timeout=120.0)

    async def test_options(self, tools):
        registered, post = tools
        await registered["phases_report"](categories=["Paredes", "OST_Doors"], by_category=True,
                                          limit=50, ctx=None)
        assert post.call_args.args[1] == {"by_category": True, "limit": 50,
                                          "categories": ["Paredes", "OST_Doors"]}

    @pytest.mark.parametrize("kwargs, message", [
        ({"limit": 0}, "limit"),
        ({"limit": 1001}, "limit"),
        ({"categories": []}, "categories"),
        ({"categories": [" "]}, "categories"),
    ])
    async def test_rejects_bad_input(self, tools, kwargs, message):
        registered, post = tools
        result = await registered["phases_report"](ctx=None, **kwargs)
        assert result.startswith("Error:") and message in result
        post.assert_not_called()


class TestSetElementPhases:
    async def test_changes_dry_run_by_default(self, tools):
        registered, post = tools
        await registered["set_element_phases"](changes=[CHANGE], ctx=None)
        post.assert_called_once_with("/set_element_phases/",
                                     {"dry_run": True, "changes": [CHANGE]}, None, timeout=120.0)

    async def test_none_valued_keys_are_dropped(self, tools):
        registered, post = tools
        change = {"element_id": 5, "created_phase": 77, "demolished_phase": None}
        await registered["set_element_phases"](changes=[change], dry_run=False, ctx=None)
        assert post.call_args.args[1] == {"dry_run": False,
                                          "changes": [{"element_id": 5, "created_phase": 77}]}

    async def test_selector(self, tools):
        registered, post = tools
        await registered["set_element_phases"](
            selector={"category": "Paredes", "level": u"Térreo", "view_id": None},
            created_phase="Existente", demolished_phase="none", ctx=None)
        assert post.call_args.args[1] == {
            "dry_run": True, "selector": {"category": "Paredes", "level": u"Térreo"},
            "created_phase": "Existente", "demolished_phase": "none"}

    async def test_expected_count_is_sent(self, tools):
        registered, post = tools
        await registered["set_element_phases"](changes=[CHANGE], dry_run=False, expected_count=1,
                                               ctx=None)
        assert post.call_args.args[1] == {"dry_run": False, "expected_count": 1, "changes": [CHANGE]}

    async def test_selection_selector(self, tools):
        registered, post = tools
        await registered["set_element_phases"](selector={"selection": True},
                                               demolished_phase=3, ctx=None)
        assert post.call_args.args[1] == {"dry_run": True, "selector": {"selection": True},
                                          "demolished_phase": 3}

    @pytest.mark.parametrize("kwargs, message", [
        ({}, "either changes or selector"),
        ({"changes": [CHANGE], "selector": {"category": "Paredes"}}, "either"),
        ({"changes": []}, "at least one"),
        ({"changes": [CHANGE] * 201}, "at most 200"),
        ({"changes": ["x"]}, "must be an object"),
        ({"changes": [{"element_id": "1", "created_phase": "A"}]}, "element_id"),
        ({"changes": [{"element_id": True, "created_phase": "A"}]}, "element_id"),
        ({"changes": [{"element_id": 1}]}, "created_phase and/or demolished_phase"),
        ({"changes": [{"element_id": 1, "created_phase": ""}]}, "phase name or id"),
        ({"changes": [{"element_id": 1, "created_phase": 0}]}, "positive"),
        ({"changes": [{"element_id": 1, "created_phase": "none"}]}, "cannot be cleared"),
        ({"changes": [{"element_id": 1, "phase": "A"}]}, "unknown keys"),
        ({"changes": [CHANGE], "created_phase": "A"}, "inside each change"),
        ({"changes": [CHANGE, dict(CHANGE, created_phase="B")]}, "change 1: element_id 123 repeats change 0"),
        ({"changes": [CHANGE], "expected_count": 0}, "expected_count"),
        ({"changes": [CHANGE], "expected_count": True}, "expected_count"),
        ({"selector": {"category": "Paredes"}}, "created_phase and/or"),
        ({"selector": {"level": "L1"}, "created_phase": "A"}, "category or selection"),
        ({"selector": {"category": "Paredes", "foo": 1}, "created_phase": "A"}, "unknown selector"),
        ({"selector": {"category": "Paredes", "view_id": "9"}, "created_phase": "A"}, "view_id"),
        ({"selector": {"selection": "yes"}, "created_phase": "A"}, "selection"),
        ({"selector": "Paredes", "created_phase": "A"}, "must be an object"),
    ])
    async def test_rejects_malformed_input_without_calling_revit(self, tools, kwargs, message):
        registered, post = tools
        result = await registered["set_element_phases"](ctx=None, **kwargs)
        assert result.startswith("Error:") and message in result
        post.assert_not_called()
