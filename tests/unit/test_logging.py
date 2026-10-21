"""revit_mcp logs go to a file, never up to pyRevit's output window."""
import logging

import revit_mcp


def test_package_logger_does_not_propagate_and_has_one_file_handler(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    target = logging.getLogger("revit_mcp_test_target")
    target.handlers = []
    revit_mcp._keep_logs_out_of_pyrevit_window(target)
    revit_mcp._keep_logs_out_of_pyrevit_window(target)  # pyRevit reload
    assert target.propagate is False
    assert len(target.handlers) == 1
    logging.getLogger("revit_mcp_test_target.child").error(u"falhou: Comentários")
    target.handlers[0].close()
    text = (tmp_path / "RevitMCPPython" / "revit-mcp.log").read_text(encoding="utf-8")
    assert u"falhou: Comentários" in text


def test_real_package_logger_is_detached():
    assert logging.getLogger("revit_mcp").propagate is False
