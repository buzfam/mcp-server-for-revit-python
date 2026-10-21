# -*- coding: UTF-8 -*-
"""
Sheet set-up in batches: create sheets, place views on sheets and apply a
view template to many views. Each request is one all-or-nothing transaction;
dry_run validates inside a transaction that is rolled back.
"""

from pyrevit import routes, DB
import json
import logging
from .security import authorize_request
from .utils import normalize_string, element_id_from_int, element_id_value

logger = logging.getLogger(__name__)

MAX_ITEMS = 200
FEET_PER_METER = 1.0 / 0.3048
MAX_LISTED = 20


# ---- pure helpers (no Revit API; checked by scripts/ironpython_check.py) ----

def name_key(text):
    """Case-insensitive comparison key for names typed by the user or read from Revit."""
    if text is None:
        return u""
    return u" ".join(normalize_string(text).lower().split())


def meters_to_feet(value):
    return float(value) * FEET_PER_METER


def is_number(value):
    return isinstance(value, (int, long, float)) and not isinstance(value, bool)


def split_title_block(text):
    """'Family : Type' -> (family, type); 'Type' -> (None, 'Type')."""
    text = normalize_string(text)
    if u":" in text:
        family, type_name = text.split(u":", 1)
        return family.strip(), type_name.strip()
    return None, text


def validate_sheet_item(item):
    """Shape check of one sheet to create. Returns an error message or None."""
    if not isinstance(item, dict):
        return "Each sheet must be an object"
    for key in ("number", "name"):
        value = item.get(key)
        if not isinstance(value, basestring) or not value.strip():
            return "{} must be a non-empty text".format(key)
    return None


def validate_placement(item):
    """Shape check of one view placement. Returns an error message or None."""
    if not isinstance(item, dict):
        return "Each placement must be an object"
    number = item.get("sheet_number")
    if not isinstance(number, basestring) or not number.strip():
        return "sheet_number must be a non-empty text"
    has_name = isinstance(item.get("view"), basestring) and item["view"].strip() != ""
    view_id = item.get("view_id")
    has_id = view_id is not None
    if has_id and (not isinstance(view_id, (int, long)) or isinstance(view_id, bool)):
        return "view_id must be an integer"
    if has_name == has_id:
        return "Give exactly one of view (name) or view_id"
    x, y = item.get("x"), item.get("y")
    if (x is None) != (y is None):
        return "Give both x and y, or neither"
    if x is not None and not (is_number(x) and is_number(y)):
        return "x and y must be numbers (meters)"
    return None


def validate_view_ref(ref):
    """A view reference is a non-empty name or an integer element id."""
    if isinstance(ref, bool):
        return "View reference must be a name or an element id"
    if isinstance(ref, (int, long)):
        return None
    if isinstance(ref, basestring) and ref.strip():
        return None
    return "View reference must be a name or an element id"


def duplicate_indexes(keys):
    """Indexes whose key already appeared earlier in the list (None keys ignored)."""
    seen = set()
    duplicates = []
    for index, key in enumerate(keys):
        if key is None:
            continue
        if key in seen:
            duplicates.append(index)
        seen.add(key)
    return duplicates


# ---- Revit helpers ----

def element_name(element):
    """Name of any element; Element.Name raises AttributeError on some types in IronPython."""
    if element is None:
        return None
    try:
        return normalize_string(DB.Element.Name.GetValue(element))
    except Exception:
        pass
    try:
        return normalize_string(element.Name)
    except Exception:
        return None


def _id(element):
    return element_id_value(element.Id)


def _sheets_by_number(doc):
    sheets = {}
    for sheet in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet):
        sheets[name_key(sheet.SheetNumber)] = sheet
    return sheets


def _title_block_label(symbol):
    family = None
    try:
        family = normalize_string(symbol.FamilyName)
    except Exception:
        try:
            family = element_name(symbol.Family)
        except Exception:
            family = None
    return family, element_name(symbol)


def _title_blocks(doc):
    """[(family, type, symbol)] of loaded title block types, sorted by name."""
    collector = DB.FilteredElementCollector(doc) \
        .OfCategory(DB.BuiltInCategory.OST_TitleBlocks).WhereElementIsElementType()
    found = []
    for symbol in collector:
        family, type_name = _title_block_label(symbol)
        found.append((family, type_name, symbol))
    found.sort(key=lambda entry: (name_key(entry[0]), name_key(entry[1])))
    return found


