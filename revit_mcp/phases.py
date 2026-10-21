# -*- coding: UTF-8 -*-
"""
Phases (spec 016): report the project phases, phase filters and how many model
elements exist in each (created in / demolished in) combination, and set the
created/demolished phase of many elements at once (all or nothing).

A renovation plan in Revit is "existing / demolish / new construction": the
survey is modeled in the first phase (e.g. "Existente"), what is demolished
gets the next phase as demolished phase, and new work is created in the last
phase. set_element_phases makes that tagging a batch with a dry run first.
"""

from pyrevit import routes, DB
import json
import logging
from .security import authorize_request
from .utils import normalize_string, element_id_from_int, element_id_value, find_category
from .modeling import element_name, error_text, same_name
from .transactions import Batch

logger = logging.getLogger(__name__)

MAX_ITEMS = 200
DEFAULT_LIMIT = 200
MAX_LIMIT = 1000
TOP_CATEGORIES = 10
MAX_PREVIEW_IDS = 50
# View types without a model to collect from (a schedule has a phase but no geometry).
NON_GRAPHICAL_VIEWS = ("Schedule", "PanelSchedule", "ColumnSchedule", "DrawingSheet", "Legend",
                       "Report", "ProjectBrowser", "SystemBrowser", "Internal", "Undefined",
                       "CostReport", "LoadsReport", "PresureLossReport")
CLEAR_WORDS = (u"none", u"nenhum", u"nenhuma", u"nenhum(a)")
# ElementOnPhaseStatus values shown as columns in the Phase Filter dialog.
FILTER_STATUSES = ("New", "Existing", "Demolished", "Temporary")
# PhaseStatusPresentation enum (the API spells "Overriden" with one d).
PRESENTATIONS = {
    "ShowByCategory": "by_category",
    "ShowOverriden": "overridden",
    "ShowOverridden": "overridden",
    "DontShow": "not_displayed",
}


class ItemError(Exception):
    def __init__(self, text):
        Exception.__init__(self, text)
        self.text = text


# ---------------------------------------------------------------------------
# Pure helpers (no Revit API; checked under IronPython by scripts/ironpython_check.py)
# ---------------------------------------------------------------------------

def is_clear(value):
    """True for "none"/"nenhum": clears the demolished phase."""
    return isinstance(value, basestring) and \
        u" ".join(normalize_string(value).lower().split()) in CLEAR_WORDS


def phase_ref_error(value, key, allow_clear):
    """Shape check of a phase reference (name or id). Returns an error or None."""
    if isinstance(value, bool):
        return "{} must be a phase name or id".format(key)
    if isinstance(value, (int, long)):
        return None if value > 0 else "{} must be a positive phase id".format(key)
    if isinstance(value, basestring) and value.strip():
        if is_clear(value) and not allow_clear:
            return "{} cannot be cleared; every element has a created phase".format(key)
        return None
    return "{} must be a phase name or id".format(key)


def validate_change(item):
    """Shape check of one {element_id, created_phase?, demolished_phase?}."""
    if not isinstance(item, dict):
        return "Each change must be an object"
    element_id = item.get("element_id")
    if isinstance(element_id, bool) or not isinstance(element_id, (int, long)):
        return "element_id must be an integer"
    return phases_error(item)


def phases_error(item):
    """created_phase/demolished_phase of a change or of the batch."""
    created = item.get("created_phase")
    demolished = item.get("demolished_phase")
    if created is None and demolished is None:
        return "Give created_phase and/or demolished_phase"
    if created is not None:
        error = phase_ref_error(created, "created_phase", False)
        if error:
            return error
    if demolished is not None:
        return phase_ref_error(demolished, "demolished_phase", True)
    return None


