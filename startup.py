# -*- coding: UTF-8 -*-
"""
Revit MCP Extension Startup
Registers all MCP routes and initializes the API
"""

from pyrevit import routes
import importlib
import logging

logger = logging.getLogger(__name__)

# Initialize the main API
api = routes.API("revit_mcp")

# (module, register function). Each module is registered on its own, so one
# that fails to import (e.g. a Revit API missing in an older version) does not
# take the routes of every module after it down with it.
ROUTE_MODULES = [
    ("revit_mcp.status", "register_status_routes"),
    ("revit_mcp.model_info", "register_model_info_routes"),
    ("revit_mcp.views", "register_views_routes"),
    ("revit_mcp.placement", "register_placement_routes"),
    ("revit_mcp.colors", "register_color_routes"),
    ("revit_mcp.code_execution", "register_code_execution_routes"),
    ("revit_mcp.document", "register_document_routes"),
    ("revit_mcp.selection", "register_selection_routes"),
    ("revit_mcp.parameters", "register_parameter_routes"),
    ("revit_mcp.health", "register_health_routes"),
    ("revit_mcp.sheets", "register_sheet_routes"),
    ("revit_mcp.annotation", "register_annotation_routes"),
    ("revit_mcp.exports", "register_export_routes"),
    ("revit_mcp.modeling", "register_modeling_routes"),
    ("revit_mcp.audit", "register_audit_routes"),
    ("revit_mcp.sheet_checks", "register_sheet_check_routes"),
    ("revit_mcp.material_checks", "register_material_check_routes"),
    ("revit_mcp.quantities", "register_quantity_routes"),
    ("revit_mcp.renumber", "register_renumber_routes"),
    ("revit_mcp.pdf_export", "register_pdf_export_routes"),
    ("revit_mcp.phases", "register_phase_routes"),
    ("revit_mcp.finish_elements", "register_finish_element_routes"),
]


def register_routes():
    """Register all MCP route modules; returns the modules that failed."""
    failed = []
    for module_name, function_name in ROUTE_MODULES:
        try:
            module = importlib.import_module(module_name)
            getattr(module, function_name)(api)
        except Exception as e:
            logger.error("Failed to register %s: %s", module_name, e)
            failed.append(module_name)
    if failed:
        logger.error("MCP routes registered except: %s", ", ".join(failed))
    else:
        logger.info("All MCP routes registered successfully")
    return failed


# Register all routes when the extension loads
register_routes()
