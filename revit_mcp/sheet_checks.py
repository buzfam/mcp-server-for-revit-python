# -*- coding: UTF-8 -*-
"""
Sheet checklist before issuing: compares each sheet with the checklist of its
sheet type (required views, schedules, legends, scales, title block
parameters, current revision, number of viewports) plus generic checks
(sheet without views, empty viewport, missing title block, views not on any
sheet). Read-only: no transaction.
"""

from pyrevit import routes, DB
import logging
import re
import time
from .security import authorize_request
from .utils import normalize_string, element_id_value
from .sheets import name_key, element_name
from .health import NON_PLACEABLE_VIEW_TYPES
from .parameters import _builtin_parameter
from .exports import _parse_body

logger = logging.getLogger(__name__)

MAX_RULES = 50
MAX_SHEET_FILTER = 500
DEFAULT_LIMIT = 200
MAX_LIMIT = 1000
DEFAULT_UNPLACED_LIMIT = 50
MAX_UNPLACED_LIMIT = 200

RULE_KEYS = ("match", "name", "required_views", "required_schedules", "required_legends",
             "allowed_scales", "title_block_params", "require_revision",
             "max_viewports", "min_viewports", "manual")
MATCH_KEYS = ("number_prefix", "number_regex", "name_regex")
VIEW_SPEC_KEYS = ("view_type", "name_regex", "min")
# ViewType names that can be placed on a sheet through a viewport.
PLACEABLE_VIEW_TYPES = ("FloorPlan", "CeilingPlan", "Elevation", "ThreeD", "DraftingView",
                        "Legend", "EngineeringPlan", "AreaPlan", "Section", "Detail",
                        "Walkthrough", "Rendering")
# View types checked for "empty viewport" (views that show model elements).
EMPTY_CHECK_TYPES = ("FloorPlan", "CeilingPlan", "EngineeringPlan", "AreaPlan", "Section",
                     "Elevation", "Detail", "ThreeD")
# Datum and helper categories that do not make a view "non-empty".
DATUM_CATEGORIES = ("OST_Levels", "OST_Grids", "OST_CLines", "OST_Cameras", "OST_Viewers",
                    "OST_SectionBox", "OST_VolumeOfInterest", "OST_ProjectBasePoint",
                    "OST_SharedBasePoint", "OST_Elev", "OST_ElevationMarks")
# Seconds spent on "empty viewport" checks per request; after that they are skipped.
EMPTY_CHECK_BUDGET_S = 60.0
# Problems that need a look, not something missing on the sheet.
REVIEW_CHECKS = ("scale", "empty_viewport", "viewport_count")


# ---- pure helpers (no Revit API; checked by scripts/ironpython_check.py) ----

def _text(value):
    return isinstance(value, basestring) and value.strip() != u""


def _integer(value):
    return isinstance(value, (int, long)) and not isinstance(value, bool)


def _regex_error(pattern):
    try:
        re.compile(pattern, re.IGNORECASE | re.UNICODE)
    except re.error as error:
        return u"invalid regex '{}': {}".format(normalize_string(pattern), error)
    return None


def search(pattern, text):
    """Case-insensitive regex search; None text never matches."""
    if text is None:
        return False
    return re.search(pattern, normalize_string(text), re.IGNORECASE | re.UNICODE) is not None


def canonical_view_type(text):
    """'floorplan' -> 'FloorPlan'; None when not a placeable ViewType name."""
    for view_type in PLACEABLE_VIEW_TYPES:
        if name_key(view_type) == name_key(text):
            return view_type
    return None


def _text_list_error(value, key, regex):
    if not isinstance(value, list) or not value:
        return u"{} must be a non-empty list of texts".format(key)
    for index, item in enumerate(value):
        if not _text(item):
            return u"{}[{}] must be a non-empty text".format(key, index)
        if regex:
            error = _regex_error(item)
            if error:
                return u"{}[{}]: {}".format(key, index, error)
    return None


