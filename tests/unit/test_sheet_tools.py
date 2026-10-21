# -*- coding: utf-8 -*-
"""Wrappers for create_sheets, place_views_on_sheets (007) and apply_view_template (008)."""
import pytest
from tools.sheet_tools import register_sheet_tools

SHEET = {"number": "A101", "name": u"Planta Térreo"}
PLACEMENT = {"sheet_number": "A101", "view": u"Térreo"}


@pytest.fixture
def tools(mock_mcp, mock_revit_get, mock_revit_post):
    mock_revit_post.return_value = {"status": "success", "applied": 1, "results": []}
    register_sheet_tools(mock_mcp, mock_revit_get, mock_revit_post)
    return mock_mcp.tools, mock_revit_post


class TestCreateSheets:
    async def test_dry_run_is_the_default_and_no_null_keys(self, tools):
        registered, post = tools
        await registered["create_sheets"](sheets=[SHEET], ctx=None)
        post.assert_called_once_with(
            "/create_sheets/", {"sheets": [SHEET], "dry_run": True}, None, timeout=120.0)

    async def test_real_run_with_title_block(self, tools):
        registered, post = tools
        await registered["create_sheets"](sheets=[SHEET], title_block="A1 : Padrão",
                                          dry_run=False, ctx=None)
        payload = post.call_args.args[1]
        assert payload["dry_run"] is False and payload["title_block"] == "A1 : Padrão"

    async def test_extra_keys_are_not_sent(self, tools):
        registered, post = tools
        await registered["create_sheets"](sheets=[dict(SHEET, junk=None)], ctx=None)
        assert post.call_args.args[1]["sheets"] == [SHEET]

    @pytest.mark.parametrize("sheets, title_block, message", [
        ([], None, "at least one"),
        ([SHEET] * 201, None, "at most 200"),
        (["x"], None, "must be an object"),
        ([{"number": "", "name": "a"}], None, "number"),
        ([{"number": "A1"}], None, "name"),
        ([{"number": 101, "name": "a"}], None, "number"),
        ([SHEET], " ", "title_block"),
    ])
    async def test_rejects_bad_input_without_calling_revit(self, tools, sheets, title_block, message):
        registered, post = tools
        result = await registered["create_sheets"](sheets=sheets, title_block=title_block, ctx=None)
        assert result.startswith("Error:") and message in result
        post.assert_not_called()


class TestPlaceViewsOnSheets:
    async def test_default_payload(self, tools):
        registered, post = tools
        await registered["place_views_on_sheets"](placements=[PLACEMENT], ctx=None)
        post.assert_called_once_with(
            "/place_views_on_sheets/", {"placements": [PLACEMENT], "dry_run": True},
            None, timeout=120.0)

    async def test_none_values_are_dropped(self, tools):
        registered, post = tools
        item = {"sheet_number": "A101", "view": None, "view_id": 5, "x": 0.4, "y": 0.3}
        await registered["place_views_on_sheets"](placements=[item], dry_run=False, ctx=None)
        payload = post.call_args.args[1]
        assert payload == {"placements": [{"sheet_number": "A101", "view_id": 5, "x": 0.4, "y": 0.3}],
                           "dry_run": False}

    @pytest.mark.parametrize("item, message", [
        ("x", "must be an object"),
        ({"view": "a"}, "sheet_number"),
        ({"sheet_number": "A1"}, "exactly one"),
        ({"sheet_number": "A1", "view": "a", "view_id": 3}, "exactly one"),
        ({"sheet_number": "A1", "view_id": "3"}, "view_id must be an integer"),
        ({"sheet_number": "A1", "view_id": True}, "view_id must be an integer"),
        ({"sheet_number": "A1", "view": "a", "x": 1}, "both x and y"),
        ({"sheet_number": "A1", "view": "a", "x": "1", "y": 2}, "numbers"),
    ])
    async def test_rejects_bad_items(self, tools, item, message):
        registered, post = tools
        result = await registered["place_views_on_sheets"](placements=[item], ctx=None)
        assert result.startswith("Error:") and message in result
        post.assert_not_called()

    async def test_batch_limit(self, tools):
        registered, post = tools
        result = await registered["place_views_on_sheets"](placements=[PLACEMENT] * 201, ctx=None)
        assert "at most 200" in result
        post.assert_not_called()


class TestApplyViewTemplate:
    async def test_by_views(self, tools):
        registered, post = tools
        await registered["apply_view_template"](template="Executivo", views=[u"Térreo", 12],
                                                ctx=None)
        post.assert_called_once_with(
            "/apply_view_template/",
            {"template": "Executivo", "dry_run": True, "views": [u"Térreo", 12]},
            None, timeout=120.0)

    async def test_by_view_type(self, tools):
        registered, post = tools
        await registered["apply_view_template"](template="Executivo", view_type="FloorPlan",
                                                dry_run=False, ctx=None)
        assert post.call_args.args[1] == {"template": "Executivo", "dry_run": False,
                                          "view_type": "FloorPlan"}

    @pytest.mark.parametrize("kwargs, message", [
        ({"template": " ", "views": ["a"]}, "template"),
        ({"template": "T"}, "exactly one"),
        ({"template": "T", "views": ["a"], "view_type": "FloorPlan"}, "exactly one"),
        ({"template": "T", "views": []}, "at least one"),
        ({"template": "T", "views": ["a"] * 201}, "at most 200"),
        ({"template": "T", "views": [True]}, "name or an element id"),
        ({"template": "T", "views": [""]}, "name or an element id"),
        ({"template": "T", "view_type": ""}, "view_type"),
    ])
    async def test_rejects_bad_input(self, tools, kwargs, message):
        registered, post = tools
        result = await registered["apply_view_template"](ctx=None, **kwargs)
        assert result.startswith("Error:") and message in result
        post.assert_not_called()

    async def test_dry_run_response_is_not_an_error(self, tools):
        registered, post = tools
        post.return_value = {"status": "dry_run", "message": "Dry run: 1 views are valid.",
                             "applied": 0, "results": [{"view": "A", "before": None,
                                                        "after": "Executivo"}]}
        result = await registered["apply_view_template"](template="Executivo", views=["A"],
                                                         ctx=None)
        assert "ERROR" not in result and '"after": "Executivo"' in result
