# -*- coding: utf-8 -*-
"""
Reports of the ribbon buttons MCP > Conferir (no AI): turn the result dicts
of the check routes (check_sheets, check_render_materials, export_sheets_pdf,
audit_template) into pt-BR blocks for the pyRevit output window and texts for
the confirmation dialogs.

Pure module (no Revit or pyRevit import) so it runs under CPython tests and
IronPython 2.7 (scripts/ironpython_check.py). A report is a list of blocks:
    {"kind": "md", "text": u"..."}
    {"kind": "table", "title": u"...", "columns": [...], "rows": [[...]]}
render(output, blocks) prints them with output.print_md / output.print_table.
Element ids go through link(id), which the button sets to output.linkify.
"""

import json
import os
import shutil

try:
    TEXT = unicode  # noqa: F821 - IronPython 2.7
    STRINGS = basestring  # noqa: F821
except NameError:  # CPython
    TEXT = str
    STRINGS = str

MAX_LISTED = 30
MAX_PLAN_FILES = 15

SHEET_STATUS = {"ok": u"OK", "missing": u"Faltando", "review": u"Revisar"}
SHEET_CHECKS = {
    "no_views": u"Folha vazia",
    "title_block": u"Sem carimbo",
    "empty_viewport": u"Vista vazia",
    "required_view": u"Vista obrigatória",
    "required_schedule": u"Tabela obrigatória",
    "required_legend": u"Legenda obrigatória",
    "scale": u"Escala",
    "title_block_param": u"Parâmetro do carimbo",
    "revision": u"Revisão",
    "viewport_count": u"Número de vistas",
}
VIEW_TYPES = {
    "FloorPlan": u"Planta", "CeilingPlan": u"Planta de forro", "Elevation": u"Elevação",
    "ThreeD": u"3D", "DraftingView": u"Vista de desenho", "Legend": u"Legenda",
    "EngineeringPlan": u"Planta estrutural", "AreaPlan": u"Planta de área",
    "Section": u"Corte", "Detail": u"Detalhe", "Walkthrough": u"Percurso",
    "Rendering": u"Renderização", "Schedule": u"Tabela", "DrawingSheet": u"Folha",
    "PanelSchedule": u"Tabela de quadro", "ColumnSchedule": u"Tabela de pilares",
}
MATERIAL_RULES = (
    ("no_material", u"Sem material",
     u"A geometria não tem material e a categoria também não: o render usa um material padrão."),
    ("category_material_only", u"Só o material da categoria",
     u"Os acabamentos não estão separados na família: só o material da categoria chega ao render."),
    ("name_pattern", u"Nome fora do padrão OBJETO_MATERIAL_PARTE_NN",
     u"Renomeie o material (sem acentos, maiúsculas, partes separadas por _ e número no fim)."),
    ("duplicate_names", u"Nomes repetidos ou parecidos",
     u"Materiais com o mesmo nome depois de tirar acentos, espaços e _: junte ou diferencie."),
    ("generic_shared", u"Material genérico usado por várias famílias",
     u"Crie um material próprio para cada objeto."),
    ("single_material", u"Mobiliário com um material só",
     u"Confira se cada acabamento tem o seu material (pode ser intencional)."),
)
SEVERITY = {"error": u"Erro", "review": u"Revisar"}
MESH_NOTE = u"geometria só de malha (importada): confira o render a olho"
PDF_SKIP_REASONS = {"placeholder": u"folha reservada (placeholder)",
                    "not_printable": u"não pode ser impressa",
                    "not_a_sheet": u"não é uma folha"}
AUDIT_SECTIONS = (
    ("browser_organization", u"Organização do navegador"),
    ("levels_grids", u"Níveis e eixos"),
    ("phases", u"Fases"),
    ("view_templates", u"Modelos de vista"),
    ("filters", u"Filtros"),
    ("views", u"Vistas"),
    ("line_styles", u"Estilos de linha"),
    ("annotation_types", u"Tipos de anotação"),
    ("families", u"Famílias"),
    ("shared_parameters", u"Parâmetros compartilhados"),
    ("project_parameters", u"Parâmetros de projeto"),
    ("materials", u"Materiais"),
    ("schedules", u"Tabelas"),
    ("sheets", u"Folhas"),
    ("export_settings", u"Configurações de exportação"),
    ("naming", u"Nomenclatura"),
)
AUDIT_PARTS = {
    "levels": u"níveis", "grids": u"eixos", "phases": u"fases", "phase_filters": u"filtros de fase",
    "styles": u"estilos", "line_patterns": u"padrões de linha", "fill_patterns": u"padrões de preenchimento",
    "dimension_types": u"tipos de cota", "text_types": u"tipos de texto",
    "tag_and_symbol_types": u"etiquetas e símbolos", "sheets": u"folhas",
    "title_blocks": u"carimbos", "view_templates": u"modelos de vista", "filters": u"filtros",
}
COVERAGE = {"api": u"automática", "parcial": u"parcial"}