def _view_spec_error(spec):
    if not isinstance(spec, dict):
        return u"must be an object"
    unknown = sorted(set(spec) - set(VIEW_SPEC_KEYS))
    if unknown:
        return u"unknown keys {}; use {}".format(", ".join(unknown), ", ".join(VIEW_SPEC_KEYS))
    if spec.get("view_type") is None and spec.get("name_regex") is None:
        return u"give view_type and/or name_regex"
    if spec.get("view_type") is not None and canonical_view_type(spec["view_type"]) is None:
        return u"view_type must be one of {}".format(", ".join(PLACEABLE_VIEW_TYPES))
    if spec.get("name_regex") is not None:
        if not _text(spec["name_regex"]):
            return u"name_regex must be a non-empty text"
        error = _regex_error(spec["name_regex"])
        if error:
            return error
    if spec.get("min") is not None and (not _integer(spec["min"]) or spec["min"] < 1):
        return u"min must be an integer >= 1"
    return None


def validate_rule(rule):
    """Shape check of one checklist rule. Returns an error message or None."""
    if not isinstance(rule, dict):
        return u"must be an object"
    unknown = sorted(set(rule) - set(RULE_KEYS))
    if unknown:
        return u"unknown keys {}".format(", ".join(unknown))
    match = rule.get("match")
    if not isinstance(match, dict) or not match:
        return u"match must be an object with {}".format(" and/or ".join(MATCH_KEYS))
    unknown = sorted(set(match) - set(MATCH_KEYS))
    if unknown:
        return u"match: unknown keys {}".format(", ".join(unknown))
    for key, value in match.items():
        if not _text(value):
            return u"match.{} must be a non-empty text".format(key)
        if key != "number_prefix":
            error = _regex_error(value)
            if error:
                return u"match.{}: {}".format(key, error)
    if rule.get("name") is not None and not _text(rule["name"]):
        return u"name must be a non-empty text"
    views = rule.get("required_views")
    if views is not None:
        if not isinstance(views, list) or not views:
            return u"required_views must be a non-empty list"
        for index, spec in enumerate(views):
            error = _view_spec_error(spec)
            if error:
                return u"required_views[{}]: {}".format(index, error)
    for key, regex in (("required_schedules", True), ("required_legends", True),
                       ("title_block_params", False), ("manual", False)):
        if rule.get(key) is not None:
            error = _text_list_error(rule[key], key, regex)
            if error:
                return error
    scales = rule.get("allowed_scales")
    if scales is not None:
        if not isinstance(scales, list) or not scales or \
                not all(_integer(s) and s > 0 for s in scales):
            return u"allowed_scales must be a non-empty list of positive integers (50 = 1:50)"
    if rule.get("require_revision") is not None and not isinstance(rule["require_revision"], bool):
        return u"require_revision must be true or false"
    for key in ("min_viewports", "max_viewports"):
        if rule.get(key) is not None and (not _integer(rule[key]) or rule[key] < 0):
            return u"{} must be an integer >= 0".format(key)
    if rule.get("min_viewports") is not None and rule.get("max_viewports") is not None \
            and rule["min_viewports"] > rule["max_viewports"]:
        return u"min_viewports is greater than max_viewports"
    return None


def parse_options(data):
    """Returns (options, error message, per-rule errors)."""
    data = data or {}
    rules = data.get("checklists")
    if rules is None:
        rules = []
    if not isinstance(rules, list):
        return None, "checklists must be a list", None
    if len(rules) > MAX_RULES:
        return None, "At most {} checklists per call, got {}".format(MAX_RULES, len(rules)), None
    errors = []
    for index, rule in enumerate(rules):
        error = validate_rule(rule)
        if error:
            errors.append({"index": index, "error": error})
    if errors:
        return None, "Invalid checklists", errors

    numbers = data.get("sheets")
    if numbers is not None:
        if not isinstance(numbers, list) or not numbers:
            return None, "sheets must be a non-empty list of sheet numbers", None
        if len(numbers) > MAX_SHEET_FILTER:
            return None, "At most {} sheet numbers, got {}".format(
                MAX_SHEET_FILTER, len(numbers)), None
        if not all(_text(n) for n in numbers):
            return None, "Each sheet number must be a non-empty text", None
    options = {"rules": rules, "sheets": numbers}
    for key, default in (("generic", True), ("only_problems", False),
                         ("unplaced_include_schedules", False)):
        value = data.get(key, default)
        if not isinstance(value, bool):
            return None, "{} must be true or false".format(key), None
        options[key] = value
    for key, default, low, high in (("limit", DEFAULT_LIMIT, 1, MAX_LIMIT),
                                    ("unplaced_limit", DEFAULT_UNPLACED_LIMIT, 0,
                                     MAX_UNPLACED_LIMIT)):
        value = data.get(key, default)
        if not _integer(value) or value < low or value > high:
            return None, "{} must be an integer between {} and {}".format(key, low, high), None
        options[key] = int(value)
    if not rules and not options["generic"]:
        return None, "Nothing to check: give checklists or keep generic=true", None
    return options, None, None


