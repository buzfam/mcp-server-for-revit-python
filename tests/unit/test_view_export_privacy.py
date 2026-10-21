"""Verify request isolation and cleanup using a stand-in export API."""
import ast
import base64
import logging
import os
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("fails", [False, True])
def test_view_export_uses_fresh_directory_and_cleans_it(tmp_path, monkeypatch, fails):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    stale = tmp_path / "stale.png"
    stale.write_bytes(b"different model")
    view = SimpleNamespace(Name="Test view", IsTemplate=False, ViewType="floor", Id=1)
    collector = SimpleNamespace(OfClass=lambda cls: SimpleNamespace(ToElements=lambda: [view]))

    class NetList(list):
        def Add(self, value):
            self.append(value)

    class Options:
        def SetViewsAndSheets(self, items):
            pass

    db = SimpleNamespace(
        FilteredElementCollector=lambda doc: collector, View=object, ElementId=int,
        ViewType=SimpleNamespace(Internal="internal", ProjectBrowser="browser"),
        ImageExportOptions=Options, ExportRange=SimpleNamespace(SetOfViews=1),
        ImageFileType=SimpleNamespace(PNG=1), ImageResolution=SimpleNamespace(DPI_150=1),
        ZoomFitType=SimpleNamespace(FitToPage=1),
    )
    directories = []
    def export(options):
        directory = Path(options.FilePath).parent
        assert directory != tmp_path
        directories.append(directory)
        (directory / "view.png").write_bytes(b"current model image")
        if fails:
            raise RuntimeError("Export failed after creating a file")

    tree = ast.parse((ROOT / "revit_mcp/views.py").read_text(encoding="utf-8"))
    node = next(item for item in ast.walk(tree) if isinstance(item, ast.FunctionDef) and item.name == "get_view")
    node.decorator_list = []
    namespace = {
        "authorize_request": lambda *args, **kwargs: None, "tempfile": tempfile,
        "os": os, "shutil": shutil, "base64": base64, "DB": db,
        "List": NetList, "unquote": lambda name: name, "repair_utf8": lambda name: name,
        "normalize_string": lambda text: text, "get_element_name": lambda item: item.Name,
        "logger": logging.getLogger(__name__),
        "routes": SimpleNamespace(make_response=lambda data, status=200: SimpleNamespace(data=data, status=status)),
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), "views.py", "exec"), namespace)
    result = namespace["get_view"](SimpleNamespace(ExportImage=export), "Test view", object())
    assert result.status == (500 if fails else 200)
    if not fails:
        assert base64.b64decode(result.data["image_data"]) == b"current model image"
    assert directories and all(not directory.exists() for directory in directories)
    assert stale.read_bytes() == b"different model"