# ---- small helpers ----

def text(value):
    """Any value as unicode; None -> u""."""
    if value is None:
        return u""
    if isinstance(value, TEXT):
        return value
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except Exception:
            return value.decode("latin-1")
    return TEXT(value)


def cell(value):
    """Text safe inside a markdown table cell."""
    return text(value).replace(u"|", u"/").replace(u"\r", u" ").replace(u"\n", u" ").strip()


def plain_link(element_id):
    return text(element_id)


def _link(link, element_id):
    if element_id is None:
        return u""
    return (link or plain_link)(element_id)


def md(value):
    return {"kind": "md", "text": text(value)}


def table(title, columns, rows):
    return {"kind": "table", "title": text(title), "columns": list(columns),
            "rows": [[r if isinstance(r, TEXT) else cell(r) for r in row] for row in rows]}


def capped(items, limit=MAX_LISTED):
    """(first items, how many more)."""
    items = list(items or [])
    return items[:limit], max(0, len(items) - limit)


AUDIT_MORE_HINT = u"veja a planilha Excel para a lista completa"


def more_line(count, hint=None):
    """"... e mais N." (with the hint in parentheses), or None when count is 0."""
    if not count:
        return None
    return md(u"... e mais {}{}.".format(count, u" ({})".format(hint) if hint else u""))


def plural(count, one, many):
    return u"{} {}".format(count, one if count == 1 else many)


def render(output, blocks):
    """Print blocks in the pyRevit output window (output: script.get_output())."""
    for block in blocks:
        if block is None:
            continue
        if block["kind"] == "table":
            if not block["rows"]:
                continue
            output.print_table(table_data=block["rows"], columns=block["columns"],
                               title=block["title"] or None)
        else:
            output.print_md(block["text"])


def as_text(blocks):
    """Blocks as plain text (tests, logs)."""
    lines = []
    for block in blocks:
        if block is None:
            continue
        if block["kind"] == "table":
            if block["title"]:
                lines.append(block["title"])
            lines.append(u" | ".join(block["columns"]))
            for row in block["rows"]:
                lines.append(u" | ".join(text(c) for c in row))
        else:
            lines.append(block["text"])
    return u"\n".join(lines)


# ---- checklists file (Checar pranchas) ----

CHECKLISTS_NAME = u"checklists_pranchas.json"
EXAMPLE_CHECKLISTS = (u"config", u"checklists_pranchas.exemplo.json")
USER_FOLDER = u"RevitMCPPython"


def checklists_paths(appdata, extension_root):
    """(office file in %APPDATA%\\RevitMCPPython, example shipped with the extension).
    The office file lives outside the extension so the Atualizar button (git
    pull) never overwrites it."""
    user = os.path.join(text(appdata), USER_FOLDER, CHECKLISTS_NAME) if appdata else None
    example = os.path.join(text(extension_root), *EXAMPLE_CHECKLISTS)
    return user, example


def prepare_checklists(appdata, extension_root, isfile=os.path.isfile, copy=shutil.copyfile,
                       makedirs=os.makedirs):
    """Office checklists file, copied from the example on first use.

    Returns (path, created, error): created is True when the example was just
    copied (tell the user where to edit it)."""
    user, example = checklists_paths(appdata, extension_root)
    if user is None:
        return None, False, u"pasta APPDATA não encontrada"
    if isfile(user):
        return user, False, None
    if not isfile(example):
        return None, False, u"exemplo de checklists não encontrado: {}".format(example)
    try:
        folder = os.path.dirname(user)
        if not isfile(folder) and not os.path.isdir(folder):
            makedirs(folder)
        copy(example, user)
    except (IOError, OSError) as error:
        return None, False, u"não foi possível criar {} ({})".format(user, text(error))
    return user, True, None


