# -*- coding: UTF-8 -*-
"""
Render material check (spec 014)
Checks the office's permanent material rule (one distinct, well-named
material per finish, really applied to the geometry) before exporting the
model to the renderer. Read-only: no transaction.

Materials are read per family type from ONE representative instance
(Element.GetMaterialIds), which is enough because instances of a type share
their geometry; materials driven by instance parameters may differ.
"""

from pyrevit import routes, DB
import json
import logging
import re
from .security import authorize_request
from .utils import normalize_string, element_id_value, element_id_from_int, find_category

logger = logging.getLogger(__name__)

try:
    import unicodedata
except ImportError:  # pragma: no cover - IronPython without the module
    unicodedata = None

DEFAULT_NAME_PATTERN = u"^[A-Z0-9]+(_[A-Z0-9]+){2,}_[0-9]{2}$"
DEFAULT_MAX_FAMILIES = 2
DEFAULT_MIN_MATERIALS = 2
DEFAULT_LIMIT = 200
MAX_LIMIT = 1000
MAX_LISTED = 20
# Categories where one material for the whole object is suspicious (rule 4)
# and the default categories checked when none is given.
FURNITURE_CATEGORIES = (
    "OST_Furniture", "OST_FurnitureSystems", "OST_Casework", "OST_GenericModel",
    "OST_PlumbingFixtures", "OST_LightingFixtures", "OST_SpecialityEquipment",
)
# Nested shared families read below the parent (sub-sub-components too).
MAX_SUBCOMPONENT_DEPTH = 3
MESH_ONLY_NOTE = (u"Geometry has only meshes (imported DWG/SKP/OBJ-like geometry, no Revit "
                  u"solids): materials may come from the import, check the render by eye.")
RULES = ("name_pattern", "duplicate_names", "generic_shared", "single_material",
         "category_material_only", "no_material")
_FALLBACK_ACCENTS = {
    u"á": u"a", u"à": u"a", u"â": u"a", u"ã": u"a", u"ä": u"a", u"é": u"e",
    u"ê": u"e", u"è": u"e", u"í": u"i", u"ì": u"i", u"ó": u"o", u"ô": u"o",
    u"õ": u"o", u"ò": u"o", u"ö": u"o", u"ú": u"u", u"ü": u"u", u"ù": u"u",
    u"ç": u"c", u"ñ": u"n",
}


# ---- pure helpers (also run under IronPython by scripts/ironpython_check.py) ----

def strip_accents(text):
    """u"AÇÃO" -> u"ACAO"; keeps case. None -> u""."""
    if text is None:
        return u""
    text = normalize_string(text)
    if unicodedata is not None:
        try:
            decomposed = unicodedata.normalize("NFKD", text)
            return u"".join(c for c in decomposed if not unicodedata.combining(c))
        except Exception:
            pass
    result = []
    for char in text:
        plain = _FALLBACK_ACCENTS.get(char.lower())
        if plain is None:
            result.append(char)
        else:
            result.append(plain.upper() if char.isupper() else plain)
    return u"".join(result)


def compile_pattern(pattern):
    """Returns (regex, error)."""
    if pattern is None:
        pattern = DEFAULT_NAME_PATTERN
    if not isinstance(pattern, basestring) or not pattern.strip():
        return None, "name_pattern must be a non-empty regular expression"
    try:
        return re.compile(pattern), None
    except Exception as e:
        return None, "name_pattern is not a valid regular expression: {}".format(e)


def name_ok(name, regex):
    """True when the name, without accents, matches the pattern."""
    return regex.search(strip_accents(name)) is not None


def strict_key(name):
    """Name without accents, case, spaces, underscores and punctuation."""
    return u"".join(c for c in strip_accents(name).lower() if c.isalnum())


def near_duplicate_key(name):
    """strict_key without the trailing number: "Madeira 01" ~ "MADEIRA_02"."""
    return strict_key(name).rstrip(u"0123456789")