def validate_selector(selector):
    """Shape check of {category?, level?, view_id?, selection?}."""
    if not isinstance(selector, dict):
        return "selector must be an object"
    unknown = sorted(set(selector) - set(("category", "level", "view_id", "selection")))
    if unknown:
        return "Unknown selector keys: {}".format(", ".join(unknown))
    for key in ("category", "level"):
        value = selector.get(key)
        if value is not None and not (isinstance(value, basestring) and value.strip()):
            return "selector.{} must be a non-empty name".format(key)
    view_id = selector.get("view_id")
    if view_id is not None and (isinstance(view_id, bool) or not isinstance(view_id, (int, long))):
        return "selector.view_id must be an integer"
    selection = selector.get("selection")
    if selection is not None and not isinstance(selection, bool):
        return "selector.selection must be true or false"
    if not selector.get("category") and selection is not True:
        return "selector needs a category or selection=true"
    return None


def duplicate_errors(changes):
    """Per-index errors for an element_id already used by an earlier change."""
    seen = {}
    errors = []
    for index, change in enumerate(changes):
        if not isinstance(change, dict):
            continue
        element_id = change.get("element_id")
        if isinstance(element_id, bool) or not isinstance(element_id, (int, long)):
            continue
        if element_id in seen:
            errors.append({"index": index,
                           "error": "element_id {} repeats change {}".format(element_id, seen[element_id])})
        else:
            seen[element_id] = index
    return errors


def expected_count_error(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, long)) or value < 1:
        return "expected_count must be a positive integer"
    return None


def view_kind_error(view_type, is_template):
    """Error for a view that cannot be used to collect model elements, else None."""
    if is_template:
        return "view_id is a view template; use a plan, section, elevation or 3D view"
    name = u"{}".format(view_type).split(".")[-1]
    if name in NON_GRAPHICAL_VIEWS:
        return u"view_id is a {} view without model elements; use a plan, section, " \
               u"elevation or 3D view".format(name)
    return None


def preview_ids(element_ids):
    """First MAX_PREVIEW_IDS ids for the approval dialog."""
    return list(element_ids[:MAX_PREVIEW_IDS])


def resolve_phase(phases, value):
    """Position of a phase in [(id, name)] (project order) by id or name.
    Returns (position, error)."""
    wanted_id = None
    if isinstance(value, (int, long)) and not isinstance(value, bool):
        wanted_id = int(value)
    elif isinstance(value, basestring) and value.strip().isdigit():
        wanted_id = int(value.strip())
    for position, (phase_id, name) in enumerate(phases):
        if wanted_id is not None and phase_id == wanted_id:
            return position, None
        if wanted_id is None and same_name(name, value):
            return position, None
    return None, u"Phase '{}' not found. Phases in order: {}".format(
        normalize_string(value), u", ".join(name for _, name in phases))


def order_error(created, demolished, phases):
    """Demolition cannot come before creation (same phase = temporary)."""
    if created is None or demolished is None or demolished >= created:
        return None
    return u"Demolished phase '{}' comes before created phase '{}'".format(
        phases[demolished][1], phases[created][1])


def status_in_phase(created, demolished, phase):
    """ElementOnPhaseStatus of an element in a view of phase position `phase`:
    future, new, temporary, existing, demolished or past."""
    if created is None:
        return None
    if created > phase:
        return "future"
    if created == phase:
        return "temporary" if demolished == phase else "new"
    if demolished is None or demolished > phase:
        return "existing"
    if demolished == phase:
        return "demolished"
    return "past"


def presentation_name(value):
    """Readable PhaseStatusPresentation."""
    text = u"{}".format(value)
    return PRESENTATIONS.get(text.split(".")[-1], text)


def combination_rows(counts, phases, by_category, top=TOP_CATEGORIES):
    """Rows per (created, demolished) combination in phase order.

    counts: {(created_pos, demolished_pos or None): {"count": n, "categories": {name: n}}}
    """
    last = len(phases) - 1

    def sort_key(key):
        created, demolished = key
        return (created, demolished is None, demolished if demolished is not None else 0)

    rows = []
    for key in sorted(counts, key=sort_key):
        created, demolished = key
        entry = counts[key]
        row = {"created_phase": phases[created][1],
               "demolished_phase": phases[demolished][1] if demolished is not None else None,
               "status_in_last_phase": status_in_phase(created, demolished, last),
               "count": entry["count"]}
        if by_category:
            ranked = sorted(entry["categories"].items(), key=lambda item: (-item[1], item[0]))
            row["categories"] = [{"category": name, "count": n} for name, n in ranked[:top]]
            if len(ranked) > top:
                row["other_categories"] = sum(n for _, n in ranked[top:])
        rows.append(row)
    return rows


