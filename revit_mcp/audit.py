# -*- coding: UTF-8 -*-
"""
Template audit (read-only)
Inventory of the open project or template by section (browser organization,
levels, phases, view templates, filters, views, line styles, annotation types,
families, parameters, materials, schedules, sheets, export settings), flagging
unused items and naming violations, without changing the model.
"""

from pyrevit import routes, DB
import logging
import re
import time
from .security import authorize_request
from .utils import normalize_string, element_id_value, element_id_from_int
from .health import NON_PLACEABLE_VIEW_TYPES
from .exports import _parse_body

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 100
MAX_LIMIT = 1000
MAX_CATEGORIES = 20
FT_TO_M = 0.3048
SOLID_PATTERN = u"<solid>"
MULTI_CATEGORY = u"<multicategory>"
MANUAL = u"conferir a olho"
# Seconds after which the remaining sections are skipped (the MCP wrapper waits 300 s).
TIME_BUDGET_S = 240.0
SKIPPED_BY_BUDGET = u"skipped: time budget"

SECTIONS = (
    "browser_organization", "levels_grids", "phases", "view_templates", "filters",
    "views", "line_styles", "annotation_types", "families", "shared_parameters",
    "project_parameters", "materials", "schedules", "sheets", "export_settings",
    "purge_preview",
)
NAMING_KINDS = ("views", "view_templates", "sheets", "sheet_numbers", "schedules",
                "materials", "families", "levels")
PHASE_STATUSES = ("New", "Existing", "Demolished", "Temporary")
SKIPPED_VIEW_TYPES = set(["Schedule"])

# Office checklist (spec section 11.2): item -> sections, and what the API cannot audit.
CHECKLIST = [
    (1, u"Estrutura do navegador de projeto", ["browser_organization"], "parcial",
     u"regras de filtro e ordenação de cada organização"),
    (2, u"Níveis e eixos", ["levels_grids"], "api", None),
    (3, u"Fases de projeto", ["phases"], "api", None),
    (4, u"Filtros de fase e de elementos", ["phases", "filters"], "parcial",
     u"regras e sobreposições gráficas de cada filtro"),
    (5, u"Modelos de vista", ["view_templates"], "parcial",
     u"configurações internas de cada modelo (V/G, detalhe, escala)"),
    (6, u"Vistas de planta, corte, elevação, 3D e detalhamento", ["views"], "api", None),
    (7, u"Categorias e visibilidades", [], MANUAL,
     u"Visibilidade/Gráficos de cada modelo de vista"),
    (8, u"Escalas e padrões gráficos", ["views", "line_styles"], "parcial",
     u"aparência gráfica nas pranchas impressas"),
    (9, u"Estilos de linha e pesos de linha", ["line_styles"], "parcial",
     u"tabela de espessuras (Gerenciar > Espessuras de linha)"),
    (10, u"Cotas, textos, símbolos e anotações", ["annotation_types"], "parcial",
     u"aparência de cada tipo (fonte, tamanho, marcas)"),
    (11, u"Famílias carregadas e famílias padrão", ["families", "annotation_types"], "parcial",
     u"tipo padrão de cada categoria"),
    (12, u"Parâmetros compartilhados", ["shared_parameters", "project_parameters"], "api", None),
    (13, u"Materiais", ["materials"], "parcial", u"aparência e ativos de renderização"),
    (14, u"Tabelas e quantitativos", ["schedules"], "parcial",
     u"campos, filtros e formatação de cada tabela"),
    (15, u"Pranchas, carimbos e organização de folhas", ["sheets"], "parcial",
     u"conteúdo gráfico do carimbo"),
    (16, u"Nomenclatura de vistas, folhas e arquivos", ["naming"], "parcial",
     u"nomes de arquivos fora do modelo"),
    (17, u"Padrões de exportação", ["export_settings"], "parcial",
     u"camadas, cores e opções dentro de cada configuração"),
    (18, u"Organização das fases existente, demolição e construção nova", ["phases"], "parcial",
     u"sobreposições gráficas de fase (Gerenciar > Fases)"),
    (19, u"Regras para preparar arquivos de entrega", [], MANUAL,
     u"procedimento do escritório, fora do modelo"),
]


# ---- pure helpers (also checked under IronPython by scripts/ironpython_check.py) ----

def feet_to_m(value, digits=3):
    if value is None:
        return None
    return round(value * FT_TO_M, digits)


def rgb_hex(red, green, blue):
    return "#{:02X}{:02X}{:02X}".format(int(red), int(green), int(blue))


def sort_text(value):
    if value is None:
        return u""
    return normalize_string(value).lower()


def unused_first(items, key="name"):
    """Unused items first (so the limit does not hide them), then by name."""
    return sorted(items, key=lambda item: (0 if item.get("unused") else 1,
                                           sort_text(item.get(key))))


def summarize(items, limit, tracks_unused=True, rule=None):
    """{count, unused_count, items[:limit], truncated}; unused_count is None
    when the section cannot tell what is unused."""
    summary = {
        "count": len(items),
        "unused_count": (sum(1 for item in items if item.get("unused"))
                         if tracks_unused else None),
        "items": items[:limit],
        "truncated": len(items) > limit,
    }
    if rule and tracks_unused:
        summary["unused_rule"] = rule
    return summary


