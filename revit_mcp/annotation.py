# -*- coding: UTF-8 -*-
"""
Annotation routes
Tags every element of a category in a view, in a single transaction
"""

from pyrevit import routes, DB
import json
import logging
from .security import authorize_request
from .transactions import Batch
from .utils import (
    normalize_string,
    element_id_value,
    element_id_from_int,
    find_category,
)

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 200
MAX_LIMIT = 1000
MAX_FAILURES_LISTED = 50

# Tag categories whose name is not "<category without plural s>Tags".
TAG_CATEGORY_OVERRIDES = {
    "OST_Stairs": ["OST_StairsTags"],
    "OST_StairsRailing": ["OST_StairsRailingTags"],
    "OST_StructuralFraming": ["OST_StructuralFramingTags"],
    "OST_MEPSpaces": ["OST_MEPSpaceTags"],
}

# Views that can hold tags (plans, elevations, sections, details, 3D).
TAGGABLE_VIEW_TYPES = ("FloorPlan", "CeilingPlan", "AreaPlan", "EngineeringPlan",
                       "Elevation", "Section", "Detail", "ThreeD")


def tag_category_candidates(builtin_name):
    """Tag BuiltInCategory names for a model category, best first.

    "OST_Doors" -> ["OST_DoorTags", "OST_MultiCategoryTags"]. The
    multi-category tag is always the last resort.
    """
    if not builtin_name:
        return ["OST_MultiCategoryTags"]
    names = list(TAG_CATEGORY_OVERRIDES.get(builtin_name, []))
    base = builtin_name[:-1] if builtin_name.endswith("s") else builtin_name
    for name in (base + "Tags", builtin_name + "Tags"):
        if name not in names:
            names.append(name)
    names.append("OST_MultiCategoryTags")
    return names


def tag_type_matches(requested, family_name, type_name):
    """True when a requested tag type name matches "Type", "Family" or
    "Family: Type" (case-insensitive, extra spaces ignored)."""
    def key(text):
        return u" ".join(normalize_string(text).lower().split()) if text else u""
    wanted = key(requested)
    if not wanted:
        return False
    family = key(family_name)
    kind = key(type_name)
    return wanted in (kind, family, family + u": " + kind, family + u" : " + kind)


def bbox_centre(minimum, maximum):
    """Midpoint (x, y, z) of a bounding box given its corner tuples."""
    return tuple((a + b) / 2.0 for a, b in zip(minimum, maximum))


def plan_candidates(elements, tagged_ids, skip_tagged, limit, unplaced=None):
    """Split elements into the ones to tag and the skipped ones.

    elements: list of (element_id_int, element). tagged_ids: set of ints already
    tagged in the view. unplaced: optional function(element) -> True when it
    cannot be tagged (unplaced or not enclosed room/area).
    Returns (to_tag, skipped_counts, truncated).
    """
    to_tag = []
    skipped = {}
    truncated = False
    for element_id, element in elements:
        if skip_tagged and element_id in tagged_ids:
            skipped["already_tagged"] = skipped.get("already_tagged", 0) + 1
            continue
        if unplaced is not None and unplaced(element):
            skipped["not_placed"] = skipped.get("not_placed", 0) + 1
            continue
        if len(to_tag) >= limit:
            truncated = True
            continue
        to_tag.append((element_id, element))
    return to_tag, skipped, truncated


def run_tagging(doc, to_tag, create_tag, commit):
    """Create one tag per element in a single transaction.

    create_tag(element) returns the new tag. Elements Revit refuses to tag are
    reported in "failed" and the others are kept (partial success). The
    transaction runs inside a TransactionGroup with a failures preprocessor:
    Revit warnings are returned instead of opening dialogs, and a dry run still
    goes through Revit's failure processing before the group is rolled back.
    Nothing is kept when commit is False or nothing was tagged.
    Returns a dict: tagged, tag_ids, failed, error, warnings, revit_errors.
    """
    outcome = {"tagged": [], "tag_ids": [], "failed": [], "error": None,
               "warnings": [], "revit_errors": []}
    tagged = outcome["tagged"]
    tag_ids = []
    batch = Batch(doc, "MCP: tag elements")
    batch.start()
    try:
        for element_id, element in to_tag:
            try:
                tag = create_tag(element)
                if tag is None:
                    raise ValueError("Revit returned no tag")
                tagged.append(element_id)
                tag_ids.append(element_id_value(tag.Id))
            except Exception as e:
                outcome["failed"].append({"element_id": element_id,
                                          "error": normalize_string(str(e))})

        if not tagged:
            batch.rollback()
        elif not batch.finish(commit):
            outcome["tagged"] = []
            outcome["error"] = "Revit did not commit the transaction ({})".format(batch.status)
        elif commit:
            outcome["tag_ids"] = tag_ids
    except Exception:
        batch.abort()
        raise
    outcome["warnings"] = batch.warnings
    outcome["revit_errors"] = batch.errors
    return outcome


