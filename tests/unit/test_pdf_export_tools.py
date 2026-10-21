# -*- coding: utf-8 -*-
"""Wrapper for export_sheets_pdf (017)."""
import json

import pytest
from tools.pdf_export_tools import export_timeout, register_pdf_export_tools

DEFAULTS = {"name_pattern": "{project_number}_{sheet_number}_{revision}_{date}",
            "combine": False, "paper_format": "default", "color": "color",
            "raster_quality": "high", "hide_crop_boundaries": True, "hide_scope_boxes": True,
            "hide_reference_planes": True, "hide_unreferenced_view_tags": True,
            "overwrite": False, "dry_run": True}


@pytest.fixture
def tools(mock_mcp, mock_revit_get, mock_revit_post):
    mock_revit_post.return_value = {"status": "dry_run", "message": "Dry run", "files": []}
    register_pdf_export_tools(mock_mcp, mock_revit_get, mock_revit_post)
    return mock_mcp.tools["export_sheets_pdf"], mock_revit_post


async def test_default_payload_is_a_dry_run_without_none(tools):
    tool, post = tools
    result = await tool(ctx=None)
    post.assert_called_once_with("/export_sheets_pdf/", DEFAULTS, None, timeout=120.0)
    assert "DRY RUN" in result


async def test_all_options_are_sent(tools):
    tool, post = tools
    await tool(sheets=["A-101", u"A-102"], folder="D:\\Projetos\\Casa Árvore\\Emissão",
               name_pattern="{sheet_number} - {sheet_name}", combine=True,
               paper_format="ISO_A1", zoom_percent=100, color="black_line",
               raster_quality="presentation", hide_scope_boxes=False, overwrite=True,
               dry_run=False, issued_at=" 2026-10-21T09:05:12 ", ctx=None)
    payload = post.call_args.args[1]
    assert payload["issued_at"] == "2026-10-21T09:05:12"
    assert post.call_args.kwargs["timeout"] == 600.0
    assert payload["sheets"] == ["A-101", "A-102"]
    assert payload["folder"] == "D:\\Projetos\\Casa Árvore\\Emissão"
    assert payload["zoom_percent"] == 100 and payload["paper_format"] == "ISO_A1"
    assert payload["combine"] is True and payload["dry_run"] is False
    assert payload["hide_scope_boxes"] is False and payload["overwrite"] is True
    assert "sheet_set" not in payload


async def test_sheet_set_is_sent(tools):
    tool, post = tools
    await tool(sheet_set="Emissão executivo", ctx=None)
    payload = post.call_args.args[1]
    assert payload["sheet_set"] == "Emissão executivo" and "sheets" not in payload


async def test_partial_failure_keeps_the_written_files(tools):
    tool, post = tools
    post.return_value = {"status": "error", "error": "1 of 2 PDF file(s) failed",
                         "files": [{"file": "P01_A-101.pdf", "size_bytes": 10}],
                         "errors": [{"file": "P01_A-102.pdf", "error": "x"}]}
    result = json.loads(await tool(dry_run=False, ctx=None))
    assert result["files"][0]["file"] == "P01_A-101.pdf"


@pytest.mark.parametrize("kwargs, message", [
    ({"sheets": ["A-1"], "sheet_set": "x"}, "not both"),
    ({"sheets": []}, "at least one"),
    ({"sheets": ["A"] * 201}, "at most 200"),
    ({"sheets": ["A-1", " "]}, "non-empty"),
    ({"sheet_set": " "}, "sheet_set"),
    ({"folder": "\\\\servidor\\emissao"}, "UNC"),
    ({"folder": "//servidor/emissao"}, "UNC"),
    ({"folder": "Emissao"}, "absolute"),
    ({"folder": "D:\\"}, "root"),
    ({"folder": "D:\\Projetos\\..\\Windows"}, "'..'"),
    ({"name_pattern": "{sheet}"}, "unknown token"),
    ({"name_pattern": "fixo"}, "at least one"),
    ({"name_pattern": "{sheet_number}/{revision}"}, "not allowed"),
    ({"name_pattern": "{sheet_number}}"}, "unbalanced"),
    ({"color": "cor"}, "color"),
    ({"raster_quality": "max"}, "raster_quality"),
    ({"zoom_percent": 5}, "zoom_percent"),
    ({"paper_format": "A1 grande"}, "paper_format"),
    ({"issued_at": "21/10/2026"}, "issued_at"),
    ({"sheets": ["A"] * 61, "dry_run": False}, "at most 60"),
])
async def test_rejects_bad_input_without_calling_revit(tools, kwargs, message):
    tool, post = tools
    result = await tool(ctx=None, **kwargs)
    assert result.startswith("Error:") and message in result, result
    post.assert_not_called()


async def test_dry_run_may_list_more_than_60_and_combined_real_run_too(tools):
    tool, post = tools
    await tool(sheets=["A-{}".format(i) for i in range(61)], ctx=None)
    await tool(sheets=["A-{}".format(i) for i in range(61)], combine=True, dry_run=False, ctx=None)
    assert post.call_count == 2
    assert post.call_args.kwargs["timeout"] == 1220.0


def test_export_timeout():
    assert export_timeout(True, 50) == 120.0
    assert export_timeout(False, 10) == 600.0
    assert export_timeout(False, 60) == 1200.0
    assert export_timeout(False, None) == 1800.0