def duplicate_groups(names):
    """Groups of 2+ material names that are the same once normalized.

    kind "duplicate": identical ignoring case/accents/spaces/underscores.
    kind "similar": they only differ in the trailing number.
    """
    by_key = {}
    for name in sorted(set(names)):
        key = near_duplicate_key(name)
        if key:
            by_key.setdefault(key, []).append(name)
    groups = []
    for key in sorted(by_key):
        members = by_key[key]
        if len(members) < 2:
            continue
        stricts = [strict_key(name) for name in members]
        kind = "duplicate" if len(set(stricts)) < len(stricts) else "similar"
        groups.append({"key": key, "kind": kind, "materials": members})
    return groups


def effective_materials(record):
    """Material ids that will reach the renderer: geometry + paint."""
    seen = []
    for material_id in list(record.get("material_ids") or []) + list(record.get("paint_ids") or []):
        if material_id not in seen:
            seen.append(material_id)
    return seen


def shared_materials(records, max_families):
    """[(material_id, sorted family names)] used by more than max_families families."""
    families = {}
    for record in records:
        for material_id in effective_materials(record):
            families.setdefault(material_id, set()).add(record.get("family") or u"")
    shared = []
    for material_id, names in families.items():
        if len(names) > max_families:
            shared.append((material_id, sorted(names)))
    shared.sort(key=lambda item: (-len(item[1]), item[0]))
    return shared


def merge_ids(first, second):
    """first + the ids of second not in it, order kept."""
    result = list(first or [])
    for value in second or []:
        if value not in result:
            result.append(value)
    return result


def piece_count(counts, subcomponents):
    """Pieces = solids with volume + meshes + nested shared subcomponents.
    counts: {"solids", "meshes"} or None when the geometry could not be read."""
    if counts is None:
        return None
    return counts.get("solids", 0) + counts.get("meshes", 0) + (subcomponents or 0)


def mesh_only(counts):
    return counts is not None and not counts.get("solids") and counts.get("meshes", 0) > 0


def type_problems(record, min_materials, names):
    """Rules 4-6 for one family type. names: {material_id: name}."""
    base = {"family": record.get("family"), "type": record.get("type"),
            "category": record.get("category"), "element_id": record.get("element_id")}
    if record.get("mesh_only"):
        base["note"] = MESH_ONLY_NOTE
    painted = [names.get(m, u"{}".format(m)) for m in record.get("paint_ids") or []]
    if painted:
        base["painted"] = painted
    geometry = list(record.get("material_ids") or [])
    category_material = record.get("category_material_id")
    problems = []

    def add(rule, severity, detail, material=None):
        problem = {"rule": rule, "severity": severity, "detail": detail}
        problem.update(base)
        if material is not None:
            problem["material"] = material
        problems.append(problem)

    if not painted:
        if not geometry and category_material is None:
            add("no_material", "error",
                u"No material in the geometry and the category has no material "
                u"(<By Category>): the renderer gets a default material.")
            return problems
        if not geometry or geometry == [category_material]:
            add("category_material_only", "error",
                u"Only the category material reaches the geometry: the finishes are not "
                u"separated in the family.",
                material=names.get(category_material) if category_material is not None else None)
            return problems

    used = effective_materials(record)
    pieces = record.get("pieces")
    required = min_materials if pieces is None else min(min_materials, pieces)
    if record.get("is_furniture") and used and len(used) < required:
        add("single_material", "review",
            u"{} material(s) for {} geometry piece(s): check that each finish has its own "
            u"material (can be intentional).".format(
                len(used), u"?" if pieces is None else pieces),
            material=names.get(used[0]) if len(used) == 1 else None)
    return problems