def compile_naming(naming):
    """{kind: regex} -> ({kind: compiled}, error). Each regex must match the whole name."""
    if naming is None:
        return {}, None
    if not isinstance(naming, dict) or not naming:
        return None, u"naming must be an object like {\"views\": \"^[A-Z]{2}-.+\"}"
    compiled = {}
    for kind in sorted(naming):
        pattern = naming[kind]
        if kind not in NAMING_KINDS:
            return None, u"naming: unknown kind '{}'; valid: {}".format(
                kind, ", ".join(NAMING_KINDS))
        if not isinstance(pattern, basestring) or not pattern.strip():
            return None, u"naming: the pattern for '{}' must be a non-empty text".format(kind)
        try:
            compiled[kind] = (pattern, re.compile(u"(?:{})\\Z".format(pattern), re.UNICODE))
        except re.error as e:
            return None, u"naming: invalid regex for '{}': {}".format(kind, e)
    return compiled, None


def parse_request(data):
    """Returns (options, error). options: sections, limit, naming."""
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return None, "the request body must be a JSON object"
    sections = data.get("sections")
    if sections is None:
        chosen = list(SECTIONS)
    else:
        if isinstance(sections, basestring) or not isinstance(sections, (list, tuple)) \
                or not sections:
            return None, "sections must be a non-empty list"
        unknown = [u"{}".format(name) for name in sections if name not in SECTIONS]
        if unknown:
            return None, u"unknown sections: {}; valid: {}".format(
                u", ".join(unknown), u", ".join(SECTIONS))
        chosen = [name for name in SECTIONS if name in sections]
    limit = data.get("limit", DEFAULT_LIMIT)
    if isinstance(limit, bool) or not isinstance(limit, (int, long)) \
            or limit < 1 or limit > MAX_LIMIT:
        return None, "limit must be an integer between 1 and {}".format(MAX_LIMIT)
    naming, error = compile_naming(data.get("naming"))
    if error:
        return None, error
    return {"sections": chosen, "limit": int(limit), "naming": naming}, None


def naming_check(entries, pattern, regex, limit):
    """entries: [(id, name)]. Names that do not fully match the regex."""
    violations = [{"id": element_id, "name": name} for element_id, name in entries
                  if name is None or regex.match(name) is None]
    return {"pattern": pattern, "checked": len(entries),
            "violations_count": len(violations), "violations": violations[:limit],
            "truncated": len(violations) > limit}


def group_by_kind(entries, limit):
    """Purge preview entries [{kind, ...}] -> {count, by_kind, items, truncated}."""
    by_kind = {}
    for entry in entries:
        by_kind[entry["kind"]] = by_kind.get(entry["kind"], 0) + 1
    ordered = sorted(entries, key=lambda e: (e["kind"], sort_text(e.get("category")),
                                             sort_text(e.get("name"))))
    return {"count": len(entries), "by_kind": by_kind, "items": ordered[:limit],
            "truncated": len(entries) > limit}


def checklist(sections, naming_requested):
    """The 19 checklist items with the section keys present in this response."""
    present = set(sections)
    if naming_requested:
        present.add("naming")
    result = []
    for number, title, keys, coverage, manual in CHECKLIST:
        entry = {"item": number, "title": title, "coverage": coverage,
                 "sections": [key for key in keys if key in present]}
        if manual:
            entry["check_manually"] = manual
        result.append(entry)
    return result


# ---- Revit access ----

class AuditContext(object):
    """Caches collectors shared by several sections in one request."""

    def __init__(self, doc):
        self.doc = doc
        self.cache = {}

    def get(self, key, build):
        if key not in self.cache:
            self.cache[key] = build()
        return self.cache[key]


def element_name(element):
    if element is None:
        return None
    try:
        return normalize_string(DB.Element.Name.GetValue(element))
    except Exception:
        try:
            return normalize_string(element.Name)
        except Exception:
            return None


def class_name(element):
    try:
        return str(element.GetType().Name)
    except Exception:
        return type(element).__name__


def id_or_none(element_id):
    if element_id is None:
        return None
    try:
        value = element_id_value(element_id)
    except Exception:
        return None
    return None if value == -1 else value


def symbol_label(symbol):
    family = None
    try:
        family = normalize_string(symbol.FamilyName)
    except Exception:
        pass
    name = element_name(symbol)
    return u"{}: {}".format(family, name) if family else name


def category_name(category):
    if category is None:
        return None
    try:
        return normalize_string(category.Name)
    except Exception:
        return None


def _collect(doc, cls):
    if cls is None:
        return []
    return list(DB.FilteredElementCollector(doc).OfClass(cls).ToElements())


def _views(ctx):
    return ctx.get("views", lambda: _collect(ctx.doc, DB.View))


def _non_template_views(ctx):
    return ctx.get("non_template_views",
                   lambda: [view for view in _views(ctx) if not view.IsTemplate])


def _templates(ctx):
    return ctx.get("templates", lambda: [view for view in _views(ctx) if view.IsTemplate])