def group_views(view_rows, limit):
    """Views grouped by (phase, phase filter); names listed up to `limit` in total.
    view_rows: [(phase_name, filter_name, view_name)]. Returns (groups, truncated)."""
    groups = {}
    order = []
    for phase, phase_filter, view in view_rows:
        key = (phase, phase_filter)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(view)
    result = []
    remaining = limit
    truncated = False
    for key in sorted(order, key=lambda k: (k[0] or u"", k[1] or u"")):
        names = sorted(groups[key])
        listed = names[:max(remaining, 0)]
        remaining -= len(listed)
        if len(listed) < len(names):
            truncated = True
        result.append({"phase": key[0], "phase_filter": key[1], "count": len(names),
                       "views": listed})
    return result, truncated


def parse_limit(value):
    try:
        limit = int(value if value is not None else DEFAULT_LIMIT)
    except (TypeError, ValueError):
        return None, "limit must be an integer"
    if limit < 1 or limit > MAX_LIMIT:
        return None, "limit must be between 1 and {}".format(MAX_LIMIT)
    return limit, None


# ---------------------------------------------------------------------------
# Revit helpers
# ---------------------------------------------------------------------------

def project_phases(doc):
    """[(id_int, name)] in project order and {id_int: position}."""
    phases = [(element_id_value(phase.Id), element_name(phase)) for phase in doc.Phases]
    return phases, dict((phase_id, position) for position, (phase_id, _) in enumerate(phases))


def _is_invalid(element_id):
    return element_id is None or element_id == DB.ElementId.InvalidElementId or \
        element_id_value(element_id) < 0


def _has_phases(element):
    try:
        if not element.HasPhases():
            return False
    except Exception:
        pass
    try:
        return not _is_invalid(element.CreatedPhaseId)
    except Exception:
        return False


def _positions(element, positions):
    created = positions.get(element_id_value(element.CreatedPhaseId))
    demolished_id = element.DemolishedPhaseId
    demolished = None if _is_invalid(demolished_id) else positions.get(element_id_value(demolished_id))
    return created, demolished


def _model_elements(doc, collector=None):
    """Instances of model categories that carry phases (view-specific excluded)."""
    if collector is None:
        collector = DB.FilteredElementCollector(doc)
    for element in collector.WhereElementIsNotElementType().ToElements():
        category = element.Category
        if category is None:
            continue
        try:
            if category.CategoryType != DB.CategoryType.Model or element.ViewSpecific:
                continue
        except Exception:
            continue
        if _has_phases(element):
            yield element


def _parameter_id(element, builtin):
    try:
        parameter = element.get_Parameter(builtin)
        return parameter.AsElementId() if parameter is not None else None
    except Exception:
        return None


def _filter_presentation(phase_filter):
    shown = {}
    for status in FILTER_STATUSES:
        try:
            value = phase_filter.GetPhaseStatusPresentation(getattr(DB.ElementOnPhaseStatus, status))
            shown[status.lower()] = presentation_name(value)
        except Exception:
            shown[status.lower()] = None
    return shown


def _id_list(ids):
    """.NET List[ElementId] for API filters (plain list outside Revit)."""
    try:
        from System.Collections.Generic import List
        return List[DB.ElementId](ids)
    except ImportError:
        return list(ids)