def _safe_name(element):
    if element is None:
        return None
    try:
        return normalize_string(DB.Element.Name.GetValue(element))
    except Exception:
        try:
            return normalize_string(element.Name)
        except Exception:
            return None


def _parse_body(request):
    if not request or not request.data:
        return {}
    if isinstance(request.data, str):
        return json.loads(request.data)
    return request.data


def _builtin_category_name(category):
    try:
        return str(category.BuiltInCategory)
    except Exception:
        pass
    try:
        from System import Enum
        return str(Enum.ToObject(DB.BuiltInCategory, element_id_value(category.Id)))
    except Exception:
        return None


def _resolve_view(doc, uidoc, view_id, view_name):
    """Returns (view, error)."""
    if view_id is not None:
        view = doc.GetElement(element_id_from_int(int(view_id)))
        if view is None or not isinstance(view, DB.View):
            return None, "View {} not found".format(view_id)
        return view, None
    if view_name:
        wanted = u" ".join(normalize_string(view_name).lower().split())
        matches = []
        for view in DB.FilteredElementCollector(doc).OfClass(DB.View):
            if view.IsTemplate:
                continue
            name = _safe_name(view)
            if name and u" ".join(name.lower().split()) == wanted:
                matches.append(view)
        return pick_named_view(matches, view_name)
    view = uidoc.ActiveView if uidoc is not None else doc.ActiveView
    if view is None:
        return None, "No active view"
    return view, None


def pick_named_view(matches, view_name):
    """One view among the non-template views with the requested name.

    A floor plan and a ceiling plan (or a sheet) often share a name such as
    "Térreo". When several match, the ones that can hold tags are kept; if
    still more than one, the caller must use view_id. Returns (view, error)."""
    if not matches:
        return None, u"View '{}' not found".format(normalize_string(view_name))
    if len(matches) > 1:
        taggable = [v for v in matches if str(v.ViewType) in TAGGABLE_VIEW_TYPES]
        if len(taggable) == 1:
            return taggable[0], None
        if taggable:
            matches = taggable
        return None, u"{} views named '{}' ({}); use view_id".format(
            len(matches), normalize_string(view_name),
            u", ".join(sorted(set(str(v.ViewType) for v in matches))))
    return matches[0], None


def _view_error(view):
    if view.IsTemplate:
        return "Cannot tag in a view template"
    if str(view.ViewType) not in TAGGABLE_VIEW_TYPES:
        return u"View '{}' ({}) cannot hold tags; use a plan, section, elevation or " \
               u"3D view".format(_safe_name(view), view.ViewType)
    return None


def _tag_types(doc, category_names):
    """[(builtin_category, [symbols])] for every candidate tag category that has
    loaded types, best category first."""
    found = []
    for name in category_names:
        builtin = getattr(DB.BuiltInCategory, name, None)
        if builtin is None:
            continue
        symbols = list(DB.FilteredElementCollector(doc).OfCategory(builtin)
                       .WhereElementIsElementType())
        if symbols:
            found.append((builtin, symbols))
    return found


def _pick_tag_type(doc, tag_types, requested):
    """A requested tag type is searched in every candidate tag category (e.g. a
    multi-category tag for doors); otherwise the default type of the best
    category, else its first type. Returns the symbol or None."""
    if requested:
        for _, symbols in tag_types:
            for symbol in symbols:
                family = getattr(symbol, "FamilyName", None)
                if tag_type_matches(requested, family, _safe_name(symbol)):
                    return symbol
        return None
    builtin, symbols = tag_types[0]
    try:
        default_id = doc.GetDefaultFamilyTypeId(DB.ElementId(builtin))
        if default_id is not None and default_id != DB.ElementId.InvalidElementId:
            default = doc.GetElement(default_id)
            if default is not None:
                return default
    except Exception:
        pass
    return symbols[0]