def load_checklists(raw):
    """Text of the checklists JSON -> (request body for check_sheets, error).

    Accepts a list of rules or an object with "checklists" (plus optional
    check_sheets options such as "generic" or "only_problems"). Keys starting
    with "_" (comments) are dropped."""
    try:
        data = json.loads(text(raw).lstrip(u"﻿"))
    except ValueError as error:
        return None, u"o arquivo não é um JSON válido ({})".format(text(error))
    if isinstance(data, list):
        data = {"checklists": data}
    if not isinstance(data, dict):
        return None, u"o JSON precisa ser uma lista de checklists ou um objeto com \"checklists\""
    body = dict((k, v) for k, v in data.items() if not text(k).startswith(u"_"))
    rules = body.get("checklists")
    if not isinstance(rules, list) or not rules:
        return None, u"o JSON não tem nenhum checklist (lista \"checklists\" vazia ou ausente)"
    body["checklists"] = [_drop_comments(rule) for rule in rules]
    body.setdefault("limit", 1000)
    body.setdefault("unplaced_limit", 200)
    return body, None


def _drop_comments(rule):
    if not isinstance(rule, dict):
        return rule
    return dict((k, v) for k, v in rule.items() if not text(k).startswith(u"_"))


def checklist_errors_text(error, errors):
    """400 from sheet_checks.parse_options -> pt-BR text for an alert."""
    lines = [u"Os checklists do arquivo têm erros: {}".format(text(error))]
    for item in errors or []:
        lines.append(u"- checklist {}: {}".format(item.get("index", 0) + 1, text(item.get("error"))))
    return u"\n".join(lines)


# ---- Checar pranchas ----

def sheet_report(result, source=None, link=None):
    summary = result.get("summary") or {}
    blocks = [md(u"## Checagem de pranchas")]
    if source:
        blocks.append(md(u"Checklists: `{}`".format(text(source))))
    blocks.append(md(
        u"**{} de {} folhas conferidas** · OK: {} · Faltando: {} · Revisar: {} · "
        u"Sem checklist: {}".format(
            summary.get("sheets_checked", 0), summary.get("sheets_total", 0),
            summary.get("ok", 0), summary.get("missing", 0), summary.get("review", 0),
            summary.get("sheets_without_rule", 0))))
    notes = []
    if summary.get("truncated"):
        notes.append(u"a lista foi cortada no limite de folhas")
    if summary.get("placeholders_skipped"):
        notes.append(u"{} reservada(s) (placeholder) ignorada(s)".format(
            summary["placeholders_skipped"]))
    if summary.get("not_found"):
        notes.append(u"não encontradas: {}".format(u", ".join(text(n) for n in summary["not_found"])))
    if summary.get("empty_check_skipped"):
        notes.append(u"{} vista(s) sem a conferência de vista vazia (tempo esgotado)".format(
            summary["empty_check_skipped"]))
    if notes:
        blocks.append(md(u"Observações: " + u"; ".join(notes) + u"."))
    by_check = summary.get("problems_by_check") or {}
    if by_check:
        rows = [[SHEET_CHECKS.get(k, k), by_check[k]]
                for k in sorted(by_check, key=lambda k: (-by_check[k], k))]
        blocks.append(table(u"Problemas por tipo", [u"Conferência", u"Quantidade"], rows))

    sheets = result.get("sheets") or []
    if sheets:
        rows = []
        for sheet in sheets:
            rows.append([_link(link, sheet.get("id")), cell(sheet.get("number")),
                         cell(sheet.get("name")), cell(sheet.get("rule") or u"-"),
                         SHEET_STATUS.get(sheet.get("status"), cell(sheet.get("status"))),
                         len(sheet.get("problems") or []),
                         len(sheet.get("manual_checks") or [])])
        blocks.append(table(u"Folhas", [u"Id", u"Número", u"Nome", u"Checklist", u"Status",
                                        u"Problemas", u"Conferir a olho"], rows))
    for sheet in sheets:
        problems = sheet.get("problems") or []
        manual = sheet.get("manual_checks") or []
        if not problems and not manual:
            continue
        blocks.append(md(u"### {} {} - {} ({})".format(
            _link(link, sheet.get("id")), text(sheet.get("number")), text(sheet.get("name")),
            SHEET_STATUS.get(sheet.get("status"), text(sheet.get("status"))))))
        if problems:
            blocks.append(table(u"", [u"Conferência", u"Detalhe"],
                                [[SHEET_CHECKS.get(p.get("check"), cell(p.get("check"))),
                                  cell(p.get("detail"))] for p in problems]))
        if manual:
            blocks.append(md(u"Conferir a olho:\n\n" + u"\n".join(
                u"- ☐ {}".format(text(item)) for item in manual)))
    if not sheets:
        blocks.append(md(u"Nenhuma folha para mostrar."))

    unplaced = result.get("unplaced_views")
    if unplaced and unplaced.get("count"):
        by_type = unplaced.get("by_type") or {}
        blocks.append(md(u"### Vistas fora de pranchas: {}\n{}".format(
            unplaced["count"], u", ".join(u"{}: {}".format(VIEW_TYPES.get(k, k), by_type[k])
                                          for k in sorted(by_type)))))
        views, more = capped(unplaced.get("views"))
        blocks.append(table(u"", [u"Id", u"Nome", u"Tipo"],
                            [[_link(link, v.get("id")), cell(v.get("name")),
                              VIEW_TYPES.get(v.get("type"), cell(v.get("type")))] for v in views]))
        blocks.append(more_line(more))
        if unplaced.get("truncated"):
            blocks.append(md(u"(lista cortada no limite de vistas)"))
    return blocks