def _symbols(ctx):
    return ctx.get("symbols", lambda: _collect(ctx.doc, DB.FamilySymbol))


def _schedule_instances(ctx):
    def build():
        result = []
        for instance in _collect(ctx.doc, DB.ScheduleSheetInstance):
            try:
                if instance.IsTitleblockRevisionSchedule:
                    continue
            except Exception:
                pass
            result.append(instance)
        return result
    return ctx.get("schedule_instances", build)


def _viewports(ctx):
    return ctx.get("viewports", lambda: _collect(ctx.doc, DB.Viewport))


def _on_sheets(ctx):
    def build():
        ids = set()
        for viewport in _viewports(ctx):
            ids.add(element_id_value(viewport.ViewId))
        for instance in _schedule_instances(ctx):
            ids.add(element_id_value(instance.ScheduleId))
        return ids
    return ctx.get("on_sheets", build)


def _type_counts(ctx):
    """One pass over every placed element: type id -> instance count."""
    def build():
        counts = {}
        collector = DB.FilteredElementCollector(ctx.doc).WhereElementIsNotElementType()
        for element in collector:
            try:
                key = id_or_none(element.GetTypeId())
            except Exception:
                continue
            if key is not None:
                counts[key] = counts.get(key, 0) + 1
        return counts
    return ctx.get("type_counts", build)


def _empty_id_set():
    from System.Collections.Generic import HashSet
    return HashSet[DB.ElementId]()


def purge_result(ids, unused_symbols_by_count):
    """Ids from GetUnusedElements, or None to fall back to instance counts.

    An empty answer while instance counting finds unused family types means the
    call did not work as expected (e.g. the empty category set was not read as
    "all categories"); then the instance-count method is used instead."""
    if ids is None:
        return None
    ids = set(ids)
    if not ids and unused_symbols_by_count:
        return None
    return ids


def _unused_symbol_count(ctx):
    counts = _type_counts(ctx)
    return sum(1 for symbol in _symbols(ctx) if counts.get(element_id_value(symbol.Id), 0) == 0)


def _purgeable(ctx):
    """Ids Revit's Purge Unused would list (Document.GetUnusedElements, Revit
    2024+), or None when the API is not available or answers nothing although
    instance counts find unused types."""
    def build():
        try:
            ids = ctx.doc.GetUnusedElements(_empty_id_set())
        except Exception as e:
            logger.info("GetUnusedElements not available: %s", e)
            return None
        values = [element_id_value(element_id) for element_id in ids]
        if values:
            return set(values)
        result = purge_result(values, _unused_symbol_count(ctx))
        if result is None:
            ctx.cache["purge_fallback"] = True
            logger.info("GetUnusedElements returned nothing; using instance counts")
        return result
    return ctx.get("purgeable", build)


def is_unused(ctx, element_id, fallback):
    purgeable = _purgeable(ctx)
    if purgeable is None:
        return fallback
    return element_id in purgeable


def _view_type(view):
    try:
        return str(view.ViewType)
    except Exception:
        return None


def _param_element_id(element, builtin):
    try:
        parameter = element.get_Parameter(builtin)
        return id_or_none(parameter.AsElementId()) if parameter is not None else None
    except Exception:
        return None


def parameter_label(doc, parameter_id):
    value = element_id_value(parameter_id)
    if value < 0:
        try:
            from System import Enum
            return normalize_string(DB.LabelUtils.GetLabelFor(
                Enum.ToObject(DB.BuiltInParameter, value)))
        except Exception:
            return u"BuiltInParameter {}".format(value)
    return element_name(doc.GetElement(parameter_id))


# ---- sections ----

def section_browser_organization(ctx, limit):
    doc = ctx.doc
    current = {}
    samples = {"Views": [v for v in _non_template_views(ctx)
                         if _view_type(v) not in ("DrawingSheet", "Schedule")],
               "Sheets": _collect(doc, DB.ViewSheet)}
    for kind in ("Views", "Sheets", "Schedules"):
        try:
            organization = getattr(DB.BrowserOrganization,
                                   "GetCurrentBrowserOrganizationFor" + kind)(doc)
        except Exception:
            continue
        if organization is not None:
            current[element_id_value(organization.Id)] = samples.get(kind, [])
    items = []
    for organization in _collect(doc, DB.BrowserOrganization):
        key = element_id_value(organization.Id)
        item = {"id": key, "name": element_name(organization),
                "type": u"{}".format(getattr(organization, "Type", u"")) or None,
                "current": key in current}
        if key in current:
            item["folders"] = _folder_parameters(doc, organization, current[key])
        items.append(item)
    items.sort(key=lambda i: (0 if i["current"] else 1, sort_text(i["type"]), sort_text(i["name"])))
    return summarize(items, limit, tracks_unused=False)


def _folder_parameters(doc, organization, elements):
    """Grouping parameters of a browser organization, read from the folder
    path of the first elements it organizes."""
    for element in elements[:20]:
        try:
            folders = list(organization.GetFolderItems(element.Id))
        except Exception:
            continue
        if folders:
            return [parameter_label(doc, info.ElementId) for info in folders]
    return []


