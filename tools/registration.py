"""Keep public tool descriptions and local consent checks consistent."""
from functools import wraps
from mcp.types import ToolAnnotations
from revit_mcp.pairing import get_token, PairingError
from .catalog import TOOL_DESCRIPTIONS

READ_ONLY = {
    "get_revit_status", "get_revit_model_info", "list_levels", "get_revit_view",
    "list_revit_views", "get_current_view_info", "get_current_view_elements",
    "list_families", "list_family_categories", "list_category_parameters",
    "list_revit_installations", "get_selected_elements", "get_element_parameters",
    "model_health_report", "export_schedule", "export_rooms",
    "audit_template", "check_sheets", "check_render_materials", "room_finishes",
    "quantities_by_rule",
    "phases_report",
}


class ReviewedTools:
    def __init__(self, server):
        self.server = server

    def tool(self, **kwargs):
        def register(function):
            @wraps(function)
            async def paired_tool(*args, **arguments):
                try:
                    get_token()
                except PairingError as error:
                    return "Error: " + str(error)
                return await function(*args, **arguments)

            name = function.__name__
            kwargs["description"] = TOOL_DESCRIPTIONS[name]
            kwargs["annotations"] = ToolAnnotations(
                readOnlyHint=name in READ_ONLY,
                destructiveHint=name not in READ_ONLY,
                openWorldHint=name in {"execute_revit_code", "sync_with_central",
                                       "open_document", "save_document", "launch_revit",
                                       "import_parameters_from_csv",
                                       "export_sheets_pdf"},
            )
            return self.server.tool(**kwargs)(paired_tool)
        return register
