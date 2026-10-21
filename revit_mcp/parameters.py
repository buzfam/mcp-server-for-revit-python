# -*- coding: UTF-8 -*-
"""
Bulk parameter editing
Applies many parameter changes in a single, all-or-nothing transaction
"""

from pyrevit import routes, DB
import json
import logging
from .security import authorize_request
import re
from .utils import normalize_string, element_id_from_int, element_id_value

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


_NUMBER = re.compile(u"^([+-]?)(\\d(?:[\\d.,]*\\d)?)(\\s*[^\\d\\s.,][^\\d.,]*)?$", re.UNICODE)


def is_blank(value):
    """True for an empty or whitespace-only text value."""
    return isinstance(value, basestring) and not value.strip()


def normalize_number_text(value, decimal_symbol):
    """Rewrite a number typed with ',' or '.' as decimal separator in the
    project's format ("2,40" -> "2.40" when decimal_symbol is ".").

    Both separators present: the last one is the decimal separator and the
    other is digit grouping ("1.234,5", "1,234.5"). One separator used once:
    it is the decimal separator. Used more than once: grouping ("1.234.567").
    A unit suffix is kept ("2,40 m"). Anything else is returned unchanged.
    """
    if not isinstance(value, basestring):
        return value
    match = _NUMBER.match(value.strip())
    if not match:
        return value
    sign, number, suffix = match.groups()
    dots, commas = number.count("."), number.count(",")
    if dots and commas:
        decimal = "." if number.rfind(".") > number.rfind(",") else ","
        if number.count(decimal) > 1:
            return value
        grouping = "," if decimal == "." else "."
        whole, fraction = number.replace(grouping, "").split(decimal)
    elif dots + commas == 1:
        whole, fraction = number.replace(",", ".").split(".")
    elif dots + commas > 1:
        whole, fraction = number.replace(".", "").replace(",", ""), ""
    else:
        return value
    text = sign + whole + (decimal_symbol + fraction if fraction else "") + (suffix or "")
    return text if text != value.strip() else value


def group_changes(entries):
    """Find changes that hit the same parameter of the same element (or type).

    entries: list of (index, key, value). Returns (keep, collapsed, conflicts):
    keep is the set of indexes to apply, collapsed a list of
    {"index", "same_as"} for repeats with the same value, conflicts a list of
    index lists that give the same target different values.
    """
    groups = {}
    order = []
    for index, key, value in entries:
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append((index, value))
    keep, collapsed, conflicts = set(), [], []
    for key in order:
        items = groups[key]
        first = items[0][0]
        values = set(_comparable(value) for _, value in items)
        if len(values) > 1:
            conflicts.append([index for index, _ in items])
            continue
        keep.add(first)
        for index, _ in items[1:]:
            collapsed.append({"index": index, "same_as": first})
    return keep, collapsed, conflicts


def _comparable(value):
    if isinstance(value, basestring):
        return value.strip()
    return unicode(value)


def project_decimal_symbol(doc):
    """',' or '.' from the project units (Manage > Project Units); '.' if unknown."""
    try:
        symbol = unicode(doc.GetUnits().DecimalSymbol)
    except Exception:
        return "."
    return "," if "comma" in symbol.lower() else "."


def _safe_id(element_id):
    try:
        return element_id_value(element_id)
    except Exception:
        return unicode(element_id)


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
    """Find the parameter to change. Returns (parameter, error, target) where
    target identifies the element (or type) that is written."""
    element = doc.GetElement(element_id_from_int(int(change["element_id"])))
    if element is None:
        return None, "Element not found", None
    target = int(change["element_id"])
    if use_type:
        type_id = element.GetTypeId()
        if type_id == DB.ElementId.InvalidElementId:
            return None, "Element has no type", None
        target = _safe_id(type_id)
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
        return None, "Parameter not found on the {}".format("type" if use_type else "element"), None
    if len(matches) > 1:
        return None, "{} parameters named '{}'; rename one to disambiguate".format(
            len(matches), name), None
    parameter = matches[0]
    if parameter.IsReadOnly:
        return None, "Parameter is read-only", None

    storage = parameter.StorageType
    value = change["value"]
    if storage == DB.StorageType.ElementId:
        return None, "ElementId parameters are not supported yet", None
    if storage == DB.StorageType.Integer and not is_blank(value) and parse_bool(value) is None:
        try:
            int(value)
        except (TypeError, ValueError):
            return None, "Integer parameter needs a whole number or yes/no", None
    if storage not in (DB.StorageType.String, DB.StorageType.Integer, DB.StorageType.Double):
        return None, "Parameter has no storage type", None
    return parameter, None, target


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