def section_levels_grids(ctx, limit):
    plans = {}
    for view in _non_template_views(ctx):
        try:
            level = view.GenLevel
        except Exception:
            continue
        if level is not None:
            key = element_id_value(level.Id)
            plans[key] = plans.get(key, 0) + 1
    levels = []
    for level in _collect(ctx.doc, DB.Level):
        key = element_id_value(level.Id)
        levels.append({"id": key, "name": element_name(level),
                       "elevation_m": feet_to_m(level.Elevation),
                       "plan_views": plans.get(key, 0)})
    levels.sort(key=lambda i: (i["elevation_m"] is None, i["elevation_m"]))
    grids = [{"id": element_id_value(grid.Id), "name": element_name(grid)}
             for grid in _collect(ctx.doc, DB.Grid)]
    grids.sort(key=lambda i: sort_text(i["name"]))
    level_summary = summarize(levels, limit, tracks_unused=False)
    level_summary["without_plan_view"] = sum(1 for i in levels if not i["plan_views"])
    return {"levels": level_summary, "grids": summarize(grids, limit, tracks_unused=False)}


def section_phases(ctx, limit):
    views_by_phase, views_by_filter = {}, {}
    for view in _views(ctx):
        phase = _param_element_id(view, DB.BuiltInParameter.VIEW_PHASE)
        if phase is not None and not view.IsTemplate:
            views_by_phase[phase] = views_by_phase.get(phase, 0) + 1
        phase_filter = _param_element_id(view, DB.BuiltInParameter.VIEW_PHASE_FILTER)
        if phase_filter is not None:
            views_by_filter[phase_filter] = views_by_filter.get(phase_filter, 0) + 1
    phases = []
    for order, phase in enumerate(ctx.doc.Phases):
        key = element_id_value(phase.Id)
        phases.append({"order": order + 1, "id": key, "name": element_name(phase),
                       "views": views_by_phase.get(key, 0)})
    filters = []
    for phase_filter in _collect(ctx.doc, DB.PhaseFilter):
        key = element_id_value(phase_filter.Id)
        uses = views_by_filter.get(key, 0)
        item = {"id": key, "name": element_name(phase_filter), "views_and_templates": uses,
                "unused": uses == 0}
        presentation = {}
        for status in PHASE_STATUSES:
            try:
                presentation[status.lower()] = str(phase_filter.GetPhaseStatusPresentation(
                    getattr(DB.ElementOnPhaseStatus, status)))
            except Exception:
                pass
        if presentation:
            item["presentation"] = presentation
        filters.append(item)
    return {"phases": summarize(phases, limit, tracks_unused=False),
            "phase_filters": summarize(unused_first(filters), limit,
                                       rule="no view or view template uses it")}


def _view_filter_ids(view):
    try:
        return [element_id_value(i) for i in view.GetFilters()]
    except Exception:
        return []


def section_view_templates(ctx, limit):
    users = {}
    for view in _non_template_views(ctx):
        key = id_or_none(view.ViewTemplateId)
        if key is not None:
            users[key] = users.get(key, 0) + 1
    items = []
    for template in _templates(ctx):
        key = element_id_value(template.Id)
        count = users.get(key, 0)
        items.append({"id": key, "name": element_name(template),
                      "view_type": _view_type(template), "views": count,
                      "filters": len(_view_filter_ids(template)), "unused": count == 0})
    return summarize(unused_first(items), limit, rule="no view uses this template")


def section_filters(ctx, limit):
    in_views, in_templates = {}, {}
    for view in _views(ctx):
        target = in_templates if view.IsTemplate else in_views
        for key in _view_filter_ids(view):
            target[key] = target.get(key, 0) + 1
    items = []
    for cls, kind in ((DB.ParameterFilterElement, "parameter"),
                      (DB.SelectionFilterElement, "selection")):
        for element in _collect(ctx.doc, cls):
            key = element_id_value(element.Id)
            views, templates = in_views.get(key, 0), in_templates.get(key, 0)
            item = {"id": key, "name": element_name(element), "kind": kind,
                    "views": views, "templates": templates,
                    "unused": views == 0 and templates == 0}
            if kind == "parameter":
                try:
                    item["categories"] = len(list(element.GetCategories()))
                except Exception:
                    pass
            items.append(item)
    return summarize(unused_first(items), limit, rule="not applied to any view or template")


def _scale(view):
    try:
        scale = int(view.Scale)
    except Exception:
        return None
    return u"1:{}".format(scale) if scale > 0 else None


def _placeable_views(ctx):
    def build():
        result = []
        for view in _non_template_views(ctx):
            view_type = _view_type(view)
            if view_type in NON_PLACEABLE_VIEW_TYPES or view_type in SKIPPED_VIEW_TYPES:
                continue
            try:
                if not view.CanBePrinted:
                    continue
            except Exception:
                pass
            result.append(view)
        return result
    return ctx.get("placeable_views", build)