def check_records(records, names, regex, max_families, min_materials):
    """All rules. records: one per family type; names: {material_id: name}.

    Returns (summary, problems) with errors before reviews.
    """
    problems = []
    used_ids = set()
    for record in records:
        used_ids.update(effective_materials(record))
        problems.extend(type_problems(record, min_materials, names))

    used_names = [names[m] for m in sorted(used_ids) if m in names]
    for name in sorted(set(used_names)):
        if not name_ok(name, regex):
            problems.append({"rule": "name_pattern", "severity": "error", "material": name,
                             "detail": u"Name does not follow the pattern OBJETO_MATERIAL_PARTE_NN."})
    for group in duplicate_groups(used_names):
        problems.append({
            "rule": "duplicate_names",
            "severity": "error" if group["kind"] == "duplicate" else "review",
            "materials": group["materials"],
            "detail": u"{} names that are the same once normalized ({}): {}.".format(
                len(group["materials"]), group["kind"], u", ".join(group["materials"]))})
    for material_id, families in shared_materials(records, max_families):
        problems.append({
            "rule": "generic_shared", "severity": "error",
            "material": names.get(material_id, u"{}".format(material_id)),
            "families": families[:MAX_LISTED],
            "detail": u"Used by {} different families (max {}): one generic material for "
                      u"different objects.".format(len(families), max_families)})

    summary = dict((rule, 0) for rule in RULES)
    for problem in problems:
        summary[problem["rule"]] += 1
    summary["painted_types"] = sum(1 for r in records if r.get("paint_ids"))
    summary["mesh_only_types"] = sum(1 for r in records if r.get("mesh_only"))
    order = {"error": 0, "review": 1}
    problems.sort(key=lambda p: (order.get(p["severity"], 2), RULES.index(p["rule"])))
    return summary, problems


def cap(problems, limit):
    """(returned, truncated)"""
    return problems[:limit], len(problems) > limit


def parse_options(data):
    """Returns (options, error)."""
    options = {}
    for key, default, low in (("limit", DEFAULT_LIMIT, 1),
                              ("max_families_per_material", DEFAULT_MAX_FAMILIES, 1),
                              ("min_materials_per_family", DEFAULT_MIN_MATERIALS, 1)):
        value = data.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, long)):
            return None, "{} must be an integer".format(key)
        if key == "limit" and not low <= value <= MAX_LIMIT:
            return None, "limit must be between 1 and {}".format(MAX_LIMIT)
        if value < low:
            return None, "{} must be at least {}".format(key, low)
        options[key] = int(value)
    regex, error = compile_pattern(data.get("name_pattern"))
    if error:
        return None, error
    options["regex"] = regex
    categories = data.get("categories")
    if categories is not None:
        if not isinstance(categories, list) or not categories or not all(
                isinstance(c, basestring) and c.strip() for c in categories):
            return None, "categories must be a non-empty list of category names"
    options["categories"] = categories
    return options, None


# ---- Revit access ----

def _parse_body(request):
    if not request or not request.data:
        return {}
    if isinstance(request.data, str):
        return json.loads(request.data)
    return request.data


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


def _parameter_text(element, builtin):
    if element is None:
        return None
    try:
        parameter = element.get_Parameter(builtin)
    except Exception:
        return None
    if parameter is None:
        return None
    value = parameter.AsString() or parameter.AsValueString()
    return normalize_string(value) if value else None


def _ids(collection):
    return [element_id_value(i) for i in collection
            if element_id_value(i) != element_id_value(DB.ElementId.InvalidElementId)]


def _builtin_category(doc, name):
    try:
        return DB.Category.GetCategory(doc, getattr(DB.BuiltInCategory, name))
    except Exception:
        return None


def _resolve_categories(doc, requested):
    """Returns (categories, error)."""
    if requested is None:
        found = [_builtin_category(doc, name) for name in FURNITURE_CATEGORIES]
        return [c for c in found if c is not None], None
    categories, missing = [], []
    for name in requested:
        category = find_category(doc, name)
        if category is None:
            missing.append(normalize_string(name))
        elif element_id_value(category.Id) not in [element_id_value(c.Id) for c in categories]:
            categories.append(category)
    if missing:
        return None, u"Category not found: {}".format(u", ".join(missing))
    return categories, None