def _tag_label(symbol):
    if symbol is None:
        return None
    family = getattr(symbol, "FamilyName", None)
    name = _safe_name(symbol)
    return u"{}: {}".format(normalize_string(family), name) if family else name


def _tagged_in_view(doc, view):
    """Ids (int) of elements that already have a tag in the view."""
    ids = set()
    for tag in DB.FilteredElementCollector(doc, view.Id).OfClass(DB.IndependentTag):
        try:
            for element_id in tag.GetTaggedLocalElementIds():  # Revit 2022+
                ids.add(element_id_value(element_id))
        except AttributeError:
            try:
                ids.add(element_id_value(tag.TaggedLocalElementId))
            except Exception:
                pass
        except Exception:
            pass
    for name, attribute in (("OST_RoomTags", "TaggedLocalRoomId"), ("OST_AreaTags", None)):
        builtin = getattr(DB.BuiltInCategory, name, None)
        if builtin is None:
            continue
        for tag in DB.FilteredElementCollector(doc, view.Id).OfCategory(builtin) \
                .WhereElementIsNotElementType():
            try:
                if attribute:
                    ids.add(element_id_value(getattr(tag, attribute)))
                else:
                    ids.add(element_id_value(tag.Area.Id))
            except Exception:
                pass
    return ids


def _is_unplaced_spatial(element):
    """Unplaced or not enclosed room/area (no location or zero area)."""
    try:
        return element.Location is None or not element.Area > 0
    except Exception:
        return True


def _tag_point(element, view):
    """XYZ for the tag head: location point, curve midpoint or bbox centre."""
    location = element.Location
    if isinstance(location, DB.LocationPoint):
        return location.Point
    if isinstance(location, DB.LocationCurve):
        return location.Curve.Evaluate(0.5, True)
    box = element.get_BoundingBox(view) or element.get_BoundingBox(None)
    if box is None:
        raise ValueError("Element has no location or bounding box")
    x, y, z = bbox_centre((box.Min.X, box.Min.Y, box.Min.Z), (box.Max.X, box.Max.Y, box.Max.Z))
    return DB.XYZ(x, y, z)


def _tag_creator(doc, view, kind, tag_type_id, add_leader):
    if kind == "room":
        def create(room):
            point = room.Location.Point
            tag = doc.Create.NewRoomTag(DB.LinkElementId(room.Id),
                                        DB.UV(point.X, point.Y), view.Id)
            if tag is not None and tag_type_id is not None and tag.GetTypeId() != tag_type_id:
                tag.ChangeTypeId(tag_type_id)
            return tag
        return create
    if kind == "area":
        def create(area):
            if str(view.ViewType) != "AreaPlan":
                raise ValueError("Area tags need an area plan view")
            point = area.Location.Point
            tag = doc.Create.NewAreaTag(view, area, DB.UV(point.X, point.Y))
            if tag is not None and tag_type_id is not None and tag.GetTypeId() != tag_type_id:
                tag.ChangeTypeId(tag_type_id)
            return tag
        return create

    def create(element):
        return DB.IndependentTag.Create(
            doc, tag_type_id, view.Id, DB.Reference(element), add_leader,
            DB.TagOrientation.Horizontal, _tag_point(element, view))
    return create


