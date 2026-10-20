# -*- coding: UTF-8 -*-
"""
Model health report
Warnings, imported CAD, in-place families, views off sheets, links, groups
"""

from pyrevit import routes, DB
import json
import logging
import os
from .security import authorize_request
from .utils import normalize_string, element_id_value, get_element_name

logger = logging.getLogger(__name__)

WARNINGS_MEDIUM = 200
WARNINGS_HIGH = 500
MAX_WARNING_TYPES = 20
MAX_SAMPLE_IDS = 20
VIEWS_OFF_SHEETS_RATIO = 0.5
# View types that never go on a sheet (or are sheets themselves).
NON_PLACEABLE_VIEW_TYPES = {
    "ProjectBrowser", "SystemBrowser", "Internal", "Undefined", "DrawingSheet",
    "Report", "CostReport", "LoadsReport", "ColumnSchedule", "PanelSchedule",
}


def warning_flag(total):
    if total >= WARNINGS_HIGH:
        return "high"
    if total >= WARNINGS_MEDIUM:
        return "medium"
    return "ok"


def health_flags(report):
    """Short list of problems an LLM can act on."""
    flags = []
    if report.get("warnings", {}).get("flag") in ("medium", "high"):
        flags.append("warnings_" + report["warnings"]["flag"])
    if report.get("imported_cad", {}).get("count"):
        flags.append("imported_cad")
    if report.get("in_place_families", {}).get("count"):
        flags.append("in_place_families")
    views = report.get("views", {})
    placeable = views.get("placeable") or 0
    if placeable and float(views.get("not_on_sheets", 0)) / placeable > VIEWS_OFF_SHEETS_RATIO:
        flags.append("many_views_not_on_sheets")
    if views.get("unused_templates"):
        flags.append("unused_view_templates")
    if any(link.get("status") not in ("Loaded", None) for link in report.get("revit_links", [])):
        flags.append("revit_links_not_loaded")
    return flags


def _collect(doc, cls):
    return DB.FilteredElementCollector(doc).OfClass(cls).ToElements()


def _warnings(doc, sample_n):
    groups = {}
    warnings = list(doc.GetWarnings())
    for warning in warnings:
        text = normalize_string(warning.GetDescriptionText())
        group = groups.get(text)
        if group is None:
            group = {"description": text, "count": 0, "sample_element_ids": []}
            groups[text] = group
        group["count"] += 1
        for element_id in warning.GetFailingElements():
            if len(group["sample_element_ids"]) >= sample_n:
                break
            group["sample_element_ids"].append(element_id_value(element_id))
    by_type = sorted(groups.values(), key=lambda g: -g["count"])
    return {"total": len(warnings), "flag": warning_flag(len(warnings)),
            "types": len(by_type), "by_type": by_type[:MAX_WARNING_TYPES]}


def _cad(doc, sample_n):
    imported, linked = [], 0
    for instance in _collect(doc, DB.ImportInstance):
        if instance.IsLinked:
            linked += 1
        else:
            imported.append(element_id_value(instance.Id))
    return ({"count": len(imported), "sample_element_ids": imported[:sample_n]},
            {"count": linked})


def _in_place(doc):
    by_category = {}
    count = 0
    for family in _collect(doc, DB.Family):
        if family.IsInPlace:
            count += 1
            category = family.FamilyCategory
            name = normalize_string(category.Name) if category else "Unknown"
            by_category[name] = by_category.get(name, 0) + 1
    return {"count": count, "by_category": by_category}


def _views(doc):
    on_sheets = set()
    for viewport in _collect(doc, DB.Viewport):
        on_sheets.add(element_id_value(viewport.ViewId))
    for schedule in _collect(doc, DB.ScheduleSheetInstance):
        on_sheets.add(element_id_value(schedule.ScheduleId))

    templates = set()
    used_templates = set()
    total = placeable = not_on_sheets = 0
    for view in _collect(doc, DB.View):
        if view.IsTemplate:
            templates.add(element_id_value(view.Id))
            continue
        total += 1
        template_id = view.ViewTemplateId
        if template_id is not None and template_id != DB.ElementId.InvalidElementId:
            used_templates.add(element_id_value(template_id))
        if str(view.ViewType) in NON_PLACEABLE_VIEW_TYPES or not view.CanBePrinted:
            continue
        placeable += 1
        if element_id_value(view.Id) not in on_sheets:
            not_on_sheets += 1
    return {"total": total, "placeable": placeable, "not_on_sheets": not_on_sheets,
            "templates": len(templates), "unused_templates": len(templates - used_templates)}


def _links(doc):
    links = []
    for link_type in _collect(doc, DB.RevitLinkType):
        try:
            status = str(link_type.GetLinkedFileStatus())
        except Exception:
            status = None
        links.append({"name": normalize_string(get_element_name(link_type)), "status": status})
    return links


def _groups(doc):
    model_category = DB.ElementId(DB.BuiltInCategory.OST_IOSModelGroups)
    model_types = sum(1 for group_type in _collect(doc, DB.GroupType)
                      if group_type.Category is not None
                      and group_type.Category.Id == model_category)
    return {"model_types": model_types, "instances": len(_collect(doc, DB.Group))}


def build_report(doc, sample_n):
    report = {"status": "success", "document": normalize_string(doc.Title),
              "revit_version": str(doc.Application.VersionNumber), "file_size_mb": None}
    sections = {
        "warnings": lambda: _warnings(doc, sample_n),
        "in_place_families": lambda: _in_place(doc),
        "views": lambda: _views(doc),
        "revit_links": lambda: _links(doc),
        "groups": lambda: _groups(doc),
        "design_options": lambda: len(_collect(doc, DB.DesignOption)),
    }
    errors = {}
    for name, build in sections.items():
        try:
            report[name] = build()
        except Exception as e:
            logger.warning("Health section %s failed: %s", name, e)
            errors[name] = str(e)
    try:
        report["imported_cad"], report["linked_cad"] = _cad(doc, sample_n)
    except Exception as e:
        errors["cad"] = str(e)
    try:
        path = doc.PathName
        if path and os.path.isfile(path):
            report["file_size_mb"] = round(os.path.getsize(path) / 1048576.0, 1)
    except Exception as e:
        errors["file_size_mb"] = str(e)

    report["flags"] = health_flags(report)
    if errors:
        report["section_errors"] = errors
    return report


def register_health_routes(api):
    """Register model health routes with the API"""

    @api.route("/model_health/", methods=["POST"])
    def model_health_report(doc, request):
        """
        Model health report.

        Expected JSON payload (all fields optional):
        {
            "sample_ids": 5    // element IDs per item, 0-20
        }
        """
        denial = authorize_request(request, action=None)
        if denial is not None:
            return denial
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )
            data = json.loads(request.data) if isinstance(request.data, str) else request.data
            sample_n = int((data or {}).get("sample_ids", 5))
            if sample_n < 0 or sample_n > MAX_SAMPLE_IDS:
                return routes.make_response(
                    data={"error": "sample_ids must be between 0 and {}".format(MAX_SAMPLE_IDS)},
                    status=400)
            return routes.make_response(data=build_report(doc, sample_n))
        except Exception as e:
            logger.error("Error in model_health route: %s", e)
            return routes.make_response(data={"error": str(e)}, status=500)
