# -*- coding: utf-8 -*-
"""Wrappers for create_walls, create_rooms and place_hosted_families (011)."""
import pytest
from tools.modeling_tools import register_modeling_tools

WALL = {"start": [0, 0], "end": [5, 0]}
ROOM = {"point": [2.5, 2.0], "name": "Sala"}
DOOR = {"family": "Porta", "wall_id": 123, "offset_m": 1.2}


@pytest.fixture
def tools(mock_mcp, mock_revit_get, mock_revit_post):
    mock_revit_post.return_value = {"status": "success", "created": 1, "results": []}
    register_modeling_tools(mock_mcp, mock_revit_get, mock_revit_post)
    return mock_mcp.tools, mock_revit_post


class TestCreateWalls:
    async def test_dry_run_is_the_default_and_no_none_keys(self, tools):
        registered, post = tools
        await registered["create_walls"](walls=[WALL], ctx=None)
        post.assert_called_once_with(
            "/create_walls/",
            {"walls": [WALL], "dry_run": True, "height_m": 2.8, "structural": False,
             "join": True},
            None, timeout=120.0)

    async def test_optional_settings_are_sent(self, tools):
        registered, post = tools
        await registered["create_walls"](
            walls=[WALL], level=u"Térreo", top_level="Pavimento 1", wall_type="Tijolo 15",
            location_line="finish_interior", join=False, dry_run=False, ctx=None)
        payload = post.call_args.args[1]
        assert payload["dry_run"] is False and payload["join"] is False
        assert payload["level"] == u"Térreo" and payload["top_level"] == "Pavimento 1"
        assert payload["wall_type"] == "Tijolo 15"
        assert payload["location_line"] == "finish_interior"

    @pytest.mark.parametrize("kwargs, message", [
        ({"walls": []}, "at least one"),
        ({"walls": [WALL] * 201}, "at most 200"),
        ({"walls": ["x"]}, "must be an object"),
        ({"walls": [{"start": [0, 0]}]}, "[x, y]"),
        ({"walls": [{"start": [0, 0], "end": [0, "5"]}]}, "[x, y]"),
        ({"walls": [{"start": [0, 0], "end": [0, 0]}]}, "shorter"),
        ({"walls": [{"start": [0, 0], "end": [1, 0], "height_m": -1}]}, "height_m"),
        ({"walls": [{"start": [0, 0], "end": [1, 0], "location_line": "x"}]}, "location_line"),
        ({"walls": [{"start": [0, 0], "end": [1, 0], "join": "no"}]}, "join"),
        ({"walls": [WALL], "height_m": 0}, "height_m"),
        ({"walls": [WALL], "location_line": "center"}, "location_line"),
    ])
    async def test_rejects_malformed_input_without_calling_revit(self, tools, kwargs, message):
        registered, post = tools
        result = await registered["create_walls"](ctx=None, **kwargs)
        assert result.startswith("Error:") and message in result
        post.assert_not_called()


class TestCreateRooms:
    async def test_default_payload(self, tools):
        registered, post = tools
        await registered["create_rooms"](rooms=[ROOM], ctx=None)
        post.assert_called_once_with(
            "/create_rooms/", {"rooms": [ROOM], "dry_run": True, "tag": True},
            None, timeout=120.0)

    async def test_level_and_boundary(self, tools):
        registered, post = tools
        room = {"boundary": [[0, 0], [3, 0], [3, 3], [0, 3]], "number": 101}
        await registered["create_rooms"](rooms=[room], level="Pavimento 1", tag=False,
                                         dry_run=False, ctx=None)
        payload = post.call_args.args[1]
        assert payload == {"rooms": [room], "dry_run": False, "tag": False,
                           "level": "Pavimento 1"}

    @pytest.mark.parametrize("rooms, message", [
        ([], "at least one"),
        ([ROOM] * 201, "at most 200"),
        ([{"name": "Sala"}], "point [x, y] or boundary"),
        ([{"point": [1, 2, 3]}], "point must be"),
        ([{"boundary": [[0, 0], [1, 1]]}], "at least 3"),
        ([{"point": [1, 1], "number": 1.5}], "number"),
        ([{"point": [1, 1], "name": ""}], "name"),
    ])
    async def test_rejects_malformed_rooms(self, tools, rooms, message):
        registered, post = tools
        result = await registered["create_rooms"](rooms=rooms, ctx=None)
        assert result.startswith("Error:") and message in result
        post.assert_not_called()


class TestPlaceHostedFamilies:
    async def test_default_payload(self, tools):
        registered, post = tools
        await registered["place_hosted_families"](instances=[DOOR], ctx=None)
        post.assert_called_once_with(
            "/place_hosted_families/", {"instances": [DOOR], "dry_run": True},
            None, timeout=120.0)

    @pytest.mark.parametrize("instances, message", [
        ([], "at least one"),
        ([DOOR] * 201, "at most 200"),
        ([{"wall_id": 1, "offset_m": 1}], "family"),
        ([{"family": "Porta"}], "wall_id or point"),
        ([{"family": "Porta", "wall_id": 1}], "offset_m or point"),
        ([{"family": "Porta", "wall_id": "1", "offset_m": 1}], "wall_id must be"),
        ([{"family": "Porta", "point": [1]}], "point must be"),
        ([{"family": "Porta", "wall_id": 1, "offset_m": -2}], "offset_m"),
        ([{"family": "Janela", "point": [1, 0], "sill_height_m": "1.1"}], "sill_height_m"),
        ([{"family": "Porta", "point": [1, 0], "flip_hand": 1}], "flip_hand"),
    ])
    async def test_rejects_malformed_instances(self, tools, instances, message):
        registered, post = tools
        result = await registered["place_hosted_families"](instances=instances, ctx=None)
        assert result.startswith("Error:") and message in result
        post.assert_not_called()

    async def test_dry_run_and_item_errors_are_shown(self, tools):
        registered, post = tools
        post.return_value = {"status": "dry_run", "message": "Dry run: Revit accepted all 1 instances.",
                             "created": 0, "results": [{"index": 0, "wall_id": 123}]}
        result = await registered["place_hosted_families"](instances=[DOOR], ctx=None)
        assert "ERROR" not in result and '"wall_id": 123' in result

        post.return_value = {"status": "error", "error": "1 of 1 instances are invalid",
                             "errors": [{"index": 0, "error": "Insert > Load Family"}]}
        result = await registered["place_hosted_families"](instances=[DOOR], ctx=None)
        assert "1 of 1 instances are invalid" in result