def rule_matches(rule, number, name):
    """Every key given in rule['match'] must match (prefix is case-insensitive)."""
    match = rule.get("match") or {}
    if not match:
        return False
    prefix = match.get("number_prefix")
    if prefix is not None and not name_key(number).startswith(name_key(prefix)):
        return False
    if match.get("number_regex") is not None and not search(match["number_regex"], number):
        return False
    if match.get("name_regex") is not None and not search(match["name_regex"], name):
        return False
    return True


def pick_rule(rules, number, name):
    """First rule that matches the sheet: (index, rule) or (None, None)."""
    for index, rule in enumerate(rules):
        if rule_matches(rule, number, name):
            return index, rule
    return None, None


def rule_label(rule, index):
    if rule is None:
        return None
    if _text(rule.get("name")):
        return normalize_string(rule["name"])
    return u"#{}".format(index)


def _describe_view_spec(spec):
    parts = []
    if spec.get("view_type"):
        parts.append(canonical_view_type(spec["view_type"]) or normalize_string(spec["view_type"]))
    if spec.get("name_regex"):
        parts.append(u"nome ~ '{}'".format(normalize_string(spec["name_regex"])))
    return u" ".join(parts)


def _view_spec_matches(spec, view):
    if spec.get("view_type") and name_key(view.get("type")) != name_key(spec["view_type"]):
        return False
    if spec.get("name_regex") and not search(spec["name_regex"], view.get("name")):
        return False
    return True


def _scales_text(scales):
    return u", ".join(u"1:{}".format(s) for s in scales)


def sheet_status(problems, manual_checks):
    """missing: something required is absent; review: needs a look; ok otherwise."""
    if any(p["check"] not in REVIEW_CHECKS for p in problems):
        return "missing"
    if problems or manual_checks:
        return "review"
    return "ok"


def evaluate_sheet(info, rule, generic):
    """Check one sheet described by plain data.

    info: {"views": [{"name", "type", "scale", "empty"}], "schedules": [names],
           "has_title_block": bool, "params": {name: "ok"|"empty"|"not_found"},
           "revision": text or None}
    Returns (problems, manual_checks, status).
    """
    problems = []

    def add(check, detail):
        problems.append({"check": check, "detail": detail})

    views = info.get("views") or []
    schedules = info.get("schedules") or []
    legends = [v for v in views if v.get("type") == "Legend"]
    if generic:
        if not views and not schedules:
            add("no_views", u"folha sem vistas nem tabelas")
        if not info.get("has_title_block"):
            add("title_block", u"folha sem carimbo")
        for view in views:
            if view.get("empty") is True:
                add("empty_viewport", u"vista '{}' sem elementos de modelo visíveis".format(
                    normalize_string(view.get("name"))))
    manual = []
    if rule is not None:
        for spec in rule.get("required_views") or []:
            wanted = spec.get("min") or 1
            found = len([v for v in views if _view_spec_matches(spec, v)])
            if found < wanted:
                add("required_view", u"faltando vista {} ({} de {})".format(
                    _describe_view_spec(spec), found, wanted))
        for pattern in rule.get("required_schedules") or []:
            if not any(search(pattern, s) for s in schedules):
                add("required_schedule", u"faltando tabela ~ '{}'".format(normalize_string(pattern)))
        for pattern in rule.get("required_legends") or []:
            if not any(search(pattern, v.get("name")) for v in legends):
                add("required_legend", u"faltando legenda ~ '{}'".format(normalize_string(pattern)))
        allowed = rule.get("allowed_scales")
        if allowed:
            for view in views:
                scale = view.get("scale")
                if view.get("type") == "Legend" or view.get("perspective") \
                        or not _integer(scale) or scale <= 0:
                    continue
                if scale not in allowed:
                    add("scale", u"vista '{}' em 1:{} (permitidas: {})".format(
                        normalize_string(view.get("name")), scale, _scales_text(allowed)))
        params = info.get("params") or {}
        for name in rule.get("title_block_params") or []:
            state = params.get(name, "not_found")
            if state == "empty":
                add("title_block_param", u"parâmetro '{}' vazio".format(normalize_string(name)))
            elif state != "ok":
                add("title_block_param", u"parâmetro '{}' não encontrado na folha, no carimbo "
                                         u"(instância ou tipo) nem nas informações do projeto".format(
                                             normalize_string(name)))
        if rule.get("require_revision") and not _text(info.get("revision")):
            add("revision", u"folha sem revisão atual")
        count = len(views)
        if rule.get("min_viewports") is not None and count < rule["min_viewports"]:
            add("viewport_count", u"{} vistas na folha (mínimo {})".format(count, rule["min_viewports"]))
        if rule.get("max_viewports") is not None and count > rule["max_viewports"]:
            add("viewport_count", u"{} vistas na folha (máximo {})".format(count, rule["max_viewports"]))
        manual = [normalize_string(item) for item in rule.get("manual") or []]
    return problems, manual, sheet_status(problems, manual)