# ---- Checar materiais ----

def material_report(result, link=None):
    summary = result.get("summary") or {}
    blocks = [md(u"## Checagem de materiais para render")]
    blocks.append(md(u"Modelo: **{}** · Categorias: {}".format(
        text(result.get("document")), u", ".join(text(c) for c in result.get("categories") or [])
        or u"-")))
    if result.get("view"):
        blocks.append(md(u"Só o que aparece na vista: {} {}".format(
            _link(link, result["view"].get("id")), text(result["view"].get("name")))))
    blocks.append(md(u"{} elementos, {} tipos de família e {} materiais conferidos · "
                     u"**{} problema(s)**".format(
                         result.get("elements_checked", 0), result.get("types_checked", 0),
                         result.get("materials_checked", 0), result.get("problems_total", 0))))
    rows = [[label, summary.get(rule, 0)] for rule, label, _ in MATERIAL_RULES]
    blocks.append(table(u"Resumo por regra", [u"Regra", u"Quantidade"], rows))
    extra = []
    if summary.get("painted_types"):
        extra.append(u"{} tipo(s) com pintura (ferramenta Pintar)".format(summary["painted_types"]))
    if summary.get("mesh_only_types"):
        extra.append(u"{} tipo(s) só com malha importada".format(summary["mesh_only_types"]))
    if result.get("truncated"):
        extra.append(u"mostrando {} de {} problemas".format(result.get("returned"),
                                                          result.get("problems_total")))
    if extra:
        blocks.append(md(u"Observações: " + u"; ".join(extra) + u"."))

    problems = result.get("problems") or []
    if result.get("view") and not result.get("elements_checked"):
        blocks.append(md(u"**Nenhum elemento das categorias verificadas visível nesta vista.** "
                         u"Abra a vista 3D, planta ou elevação que vai para o render."))
    elif not problems:
        blocks.append(md(u"Nenhum problema encontrado."))
    for rule, label, hint in MATERIAL_RULES:
        found = [p for p in problems if p.get("rule") == rule]
        if not found:
            continue
        blocks.append(md(u"### {} ({})\n{}".format(label, len(found), hint)))
        shown, more = capped(found)
        if rule == "name_pattern":
            blocks.append(table(u"", [u"Material"], [[cell(p.get("material"))] for p in shown]))
        elif rule == "duplicate_names":
            blocks.append(table(u"", [u"Gravidade", u"Materiais"],
                                [[SEVERITY.get(p.get("severity"), u""),
                                  cell(u", ".join(text(m) for m in p.get("materials") or []))]
                                 for p in shown]))
        elif rule == "generic_shared":
            blocks.append(table(u"", [u"Material", u"Famílias"],
                                [[cell(p.get("material")),
                                  cell(u", ".join(text(f) for f in p.get("families") or []))]
                                 for p in shown]))
        else:
            rows = []
            for p in shown:
                notes = []
                if p.get("painted"):
                    notes.append(u"pintado: " + u", ".join(text(m) for m in p["painted"]))
                if p.get("note"):
                    notes.append(MESH_NOTE)
                rows.append([_link(link, p.get("element_id")), cell(p.get("family")),
                             cell(p.get("type")), cell(p.get("category")),
                             cell(p.get("material") or u"-"), cell(u"; ".join(notes))])
            blocks.append(table(u"", [u"Exemplo", u"Família", u"Tipo", u"Categoria", u"Material",
                                      u"Observação"], rows))
        blocks.append(more_line(more))
    return blocks