def section_views(ctx, limit):
    on_sheets = _on_sheets(ctx)
    template_names = dict((element_id_value(t.Id), element_name(t)) for t in _templates(ctx))
    by_type, scales, items = {}, {}, []
    for view in _placeable_views(ctx):
        key = element_id_value(view.Id)
        view_type = _view_type(view)
        scale = _scale(view)
        by_type[view_type] = by_type.get(view_type, 0) + 1
        if scale:
            scales[scale] = scales.get(scale, 0) + 1
        template = id_or_none(view.ViewTemplateId)
        on_sheet = key in on_sheets
        items.append({"id": key, "name": element_name(view), "view_type": view_type,
                      "scale": scale,
                      "template": template_names.get(template) if template is not None else None,
                      "on_sheet": on_sheet, "unused": not on_sheet})
    summary = summarize(unused_first(items), limit, rule="not placed on any sheet")
    summary["by_type"] = by_type
    summary["scales"] = scales
    summary["without_template"] = sum(1 for i in items if i["template"] is None)
    return summary


def _line_color(subcategory):
    try:
        color = subcategory.LineColor
        if not color.IsValid:
            return None
        return rgb_hex(color.Red, color.Green, color.Blue)
    except Exception:
        return None


def _line_pattern_name(doc, subcategory):
    try:
        pattern_id = subcategory.GetLinePatternId(DB.GraphicsStyleType.Projection)
    except Exception:
        return None
    try:
        if pattern_id == DB.LinePatternElement.GetSolidPatternId():
            return SOLID_PATTERN
    except Exception:
        pass
    if id_or_none(pattern_id) is None:
        return SOLID_PATTERN
    return element_name(doc.GetElement(pattern_id))


def section_line_styles(ctx, limit):
    doc = ctx.doc
    lines_by_style = {}
    for curve in _collect(doc, DB.CurveElement):
        try:
            key = element_id_value(curve.LineStyle.Id)
        except Exception:
            continue
        lines_by_style[key] = lines_by_style.get(key, 0) + 1
    styles = []
    lines = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Lines)
    for subcategory in (lines.SubCategories if lines is not None else []):
        key = element_id_value(subcategory.Id)
        try:
            style_key = element_id_value(
                subcategory.GetGraphicsStyle(DB.GraphicsStyleType.Projection).Id)
        except Exception:
            style_key = None
        count = lines_by_style.get(style_key, 0)
        builtin = key < 0
        try:
            weight = subcategory.GetLineWeight(DB.GraphicsStyleType.Projection)
        except Exception:
            weight = None
        styles.append({"id": key, "name": category_name(subcategory), "weight": weight,
                       "color": _line_color(subcategory),
                       "pattern": _line_pattern_name(doc, subcategory),
                       "lines": count, "builtin": builtin,
                       "unused": (not builtin) and count == 0})
    patterns = [{"id": element_id_value(p.Id), "name": element_name(p),
                 "unused": is_unused(ctx, element_id_value(p.Id), None)}
                for p in _collect(doc, DB.LinePatternElement)]
    fills = []
    for fill in _collect(doc, DB.FillPatternElement):
        key = element_id_value(fill.Id)
        try:
            target = str(fill.GetFillPattern().Target)
        except Exception:
            target = None
        fills.append({"id": key, "name": element_name(fill), "target": target,
                      "unused": is_unused(ctx, key, None)})
    purge = _purgeable(ctx) is not None
    return {"styles": summarize(unused_first(styles), limit,
                                rule="user line style with no lines in the project "
                                     "(may still be used inside families)"),
            "line_patterns": summarize(unused_first(patterns), limit, tracks_unused=purge,
                                       rule="listed by Revit Purge Unused"),
            "fill_patterns": summarize(unused_first(fills), limit, tracks_unused=purge,
                                       rule="listed by Revit Purge Unused")}


def _type_items(ctx, elements, extra):
    counts = _type_counts(ctx)
    items = []
    for element in elements:
        name = element_name(element)
        if not name:
            continue
        key = element_id_value(element.Id)
        instances = counts.get(key, 0)
        item = {"id": key, "name": name, "instances": instances,
                "unused": is_unused(ctx, key, instances == 0)}
        item.update(extra(element))
        items.append(item)
    return unused_first(items)


def section_annotation_types(ctx, limit):
    def style(element):
        try:
            return {"style": str(element.StyleType)}
        except Exception:
            return {}

    dimensions = _type_items(ctx, _collect(ctx.doc, DB.DimensionType), style)
    by_style = {}
    for item in dimensions:
        by_style[item.get("style")] = by_style.get(item.get("style"), 0) + 1
    texts = _type_items(ctx, _collect(ctx.doc, DB.TextNoteType), lambda e: {})

    title_blocks = element_id_value(DB.ElementId(DB.BuiltInCategory.OST_TitleBlocks))
    annotation_symbols = []
    for symbol in _symbols(ctx):
        category = symbol.Category
        try:
            if category is None or category.CategoryType != DB.CategoryType.Annotation \
                    or element_id_value(category.Id) == title_blocks:
                continue
        except Exception:
            continue
        annotation_symbols.append(symbol)
    symbols = _type_items(ctx, annotation_symbols,
                          lambda s: {"name": symbol_label(s), "category": category_name(s.Category)})
    by_category = {}
    for item in symbols:
        entry = by_category.setdefault(item["category"], {"types": 0, "unused": 0})
        entry["types"] += 1
        entry["unused"] += 1 if item["unused"] else 0
    rule = "no placed instance (or listed by Revit Purge Unused)"
    dimension_summary = summarize(dimensions, limit, rule=rule)
    dimension_summary["by_style"] = by_style
    symbol_summary = summarize(unused_first(symbols), limit, rule=rule)
    symbol_summary["by_category"] = by_category
    return {"dimension_types": dimension_summary, "text_types": summarize(texts, limit, rule=rule),
            "tag_and_symbol_types": symbol_summary}