def summarize_unplaced(entries, limit):
    """entries: [{"id", "name", "type"}] -> count, by type and a capped list."""
    by_type = {}
    for entry in entries:
        by_type[entry["type"]] = by_type.get(entry["type"], 0) + 1
    ordered = sorted(entries, key=lambda e: (e["type"], name_key(e["name"])))
    return {"count": len(entries), "by_type": by_type, "views": ordered[:limit],
            "truncated": len(entries) > limit}


def summarize(results):
    counts = {"ok": 0, "missing": 0, "review": 0}
    by_check = {}
    without_rule = 0
    for result in results:
        counts[result["status"]] += 1
        if result["rule"] is None:
            without_rule += 1
        for problem in result["problems"]:
            by_check[problem["check"]] = by_check.get(problem["check"], 0) + 1
    summary = {"sheets_checked": len(results), "problems_by_check": by_check,
               "sheets_without_rule": without_rule}
    summary.update(counts)
    return summary


# ---- Revit helpers ----

def _scale(view):
    try:
        scale = int(view.Scale)
    except Exception:
        return None
    return scale if scale > 0 else None


def _datum_filter():
    """Inverted multi-category filter that hides levels, grids and similar helpers."""
    try:
        from System.Collections.Generic import List
        categories = List[DB.BuiltInCategory]()
        for name in DATUM_CATEGORIES:
            category = getattr(DB.BuiltInCategory, name, None)
            if category is not None:
                categories.Add(category)
        return DB.ElementMulticategoryFilter(categories, True)
    except Exception as error:
        logger.debug("Datum filter unavailable: %s", error)
        return None


def _is_empty(doc, view, datum_filter):
    """True when no model element is visible in the view; None when unknown.
    Stops at the first element found (FirstElementId), no full count."""
    try:
        collector = DB.FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType() \
            .WhereElementIsViewIndependent()
        if datum_filter is not None:
            collector = collector.WherePasses(datum_filter)
        first = collector.FirstElementId()
        return first is None or first == DB.ElementId.InvalidElementId
    except Exception as error:
        logger.debug("Empty check failed for view %s: %s", element_id_value(view.Id), error)
        return None


class EmptyChecker(object):
    """Empty-viewport checks cached per view and limited by a time budget."""

    def __init__(self, doc, datum_filter, budget_s=EMPTY_CHECK_BUDGET_S, clock=None):
        self.doc, self.datum_filter = doc, datum_filter
        self.budget_s = budget_s
        self.clock = clock or time.time
        self.started = None
        self.cache = {}
        self.skipped = 0

    def check(self, view):
        """True/False/None like _is_empty; None also when the budget is spent."""
        key = element_id_value(view.Id)
        if key in self.cache:
            return self.cache[key]
        now = self.clock()
        if self.started is None:
            self.started = now
        if now - self.started > self.budget_s:
            self.skipped += 1
            return None
        self.cache[key] = _is_empty(self.doc, view, self.datum_filter)
        return self.cache[key]