# ---- Emitir PDF ----

def pdf_plan_text(plan):
    """Dry-run result -> (message, details) for the confirmation dialog."""
    files = plan.get("files") or []
    sheets = sum(len(f.get("sheets") or []) for f in files)
    message = u"Emitir {} de {}?".format(plural(len(files), u"PDF", u"PDFs"),
                                          plural(sheets, u"folha", u"folhas"))
    lines = [u"Pasta: {}".format(text(plan.get("folder")))]
    if not plan.get("folder_exists"):
        lines[0] += u" (será criada)"
    lines.append(u"")
    shown, more = capped(files, MAX_PLAN_FILES)
    for item in shown:
        lines.append(u"- {}{}".format(text(item.get("file")),
                                      u"  [JÁ EXISTE]" if item.get("exists") else u""))
    if more:
        lines.append(u"... e mais {}".format(more))
    skipped = plan.get("skipped") or []
    if skipped:
        lines.append(u"")
        lines.append(u"Fora da emissão:")
        for item in skipped[:MAX_PLAN_FILES]:
            lines.append(u"- {}: {}".format(
                text(item.get("sheet_number") or item.get("view")),
                PDF_SKIP_REASONS.get(item.get("reason"), text(item.get("reason")))))
    notes = []
    if plan.get("renamed_to_avoid_duplicates"):
        notes.append(u"{} nome(s) ganharam sufixo para não repetir".format(
            len(plan["renamed_to_avoid_duplicates"])))
    for warning in plan.get("warnings") or []:
        notes.append(text(warning))
    existing = existing_files(plan)
    if existing:
        notes.append(u"{} arquivo(s) já existem na pasta: a próxima pergunta é se pode "
                     u"substituir".format(len(existing)))
    if plan.get("warning") and not existing:
        notes.append(text(plan["warning"]))
    if notes:
        lines.append(u"")
        lines.extend(u"Atenção: {}".format(n) for n in notes)
    lines.append(u"")
    lines.append(u"Também grava emissao.json com a lista do que foi emitido.")
    return message, u"\n".join(lines)


def existing_files(plan):
    return [text(f.get("file")) for f in plan.get("files") or [] if f.get("exists")]


def pdf_replace_text(existing):
    """-> (message, details) for the second dialog (replace existing files)."""
    shown, more = capped(existing, MAX_PLAN_FILES)
    lines = [u"- {}".format(text(name)) for name in shown]
    if more:
        lines.append(u"... e mais {}".format(more))
    lines.append(u"")
    lines.append(u"Cada arquivo antigo é guardado como .bak até o novo ser gravado; se a "
                 u"exportação falhar, o antigo volta.")
    return (u"Substituir {} que já existe(m) na pasta?".format(
        plural(len(existing), u"arquivo", u"arquivos")), u"\n".join(lines))


def pdf_result_text(result):
    """Real-run result -> (message, details, ok)."""
    errors = result.get("errors") or []
    exported = result.get("exported", 0)
    if errors:
        message = u"{} PDF(s) gravado(s), {} com erro.".format(exported, len(errors))
    else:
        message = u"{} PDF(s) emitido(s).".format(exported)
    lines = [u"Pasta: {}".format(text(result.get("folder")))]
    if result.get("replaced"):
        lines.append(u"Substituídos: {}".format(u", ".join(text(r) for r in result["replaced"])))
    for error in errors[:MAX_PLAN_FILES]:
        lines.append(u"Erro em {}: {}".format(text(error.get("file")), text(error.get("error"))))
    for warning in result.get("warnings") or []:
        lines.append(u"Aviso: {}".format(text(warning)))
    if result.get("manifest"):
        lines.append(u"Registro: {}".format(text(result["manifest"])))
    return message, u"\n".join(lines), not errors


class Stop(object):
    """Stops pdf_export.issue_pdfs before writing (same .data/.status shape
    as a routes response)."""

    def __init__(self, error, status=409):
        self.data = {"status": "error", "error": text(error)}
        self.status = status


