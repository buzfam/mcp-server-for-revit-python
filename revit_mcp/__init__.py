# -*- coding: UTF-8 -*-
"""
MCP Module for Revit Integration
Contains all MCP route handlers organized by functionality
"""

__version__ = "0.1.0"
__author__ = "Juan D. Rodriguez, Jean-Marc Couffin"

# Common imports that all modules might need
import logging

# Make logger available to all submodules
logger = logging.getLogger(__name__)


def log_path():
    import os
    return os.path.join(os.environ.get("LOCALAPPDATA", ""), "RevitMCPPython", "revit-mcp.log")


def _keep_logs_out_of_pyrevit_window(target):
    """Send revit_mcp logs to a file instead of pyRevit's output window.

    pyRevit 7 forwards every standard-logging error to its log window, so each
    failed request (even a typo in code sent by the agent) popped a window in
    Revit. The agent already gets the error in the HTTP response.
    """
    import os
    target.propagate = False
    if any(getattr(handler, "_revit_mcp_file", False) for handler in target.handlers):
        return  # pyRevit reloads re-import this package
    try:
        if not os.environ.get("LOCALAPPDATA"):
            raise EnvironmentError("no LOCALAPPDATA (not Windows)")
        folder = os.path.dirname(log_path())
        if not os.path.isdir(folder):
            os.makedirs(folder)
        handler = logging.FileHandler(log_path(), encoding="utf-8", delay=True)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    except Exception:
        handler = logging.NullHandler()
    handler._revit_mcp_file = True
    handler.setLevel(logging.INFO)
    target.addHandler(handler)


_keep_logs_out_of_pyrevit_window(logger)