def phases_report(doc, categories, by_category, limit):
    phases, positions = project_phases(doc)
    category_ids = None
    collector = None
    if categories:
        category_ids = set()
        for name in categories:
            category = find_category(doc, name)
            if category is None:
                raise ItemError(u"Category '{}' not found".format(normalize_string(name)))
            category_ids.add(element_id_value(category.Id))
        # Only instances of the requested categories are read from the model.
        collector = DB.FilteredElementCollector(doc).WherePasses(DB.ElementMulticategoryFilter(
            _id_list([element_id_from_int(value) for value in sorted(category_ids)])))

    filters = []
    filter_names = {}
    for phase_filter in DB.FilteredElementCollector(doc).OfClass(DB.PhaseFilter).ToElements():
        name = element_name(phase_filter)
        filter_names[element_id_value(phase_filter.Id)] = name
        filters.append({"id": element_id_value(phase_filter.Id), "name": name,
                        "presentation": _filter_presentation(phase_filter)})
    filters.sort(key=lambda item: item["name"])

    counts = {}
    total = 0
    for element in _model_elements(doc, collector):
        category_id = element_id_value(element.Category.Id)
        if category_ids is not None and category_id not in category_ids:
            continue
        created, demolished = _positions(element, positions)
        if created is None:
            continue
        entry = counts.setdefault((created, demolished), {"count": 0, "categories": {}})
        entry["count"] += 1
        total += 1
        if by_category:
            category_name = normalize_string(element.Category.Name)
            entry["categories"][category_name] = entry["categories"].get(category_name, 0) + 1

    view_rows = []
    phase_names = dict(phases)
    for view in DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements():
        try:
            if view.IsTemplate:
                continue
        except Exception:
            continue
        phase_id = _parameter_id(view, DB.BuiltInParameter.VIEW_PHASE)
        if phase_id is None or _is_invalid(phase_id):
            continue
        filter_id = _parameter_id(view, DB.BuiltInParameter.VIEW_PHASE_FILTER)
        filter_name = None if filter_id is None or _is_invalid(filter_id) else \
            filter_names.get(element_id_value(filter_id))
        view_rows.append((phase_names.get(element_id_value(phase_id)), filter_name,
                          element_name(view)))
    views, truncated = group_views(view_rows, limit)

    result = {"status": "success",
              "phases": [{"id": phase_id, "name": name, "sequence": position + 1}
                         for position, (phase_id, name) in enumerate(phases)],
              "phase_filters": filters,
              "elements_total": total,
              "combinations": combination_rows(counts, phases, by_category),
              "views": views,
              "views_total": len(view_rows),
              "views_truncated": truncated}
    if categories:
        result["categories"] = [normalize_string(c) for c in categories]
    return result


def _phase_name(phases, position):
    return phases[position][1] if position is not None else None


def _plan(doc, element, phases, positions, created_ref, demolished_ref):
    """Validate one element and its target phases. Returns a plan dict."""
    if element is None:
        raise ItemError("Element not found")
    if not _has_phases(element):
        raise ItemError("Element has no phases (not a model element with phase parameters)")
    try:
        if not element.ArePhasesModifiable():
            raise ItemError("Element phases cannot be modified")
    except AttributeError:
        pass
    created, demolished = _positions(element, positions)
    new_created, new_demolished = created, demolished
    if created_ref is not None:
        parameter = element.get_Parameter(DB.BuiltInParameter.PHASE_CREATED)
        if parameter is None or parameter.IsReadOnly:
            raise ItemError("Phase Created is read-only for this element")
        new_created, error = resolve_phase(phases, created_ref)
        if error:
            raise ItemError(error)
    if demolished_ref is not None:
        parameter = element.get_Parameter(DB.BuiltInParameter.PHASE_DEMOLISHED)
        if parameter is None or parameter.IsReadOnly:
            raise ItemError("Phase Demolished is read-only for this element")
        if is_clear(demolished_ref):
            new_demolished = None
        else:
            new_demolished, error = resolve_phase(phases, demolished_ref)
            if error:
                raise ItemError(error)
    error = order_error(new_created, new_demolished, phases)
    if error:
        raise ItemError(error)
    # Revit's own checks compare against the element's current other phase, so
    # each is only meaningful when the other phase stays as it is.
    if new_created != created and new_demolished == demolished:
        _check_valid(element, "IsPhaseCreatedValid", _phase_id(phases, new_created),
                     u"Revit does not accept created phase '{}' for this element".format(
                         phases[new_created][1]))
    if new_demolished is not None and new_demolished != demolished and new_created == created:
        _check_valid(element, "IsPhaseDemolishedValid", _phase_id(phases, new_demolished),
                     u"Revit does not accept demolished phase '{}' for this element".format(
                         phases[new_demolished][1]))
    category = element.Category
    return {"element": element,
            "element_id": element_id_value(element.Id),
            "category": normalize_string(category.Name) if category is not None else None,
            "name": element_name(element),
            "before": (created, demolished), "after": (new_created, new_demolished)}