def _is_perspective(view):
    try:
        return bool(view.IsPerspective)
    except Exception:
        return False


def _parameter_text(parameter):
    if parameter is None or not parameter.HasValue:
        return None
    if parameter.StorageType == DB.StorageType.String:
        return parameter.AsString()
    if parameter.StorageType == DB.StorageType.ElementId:
        element_id = parameter.AsElementId()
        if element_id is None or element_id == DB.ElementId.InvalidElementId:
            return None
    return parameter.AsValueString()


def _find_parameter(element, name):
    """By name in the model language, else by BuiltInParameter name."""
    if element is None:
        return None
    try:
        matches = list(element.GetParameters(name))
    except Exception:
        matches = []
    if matches:
        return matches[0]
    builtin = _builtin_parameter(name)
    if builtin is None:
        return None
    try:
        return element.get_Parameter(builtin)
    except Exception:
        return None


def parameter_state(owners, name):
    """'ok' if any owner has the parameter filled, 'empty' if found but blank, else 'not_found'."""
    found = False
    for owner in owners:
        parameter = _find_parameter(owner, name)
        if parameter is None:
            continue
        found = True
        value = _parameter_text(parameter)
        if value is not None and normalize_string(value) != u"":
            return "ok"
    return "empty" if found else "not_found"


def _revision(sheet):
    parameter = sheet.get_Parameter(DB.BuiltInParameter.SHEET_CURRENT_REVISION)
    value = parameter.AsString() if parameter is not None else None
    value = normalize_string(value) if value is not None else u""
    return value or None


def _group_by_owner(elements, owner_of):
    grouped = {}
    for element in elements:
        try:
            key = element_id_value(owner_of(element))
        except Exception:
            continue
        grouped.setdefault(key, []).append(element)
    return grouped


def _schedule_instances(doc):
    for instance in DB.FilteredElementCollector(doc).OfClass(DB.ScheduleSheetInstance):
        if getattr(instance, "IsTitleblockRevisionSchedule", False):
            continue
        yield instance


def _unplaced_views(doc, placed_ids, include_schedules):
    entries = []
    for view in DB.FilteredElementCollector(doc).OfClass(DB.View):
        if view.IsTemplate:
            continue
        view_type = str(view.ViewType)
        if view_type in NON_PLACEABLE_VIEW_TYPES:
            continue
        if isinstance(view, DB.ViewSchedule):
            if not include_schedules or getattr(view, "IsTitleblockRevisionSchedule", False) or \
                    getattr(view, "IsInternalKeynoteSchedule", False):
                continue
        elif not view.CanBePrinted:
            continue
        view_id = element_id_value(view.Id)
        if view_id in placed_ids:
            continue
        entries.append({"id": view_id, "name": element_name(view), "type": view_type})
    return entries