def register_annotation_routes(api):
    """Register annotation routes with the API"""

    @api.route("/tag_elements/", methods=["POST"])
    def tag_elements(doc, uidoc, request):
        """
        Tag every element of a category in a view.

        Expected JSON payload:
        {
            "category": "Doors",          // required: "Portas", "OST_Doors"...
            "view_id": 123,               // optional (default: active view)
            "view_name": "Térreo",        // optional
            "tag_type": "Door Tag",       // optional
            "add_leader": false,
            "skip_tagged": true,
            "limit": 200,
            "dry_run": true
        }
        """
        # Authenticate first; the review dialog is shown below only for real writes.
        denial = authorize_request(request, action=None)
        if denial is not None:
            return denial
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503)
            data = _parse_body(request) or {}
            dry_run = data.get("dry_run", True) is not False
            add_leader = bool(data.get("add_leader", False))
            skip_tagged = data.get("skip_tagged", True) is not False
            category_name = data.get("category")
            if not isinstance(category_name, basestring) or not category_name.strip():
                return routes.make_response(
                    data={"error": "category is required"}, status=400)
            try:
                limit = int(data.get("limit", DEFAULT_LIMIT))
            except (TypeError, ValueError):
                limit = 0
            if limit < 1 or limit > MAX_LIMIT:
                return routes.make_response(
                    data={"error": "limit must be between 1 and {}".format(MAX_LIMIT)},
                    status=400)

            view, error = _resolve_view(doc, uidoc, data.get("view_id"), data.get("view_name"))
            if error is None:
                error = _view_error(view)
            if error:
                return routes.make_response(data={"error": error}, status=400)

            category = find_category(doc, category_name)
            if category is None:
                return routes.make_response(
                    data={"error": u"Category '{}' not found".format(
                        normalize_string(category_name))}, status=400)
            builtin_name = _builtin_category_name(category)
            kind = {"OST_Rooms": "room", "OST_Areas": "area"}.get(builtin_name, "element")

            if kind == "room":
                tag_categories = ["OST_RoomTags"]
            elif kind == "area":
                tag_categories = ["OST_AreaTags"]
            else:
                tag_categories = tag_category_candidates(builtin_name)
            tag_types = _tag_types(doc, tag_categories)
            if not tag_types:
                return routes.make_response(
                    data={"error": u"No tag type loaded for '{}'; load a tag family "
                                   u"first".format(normalize_string(category.Name))},
                    status=400)
            requested_type = data.get("tag_type")
            symbol = _pick_tag_type(doc, tag_types, requested_type)
            if symbol is None:
                available = sorted(set(_tag_label(s) for _, symbols in tag_types
                                       for s in symbols))[:50]
                return routes.make_response(
                    data={"error": u"Tag type '{}' not found".format(
                        normalize_string(requested_type)), "available": available},
                    status=400)

            elements = []
            collector = DB.FilteredElementCollector(doc, view.Id).OfCategoryId(category.Id) \
                .WhereElementIsNotElementType()
            for element in collector:
                elements.append((element_id_value(element.Id), element))
            tagged_ids = _tagged_in_view(doc, view) if skip_tagged else set()
            to_tag, skipped, truncated = plan_candidates(
                elements, tagged_ids, skip_tagged, limit,
                _is_unplaced_spatial if kind in ("room", "area") else None)

            if not dry_run and to_tag:
                denial = authorize_request(request, action="Tag elements in a view")
                if denial is not None:
                    return denial

            # Room/area tags only change type when one was asked for.
            tag_type_id = symbol.Id if (kind == "element" or requested_type) else None
            create = _tag_creator(doc, view, kind, tag_type_id, add_leader)
            outcome = run_tagging(doc, to_tag, create, commit=not dry_run)
            tagged, failed, error = outcome["tagged"], outcome["failed"], outcome["error"]

            result = {
                "status": "dry_run" if dry_run else "success",
                "view": {"id": element_id_value(view.Id), "name": _safe_name(view)},
                "category": normalize_string(category.Name),
                "tag_type": _tag_label(symbol),
                "found": len(elements),
                "tagged": len(tagged),
                "skipped": skipped,
                "failed_count": len(failed),
                "failed": failed[:MAX_FAILURES_LISTED],
                "truncated": truncated,
                "warnings": outcome["warnings"],
            }
            if error:
                result["status"] = "error"
                result["error"] = error
                result["tagged"] = 0
                result["revit_errors"] = outcome["revit_errors"]
                return routes.make_response(data=result, status=500)
            if dry_run:
                result["message"] = ("Dry run: {} elements can be tagged. Nothing was changed; call "
                                  "again with dry_run=false to tag them.".format(len(tagged)))
            else:
                result["tag_ids"] = outcome["tag_ids"]
            return routes.make_response(data=result)

        except Exception as e:
            logger.error("Error in tag_elements route: %s", e)
            return routes.make_response(data={"error": str(e)}, status=500)