def _label(family, type_name):
    return u"{} : {}".format(family, type_name)


def resolve_title_block(doc, requested):
    """Returns (symbol or None, info dict, error). No title block loaded -> sheet without one."""
    loaded = _title_blocks(doc)
    if not requested:
        if not loaded:
            return None, None, None
        family, type_name, symbol = loaded[0]
        return symbol, {"family": family, "type": type_name, "id": _id(symbol)}, None
    family_wanted, type_wanted = split_title_block(requested)
    matches = []
    for family, type_name, symbol in loaded:
        if family_wanted is not None:
            ok = name_key(family) == name_key(family_wanted) and \
                name_key(type_name) == name_key(type_wanted)
        else:
            ok = name_key(type_wanted) in (name_key(type_name), name_key(family))
        if ok:
            matches.append((family, type_name, symbol))
    if not matches:
        available = [_label(f, t) for f, t, _ in loaded[:MAX_LISTED]]
        return None, None, {"error": u"Title block '{}' not found".format(normalize_string(requested)),
                            "available": available}
    if len(matches) > 1:
        return None, None, {"error": u"{} title blocks match '{}'; use 'Family : Type'".format(
            len(matches), normalize_string(requested)),
            "matches": [_label(f, t) for f, t, _ in matches[:MAX_LISTED]]}
    family, type_name, symbol = matches[0]
    return symbol, {"family": family, "type": type_name, "id": _id(symbol)}, None


def _set_text(element, builtin, value):
    parameter = element.get_Parameter(builtin)
    if parameter is None or parameter.IsReadOnly:
        return False
    return bool(parameter.Set(value))


def _is_schedule(view):
    return isinstance(view, DB.ViewSchedule)


def _placeable_on_many(view):
    """Schedules and legends may appear on several sheets."""
    return _is_schedule(view) or str(view.ViewType) == "Legend"


SKIPPED_VIEW_TYPES = ("DrawingSheet", "ProjectBrowser", "SystemBrowser", "Internal", "Undefined")


def _views(doc):
    """Non-template views that can be named by the user (sheets excluded)."""
    for view in DB.FilteredElementCollector(doc).OfClass(DB.View):
        if view.IsTemplate or str(view.ViewType) in SKIPPED_VIEW_TYPES:
            continue
        yield view


def _views_by_name(doc):
    by_name = {}
    for view in _views(doc):
        by_name.setdefault(name_key(element_name(view)), []).append(view)
    return by_name


def _find_view(doc, by_name, name=None, view_id=None):
    """Returns (view, error) for a view given by name or element id."""
    if view_id is not None:
        view = doc.GetElement(element_id_from_int(int(view_id)))
        if view is None or not isinstance(view, DB.View):
            return None, "View not found"
        return view, None
    matches = by_name.get(name_key(name), [])
    if not matches:
        return None, "View not found"
    if len(matches) > 1:
        return None, u"{} views named '{}' ({}); use view_id".format(
            len(matches), normalize_string(name),
            ", ".join(sorted(set(str(v.ViewType) for v in matches))))
    return matches[0], None


def _sheet_centre(doc, sheet):
    """Centre of the title block on the sheet, else of the sheet outline (feet)."""
    try:
        blocks = DB.FilteredElementCollector(doc, sheet.Id) \
            .OfCategory(DB.BuiltInCategory.OST_TitleBlocks).WhereElementIsNotElementType()
        for block in blocks:
            box = block.get_BoundingBox(sheet)
            if box is not None:
                return DB.XYZ((box.Min.X + box.Max.X) / 2.0, (box.Min.Y + box.Max.Y) / 2.0, 0)
    except Exception as error:
        logger.debug("Title block bounding box unavailable: %s", error)
    outline = sheet.Outline
    return DB.XYZ((outline.Min.U + outline.Max.U) / 2.0, (outline.Min.V + outline.Max.V) / 2.0, 0)


