"""Export/check Autodesk's inventory against the registered FastMCP server."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import anyio
from mcp.types import LATEST_PROTOCOL_VERSION
from main import mcp


async def build_manifest():
    tools = await mcp.list_tools()
    resources = await mcp.list_resources()
    templates = await mcp.list_resource_templates()
    prompts = await mcp.list_prompts()
    return {
        "mcp_manifest_version": "1.0",
        "app_model": "A",
        "mcp_spec_version": LATEST_PROTOCOL_VERSION,
        "server": {"name": mcp.name, "transport": "stdio"},
        "tools": [{"name": tool.name, "description": tool.description}
                  for tool in sorted(tools, key=lambda item: item.name)],
        "resources": [item.model_dump(mode="json", exclude_none=True)
                      for item in resources + templates],
        "prompts": [item.model_dump(mode="json", exclude_none=True)
                    for item in prompts],
        # No fixed outbound internet services in the built-in bridge/client.
        # Supplied Python code can initiate arbitrary external communication:
        # the declaration draft records this unresolved certification gate.
        "external_endpoints": [],
        "autodesk_apis_used": [
            "Revit API - Document and project information, levels, rooms, elements and parameters",
            "Revit API - Views, sheets, ImageExportOptions and view graphic overrides",
            "Revit API - Family symbols, family instance placement and Transactions",
            "Revit API - UIDocument Selection and element/type parameter values",
            "Revit API - Parameter Set/SetValueString in a single Transaction (bulk edits)",
            "Revit API - Document warnings, ImportInstance, Family, View, Viewport, RevitLinkType and GroupType collectors (model health)",
            "Revit API - OpenAndActivateDocument, Document.Close, Save, SaveAs and SynchronizeWithCentral",
            "Revit API - ViewSheet.Create, title block FamilySymbol, Viewport.CanAddViewToSheet/Create and ScheduleSheetInstance.Create in a single Transaction (sheets)",
            "Revit API - View.IsValidViewTemplate and View.ViewTemplateId in a single Transaction (view templates)",
            "Revit API - IndependentTag.Create, NewRoomTag, NewAreaTag and GetDefaultFamilyTypeId in a single Transaction (tagging)",
            "Revit API - ViewSchedule TableData/GetSectionData/GetCellText and Room area, perimeter and volume (data export)",
            "Revit API - Wall.Create, WallType, WallUtils, Document.Create.NewRoom/NewRoomBoundaryLines/NewRoomTag, SketchPlane and NewFamilyInstance on wall hosts (modeling)",
            "Revit API - TransactionGroup Assimilate/RollBack and IFailuresPreprocessor/FailuresAccessor (dry-run validation and warning handling)",
            "Revit API - BrowserOrganization, PhaseFilter, ParameterFilterElement/SelectionFilterElement, line style subcategories, LinePattern/FillPatternElement, DimensionType/TextNoteType, SharedParameterElement and ParameterBindings, export setting collectors and Document.GetUnusedElements (template audit)",
            "Revit API - ViewSheet, Viewport, ScheduleSheetInstance and view-scoped FilteredElementCollector (sheet checks)",
            "Revit API - Element.GetMaterialIds, Category.Material, Element geometry solids and FamilyInstance.GetSubComponentIds (render material checks)",
            "Revit API - Room.GetBoundarySegments, Room.IsPointInRoom/UnboundedHeight, FamilyInstance FromRoom/ToRoom per Phase, door/window size and wall area/volume parameters (quantity takeoff)",
            "Revit API - View-scoped FilteredElementCollector, ViewSheet.IsPlaceholder, FamilyInstance.get_ToRoom/get_FromRoom/get_Room(phase), Element.CreatedPhaseId/GroupId, ROOM_PHASE/VIEW_PHASE and Parameter.Set on ALL_MODEL_MARK/ROOM_NUMBER/SHEET_NUMBER (renumbering)",
            "Revit API - Document.Export(String, IList<ElementId>, PDFExportOptions), PDFExportOptions, ViewSheetSet, ViewSheet.IsPlaceholder/CanBePrinted, sheet revision parameters and System.IO drive/attribute checks (PDF issue)",
            "Revit API - Document.Phases, PhaseFilter.GetPhaseStatusPresentation, Element.HasPhases/ArePhasesModifiable/IsPhaseCreatedValid/IsPhaseDemolishedValid/CreatedPhaseId/DemolishedPhaseId and ElementMulticategoryFilter (phases)",
            "Revit API - Floor.Create / Ceiling.Create (2022+) with IList<CurveLoop> from Room.GetBoundarySegments, BoundaryValidation, CreatedPhaseId/IsPhaseCreatedValid and ROOM_PHASE/ROOM_UPPER_LEVEL/ROOM_LOWER_OFFSET (floors and ceilings from rooms)",
            "Revit API - User-supplied IronPython can invoke additional APIs under user review",
        ],
        "ai_llm_providers": [],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "mcp-tool-manifest.json")
    args = parser.parse_args()
    manifest = anyio.run(build_manifest)
    if args.check:
        if json.loads(args.output.read_text(encoding="utf-8")) != manifest:
            raise SystemExit("Manifest does not match the registered MCP inventory.")
        print("Manifest matches {} tools, {} resources and {} prompts.".format(
            len(manifest["tools"]), len(manifest["resources"]), len(manifest["prompts"])))
    else:
        args.output.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