def _check_valid(element, method, phase_id, message):
    """Element.IsPhaseCreatedValid/IsPhaseDemolishedValid when the API has them."""
    check = getattr(element, method, None)
    if check is None:
        return
    try:
        valid = check(phase_id)
    except Exception:
        return
    if not valid:
        raise ItemError(message)


def _phase_id(phases, position):
    if position is None:
        return DB.ElementId.InvalidElementId
    return element_id_from_int(phases[position][0])


def _apply(plan, phases):
    element = plan["element"]
    before_created, before_demolished = plan["before"]
    created, demolished = plan["after"]
    if created == before_created and demolished == before_demolished:
        return
    # Clear the demolition first so moving creation later never trips the order check.
    if before_demolished is not None:
        element.DemolishedPhaseId = DB.ElementId.InvalidElementId
    if created != before_created:
        element.CreatedPhaseId = _phase_id(phases, created)
    if demolished is not None:
        element.DemolishedPhaseId = _phase_id(phases, demolished)


def _row(plan, phases, after=None):
    created, demolished = plan["before"]
    new_created, new_demolished = after if after is not None else plan["after"]
    return {"element_id": plan["element_id"], "category": plan["category"], "name": plan["name"],
            "before": {"created_phase": _phase_name(phases, created),
                       "demolished_phase": _phase_name(phases, demolished)},
            "after": {"created_phase": _phase_name(phases, new_created),
                      "demolished_phase": _phase_name(phases, new_demolished)},
            "changed": plan["before"] != (new_created, new_demolished)}


def select_elements(doc, uidoc, selector):
    """Model elements with phases matched by the selector. Returns (elements, skipped)."""
    view_id = selector.get("view_id")
    if view_id is not None:
        view = doc.GetElement(element_id_from_int(int(view_id)))
        if view is None or not isinstance(view, DB.View):
            raise ItemError("View {} not found".format(view_id))
        error = view_kind_error(view.ViewType, view.IsTemplate)
        if error:
            raise ItemError(error)
        # A view collector returns what the view shows, so its phase and phase
        # filter apply (e.g. demolished elements are absent from a "show new" view).
        collector = DB.FilteredElementCollector(doc, view.Id)
    else:
        collector = DB.FilteredElementCollector(doc)
    category_id = None
    if selector.get("category"):
        category = find_category(doc, selector["category"])
        if category is None:
            raise ItemError(u"Category '{}' not found".format(normalize_string(selector["category"])))
        category_id = element_id_value(category.Id)
    level_id = None
    if selector.get("level"):
        levels = DB.FilteredElementCollector(doc).OfClass(DB.Level).ToElements()
        level = None
        for candidate in levels:
            if same_name(element_name(candidate), selector["level"]):
                level = candidate
                break
        if level is None:
            raise ItemError(u"Level '{}' not found. Levels: {}".format(
                normalize_string(selector["level"]),
                u", ".join(element_name(l) for l in levels)))
        level_id = element_id_value(level.Id)
    selected = None
    if selector.get("selection"):
        if uidoc is None:
            raise ItemError("No active Revit UI document for the current selection")
        selected = set(element_id_value(i) for i in uidoc.Selection.GetElementIds())
        if not selected:
            raise ItemError("Nothing is selected in Revit")

    matched = []
    for element in _model_elements(doc, collector):
        if category_id is not None and element_id_value(element.Category.Id) != category_id:
            continue
        if selected is not None and element_id_value(element.Id) not in selected:
            continue
        if level_id is not None:
            try:
                element_level = element_id_value(element.LevelId)
            except Exception:
                element_level = None
            if element_level != level_id:
                continue
        matched.append(element)
    skipped = 0
    if selected is not None and category_id is None and level_id is None:
        skipped = len(selected) - len(matched)
    return matched, skipped


