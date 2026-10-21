# -*- coding: utf-8 -*-
"""Wrappers for create_floors_from_rooms and create_ceilings_from_rooms (020)."""
import pytest
from tools.finish_element_tools import register_finish_element_tools


@pytest.fixture
def tools(mock_mcp, mock_revit_get, mock_revit_post):
    mock_revit_post.return_value = {"status": "dry_run", "created": 0, "results": []}
    register_finish_element_tools(mock_mcp, mock_revit_get, mock_revit_post)
    return mock_mcp.tools, mock_revit_post


class TestCreateFloorsFromRooms:
    async def test_default_payload_has_no_none_keys(self, tools):
        registered, post = tools
        await registered["create_floors_from_rooms"](level=u"Térreo", ctx=None)
        post.assert_called_once_with(
            "/create_floors_from_rooms/",
            {"level": u"Térreo", "offset_m": 0.0, "boundary_location": "finish",
             "skip_existing": True, "dry_run": True},
            None, timeout=120.0)

    async def test_optional_settings_are_sent(self, tools):
        registered, post = tools
        await registered["create_floors_from_rooms"](
            room_numbers=["101", 102], floor_type=u"Porcelanato 1cm", offset_m=0.01,
            boundary_location="center", skip_existing=False, dry_run=False, ctx=None)
        payload = post.call_args.args[1]
        assert payload == {"room_numbers": ["101", "102"], "floor_type": u"Porcelanato 1cm",
                           "offset_m": 0.01, "boundary_location": "center",
                           "skip_existing": False, "dry_run": False}

    async def test_expected_count_is_sent(self, tools):
        registered, post = tools
        await registered["create_floors_from_rooms"](level="T", expected_count=4, dry_run=False,
                                                     ctx=None)
        assert post.call_args.args[1]["expected_count"] == 4

    async def test_room_ids(self, tools):
        registered, post = tools
        await registered["create_floors_from_rooms"](room_ids=[11, 12], ctx=None)
        assert post.call_args.args[1]["room_ids"] == [11, 12]

    @pytest.mark.parametrize("kwargs, message", [
        ({}, "give room_numbers, room_ids or level"),
        ({"room_numbers": ["1"], "room_ids": [1]}, "not both"),
        ({"room_numbers": []}, "must not be empty"),
        ({"room_numbers": [""]}, "room numbers"),
        ({"room_numbers": [str(i) for i in range(201)]}, "at most 200"),
        ({"room_ids": [True]}, "integers"),
        ({"room_ids": list(range(201))}, "at most 200"),
        ({"level": " "}, "level"),
        ({"level": "T", "floor_type": ""}, "floor_type"),
        ({"level": "T", "boundary_location": "core"}, "boundary_location"),
        ({"level": "T", "offset_m": 6}, "offset_m"),
        ({"level": "T", "offset_m": float("nan")}, "offset_m"),
        ({"level": "T", "expected_count": -1}, "expected_count"),
        ({"level": "T", "expected_count": True}, "expected_count"),
    ])
    async def test_rejects_malformed_input_without_calling_revit(self, tools, kwargs, message):
        registered, post = tools
        result = await registered["create_floors_from_rooms"](ctx=None, **kwargs)
        assert result.startswith("Error:") and message in result
        post.assert_not_called()


class TestCreateCeilingsFromRooms:
    async def test_default_payload_has_no_none_keys(self, tools):
        registered, post = tools
        await registered["create_ceilings_from_rooms"](room_numbers=["101"], ctx=None)
        post.assert_called_once_with(
            "/create_ceilings_from_rooms/",
            {"room_numbers": ["101"], "boundary_location": "finish",
             "skip_existing": True, "dry_run": True},
            None, timeout=120.0)

    async def test_optional_settings_are_sent(self, tools):
        registered, post = tools
        await registered["create_ceilings_from_rooms"](
            level="Pavimento 1", ceiling_type="Forro gesso", height_m=2.6, dry_run=False,
            expected_count=3, ctx=None)
        payload = post.call_args.args[1]
        assert payload["ceiling_type"] == "Forro gesso" and payload["height_m"] == 2.6
        assert payload["dry_run"] is False and "offset_m" not in payload
        assert payload["expected_count"] == 3

    @pytest.mark.parametrize("kwargs, message", [
        ({}, "give room_numbers, room_ids or level"),
        ({"level": "T", "height_m": 0}, "height_m"),
        ({"level": "T", "height_m": 11}, "height_m"),
        ({"level": "T", "ceiling_type": " "}, "ceiling_type"),
    ])
    async def test_rejects_malformed_input_without_calling_revit(self, tools, kwargs, message):
        registered, post = tools
        result = await registered["create_ceilings_from_rooms"](ctx=None, **kwargs)
        assert result.startswith("Error:") and message in result
        post.assert_not_called()