def _run(doc, name, planned, apply_one, commit, total, noun):
    """Apply every planned item in one transaction (rolled back on dry run or any error).

    apply_one(item) returns (result dict, error message or None).
    """
    results, errors = [], []
    transaction = DB.Transaction(doc, name)
    transaction.Start()
    try:
        for item in planned:
            try:
                result, error = apply_one(item)
            except Exception as exc:
                result, error = None, normalize_string(u"{}".format(exc))
            if error:
                errors.append({"index": item["index"], "error": error})
            elif result is not None:
                results.append(result)
        if errors or not commit:
            transaction.RollBack()
        else:
            status = transaction.Commit()
            if status != DB.TransactionStatus.Committed:
                return {"status": "error", "applied": 0, "errors": [],
                        "error": "Revit did not commit the transaction ({})".format(status)}
    except Exception:
        if transaction.HasStarted() and not transaction.HasEnded():
            transaction.RollBack()
        raise

    if errors:
        return {"status": "error",
                "error": "{} of {} {} were rejected by Revit; nothing was applied".format(
                    len(errors), total, noun),
                "applied": 0, "errors": errors}
    if not commit:
        for result in results:
            for key in ("sheet_id", "viewport_id"):
                result.pop(key, None)
        return {"status": "dry_run",
                "message": "Dry run: {} {} are valid. Nothing was changed; call again "
                           "with dry_run=false to apply.".format(len(results), noun),
                "applied": 0, "results": results}
    return {"status": "success", "applied": len(results), "results": results}


def _invalid(errors, total, noun):
    return {"status": "error",
            "error": "{} of {} {} are invalid; nothing was applied".format(len(errors), total, noun),
            "applied": 0, "errors": errors}


# ---- operations ----

def create_sheets(doc, items, title_block, commit):
    symbol, block_info, block_error = resolve_title_block(doc, title_block)
    if block_error:
        result = {"status": "error", "applied": 0}
        result.update(block_error)
        return result

    existing = _sheets_by_number(doc)
    errors = []
    duplicates = set(duplicate_indexes([name_key(item["number"]) for item in items]))
    for index, item in enumerate(items):
        if index in duplicates:
            errors.append({"index": index, "number": item["number"],
                           "error": "Sheet number repeated in this batch"})
        elif name_key(item["number"]) in existing:
            errors.append({"index": index, "number": item["number"],
                           "error": "A sheet with this number already exists"})
    if errors:
        return _invalid(errors, len(items), "sheets")

    block_id = symbol.Id if symbol is not None else DB.ElementId.InvalidElementId

    def apply_one(item):
        sheet = DB.ViewSheet.Create(doc, block_id)
        number = normalize_string(item["number"])
        name = normalize_string(item["name"])
        sheet.SheetNumber = number
        if not _set_text(sheet, DB.BuiltInParameter.SHEET_NAME, name):
            return None, "Revit rejected the sheet name"
        return {"index": item["index"], "number": normalize_string(sheet.SheetNumber),
                "name": name, "sheet_id": _id(sheet)}, None

    planned = [dict(item, index=index) for index, item in enumerate(items)]
    result = _run(doc, "MCP: create sheets", planned, apply_one, commit, len(items), "sheets")
    result["title_block"] = block_info
    return result


def place_views(doc, items, commit):
    sheets = _sheets_by_number(doc)
    by_name = _views_by_name(doc)
    errors, planned, single_use = [], [], []
    for index, item in enumerate(items):
        sheet = sheets.get(name_key(item["sheet_number"]))
        view, error = _find_view(doc, by_name, item.get("view"), item.get("view_id"))
        if sheet is None:
            error = "Sheet not found"
        elif error is None:
            if view.IsTemplate:
                error = "View is a view template"
            elif _is_schedule(view):
                if getattr(view, "IsTitleblockRevisionSchedule", False) or \
                        getattr(view, "IsInternalKeynoteSchedule", False):
                    error = "This schedule cannot be placed on a sheet"
            elif not DB.Viewport.CanAddViewToSheet(doc, sheet.Id, view.Id):
                error = "View cannot be added to this sheet (already on a sheet, empty or not placeable)"
        single_use.append(_id(view) if view is not None and not _placeable_on_many(view) else None)
        if error:
            errors.append({"index": index, "sheet_number": item["sheet_number"],
                           "view": item.get("view", item.get("view_id")), "error": error})
        else:
            planned.append({"index": index, "sheet": sheet, "view": view, "item": item})
    failed = set(e["index"] for e in errors)
    for index in duplicate_indexes(single_use):
        if index in failed:
            continue
        errors.append({"index": index, "sheet_number": items[index]["sheet_number"],
                       "view": items[index].get("view", items[index].get("view_id")),
                       "error": "View placed twice in this batch; a view can be on one sheet only"})
    if errors:
        errors.sort(key=lambda e: e["index"])
        return _invalid(errors, len(items), "placements")

    def apply_one(plan):
        sheet, view, item = plan["sheet"], plan["view"], plan["item"]
        if item.get("x") is not None:
            point = DB.XYZ(meters_to_feet(item["x"]), meters_to_feet(item["y"]), 0)
        else:
            point = _sheet_centre(doc, sheet)
        if _is_schedule(view):
            placed = DB.ScheduleSheetInstance.Create(doc, sheet.Id, view.Id, point)
            kind = "schedule"
        else:
            if not DB.Viewport.CanAddViewToSheet(doc, sheet.Id, view.Id):
                return None, "View cannot be added to this sheet"
            placed = DB.Viewport.Create(doc, sheet.Id, view.Id, point)
            kind = "viewport"
        if placed is None:
            return None, "Revit did not place the view"
        return {"index": plan["index"], "sheet_number": normalize_string(sheet.SheetNumber),
                "view": element_name(view), "view_id": _id(view), "kind": kind,
                "position_m": {"x": round(point.X / FEET_PER_METER, 4),
                               "y": round(point.Y / FEET_PER_METER, 4)},
                "viewport_id": _id(placed)}, None

    return _run(doc, "MCP: place views on sheets", planned, apply_one, commit,
                len(items), "placements")


