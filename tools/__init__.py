# -*- coding: utf-8 -*-
"""Tool registration system for Revit MCP Server"""


def register_tools(mcp_server, revit_get_func, revit_post_func, revit_image_func):
    """Register all tools with the MCP server"""
    from .registration import ReviewedTools
    mcp_server = ReviewedTools(mcp_server)
    # Import all tool modules
    from .status_tools import register_status_tools
    from .view_tools import register_view_tools
    from .family_tools import register_family_tools
    from .model_tools import register_model_tools
    from .colors_tools import register_colors_tools
    from .code_execution_tools import register_code_execution_tools
    from .launch_tools import register_launch_tools
    from .document_tools import register_document_tools
    from .selection_tools import register_selection_tools
    from .parameter_tools import register_parameter_tools
    from .health_tools import register_health_tools
    from .sheet_tools import register_sheet_tools
    from .annotation_tools import register_annotation_tools
    from .export_tools import register_export_tools
    from .modeling_tools import register_modeling_tools
    from .audit_tools import register_audit_tools
    from .sheet_check_tools import register_sheet_check_tools
    from .material_check_tools import register_material_check_tools
    from .quantity_tools import register_quantity_tools
    from .renumber_tools import register_renumber_tools
    from .pdf_export_tools import register_pdf_export_tools
    from .phase_tools import register_phase_tools
    from .finish_element_tools import register_finish_element_tools

    # Register tools from each module
    register_status_tools(mcp_server, revit_get_func)
    register_view_tools(mcp_server, revit_get_func, revit_post_func, revit_image_func)
    register_family_tools(mcp_server, revit_get_func, revit_post_func)
    register_model_tools(mcp_server, revit_get_func)
    register_colors_tools(mcp_server, revit_get_func, revit_post_func)
    register_code_execution_tools(
        mcp_server, revit_get_func, revit_post_func, revit_image_func
    )
    register_launch_tools(mcp_server, revit_get_func)
    register_document_tools(mcp_server, revit_get_func, revit_post_func)
    register_selection_tools(mcp_server, revit_get_func, revit_post_func)
    register_parameter_tools(mcp_server, revit_get_func, revit_post_func)
    register_health_tools(mcp_server, revit_get_func, revit_post_func)
    register_sheet_tools(mcp_server, revit_get_func, revit_post_func)
    register_annotation_tools(mcp_server, revit_get_func, revit_post_func)
    register_export_tools(mcp_server, revit_get_func, revit_post_func)
    register_modeling_tools(mcp_server, revit_get_func, revit_post_func)
    register_audit_tools(mcp_server, revit_get_func, revit_post_func)
    register_sheet_check_tools(mcp_server, revit_get_func, revit_post_func)
    register_material_check_tools(mcp_server, revit_get_func, revit_post_func)
    register_quantity_tools(mcp_server, revit_get_func, revit_post_func)
    register_renumber_tools(mcp_server, revit_get_func, revit_post_func)
    register_pdf_export_tools(mcp_server, revit_get_func, revit_post_func)
    register_phase_tools(mcp_server, revit_get_func, revit_post_func)
    register_finish_element_tools(mcp_server, revit_get_func, revit_post_func)