def _resolve_view(doc, ref):
    """Returns (view, error). ref: view id (int or digits) or view name."""
    if isinstance(ref, (int, long)) and not isinstance(ref, bool):
        view = doc.GetElement(element_id_from_int(int(ref)))
        if view is None or not isinstance(view, DB.View):
            return None, "View {} not found".format(ref)
        return view, None
    if not isinstance(ref, basestring) or not ref.strip():
        return None, "only_visible_in_view must be a view name or id"
    wanted = u" ".join(normalize_string(ref).lower().split())
    for view in DB.FilteredElementCollector(doc).OfClass(DB.View):
        if view.IsTemplate:
            continue
        name = _safe_name(view)
        if name and u" ".join(name.lower().split()) == wanted:
            return view, None
    if ref.strip().isdigit():
        return _resolve_view(doc, int(ref.strip()))
    return None, u"View '{}' not found".format(normalize_string(ref))


def _geometry_counts(element, options):
    """{"solids": solids with volume, "meshes": meshes} in the geometry, or None."""
    counts = {"solids": 0, "meshes": 0}
    mesh_class = getattr(DB, "Mesh", None)

    def walk(geometry):
        if geometry is None:
            return
        for item in geometry:
            if isinstance(item, DB.Solid):
                try:
                    if item.Volume > 0:
                        counts["solids"] += 1
                except Exception:
                    pass
            elif mesh_class is not None and isinstance(item, mesh_class):
                counts["meshes"] += 1
            elif isinstance(item, DB.GeometryInstance):
                walk(item.GetInstanceGeometry())

    try:
        walk(element.get_Geometry(options))
    except Exception:
        return None
    return counts


def _subcomponents(doc, element, depth=MAX_SUBCOMPONENT_DEPTH, seen=None):
    """Nested shared family instances under element (recursively, capped)."""
    seen = set() if seen is None else seen
    result = []
    if depth <= 0:
        return result
    try:
        ids = list(element.GetSubComponentIds())
    except Exception:
        return result
    for sub_id in ids:
        key = element_id_value(sub_id)
        if key in seen:
            continue
        seen.add(key)
        sub = doc.GetElement(sub_id)
        if sub is None:
            continue
        result.append(sub)
        result.extend(_subcomponents(doc, sub, depth - 1, seen))
    return result


def _material_ids(element):
    """(geometry material ids, paint material ids) of one element."""
    try:
        return _ids(element.GetMaterialIds(False)), _ids(element.GetMaterialIds(True))
    except Exception as e:
        logger.warning("Could not read materials of %s: %s", element_id_value(element.Id), e)
        return [], []


def collect_type_records(doc, categories, view):
    """One record per family type (first instance found represents the type)."""
    furniture_ids = set()
    for name in FURNITURE_CATEGORIES:
        category = _builtin_category(doc, name)
        if category is not None:
            furniture_ids.add(element_id_value(category.Id))
    options = DB.Options()
    invalid = element_id_value(DB.ElementId.InvalidElementId)
    records = {}
    elements = 0
    for category in categories:
        if view is not None:
            collector = DB.FilteredElementCollector(doc, view.Id)
        else:
            collector = DB.FilteredElementCollector(doc)
        collector = collector.OfCategoryId(category.Id).WhereElementIsNotElementType()
        category_name = normalize_string(category.Name)
        category_material = category.Material
        category_material_id = (element_id_value(category_material.Id)
                                if category_material is not None else None)
        is_furniture = element_id_value(category.Id) in furniture_ids
        for element in collector:
            elements += 1
            type_id = element_id_value(element.GetTypeId())
            key = type_id if type_id != invalid else -element_id_value(element.Id)
            record = records.get(key)
            if record is not None:
                record["instances"] += 1
                continue
            element_type = doc.GetElement(element.GetTypeId()) if type_id != invalid else None
            family = (_parameter_text(element_type, DB.BuiltInParameter.SYMBOL_FAMILY_NAME_PARAM)
                      or _parameter_text(element, DB.BuiltInParameter.ALL_MODEL_FAMILY_NAME)
                      or category_name)
            type_name = (_parameter_text(element_type, DB.BuiltInParameter.SYMBOL_NAME_PARAM)
                         or _safe_name(element_type) or _safe_name(element))
            material_ids, paint_ids = _material_ids(element)
            # Shared nested families are separate elements: their materials are
            # not in the parent's GetMaterialIds but reach the renderer with it.
            subs = _subcomponents(doc, element)
            for sub in subs:
                sub_materials, sub_paint = _material_ids(sub)
                material_ids = merge_ids(material_ids, sub_materials)
                paint_ids = merge_ids(paint_ids, sub_paint)
            counts = _geometry_counts(element, options) if is_furniture else None
            records[key] = {
                "family": family, "type": type_name, "category": category_name,
                "element_id": element_id_value(element.Id), "instances": 1,
                "material_ids": material_ids, "paint_ids": paint_ids,
                "category_material_id": category_material_id, "is_furniture": is_furniture,
                "pieces": piece_count(counts, len(subs)) if is_furniture else None,
                "subcomponents": len(subs),
                "mesh_only": mesh_only(counts),
            }
    return list(records.values()), elements


