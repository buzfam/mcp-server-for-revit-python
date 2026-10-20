# -*- coding: UTF-8 -*-
"""
Selection and parameter inspection routes
Lets the LLM see what the user selected and read element parameters
"""

from pyrevit import routes, DB
import json
import logging
from .security import authorize_request
from .utils import (
    normalize_string,
    element_id_value,
    element_id_from_int,
    get_element_name,
)

logger = logging.getLogger(__name__)

MAX_SELECTION_LIMIT = 1000
MAX_PARAMETER_ELEMENTS = 50


def _parse_body(request):
    if not request or not request.data:
        return {}
    if isinstance(request.data, str):
        return json.loads(request.data)
    return request.data


def _element_summary(doc, element, level_cache):
    """Identity of an element: id, category, family, type, level."""
    category = element.Category
    info = {
        "element_id": element_id_value(element.Id),
        "name": normalize_string(get_element_name(element)),
        "category": normalize_string(category.Name) if category else None,
        "family": None,
        "type": None,
        "level": None,
        "view_specific": bool(element.ViewSpecific),
    }

    type_id = element.GetTypeId()
    if type_id != DB.ElementId.InvalidElementId:
        element_type = doc.GetElement(type_id)
        if element_type is not None:
            info["type"] = normalize_string(get_element_name(element_type))
            family_name = getattr(element_type, "FamilyName", None)
            if family_name:
                info["family"] = normalize_string(family_name)

    level_id = element.LevelId
    if level_id is not None and level_id != DB.ElementId.InvalidElementId:
        key = element_id_value(level_id)
        if key not in level_cache:
            level = doc.GetElement(level_id)
            level_cache[key] = (
                normalize_string(get_element_name(level)) if level else None
            )
        info["level"] = level_cache[key]

    return info


def _builtin_name(parameter):
    try:
        builtin = parameter.Definition.BuiltInParameter
    except AttributeError:
        return None
    if builtin == DB.BuiltInParameter.INVALID:
        return None
    return str(builtin)


def _parameter_info(doc, parameter):
    storage = parameter.StorageType
    value = None
    display = None

    if storage == DB.StorageType.String:
        value = parameter.AsString()
        display = value
    elif storage == DB.StorageType.Integer:
        value = parameter.AsInteger()
        display = parameter.AsValueString()
    elif storage == DB.StorageType.Double:
        value = parameter.AsDouble()
        display = parameter.AsValueString()
    elif storage == DB.StorageType.ElementId:
        ref_id = parameter.AsElementId()
        if ref_id is not None and ref_id != DB.ElementId.InvalidElementId:
            value = element_id_value(ref_id)
            ref = doc.GetElement(ref_id)
            display = (
                normalize_string(get_element_name(ref))
                if ref is not None
                else parameter.AsValueString()
            )

    return {
        "name": normalize_string(parameter.Definition.Name),
        "value": normalize_string(value) if isinstance(value, basestring) else value,
        "display": normalize_string(display) if display is not None else None,
        "storage": str(storage),
        "read_only": bool(parameter.IsReadOnly),
        "shared": bool(parameter.IsShared),
        "builtin": _builtin_name(parameter),
    }


def _parameters(doc, element, name_filter):
    result = []
    for parameter in element.Parameters:
        try:
            name = parameter.Definition.Name
            if name_filter and name_filter not in name.lower():
                continue
            result.append(_parameter_info(doc, parameter))
        except Exception as e:
            logger.warning("Could not read parameter: %s", e)
    result.sort(key=lambda item: item["name"].lower())
    return result


def register_selection_routes(api):
    """Register selection and parameter routes with the API"""

    @api.route("/selection/", methods=["POST"])
    def get_selected_elements(doc, uidoc, request):
        """
        Get the elements currently selected by the user.

        Expected JSON payload (all fields optional):
        {
            "limit": 200
        }
        """
        denial = authorize_request(request, action=None)
        if denial is not None:
            return denial
        try:
            if not doc or not uidoc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            data = _parse_body(request)
            limit = int(data.get("limit", 200))
            if limit < 1 or limit > MAX_SELECTION_LIMIT:
                return routes.make_response(
                    data={"error": "limit must be between 1 and {}".format(
                        MAX_SELECTION_LIMIT)},
                    status=400,
                )

            selected_ids = list(uidoc.Selection.GetElementIds())
            level_cache = {}
            elements = []
            for element_id in selected_ids[:limit]:
                element = doc.GetElement(element_id)
                if element is None:
                    continue
                try:
                    elements.append(_element_summary(doc, element, level_cache))
                except Exception as e:
                    logger.warning("Could not describe element: %s", e)

            return routes.make_response(
                data={
                    "status": "success",
                    "document": normalize_string(doc.Title),
                    "total": len(selected_ids),
                    "returned": len(elements),
                    "truncated": len(selected_ids) > limit,
                    "elements": elements,
                }
            )

        except Exception as e:
            logger.error("Error in selection route: %s", e)
            return routes.make_response(data={"error": str(e)}, status=500)

    @api.route("/element_parameters/", methods=["POST"])
    def get_element_parameters(doc, uidoc, request):
        """
        Read instance (and optionally type) parameters of elements.

        Expected JSON payload (all fields optional):
        {
            "element_ids": [123456, 123457],   // default: current selection
            "include_type": false,
            "name_filter": "fire"              // case-insensitive substring
        }
        """
        denial = authorize_request(request, action=None)
        if denial is not None:
            return denial
        try:
            if not doc or not uidoc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            data = _parse_body(request)
            include_type = bool(data.get("include_type", False))
            name_filter = data.get("name_filter")
            name_filter = name_filter.lower() if name_filter else None

            raw_ids = data.get("element_ids")
            if raw_ids:
                ids = [int(value) for value in raw_ids]
            else:
                ids = [
                    element_id_value(element_id)
                    for element_id in uidoc.Selection.GetElementIds()
                ]
                if not ids:
                    return routes.make_response(
                        data={"error": "No element_ids and nothing selected"},
                        status=400,
                    )

            if len(ids) > MAX_PARAMETER_ELEMENTS:
                return routes.make_response(
                    data={"error": "At most {} elements per call, got {}".format(
                        MAX_PARAMETER_ELEMENTS, len(ids))},
                    status=400,
                )

            elements = []
            not_found = []
            for value in ids:
                element = doc.GetElement(element_id_from_int(value))
                if element is None:
                    not_found.append(value)
                    continue

                category = element.Category
                info = {
                    "element_id": value,
                    "name": normalize_string(get_element_name(element)),
                    "category": normalize_string(category.Name) if category else None,
                    "type_id": None,
                    "instance": _parameters(doc, element, name_filter),
                }

                type_id = element.GetTypeId()
                if type_id != DB.ElementId.InvalidElementId:
                    info["type_id"] = element_id_value(type_id)
                    if include_type:
                        element_type = doc.GetElement(type_id)
                        if element_type is not None:
                            info["type"] = _parameters(doc, element_type, name_filter)

                elements.append(info)

            return routes.make_response(
                data={
                    "status": "success",
                    "elements": elements,
                    "not_found": not_found,
                }
            )

        except Exception as e:
            logger.error("Error in element_parameters route: %s", e)
            return routes.make_response(data={"error": str(e)}, status=500)
