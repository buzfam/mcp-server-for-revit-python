# -*- coding: UTF-8 -*-
"""
Bulk parameter editing
Applies many parameter changes in a single, all-or-nothing transaction
"""

from pyrevit import routes, DB
import json
import logging
from .security import authorize_request
from .utils import normalize_string, element_id_from_int

logger = logging.getLogger(__name__)

MAX_CHANGES = 500
TRUE_WORDS = {"true", "yes", "sim", "s", "y", "1"}
FALSE_WORDS = {"false", "no", "nao", u"não", "n", "0"}


def parse_bool(value):
    """Yes/No value from bool, 0/1 or words (pt/en). None if not a boolean."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, long)) and value in (0, 1):
        return bool(value)
    if isinstance(value, basestring):
        word = value.strip().lower()
        if word in TRUE_WORDS:
            return True
        if word in FALSE_WORDS:
            return False
    return None


def validate_change(change):
    """Shape check of one change dict. Returns an error message or None."""
    if not isinstance(change, dict):
        return "Each change must be an object"
    try:
        int(change.get("element_id"))
    except (TypeError, ValueError):
        return "element_id must be an integer"
    if not isinstance(change.get("parameter"), basestring) or not change["parameter"].strip():
        return "parameter must be a non-empty name"
    if "value" not in change or change["value"] is None:
        return "value is required"
    return None


def _shown(parameter):
    if parameter.StorageType == DB.StorageType.String:
        value = parameter.AsString()
    else:
        value = parameter.AsValueString()
    return normalize_string(value) if value is not None else None


def _builtin_parameter(name):
    """BuiltInParameter for an API name like ALL_MODEL_INSTANCE_COMMENTS, else None."""
    if not isinstance(name, basestring) or not name or name.upper() != name or " " in name:
        return None
    builtin = getattr(DB.BuiltInParameter, str(name), None)
    if builtin is None or builtin == getattr(DB.BuiltInParameter, "INVALID", None):
        return None
    return builtin


def _resolve(doc, change, use_type):
    """Find the parameter to change. Returns (parameter, error)."""
    element = doc.GetElement(element_id_from_int(int(change["element_id"])))
    if element is None:
        return None, "Element not found"
    if use_type:
        type_id = element.GetTypeId()
        if type_id == DB.ElementId.InvalidElementId:
            return None, "Element has no type"
        element = doc.GetElement(type_id)

    name = change["parameter"]
    matches = list(element.GetParameters(name))
    if not matches:
        # Names follow the Revit language ("Comentários"); the BuiltInParameter
        # name reported by get_element_parameters ("ALL_MODEL_INSTANCE_COMMENTS")
        # works in any language.
        builtin = _builtin_parameter(name)
        parameter = element.get_Parameter(builtin) if builtin is not None else None
        if parameter is not None:
            matches = [parameter]
    if not matches:
        return None, "Parameter not found on the {}".format("type" if use_type else "element")
    if len(matches) > 1:
        return None, "{} parameters named '{}'; rename one to disambiguate".format(
            len(matches), name)
    parameter = matches[0]
    if parameter.IsReadOnly:
        return None, "Parameter is read-only"

    storage = parameter.StorageType
    value = change["value"]
    if storage == DB.StorageType.ElementId:
        return None, "ElementId parameters are not supported yet"
    if storage == DB.StorageType.Integer and parse_bool(value) is None:
        try:
            int(value)
        except (TypeError, ValueError):
            return None, "Integer parameter needs a whole number or yes/no"
    if storage not in (DB.StorageType.String, DB.StorageType.Integer, DB.StorageType.Double):
        return None, "Parameter has no storage type"
    return parameter, None


def _set(parameter, value):
    """Apply value according to storage type. Returns True when Revit accepts it."""
    storage = parameter.StorageType
    if storage == DB.StorageType.String:
        return parameter.Set(normalize_string(value) if isinstance(value, basestring)
                             else unicode(value))
    if storage == DB.StorageType.Integer:
        as_bool = parse_bool(value)
        return parameter.Set(int(as_bool) if as_bool is not None else int(value))
    # Double: text in project units ("110 cm", "2.40"); Revit converts.
    return parameter.SetValueString(unicode(value))


def apply_changes(doc, changes, use_type, commit):
    """Validate everything, then apply in one transaction (rolled back when
    commit is False or anything fails). Returns the response dict."""
    errors = []
    resolved = []
    for index, change in enumerate(changes):
        parameter, error = _resolve(doc, change, use_type)
        if error:
            errors.append({"index": index, "element_id": change.get("element_id"),
                           "parameter": change.get("parameter"), "error": error})
        else:
            resolved.append((index, change, parameter))

    if errors:
        return {"status": "error",
                "error": "{} of {} changes are invalid; nothing was applied".format(
                    len(errors), len(changes)),
                "applied": 0, "errors": errors}

    results = []
    transaction = DB.Transaction(doc, "MCP: bulk set parameters")
    transaction.Start()
    try:
        for index, change, parameter in resolved:
            before = _shown(parameter)
            accepted = _set(parameter, change["value"])
            if not accepted:
                errors.append({"index": index, "element_id": change["element_id"],
                               "parameter": change["parameter"],
                               "error": "Revit rejected the value '{}'".format(change["value"])})
            results.append({"index": index, "element_id": change["element_id"],
                            "parameter": change["parameter"], "before": before,
                            "after": _shown(parameter), "ok": bool(accepted)})

        if errors or not commit:
            transaction.RollBack()
        else:
            status = transaction.Commit()
            if status != DB.TransactionStatus.Committed:
                return {"status": "error", "applied": 0, "results": results, "errors": [],
                        "error": "Revit did not commit the transaction ({})".format(status)}
    except Exception:
        if transaction.HasStarted() and not transaction.HasEnded():
            transaction.RollBack()
        raise

    if errors:
        return {"status": "error",
                "error": "{} of {} values were rejected by Revit; nothing was applied".format(
                    len(errors), len(changes)),
                "applied": 0, "results": results, "errors": errors}
    if not commit:
        return {"status": "dry_run",
                "message": "Dry run: {} changes are valid. Nothing was changed; call again "
                           "with dry_run=false to apply.".format(len(results)),
                "applied": 0, "results": results}
    return {"status": "success", "applied": len(results), "results": results}


def register_parameter_routes(api):
    """Register bulk parameter routes with the API"""

    @api.route("/bulk_set_parameters/", methods=["POST"])
    def bulk_set_parameters(doc, request):
        """
        Set many parameter values at once (all or nothing).

        Expected JSON payload:
        {
            "dry_run": true,
            "use_type": false,
            "changes": [{"element_id": 123, "parameter": "Mark", "value": "P01"}]
        }
        """
        # Authenticate first; the review dialog is shown below only for real writes.
        denial = authorize_request(request, action=None)
        if denial is not None:
            return denial
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )
            data = json.loads(request.data) if isinstance(request.data, str) else request.data
            data = data or {}
            dry_run = data.get("dry_run", True) is not False
            use_type = bool(data.get("use_type", False))
            changes = data.get("changes") or []

            if not isinstance(changes, list) or not changes:
                return routes.make_response(
                    data={"error": "changes must be a non-empty list"}, status=400)
            if len(changes) > MAX_CHANGES:
                return routes.make_response(
                    data={"error": "At most {} changes per call, got {}".format(
                        MAX_CHANGES, len(changes))}, status=400)
            shape_errors = []
            for index, change in enumerate(changes):
                error = validate_change(change)
                if error:
                    shape_errors.append({"index": index, "error": error})
            if shape_errors:
                return routes.make_response(
                    data={"error": "Invalid changes", "errors": shape_errors}, status=400)

            if not dry_run:
                denial = authorize_request(request, action="Set element parameters")
                if denial is not None:
                    return denial

            return routes.make_response(
                data=apply_changes(doc, changes, use_type, commit=not dry_run))

        except Exception as e:
            logger.error("Error in bulk_set_parameters route: %s", e)
            return routes.make_response(data={"error": str(e)}, status=500)