def material_names(doc, records):
    names = {}
    for record in records:
        ids = effective_materials(record)
        if record.get("category_material_id") is not None:
            ids.append(record["category_material_id"])
        for material_id in ids:
            if material_id not in names:
                material = doc.GetElement(element_id_from_int(material_id))
                names[material_id] = _safe_name(material) or u"{}".format(material_id)
    return names


def register_material_check_routes(api):
    """Register the render material check route with the API"""

    @api.route("/check_render_materials/", methods=["POST"])
    def check_render_materials(doc, request):
        """
        Check materials against the render rule before exporting to the renderer.

        Expected JSON payload (all optional):
        {
            "categories": ["Mobiliário", "OST_Casework"],
            "name_pattern": "^[A-Z0-9]+(_[A-Z0-9]+){2,}_[0-9]{2}$",
            "max_families_per_material": 2,
            "min_materials_per_family": 2,
            "only_visible_in_view": "3D Render",   // view name or id
            "limit": 200
        }
        """
        denial = authorize_request(request, action=None)
        if denial is not None:
            return denial
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503)
            data = _parse_body(request) or {}
            options, error = parse_options(data)
            if error:
                return routes.make_response(data={"error": error}, status=400)
            categories, error = _resolve_categories(doc, options["categories"])
            if error:
                return routes.make_response(data={"error": error}, status=400)
            view = None
            view_ref = data.get("only_visible_in_view")
            if view_ref is not None:
                view, error = _resolve_view(doc, view_ref)
                if error:
                    return routes.make_response(data={"error": error}, status=404)
                if view.IsTemplate:
                    return routes.make_response(
                        data={"error": "only_visible_in_view cannot be a view template"},
                        status=400)

            records, elements = collect_type_records(doc, categories, view)
            names = material_names(doc, records)
            summary, problems = check_records(
                records, names, options["regex"],
                options["max_families_per_material"], options["min_materials_per_family"])
            returned, truncated = cap(problems, options["limit"])
            used = set()
            for record in records:
                used.update(effective_materials(record))
            result = {
                "status": "success",
                "document": normalize_string(doc.Title),
                "categories": [normalize_string(c.Name) for c in categories],
                "name_pattern": options["regex"].pattern,
                "elements_checked": elements,
                "types_checked": len(records),
                "materials_checked": len(used),
                "summary": summary,
                "problems_total": len(problems),
                "returned": len(returned),
                "truncated": truncated,
                "problems": returned,
                "method": "one representative instance per family type (with its nested "
                          "shared families)",
            }
            if view is not None:
                result["view"] = {"id": element_id_value(view.Id), "name": _safe_name(view)}
            return routes.make_response(data=result)
        except Exception as e:
            logger.error("Error in check_render_materials route: %s", e)
            return routes.make_response(data={"error": str(e)}, status=500)