def check_sheets(doc, options):
    rules = options["rules"]
    generic = options["generic"]
    all_sheets = list(DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet))
    placeholders = [s for s in all_sheets if getattr(s, "IsPlaceholder", False)]
    sheets = [s for s in all_sheets if not getattr(s, "IsPlaceholder", False)]

    not_found = []
    if options["sheets"]:
        by_number = dict((name_key(s.SheetNumber), s) for s in sheets)
        chosen, seen = [], set()
        for number in options["sheets"]:
            sheet = by_number.get(name_key(number))
            if sheet is None:
                not_found.append(normalize_string(number))
            elif element_id_value(sheet.Id) not in seen:
                seen.add(element_id_value(sheet.Id))
                chosen.append(sheet)
        sheets = chosen
    sheets.sort(key=lambda s: name_key(s.SheetNumber))
    total = len(sheets)
    sheets = sheets[:options["limit"]]

    viewports = _group_by_owner(DB.FilteredElementCollector(doc).OfClass(DB.Viewport),
                                lambda v: v.SheetId)
    schedules = _group_by_owner(_schedule_instances(doc), lambda s: s.OwnerViewId)
    title_blocks = _group_by_owner(
        DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
        .WhereElementIsNotElementType(), lambda t: t.OwnerViewId)
    checker = EmptyChecker(doc, _datum_filter()) if generic else None
    project_info = getattr(doc, "ProjectInformation", None)

    results = []
    for sheet in sheets:
        sheet_id = element_id_value(sheet.Id)
        number = normalize_string(sheet.SheetNumber)
        name = element_name(sheet)
        index, rule = pick_rule(rules, number, name)
        views = []
        for viewport in viewports.get(sheet_id, []):
            view = doc.GetElement(viewport.ViewId)
            if view is None:
                continue
            view_type = str(view.ViewType)
            entry = {"name": element_name(view), "type": view_type, "scale": _scale(view),
                     "empty": None}
            if view_type == "ThreeD" and _is_perspective(view):
                entry["perspective"] = True
            if generic and view_type in EMPTY_CHECK_TYPES:
                entry["empty"] = checker.check(view)
            views.append(entry)
        schedule_names = []
        for instance in schedules.get(sheet_id, []):
            schedule_names.append(element_name(doc.GetElement(instance.ScheduleId)))
        blocks = title_blocks.get(sheet_id, [])
        params = {}
        if rule is not None:
            block_types = []
            for block in blocks:
                try:
                    block_type = doc.GetElement(block.GetTypeId())
                except Exception:
                    block_type = None
                if block_type is not None:
                    block_types.append(block_type)
            owners = [sheet] + blocks + block_types + \
                ([project_info] if project_info is not None else [])
            for param_name in rule.get("title_block_params") or []:
                params[param_name] = parameter_state(owners, param_name)
        revision = _revision(sheet)
        info = {"views": views, "schedules": schedule_names, "has_title_block": bool(blocks),
                "params": params, "revision": revision}
        problems, manual, status = evaluate_sheet(info, rule, generic)
        results.append({"id": sheet_id, "number": number, "name": name,
                        "rule": rule_label(rule, index), "status": status,
                        "revision": revision, "viewports": len(views),
                        "schedules": len(schedule_names),
                        "problems": problems, "manual_checks": manual})

    summary = summarize(results)
    summary.update({"sheets_total": total, "truncated": total > len(results),
                    "placeholders_skipped": len(placeholders), "not_found": not_found})
    if checker is not None:
        summary["empty_check_skipped"] = checker.skipped
    report = {"status": "success", "summary": summary,
              "sheets": [r for r in results
                         if not (options["only_problems"] and r["status"] == "ok")]}
    if generic:
        placed = set()
        for viewport in DB.FilteredElementCollector(doc).OfClass(DB.Viewport):
            placed.add(element_id_value(viewport.ViewId))
        for instance in _schedule_instances(doc):
            placed.add(element_id_value(instance.ScheduleId))
        report["unplaced_views"] = summarize_unplaced(
            _unplaced_views(doc, placed, options["unplaced_include_schedules"]),
            options["unplaced_limit"])
    return report


# ---- route ----

def register_sheet_check_routes(api):
    """Register the sheet checklist route with the API"""

    @api.route("/check_sheets/", methods=["POST"])
    def check_sheets_route(doc, request):
        """
        {"checklists": [{"name": "Planta de layout", "match": {"number_prefix": "A-1"},
                         "required_views": [{"view_type": "FloorPlan", "name_regex": "layout"}],
                         "allowed_scales": [50], "require_revision": true,
                         "manual": ["cotas conferidas"]}],
         "sheets": ["A-101"], "generic": true, "only_problems": false, "limit": 200}
        """
        denial = authorize_request(request, action=None)
        if denial is not None:
            return denial
        try:
            if not doc:
                return routes.make_response(data={"error": "No active Revit document"}, status=503)
            options, error, errors = parse_options(_parse_body(request))
            if error:
                body = {"error": error}
                if errors:
                    body["errors"] = errors
                return routes.make_response(data=body, status=400)
            return routes.make_response(data=check_sheets(doc, options))
        except Exception as e:
            logger.error("Error in check_sheets route: %s", e)
            return routes.make_response(data={"error": str(e)}, status=500)