def apply_changes(doc, changes, use_type, commit, normalize_numbers=False):
    """Validate everything, then apply in one transaction (rolled back when
    commit is False or anything fails). Returns the response dict.

    Empty text for a non-text parameter is skipped with a warning. Repeated
    changes to the same parameter of the same element (or type, with
    use_type) are applied once when the values match and rejected when they
    differ. normalize_numbers rewrites number text of Double parameters to
    the project's decimal symbol."""
    errors = []
    warnings = []
    candidates = []
    decimal_symbol = project_decimal_symbol(doc) if normalize_numbers else None
    normalized = 0
    for index, change in enumerate(changes):
        parameter, error, target = _resolve(doc, change, use_type)
        if error:
            errors.append({"index": index, "element_id": change.get("element_id"),
                           "parameter": change.get("parameter"), "error": error})
            continue
        value = change["value"]
        storage = parameter.StorageType
        if is_blank(value) and storage != DB.StorageType.String:
            warnings.append({"index": index, "element_id": change["element_id"],
                             "parameter": change["parameter"],
                             "warning": "Empty value skipped: the parameter is not text"})
            continue
        if decimal_symbol and storage == DB.StorageType.Double:
            converted = normalize_number_text(value, decimal_symbol)
            if converted != value:
                normalized += 1
                value = converted
        parameter_id = getattr(parameter, "Id", None)
        key = (target, _safe_id(parameter_id) if parameter_id is not None
               else change["parameter"].lower())
        candidates.append((index, change, parameter, value, key))

    keep, collapsed, conflicts = group_changes(
        [(index, key, value) for index, _, _, value, key in candidates])
    for indexes in conflicts:
        for index in indexes:
            errors.append({
                "index": index, "element_id": changes[index].get("element_id"),
                "parameter": changes[index].get("parameter"),
                "error": "Changes {} set different values on the same {} parameter".format(
                    indexes, "type" if use_type else "element")})
    resolved = [(index, change, parameter, value)
                for index, change, parameter, value, _ in candidates if index in keep]

    extra = {}
    if warnings:
        extra["warnings"] = warnings
    if collapsed:
        extra["collapsed"] = collapsed
    if normalize_numbers:
        extra["number_format"] = {"decimal_symbol": decimal_symbol, "normalized": normalized}

    if errors:
        errors.sort(key=lambda item: item["index"])
        result = {"status": "error",
                  "error": "{} of {} changes are invalid; nothing was applied".format(
                      len(errors), len(changes)),
                  "applied": 0, "errors": errors}
        result.update(extra)
        return result

    results = []
    transaction = DB.Transaction(doc, "MCP: bulk set parameters")
    transaction.Start()
    try:
        for index, change, parameter, value in resolved:
            before = _shown(parameter)
            accepted = _set(parameter, value)
            if not accepted:
                errors.append({"index": index, "element_id": change["element_id"],
                               "parameter": change["parameter"],
                               "error": "Revit rejected the value '{}'".format(value)})
            item = {"index": index, "element_id": change["element_id"],
                    "parameter": change["parameter"], "before": before,
                    "after": _shown(parameter), "ok": bool(accepted)}
            if value is not change["value"]:
                item["sent"] = value
            results.append(item)

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
        result = {"status": "error",
                  "error": "{} of {} values were rejected by Revit; nothing was applied".format(
                      len(errors), len(changes)),
                  "applied": 0, "results": results, "errors": errors}
    elif not commit:
        result = {"status": "dry_run",
                  "message": "Dry run: {} changes are valid. Nothing was changed; call again "
                             "with dry_run=false to apply.".format(len(results)),
                  "applied": 0, "results": results}
    else:
        result = {"status": "success", "applied": len(results), "results": results}
    result.update(extra)
    return result


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
            "normalize_numbers": false,   // number text of Double parameters -> project decimal symbol
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
            normalize_numbers = data.get("normalize_numbers", False) is True
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
                data=apply_changes(doc, changes, use_type, commit=not dry_run,
                                   normalize_numbers=normalize_numbers))

        except Exception as e:
            logger.error("Error in bulk_set_parameters route: %s", e)
            return routes.make_response(data={"error": str(e)}, status=500)