def _template_name(doc, view):
    template_id = view.ViewTemplateId
    if template_id is None or template_id == DB.ElementId.InvalidElementId:
        return None
    return element_name(doc.GetElement(template_id))


def find_template(doc, name):
    """Returns (template, error)."""
    matches = [view for view in DB.FilteredElementCollector(doc).OfClass(DB.View)
               if view.IsTemplate and name_key(element_name(view)) == name_key(name)]
    if not matches:
        return None, u"View template '{}' not found".format(normalize_string(name))
    if len(matches) > 1:
        return None, u"{} view templates named '{}'".format(len(matches), normalize_string(name))
    return matches[0], None


def apply_template(doc, template_name, view_refs, view_type, commit):
    template, error = find_template(doc, template_name)
    if error:
        return {"status": "error", "applied": 0, "error": error}

    errors, targets = [], []
    if view_refs:
        by_name = _views_by_name(doc)
        for index, ref in enumerate(view_refs):
            if isinstance(ref, (int, long)):
                view, error = _find_view(doc, by_name, view_id=ref)
            else:
                view, error = _find_view(doc, by_name, name=ref)
            targets.append((index, ref, view, error))
    else:
        wanted = name_key(view_type)
        index = 0
        for view in _views(doc):
            if name_key(str(view.ViewType)) == wanted:
                targets.append((index, _id(view), view, None))
                index += 1
        if not targets:
            return {"status": "error", "applied": 0,
                    "error": u"No views of type '{}'".format(normalize_string(view_type))}
        if len(targets) > MAX_ITEMS:
            return {"status": "error", "applied": 0,
                    "error": u"{} views of type '{}'; at most {} per call, pass views "
                             u"explicitly in batches".format(len(targets), view_type, MAX_ITEMS)}

    duplicates = set(duplicate_indexes(
        [_id(view) if view is not None else None for _, _, view, _ in targets]))
    planned = []
    for index, ref, view, error in targets:
        if error is None:
            if view.IsTemplate:
                error = "View is a view template"
            elif index in duplicates:
                error = "View repeated in this batch"
            elif not view.IsValidViewTemplate(template.Id):
                error = "Template cannot be applied to this view (view type mismatch)"
        if error:
            errors.append({"index": index, "view": ref, "error": error})
        else:
            planned.append({"index": index, "view": view})
    if errors:
        return _invalid(errors, len(targets), "views")

    def apply_one(plan):
        view = plan["view"]
        before = _template_name(doc, view)
        view.ViewTemplateId = template.Id
        return {"index": plan["index"], "view": element_name(view), "view_id": _id(view),
                "view_type": str(view.ViewType), "before": before,
                "after": _template_name(doc, view)}, None

    result = _run(doc, "MCP: apply view template", planned, apply_one, commit,
                  len(targets), "views")
    result["template"] = {"name": element_name(template), "id": _id(template)}
    return result


# ---- routes ----

def _payload(request):
    data = json.loads(request.data) if isinstance(request.data, str) else request.data
    return data or {}