def replacement_guard(approved_paths):
    """approve(action, preview) for issue_pdfs: refuses when the run would
    replace a file the user did not confirm (it appeared after the plan)."""
    approved = set(text(p) for p in approved_paths or [])

    def approve(action, preview):
        extra = [text(p) for p in (preview or {}).get("_will_replace") or []
                 if text(p) not in approved]
        if extra:
            return Stop(u"{} arquivo(s) apareceram na pasta depois do plano; nada foi "
                        u"exportado. Rode de novo.".format(len(extra)))
        return None
    return approve


def replace_paths(plan):
    """Full paths of the planned files that already exist."""
    folder = text(plan.get("folder"))
    return [os.path.join(folder, name) for name in existing_files(plan)]


def needs_overwrite(plan):
    return bool(existing_files(plan))


def is_plan(data):
    """True for a dry-run answer of issue_pdfs (decided from the data, not
    from the HTTP-like status)."""
    return isinstance(data, dict) and data.get("status") == "dry_run" and "files" in data


def is_run_result(data):
    """True for a real run that reached the export (all or some files)."""
    return isinstance(data, dict) and data.get("status") in ("success", "error") \
        and "exported" in data and "files" in data


def pdf_history_summary(result):
    """One line for the approval history (Histórico button)."""
    return u"{} PDF(s) em {}{}".format(
        result.get("exported", 0), text(result.get("folder")),
        u", {} com erro".format(len(result["errors"])) if result.get("errors") else u"")


def pdf_error_text(data, status=None):
    """Error answer of the export -> pt-BR text. status is only a hint: the
    kind of error is read from the data."""
    data = data or {}
    error = text(data.get("error"))
    lower = error.lower()
    if data.get("existing") and u"approval dialog" in lower:
        return u"Apareceram arquivos novos na pasta durante a emissão; nada foi exportado. " \
               u"Rode de novo. ({})".format(u", ".join(text(e) for e in data["existing"][:5]))
    if data.get("existing"):
        return u"{} arquivo(s) já existem na pasta; nada foi exportado. Rode de novo e " \
               u"confirme a substituição.".format(len(data["existing"]))
    if data.get("conflicts") or status == 409:
        return u"Há conflito com arquivos ou pastas no destino; nada foi exportado. ({})".format(error)
    if u"in use or locked" in lower or status == 423:
        return u"Um PDF está aberto em outro programa ou bloqueado; feche e tente de novo. " \
               u"Nada foi alterado. ({})".format(error)
    if u"revit 2022" in lower or status == 501:
        return u"Este Revit não aceita a exportação nativa de PDF (precisa do 2022 ou mais " \
               u"novo). ({})".format(error)
    return u"A emissão não foi feita: {}".format(
        error or (u"erro {}".format(status) if status else u"resposta inesperada"))


# ---- Checar materiais: which views can limit the check ----

MATERIAL_VIEW_TYPES = ("ThreeD", "FloorPlan", "CeilingPlan", "EngineeringPlan", "AreaPlan",
                       "Elevation", "Section", "Detail", "Walkthrough")


def material_view_error(view_type, is_template):
    """pt-BR refusal for views that show no model (sheet, schedule, legend,
    drafting view...) or None when the view can limit the check."""
    if is_template:
        return u"A vista ativa é um modelo de vista: abra uma vista 3D, planta ou elevação."
    if text(view_type) not in MATERIAL_VIEW_TYPES:
        return u"A vista ativa ({}) não mostra o modelo: abra uma vista 3D, planta ou " \
               u"elevação.".format(VIEW_TYPES.get(text(view_type), text(view_type)))
    return None


# ---- Auditar template ----

def _is_summary(value):
    return isinstance(value, dict) and "count" in value and "items" in value


def _audit_rows(name, value):
    """(label, summary) pairs of one section."""
    if _is_summary(value):
        return [(None, value)]
    pairs = []
    if isinstance(value, dict):
        for key in sorted(value):
            if _is_summary(value[key]):
                pairs.append((key, value[key]))
    return pairs


def _item_label(item):
    if item.get("number") is not None:
        return u"{} - {}".format(text(item.get("number")), text(item.get("name")))
    return text(item.get("name") or item.get("id"))