def plan_phases(doc, items):
    """Validate every item before any transaction.
    items: [{"index", "element_id", "element", "created", "demolished"}].
    Returns (phases, positions, plans, error_result or None)."""
    phases, positions = project_phases(doc)
    if not phases:
        return phases, positions, [], {"status": "error", "error": "The model has no phases",
                                       "changed": 0}
    plans = []
    errors = []
    for item in items:
        try:
            plans.append((item["index"], _plan(doc, item["element"], phases, positions,
                                               item["created"], item["demolished"])))
        except ItemError as e:
            errors.append({"index": item["index"], "element_id": item["element_id"],
                           "error": e.text})
        except Exception as e:
            errors.append({"index": item["index"], "element_id": item["element_id"],
                           "error": error_text(e)})
    if errors:
        return phases, positions, plans, {
            "status": "error",
            "error": "{} of {} elements are invalid; nothing was changed".format(
                len(errors), len(items)),
            "changed": 0, "errors": errors}
    return phases, positions, plans, None


def apply_phases(doc, phases, positions, plans, commit, skipped=0):
    """Apply validated plans in one batch (rolled back when commit is False)."""
    batch = Batch(doc, "MCP: set element phases")
    rows = []
    errors = []
    batch.start()
    try:
        for index, plan in plans:
            try:
                _apply(plan, phases)
                rows.append(_row(plan, phases, _positions(plan["element"], positions)))
            except Exception as e:
                errors.append({"index": index, "element_id": plan["element_id"],
                               "error": error_text(e)})
        if errors:
            batch.rollback()
            return {"status": "error",
                    "error": "{} of {} elements were rejected by Revit; nothing was changed".format(
                        len(errors), len(plans)),
                    "changed": 0, "errors": errors, "warnings": batch.warnings}
        if not batch.finish(commit):
            return {"status": "error",
                    "error": "Revit did not commit the phase changes ({}); nothing was changed".format(
                        getattr(batch, "status", None)),
                    "changed": 0, "errors": [{"error": e} for e in batch.errors],
                    "warnings": batch.warnings}
    except Exception:
        batch.abort()
        raise

    changed = len([row for row in rows if row["changed"]])
    result = {"results": rows, "matched": len(rows), "unchanged": len(rows) - changed,
              "warnings": batch.warnings, "phases": [name for _, name in phases]}
    if skipped:
        result["skipped_without_phases"] = skipped
    if not commit:
        result.update({"status": "dry_run", "changed": 0, "would_change": changed,
                       "message": "Dry run: Revit accepted the phases of {} elements ({} would "
                                  "change). Nothing was changed; call again with dry_run=false "
                                  "and expected_count={} to apply.".format(
                                      len(rows), changed, len(rows))})
    else:
        result.update({"status": "success", "changed": changed})
    return result


def set_element_phases(doc, items, commit, skipped=0):
    """Plan and apply (all or nothing)."""
    phases, positions, plans, failed = plan_phases(doc, items)
    if failed is not None:
        return failed
    return apply_phases(doc, phases, positions, plans, commit, skipped)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _payload(request):
    data = json.loads(request.data) if isinstance(request.data, str) else request.data
    return data or {}


def _bad(message, errors=None):
    body = {"error": message}
    if errors:
        body["errors"] = errors
    return routes.make_response(data=body, status=400)