def section_families(ctx, limit):
    counts = _type_counts(ctx)
    purgeable = _purgeable(ctx)
    items, by_category = [], {}
    for family in _collect(ctx.doc, DB.Family):
        key = element_id_value(family.Id)
        symbol_ids = [element_id_value(i) for i in family.GetFamilySymbolIds()]
        instances = sum(counts.get(i, 0) for i in symbol_ids)
        in_place = bool(getattr(family, "IsInPlace", False))
        if purgeable is None:
            unused_types = sum(1 for i in symbol_ids if counts.get(i, 0) == 0)
            unused = instances == 0 and not in_place
        else:
            unused_types = sum(1 for i in symbol_ids if i in purgeable)
            unused = key in purgeable or (bool(symbol_ids) and unused_types == len(symbol_ids))
        category = category_name(family.FamilyCategory)
        items.append({"id": key, "name": element_name(family), "category": category,
                      "in_place": in_place, "types": len(symbol_ids),
                      "unused_types": unused_types, "instances": instances, "unused": unused})
        entry = by_category.setdefault(category, {"families": 0, "unused": 0})
        entry["families"] += 1
        entry["unused"] += 1 if unused else 0
    summary = summarize(unused_first(items), limit,
                        rule="no placed instance (or listed by Revit Purge Unused)")
    summary["by_category"] = by_category
    return summary


def _bindings(ctx):
    """Parameter element id -> {name, binding, categories} for project bindings."""
    def build():
        result = {}
        iterator = ctx.doc.ParameterBindings.ForwardIterator()
        iterator.Reset()
        while iterator.MoveNext():
            definition, binding = iterator.Key, iterator.Current
            try:
                key = element_id_value(definition.Id)
            except Exception:
                continue
            categories = []
            try:
                for category in binding.Categories:
                    categories.append(category_name(category))
            except Exception:
                pass
            categories.sort(key=sort_text)
            result[key] = {"name": normalize_string(definition.Name),
                           "binding": "instance" if isinstance(binding, DB.InstanceBinding)
                           else "type",
                           "categories": categories[:MAX_CATEGORIES],
                           "categories_count": len(categories)}
        return result
    return ctx.get("bindings", build)


def section_shared_parameters(ctx, limit):
    bindings = _bindings(ctx)
    items = []
    for parameter in _collect(ctx.doc, DB.SharedParameterElement):
        key = element_id_value(parameter.Id)
        item = {"id": key, "name": element_name(parameter),
                "guid": str(parameter.GuidValue), "bound": key in bindings}
        if key in bindings:
            binding = bindings[key]
            item.update({"binding": binding["binding"], "categories": binding["categories"],
                         "categories_count": binding["categories_count"]})
        items.append(item)
    items.sort(key=lambda i: sort_text(i["name"]))
    summary = summarize(items, limit, tracks_unused=False)
    summary["bound_count"] = sum(1 for i in items if i["bound"])
    return summary


def section_project_parameters(ctx, limit):
    shared = ctx.get("shared_ids", lambda: set(
        element_id_value(p.Id) for p in _collect(ctx.doc, DB.SharedParameterElement)))
    items = []
    for key, binding in _bindings(ctx).items():
        item = {"id": key, "shared": key in shared}
        item.update(binding)
        items.append(item)
    items.sort(key=lambda i: sort_text(i["name"]))
    return summarize(items, limit, tracks_unused=False)


def section_materials(ctx, limit):
    items = []
    for material in _collect(ctx.doc, DB.Material):
        key = element_id_value(material.Id)
        item = {"id": key, "name": element_name(material),
                "unused": is_unused(ctx, key, None)}
        for field, attribute in (("class", "MaterialClass"), ("category", "MaterialCategory")):
            try:
                value = getattr(material, attribute)
                item[field] = normalize_string(value) if value else None
            except Exception:
                pass
        items.append(item)
    tracked = _purgeable(ctx) is not None
    return summarize(unused_first(items), limit, tracks_unused=tracked,
                     rule="listed by Revit Purge Unused")


def _schedules(ctx):
    def build():
        result = []
        for schedule in _collect(ctx.doc, DB.ViewSchedule):
            try:
                if schedule.IsTemplate or schedule.IsTitleblockRevisionSchedule:
                    continue
            except Exception:
                continue
            result.append(schedule)
        return result
    return ctx.get("schedules", build)