def _bad(message, errors=None):
    data = {"error": message}
    if errors:
        data["errors"] = errors
    return routes.make_response(data=data, status=400)


def _check_list(items, noun, validate):
    if not isinstance(items, list) or not items:
        return _bad("{} must be a non-empty list".format(noun))
    if len(items) > MAX_ITEMS:
        return _bad("At most {} {} per call, got {}".format(MAX_ITEMS, noun, len(items)))
    errors = []
    for index, item in enumerate(items):
        error = validate(item)
        if error:
            errors.append({"index": index, "error": error})
    if errors:
        return _bad("Invalid {}".format(noun), errors)
    return None


def register_sheet_routes(api):
    """Register sheet and view template routes with the API"""

    @api.route("/create_sheets/", methods=["POST"])
    def create_sheets_route(doc, request):
        """
        {"dry_run": true, "title_block": "A1 : A1", "sheets": [{"number": "A101", "name": "Planta"}]}
        """
        denial = authorize_request(request, action=None)
        if denial is not None:
            return denial
        try:
            if not doc:
                return routes.make_response(data={"error": "No active Revit document"}, status=503)
            data = _payload(request)
            dry_run = data.get("dry_run", True) is not False
            items = data.get("sheets")
            title_block = data.get("title_block")
            invalid = _check_list(items, "sheets", validate_sheet_item)
            if invalid is not None:
                return invalid
            if title_block is not None and (not isinstance(title_block, basestring)
                                            or not title_block.strip()):
                return _bad("title_block must be a non-empty text")
            if not dry_run:
                denial = authorize_request(request, action="Create sheets")
                if denial is not None:
                    return denial
            return routes.make_response(
                data=create_sheets(doc, items, title_block, commit=not dry_run))
        except Exception as e:
            logger.error("Error in create_sheets route: %s", e)
            return routes.make_response(data={"error": str(e)}, status=500)

    @api.route("/place_views_on_sheets/", methods=["POST"])
    def place_views_route(doc, request):
        """
        {"dry_run": true, "placements": [{"sheet_number": "A101", "view": "Térreo", "x": 0.4, "y": 0.3}]}
        """
        denial = authorize_request(request, action=None)
        if denial is not None:
            return denial
        try:
            if not doc:
                return routes.make_response(data={"error": "No active Revit document"}, status=503)
            data = _payload(request)
            dry_run = data.get("dry_run", True) is not False
            items = data.get("placements")
            invalid = _check_list(items, "placements", validate_placement)
            if invalid is not None:
                return invalid
            if not dry_run:
                denial = authorize_request(request, action="Place views on sheets")
                if denial is not None:
                    return denial
            return routes.make_response(data=place_views(doc, items, commit=not dry_run))
        except Exception as e:
            logger.error("Error in place_views_on_sheets route: %s", e)
            return routes.make_response(data={"error": str(e)}, status=500)

    @api.route("/apply_view_template/", methods=["POST"])
    def apply_view_template_route(doc, request):
        """
        {"dry_run": true, "template": "Planta - Executivo", "views": ["Térreo", 12345]}
        or {"dry_run": true, "template": "...", "view_type": "FloorPlan"}
        """
        denial = authorize_request(request, action=None)
        if denial is not None:
            return denial
        try:
            if not doc:
                return routes.make_response(data={"error": "No active Revit document"}, status=503)
            data = _payload(request)
            dry_run = data.get("dry_run", True) is not False
            template = data.get("template")
            views = data.get("views")
            view_type = data.get("view_type")
            if not isinstance(template, basestring) or not template.strip():
                return _bad("template must be a non-empty name")
            if (views is None) == (view_type is None):
                return _bad("Give exactly one of views or view_type")
            if views is not None:
                invalid = _check_list(views, "views", validate_view_ref)
                if invalid is not None:
                    return invalid
            elif not isinstance(view_type, basestring) or not view_type.strip():
                return _bad("view_type must be a non-empty text such as FloorPlan")
            if not dry_run:
                denial = authorize_request(request, action="Apply view template")
                if denial is not None:
                    return denial
            return routes.make_response(
                data=apply_template(doc, template, views, view_type, commit=not dry_run))
        except Exception as e:
            logger.error("Error in apply_view_template route: %s", e)
            return routes.make_response(data={"error": str(e)}, status=500)