def register_phase_routes(api):
    """Register phase routes with the API"""

    @api.route("/phases_report/", methods=["POST"])
    def phases_report_route(doc, request):
        """
        Phases, phase filters, element counts per created/demolished phase and
        views per phase.

        Expected JSON payload:
        {"categories": ["Paredes"], "by_category": true, "limit": 200}
        """
        denial = authorize_request(request, action=None)
        if denial is not None:
            return denial
        try:
            if not doc:
                return routes.make_response(data={"error": "No active Revit document"}, status=503)
            data = _payload(request)
            limit, error = parse_limit(data.get("limit"))
            if error:
                return _bad(error)
            categories = data.get("categories")
            if categories is not None and (
                    not isinstance(categories, list)
                    or not all(isinstance(c, basestring) and c.strip() for c in categories)):
                return _bad("categories must be a list of category names")
            try:
                result = phases_report(doc, categories, bool(data.get("by_category", False)), limit)
            except ItemError as e:
                return _bad(e.text)
            return routes.make_response(data=result)
        except Exception as e:
            logger.error("Error in phases_report route: %s", e)
            return routes.make_response(data={"error": error_text(e)}, status=500)

    @api.route("/set_element_phases/", methods=["POST"])
    def set_element_phases_route(doc, uidoc, request):
        """
        Set the created and/or demolished phase of many elements (all or nothing).

        Expected JSON payload (one of the two forms):
        {"dry_run": true,
         "changes": [{"element_id": 123, "created_phase": "Existente",
                      "demolished_phase": "Nova construção"}]}
        {"dry_run": true, "selector": {"category": "Paredes", "level": "Térreo"},
         "created_phase": "Existente", "demolished_phase": "none", "expected_count": 12}
        """
        # Authenticate first; the review dialog is shown below only for real writes.
        denial = authorize_request(request, action=None)
        if denial is not None:
            return denial
        try:
            if not doc:
                return routes.make_response(data={"error": "No active Revit document"}, status=503)
            data = _payload(request)
            dry_run = data.get("dry_run", True) is not False
            changes = data.get("changes")
            selector = data.get("selector")
            if (changes is None) == (selector is None):
                return _bad("Give either changes or selector (not both)")
            expected = data.get("expected_count")
            error = expected_count_error(expected)
            if error:
                return _bad(error)
            if changes is not None:
                if not isinstance(changes, list) or not changes:
                    return _bad("changes must be a non-empty list")
                if len(changes) > MAX_ITEMS:
                    return _bad("At most {} changes per call, got {}".format(MAX_ITEMS, len(changes)))
                errors = []
                for index, change in enumerate(changes):
                    error = validate_change(change)
                    if error:
                        errors.append({"index": index, "error": error})
                errors.extend(duplicate_errors(changes))
                if errors:
                    return _bad("Invalid changes", errors)
            else:
                error = validate_selector(selector) or phases_error(data)
                if error:
                    return _bad(error)

            if changes is not None:
                items = [{"index": index, "element_id": int(change["element_id"]),
                          "element": doc.GetElement(element_id_from_int(int(change["element_id"]))),
                          "created": change.get("created_phase"),
                          "demolished": change.get("demolished_phase")}
                         for index, change in enumerate(changes)]
                skipped = 0
            else:
                try:
                    elements, skipped = select_elements(doc, uidoc, selector)
                except ItemError as e:
                    return _bad(e.text)
                if not elements:
                    return _bad("No model element with phases matches the selector")
                if len(elements) > MAX_ITEMS:
                    return _bad("The selector matches {} elements; at most {} per call. Narrow it "
                                "with level, view_id or a selection.".format(len(elements), MAX_ITEMS))
                items = [{"index": index, "element_id": element_id_value(element.Id),
                          "element": element, "created": data.get("created_phase"),
                          "demolished": data.get("demolished_phase")}
                         for index, element in enumerate(elements)]

            phases, positions, plans, failed = plan_phases(doc, items)
            if failed is not None:
                return routes.make_response(data=failed)
            if expected is not None and expected != len(plans):
                # e.g. the selection changed between the dry run and the real run.
                return routes.make_response(data={
                    "status": "error", "changed": 0, "matched": len(plans),
                    "expected_count": expected,
                    "error": "The request now matches {} elements, not the expected {}; nothing "
                             "was changed. Run the dry run again.".format(len(plans), expected)},
                    status=409)

            if not dry_run:
                # Show what will change in the review dialog.
                if isinstance(request.data, dict):
                    request.data["_resolved_count"] = len(plans)
                    request.data["_element_ids"] = preview_ids(
                        [plan["element_id"] for _, plan in plans])
                denial = authorize_request(request, action="Set element phases")
                if denial is not None:
                    return denial
            return routes.make_response(
                data=apply_phases(doc, phases, positions, plans, not dry_run, skipped))
        except Exception as e:
            logger.error("Error in set_element_phases route: %s", e)
            return routes.make_response(data={"error": error_text(e)}, status=500)

    logger.info("Phase routes registered successfully")