def audit_report(report, link=None):
    blocks = [md(u"## Auditoria do template")]
    blocks.append(md(u"Modelo: **{}**{} · Revit {}".format(
        text(report.get("document")),
        u" (arquivo de template .rte)" if report.get("is_template_file") else u"",
        text(report.get("revit_version")))))
    sections = report.get("sections") or {}
    labels = dict(AUDIT_SECTIONS)
    rows = []
    unused_lists = []
    for key, label in AUDIT_SECTIONS:
        if key not in sections or key in ("naming",):
            continue
        for part, summary in _audit_rows(key, sections[key]):
            name = label if part is None else u"{} / {}".format(label, AUDIT_PARTS.get(part, part))
            unused = summary.get("unused_count")
            rows.append([name, summary.get("count", 0), u"-" if unused is None else unused])
            if unused:
                unused_lists.append((name, summary))
    if rows:
        blocks.append(table(u"Inventário", [u"Seção", u"Itens", u"Sem uso"], rows))
    for name, summary in unused_lists:
        items = [i for i in summary.get("items") or [] if i.get("unused")]
        shown, more = capped(items)
        blocks.append(md(u"### Sem uso: {} ({})".format(name, summary.get("unused_count"))))
        blocks.append(table(u"", [u"Id", u"Nome"],
                            [[_link(link, i.get("id")), cell(_item_label(i))] for i in shown]))
        hidden = summary.get("unused_count", 0) - len(shown)
        blocks.append(more_line(max(more, hidden), AUDIT_MORE_HINT))

    naming = sections.get("naming") or {}
    for kind in sorted(naming):
        check = naming[kind]
        blocks.append(md(u"### Nomenclatura de {}: {} fora do padrão `{}` ({} conferidos)".format(
            kind, check.get("violations_count", 0), text(check.get("pattern")),
            check.get("checked", 0))))
        shown, more = capped(check.get("violations"))
        blocks.append(table(u"", [u"Id", u"Nome"],
                            [[_link(link, v.get("id")), cell(v.get("name"))] for v in shown]))
        blocks.append(more_line(max(more, check.get("violations_count", 0) - len(shown)),
                                AUDIT_MORE_HINT))

    purge = sections.get("purge_preview")
    if purge:
        source = (u"lista do Limpar não utilizados do Revit" if purge.get("source") == "revit_purge"
                  else u"famílias sem instância colocada")
        blocks.append(md(u"### Prévia do Limpar não utilizados: {} item(ns)\nFonte: {}. "
                         u"Nada foi apagado.".format(purge.get("count", 0), source)))
        by_kind = purge.get("by_kind") or {}
        blocks.append(table(u"", [u"Tipo de elemento", u"Quantidade"],
                            [[cell(k), by_kind[k]] for k in sorted(by_kind)]))
        shown, more = capped(purge.get("items"))
        blocks.append(table(u"", [u"Id", u"Tipo", u"Categoria", u"Nome"],
                            [[_link(link, i.get("id")), cell(i.get("kind")),
                              cell(i.get("category")), cell(i.get("name"))] for i in shown]))
        blocks.append(more_line(max(more, purge.get("count", 0) - len(shown)), AUDIT_MORE_HINT))
        extra = purge.get("not_in_revit_purge") or {}
        for key in ("view_templates", "filters"):
            summary = extra.get(key) or {}
            if summary.get("count"):
                blocks.append(md(u"Fora do Limpar do Revit (apagar à mão se quiser): "
                                 u"{} {} sem uso.".format(summary["count"], AUDIT_PARTS[key])))

    checklist = report.get("checklist") or []
    if checklist:
        rows = []
        for item in checklist:
            coverage = COVERAGE.get(item.get("coverage"), text(item.get("coverage")))
            sections_found = u", ".join(text(labels.get(s, s)) for s in item.get("sections") or [])
            rows.append([item.get("item"), cell(item.get("title")), cell(coverage),
                         cell(sections_found or u"-"), cell(item.get("check_manually") or u"")])
        blocks.append(table(u"Checklist do template (19 itens)",
                            [u"Item", u"Assunto", u"Cobertura", u"Seções", u"Conferir a olho"], rows))
    errors = report.get("section_errors") or {}
    if errors:
        blocks.append(md(u"**Seções com erro ou puladas:** " + u"; ".join(
            u"{}: {}".format(labels.get(k, k), text(errors[k])) for k in sorted(errors))))
    return blocks


def audit_file_name(document, today):
    """Suggested JSON name: auditoria_<model>_<YYYY-MM-DD>.json (no bad characters)."""
    base = u"".join(c if (c.isalnum() or c in u"-_ ") else u"_" for c in text(document))
    base = u"_".join(base.split()) or u"modelo"
    return u"auditoria_{}_{}.json".format(base[:60], text(today))