def section_schedules(ctx, limit):
    on_sheets = _on_sheets(ctx)
    items = []
    for schedule in _schedules(ctx):
        key = element_id_value(schedule.Id)
        category, key_schedule = None, None
        try:
            definition = schedule.Definition
            key_schedule = bool(definition.IsKeySchedule)
            category_id = definition.CategoryId
            if id_or_none(category_id) is None:
                category = MULTI_CATEGORY
            else:
                category = category_name(DB.Category.GetCategory(ctx.doc, category_id))
        except Exception:
            pass
        on_sheet = key in on_sheets
        items.append({"id": key, "name": element_name(schedule), "category": category,
                      "key_schedule": key_schedule, "on_sheet": on_sheet,
                      "unused": not on_sheet})
    return summarize(unused_first(items), limit, rule="not placed on any sheet")


def section_sheets(ctx, limit):
    doc = ctx.doc
    placed = {}
    for viewport in _viewports(ctx):
        key = element_id_value(viewport.SheetId)
        placed[key] = placed.get(key, 0) + 1
    for instance in _schedule_instances(ctx):
        key = element_id_value(instance.OwnerViewId)
        placed[key] = placed.get(key, 0) + 1
    title_by_sheet, sheets_by_type = {}, {}
    collector = DB.FilteredElementCollector(doc).OfCategory(
        DB.BuiltInCategory.OST_TitleBlocks).WhereElementIsNotElementType()
    for instance in collector:
        type_key = id_or_none(instance.GetTypeId())
        sheets_by_type[type_key] = sheets_by_type.get(type_key, 0) + 1
        title_by_sheet[element_id_value(instance.OwnerViewId)] = type_key
    title_types = list(DB.FilteredElementCollector(doc).OfCategory(
        DB.BuiltInCategory.OST_TitleBlocks).WhereElementIsElementType().ToElements())
    labels = dict((element_id_value(t.Id), symbol_label(t)) for t in title_types)

    sheets = []
    for sheet in _collect(doc, DB.ViewSheet):
        if sheet.IsTemplate:
            continue
        key = element_id_value(sheet.Id)
        views = placed.get(key, 0)
        placeholder = bool(getattr(sheet, "IsPlaceholder", False))
        sheets.append({"id": key, "number": normalize_string(sheet.SheetNumber),
                       "name": element_name(sheet), "placeholder": placeholder,
                       "views": views, "title_block": labels.get(title_by_sheet.get(key)),
                       "unused": views == 0 and not placeholder})
    sheets.sort(key=lambda i: (0 if i["unused"] else 1, sort_text(i["number"])))
    blocks = []
    for title_type in title_types:
        key = element_id_value(title_type.Id)
        count = sheets_by_type.get(key, 0)
        blocks.append({"id": key, "name": labels.get(key), "sheets": count,
                       "unused": is_unused(ctx, key, count == 0)})
    sheet_summary = summarize(sheets, limit, rule="no view or schedule placed (placeholders excluded)")
    sheet_summary["placeholders"] = sum(1 for i in sheets if i["placeholder"])
    sheet_summary["without_title_block"] = sum(
        1 for i in sheets if i["title_block"] is None and not i["placeholder"])
    return {"sheets": sheet_summary,
            "title_blocks": summarize(unused_first(blocks), limit,
                                      rule="no sheet uses it (or listed by Revit Purge Unused)")}


def section_export_settings(ctx, limit):
    result = {}
    for key, cls_name in (("dwg", "ExportDWGSettings"), ("pdf", "ExportPDFSettings"),
                          ("print_settings", "PrintSetting"), ("view_sheet_sets", "ViewSheetSet")):
        cls = getattr(DB, cls_name, None)
        try:
            items = [{"id": element_id_value(e.Id), "name": element_name(e)}
                     for e in _collect(ctx.doc, cls)]
        except Exception as e:
            logger.info("Export settings %s not available: %s", cls_name, e)
            items = []
        items.sort(key=lambda i: sort_text(i["name"]))
        result[key] = summarize(items, limit, tracks_unused=False)
    try:
        active = DB.ExportDWGSettings.GetActivePredefinedSettings(ctx.doc)
        result["dwg"]["active"] = element_name(active) if active is not None else None
    except Exception:
        pass
    return result


def section_purge_preview(ctx, limit):
    doc = ctx.doc
    purgeable = _purgeable(ctx)
    entries = []
    if purgeable is not None:
        source = "revit_purge"
        for key in purgeable:
            element = _element(doc, key)
            if element is None:
                continue
            kind = class_name(element)
            name = symbol_label(element) if kind == "FamilySymbol" else element_name(element)
            entries.append({"id": key, "kind": kind,
                            "category": category_name(getattr(element, "Category", None)),
                            "name": name})
    else:
        source = "instance_count"
        counts = _type_counts(ctx)
        for symbol in _symbols(ctx):
            key = element_id_value(symbol.Id)
            if counts.get(key, 0) == 0:
                entries.append({"id": key, "kind": "FamilySymbol",
                                "category": category_name(symbol.Category),
                                "name": symbol_label(symbol)})
    preview = group_by_kind(entries, limit)
    preview["source"] = source
    templates = [i for i in section_view_templates(ctx, MAX_LIMIT)["items"] if i["unused"]]
    filters = [i for i in section_filters(ctx, MAX_LIMIT)["items"] if i["unused"]]
    preview["not_in_revit_purge"] = {
        "view_templates": summarize([{"id": i["id"], "name": i["name"]} for i in templates],
                                    limit, tracks_unused=False),
        "filters": summarize([{"id": i["id"], "name": i["name"]} for i in filters],
                             limit, tracks_unused=False),
    }
    preview["note"] = ("Preview only, nothing is deleted. Revit Purge Unused may need "
                       "several passes (nested families and types appear after the first).")
    return preview


def _element(doc, key):
    try:
        return doc.GetElement(element_id_from_int(key))
    except Exception:
        return None


def _naming_entries(ctx, kind):
    doc = ctx.doc
    if kind == "views":
        return [(element_id_value(v.Id), element_name(v)) for v in _placeable_views(ctx)]
    if kind == "view_templates":
        return [(element_id_value(v.Id), element_name(v)) for v in _templates(ctx)]
    if kind in ("sheets", "sheet_numbers"):
        sheets = [s for s in _collect(doc, DB.ViewSheet) if not s.IsTemplate]
        if kind == "sheets":
            return [(element_id_value(s.Id), element_name(s)) for s in sheets]
        return [(element_id_value(s.Id), normalize_string(s.SheetNumber)) for s in sheets]
    if kind == "schedules":
        return [(element_id_value(s.Id), element_name(s)) for s in _schedules(ctx)]
    cls = getattr(DB, {"materials": "Material", "families": "Family", "levels": "Level"}[kind])
    return [(element_id_value(e.Id), element_name(e)) for e in _collect(doc, cls)]


def section_naming(ctx, naming, limit):
    result = {}
    for kind in sorted(naming):
        pattern, regex = naming[kind]
        result[kind] = naming_check(_naming_entries(ctx, kind), pattern, regex, limit)
    return result


SECTION_BUILDERS = {
    "browser_organization": section_browser_organization,
    "levels_grids": section_levels_grids,
    "phases": section_phases,
    "view_templates": section_view_templates,
    "filters": section_filters,
    "views": section_views,
    "line_styles": section_line_styles,
    "annotation_types": section_annotation_types,
    "families": section_families,
    "shared_parameters": section_shared_parameters,
    "project_parameters": section_project_parameters,
    "materials": section_materials,
    "schedules": section_schedules,
    "sheets": section_sheets,
    "export_settings": section_export_settings,
    "purge_preview": section_purge_preview,
}


def build_audit(doc, options, budget_s=TIME_BUDGET_S, clock=None):
    """Sections in order; once budget_s seconds have passed, the remaining
    sections are listed in section_errors as skipped instead of built."""
    clock = clock or time.time
    started = clock()
    ctx = AuditContext(doc)
    limit = options["limit"]
    path = u""
    try:
        path = normalize_string(doc.PathName) if doc.PathName else u""
    except Exception:
        pass
    report = {"status": "success", "document": normalize_string(doc.Title),
              "is_template_file": path.lower().endswith(u".rte"),
              "revit_version": str(doc.Application.VersionNumber),
              "limit": limit, "sections": {}}
    errors = {}
    for name in options["sections"]:
        if clock() - started > budget_s:
            errors[name] = SKIPPED_BY_BUDGET
            continue
        try:
            report["sections"][name] = SECTION_BUILDERS[name](ctx, limit)
        except Exception as e:
            logger.warning("Audit section %s failed: %s", name, e)
            errors[name] = normalize_string(e)
    if options["naming"] and clock() - started > budget_s:
        errors["naming"] = SKIPPED_BY_BUDGET
    elif options["naming"]:
        try:
            report["sections"]["naming"] = section_naming(ctx, options["naming"], limit)
        except Exception as e:
            logger.warning("Audit naming failed: %s", e)
            errors["naming"] = normalize_string(e)
    if "purgeable" in ctx.cache:
        report["unused_source"] = ("revit_purge" if ctx.cache["purgeable"] is not None
                                   else "instance_count")
        if ctx.cache.get("purge_fallback"):
            report["unused_source_note"] = (
                "GetUnusedElements returned no ids although some family types have no "
                "placed instance: used the instance-count method")
    report["checklist"] = checklist(options["sections"], bool(options["naming"]))
    if errors:
        report["section_errors"] = errors
        skipped = [name for name in errors if errors[name] == SKIPPED_BY_BUDGET]
        if skipped:
            report["time_budget"] = {
                "seconds": budget_s, "skipped_sections": sorted(skipped),
                "hint": "call again with only the skipped sections"}
    return report


def register_audit_routes(api):
    """Register template audit routes with the API"""

    @api.route("/audit_template/", methods=["POST"])
    def audit_template(doc, request):
        """
        Read-only inventory of the open project or template.

        Expected JSON payload (all fields optional):
        {
            "sections": ["view_templates", "families"],   // default: all
            "limit": 100,                                 // items per list, 1-1000
            "naming": {"views": "^[A-Z]{2} - .+$"}        // regex per kind, full match
        }
        """
        denial = authorize_request(request, action=None)
        if denial is not None:
            return denial
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503)
            options, error = parse_request(_parse_body(request))
            if error:
                return routes.make_response(data={"error": error}, status=400)
            return routes.make_response(data=build_audit(doc, options))
        except Exception as e:
            logger.error("Error in audit_template route: %s", e)
            return routes.make_response(data={"error": str(e)}, status=500)
